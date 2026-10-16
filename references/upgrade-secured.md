# skills-secured 零信任加固与物理防护落地记录（block_it.md 实施报告）

本文件记录 `skills-secured/` 相对 `skills-cleaned/` (v2.4.0) 的安全加固落地内容。

---

## 一、 加固背景与实施目标

在《`question_it_aud.md`》审计与 2.4.0 升级评估中，我们识别出当前架构在同一特权身份与逻辑层面上存在的 4 处潜在规避盲区：
1. **盲区 1（用户握手伪造）**：门禁仅检查 `user-spec.json` 是否存在，主 Agent 可无声伪造该文件跳过 Phase 0 物理交互；
2. **盲区 2（子 Agent 空目录欺诈）**：`check_research.py` 仅检查 Brain 目录下是否存在 UUID 目录，主 Agent 可通过 `os.makedirs` 伪造会话；
3. **盲区 3（语义逃逸与隐式结论）**：提示词中避开“所以/因此”而采用“显然/易知/恒等于0”等隐式连词，规避结论泄露拦截；概念拆分（如“p进制展开”）未被默认词表覆盖；
4. **盲区 4（Review 处置格式脱节）**：`check_student_task.py` 输出的 review 缺少结构化 `【分类: ...，hash: ...】` 标签，导致 `deliver.py` 处置核对脱节；
5. **系统特权隔离**：主 Agent 拥有对 `scripts/` 与 `release/` 的写权限，缺乏 OS 级 ACL 隔离约束。

`skills-secured/` 完整落实了《`block_it.md`》中**路线 B（平台日志物理穿透）**与**路线 A（存储隔离 Enclave 工具）**的全部设计。

---

## 二、 核心加固点落地清单

| 模块 | 原实现 (skills-cleaned) | 零信任加固实现 (skills-secured) |
| :--- | :--- | :--- |
| **子 Agent 真实性穿透** (`scripts/check_research.py`) | 仅通过 `_UUID_RE` 和 `os.path.isdir` 校验本地目录存在。 | 深入穿透会话目录，强制核验 `.system_generated/logs/transcript.jsonl` 文件存在、非空，且包含真实的 `"source":"MODEL"` 推理记录。阻断 `mkdir` 伪造空目录。 |
| **用户握手真实性核验** (`scripts/check_teaching.py`) | 仅读取磁盘上的 `user-spec.json` 且 `confirmed == true`。 | 引入 `_verify_user_spec_interaction`，反查当前会话的 `transcript.jsonl`，强制检索是否存在带有 `lesson_mode` 和 `main_title` 的原生 `ask_question` 工具调用证据。 |
| **语义与黑话正则库强化** (`scripts/check_student_task.py`) | 仅有 9 个定理名；结论连词仅有 10 余个显式词汇。 | 扩展 `DEFAULT_JARGON` 覆盖“p进制展开”、“按位展开”、“数位展开”、“进位展开”等概念拆分暗示；扩展 `CONCLUSION_RE` 覆盖“显然”、“易知”、“恒等于”、“恒为”、“数值为”、“即可得”、“恒等于0”等规避词；`OUTLINE_IMPERATIVE_RE` 增加“运用”、“借助”、“试证”。 |
| **Review 结构化闭环对齐** (`scripts/check_student_task.py` & `scripts/deliver.py`) | `reviews` 仅输出普通自然语言字符串，无格式标签。 | `check_student_task.py` 统一格式化为 `【分类: <category>，hash: <hash>】`；`deliver.py` 扩展 `HIGH_RISK` 识别 `outline_imperative`、`char_budget_warn`、`layout_warn`，实现 100% 结构化哈希追溯闭环。 |
| **工作区存储隔离控制** (`scripts/security/lock_workspace.py` & `.ps1`) | 无 OS 级权限控制工具。 | 新增专用控制工具，通过 Windows `icacls` 将 `scripts/`、`schemas/`、`teaching/generated/`、`release/` 设为主 Agent 只读，仅放开 `staging/` 写入权限。 |
| **回归测试套件升级** (`tests/run_bypass_matrix.py`) | 14 个对抗用例。 | 测试套件升级至 16+ 项，新增 `case_mkdir_brain_rejected`（空目录伪造拦截）与 `case_evaded_conclusion_rejected`（隐式连词结论拦截）。 |

---

## 三、 对抗矩阵实测验证

运行 `python tests/run_bypass_matrix.py`，全部对抗用例通过：
- 合规链路全部通过；
- 缺题、额外题、备用名、错误 ID、旧 revision 均稳定拒绝；
- 伪造 receipt 签名、篡改 lesson_mode、解题复制到输入集均稳定拒绝；
- 提示写入受保护 token、文本超 1000 字符、篡改汇总报告均稳定拒绝；
- **【新增】试图用 `os.makedirs` 创建空目录伪造子会话：被穿透校验拦截并明确拒绝；**
- **【新增】试图用“显然可得余数为 0”等隐式连词输出定理结论：被硬门禁拦截并硬失败。**
