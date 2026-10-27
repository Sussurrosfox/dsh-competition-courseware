# competition-courseware 强制子 Agent 机制改造 — 升级报告

对象：`competition-courseware` 插件（DSH 宿主 competition 教研流水线）
依据：`competition-courseware-subagent-enforcement-plan.md`（WP0–WP8）
修订：`competition-courseware-acceptance-report.md`（红队验收，2 项阻断缺陷 B1/B2 + 8 项非阻断问题）

---

## 一、改造目标（北极星）

流水线产出的**每一份教研产物**都必须可追溯到一次真实的子 Agent 派发：
`teaching/agent-requests/<dispatch_id>.json`（派发单）+ `teaching/agent-calls/<dispatch_id>.json`（HMAC 签名证据台账）。
主 Agent 单会话直写、伪造证据、篡改已认证产物，在**运行时（resume 侧）**与**交付门禁（磁盘侧）**双重被拦截。

三层防线：

| 层 | 位置 | 拦截手段 |
|---|---|---|
| L1 派发准入 | `index.js` → `courseware_dispatch` | `dispatch_token` HMAC 校验 + 产物白名单 + 并发/重试控制 |
| L2 运行时对账 | `workflow/nodes/agent_nodes.py` | `dispatch_protocol_violation`（返回协议不符即拒）+ `check_request_attested` 磁盘重推导 |
| L3 交付门禁 | `scripts/check_agent_audit.py`、`scripts/check_research.py` | 全量台账对账（签名、摘要、worker 身份）+ broker `run_gates`/`promote` 必经 |

---

## 二、WP0–WP8 交付清单

| WP | 交付物 | 状态 |
|---|---|---|
| WP0 | 基线快照 `docs/audit-baseline.md`（5 个文件 SHA256）+ git 分支 `backup-baseline` | ✅ |
| WP1 | `workflow/nodes/agent_evidence.py`：证据 schema、HMAC 签验、`check_request_attested`、协议块 | ✅ |
| WP2 | `index.js` → `courseware_dispatch` 工具 + `prompts/dispatch_prompt.md` + 协作者规则（systemPromptService.context 第 6/7 条） | ✅ |
| WP3 | `workflow/nodes/agent_nodes.py`：`dispatch_token` 注入、`write_dispatch_plan`、派发后对账、`_record_dispatch_evidence` | ✅ |
| WP4 | `scripts/check_agent_audit.py` 新门禁 + `scripts/deliver.py` 接线 + `courseware_gate` 枚举 `agent_audit` | ✅ |
| WP5 | `scripts/check_research.py` §1b 台账化（详见第三节 B1） | ✅（验收后补齐） |
| WP6 | `README.md` / `SKILL.md` / `AGENTS.md` / `USER_GUIDE.md` 文档同步 | ✅ |
| WP7 | `scripts/contract.py` `broker_key()` Windows DPAPI 加固（`broker.key.dpapi`，明文自动迁移） | ✅ |
| WP8 | `tests/test_redteam_subagent.py` 红队攻击用例集 | ✅ |

---

## 三、红队验收缺陷修复（B1 / B2 / NB1–NB8）

### 阻断缺陷

**B1 — `check_research.py` 台账化改造（WP5）**

- 旧逻辑：`execution_metadata.subagent_conversation_id` → UUID 格式校验 → `~/.gemini/antigravity-cli/brain/<uuid>/` 目录嗅探 → `transcript.jsonl` 存在性。DSH 宿主没有 `invoke_subagent` 与 Antigravity brain 目录 → 合法派发的 probe/pitfall/solution 永远无法通过 research 门禁。
- 新逻辑（`scripts/check_research.py`）：
  1. `_verify_artifact_evidence(root, pid, role, rel, doc)` 三重对账：
     `execution_metadata.dispatch_id` 存在 → `teaching/agent-calls/<dispatch_id>.json` 在案且 HMAC 验签通过、`worker` 为 `subagent:` / `llm-worker:` 前缀、`status == "ok"` → `output_digests[rel]` 与产物磁盘 sha256 逐字节相等。
  2. 三件套 `dispatch_id` 互异检测保留（复用同一 ID = 主进程代劳嫌疑）。
  3. legacy 旁路收紧：仅当运维**显式**设置 `SKILLS_BRAIN_DIR` 时，UUID 形态 ID 才走 Antigravity brain 嗅探；未设置时（DSH 默认形态）一律台账对账。
  4. 失败码：`[MISSING_EXECUTION_METADATA]` / `[EMPTY_SUBAGENT_CONVERSATION_ID]` / `[UNATTESTED_ARTIFACT]` / `[EVIDENCE_FORGED]` / `[EVIDENCE_ID_MISMATCH]` / `[EVIDENCE_STATUS_NOT_OK]` / `[EVIDENCE_WORKER_INVALID]` / `[EVIDENCE_ARTIFACT_NOT_RECORDED]` / `[EVIDENCE_STALE]`，文案统一指向 `courseware_dispatch`（删除 `invoke_subagent` 提法）。
  5. 报告 `evidence.agent_evidence` 输出每题每角的审计明细（dispatch_id / worker / provider / model / legacy）。
- `agent_evidence` 导入改为带文件路径回退的加载器（`_import_agent_evidence`）：broker `run_gates` 只复制 `scripts/`+`schemas/` 到干净代码副本，包内 import 会失效。

**B2 — `courseware_dispatch` 的 `project_root` 静默丢弃**

- 旧逻辑：`resolveCurrentProjectDir(ctx, args.project_root)` 的形参表只有 `ctx`，恒返回 `workspaceRegistry[0].path`；冒烟传 `D:\time\.audit-smoke` 时产物/台账写入无关项目。
- 新逻辑（`index.js`）：
  1. `looksLikeProjectRoot(dir)`：含 `teaching/` 或 `research/` 或 `config.yaml|yml` 即判定为项目根；
  2. `resolveCurrentProjectDir(ctx, explicitRoot, requestRef)` 优先级：
     ① 显式 `project_root` → `path.resolve` 后如实返回（**不静默回退**，传错时下游报错可见）；
     ② `request_ref` 为绝对路径 → 从 `dirname(request_ref)` 向上回推（`../..` → `..` → 自身）探测 `looksLikeProjectRoot`，否则取 `candidates[0]`；
     ③ `workspaceRegistry[0].path`；④ `process.cwd()`；
  3. 调用点 `index.js:961` 改为 `resolveCurrentProjectDir(ctx, args.project_root, args.request_ref)`；
  4. **同源修复**：`courseware_gate` 的 `proj = args.target_dir || workspaceRoot` 改为 `resolveCurrentProjectDir(ctx, args.target_dir)`（空 `target_dir` 时不再落到插件目录 `__dirname`）；工作台预加载同路径复用；
  5. **活体验证**（重载后 fiber）：`courseware_dispatch request_ref=D:\time\.audit-smoke2\teaching\agent-requests\dsp-b2-live.json` → 汇总行 `project_root: D:\time\.audit-smoke2`（按 request_ref 回推正确落点），白名单越界路径 `../../outside.txt` 被拒（`worker_error`），失败证据落 `D:\time\.audit-smoke2\teaching\agent-calls\dsp-b2-live.json`（`status=worker_error`、`attempts=3`、`provider/model=host-default`）；
  6. **顺带清污**：删除 `apply()` 与 `startControlServer()` 内共 4 处向插件目录写 `apply.log` 的调试语句（`EADDRINUSE` 静默降级、其余错误走 `console.warn`），重载后确认 `apply.log` 不再生成。

### 非阻断问题

| # | 问题 | 修复 |
|---|---|---|
| NB1 | Python `verify_dispatch_token` 用 `sorted(artifacts)`，与 `build_request` 的 `list(artifacts)` 口径不一致（死代码假阳性） | `agent_evidence.verify_dispatch_token` 改为 `[str(a) for a in (request.get("artifacts") or [])]`，与 JS 侧 `tokenPayload` 及 `build_request` 逐字节一致 |
| NB2 | `cdReadJson` 不剥 UTF-8 BOM | `index.js` `cdReadJson` 剥离 `0xFEFF` 首字符 |
| NB3 | broker `run_gates` 必经清单未含 `check_agent_audit` | `broker.py` `GATE_CHECKS` 置首位加入 `("check_agent_audit", "check_agent_audit.py", "teaching/checks/check_agent_audit.json", False)`；`cmd_promote` 的 `required_gates` 由 `GATE_CHECKS` 派生，自动获得该层纵深。**二次加固**：`cmd_run_gates` 干净副本复制范围由 `scripts/+schemas/` 扩为 `scripts/+schemas/+workflow/`（`for sub in ("scripts", "schemas", "workflow")`），并把 `code_dir/workflow` 并入 `contract.code_digest`——否则隔离副本内 `from workflow.nodes import agent_evidence` 必然 ImportError（台账对账门禁全灭） |
| NB4 | 台账 `provider`/`model` 空串 | `agent_evidence.load_dispatch_config(project_root)` 读 `config.yaml`（`agent_dispatch` 块 + 扁平键 `agent_max_concurrency`/`agent_max_retries`/`agent_model_map`）；`index.js` `readDispatchConfig`（python -c 回取 JSON，失败兜底默认值）+ `stageRoute(stage)`（stage 大写前缀优先、全称兜底）→ `callSubagent` 透传 `provider/model`，台账回填 `route.provider \|\| 'host-default'`；`maxRetries` 缺省改读配置 |
| NB5 | 门禁脚本在非项目目录创建 `research/checks`、`teaching/checks` 空壳 | `check_research.py` / `check_teaching.py` 的 `main()` 在 `fatal`（输入缺失）时只打印到 stdout、不写报告（exit 2），零目录副作用；**二次加固**：`check_teaching.check()` 入口加 `teaching/` 目录存在性 fatal 守卫；`check_agent_audit.main()` 对"既无 `teaching/` 又无 `research/`"的目标目录直接 exit 2 不写报告（此前裸目录会 PASS 并留下 `teaching/checks/` 空壳） |
| NB6 | `docs/upgrade-report.md` 缺失（DoD 第 4 条） | 本文件 |
| NB7 | 红队用例缺口（断点续跑覆盖攻击、research 阶段） | `tests/test_redteam_subagent.py` 测项 7 `test_redteam_attack_07_resume_evidence_transplant`（改名移植台账→`EVIDENCE_ID_MISMATCH`；改内部 ID 不重签→`EVIDENCE_FORGED`；resume 覆写沿用旧证据→`attested_mismatch`）+ `tests/test_check_research_evidence.py` 14 例（legacy 旁路 / worker 身份 / stale / 三件套互异）+ `tests/test_gate_guards.py` 6 例（裸目录零副作用 ×3、已认证项目 PASS、broker 干净副本 import、`workflow` 随副本断言） |
| NB8 | 改造完成后未做 fiber 重载验证 | 交付前执行 `dev_reload_package` 热重载并复跑门禁（见第五节） |

---

## 四、验证证据

| 项 | 命令 | 结果 |
|---|---|---|
| 全量单测 | `python -m pytest tests/ -q` | **51 passed**（`test_agent_evidence` 8 + `test_contract_dpapi` 3 + `test_agent_nodes_dispatch` 6 + `test_redteam_subagent` 7 + `test_check_research_evidence` 14 + `test_check_agent_audit` 7 + `test_gate_guards` 6） |
| 语法 | `python -m py_compile scripts/*.py workflow/nodes/*.py`；`node --check index.js`；`import('index.js')` | 通过 |
| 跨语言 HMAC | Python 签 token → JS 验；JS 签台账 → Python 验 | 双向互通（canonical JSON 口径等价） |
| 攻击面 | 伪造 token / 篡改产物 / 伪造台账 / 无台账 / 跨项目 project_root | 拒绝或 FAIL（`token_invalid` / `EVIDENCE_STALE` / `EVIDENCE_FORGED` / `EVIDENCE_MISSING`） |
| 空工作区 | `check_agent_audit` 对无产物无派发单目录 | PASS（不误伤）；裸目录（无 teaching/ 且无 research/）→ exit 2 零副作用 |
| 活体门禁 | 重载后 `courseware_gate gate=agent_audit target_dir=<已认证项目>` | PASS（1 通过）；改密钥重签 → `EVIDENCE_FORGED` FAIL；篡改产物 → `EVIDENCE_STALE` FAIL |
| 热重载 | `dev_reload_package` → `courseware_gate` 复跑 | 通过（见第五节） |

## 五、交付与运维注意

1. **broker 密钥**：首次运行自动生成 `~/.skills-broker/broker.key`（Windows 下同时写 `broker.key.dpapi` 并删除明文）；可用 `SKILLS_BROKER_KEY` 显式覆盖。
2. **逃生阀**：`config.yaml` 顶层 `allow_unattested: true` 或 `agent_dispatch.allow_unattested: true` 可把"产物有变但无证据"降级为 WARN（仅限受控调试，交付门禁默认 FAIL）。
3. **legacy 旁路**：仅在显式设置 `SKILLS_BRAIN_DIR` 时启用 Antigravity brain 嗅探；DSH 宿主不需要该变量。
4. **热重载**：插件文件变更后必须 `dev_reload_package` 重载 fiber 再调 `courseware_gate`/`courseware_dispatch`，否则命中旧代码（验收报告 NB8）。
