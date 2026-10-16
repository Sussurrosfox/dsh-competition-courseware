# 揭示层级与发放机制（reveal-levels）

本文件规定 competition-courseware 的揭示层级定义与发放机制。字段、枚举的
权威来源是 `../schemas/`（brief 的 reveal_plan 与 materials 的
blocks）；语义口径与 `../references/handoff.md` 第 2 节一致，
冲突时以 schemas 与 handoff.md 为准。

## 三个轴：level / available_after / audience

揭示不是单一维度。每个内容块同时具有三个相互独立的属性（再加一个发放
渠道 delivery_channel），判定任何块是否可发放时必须三轴一起看：

| 轴 | 取值 | 含义 |
|---|---|---|
| **level（信息强度）** | L0 / L1 / L2 | 内容本身泄露多少策略信息 |
| **available_after（时机）** | `initial` 或事件 ID（如 `attempt-p03-finished`） | 学生在哪个事件之后才收到 |
| **audience（读者）** | student / teacher 的非空子集 | 谁能读到该块 |
| **delivery_channel（渠道）** | in_main / separate_hint / separate_review / teacher_only | 经哪个编译入口/PDF 发放 |

**level（信息强度）与四 Agent 认知映射建议**：

- `L0`：不泄露策略的信息（题面、约定前置、观察表、可复核现象报告）。
  - **建议映射**：`Probe 特例破冰`（代入试算、反常现象观察）= L0；`Pitfall 伪证明找茬任务`（作为独立阅卷诊断任务）= L0 或 L1。**注**：该找茬映射当前为预留——pitfall 内容不进学生窄包（白名单仅 statement_ref/probe_ref），错解辨析通道未打通前此映射不产生实际块。
- `L1`：提示方向的信息（该看哪个量、该检查哪个约束；策略类别，不给出具体构造）。
  - **建议映射**：`三级渐进支架`（Level 1 微光线索、Level 2 方向指引、Level 3 算子钥匙）= L1 分阶段发放，必须配合 `separate_hint` 或按需发放。
- `L2`：关键步骤或结论（证明主线、等号分类、完整解答、唯一构造、精确调整式）。
  - **建议映射**：`Solver 完整严格证明`、`等号与退化极限`、`动机逆向解密` = 严格归为 L2 或 `teacher_only`；学生端初始视图严禁包含任何 L2 块。

**判定示例**：

- “代入 x=y=z=1 与 x=0.001 测算表达式的值”——L0（特例观察指令）；
- “观察分母带有加法根号，思考如何消除根号”——L1（策略方向）；
- “分子分母同乘 $\sqrt{x}$ 后对分母用基本不等式”——L2（核心钥匙算子）。

## 核心机制规则

1. **静态 PDF 不会因印在留白之后就延迟揭示。** `available_after` 不是
   `initial` 的块，不得进入 `student_initial` 视图（首轮发放 PDF）；
   延迟揭示只能靠 `separate_hint` / `separate_review` 渠道对应的独立
   入口（hint-\<pid\>.pdf / review-\<pid\>.pdf）按需分阶段发放。
   这是 diagnoses.md 的核心教训在新体系中的机制化：旧版把 L1/L2 内容
   印在同一份学生 PDF 的"留白之后"，学生在作答前即可读到。
2. **首轮发放视图只接收 `available_after="initial"` 的 L0 块。**
   check_teaching.py 在构建前拦下违规块；build.py 逐视图核对计数；
   compare_versions.py 对全部学生侧 PDF 做字面泄露扫描。
3. **同一规则检查一切载体**：标题、microknowledge、knowledgebox、
   普通段落和图注都按同一揭示规则检查，不存在"装在知识框里就豁免"
   的通道。check_student_task.py 的泄露比对输入已扩大到全部学生
   可见文本（题干、prompt 与渲染产物；已删除 hint 与 expected_evidence）。
4. **环境缺省安全**：`knowledgebox` / `teacherKnowledgebox` / `hintbox`
   / `reviewbox` 默认仅 teacher 视图可见，学生侧视图必须在第二可选
   参数中显式声明（如 `\begin{hintbox}[提示][teacher,hint-p03]`）；
   `microknowledge` 缺省全视图可见（按 L0 前置知识对待），可经第二
   可选参数限制。装配层 `\ViewBlock{<views>}{...}` 的视图列表必须与
   materials.json 一致（check_numbering.py 字面核对）。
5. **不要把 L2 改标成 L0/L1 来通过检查。** 层级标签只是声明，语义审查
   （T3 独立学生阅读审查）判定的是内容实质：改写后无字面命中但实质
   提示策略/结论的，仍属泄露。把字样从 L2 改成 L0 不能通过语义审查。
6. **题目本身已给出的待证常数、确已学过的通用知识**，不机械判为泄露；
   审查的是它对本题尚待学生完成的判断有何影响。不要为了隐藏提示把
   题面必需条件一起删去。
7. **模式与揭示是两种约束**。lesson_mode（唯一取值 guided）
   决定教学目标；level/available_after/audience 决定信息安排。
   改成 guided 不能使课前 L2 自动合规；层级标签必须与内容实质一致，
   不得为了通过旧检查而映射成其他取值。

## 各视图应含什么

| 视图 | 入口 | 内容 |
|---|---|---|
| `student_initial` | student.pdf | 首轮发放：`available_after="initial"` 的 L0 块（题面、任务、留白、前置知识） |
| `teacher` | teacher.pdf | 完整教师资料：题面、任务、提示、复盘、完整证明（proof_obligation_refs 全覆盖，不可因版面压缩删除） |
| `hint-<pid>` | hint-\<pid\>.pdf | 该题 available_after 为某事件的 L1 提示块（separate_hint） |
| `review-<pid>` | review-\<pid\>.pdf | 该题复盘块（separate_review；经审定可给学生看的结论回顾） |

没有提示/复盘需求的题不生成对应入口（gen_config.py 按 materials.json
判断，不生成空入口文件）。

## 检查分工（字面可机查 vs 语义审查）

- 可机查（构建前/构建后硬门槛）：块四元组可回溯 brief 揭示计划、首轮
  视图只含 initial+L0、L2 块 audience 不含 student、% block: 注释与
  materials 一致、逐视图计数核对、学生侧 PDF 字面禁用标记扫描；
- 语义审查（T3，独立审查者，盲读实际渲染页）：内容实质是否提示策略、
  揭示顺序是否满足教学目标、版面阅读顺序是否提前暴露关键信息。
  脚本全绿不构成语义合规。
