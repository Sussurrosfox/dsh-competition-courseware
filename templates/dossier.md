# 题目研究档案（可读视图模板）

> 本模板由主 agent 在 R2/R3 汇总时填写，产出
> `research/problems/<problem_id>/dossier.md`。
> **权威机器数据是 `research/problems/<problem_id>/dossier.json`**
> （结构以 `../schemas/dossier.schema.json` 为准）；本文件是
> 它的可读视图，不另维护可能冲突的事实表。字段冲突时以 dossier.json
> 为准。

## 头部

- problem_id：`________`
- 题面引用 statement_ref：`________`（项目相对路径）
- 探针引用 probe_ref：`________`（项目相对路径）
- 死胡同引用 pitfall_ref：`________`（项目相对路径）
- 严谨推导引用 solution_ref：`________`（项目相对路径）
- 动机解密引用 motivation_ref：`________`（项目相对路径）
- 证明引用 proof_refs：`________`（分号分隔）
- 来源状态：`verified | unverified | unknown`（出处不可定位时如实
  写 unverified，不得写未经证实的赛事名）
- 研究状态：`R2 完成 | R3 完成 | needs_evidence（列缺失项）`

## 一、已核验事实（math_fact / source_fact，verification=verified）

> 每条附证据引用（证明、math-audit 记录、计算记录）。正式 verified
> 只由主 agent 在接收 math-audit 候选结论后写入。

| claim_id | 断言 | 证据引用 | used_by |
|---|---|---|---|
|  |  |  |  |

## 二、教学推断（teaching_inference）

> 由已核验材料合理推出、未经试教的判断。如实标注，不得升级为
> 课堂观察。

| claim_id | 断言 | 推断依据 | used_by |
|---|---|---|---|
|  |  |  |  |

## 三、教学假设（teaching_hypothesis，待试教）

> 允许保留并交接，必须如实标注为假设。

| claim_id | 断言 | 假设理由 | used_by |
|---|---|---|---|
|  |  |  |  |

## 四、真实课堂观察（classroom_observation）

> **仅限真实试教来源**；无试教记录时本节留空。仅凭推断填写会被
> check_research.py 拒绝。

| claim_id | 断言 | 试教来源记录 | used_by |
|---|---|---|---|
|  |  |  |  |

## 五、证明义务清单（每题最小证据五项）

> 进入交接包的数学内容必须闭合（verified）。逐项核对：

1. **原题、解答与知识源核验**：变量域与参数范围已明确：
   - obligation_id / statement / status / evidence_refs
2. **完整、已复核的可行证明及关键证明义务**：
3. **边界、退化、等号与调整可行域检查**（等号分类含小参数复核，
   如 n=2；每步调整后与所有未选变量的约束逐项检查）：
4. **解法触发信号与适用边界**：
5. **值得保留的错误尝试、实例或替代路线**（无价值时留空并写明
   原因；注意 no_useful_result 不能推出任何教学安排结论）：

| obligation_id | 义务陈述 | status | 证据引用 |
|---|---|---|---|
|  |  | pending / verified / refuted / unverified |  |

## 六、障碍表（R3，含认知障碍症状与介入提问）

| obstacle_id | 关联误路 (pitfall_ref) | 学生知识起点 | 可能尝试 | 纸面表现 (可观察症状) | 受阻证据 | 修复目标 | 教学介入 (一句话提问) | 证据性质 | 可迁移内容 |
|---|---|---|---|---|---|---|---|---|---|
|  |  |  |  |  |  |  |  |  |  |

## 七、补充材料清单（教研产物）

> 每项有 parent_problem_id、用途、题面引用与核验记录；新增数学内容
> 进入正式交接前须经 math-audit 独立复核（review_refs 非空）。

| supplement_id | 用途 | 题面引用 content_ref | verification | review_refs |
|---|---|---|---|---|
|  |  |  |  |  |

## 八、尚未解决事项

> 未闭合的证明义务、needs_evidence 项、被排除路线的未决问题（允许
> 保留，但只有进入教学交接包的数学内容必须闭合）。**未解决的入选题
> 不能冻结。**

| 事项 | 类型 | 缺失证据 / 下一步 | 是否阻止交接 |
|---|---|---|---|
|  |  |  |  |
