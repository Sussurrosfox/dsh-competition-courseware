# 2.4.0 升级与审计整改记录（question_it_aud.md 执行报告）

本文件记录 `skills-cleaned/` 相对 2.3.0 的改造内容、验收证据与**剩余边界声明**。
审计结论（question_it_aud.md）：三项改造方向均可实施，但在同一身份拥有全部
写权限时，任何只在 Skill 文档/schema/普通脚本中的限制都是协作规范，不是物理
边界。本次改造把可机器判定的部分全部落地，并把无法落地的部分如实降级标注。

## 一、清理验收（阶段 0）：删除而非 legacy

按审计与用户要求，以下过时内容**直接删除**，不以 legacy 版本保留：

| 删除项 | 原位置 | 理由 |
|---|---|---|
| 空/旧装配模板 | `templates/problemcase.tex` | 无脚本引用，且引用不存在的 `competition-case-template.tex`；sec1.tex 即装配写作层 |
| 重复 schema 目录 | `scripts/schemas/`（23 个副本） | 与 `schemas/` 逐字节相同；唯一权威目录为 skill 根 `schemas/`，`contract.py` 已改指 |
| 三个旧 linter 入口 | `scripts/check_prompt_shape.py`、`check_guidance_leak.py`、`check_small_outputs.py` | 统一为 `check_student_task.py`（发现逻辑与状态语义单一来源） |
| `__pycache__` | 全目录 | 不得进入可交付 Skill 包 |
| architect 旧结构 | `warmup_task` / `subquestion_tasks` / `single_problem` 形态 | 默认“每题一个 `task-<qid>`，小问属于 task 内部”；**不保留 legacy schema** |
| 备用名兼容 | `task-<qid>.json` / `student-task-<qid>.json` | 唯一规范文件名 `teaching/generated/<qid>-student-task.json` |
| `problemcase` 保留入口名 | `gen_config.py` RESERVED_ENTRIES | 模板已删除 |

版本升级：契约 `2.3.0 → 2.4.0`（`contract.py` / `schemas/SCHEMA_VERSION` /
packet / student-task / architect / agent-envelope / output-manifest /
section-manifest 全部一致），旧报告不会被误认为新门禁报告（deliver 核对
报告 `schema_version`）。

## 二、可信执行边界（阶段 1）：`scripts/broker.py`

六条操作全部经 broker：`create_run` / `issue_packet` / `submit_task` /
`render_task` / `run_gates` / `promote`。

- **任务清单来自冻结 brief**：`create_run` 重算 brief_digest 与 freeze 核对，
  按 `selection.selected_problem_ids` 生成 expected tasks（每题一个
  `task-<qid>`）；用户确认摘要（user-spec.json）的 lesson_mode 绑定进 run。
- **窄包白名单**：允许输入由冻结 brief 的 L0 引用（statement_ref /
  probe_ref）计算；路径解析检查真实路径、符号链接与项目根越界；
  solution/proof/audit/review/教师备注关键词第二道防线；`input_digests`
  非空且由生成器实读重算；输出只能落 `staging/<run_id>/packets/`。
- **签名 receipt**：HMAC-SHA256（密钥取 `$SKILLS_BROKER_KEY`，否则工作区外
  `~/.skills-broker/broker.key`，首次自动生成），含 run/task/packet digest /
  worker_session_id / input_digests / verifier 版本。`execution_metadata`
  的 UUID 仅作索引。
- **提交验证**：schema + 输出字段白名单 + source_refs 白名单与实读摘要 +
  packet/receipt 绑定验签 + worker_session_id 一致；拒绝只进返修队列。
- **渲染**：只接受已验证 JSON；修复了 TeX 转义与数学公式处理（`$...$`
  原样透报、文本段才转义、`\ansspace{<n>mm}` 带单位、禁控制序列注入）；
  输出带机器标记与 input digest；只写 `staging/<run_id>/rendered/`。
- **门禁**：干净代码副本（scripts+schemas 快照 + code_digest）；**先构建后
  检查**（版面以实际 PDF/student.log 为准）；汇总签名；失败轮次回滚本次
  预置的规范文件。
- **提升**：验签汇总、必经检查齐全且全 pass、brief 冻结状态仍一致、逐任务
  核对输出/渲染摘要后原子提升；写 `staging/<run_id>/promotion.json`。

## 三、门禁收紧（阶段 2/4/5）

- `check_teaching.py`：expected 清单来自冻结 brief；唯一规范文件名；schema +
  question_id/task_id/brief_id/brief_revision/packet_digest/receipt_ref/run_id
  绑定核对；lesson_mode 必须同时绑定 user-spec 与冻结 brief（混合模式失败）；
  **不再有 guided 豁免**；缺题/额外题/备用名一律 fail。
- `check_student_task.py`（新统一门禁，必经）：硬失败 F1—F12（brief 缺失、
  覆盖、schema、字段白名单、来源 digest、渲染映射、禁止环境/答案宏、
  初始视图纯净、归一化黑话匹配、结构、字符预算 >1000、实际 PDF 版面）；
  review R1—R5（证明大纲式 prompt、同义改写、公共片段、平行解法、题面
  token 复现）；版面例外须登记 `teaching/layout-exceptions.json`。
  受保护策略 token 以 `brief.protected_strategy_tokens` 为第一道防线
  （schema 已扩展），内置黑名单仅第二道。
- `check_student_visibility.py`：材料缺失/不可读/越权可见全部 fail，
  不再“跳过即 PASS”；新增渲染产物机器标记核对。
- `deliver.py`：必经检查 = check_research / check_architect / check_teaching /
  check_student_visibility / check_numbering（并行）+ build + check_student_task
  （构建后串行）+ compare_versions + make_review_pack + 三类独立审查 + issue/
  review 处置；报告绑定契约版本与 code_digest；`--skip-build` 只写技术测试
  报告，不创建/更新 `release/manifest.json`，不复制正式 PDF。

## 四、对抗测试（阶段 6）：`tests/run_bypass_matrix.py`

```
python tests/run_bypass_matrix.py        # 14 个用例全部通过
```

| 对抗样例 | 结果 |
|---|---|
| 删除入选题 JSON / 增加额外题 / 三个备用名 | 失败且报告列出题目 |
| 合法 schema 但错误 question_id / 旧 revision | 失败 |
| 伪造 receipt 签名 / packet_digest | 失败（验签/绑定） |
| lesson_mode 改成其他值 | 失败（模式与 frozen brief 不一致） |
| 传入空检查列表、直接调用单个脚本、跳过 deliver | 不产生 release |
| solution 复制到普通文件名后进入输入集合 | packet/白名单/receipt 拒绝 |
| 提示写入受保护 token（同义/归一化） | 硬失败 |
| 渲染可见文本 >1000 字符 | 硬失败 |
| 篡改门禁汇总状态 / 删除门禁调用 | promote 拒绝（验签/缺失检查） |
| `--skip-build` 后已有 release/manifest.json | 不被创建/覆盖/更新 |

fixture 覆盖：合规题、缺题、非法 schema、答案泄露、版面超限（真实 XeLaTeX
构建 + pdftotext 页级抽取 + student.log 的 Overfull 解析）。

## 五、剩余边界声明（重要，不得由文档措辞掩盖）

以下风险**未**消除，部署时必须由外部边界控制：

1. **管理员级绕过**：若主 agent 与 broker/verifier 以同一 Windows 身份运行，
   它可以修改 `scripts/`、`schemas/`、`staging/` 乃至 `~/.skills-broker/
   broker.key`。本实现通过“干净代码副本 + code_digest + 汇总签名 + 原子
   提升 + 失败回滚”提高绕过成本，但**不能**物理阻断。生产部署必须以
   ACL/容器卷/CI workspace 落实：主 agent 只写 `staging/<run_id>/tasks/
   <task_id>/`；`scripts/`、`schemas/`、broker 密钥、`teaching/generated/`、
   `teaching/content/`、`teaching/materials.json`、检查报告与 `release/`
   对主 agent 只读或由外部 runner 独占。
2. **语义质量不可能只靠正则证明**：同义改写、拆分等式、图片/表格泄露由
   Linter 归入 review，最终由独立学生阅读审查判定；Linter 的责任是稳定
   拦截已知坏例与提高审查效率。
3. **版面结论以实际构建为准**：字符预算（600--900 目标 / >850 告警 /
   >1000 硬失败）只是代理指标；无 student.log 时版面记 review 不放行。
4. **子 agent 真实性**：R2 三件套仍以平台 brain 目录物理校验为准
   （`SKILLS_BRAIN_DIR` 可覆盖用于测试）；T2 链路的真实性由 broker 签名
   receipt 与 digest 绑定决定，UUID 只是索引。

**验收结论**：Skill 内部门禁已按审计加强为“严格、可追溯、默认失败”的门禁
链，14 项对抗测试通过；但在同一写权限下仍存在管理员级绕过风险——这是系统
边界的事实，落实上述 ACL/容器/CI 隔离前，不得宣称“主 agent 无法绕过”。
