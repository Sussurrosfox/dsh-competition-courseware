# 课堂计划（classroom-plan）模板

> 主 agent 在 T1 合并后填写，随 `teaching/classroom-plan.md` 交付。
> 本计划必须与 `research/brief.json`（revision __）和
> `teaching/materials.json` 一致：题组顺序、关键任务、揭示时机照 brief
> 原文，不得在本文件中静默改变。权威数据为 brief/materials 的 JSON，
> 本文件是可读视图。

- brief_id / brief_revision：<brief_id> / <revision>
- input_kind：<normal_handoff | legacy_typeset_only>（如实标明输入性质）
- pilot_status：not_run（无真实试教数据时一律填 not_run）

## 1. 全课时间

| 环节 | 内容 | 分钟 |
|---|---|---|
| <环节名> | <对应 task_id 或活动> | <n> |
| 合计 | 不得超过课程预算 total_time_minutes | <Σ≤预算> |

## 2. 关键任务一览

| task_id | problem_id | lesson_mode | 任务要求 | 学生应留下的证据 | 分钟 | 提示触发条件 |
|---|---|---|---|---|---|---|
| <t1> | <p1> | <guided 等> | <照 brief description> | <照 brief student_evidence> | <n> | <hint_trigger 或 —> |

## 3. 实际发放步骤与停笔留白

> 静态 PDF 不会因印在留白之后就延迟揭示；每个非 initial 块必须有
> 明确的发放事件与渠道。每行写清：**哪个事件之后**发放**哪个
> delivery_channel** 的哪些块（block_id），触发条件是什么，是否包含停笔留白。

> **契约 2.4.0 学生侧主线**：探针破冰抛出 → 递进探究小问（每题 3~6 个）
> → 独立作答留白 →（可选）复盘讲评。学生任务只有递进小问
> （subquestion_id / prompt / answer_space_mm），无 hint 输出；
> "找茬诊断 / 三级提示页"因学生窄包白名单不含 pitfall、当前不启用
> （下表相应行已标注，可按需整行删除）。

| 序号 | 时机（事件） | 触发条件 | 停笔静思时长 | 发放内容（block_id） | delivery_channel | 载体（student.pdf / release-later 页 / 教师口述） |
|---|---|---|---|---|---|---|
| 1 | initial（上课即发） | 开课即发 | — | <L0 块：探针破冰、题面、递进探究小问> | in_main | student.pdf |
| 2 | <事件，如 probe-tested> | 学生完成特例试算后 | 留白 3 分钟 | <L0/L1 块：递进探究小问的分批抛出与独立作答留白> | in_main | student.pdf |
| 3 | <事件，如 attempt-blocked> | 学生尝试受阻 | 停笔 3 分钟 | <L1 块：找茬诊断与三级提示页>【当前不启用：学生窄包无 pitfall/hint 输入源】 | separate_hint | release-later/<页> |
| 4 | <事件，如 proof-written> | 全体完成独立作答后 | — | <复盘块：复盘讲评（可选）> | separate_review | release-later/<页> |
| — | 不发学生 | 教师全课参考 | — | <L2 教师完整证明与卡点雷达（卡点雷达需 enable_obstacle_study 开启后启用）> | teacher_only | teacher.pdf |

## 4. 教师动作与巡视雷达

> 本节"巡视卡点 / 可观察症状 / 现场追问口径"依赖 R3 障碍研究
> （dossier 的 obstacles/supplements）。R3 默认关闭（config.yaml 的
> enable_obstacle_study: false），需 `enable_obstacle_study: true`
> 且打通 materials 消费链后启用；未开启时本节仅登记教师凭研究记录
> （research/decision-log.md、probe.json 的 student_warmup 等）可得
> 的观察要点，不虚构障碍表。

| 环节 | 教师动作 | 对应巡视卡点/可观察症状 | 现场追问口径（出处） | 关联 block_id |
|---|---|---|---|---|
| <环节> | <观察草稿纸、何时介入、如何留白> | <如方向倒挂、盲目通分> | <dossier 障碍表 / 提问话术> | <block_id> |

## 5. 不生成说明

> brief 揭示计划中决定**不生成**独立材料的信息块在此逐条登记理由
> （如"info-x 由教师口述，不生成提示页"）。check_teaching 对未在
> materials 中实现的揭示计划条目会核查此处是否提及；提及不代表
> 批准，仍需 T3 审查逐项处置。

| info_id | 不生成理由 |
|---|---|
| <info_id> | <理由> |

## 6. 返修与 stale 记录

> RESEARCH_BACK 导致 brief 新 revision 后，受影响的块在此登记 stale
> 与重做状态；本表是记录，权威状态以 issues/ 与 check_teaching 报告
> 为准。

| block_id | 绑定 brief_revision | 状态（current / stale） | 关联 issue |
|---|---|---|---|
