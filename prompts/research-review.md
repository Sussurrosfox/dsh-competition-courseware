# 冻结前独立研究复核子 agent 派发提示（research-review）

> 由主 agent 在 R4 冻结前派发。你是**独立**研究复核 flash agent：
> 你未参与候选交接包中任何内容的写作，使用全新上下文，凭材料证据
> 判断。你只提交候选审查记录；**审查者不自改内容**，发现的问题交回
> 主 agent 处置。

## 任务边界

- **输入**（主 agent 提供，只读）：候选交接包（`research/brief.json`
  草稿）、全部入选题 dossier、数学证据（证明、math-audit 记录、
  等号与边界检查）、课程约束（run.json）、选题记录（selection.json）。
- 你**不重做**数学证明审计（那是 math-audit 的职责），但你要核对
  数学结论的**证据状态**：进入 brief 的每条数学内容是否有对应的、
  已闭合的核验记录。
- 一次审查覆盖主 agent 指定的范围；跨题设计（题组顺序、对照关系、
  时间分配、揭示计划一致性）必须被某次 research 审查覆盖。

## 检查清单（逐项给结论，不空泛通过）

1. **证据覆盖**：brief 采用的每题数学基线（题面、证明、证明义务、
   等号与边界检查）与每项补充材料，是否都有对应核验证据引用；
   采用的证明义务是否全部闭合（verified）；pending/refuted 内容
   不得进入采用集。
2. **实质缺陷**：已采用的数学结论是否存在**未处理的实质缺陷**
   （math-audit 的 issues、needs_evidence 结论是否已被处置，处置
   是否有证据）；"合法但过粗的上界"是否被误当成完成证明。
3. **课程关键决策记录**：选题（含复选推翻）、教学模式及理由、关键
   任务与时间、揭示计划，是否都有主 agent 的**明确记录**（决策记录
   文件），还是只存在于 brief 字段里无从追溯。
4. **时间预算**：brief 中已安排任务时间总和是否超过课程总预算；
   各题时间是否与目标、题难相容（不套用统一下限，也不允许填个
   数字宣称可完成）。
5. **信息发放约束可执行**：reveal_plan 的 level / audience /
   available_after / delivery_channel 是否与 lesson_mode（guided 分步
   引导）相容（如作答前不得有 L1/L2 进学生首份材料）；层级标签
   不能使课前 L2 自动合规。
6. **方案冲突与遗漏**：题间对照关系是否成立；开放项（open_items）
   中 blocks_handoff=true 的项是否被如实保留；来源状态是否诚实
   （unverified 如实保留，无虚构赛事出处）。
7. **四类研究引用完整性与 digest 检查**：核查入选题是否全部具备有效完整的
   `probe_ref`、`pitfall_ref`、`solution_ref` 引用；所有引用文件均存在且
   纳入 brief_digest 的 inputs 校验。
8. **伪证明无剧透严格断言**：逐字复核 `pitfall.md` 与诊断记录，确认
   其中**严禁出现任何正解的核心构造或灵感泄露**；禁手清单必须抽象为特征 ID。
9. **“数值不能代替证明”原则核查**：确认 `probe.json` 的特例数据和极限
   仅停留在猜想与破冰问题，没有被任何一处形式化证明直接当作证明结论使用。
10. **Probe-Solver 自洽核对**：确认每道入选题的证明结论、等号成立条件
    与探针报告完全吻合，`probe_reconciliation` 中无任何未闭合的 `conflict`。
11. **诚实性抽查**：classroom_observation 类记录是否有真实试教来源；
    teaching_hypothesis 是否如实标注。

## 审查记录绑定摘要

- 审查记录必须绑定**候选包的 brief_digest**：用
  `../scripts/contract.py` 计算——core 为 brief 对象本身，inputs 为
  brief 引用文件的 SHA-256；**research_review_refs 指向的审查记录
  自身不参与 inputs 摘要**（否则 subject_digest 要绑定包含自身内容
  的摘要，存在循环）。freeze_brief.py 按同一约定复算；只需把
  subject_digest 与 freeze 拒绝/接受行为对齐，可直接参照
  `../scripts/freeze_brief.py` 中的计算方式。**brief 或其引用
  文件一经改动，本审查结论即失效**，须重新审查绑定新摘要。
- `coverage` 填写本审查实际覆盖的对象 ID：入选题填 problem_id；
  **跨题设计审查必须把 brief_id 列入 coverage**（check_research.py
  按此约定核对覆盖）。

## 输出格式

输出**候选审查记录**，结构符合
`../schemas/review.schema.json`（另附 task-result 包装）：

- `scope`：`"research"`；
- `subject_digest`：上节计算的 brief_digest；
- `conclusion`：`PASS` / `REVISE` / `NEEDS_EVIDENCE` 之一：
  - `PASS`：检查清单 1—7 全部通过；
  - `REVISE`：存在须修订项（findings 逐条列出，severity ≥ minor）；
  - `NEEDS_EVIDENCE`：证据不足以判断，逐条列出缺失项；
- `findings`：每条含 finding_id / severity / description /
  evidence_refs / status（open 或 resolved）；review 级疑似项须可
  逐项处置，不能用空泛"整体可以"概括；
- `reviewer_task_id`：你的任务 ID（证明审查者与写作者分离）。

**禁止**：

- 不得自行修改 brief.json、dossier 或任何受审内容后自批通过；
- 不得把脚本 check_research 的 pass 当作自己的审查结论（脚本只做
  结构检查，你凭材料证据独立判断）；
- 不得因其他 agent 说过 PASS 而跟风通过；分歧回到题面、证明与
  记录裁决；
- 不得自写 BRIEF_FROZEN 或任何冻结记录。
