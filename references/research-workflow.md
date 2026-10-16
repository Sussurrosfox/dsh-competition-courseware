# 教研流程（R0—R4）操作化说明

本文件是 competition-research 的运行流程引用。字段、枚举与版本以
`../schemas/` 为准；交接语义见 `../references/handoff.md`。
本文件只规定"怎么做"，不重复维护契约字段表。

## 状态机

```text
R_DRAFT -> R_SHORTLISTED -> R_STUDIED -> BRIEF_FROZEN
                         ^                 |
                         | RESEARCH_BACK   |
                         +-----------------+
```

- 状态记录在 `research/run.json` 的 `current_state`（枚举见 run.schema.json）。
- RESEARCH_BACK 不是状态，是 issue 的 `route`；接受后回到相应研究环节，
  重新复核并冻结新 revision。
- 仅选题请求的出口在 R_SHORTLISTED：交付 selection 记录即完成（见 R1 末节）。

## R0：课程约束与候选题目池

**输入**：授权范围内的题面、解答、讲义（旧 `knowledge-source/`、
`problem-source/`、`solution-source/` 可继续作为输入路径，登记进
`source_manifest`）；已知的课程目标、学生基础、课时、输出用途。

**动作**（主 agent）：

1. 形成课程约束记录，写入 `research/run.json`；缺少的信息写成明确假设
   （`assumptions`）。只有缺失项确实阻止选题时才向用户询问，不把一般性
   教研冻结变成人工批准流程。
2. 清点候选池，覆盖用户授权范围内的材料。既有代表题是**回归样本**，
   不自动成为下一轮课程的全部入选题；其他编号题目同样参加初筛。
   已知先备知识与有证据的"已教内容"须单独登记
   （`student_prerequisites` 的 `basis` 区分 verified / assumed）。
3. 每题登记：稳定 `problem_id`（全项目唯一）、题面位置、解答位置、
   来源状态（`source_status`）、结构特征（`structural_features`）和初步
   问题。
4. **来源状态与难度估计分离**：出处不详但本地题面可追溯记 `unverified`；
   不能把"估计为 CMO 预备难度"写成"来源于 CMO"。难度、赛事风格等属于
   估计，记入 `initial_notes`，不进 `source_status`。
5. 旧 maps（tool-map、problem-dossiers 等）可导出为兼容视图供参考；
   兼容表是视图，不是第二份权威数据，旧 PASS 不自动转成新版已核验。
6. **不可变输入清单（防哈希死锁）**：`brief.json` 中的 `source_manifest_ref`
   必须指向 `sources/manifest.json` 等不可变输入清单文件，严禁指向含可变
   运行状态（如 `current_state`）的 `research/run.json`。否则在 R4 冻结之后
   更新 `run.json.current_state = "BRIEF_FROZEN"` 会导致 `brief_digest` 重算
   突变，使刚冻结生成的 `freeze.json` 当场失效。
7. **工作区归整（self/ 隔离原则）**：
   为保持母目录（如各讲次根目录）题库原件、解答原件与讲义的纯净与只读，教研与后续教学阶段产生的所有结构化中间文件、资产档案与最终交付物，一律写入 `<chapter_dir>/self/` 工作区；以 `self` 作为各工具的 `project_root`，母目录严禁直接平铺混杂生成文件。
8. **严禁私自代拟/伪造源解答（绝对红线）**：
   清点候选题目时，若母目录原始题库缺少部分题目的解答，必须在 `candidates` 中记录 `solution_ref: null`、`source_status: "unverified"` 并在 `initial_notes` 中标明“源题缺解答”。**严禁 Agent 在母目录或 `sources/solutions/` 中自行编写所谓的解答文件充当原始输入源**。对无官方解答题目的深入教研推导，只能作为 R2 Solver Agent 的研究成果写入 `research/problems/<pid>/solution.md`，绝不能反向写入原始题库冒充源解答。

**出口**：`run.json` + 候选池写入 `research/candidates.json`
（逐题 `disposition = pending`）。

## R1：低成本初筛与题组候选

**入口**：候选池已建立；学生知识清单（来自课程约束）可用。

**动作**：

1. 主 agent 按候选题分组，派发初筛子 flash agent（`../prompts/screen.md`），
   每组只处理明确的候选子集。每题回答五个问题：
   - 这题能训练什么，学生需要做出的关键判断是什么？
   - 与其他题相比新增了什么？是策略对照，还是只换数字？
   - 前置负担、策略难度、计算难度和书写量分别怎样？
   - 在本课时间内能研究到什么程度？是否更适合课后或教师讲解？
   - 原材料有没有明显错误或缺口，值得投入多少研究？
2. 可选：运行 `../scripts/score_problem_value.py` 取得疑似重复/低增量
   题对提示。该输出只是**辅助信号**（status 至多为 review，永不阻断），
   不能代替主 agent 的教研判断。
3. 主 agent 汇总各组 task-result，将每题处理为
   `selected_for_research` / `reserve` / `excluded` 三类，**逐题附理由**，
   并写入 shortlist。比较维度：目标覆盖、题间增量、前置负担、可用时间。
4. 可选：对初选题组派发题组评审（`../prompts/group-review.md`），收集
   对照价值、覆盖缺口与替换建议后再定稿 shortlist。

**选题纪律**（主 agent 必须遵守）：

- **不依赖单一总分自动选题**；不凑固定题数；不强制每类方法一题。
- **题目不足时交付较小题组**（含单题精研），不编造题组。
- **同结构重复题可排除**，但排除理由必须具体（如"与 adj-poly-three
  同为保和调整下的主元分离结构，增量只是换了约束区间"），不能写
  "价值不高"之类空话。
- **不默认全选既有代表题**；代表题与其他候选按同一标准初筛。
- 源题缺解答不自动排除，但需在理由中明确证明研究成本。

**出口**：更新后的 candidates（逐题 disposition + reason）、shortlist、
`research/selection.json`（符合 selection.schema.json）及其可读视图
（按 `../templates/selection.md` 填写）。此时 `current_state = R_SHORTLISTED`。

**仅选题请求的出口**：用户只要求"帮我选题并说明理由"时，交付
`research/selection.json` + selection 可读视图即完成任务，向用户说明
理由与后续可选动作（深研 / 直接结束）；**不强制进入 R2，不生成 PDF**。

## R2：对入围题做数学与解法研究（四 Agent 认知协同）

> 承载文件：
> - 探针：`../prompts/probe-agent.md`、`../scripts/numerical_probe.py`、`../scripts/check_probe.py`
> - 错解：`../prompts/pitfall-agent.md`、`../templates/pitfall.md`
> - 求解：`../prompts/solver-agent.md`、`../templates/solution.md`、`../scripts/check_solution.py`
> - 审计：`../prompts/math-audit.md`
> 每题档案按 `../templates/dossier.md` 填写（权威数据为 `research/problems/<problem_id>/dossier.json`）；结构检查由 `../scripts/check_research.py` 执行。

**入口条件**：`problem_id ∈ shortlist`；题面与（如有）解答路径已登记。只对入围题投入深研。

**执行子流程**：

1. **R2-P Probe（特例猜想探针，Agent 0）**：
   - 输入：题面、定义域、参数范围与工具限制；
   - 动作：按适用性依序执行对称点、边界退化、低维投影、微扰分析（或调用 `numerical_probe.py`）；
   - 输出：`research/problems/<pid>/probe.json`。记录精确式、近似值、逼近路径与等号可达性，并设计学生破冰问题；
   - 纪律：不适用时返回 `no_useful_result`，**严禁伪造数值**；数值结论只能作为猜想线索，不得直接充当证明。
2. **R2-F Pitfall（错解与死胡同解剖员，Agent 1）**：
   - 输入：题面、`probe.json` 摘要与学生前置，**严禁输入 Solver 解答或参考题解**；
   - 动作：模拟优秀学生的前两个第一冲动，构造 1~2 种自然误路，将其中最具迷惑性的一条写成编号伪证明（`pitfall.md`）；
   - 输出：`pitfall.json` 与 `pitfall.md`。标出首错位置、病理分类（方向倒挂/放缩过头/取等互斥/结构破碎）与草稿纸停止信号；
   - 纪律：禁手清单只保存抽象特征 ID，**严禁向学生泄露正解或突破口技巧**。
3. **R2-S Solver（严格破局与通法专家，Agent 2）**：
   - 启动约束：**必须在 Probe 与 Pitfall 完成后启动**；
   - 输入：题面、定义域、`probe.json`、`pitfall.json`、前置知识与板书线索；
   - 动作：
     1. **比较至少两条攻坚路线**，说明排除次优路线的理由及选择主攻路线的依据；
     2. 逐行严格证明（零跳步恒等变形）；**逐行注明初等代数定理依据**（每次应用均值/柯西/排序等不等式必须核对正负与等号条件）；
     3. 闭合等号条件与极限逼近过程；
     4. 书写"触发信号—构造目的—核验机制—适用边界"四要素的动机逆向解密；
   - 输出：`solution.json` 与 `solution.md`。逐项核对与 `probe.json` 的自洽性，提取全部闭合的证明义务（`proof_obligations`）；
   - 纪律：若与 Probe 存在矛盾且无法理论化解，只能返回 `needs_evidence`，不得强行宣布 verified；禁止用"显然"跳过证明义务。
4. **R2-Audit（数学与认知审计）**：
   - 输入：题面、`probe.json`、`pitfall.json`、`solution.json` 及证据引用；
   - 动作：逐项核对以下四类：
     1. **Probe-Solver 一致性**：Solver 得出的等号点与极值必须与 Probe 数值一致（`consistent`）或有理论解释（`explains`）；出现 `conflict` 立即 REVISE；
     2. **Pitfall 诊断质量**：伪证明定位首错是否准确，forbidden_feature_refs 是否无泄露正解特征；
     3. **Solver 证明义务**：所有 `proof_obligations` 是否全部闭合为 `verified`；
     4. **动机段**：四要素（触发信号/构造目的/核验机制/适用边界）是否完整且不超出域约束；
   - 结论：任何实质缺陷返回 `REVISE` 或 `NEEDS_EVIDENCE`；主 agent 审核通过后将正式 verified 写入 dossier。

**每题最小证据**（满足后方可视为研究完成）：
1. 原题、解答与知识源的核验，明确变量域和参数范围；
2. 完整、已复核的可行证明及关键证明义务闭合；
3. 与题目有关的边界、退化、等号和极限逼近检查；
4. 解法的触发信号、构造动机与适用边界；
5. 至少一条有教育价值的错解分析、伪证明与禁手卡（或明确无价值理由）。

## R3：学生障碍研究与所需资料设计

> **⚠️ 条件执行说明：R3 默认关闭（`config.yaml` 的 `enable_obstacle_study: false`）**
>
> - R3 在 LangGraph 流水线（`../workflow/graph.py`）中**没有对应节点**，不是流水线的必经环节；
> - 默认交付目标 student.pdf / student_solution.pdf **不消费任何 R3 产物**（学生窄包白名单仅 statement_ref + probe_ref + brief；学生解答版由 solution.md 生成）；
> - **开关关闭时**：`check_research.py` 对非空的 dossier.obstacles / dossier.supplements / brief.supplements 一律 fail-closed 拒收（fail 消息带 `[ORPHAN_R3_DISABLED]` 前缀）；dossier 契约允许 obstacles / supplements 为空数组，R4 冻结四件套（probe_ref / pitfall_ref / solution_ref / proof_obligation_refs）不含 R3 产物，R3 完全不做也能合法冻结；
> - **仅在以下情形才开启**：交付教师版（teacher.pdf），或声明 separate_hint / separate_review 视图且已打通 R3 产物到 materials.json 内容块的消费链；
> - **开关开启时**：`check_teaching.py` §3d 对每条 R3 产物做孤儿引用闭合检查——每条 dossier obstacle 必须有 materials.json 中同题 `block_id == obstacle_id` 的内容块消费；每条 dossier supplement 必须登记进 brief.supplements（再由既有 §3c 要求 materials 块覆盖）或有同题 `block_id == supplement_id` 的块；违反者 fail，消息带 `[ORPHAN_RESEARCH_ARTIFACT]` 前缀。
>
> 以下正文为 **R3 开启时的操作规范**；默认关闭时跳过本节，R4 入口条件中的 R3 要求自动豁免。

> 承载文件：`../prompts/obstacle-study.md`（障碍研究）、
> `../prompts/supplement-study.md`（补充材料研究）；障碍表与补充
> 材料清单写入 dossier（`../templates/dossier.md` 第六、七节）。

**入口条件**：该题 R2 已完成四 Agent 研究并闭合；有依据的学生前置（无依据时列为教学假设）。

**动作**：

1. 派发 obstacle-study：将"这题不好教"拆成可观察、可处理的障碍。
   **吸收 Pitfall 的研究成果**：障碍表须新增以下三个字段：
   - `pitfall_ref`：关联本障碍对应的误路 ID（`pitfall.json` 中的 `attempt_id`）；
   - `observable_symptom`：对应 Pitfall 中的可观察草稿纸停止信号（`observable_stop_signal`），只记可在课堂巡视中观察到的纸面现象；
   - `intervention_question`：教师针对该症状的现场介入提问口径（一句话提问话术）；
   证据性质必须区分已核验事实 / 教学推断 / 待试教假设 / 真实课堂观察，仅凭推断不得填 classroom_observation。
2. 主 agent 汇总资料需求清单，按需派发 supplement-study：
   **优先采用 Probe/Pitfall/Solver 暴露的缺口**：
   - 特例反例与数值破冰题（源自 Probe）；
   - 伪证明找茬与放缩方向辨析短练习（源自 Pitfall）；
   - 核心代数算子与恒等变形验证任务（源自 Solver）；
   - 降维巩固与升维挑战变式（源自通法适用边界）；
   新增数学内容进入正式交接前须挂 math-audit 独立复核（dossier 中 review_refs 非空）。

缺少有价值的错误路线与"适合学生自主探究"是不同判断，不能从 no_useful_result 直接推出教学结论。

## R4：复选、教学设计与冻结

**入口条件**：shortlist 内题目完成 R2/R3（R3 默认关闭时该条件自动豁免，仅需完成 R2；或明确 needs_evidence 并已从候选入选中剔除）。

**动作**（主 agent）：

1. **复选**：深入研究后再选一次题——保留、替换、缩小题组或改成单题精研。允许研究结果推翻初筛；推翻记录写入 selection 可读视图（`../templates/selection.md` 的"复选推翻记录"一节），并更新 candidates 的 disposition/reason。最终有序 `selected_ids` 只由主 agent 写入 `research/selection.json`。
2. 对入选题冻结以下决定：
   - 教学目标、学生前置和需要新增的资料；
   - 题组位置、题间对照和迁移关系；
   - 教学模式及选择理由；
   - 关键任务、学生应留下的证据、提示触发条件；
   - 分项时间与全课总时间；
   - 可先给出的知识与必须延迟的信息；
   - 采用的证明、等号、边界结论和已核验补充材料；
   - **四件套硬性约束**：`brief.json` 的 `math_baselines` 必须完整包含每道入选题的 `probe_ref`、`pitfall_ref`、`solution_ref` 以及全部为 verified 的 `proof_obligation_refs`；四件套任一缺失或未审计，`freeze_brief.py` 必须拒绝冻结。
3. **时间纪律**：不套用"每题至少 20 分钟"等统一下限。独立时间应与目标、题难和总课时相容；允许合并复盘或不安排某个任务，但不可通过填一个时间数字宣称任务可完成。
4. 按冻结条件（handoff.md 第 1 节）核验后标记 BRIEF_FROZEN，产出 `brief.json`、`teaching-brief.md`、`freeze.json`；旧版归档 `research/revisions/`，不静默覆盖。冻结成功后将 `research/run.json` 的 `current_state` 更新为 `BRIEF_FROZEN`（前提是遵守 R0 第 6 条的不可变清单约定，否则更新状态将破坏刚计算的摘要）。

## 回退：RESEARCH_BACK 的处理

RESEARCH_BACK 是带证据的返修请求（issue，`route = "RESEARCH_BACK"`），
不是第三个生产阶段。完整流程见 handoff.md 第 5 节；本 skill 侧的处理：

1. 主 agent 收到请求后先**验证证据**（evidence_refs 是否支持 reason）；
   接受才开启研究新 revision，拒绝须记录理由。
2. 接受后：更新相应研究产物（题面/证明/等号/模式/题组，按回退目标
   定位 R1—R4 中的环节）→ 重新复核 → 冻结新 brief revision。
   在 LangGraph 流水线（`../workflow/`）中，打回后的重做按 **probe → solver
   两阶段**执行：先为每题派发探针与坑点重做（role=`probe`，产物
   `probe.json` / `pitfall.json`），全部产物逐单校验通过后，再基于修正结果
   为每题派发重新研学（role=`solver`，产物 `solution.json` / `solution.md`
   / `dossier.json`）。每题一份独立派发单与一个独立子会话，各题之间、以及
   probe 与 solver 之间的会话 ID（`subagent_conversation_id`）必须互异；
   solver 阶段必须等 probe 批次全部完成后才会启动。
3. 旧版归档 `research/revisions/`；依赖旧版的教学产物标 stale；
   复验满足 `resume_condition` 后 issue 置 resolved。
4. 提出请求不暂停不依赖该题的其他工作；可完成的题继续。
5. 同类返修再次失败时，主 agent 检查任务边界与输入证据，再决定缩小
   任务、换实现或补研究；预算或重试次数用完不能标成 PASS，保留
   needs_evidence 并按任务范围交付已完成研究。

## 调度与并行边界

- **角色安排**：不是每题固定启动十几个角色。按题目缺口和风险安排
  flash agent，允许多个角色合并为一次窄任务；数学复核与对应写作保持
  独立。无价值路线说明理由后结束，不靠增加 agent 数量补足材料数量。
- **并行**：同轮不同题可并行；同题的独立探索可并行。
- **串行**：汇总、同一共享文件（candidates/selection/run/brief）的
  修改、研究版本冻结与最终发布串行——这些只由主 agent 写入。
- **独立探索的首轮输入**：只给题面与学生知识清单，不先灌输标准解答；
  随后再与已核验路线比较，减少"看完答案再扮演不会做"的失真。
- 子任务统一返回 task-result（候选结论 completed / needs_evidence /
  no_useful_result / failed），不能自写 BRIEF_FROZEN 或正式 verified；
  主 agent 保存接收/拒收及理由。failed、超时、工具不可用是执行状态，
  不能冒充无教学价值。
