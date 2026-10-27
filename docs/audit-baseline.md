# competition-courseware 基线侦察报告 (WP0)

- 日期：2026-10-23
- 目标目录：`C:\Users\63254\.dsh\plugins\competition-courseware`
- 克隆蓝本：`C:\Users\63254\.dsh\plugins\dsh-qchain`

## 1. 测试基线

- 运行命令：`cd C:\Users\63254\.dsh\plugins\competition-courseware && python -m pytest tests/ -q`
- 结果：基线 `tests/` 目录为空/新建，基线测试用例数 = 0（`no tests ran in 0.00s`）。
- 改造完成后预期：WP1, WP4, WP5, WP8 等测试全部通过。

## 2. 核心文件基线 SHA-256

| 文件相对路径 | Baseline SHA-256 |
|---|---|
| `workflow/nodes/agent_nodes.py` | `CE69665898810DEF4E04DC39A87728A43D560DAF97160F0D3505B7158D1275EF` |
| `scripts/check_research.py` | `8A6446CD283D82F917920BFF4BF2A5DD579FC58394C3F3FC4258579F4D999E60` |
| `scripts/check_teaching.py` | `3BFF5A388A258B913596A155C523D9B0A7C669D7C60777E4980F307B4D1CD079` |
| `scripts/contract.py` | `6C8CBE8FE359067F0582727933069597BB52799A77BA6DB18A53E87E8FDF9701` |
| `index.js` | `F8FDEEF8E92ACB390A7E991195A92195BFE3FD33C055F2907F8F94476E77ED88` |

## 3. 克隆蓝本文件确认

- [x] `$QC/workflow/nodes/agent_evidence.py` 存在且可读
- [x] `$QC/scripts/check_agent_audit.py` 存在且可读
- [x] `$QC/prompts/subagent_prompt.md` 存在且可读
- [x] `$QC/index.js` 派发工具实现存在且可读

## 4. 备份保护

- Git 分支 `backup-baseline` 已建立。
