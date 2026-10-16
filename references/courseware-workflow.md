# 教学流程（T0—T4）操作化说明

本文件是 competition-courseware 的运行流程引用。字段、枚举与版本以
`../schemas/` 为准；交接语义、揭示字段（level / audience /
available_after / delivery_channel）、摘要失效规则见
`../references/handoff.md`。本文件只规定"怎么做"，不重复维护
契约字段表。T0/T1 与 RESEARCH_BACK 由 B4 详写；T2 由 B5 填充；
T3/T4 由 B6 填充。

## 状态机

```text
T_DRAFT -> T_BUILT -> T_REVIEWED -> DELIVERABLE

旁路（任意阶段可触发）：
  RESEARCH_BACK  -> 教研新 revision（R1—R4 相应环节）-> 新 BRIEF_FROZEN
                    -> 旧版归档、受影响教学产物标 stale -> 回到 T0 重新核对
  TEACHING_FIX   -> 回 T1（措辞、实现缺陷，不走教研）
  LAYOUT_FIX     -> 回 T2（版面、构建缺陷，不走教研）
  真实试教记录    -> 教研修订 或 教学局部修订
```

- RESEARCH_BACK / TEACHING_FIX / LAYOUT_FIX 不是状态，是 issue 的 `route`
  （issue.schema.json）；主 agent 接受后才执行对应回退。
- 研究包过期（brief_digest 不一致）或 brief 新 revision 冻结后，绑定旧
  revision 的 materials 标 stale，必须回到 T0 重新接包核对，**禁止沿用
  旧 PASS**。

## T0：接包核对

### 前置询问（T0 正式核对之前，必须完成）

主 agent 进入接包核对之前，**必须先向用户提出以下询问并等待明确回复**，不得假设默认值自行跳过：

**问题 0：学案标题（`\title`）是什么？**

> 请提供封面标题（**默认值：学案**）。标题写入 `config.yaml` 的 `title` 字段，由 gen_config.py 注入为 `\pdMainTitle`；学生版封面显示 `\pdMainTitle`，教师版显示 `\pdMainTitle（教师版）`。若用户无特殊要求，可直接回复"使用默认值"，主 agent 填入"学案"。

**问题 1：教学模式（`lesson_mode`）确认**

> 教学模式唯一取值为 `guided`（分步引导，主探究线）：独立尝试后借助分步提示支架继续。如 brief 已明确记录，只需用户确认；如未记录，按 `guided` 写入并请用户确认。

**收到回复后的处理规则**：

| 用户选择 | `lesson_mode` |
|---|---|
| 确认分步引导 | `guided` |

主 agent 将以上选择同步写入：
- `teaching/user-spec.json` 与 `self/config.yaml` 的 `user_alignment` 区块；
- `teaching/architect-plan.json` 的 `lesson_mode` 字段（若 brief 未记录）。

**禁止**：在用户明确回复前自行假设并进入下一步。

---

**入口**：用户请求进入教学制作，且已完成上述前置询问。主 agent 逐项核对：

1. **冻结状态与认知引用完备性**：`research/freeze.json` 存在且通过 freeze.schema.json 校验，`brief_id` / `revision` 与 `research/brief.json` 一致。在四 Agent 认知工作流下，核实每道入选题均具备 `probe_ref`、`pitfall_ref`、`solution_ref`，且证明义务全部闭合；
2. **摘要一致**：用 `../scripts/contract.py` 重算 `compute_brief_digest(project_root, brief, exclude_refs=brief.freeze_basis.research_review_refs)`，与 freeze 记录的 `brief_digest` 比较。不一致 → 研究包过期，回到教研重新冻结或恢复研究文件，禁止沿用旧 PASS；
3. **schema_version 匹配**：brief / freeze / materials 的 schema_version 与 shared 契约版本一致；
4. **覆盖范围**：入选题、关键任务、补充材料清单、揭示计划可在教学侧全部落实；`open_items` 中 `blocks_handoff=true` 的项未处置时不得制作相应部分；
5. **发放约束可执行**：reveal_plan 的 level / audience / available_after / delivery_channel 语义明确，首轮发放视图（默认 `student_initial`）只接收 `available_after="initial"` 的 L0 块；
6. **工作区归整（self/ 隔离原则）**：教学制作的所有产物（双轨剧本 `teaching/`、统一内容源 `materials.json`、生成的学案与手册 Markdown、编译物 `build/`、最终交付 `release/`）一律统一创建并存放在 `<chapter_dir>/self/` 目录下；各检查与构建脚本以 `self` 为 `project_root` 参数传入。

**T0 规则（缺少有效交接包时）**：

- 用户要求**全流程**制作 → 自动先衔接 competition-research 完成 R0—R4 并冻结 brief，再回本 skill 的 T0；
- 仅有旧 maps 或一篇解答时，**不冒充已完成教研**；
- 用户**只要求排版已定材料**时，按其范围执行，并在产物中如实标明输入性质（如在 materials/交付说明中标注 `input_kind: legacy_typeset_only`），不声称经过两阶段教研。

**工具**：`../scripts/check_teaching.py` 的交接核对项即本小节的机器化；通过后才进入 T1。

## T1：教学剧本编导与内容制作

### 核心链路：`architect-agent -> architect-plan.json -> student/teacher material -> materials.json`

1. **阶段 1：剧本架构师规划（Architect Agent，Agent 3）**：
   - 输入：冻结的 `brief.json`、`probe.json`、`pitfall.json`、`solution.json`；
   - 动作：派发 `../prompts/architect-agent.md`，规划每题统一 Block 矩阵与双轨交付框架；
   - 检查：运行 `../scripts/check_architect.py` 验证 brief digest 绑定、双轨覆盖、初始 L0 纯净性与证明义务覆盖；
   - 产出：`teaching/architect-plan.json`。
2. **阶段 2：双轨并行撰写（学生任务写作与教师编辑）**：
   - **学生轨（`student-task.md`）**：按自然递进小问模型输出结构化 `student-task.json`（契约 2.4.0：每题 3~6 个梯度探究子问，每问仅 `subquestion_id` / `prompt` / `answer_space_mm`；2.4.0 起学生学案**已删除提示（hint）与应留证据（expected_evidence）字段**，引导完全融入递进设问），经确定性渲染器 `render_student_task.py` 自动排版并注入每问独立 `\ansspace` 留白；特例破冰素材来自 probe 的 `student_warmup` 与 L0 数值观察（窄包白名单真实保留的通道）；旧版"四幕模型"中的**错解辨析一幕因窄包白名单（statement_ref + probe_ref + brief）不含 pitfall 内容而暂无输入源、当前不启用**；**输入严格通过 `make_small_packet.py` 过滤，严禁混入 solution 或 L2 答案**；
   - **教师轨（`teacher-material.md`）**：完整呈现探针事实、巡视雷达、通法动机、完整证明与变式库，**必须完整覆盖所有证明义务**；
   - **辅助角色**：按需派发补充练习；
3. **阶段 3：主 Agent 注册统一内容源**：
   - 统一汇总合并至 `teaching/materials.json` 的 blocks 与 `teaching/classroom-plan.md`，严守单源多视图纪律。

### 并行分工（角色与输入矩阵）

| 角色 | 提示文件 | 输入 | 独占输出 |
|---|---|---|---|
| 剧本架构师 | `../prompts/architect-agent.md` | 冻结 brief + probe + pitfall + solution | `teaching/architect-plan.json` |
| 学生任务写作 | `../prompts/student-task.md` | 窄上下文输入包（`make_small_packet.py` 生成，仅含题干与 L0 特例观察，严格剔除答案与解析） | `teaching/generated/` 中 `student-task.json`，再由 `render_student_task.py` 渲染为 `teaching/content/` 的 `.tex` |
| 教师编辑 | `../prompts/teacher-material.md` | 完整已核验数学（dossier/proof/solution/brief 引用）与教学设计 | `teaching/content/` 中教师视图块 **TeX 正文文件**（`.tex`，不得为 `.md`） |
| 辅助资料制作 | 按 brief 补充材料清单窄任务派发 | 已核验例子、引理、迁移题内容 | 提示卡、局部练习、对照页、迁移页 TeX 块正文文件 |

> **内容块文件格式强制约束**：`teaching/content/` 下的所有块正文文件扩展名**必须为 `.tex`**，严禁写成 `.md`。`build.py` 通过 `\input{teaching/content/<block_id>.tex}` 将内容块嵌入 sec*.tex 切片，Markdown 文件无法被 XeLaTeX `\input` 装配，会导致 T2 编译报错退出码 1。块内正文使用对应 TeX 宏：独立作答留白用 `\ansspace{<行数>mm}`，教师专用内容放入 `\begin{solution}...\end{solution}` 或 `\begin{teacherNote}...\end{teacherNote}`，分步提示放入 `\begin{hintbox}...\end{hintbox}`。

- 子任务统一返回 task-result（候选结论 completed / needs_evidence / no_useful_result / failed），主 agent 接收后才把块登记进 `teaching/materials.json`；materials.json 只由主 agent 写入。
- 合并时主 agent 把每块的 block_id、problem_id、task_id、source_refs、brief_revision、content_ref（路径须以 `.tex` 结尾）、level、audience、available_after、delivery_channel、views、cognitive_role、forbidden_feature_refs 登记齐全。

### 单源多视图规则

- 学生包与教师包由**同一份 materials blocks** 按 `views` 生成；同一题面
  同一公式只有一个内容块，学生视图与教师视图引用同一 `content_ref` 的
  同一数学来源（source_refs 一致）。分工写作不等于维护两套可独立漂移
  的题面与公式。
- 研究包要求保留的**证明义务不可因压缩版面删除**；教师版证明块
  （task_id 可为 null）必须完整覆盖 brief.math_baselines 采用的
  proof_obligation_refs。
- `available_after` 不是 `initial` 的块不得进入首轮发放视图；须拆到
  separate_hint / separate_review 渠道的分阶段发放页。静态 PDF 不会因
  把提示印在留白之后就延迟揭示。

### 教学可改范围与禁止静默改变清单

**允许调整**（不需要回退教研）：

- 表述与措辞（不改变任务含义与数学内容）；
- 版面、栏数、纸张、留白安排；
- 不影响任务含义的顺序细节（如同一任务内的子问排列）。

**禁止静默改变**（改动必须走 RESEARCH_BACK）：

- 题目条件、变量域、参数范围；
- 证明结论、等号分类、边界结论；
- 题组顺序（selected_problem_ids 的顺序）；
- 教学模式（lesson_mode，唯一取值 guided）；
- 关键任务（task_id、学生应留下的证据、提示触发条件）；
- 揭示时机（reveal_plan 的 level / available_after / audience /
  delivery_channel）。

**版式与内容呈现禁止事项**（写作 agent 必须遵守，无论是否有 RESEARCH_BACK）：

- 严禁在任何交付物（学生版/教师版/提示页/复盘页）中出现**题目来源信息**：不得出现原文例题编号（如"例 4.41"）、命题编号（如"命题 4.45"）、书名章节引用、IMO/联赛年份标记等；
- 探究子任务不得用 `tcolorbox`/`hintbox`/`mdframed` 框包裹，一律写为普通 `enumerate` 列表；
- 节（section）标题以罗马数字 I、II、III… 开头，文字简洁（通常≤8字），不写"例题"/"问题"等泛称；
- 所有 box 框（`knowledgebox`/`hintbox`/`reviewbox`）使用黑白风格（黑色细线、白色背景），不得引入彩色背景或彩色边框。

发现需要**新增数学例子、改变提示或改模式** → 提交 RESEARCH_BACK；
不依赖该题的其他工作继续。


## 9. broker 门禁链路执行协议（v2.4.0）

为适配较小语言模型的指令遵循能力，本技能体系已从"大而全"架构重构为"减负解耦"的小模型友好架构；**T2 起的所有中间产物必须经 `scripts/broker.py` 可信执行边界流转**。子 agent 只写 `staging/<run_id>/tasks/<task_id>/`，不得直接写规范目录。

### 9.1 建立 run 与签发窄包（T2.0）

```powershell
# 1. 从冻结 brief 建立本轮任务清单（expected tasks，主 agent 不能临时增删）
python scripts/broker.py . create_run

# 2. 为每个入选题签发窄包 + 签名 receipt
python scripts/broker.py . issue_packet --run-id <run_id> --task-id task-<qid>
# 等价于 make_small_packet.py（broker 内部调用）：
python scripts/make_small_packet.py --project . --run-id <run_id> --task-id task-<qid> --kind student-task
```

**强制规则**：
- packet 大小硬限制 **6144 字节**，超过报错 `PACKET_TOO_LARGE`；
- packet 必须含 `packet_kind` / `run_id` / `brief_id` / `brief_revision` /
  `question_id` / `input_digests`（非空）/ `allowed_source_refs` /
  `output_fields_white_list` / `size_bytes` / `packet_digest`；
- **允许输入由冻结 brief 的 L0 引用（statement_ref / probe_ref）计算**，不接受
  调用方传入任意 source_paths；solution / proof / audit / review / 教师备注
  即使在读取层也不得进入 student packet（路径解析检查真实路径、符号链接与
  项目根越界）；
- 输出只能落在 `staging/<run_id>/packets/`，拒绝任意项目外输出路径；
- `issue_packet` 同时签发 HMAC 签名 receipt（run/task/packet digest /
  worker_session_id / input_digests / verifier 版本），receipt 是真实性证明，
  UUID 只是索引。

### 9.2 提示词调用与生成规则

**规范原则**：
1. **学生任务**：统一强制使用 `student-task.md`（产出 `staging/<run_id>/tasks/<task_id>/student-task.json`），严禁手写 `.tex` 绕过结构化中间层。
2. **返修循环**：失败时使用 `repair-small-output.md`，最多 2 次重试。

**调用与生成约束**：
- 输入包必须由 broker 签发，严格执行 ≤6KB 窄上下文限制，严禁混入 solution 或 L2 答案；
- 子 agent 输出字段必须是 packet `output_fields_white_list` 的子集；
- 不得将完整 dossier 原文或完整 study 材料转发给学生任务 Agent；
- 不得绕过窄包机制直接向 Agent 发送未经过滤的源文件。

### 9.3 提交、渲染与门禁顺序

**标准流程**：
```
create_run → issue_packet → 子 agent 生成 JSON（staging）
→ submit_task（schema + 白名单 + 来源摘要 + packet/receipt 绑定验签）
→ render_task（确定性渲染到 staging/<run_id>/rendered/）
→ run_gates（干净代码副本：build → 全部必经检查）
→ promote（门禁全过后原子提升到 teaching/generated/ 与 teaching/content/）
```

**详细步骤**：
1. **提交验证**：`submit_task` 拒绝时只进返修队列，不得写入规范目录；
2. **确定性渲染**：`render_student_task.py` 校验 subquestions 数量（3~6 个）、
   ID 唯一性、留白范围与禁止控制序列；数学片段 `$...$` 原样透传、文本段
   才转义；每题作成一个大题（`1.`, `2.`, `3.` 等，由 `probchain` 控制），
   其中各问做成小问（`(1)`, `(2)`, `(3)` 等，由嵌套 `enumerate` 控制）；
   学生学案删除提示与应留证据；每问生成 `\ansspace{<n>mm}`；输出带机器标记
   （renderer_version / input_digest / run/task/question 绑定）；
3. **门禁运行**：`run_gates` 先构建（版面以实际 PDF/日志为准），再在干净
   代码副本上运行 check_research / check_architect / check_teaching /
   check_student_task / check_student_visibility / check_numbering，
   汇总签名落盘 `staging/<run_id>/gates/summary.json`；失败轮次回滚本次
   预置的规范文件；
4. **提升**：`promote` 验签汇总、核对必经检查齐全且全为 pass、逐任务核对
   输出/渲染摘要后，原子提升 `teaching/generated/<qid>-student-task.json`
   与 `teaching/content/task-<qid>.tex`。

**退出码规范**：
- 0 = pass（可以进入下一环节）
- 1 = fail/review（review 是未完成状态：错误列表或疑似项须逐项处置）
- 2 = error（输入缺失或环境错误，立即停止）

### 9.4 规范化与合规约束

**学生任务规范约束**：
- 每道入选题必须在 `teaching/generated/` 中拥有对应的 `<qid>-student-task.json`（唯一规范名；`task-<qid>.json`、`student-task-<qid>.json` 等备用名兼容已删除）；
- 所有学生任务块必须由确定性渲染脚本从 JSON 编译，严禁手写 `.tex`；
- **学生学案排版规范**：`student.tex` 严格实行「每题一栏均匀排布」。在大8开横向双栏作业纸架构下，切片装配必须配合 `\pdColumnBreak` 强制分栏，每道代表题（题干 + 3~6 个探究子问 + \ansspace 留白）完整且均匀地占满一栏，严禁跨栏断开或多题挤栏；
- 只有注释或自报 UUID 而没有 broker receipt/digest 的文件一律拒绝；
- `lesson_mode` 必须同时绑定用户确认摘要（user-spec.json）与冻结 brief 的 teaching_decisions，任何模式都不能绕开学生任务 schema、来源与签名检查。

---

## RESEARCH_BACK 全流程

RESEARCH_BACK 是带证据的返修请求，不是第三个生产阶段。教学制作 agent
没有权限修改 research/ 正式内容。完整语义见 handoff.md 第 5 节；本侧
操作步骤：

1. **发现问题**（T1—T3 任一环节）：题面/证明/等号/新增例子错误；题目
   太难、模式不适合、需换题；计划中的提示本身违反目标；需要新增数学
   例子或改提示。
2. **写 issue**：由发现问题的子 agent 按 `../prompts/research-back.md`
   起草，主 agent 核实后写入 `issues/<issue_id>.json`，字段符合
   issue.schema.json 且照 meet.md B4 样例：
   `route="RESEARCH_BACK"`、`problem_ids`、`brief_id`、`brief_revision`
   （绑定当前版本）、`evidence_refs`（非空，无证据的请求不应被接受）、
   `reason`、`requested_change`、`resume_condition`、`status="open"`。
3. **主 agent 验证**：evidence_refs 是否支持 reason；接受（accepted）
   才开启研究新 revision，拒绝（rejected）须记录理由。
4. **教研更新**：相应研究环节（按回退表定位 R1—R4）更新产物 → 重新
   复核 → freeze_brief.py 冻结新 brief revision → 旧版归档
   `research/revisions/`。
5. **教学侧联动**：依赖旧 revision 的教学产物标 stale（blocks 的
   brief_revision 与新 freeze.revision 不一致即被 check_teaching 判
   fail），回到 T0 重新核对后重做受影响块；复验满足 resume_condition
   后 issue 置 resolved，resolution_log 逐项记录处置与复验引用。
6. **不暂停无关工作**：提出请求不暂停不依赖该题的其他题的制作；
   可完成的题继续。

**普通文字返修不走教研**：措辞、实现缺陷 → `route="TEACHING_FIX"`
回 T1；版面、构建缺陷 → `route="LAYOUT_FIX"` 回 T2。同类返修再次失败
时，主 agent 检查任务边界与输入证据，再决定缩小任务、换实现或补研究；
预算或重试次数用完不能标成 PASS。

## T2：版本渲染、发放和版面

**目标**：由统一内容源（teaching/materials.json + teaching/content/）按
视图装配并构建全部发放 PDF，保持单源多视图。原则（meet.md T2）：默认
交付 student.pdf / teacher.pdf / release-later/ / materials.json /
classroom-plan.md；不为隐藏提示删去题面必需条件；独立作答留白保留；
栏数纸张可配置。

### 视图机制（本阶段核心）

- **视图在 config.yaml 的 `views` 节声明**：每个视图一个编译入口与一个
  输出 PDF（`<entry>.tex` → `<entry>.pdf`）；块的 `delivery_channel`
  决定它进入哪个入口（in_main → student/teacher 主视图，separate_hint →
  hint-\<pid\>，separate_review → review-\<pid\>，teacher_only → 仅
  teacher 视图）。缺省必须声明 `student_initial` 与 `teacher` 两个
  in_main 视图；separate_* 视图按需声明。旧版 config.yaml（无 views 键）
  走兼容模式，gen_config 行为与旧版完全一致；旧字段（competition_mode
  等）全部保留，语义不变。
- **生成入口**：`scripts/gen_config.py --project <项目>` 生成
  config-class.tex / config.tex（含视图开关宏）与各入口文件。
  separate_* 视图在 materials.json 中没有任何内容块时**不生成空入口**
  （没有提示/复盘需求就没有空提示/复盘 PDF）；materials 块出现未声明
  视图即报错。入口缺失或 config.yaml 更新时 build.py 自动重跑
  gen_config。
- **装配层**：sec*.tex 中每个内容块以 `% block: <block_id>` 注释开头，
  紧随 `\ViewBlock{<views>}{\input{teaching/content/...}}`；views 列表
  必须与 materials.json 一致（check_numbering.py 字面核对）。不带
  `% block:` 的 `\ViewBlock` 是结构性容器（如避免空题链的题组容器）。
  probchain 主体须整体放入进主视图的容器，避免提示/复盘视图渲染出
  空 enumerate（编译错误）。
- **环境缺省安全**（templates/header.tex）：`knowledgebox` /
  `teacherKnowledgebox` / `hintbox` / `reviewbox` 默认仅 teacher 视图
  可见，学生侧视图须显式声明；`microknowledge` 缺省全视图可见（按 L0
  前置知识对待）。未声明 `\pdCurrentView` 的编译不定义任何视图开关，
  `\ViewBlock` 内容一律不输出——视图未声明时宁可不印，也不误印。
  v2 不再要求"每切片末必须知识结论框"。

### 构建与检查顺序

> **前置：模板外壳初始化（T2 第一次运行时必做，此后仅在模板更新时重做）**
>
> `build.py` 要求工作区（`self/`）中已存在完整的 TeX 外壳文件，这些文件不会自动生成——必须从 `competition-courseware/templates/` **手动复制**进 `self/` 根目录：
>
> ```bash
> # 步骤 0a：复制 TeX 外壳模板（仅新项目或模板更新时需做）
> cp competition-courseware/templates/main.tex        self/
> cp competition-courseware/templates/header.tex      self/
> cp competition-courseware/templates/footer.tex      self/
> cp competition-courseware/templates/student.tex     self/
> cp competition-courseware/templates/teacher.tex     self/
> cp competition-courseware/templates/config.yaml     self/
> ```
>
> 步骤 0b：按 `teaching/materials.json` 中实际出现的视图（delivery_channel 为 `separate_hint` / `separate_review` 的块涉及哪些 problem_id）编辑 `self/config.yaml` 的 `views` 节，取消注释并替换 `<pid>` 为真实 problem_id。**至少须声明 `student_initial` 与 `teacher` 两个 in_main 视图**。
>
> ⚠️ **缺少上述外壳文件时，`build.py` 将以退出码 2（缺少依赖）立即终止**，不会给出 TeX 编译错误——需先完成初始化再执行后续步骤。

0. **（已完成初始化后跳过）** 按上述前置操作将外壳文件复制到工作区；
1. `gen_config.py --project <项目>`（config.yaml 变更后必跑，生成 config-class.tex / config.tex 与各视图入口文件）；
2. `check_teaching.py <项目>`（T0/T1 结构门槛，构建前拦下揭示违规）；
3. `check_numbering.py <项目>`（装配完整性 + materials 块对应）；
4. `check_student_visibility.py <项目>`（视图隔离 + 学生可见正文的教师专属/答案标记）；
5. `build.py <项目>`：**同一工作目录内各视图串行构建**（各视图共享
   main.tex/config.tex/sec*.tex 与辅助文件；如需并行多个构建必须隔离
   工作目录，即复制项目副本）。xelatex 两遍、超时重试、日志告警分级；
   逐视图 SUMMARY 计数与 materials 期望核对；非教师视图日志须含
   "已剥离"标记；
6. `check_student_task.py <项目>`（学生任务质量统一门禁：覆盖/白名单/来源
   摘要/渲染映射/黑话/字符预算；**版面以实际 PDF 与 student.log 为准**，
   题目跨页或 Overfull \hbox > 10pt 判失败，例外须登记
   `teaching/layout-exceptions.json`）；
7. `compare_versions.py <项目>`：views 模式下自动把全部 audience=student
   的入口 PDF（student.pdf、hint-*.pdf、review-*.pdf）纳入禁用标记与
   \fillin 答案扫描；
8. `make_review_pack.py <项目>`：生成 teaching/review-pack/（按发放阶段
   组织：student_initial 页 → 各延迟块页 → teacher 页），含
   production_digest（scripts/contract.py 计算），供 T3 独立审查使用。

### 版面纪律

- 独立作答需要的留白应保留；重点检查题干与留白是否分离、同页是否提前
  暴露关键提示、双栏抽取是否错序；栏数和纸张可配置，不把某次排版问题
  推成一律单栏或禁用所有框。
- teacher 视图固定 A4 竖版单栏；hint/review 入口为 A4 竖版单栏
  （\pdCompactPage）；student_initial 沿用 config.yaml 版面。
- **Overfull `\hbox` 防御与版心自适应规则**：
  - 教师用书与 separate_* 独立页采用 A4 竖版单栏（版心相比大 8 开横版显著收窄）；
  - 数学长公式（超过两个等号的长连等式、或较长分式分子分母）**严禁单行连排**，
    必须主动使用 `\begin{aligned} ... \end{aligned}` 或 `\begin{split} ... \end{split}`
    在等号/不等号处折行；
  - 超过 2 列的对比表或参数表，必须局部添加 `\small` 或 `\footnotesize`，配合
    `\setlength{\tabcolsep}{3pt}` 或指定列宽（如 `p{...}`），严防表格溢出
    触发严重 Overfull `\hbox` 错误（> 10.0pt 会导致 build.py 直接判失败）。
- 字面检查全绿不构成语义合规：标题、microknowledge、knowledgebox、普通
  段落、图注按同一揭示规则由 T3 独立审查判定（reveal-levels.md）。


## T3：独立验收与返修

**顺序原则**：先生成草稿并编译出实际 PDF，再审读实际 PDF，修订后重建
受影响材料；不采用"未生成渲染页就已通过渲染审查"的顺序。

### 四种审查的受控输入

| 审查 | 提示文件 | 受控输入 | 检查重点 |
|---|---|---|---|
| 学生阅读审查 | `../prompts/student-review.md` | 学生前置清单、各阶段实际收到的页面（review-pack 按发放阶段组织的 stages.json / blocks.md / 页面文本与渲染页）；**不提供答案与写作自评** | 阅读顺序、任务清晰度、课前策略泄露（语义级） |
| 教师使用审查 | `../prompts/teacher-review.md` | 教师包 + classroom-plan + brief 交接计划 | 追问是否可执行、证明义务是否完整保留、分项时间与发放步骤是否相容、题组对照关系是否落实 |
| 数学复核（编辑层） | `../prompts/content-review.md` | 采用的数学基线（dossier/proof 引用）+ 生成正文 | 编辑是否改错条件、公式、等号或适用边界；发现原研究错误可发起 RESEARCH_BACK（写 issue，不自行改） |
| 结构与版面检查 | （脚本） | 源文件、日志、摘要及渲染页 | 版本剥离、文件装配、页栏关系、题目与留白关联 |

前三种审查输出符合 review.schema.json 的候选审查记录（scope 分别为
student_read / teacher_use / math_recheck），`subject_digest` 绑定
review-pack manifest 的 production_digest；结构与版面由脚本检查
（check_numbering / build / check_student_visibility / check_student_task /
compare_versions）承担，其 review 级疑似项按下节处置。

### 独立性定义与分歧裁决

- 独立指审查者**未参与对应内容写作**、使用**新上下文**、**凭材料证据**
  判断；`reviewer_task_id` 记录执行审查的独立任务，用于确认与写作者
  分离。
- 审查者不替写作者修改后自批通过；发现的问题以 findings / issue 交回
  主 agent 处置。
- **不同 agent 都说 PASS 不是正确性的投票证明**；分歧回到题面、证明
  和实际页面裁决。
- 学生阅读审查按真实发放顺序**盲读**，先提交学生视角记录再通读。

### review 级疑似项的逐项处置

- 审查记录 findings 中 status=open 的项必须逐条处置（resolved 或转为
  issue），deliver 核对时发现未处置项即拒绝。
- 脚本报告 status=review 的疑似项（如 check_student_task 的题面受保护
  token 复现确认、compare_versions 的疑似答案项、check_teaching 的
  不生成理由确认）须逐条登记到处置记录：约定文件
  `research/review-notes.json` / `teaching/review-notes.json`，含
  `dispositions` 列表，每项 `{"report", "finding", "action",
  "category", "finding_hash", "actor", "evidence_refs"}`，`finding` 为
  对应 finding 消息的子串，逐条对应；
  **不能用一份非空"已复核"文件概括放行**（deliver 第 5 条机器核对）。
- **公共数学片段误报与处置规程**：
  题干公式（如已知条件、目标求和/乘积式）必然在题干块（L0）、学生任务（L0）、
  提示块（L1）和解答（L2）中复现，字面/归一化匹配常触发 review 告警。处置时
  必须在 `review-notes.json` 中逐条提取每个报警消息子串，在 `action` 中明确
  说明：“该公共片段为题面已知数学表达式/记号复述，不含策略方向或解题结论；
  且该块严格按 reveal_plan 由视图隔离（或延迟发放），学生初始端无解答内容”，
  并引用证据文件。

### 摘要失效与单向时序纪律

失效规则全文见 handoff.md 第 3 节；要点：改动版面（模板、栏数、装配
顺序、资源）只使对应构建与版面检查失效；改变教学措辞使该内容块审查
失效；改变题面/证明/模式使依赖它的 brief 与全部教学审查失效。审查
记录以 subject_digest 绑定摘要，内容改动后旧结论自动失效，须重新
审查绑定新摘要。deliver 在摘要比对不一致时按 contract.affected_set
列出变动文件与受影响节点，主 agent 按依赖重做，不无限重跑全部 agent。

- **单向时序纪律（One-Way Causal Lock）**：
  交付必须严格遵守单向因果顺序：
  $$\text{内容冻结} \longrightarrow \text{build.py (编译生成 PDF)} \longrightarrow \text{make\_review\_pack.py (计算 production\_digest)} \longrightarrow \text{独立审查记录签名 (subject\_digest 绑定)} \longrightarrow \text{deliver.py}$$
  **严禁逆向微调**：在审查 agent 签名之后，任何对正文、TeX 模板甚至单个
  空格的修改，都会使当前重算的 `production_digest` 发生突变，导致
  `deliver.py` 立即以“审查记录绑定的是过期 production_digest”阻断发布！
  任何正文微调后必须重新生成 review-pack 并重新签署审查记录。

## T4：交付与反馈

### 交付门禁（deliver.py）

`../scripts/deliver.py <项目根> [--skip-build] [--json PATH]
[--pilot-status STATUS]` 按 meet.md B6 的固定顺序执行并逐项核对
八条（脚本 docstring 逐条对应）：

```text
check_research -> 校验 freeze
-> check_architect / check_teaching / check_student_visibility /
   check_numbering（纯读取，并行）
-> build（串行；--skip-build 仅技术测试场景）
-> check_student_task（构建后串行：版面以实际 PDF/日志为准）
-> compare_versions -> make_review_pack（读取已生成产物，与构建串行）
-> 独立审查记录与 issue 核对 -> deliver 判定
```

- 退出码：0=交付成功 / 1=拒绝（逐项列出拒绝原因）/ 2=用法错误；
  拒绝与通过都落盘 deliver 报告（默认 teaching/checks/deliver.json，
  --json 可覆盖），全部中间检查报告落盘可追溯。
- 判定读取各报告的 status 字段（handoff.md 第 4 节转换规则），不只
  看进程退出码；把脚本状态词（pass 等）写进审查记录的 conclusion
  会被 schema 校验拦下并拒绝。各报告还必须绑定当前契约版本
  （schema_version == CONTRACT_VERSION）与门禁代码摘要（code_digest），
  旧版本报告不得当作新门禁证据。
- 通过后生成 `release/`：student.pdf、teacher.pdf、release-later/
  （有 separate_hint / separate_review 产物才生成）、manifest.json
  （含 brief_digest / production_digest / build_digest / 审查记录
  引用 / pilot_status / schema_version / code_digest）。
- **--skip-build 只允许技术测试场景**：只写 deliver 报告（result=
  technical_test_only），**不得创建、覆盖或更新 release/manifest.json，
  也不得复制正式 release PDF**；正式交付必须不带 --skip-build 重跑。
- 缺依赖、证据不齐、局部 PASS 冒充全课通过、过期摘要、未处理 issue、
  review 未处置均为拒绝项；`pilot_status: not_run` 不阻止交付（meet.md
  T4：真实试教不是本次自动制作的强制外部依赖）。

### 真实反馈回路

只有收到**真实反馈**才记录试教数据，字段：开始书写时间、尝试路线、
卡点、提示后动作、证明缺口、迁移表现。规则：

- **记录数据出处**：试教记录放入 `pilot/` 或 `trial/` 路径（与
  check_research 对 classroom_observation 证据的路径约定一致），
  不允许无出处的课堂事实；
- **模拟预测单独保存**：模型模拟的学生表现只作为设计参考另行存放，
  不得填写"多数学生已掌握"等虚构课后事实；
- **反馈分流**：反馈若改变选题或教学设计 → 回教研（RESEARCH_BACK /
  新 revision 流程）；若只是版面或措辞问题 → 在教学阶段修订
  （TEACHING_FIX / LAYOUT_FIX），并按摘要失效规则重跑受影响检查与
  审查，不无限重跑全部 agent。

## 调度与并行边界

- **角色安排**：按题目缺口和风险安排 flash agent，四种角色可合并为
  窄任务；数学复核与对应写作保持独立。
- **并行**：同轮不同题可并行；同一题的学生写作与教师编辑可并行
  （输入视图不同、输出文件独占）。
- **串行**：materials.json 的写入、task-result 的接收/拒收、issue 的
  状态变更、与教研侧的交接（RESEARCH_BACK 接受、新 revision 接包）
  只由主 agent 执行。
- **写作上下文隔离**：学生任务写作 agent 的上下文不含答案文本与
  禁止提前给出的信息；教师编辑获得完整证明与教学依据；二者不共享
  上下文。
- **RESEARCH_BACK 不停全局**：提出请求只影响依赖该题的块；其余并行
  任务继续。
