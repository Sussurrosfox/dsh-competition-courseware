# AGENTS.md — 工程约定与协作说明

本文件记录 skills-langgraph（竞赛教研与学案制作流水线，LangGraph 改造版）的
项目结构、既定约定与测试方式，供代码代理快速对齐。**修改了下列约定对应的
代码时，必须同步更新本文件。**

## 项目结构

```
workflow/            # LangGraph 编排引擎
  state.py           # CoursewareState 强类型状态
  graph.py           # StateGraph 组装（节点注册 + 条件路由）
  edges.py           # 条件路由函数（HALT 一律终止本轮执行）
  runner.py          # CLI / Python API 入口
  nodes/             # research / human / teaching / build / repair / agent 节点
  adapters/          # script_runner、llm_worker、worker_registry
  checkpoints/       # SqliteSaver
  ui/                # Web UI（PipelineManager 等）
scripts/             # broker.py 与 check_*.py 门禁（零信任执行基底）
schemas/  prompts/  templates/   # 契约、子 Agent 提示词、LaTeX 模板
tests/               # pytest 套件（test_langgraph_pipeline.py、
                     # test_batch_dispatch.py、run_bypass_matrix.py）
references/          # 流程引用文档（research-workflow、courseware-workflow 等）
```

## 本次改造确立的约定

### 批次派发引擎（`run_batch_dispatch`，workflow/nodes/agent_nodes.py）

- 认知工位按**批次派发单**执行：逐单串行派发、逐单 SHA-256 校验；产物缺失或
  与派发前完全一致即 HALT（绝不做无变更重跑）。
- `dispatch_results` 中 `status == "ok"` 的 sheet 在续跑时跳过，不重复派发。
- 无可用 worker 时一次性 `interrupt()`，payload 形如
  `{"type": "AGENT_DISPATCH_BATCH", "stage": ..., "sheets": [...]}`。
- worker 模式要求批次内**所有**未完成 sheet 的角色均
  `worker_registry.available_for_role(role)`，否则整批走 interrupt。

### 多角色 worker registry（workflow/adapters/worker_registry.py）

- `set(fn, name=...)` 注册默认 worker；`set_role(role, fn, name=None)` 注册
  角色专属 worker；`call_for_role` 角色优先、回退默认；
  `available_for_role(role)` 决定就地派发还是挂起；`clear()` 清空全部。
- **关键契约：每次调用必须是全新会话**，宿主不得复用上下文；probe 与 solver
  等角色的 `subagent_conversation_id` 必须互异。图只负责派发与校验，会话
  隔离由注入的 worker 保证。

### pending_dispatch 与 pending_dispatch_batch 分工

- `pending_dispatch`：单派发单（T1 架构计划、定点返修 repair）。
- `pending_dispatch_batch` + `batch_stage`：批次派发（T2 学生任务、T2 自审、
  RB probe / solver），每题/每子任务一份 sheet。

### T2 按题派发

- `node_t2_request_student_task` 为每个 expected task 生成一份 sheet（单产物
  `staging/<run>/tasks/<tid>/student-task.json`），`node_t2_await_student_task`
  调 `run_batch_dispatch(success_stage="T2_STAGED")`。

### Research-Back 两阶段

- 图链：`research_back → rb_probe_request → rb_probe_await → rb_solver_request
  → rb_solver_await → r2_deep_research`。
- probe 阶段 role=`probe`（每题 `probe.json`+`pitfall.json`）；solver 阶段
  role=`solver`（每题 `solution.json`+`solution.md`+`dossier.json`）。
- await 成功置 `state["rb_phase"]="probe_done"/"solver_done"`；solver 阶段在
  probe 批次全部完成前不得启动。

### T2 自审开关

- `enable_t2_self_review` 默认开启；仅当项目 `config.yaml` 显式写
  `enable_t2_self_review: false` 时跳过。
  开启时 `route_after_t2_await` 路由到 `t2_review_request → t2_review_await`
  （role=`student-task-review`，产物 `review-student.json`+定点修复后的
  `student-task.json`），再进 `t2_student_task` 核验。
  自审子 agent 持有 teacher-solution packet（含完整证明视野），
  是唯一能判定"设问链是否抵达解"的防线。

### R3 孤儿产物门禁（enable_obstacle_study，默认关闭）

- R3（学生障碍/补充材料研究）是默认关闭的条件执行环节，图中无 R3 节点；
  `student.pdf` / `student_solution.pdf` 交付链路均不消费 R3 产物。
- 项目 `config.yaml` 写 `enable_obstacle_study: true` 才启用；未开启时
  `check_research.py` §3.9 对任一非空 `dossier.obstacles` /
  `dossier.supplements` / `brief.supplements` fail-closed
  （fail 消息带 `[ORPHAN_R3_DISABLED]` 前缀）。
- 开关开启时 `check_teaching.py` §3d 逐条核对 R3 产物被 materials 内容块
  消费（`block_id == obstacle_id`）或登记进 `brief.supplements`，否则
  fail（消息带 `[ORPHAN_RESEARCH_ARTIFACT]` 前缀）。

### 配置开关读取口径（顶层键）

- 项目级布尔开关（`enable_t2_self_review`、`enable_obstacle_study` 等）
  一律为 `config.yaml` **顶层无缩进键**；读取入口有两处且口径已统一：
  `scripts/contract.py` 的 `read_project_flag`（检查脚本用，带 default
  参数）与 `workflow/nodes/agent_nodes.py` 的 `_read_project_flag`
  （流水线节点用）。两者均优先 PyYAML，回退逐行解析时**只匹配无缩进的
  顶层键行**（缩进的嵌套键一律跳过），任何异常按关闭/default 处理。
- 新增开关时两处口径不得再分叉；workflow 包就地解析，不引入 sys.path
  hack 复用 contract。

### state.py 可选字段

`pending_dispatch_batch`、`batch_stage`、`rb_phase`、`t2_self_review`
（均为可选；节点返回 None 以清空）。

### Phase 0 人工授权码（auth token）

- `node_phase0_breakpoint` 挂起前在 `<project>/.langgraph/phase0_token.txt`
  落盘一次性授权码（`secrets.token_hex(4)`，已存在则复用）；**token 值绝不进入
  interrupt payload**，payload 只携带 `token_file` 路径与警示文案。
- 恢复时 resume 值必须携带匹配的 `auth_token`，否则节点重新 `interrupt()`
  （payload 含 `[AUTH_FAILED]`），绝不推进；确认成功后删除 token 文件（一次性失效）。
- CLI `runner.py --resume` 强制校验 `--auth-token`：缺失/不匹配即
  `[PERMISSION_DENIED]` 退出（exit 2），不触碰检查点；Web UI 确认按钮自动从
  token 文件读取并携带（UI 是真人通道）。
- 红线：主 Agent 严禁自行读取 token 文件或静默 `--resume`，必须先
  `ask_question` 获得真人确认与授权码（AGY_TASK.md 模板已内嵌该警示）。

## 测试与检查

```powershell
python -m pytest tests/ -q          # 全部测试必须全绿
python -m ruff check workflow/ tests/   # 不得引入新问题（既有问题不顺手改）
```

- 测试工作区使用 `tests/_fixture.py` 的 `FixtureWorkspace`（独立临时目录 +
  隔离 brain 沙箱）；临时目录一律用 `tmp_path`/`tempfile`，不写仓库内路径。
- fixture（`run_bypass_matrix.build_project`）按 `templates/main.tex` 的固定
  引用装配 sec1–sec4 四个切片：内容块全部在 sec1，sec2–sec4 为纯注释占位
  切片（check_numbering 要求被引入切片全部存在，xelatex 构建亦同）。
- 每个测试 setUp/tearDown 必须 `worker_registry.clear()`，避免注册表串扰。
- 图编译冒烟由 `test_01_graph_compilation` 覆盖（含 mermaid 节点名断言）。
