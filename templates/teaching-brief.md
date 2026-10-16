# 教学交接包（可读视图模板）

> 本模板由主 agent 在 R4 冻结时填写，产出 `research/teaching-brief.md`。
> **`research/brief.json` 是权威机器数据**（结构以
> `../schemas/brief.schema.json` 为准）；本文件是 brief.json
> 及其引用证据的可读视图，不另维护一份可能冲突的事实表。字段冲突时
> 以 brief.json 为准。

## 1. 身份与版本

- schema_version：`________`（契约版本，当前 2.0.0）
- brief_id：`________`；revision：`__`；run_id：`________`
- source_manifest_ref：`________`

## 2. 课程约束

- 目标 goals：
- 学生前置 student_prerequisites（逐条标 basis：verified / assumed）：
- 总时长 total_time_minutes：`__` 分钟
- 明确假设 assumptions：

## 3. 选题

- 有序入选题 selected_problem_ids（顺序即题组顺序）：
- 备选与排除理由引用 alternates_ref：
- 复选推翻初筛的记录（如有）：

## 4. 每题数学基线

> 逐题填写；进入交接包的数学内容必须闭合。

| problem_id | 题面 statement_ref | 证明 proof_refs | 采用的证明义务 | 等号与边界检查引用 |
|---|---|---|---|---|
|  |  |  |  |  |

## 5. 教学决策

> 逐题：lesson_mode、objectives、key_tasks（task_id / 描述 /
> 学生应留下的证据 / time_minutes / hint_trigger）、mode_rationale。

**lesson_mode 与默认信息安排**（唯一取值 guided）：

| lesson_mode | 教学目标 | 默认信息安排 |
|---|---|---|
| guided | 独立尝试后借助分步提示支架继续 | 初始任务与 L1 提示分开，写明提示触发条件 |

**模式与揭示是两种约束**：L0/L1/L2 是**信息强度**（L0 不泄露策略、
L1 提示方向、L2 关键步骤或结论）；`available_after` 是**时机**
（学生何时收到："initial" 或事件 ID）；`audience` 是**读者**
（student / teacher）。三者不可互相替代：改成 guided 不能使课前
L2 自动合规；把 L2 改标 L0 不能代替语义审查。题目本身已给出的
待证常数与确已学过的通用知识不机械判为泄露；审查的是它对本题
尚待学生完成的判断有何影响。

| problem_id | lesson_mode | 模式理由 | 关键任务（含分钟数） |
|---|---|---|---|
|  |  |  |  |

时间纪律：不套用"每题至少 N 分钟"统一下限；已安排任务时间总和
不得超过课程预算（check_research.py 核验）。

## 6. 补充材料

| supplement_id | kind | problem_id | content_ref | verification |
|---|---|---|---|---|
|  | example / misstep / lemma / transfer / exercise |  |  |  |

所需资料清单（尚未制作但教学阶段需要的）：

## 7. 揭示计划

| info_id | problem_id | level | audience | available_after | delivery_channel |
|---|---|---|---|---|---|
|  |  | L0 / L1 / L2 | student / teacher | initial 或事件 ID | in_main / separate_hint / separate_review / teacher_only |

## 8. 验收要求

- 逐题完成标准 completion_criteria：
- 整课对照关系 lesson_alignment：
- 计划中的发放顺序 release_order：

## 9. 开放项

> 明确哪些只是教学假设（可交接、如实标注），哪些阻止交接
> （blocks_handoff=true：源题不成立、证明未闭合、学生所需材料缺失）。

| item_id | 描述 | kind | blocks_handoff |
|---|---|---|---|
|  |  | teaching_hypothesis / unverified_source / missing_material / other | true / false |

## 10. 冻结依据

- 主 agent 决策记录 decision_log_ref：
- 研究复核报告 research_review_refs（覆盖全部入选题与跨题设计，
  subject_digest 绑定本 revision 的 brief_digest）：
- freeze.json 的 brief_digest / frozen_at（冻结后回填）：
