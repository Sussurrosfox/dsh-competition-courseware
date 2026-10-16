# LangGraph 竞赛教研流水线详细使用指南
> 面向初学者的全景架构解析、实操手册与二次开发指南

---

## 目录
- [一、 5 分钟搞懂 LangGraph 核心哲学（零基础入门）](#一-5-分钟搞懂-langgraph-核心哲学零基础入门)
- [二、 新旧架构对照：它是如何解救主 Agent 的？](#二-新旧架构对照它是如何解救主-agent-的)
- [三、 状态机全生命周期图解（从 R0 到 T4）](#三-状态机全生命周期图解从-r0-到-t4)
- [四、 手把手实操教程（CLI 交互）](#四-手把手实操教程cli-交互)
  - [4.1 场景 A：标准一键全流程执行](#41-场景-a标准一键全流程执行)
  - [4.2 场景 B：遇到断点时的交互与恢复](#42-场景-b遇到断点时的交互与恢复)
  - [4.3 场景 C：查看状态快照与历史轨迹](#43-场景-c查看状态快照与历史轨迹)
  - [4.4 场景 D：导出与查看可视化计算图](#44-场景-d导出与查看可视化计算图)
- [五、 核心代码拆解（读懂每个文件）](#五-核心代码拆解读懂每个文件)
- [六、 异常处理与自愈机制（门禁不通过怎么办？）](#六-异常处理与自愈机制门禁不通过怎么办)
- [七、 进阶配置与二次开发（接入真实模型）](#七-进阶配置与二次开发接入真实模型)
- [八、 常见问题与避坑指南 (FAQ)](#八-常见问题与避坑指南-faq)

---

## 一、 5 分钟搞懂 LangGraph 核心哲学（零基础入门）

如果你熟悉 Python，但从未接触过 **LangGraph**，你可以把它想象成一个**“带有自动存档功能的高级工业自动化流水线”**。

在传统的 Agent 中，LLM（大语言模型）就像一个被派到车间的总管，需要自己记着第一步做什么、第二步做什么、谁做完了给谁。一旦步骤超过 10 步，LLM 就容易“健忘、跳步、搞错命令行参数”。

**LangGraph 彻底颠覆了这种模式：流程由代码说了算，LLM 只在特定工位干活。**

LangGraph 只有 5 个最核心的概念：

| 概念 | 通俗比喻 | 在本项目中的具体对应 |
| :--- | :--- | :--- |
| **State（状态）** | **流转病历夹 / 共享白板** | 一个强类型的 Python 字典（[`CoursewareState`](workflow/state.py)）。里面记录了：当前第几阶段、入选题号、探针结果、门禁报告、编译是否通过等。所有工位都可以读取它、更新它。 |
| **Node（节点）** | **专门工位 / 工作站** | 一个个纯 Python 函数。每个工位只做一件事，例如：`node_r1_screen_problems` 只管初筛评分；`node_t3_latex_build` 只管调 XeLaTeX 编译。每个节点执行完毕后，把新的产物写回 State。 |
| **Edge（边）** | **固定传送带** | 单向箭头。比如 `R1 初筛` 完成后，传送带必定把病历夹送给 `R2 深度研学`。 |
| **Conditional Edge（条件边）** | **道岔 / 门禁分流器** | 根据检查结果决定走哪条路。例如 `t2_broker_submit_and_check` 节点跑完后：<br>① 若门禁全过 → 走向 `T3 编译`；<br>② 若只是小问标点或黑话超标 → 走向 `定点返修 repair`；<br>③ 若证明出现根本漏洞 → 走向 `退回研学 research_back`。 |
| **Interrupt（原生断点）** | **红灯刹车器 / 物理人机断点** | 当流水线需要真人确认时（如 Phase 0 确认学案标题和上课模式），代码调用 `interrupt()`，整个系统瞬间在内存/磁盘暂停。**未得到真人输入前，物理上寸步难行**。 |
| **Checkpointer（检查点持久化）** | **游戏自动存档** | 每一个节点执行完毕，系统就自动做一次快照（基于 `thread_id`）。电脑断电、网络卡顿，随时可以读取存档原地恢复。 |

---

## 二、 新旧架构对照：它是如何解救主 Agent 的？

在之前的 `skills-secured` 中：
```
[主 Agent] ──(阅读 11KB 提示词)──> 记忆 20 步规则 ──> 容易混淆阶段
   │
   ├─► 手工拼写: python scripts/broker.py issue_packet ... (参数容易写错)
   ├─► 手工调用: 子 Agent (有时会忘记派发或伪造)
   ├─► 手工调用: python scripts/check_student_task.py ...
   └─► 遇到报错: 上下文被日志刷屏，不知所措，陷入无休止的重试
```

在改造后的 `skills-langgraph` 中：
```
                   [LangGraph 确定性计算图 (StateGraph)]
               (严格按图拓扑前行，拥有 100% 流程控制权)
                                  │
         ┌────────────────────────┼────────────────────────┐
         ▼                        ▼                        ▼
  [自动执行节点]             [专职认知 Worker]         [人机断点 (Interrupt)]
  - 自动调 broker.py        - Probe Agent (探针)       - 流程自动暂停
  - 自动运行门禁脚本          - Solver Agent (证明)      - 提示用户输入标题/模式
  - 自动调用 XeLaTeX 编译    - Architect (双轨排版)     - 收到输入后恢复前行
  - 门禁失败自动分流返修      - Student-Task (小问设计)
```

**主 Agent 从此彻底解脱**：不再需要去记忆复杂的阶段流转，只需敲一行启动命令，把精力完全集中在数学思考与教学指导上！

---

## 三、 状态机全生命周期图解（从 R0 到 T4）

系统内嵌的完整状态图拓扑如下：

```mermaid
graph TD
    Start([🚀 开始]) --> R1[R1: 题目价值初筛评分 + 选题与冻结 brief 核对]
    R1 --> R2[R2: 深度研学探针/证明/独立审查]
    
    R2 -. 门禁 PASS .-> R4[R4: 冻结交接包 brief & 计算哈希]
    R2 -. 证明漏洞 .-> RB[research_back: 撤销冻结 + 派发重新研学]
    RB -. 等待产物变化 .-> R2
    
    R4 -. 冻结完成 .-> P0{🔴 Phase 0 人机断点 (Interrupt)}
    P0 -. 用户确认标题与模式 .-> T0[T0: 初始化教学工程目录]
    
    T0 -. 初始化成功 .-> T1Req[T1: 派发架构计划（worker 或中断）]
    T1Req -. 等待产物 .-> T1[T1: check_architect 门禁 + 装配教学骨架]
    T1 -. 架构合规 .-> T2Issue[T2: Broker 建立 run 并签发窄包]
    T2Issue --> T2Req[T2: 派发学生探究小问（worker 或中断）]
    T2Req -. 等待产物 .-> T2Verify[T2: 只读核验产物齐全]
    T2Verify --> T2Check[T2: Broker 验签/渲染 + 质量门禁]
    
    T2Check -. 门禁全过 .-> T3[T3: 双源 XeLaTeX 排版编译]
    T2Check -. 局部格式/黑话瑕疵 .-> Repair[repair: 派发定点返修]
    Repair -. 等待产物变化 .-> T2Check
    T2Check -. 实质数学漏洞 .-> RB
    
    T3 -. 编译生成两份 PDF .-> T4[T4: run_gates → promote → deliver]
    T3 -. 编译超限/排版溢出 .-> Repair
    T4 -. deliver 通过且 manifest 归属本轮 .-> End([🏁 交付完成 release/manifest.json])
    T4 -. deliver 失败 / 旧 manifest .-> Fail([🛑 T4_FAILED + HALT])

    style P0 fill:#ffcdd2,stroke:#b71c1c,stroke-width:2px
    style End fill:#c8e6c9,stroke:#2e7d32,stroke-width:2px
    style Fail fill:#ffcdd2,stroke:#b71c1c,stroke-width:2px
```

---

## 四、 手把手实操教程（CLI 交互）

所有操作均通过统一驱动器 [`workflow/runner.py`](workflow/runner.py) 执行。

### 4.0 断点恢复：检查点默认落盘 SQLite

`--run` 与 `--resume` 可以分属**不同的 Python 进程**：检查点、interrupt 与
`thread_id` 全部持久化在 SQLite 里（默认 `<项目>/.langgraph/courseware_checkpoints.sqlite`，
可用 `--checkpoint-db` 指定）。

```powershell
# 进程 1：跑到 Phase 0 断点后退出
python -m workflow.runner --project . --run --thread-id session_01 --checkpoint-db .\.langgraph\cp.sqlite

# 进程 2（甚至重启电脑后）：原地恢复
python -m workflow.runner --project . --resume --title "Lucas定理专题研析" --mode guided --thread-id session_01 --checkpoint-db .\.langgraph\cp.sqlite
```

> 只在单进程内调试时才加 `--memory-checkpointer`（内存检查点，进程退出即丢失）。

### 4.1 场景 A：标准一键全流程执行

如果你在启动时就已经确定了学案主标题和上课模式，可以直接一次性启动：

```powershell
python skills-langgraph/workflow/runner.py --project . --run --title "Lucas定理与组合数同余进阶" --mode guided --thread-id session_01
```

**运行过程解释**：
1. `--run`：启动图计算引擎。
2. `--title "..." --mode guided`：预先提供了 Phase 0 所需的参数，因此断点不会阻塞，会一口气自动跑完：
   - R1 初筛评分 → R2 深度研学审查 → R4 Brief 冻结
   - T0 初始化 → T1 架构设计 → T2 Broker 签发与门禁
   - T3 XeLaTeX 编译 → T4 提升发布
3. 终端会打印出类似如下的绿色执行日志：
   ```text
   [+] 流水线执行完毕！阶段: T4_DELIVERED
       R1: Screened problems. Selected: ['P01']
       R2: Ran check_research. Status=PASS
       R4: Freeze brief completed. Frozen=True, Digest=a7f2...
       Phase 0: Resumed with confirmed spec: title=Lucas定理与组合数同余进阶, mode=guided
       T0: init_project finished. Success=True
       T1: Architect check status=PASS
       T2: Issued broker packets for ['task-p01'] in run run_20260923
       T2: All T2 quality gates PASSED.
       T3: LaTeX build finished. Passed=True
       T4: Delivered successfully. Promoted=True, DeliverStatus=PASS
   ```

---

### 4.2 场景 B：遇到断点时的交互与恢复

如果你不带 `--title` 启动，流水线会在 R4 结束后**安全挂起**：

#### 步骤 1：启动流水线
```powershell
python skills-langgraph/workflow/runner.py --project . --run --thread-id session_interactive
```

#### 终端输出：
```text
[*] 启动 LangGraph 流水线: 项目=., Thread=session_interactive ...
[*] 检查点数据库: .\.langgraph\courseware_checkpoints.sqlite

[!] 流水线安全挂起，等待人工 / Agent 产物：
    - 挂起原因: Phase 0 物理对齐断点：请确认学案主标题（教学模式固定为 guided 分步引导）
    - 候选参数: {
        "main_title": "竞赛数学专题：组合数与同余式",
        "lesson_mode": "guided",
        "available_modes": [
          "guided"
        ]
      }

请运行以下命令恢复执行（检查点已持久化，可另起进程）：
python -m workflow.runner --project . --resume --title "<确认的标题>" --mode guided --thread-id session_interactive --checkpoint-db ".\.langgraph\courseware_checkpoints.sqlite"
```
此时整个流水线的检查点已写入 SQLite，终端关掉也不会丢。

#### 步骤 2：人工（或向用户确认后）恢复执行
```powershell
python -m workflow.runner --project . --resume --title "Lucas定理专题研析" --mode guided --thread-id session_interactive
```
流水线立即从断点处精准唤醒，读取输入写入 `teaching/user-spec.json`，并自动推进后续 T0~T4 的所有步骤！

---

### 4.2b 场景 B2：认知工位派发断点（T1 架构计划 / T2 学生任务 / 返修）

图里的认知工位**不是自动完成的**：它们由
[`workflow/nodes/agent_nodes.py`](workflow/nodes/agent_nodes.py) 派发，只有两种落地方式：

1. **注入了认知 worker**（宿主进程调用 `worker_registry.set(fn)`）：图内就地派发并校验产物；
2. **没有 worker**：抛出带 checkpoint 的 `interrupt()`，把一份机器可读派发单写到
   `teaching/agent-requests/<dispatch_id>.json`，等人工 / Antigravity 产出文件后恢复。

派发单里写明：产出哪些文件、用哪个 prompt / schema、派发前这些文件的摘要。
**产物没出现、或与派发前完全一致时，流水线会中止并报错**，而不是原样重跑同一个门禁：

```text
[!] 流水线安全挂起，等待人工 / Agent 产物：
    - 挂起原因: 认知工位派发：基于冻结 brief 生成本轮双轨架构计划 ...
    - 待产产物: ["teaching/architect-plan.json"]
    - 派发单: teaching/agent-requests/dsp-t1-architect-xxxx.json
    - 操作指引: 按 prompts/architect-agent.md 生成 teaching/architect-plan.json
```

接入自己的模型：

```python
from workflow.adapters.worker_registry import worker_registry

def my_llm(system_prompt: str, user_content: str) -> str:
    # 调用 OpenAI / Anthropic / 本地 vLLM …
    return response_text

worker_registry.set(my_llm, name="my-model")
```

worker 只能写派发单声明的产物路径；返回
`{"files": {"<项目相对路径>": "<完整内容>"}}`，越界路径会被忽略。

### 4.2c 教学骨架为什么不会被覆盖

T1 通过架构门禁后才会生成教学骨架（`materials.json` / `content/*.tex` / `sec*.tex` /
`main.tex`），且**默认只创建缺失文件**：已存在的人工/Agent 编辑一律保留。
确需重新生成时，在初始状态里传 `regenerate_materials: True`：节点会先把
`materials.json`、`content/`、`sec*.tex`、`main.tex` 备份到
`teaching/backups/materials-<时间戳>/`，再覆盖，并把覆盖清单写进状态的
`materials_overwrite_log`。

### 4.3 场景 C：查看状态快照与历史轨迹

无论流水线进行到哪一步，都可以通过 `--status` 随时查看：

```powershell
python -m workflow.runner --project . --status --thread-id session_interactive
```

输出示例：
```text
[*] 当前 Thread: session_interactive
    - 当前 Stage: T2_CHECKED
    - 历史事件: 8 项
      * R1: Screened problems. Selected: ['P01']
      * R2: Ran check_research. Status=PASS
      * R4: Freeze brief completed. Frozen=True
      * Phase 0: User confirmed spec: {'main_title': 'Lucas定理专题研析', 'lesson_mode': 'guided', 'confirmed': True}
      * T0: init_project finished. Success=True
      * T1: Architect check status=PASS
      * T2: Issued broker packets for ['task-p01'] in run run_9901
      * T2: All T2 quality gates PASSED.
```

---

### 4.4 场景 D：导出与查看可视化计算图

想要直观了解图的连接关系，运行：

```powershell
python -m workflow.runner --visualize
```

终端会输出标准的 Mermaid 代码，可直接复制到任何 Markdown 渲染器或 GitHub 中查看流程图。

---

## 五、 核心代码拆解（读懂每个文件）

`skills-langgraph/workflow/` 目录结构极其精炼：

### 1. `workflow/state.py` —— 全局状态中枢
使用 Python 强类型 `TypedDict` 定义。核心字段包括：
- `stage`：当前阶段字面量（如 `"R1_SCORED"`, `"PHASE0_BREAKPOINT"`, `"T4_DELIVERED"`, `"T4_FAILED"`）；
- `selected_problems`：入选题列表；
- `brief_selected_problem_ids` / `selection_mismatch`：冻结 brief 的权威题单与不一致原因；
- `brief_frozen`：教研交接包是否成功冻结；
- `user_spec`：用户对齐规格；
- `t2_gates`：T2 各项门禁检查结果字典；
- `pending_dispatch` / `dispatch_results` / `worker_available`：认知工位派发单、派发结果、是否注入了 worker；
- `broker_failures`：broker 子命令失败清单（命令、任务、退出码、stderr）；
- `deliver_gate` / `delivery_result`：T4 的 deliver 结论与 promote/deliver/manifest 逐项结果；
- `regenerate_materials` / `materials_overwrite_log`：骨架重新生成开关与覆盖清单；
- `routing_action`：控制下一步去向（`"PROCEED"` / `"REPAIR_TASK"` / `"RESEARCH_BACK"` / `"HALT"`）；
- `history`：不可篡改的时间线审计日志列表。

### 2. `workflow/graph.py` —— 状态机装配厂
这是整个系统的心脏：
- 通过 `workflow = StateGraph(CoursewareState)` 初始化；
- 挂载全部功能节点（教研 R1-R4、Phase 0 断点、T0-T4、认知工位派发/等待、返修与研究打回）；
- 用 `workflow.add_conditional_edges(...)` 绑定路由分支；
- 检查点器可注入：默认 `MemorySaver`（调试），CLI/UI 用
  `workflow/checkpoints/sqlite_saver.py` 的 `SqliteSaver` 持久化到 SQLite。

### 3. `workflow/edges.py` —— 分流道岔
每个函数只做一件事：**根据当前 state 的结果，返回下一个节点的字符串名称**。
```python
def route_after_t2_check(state: CoursewareState) -> str:
    action = state.get("routing_action", "PROCEED")
    if action == "PROCEED":
        return "t3_latex_build"         # 全部通过，去编译
    elif action == "REPAIR_TASK":
        return "repair_small_output"    # 局部瑕疵，去返修
    elif action == "RESEARCH_BACK":
        return "research_back"          # 实质漏洞，大退回
    return END                          # HALT：中止并保留 error_message
```

### 4. `workflow/nodes/agent_nodes.py` —— 认知工位派发
T1 架构计划、T2 学生任务、重新研学、定点返修都由这里派发。每个阶段拆成
`*_request`（写派发单 + 记录派发前产物摘要）与 `*_await`（派发 worker 或人工中断
+ 校验产物确已变化）两个节点。产物没出现、或与派发前完全一致时返回 `HALT`
并报错，不会无变更重试门禁。

### 5. `workflow/adapters/script_runner.py` —— 安全调用包装器
把原有 `scripts/` 下复杂的脚本执行、超时捕获、环境变量注入（`PYTHONPATH`、
`SKILLS_BRAIN_DIR`）以及输出 JSON 报告的解析统统标准化，并转化成结构化的
`GateReport`。**报告按本轮隔离**：运行前把旧报告移到 `.stale/`，运行后校验报告
确由本次调用生成，并联合校验进程退出码与报告 `status`——三者不一致即判失败，
绝不会把上一次运行的 PASS 当成本轮证据。

### 6. `workflow/checkpoints/sqlite_saver.py` —— 跨进程检查点
用标准库 `sqlite3` 实现的 LangGraph checkpointer，无需额外依赖。
`--run` 与 `--resume` 分属不同进程时，靠它按 `thread_id` 原地恢复。

---

## 六、 异常处理与自愈机制（门禁不通过怎么办？）

门禁拦截到不合规内容时，流水线按下面的路径分流。**返修与研究打回都不是“原样重跑”**：
两者都会先把门禁结论转成一份具体派发单（改哪个文件、按哪些 findings 改），派发给认知
worker 或人工 / Antigravity，校验产物确实变化后才重跑对应门禁；上限 3 次，超过即中止并
报告错误，不会无限重试。

```
                    ┌─────────────────────────┐
                    │ t2_broker_submit_and_check │
                    └───────────┬─────────────┘
                                │
                 ┌──────────────┴──────────────┐
                 ▼                             ▼
       [检测到局部小问瑕疵]             [检测到实质数学漏洞]
     (如：字符数超标、黑话暗示)         (如：公式假证明、探针矛盾)
                 │                             │
                 ▼                             ▼
      ┌──────────────────────┐      ┌─────────────────────┐
      │ repair_small_output  │      │    research_back    │
      │  → 生成定点修改派发单  │      │  → 撤销 freeze.json │
      └──────────┬───────────┘      │  → 生成重新研学派发单 │
                 │                  └──────────┬──────────┘
                 ▼                             ▼
         等待产物变化（无变化即中止）      等待研学产物变化
                 │                             │
                 ▼                             ▼
         重新提交 → 渲染 → 二次门禁        状态退回 R2 深度研学
         (上限重试 3 次)                  重新由 Solver 推导
```

T4 的三个结论也是分开记录的：`promote` / `deliver` / `manifest` 各自独立。只有本轮
`deliver` 通过、且 `release/manifest.json` 确由本轮生成（`run_id` 与生成时间对得上、
`result=delivered`、`build_skipped=false`）时才返回 `T4_DELIVERED`；否则返回
`T4_FAILED` + `HALT`，并且**不会把磁盘上遗留的旧 manifest 当成本轮产物**。

---

## 七、 进阶配置与二次开发（接入真实模型）

### 如何让认知工位调用外部的大模型 API？
在 `workflow/adapters/worker_registry.py` 里注入一个进程级 callable 即可；图内的派发节点
会自动改走 worker，不再挂起中断：

```python
from workflow.adapters.worker_registry import worker_registry

# 绑定你自己的 LLM 调用函数：(system_prompt, user_content) -> response_text
def my_llm_caller(system_prompt: str, user_content: str) -> str:
    # 比如调用 OpenAI, Anthropic, Gemini 或本地 vLLM
    # response = client.chat.completions.create(...)
    # return response.choices[0].message.content
    pass

worker_registry.set(my_llm_caller, name="my-model")
```

worker 返回 `{"files": {"<项目相对路径>": "<完整文件内容>"}}`；只能写派发单声明的产物
路径，越界写入会被忽略。没有注入 worker 时，派发节点会改用带 checkpoint 的
`interrupt()` 等待人工 / Antigravity 产物（见 4.2b）。

### 如何让检查点跨进程/跨重启保留？
CLI 与 UI 默认就用 `workflow/checkpoints/sqlite_saver.py` 的 `SqliteSaver`
（标准库 `sqlite3` 实现，无需额外依赖）。想自定义存储：

```python
from workflow.graph import create_courseware_graph
from workflow.checkpoints import SqliteSaver

saver = SqliteSaver("courseware_checkpoints.sqlite")
graph = create_courseware_graph(checkpointer=saver)
```

这样哪怕关掉终端甚至重启电脑，`thread_id` 中的所有状态、interrupt 与历史依然完整存在。

---

## 八、 常见问题与避坑指南 (FAQ)

### Q1: 运行 `--run` 后程序停在断点不动了，是死锁了吗？
**A**: 不是死锁，这是特意设计的 **Phase 0 物理人机断点**！流水线执行完教研阶段（R4 冻结）后，依据安全红线，必须由人确认《学案主标题》和《上课模式》。请直接运行 `python workflow/runner.py --resume --title "你的标题" --mode guided` 即可唤醒。

### Q2: 底层的 `broker.py` 和 `check_*.py` 还需要单独去跑吗？
**A**: **不需要！** 所有底层的校验脚本都已被 LangGraph 对应的 Node 自动调用。主 Agent 或开发者不需要在终端单独敲任何 `check_student_task.py`。

### Q3: 为什么每个命令都要加 `--thread-id`？
**A**: `thread_id` 是 LangGraph 用来区分**不同教研工程或不同会话会话分支**的唯一标识符。
- 同一个 `thread_id` 共享同一份存档；
- 比如针对第一章可以用 `--thread-id chapter_1`，针对第二章可以用 `--thread-id chapter_2`，彼此完全隔离，互不干扰。

### Q4: 如何运行自带的自动化测试？
```powershell
:: 单元 + 集成测试（图装配、断点恢复、派发、T2/T4 失败路径、跨进程检查点）
python -m pytest skills-langgraph/tests/test_langgraph_pipeline.py -q

:: 2.4.1 零信任门禁对抗测试（16 个用例，必须全绿）
python skills-langgraph/tests/run_bypass_matrix.py
```

### Q5: 为什么勾选了题目却没生效？
冻结 brief 是权威输入：`broker create_run` 只按 brief 的 `selected_problem_ids`
建立任务清单。界面/CLI 的勾选若与 brief 不一致，R1 会直接拒绝启动并在
`selection_mismatch` 里说明原因。要改题单请重新冻结 brief。
