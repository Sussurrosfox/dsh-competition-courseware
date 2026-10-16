# skills-langgraph：竞赛教研与学案制作流水线（LangGraph 改造版）

本项目是针对原 `skills-secured` 进行 **LangGraph 状态机重构** 的独立改造副本。

---

## 一、 改造背景与核心收益

原 `skills-secured` 拥有极其完善的零信任安全设计、HMAC 凭证机制与 XeLaTeX 排版门禁，但由于采用单一的 Prompt Skill 形式承载，导致**主 Agent 承担了状态调度器、命令运行者、密码学验签器与门禁协调者的所有琐碎职责**，引发了严重的上下文膨胀、状态遗忘与脆弱的执行闭环。

在 `skills-langgraph` 中，我们完成了**三层解耦与控制权外生化**：

```
+-----------------------------------------------------------------------------------+
| 1. 控制平面 (Control Plane) - 由 LangGraph 驱动                                     |
|    - 强类型状态 StateGraph: 状态转移、条件分支、时间回溯、检查点持久化                 |
|    - 原生断点 interrupt(): 物理级挂起 Phase 0 用户确认，防跳步伪造                    |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
| 2. 算力与认知层 (Cognitive Workers) - 专职子 Agent                                  |
|    - Probe / Solver / Math-Audit / Architect / Student-Task                       |
|    - 纯粹的无状态 Worker，仅关注特定题目的数学求解与教学设计                          |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
| 3. 安全与执行基底 (Execution Enclave) - scripts/ 保持只读                           |
|    - broker.py: HMAC 签名、窄包签发、确定性渲染、原子提升                             |
|    - check_*.py: 严格的质量门禁与 XeLaTeX 盒模型校验                                |
+-----------------------------------------------------------------------------------+
```

---

## 二、 认知工位派发契约（本轮改造新增）

图引擎从不直接调用 LLM。所有认知工位（架构计划、学生任务、探针/求解重做、
定点返修）统一经过**批次派发引擎** `run_batch_dispatch`
（`workflow/nodes/agent_nodes.py`）：

- **逐单串行派发、逐单 SHA-256 校验**：产物未出现或与派发前完全一致即 HALT，
  绝不无变更重跑门禁；已标记 `ok` 的派发单在续跑时自动跳过，不会重复派发。
- **按题独立派发单/子会话**：T2 学生任务与 Research-Back 两阶段（probe →
  solver）均为每题一份派发单（`state["pending_dispatch_batch"]`），单次派发仅
  携带该题的窄包与上下文，互不串题。
- **无 worker 时物理挂起**：抛出 `interrupt()`，payload 为
  `{"type": "AGENT_DISPATCH_BATCH", "stage": ..., "sheets": [...]}`，由人工或
  Antigravity 会话产出文件后恢复执行。

**多角色 worker registry**（`workflow/adapters/worker_registry.py`）：宿主进程
（Web UI / CLI / 测试）通过 `worker_registry.set(fn)` 注册默认 worker，或用
`worker_registry.set_role(role, fn)` 为特定角色（如 `probe` / `solver` /
`student-task-review`）注册专属 worker；派发时 `call_for_role` 角色优先、回退
默认，`available_for_role` 决定就地派发还是挂起中断。

**关键契约：每次调用必须是全新会话**，宿主侧不得复用上下文——这是“图内子
agent”隔离语义的全部实现。probe 与 solver 等角色必须使用互不相同的会话 ID
（`execution_metadata.subagent_conversation_id`），图本身只负责派发与校验，
会话隔离由注入的 worker 保证。

T2 自审默认开启：学生任务批次产物落盘先按题派发自审（role
`student-task-review`，产物 `review-student.json`，可定点修复）再进入核验；
自审子 agent 持有 teacher-solution packet（完整证明视野），核验设问链是否
抵达解。仅当项目 `config.yaml` 显式写 `enable_t2_self_review: false` 才关闭。

---

## 三、 目录结构

```
competition-courseware/
├── launcher.py                     # 统一启动器（Web UI / CLI 模式分流）
├── start_ui.bat                    # Windows 一键启动 Web UI 批处理
├── workflow/                       # 【核心】LangGraph 编排引擎（详见 workflow/README.md）
│   ├── state.py                    # CoursewareState 强类型状态定义
│   ├── graph.py                    # StateGraph 组装 (23个节点注册、单向推进、条件路由)
│   ├── edges.py                    # 条件路由分流器 (Gate PASS/REPAIR/RESEARCH_BACK)
│   ├── runner.py                   # 统一 CLI 与 Python API 运行入口
│   ├── nodes/                      # 流程执行节点 (research/human/teaching/build/repair/agent)
│   ├── adapters/                   # 隔离适配器 (script_runner / llm_worker / worker_registry)
│   ├── checkpoints/                # 跨进程持久化检查点 (SqliteSaver)
│   ├── ui/                         # Gradio Web UI 工作台 (app / folder_picker / pipeline_manager)
│   └── README.md                   # 编排引擎与状态机完整技术手册
├── scripts/                        # 20+ 核心验证与安全门禁脚本（零信任只读基底）
├── schemas/                        # 20+ JSON Schemas 规范契约
├── prompts/                        # 专职子 Agent 提示词模板
├── templates/                      # LaTeX 模板与样式配置
├── tests/                          # 测试套件 (37 passed)
│   ├── test_langgraph_pipeline.py  # LangGraph 流水线单元与集成测试
│   ├── test_batch_dispatch.py      # 批次派发 / 多角色 worker / RB 两阶段 / T2 自审测试
│   ├── run_bypass_matrix.py        # 16+ 项对抗穿透测试矩阵
│   └── _fixture.py                 # 测试沙箱与工作区隔离设施
├── AGENTS.md                       # 工程约定与协作说明（批次派发、worker 契约、测试方式）
├── SKILL.md                        # 高阶 Agent 交互操作指导
├── USER_GUIDE.md                   # 面向初学者的全景架构与实操手册
└── README.md                       # 项目总体白皮书
```

---

## 四、 使用方法

### 1. 🚀 一键启动 Web UI 可视化工作台（推荐）
告别复杂的 CLI 命令行参数，通过现代交互式界面一键管理全生命周期：

```powershell
# 方式 A：双击目录下的批处理脚本
start_ui.bat

# 方式 B：通过 Python 启动（自动唤起默认浏览器）
python launcher.py
```
- **核心功能**：
  - **位置无关与解耦构建**：工作流引擎可置于硬盘任意路径，通过 Windows 原生文件管理器弹窗直选【输入文件夹】与【输出工作区】
  - **原地构建 / 分离构建双模切换**：勾选“原地构建”直接在源目录运作；取消勾选可指定全新的独立构建与发布目录
  - **动态拓扑与进度看板**：实时流式显示 R1 ~ T4 阶段流动拓扑与当前活跃工位
  - **Phase 0 物理断点表单**：自动拦截并呈现交互卡片，一键确认标题与模式后恢复推进
  - **质量门禁体检中心（Gate Inspector）**：全景解析 11 项硬门禁的 PASS/REJECT 状态、违规分类与修复指引
  - **双源 PDF 成果与交付**：学生版学案 (`student.pdf`) 与教师版讲义 (`teacher.pdf`) 一键下载与大小检测
  - **专家单步工位**：针对指定输出工作区单独复验特定阶段，避免重头重跑

### 2. 运行自动化测试套件
验证图拓扑、状态转移、条件边、批次派发与断点机制（37 项测试）：

```powershell
python -m pytest tests/ -q
```

### 3. 命令行 CLI 交互模式（进阶）
若在无头服务器或 CI/CD 环境下，可使用 CLI 驱动器：

```powershell
# 1. 启动教研流（在 Phase 0 自动挂起）
python launcher.py --cli --project . --run --thread-id my_session

# 2. 交互确认后恢复执行（需传入 .langgraph/phase0_token.txt 中的人工授权码）
python launcher.py --cli --project . --resume --title "Lucas定理与高阶同余" --mode guided --thread-id my_session --auth-token "<真人提供的授权码>"

# 3. 导出状态机 Mermaid 拓扑
python launcher.py --cli --visualize
```

### 4. 🧩 DeepSeek Harness 插件模式 (DSH Plugin)
本项目已封装为标准的 DeepSeek Harness (Cordis) 扩展 Bundle (`@local/competition-courseware`)：

- **自动化安装与加载**：通过 Harness `plugin_manager` 即可一键挂载到当前 Profile，支持热重载 (HMR)。
- **专属 Agent 工具 (Host Tools)**：
  - `courseware_pipeline`：以结构化参数控制流水线生命周期（`status` / `run` / `resume` / `visualize`），深度适配 Phase 0 人工授权码安全机制。
  - `courseware_gate`：一键触发全量或定向教研安全门禁检查（`probe`, `solution`, `architect`, `student_visibility`, `section_ownership`, `numbering`, `teaching` 等）。
  - `courseware_workbench`：从聊天会话直接拉起、监控或停止 Gradio 可视化教研工作台。
- **用户指令 (Human Slash Command)**：
  - `/courseware status`：随时在输入框查询流水线当前检查点与阶段。
  - `/courseware run` / `/courseware resume`：便捷下发启动或断点恢复指令。
  - `/courseware gates`：运行零信任质量门禁。
  - `/courseware ui [start|status|stop]`：管理 Gradio 服务。
- **现代化 Web UI 槽位集成 (Client UI)**：
  - 在 Web 界面对话框底部的 `conversation.composer.dock` 注入轻量状态徽章与快捷操作栏。
  - 完美适配 Harness 浅色/深色主题 Token (`--dsw-alias-*`)，一键直达可视化工作台。

---

## 五、 核心解决的痛点总结

1. **消除上下文爆炸**：主 Agent 不再需要在长对话中携带数十个脚本的运行输出与繁冗规则，`SKILL.md` 从 11KB 缩减至精炼高阶指令。
2. **确定性状态驱动**：从 `R1` 到 `T4` 的推进由确定性的图拓扑控制，彻底消除 LLM 记忆紊乱、漏步或跳步的可能性。
3. **零信任控制外生**：代码级图引擎控制何时调用子 Agent、何时签发窄包、何时调用门禁，不可信主体（LLM）无法操纵流程走向。
4. **原生断点保证**：Phase 0 Breakpoint 不再依赖 LLM“自觉遵守”，而是由 LangGraph `interrupt()` 在运行时物理挂起，用户输入前无法推进。
