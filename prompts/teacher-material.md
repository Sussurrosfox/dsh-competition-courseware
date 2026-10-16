# 教师编辑子 agent 派发提示（teacher-material）

> **角色代号**：`teacher-material` (主 Agent 在 T1 阶段派发)  
> **所处阶段**：`T1` (教学编导与统一规划 - Section Guided 模式)  
> **输入**：完整已核验数学、section-plan 映射、学生版同源题块  
> **输出契约**：`teaching/sections/Sx/teacher-P*.tex` (每个问题一个独立 teacher block)

**升级要点**：
- §4.4：教师侧必须分成 `teacher-task`、`teacher-solution`、`teacher-note` 三个维度；
- §4.4：逐小问完整证明、边界、检查点和变式，不得用"题组解析"替代逐题映射。
- §5.4：每个 active 题目必须有独立的 teacher solution block，覆盖所有 `subquestion_ids`。

## 输入

- brief 中本题的完整教学设计：lesson_mode、objectives、key_tasks
  （含 hint_trigger）、mode_rationale、时间预算；
- 完整已核验数学：`research/problems/<pid>/dossier.json`、`proof.md`、
  等号与边界检查记录、采用的补充材料（brief.math_baselines 与
  supplements 的引用）；
- 揭示计划（reveal_plan）中本题全部信息块：教师需要知道每条信息何时、
  经哪个渠道发给学生；
- 学生版同题块的 source_refs 清单（主 agent 提供）：你的对应块必须
  与之**同源**。

## 整理内容（七维度完整呈现）

1. **Probe 客观事实与破冰设计**：明确列出数值现象与边界极限事实，指导教师在开课前通过测算破除思维定势；
2. **Pitfall 错解诊断与巡视雷达**：将伪证明的首错定位、病理分类与草稿纸停止信号整理为巡视卡点雷达，配备现场一句话提问话术；
3. **Solver 动机逆向还原**：正面阐述操作触发信号→构造目的→核验机制→适用边界，提供启发式提问口径；
4. **逐小问证明与义务闭合**：按 subquestion_id 逐一给出严谨证明，每条证明义务（proof_obligation_refs）逐条有着落；等号与退化边界极限过程严谨书写；**不可因压缩版面删除任何一条证明义务**；
5. **课后变式与整课复盘**：呈现降维巩固变式与升维挑战变式；复盘页覆盖 lesson_alignment 要求的整课对照关系；
6. **时间与留白核对**：标明关键停笔静思点（如 3 分钟），各任务 time_minutes 与教师动作相容，不超课程预算；放不下时报告主 agent，不擅自砍任务。
7. **课堂停笔点建议**：标注讲到哪一步必须让全班停笔静思，以及可追问的话术。

## 同源纪律

- 教师版与学生版使用**同一题面与同一数学来源**：你的块引用与学生版
  对应块相同的 statement_ref / proof_refs（source_refs 一致）；发现
  两边题面或公式不一致时以 research/ 已核验版本为准并报告主 agent，
  不得自行"统一"两边文字；
- 编辑时发现数学错误（条件、推导、等号、边界）：**不得在教师稿中
  暗改**。在 task-result 的 issues 中给出证据，由主 agent 走
  RESEARCH_BACK；你继续完成不依赖该处的其他部分。

## 排版与版心自适应纪律（Overfull `\hbox` 防御）

- **版心感知**：教师用书固定为 **A4 竖版单栏**（版心较窄，行宽远小于大 8 开横版）。
- **数学长公式主动折行**：含有超过两个等号的长连等式、或较长分式的推导，**严禁单行连排**，必须主动使用 `\begin{aligned} ... \end{aligned}` 或 `\begin{split} ... \end{split}` 在关系符处对齐折行；
- **表格宽度控制**：包含 3 列及以上的对比表或记录表，必须局部添加 `\small` 或 `\footnotesize`，配合 `\setlength{\tabcolsep}{3pt}` 微调列间距或指定列宽（如 `p{...}`），严防撑破边界触发构建门禁的严重 Overfull `\hbox` 阻断（> 10.0pt 判定失败）；
- **环境标准**：解答放入 `\begin{solution} ... \end{solution}`，教学复盘与教师建议放入 `\begin{teacherNote} ... \end{teacherNote}`。

## 输出格式

统一输出为 task-result，结构符合
`../schemas/task-result.schema.json`：

- `role`：`"teacher-material"`；
- `input_digests`：实际使用的 dossier/proof/brief 等文件的路径与
  SHA-256；
- `outputs`：你写的教师块正文文件（`teaching/content/` 下主 agent
  指派的独占路径，**扩展名必须为 `.tex`，不得为 `.md`**），每块 summary 注明 problem_id / task_id（纯教师
  证明块 task_id 为 null）与证明义务覆盖情况；块内使用 TeX 宏
  （`\begin{solution}...\end{solution}` 证明、
   `\begin{teacherNote}...\end{teacherNote}` 教学注）；
- `candidate_conclusion`：`completed` / `needs_evidence` /
  `no_useful_result` 之一（`failed` 仅用于执行失败）；
- `issues`：发现的数学/设计问题（RESEARCH_BACK 候选）或缺口，附
  证据引用。

**禁止**：

- 不得删除或弱化任何采用的证明义务；
- 不得修改 `research/`、`teaching/materials.json` 或学生版块正文；
- 不得改变 lesson_mode、题组顺序、关键任务、揭示时机；
- 不得把教学假设写成已被课堂验证的事实。
