# 教研—教学交接协议（handoff）

本文件是 shared/ 契约的人类可读说明。**字段、枚举与版本以 schemas/ 为准**
（当前 CONTRACT_VERSION = 2.4.0，见 schemas/SCHEMA_VERSION 与 contract.py）；
本文件解释语义与规则，不另维护一份可能冲突的字段表。

## 1. 两个 skill 如何交接

- 教研（competition-research）交付 `research/brief.json`（机器可读，权威）与
  `research/teaching-brief.md`（同一内容及其引用证据的可读视图，不另存事实表）。
- 冻结记录在 `research/freeze.json`，其中保存 `brief_digest` 与
  `dependency_digests`（各引用文件的 SHA-256）。冻结由主 agent 按 meet.md
  第四节的六条冻结条件执行，不默认等待用户审批；修改形成下一 revision，
  旧版归档于 `research/revisions/`，不静默覆盖。
- 教学（competition-courseware）在 T0 接包时核对：存在有效 BRIEF_FROZEN、
  `schema_version` 匹配、`brief_digest` 与 freeze 记录一致、覆盖范围与
  发放约束可执行。仅有旧 maps 或一篇解答时，不冒充已完成教研。
- 教学 skill 只获得这份包及其引用的已核验研究内容，不默认读取整个原始
  题库、全部研究候选和被否定草稿。学生内容写作者只收可见材料与任务要求；
  教师编辑获得完整证明与教学依据。

### 1.1 四专职 Agent 协同与认知资产流转 (Cognitive Multi-Agent Pipeline)

系统引入基于认识论规律的四专职 Agent 协同，将传统静态讲义升级为具有思维梯度的高阶研学资产：

```text
[题面/域]
   │
   ├──> Agent 0 (Probe)    ──> research/problems/<pid>/probe.json (对称点/退化极限/微扰/破冰问题)
   │        │
   │        └──> Agent 1 (Pitfall) ──> research/problems/<pid>/pitfall.json & pitfall.md
   │                 │                 (方向倒挂/过头放缩/伪证明/禁手卡，严禁泄露正解)
   │                 ▼
   └───────> Agent 2 (Solver)  ──> research/problems/<pid>/solution.json & solution.md
                     │                 (零跳步严谨证明/等号极限闭合/动机还原/探针对账)
                     ▼
           [Math-Audit & Gatekeeper] ──> 数值自洽/无跳步/无剧透断言
                     │
                     ▼
           [R4 冻结: brief.json + freeze.json] (必须完整包含 probe_ref, pitfall_ref, solution_ref)
                     │
                     ▼
             Agent 3 (Architect) ──> teaching/architect-plan.json (双轨编导)
                     │
                     ├──> 学生探究学案 (student-worksheet: 特例破冰 -> 递进探究小问 -> 独立作答；契约 2.4.0 仅 subquestion_id/prompt/answer_space_mm，已删除 hint/expected_evidence；"找茬巡诊/三级提示"因窄包白名单不含 pitfall 而暂无输入源、不启用)
                     └──> 教师交锋教案 (teacher-playbook: 时间留白 -> 卡点雷达 -> 追问话术 -> 完整证明 -> 变式；其中卡点雷达/追问话术依赖 R3 障碍研究产物，R3 默认关闭，需 enable_obstacle_study: true 且打通 materials 消费链后启用)
```

**契约流转纪律**：
- `probe.json` 提供客观计算证据（非证明）；
- `pitfall.json` 只能读取题面与 `probe.json` 摘要，**严禁读取 Solver 或正解**；
- `solution.json` 必须显式闭合所有证明义务（`proof_obligations`），并逐项核对与 `probe.json` 的一致性（`probe_reconciliation` 不得包含 `conflict`）；
- `brief.json` 的 `math_baselines` 在四 Agent 工作流下必须同时引用 `probe_ref`、`pitfall_ref`、`solution_ref`；
- `architect-plan.json` 必须在 R4 冻结后启动，单源映射到 `materials.json` 的 blocks，其中学生初始视图严格禁止包含 L2、未到期提示或 `forbidden_feature_refs`。

### 1.2 项目工作区与 self/ 目录隔离规范 (Workspace Isolation)

跨阶段协同中，为保持母目录（各讲次根目录）题库与原件的干净纯粹，统一采用 `self/` 隔离规范：
- **项目根（`project_root`）**：母目录只包含原生题面（`1.md`~`23.md`）、原始解答、原讲义和题库维护脚本。所有由 skills-c-v5 产生的数据集（`sources/`）、教研卷宗（`research/`）、双轨教学（`teaching/`）以及交付件（Markdown 学案/教案与后续编译的 `build/`、`release/`），均统一下沉于 `<chapter_dir>/self/` 目录下。
- **跨阶段路径基准**：教研阶段计算 `brief_digest`、冻结 `freeze.json` 以及教学阶段执行接包核对与审查（`check_research.py`、`check_teaching.py`、`build.py`、`deliver.py`）时，统一以 `<chapter_dir>/self` 作为 `project_root`，各契约内的相对路径（如 `sources/manifest.json`、`research/brief.json`、`teaching/materials.json`）均相对于此 `project_root` 进行解析。

### 1.3 数据源忠实度与严禁私自代拟解答 (Source Fidelity & Anti-Fabrication)

两阶段协同中，原始数据源的权威性与真实性是一切认识论模型的前提：
- **原始数据源不可篡改**：母目录原始题干、原始解答及原讲义，代表现实给定的输入。若某道题未附官方/原作者解答，必须如实反映为 `solution_ref: null` 与 `source_status: "unverified"`；
- **绝对禁止私拟源解答**：严禁任何 Agent 在母目录或 `sources/solutions/` 编写解答并伪称其为源题库解答；
- **解法生产的归属与边界**：对无官方解答题目的数学推导，只能作为教研阶段（R2 Solver Agent）的新增研究成果，落在 `research/problems/<pid>/solution.md`，并在档案中明确记录解法由来；在最终交付给师生的研学材料中，对原始缺解题必须明确标明“原题未附官方解答，留作自主研习/课后探索”，绝不能反向混入原始数据源。

### 1.4 语言风格规范（朴实简明准则）

跨阶段所有输出文档（包括教研案卷、教学剧本、学案、教案及习题解答）必须严格遵守朴实简明的语言风格：
- **拒绝噱头与浮夸词汇**：禁止使用“思维震撼”、“撕开假象”、“名医巡诊/找茬”、“车祸现场”、“致命硬伤/追问”、“秒杀神技”等煽情和戏剧化表述；
- **规范专业术语**：统一使用“特例试算与观察”、“常见错解辨析”、“分步提示与思考”、“规范证明与总结”、“常见卡点与启发提问”等客观严谨的数学教学语言；
- **表达清晰精炼**：行文直指代数核心与逻辑脉络，去除冗长空洞的修饰与口号。

## 2. 揭示与发放的语义（level / audience / available_after / delivery_channel）

这四个字段出现在 brief 的 `reveal_plan` 与 materials 的 `blocks` 中：

- **level（信息强度）**
  - `L0`：不泄露策略的信息（题面、约定前置、观察表等）；
  - `L1`：提示方向的信息（该看哪个量、该检查哪个约束）；
  - `L2`：关键步骤或结论（证明主线、等号分类、完整解答）。
- **audience**：`student` / `teacher` 的非空子集，谁能读到该块。
- **available_after**：学生何时收到。取值为 `"initial"`（首次发放即可见）
  或一个事件 ID（如 `attempt-p03-finished`，表示该事件发生之后才发放）。
- **delivery_channel（发放渠道）**
  - `in_main`：编入当前发放的主 PDF；
  - `separate_hint`：独立的提示页（release-later/），按需分阶段发放；
  - `separate_review`：独立的复盘页（release-later/）；
  - `teacher_only`：只进教师资料，任何学生视图都不得包含。

关键规则：

- **模式与揭示是两种约束**。lesson_mode（唯一取值 guided）
  决定教学目标；level/available_after/audience 决定信息安排。改成 guided
  不能使课前 L2 自动合规；把字样从 L2 改成 L0 不能代替语义审查。
- **静态 PDF 不会因为把提示印在留白之后就延迟揭示**。`available_after`
  不是 `initial` 的块，不得进入 `student_initial` 视图；须拆到
  `separate_hint` / `separate_review` 渠道对应的分阶段发放页。
- 题目本身已给出的待证常数、确已学过的通用知识，不机械判为泄露；
  审查的是它对本题尚待学生完成的判断有何影响。
- 不要为了隐藏提示把题面必需条件一起删去；L1/L2 只进入约定视图，
  标题、microknowledge、knowledgebox、普通段落和图注按同一规则检查。

## 3. 摘要与失效规则

摘要统一由 `scripts/contract.py` 计算（SHA-256，UTF-8 规范 JSON，
项目相对路径统一正斜杠）：

- `brief_digest` = brief 本身 + 其引用的研究文件与必要输入；
- `production_digest` = materials + 各块正文 + 配置、模板、main 装配顺序和资源；
- `build_digest` = 对应 PDF、日志 + 所依据的 production_digest。

**循环排除约定**：`brief.freeze_basis.research_review_refs` 指向的研究复核
记录，其 `subject_digest` 绑定的是 brief_digest，因此这些审查记录文件自身
**不参与** brief_digest 的 inputs 文件摘要（仍保留在引用列表中，绑定有效）；
调用 `contract.compute_brief_digest(..., exclude_refs=研究复核记录路径)`。
否则摘要与审查互相包含，不存在不动点。其余引用（dossier、证明、证据、输入）
全部参与摘要，任一改动都使 brief_digest 变化。

摘要元数据保存在 freeze/build/review 记录中，**文件自己的 digest 不纳入
自身计算**（避免循环）。失效判断比较摘要，**不只比较时间戳**。候选池中
未被使用的文件改变，由依赖图（`affected_set`）决定影响范围，不自动使
所有题目证明失效。

**可变状态排除约定（不可变输入清单契约）**：`brief.source_manifest_ref` 必须指向
`sources/` 下的**不可变输入清单文件**（如 `sources/manifest.json`），严禁指向
含可变状态字段的 `research/run.json`——否则冻结后主 agent 按提示更新
`run.json.current_state` 会使 run.json 的 SHA-256 改变，进而导致依据
`source_manifest_ref` 计算的 brief_digest 发生雪崩漂移，旧冻结记录立即失效，
引发 Gate 1 / Gate 2 校验死锁。运行状态只以 `freeze.json` 与 `run.json` 各自表达，
摘要对可变状态字段不知情。

**单向因果锁（One-Way Causal Lock）序列纪律**：
`production_digest` 严格绑定 `materials.json`、`content/*.tex`、模板与装配结构；
`make_review_pack.py` 将计算出的 `production_digest` 写入审查包模板中。
因此，教学阶段的执行顺序存在不可逆的单向因果约束：
`定稿 TeX 内容` $\to$ `build.py 构建 PDF` $\to$ `make_review_pack.py 生成审查模板` $\to$ `签署 reviews/*.json` $\to$ `deliver.py 交付门禁`。
在生成审查包之后，**严禁**再对 `content/*.tex` 或模板文件做任何修改（哪怕是修复排版溢出或微调空格）；
任何文件改动都会破坏 `production_digest`，导致 Gate 3 抛出 `subject_digest mismatch` 拒绝交付。
若必须修改 TeX 代码以解决排版或构建问题，必须重置因果链，重新完整跑通后半程流程。

改动什么使什么失效：

| 改动 | 失效范围 |
|---|---|
| 版面（模板、栏数、装配顺序、资源） | 对应构建（build_digest）与版面检查 |
| 教学措辞（某内容块正文） | 该内容块的审查（student_read / math_recheck 中覆盖它的部分） |
| 题面 / 证明 / 教学模式 | 依赖它的 brief（brief_digest 变化，freeze 失效）与全部教学审查 |
| brief 修订（新 revision） | 绑定旧 revision 的 materials 标 stale，须重新绑定与重审 |

主 agent 按依赖重做受影响部分，不无限重跑全部 agent；研究包过期或引用
文件变动时按依赖定位，不继续沿用旧 PASS。

## 4. 报告结论与脚本状态的转换规则

两套词汇分开，不能只看进程退出码：

- **审查报告结论**（review.schema.json 的 conclusion）：
  `PASS` / `REVISE` / `NEEDS_EVIDENCE`。
- **脚本检查状态**（contract.write_check_report 的 status）：
  `pass` / `fail` / `review` / `error`。

转换规则：

- 脚本 `pass` 只是结构检查通过，**不自动构成审查 PASS**；审查结论由独立
  审查者凭材料证据给出。
- 脚本 `fail` ⇒ 对应范围不能通过；修复后重跑该检查。
- 脚本 `review`（脚本无法判定的疑似项）⇒ 须有人工/独立审查逐项处置，
  **严禁概括放行**：
   - 典型场景：`check_student_task.py` / `compare_versions.py` 通过归一化
     匹配与子串比对（默认阈值 $\ge 12$ 字符）筛查题面在解答/提示/教师批注中的
     重复。对于数竞题目中的长公式、待证恒等式、已知题设条件在证明与分阶段
     提示中的合理引用，脚本极易报告 `status = review`（疑似泄露）。
   - **处置契约（Gate 5）**：必须在 `<stage>/review-notes.json`（如
     `teaching/review-notes.json`）的 `dispositions` 列表中逐项处置。
     每项必须包含：
     - `report`：报告相对路径（如 `"teaching/checks/check_student_task.json"`）；
    - `finding`：待处置消息中的特征子串（用于模糊匹配精确对应）；
    - `action`：具体处置动作（如 `"approved_exception"` 或 `"mitigated"`）；
    - `justification` / `evidence_refs`：合规理由（如题设已知在证明中的必然代入、
      分阶段提示允许引用的已知形式）及依据文件。
  - `deliver.py` 会逐条检验 findings，任一 review 级项未在 `review-notes.json`
    中命中即触发 Gate 5 拒绝交付。
- 脚本 `error`（输入错误、缺文件、工具失败）⇒ 是执行状态，按错误处理，
  不得解读为内容无问题。
- 审查 `REVISE` ⇒ 按依赖定位修订并重跑受影响检查；`NEEDS_EVIDENCE` ⇒
  保留并列明缺失项，预算或重试用完不能标成 PASS。
- 不同 agent 都说 PASS 不是正确性的投票证明；分歧回到题面、证明和实际
  页面裁决。

## 5. RESEARCH_BACK 流程要点

- RESEARCH_BACK 是**带证据的返修请求**（issue.schema.json，
  `route = "RESEARCH_BACK"`），不是第三个生产阶段。
- 触发情形（meet.md 第六节回退表）：题面/证明/等号/新增例子错误；
  题目太难、模式不适合、需换题；计划中的提示本身违反目标；发现需要
  新增数学例子、改变提示或改模式。
- 教学制作 agent **没有权限**修改 `research/` 正式内容，也不得以
  "在教师稿中暗改""只改 L0/L1 标签"代替回退。
- 流程：提交 issue（evidence_refs、reason、requested_change、
  resume_condition，status=open）→ 主 agent 验证并接受（accepted）→
  更新相应研究产物、重新复核、冻结新 brief revision → 旧版归档，
  依赖旧版的教学产物标 stale → 复验满足 resume_condition 后 resolved。
- 提出请求不暂停不依赖该题的其他工作；可完成的题继续。
- 措辞、实现、排版缺陷走 TEACHING_FIX（回 T1）/ LAYOUT_FIX（回 T2），
  不无必要地重做数学研究。

## 6. 机器记录约定

- 机器记录统一 UTF-8 JSON，按 schemas/ 校验；Markdown 用于证明、
  讨论和可读报告，不依靠解析自由文本或 pipe-table 决定状态。
- 引用一律使用项目相对路径、正斜杠分隔符；缺引用、重复 ID、未知字段、
  非法枚举都会被契约检查识别。错误 JSON 是输入错误，不让模型猜字段继续。
- 子任务统一返回 task-result（候选结论 completed / needs_evidence /
  no_useful_result / failed），不能自写 BRIEF_FROZEN 或正式 verified；
  主 agent 接收后才写正式记录。
