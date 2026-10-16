# W5 修复夹具工程 · 端到端门禁联动验收报告

- 夹具工程：`repair_fixtures/proj/`（冻结 brief revision 1，brief_digest=`da96c44ab32124dc…`）
- 被测对象：`scripts/make_small_packet.py`（W1）、`schemas/student-task.schema.json`（W2）、`scripts/check_student_task.py`（W3）、`workflow/nodes/agent_nodes.py` + `prompts/`（W4）
- 写域纪律：全部产物仅落在 `repair_fixtures/`；对 scripts/schemas/prompts/workflow 只读，未做任何修改。

## 夹具形状

- P001：`solution.json` 含 4 条 verified 义务 OB1..OB4（OB2 claim 含受保护 token「威尔逊定理」→ 触发中性化回退；OB4 claim 为「综合以上各步，故原题结论成立」→ 首子句截断，结论文本不进入 signpost）；`target_subquestion_count = max(3, min(6, 4+1)) = 5`。
- P002：无 `solution_ref` → packet 无骨架（旧式回归路径）。
- `protected_strategy_tokens: ["威尔逊定理"]`。
- 5 个 run：run-fix-a/b/c/d/e；A/B/C/D/E 五个 P001 任务变体 + P002 旧式任务（无 obligation_refs），全部经 broker issue/submit/render 真实签发后跑门禁。

## 验收矩阵

| 项 | 验收点 | 结果 |
|----|--------|------|
| 2a | packet 携带 solution_skeleton 且 target_subquestion_count=5、4 条义务、末位 terminal=true | ✅ PASS |
| 2b | allowed_source_refs/input_digests 无任何 solution/proof 路径，骨架外 body 不含解法痕迹 | ✅ PASS |
| 2c | signpost 中性化：OB2 命中 protected token 回退；OB4 截断首子句、不含结论文本 | ✅ PASS |
| 2d | packet 通过 contract.validate(_, 'packet') | ✅ PASS |
| 2e | P002（无 solution_ref）packet 不携带 solution_skeleton | ✅ PASS |
| 3-prep | 夹具提交链：A/B/C/D/E + P002 旧式任务均通过 broker submit/render（骨架门禁只在 check 侧） | ✅ PASS |
| 3A | 夹具 A（5 问全覆盖、末问绑 OB4 且收束题干）：退出码 0，无 F10b/F13a/F13b fail | ✅ PASS |
| 3B | 夹具 B（3 问）：触发且仅触发 F10b fail（无 F13 串报） | ✅ PASS |
| 3C | 夹具 C（5 问并集缺 OB3）：触发且仅触发 F13a，消息列出 OB3 | ✅ PASS |
| 3D | 夹具 D（5 问全覆盖但末问未绑 OB4）：触发且仅触发 F13b | ✅ PASS |
| 3E | 夹具 E（末问 prompt 脱离题干）：退出码 0 无 fail，R6 terminal_convergence review 正向触发 | ✅ PASS |
| 4 | 无骨架回归：P002（无 solution_skeleton、无 obligation_refs）不产生任何 F10b/F13/R6 相关 fail/review（行为与修复前一致） | ✅ PASS |
| 4b | 回归守卫静态断言：skel 缺失→None→F10b/F13/R6 全组跳过 | ✅ PASS |
| 5 | T2 自审派发单 context 含 teacher_packet_ref 且 teacher-solution packet 真实落盘（runtime） | ✅ PASS |

## 逐项证据

### 2a packet 携带 solution_skeleton 且 target_subquestion_count=5、4 条义务、末位 terminal=true — PASS

- 命令/方式：`python scripts/make_small_packet.py --project proj --run-id run-fix-a --task-id task-P001 --kind student-task`
- 关键输出摘录：

```
rc=0; target=5; ids=['OB1', 'OB2', 'OB3', 'OB4']; terminals=[False, False, False, True]
```

### 2b allowed_source_refs/input_digests 无任何 solution/proof 路径，骨架外 body 不含解法痕迹 — PASS

- 命令/方式：`python scripts/make_small_packet.py --project proj --run-id run-fix-a --task-id task-P001 --kind student-task`
- 关键输出摘录：

```
allowed_source_refs=['research/problems/P001/statement.md', 'research/problems/P001/probe.json', 'research/brief.json']
```

### 2c signpost 中性化：OB2 命中 protected token 回退；OB4 截断首子句、不含结论文本 — PASS

- 命令/方式：`python scripts/make_small_packet.py --project proj --run-id run-fix-a --task-id task-P001 --kind student-task`
- 关键输出摘录：

```
signposts=['枚举小情形', '完成第 2 步关键论证（路标已中性化）', '比较两边的素因子幂次', '综合以上各步']
```

### 2d packet 通过 contract.validate(_, 'packet') — PASS

- 命令/方式：`python scripts/make_small_packet.py --project proj --run-id run-fix-a --task-id task-P001 --kind student-task`
- 关键输出摘录：

```
schema errors=无
```

### 2e P002（无 solution_ref）packet 不携带 solution_skeleton — PASS

- 命令/方式：`python scripts/make_small_packet.py --project proj --run-id run-fix-a --task-id task-P001 --kind student-task`
- 关键输出摘录：

```
keys=['allowed_source_refs', 'brief_id', 'brief_revision', 'forbidden_fields', 'input_digests', 'lesson_mode', 'output_fields_white_list', 'packet_digest', 'packet_kind', 'probe_evidence', 'problem_stem', 'question_id', 'run_id', 'schema_version', 'size_bytes', 'task_id']
```

### 3-prep 夹具提交链：A/B/C/D/E + P002 旧式任务均通过 broker submit/render（骨架门禁只在 check 侧） — PASS

- 命令/方式：`broker submit/render`
- 关键输出摘录：

```
A: submit_task rc=0 [broker] 提交已验证并接受：task-P001（output_digest=763e9d87244dd989） | render_task rc=0
B: submit_task rc=0 [broker] 提交已验证并接受：task-P001（output_digest=a92608ae00b82535） | render_task rc=0
C: submit_task rc=0 [broker] 提交已验证并接受：task-P001（output_digest=fd630c099dec3198） | render_task rc=0
D: submit_task rc=0 [broker] 提交已验证并接受：task-P001（output_digest=4346ec76b3a312fb） | render_task rc=0
E: submit_task rc=0 [broker] 提交已验证并接受：task-P001（output_digest=4aad72ecdd2ad9e5） | render_task rc=0
P2: submit_task rc=0 [broker] 提交已验证并接受：task-P002（output_digest=e9498680ef9cddf0） | render_task rc=0
```

### 3A 夹具 A（5 问全覆盖、末问绑 OB4 且收束题干）：退出码 0，无 F10b/F13a/F13b fail — PASS

- 命令/方式：`python scripts/check_student_task.py proj --json repair_fixtures/out/gate-A.json`
- 关键输出摘录：

```
rc=0 status=review fails=无 reviews=1 条（样例：【分类: layout_warn，hash: f229e943】版面：未找到 student.log，无法按实际构建结果核对 Overfull \hbox 与页数（字符预算不构成版…）
```

### 3B 夹具 B（3 问）：触发且仅触发 F10b fail（无 F13 串报） — PASS

- 命令/方式：`python scripts/check_student_task.py proj --json repair_fixtures/out/gate-B.json`
- 关键输出摘录：

```
rc=1; F10b=['teaching/generated/P001-student-task.json 小问数 3 与解法骨架目标问数 5 不一致（小问数量由 proof_obligations 推导，须精确相等）']; 串报=无; 全部 fails=['teaching/generated/P001-student-task.json 小问数 3 与解法骨架目标问数 5 不一致（小问数量由 proof_obligations 推导，须精确相等）']
```

### 3C 夹具 C（5 问并集缺 OB3）：触发且仅触发 F13a，消息列出 OB3 — PASS

- 命令/方式：`python scripts/check_student_task.py proj --json repair_fixtures/out/gate-C.json`
- 关键输出摘录：

```
rc=1; F13a=['teaching/generated/P001-student-task.json 小问 obligation_refs 并集未覆盖全部解法义务，缺失 ID: OB3（∪ obligation_refs 必须精确等于骨架义务全集）']; 串报=无; 全部 fails=['teaching/generated/P001-student-task.json 小问 obligation_refs 并集未覆盖全部解法义务，缺失 ID: OB3（∪ obligation_refs 必须精确等于骨架义务全集）']
```

### 3D 夹具 D（5 问全覆盖但末问未绑 OB4）：触发且仅触发 F13b — PASS

- 命令/方式：`python scripts/check_student_task.py proj --json repair_fixtures/out/gate-D.json`
- 关键输出摘录：

```
rc=1; F13b=['teaching/generated/P001-student-task.json 设问链未收束到终末证明义务：末位小问未绑定 terminal 义务 OB4']; 串报=无; 全部 fails=['teaching/generated/P001-student-task.json 设问链未收束到终末证明义务：末位小问未绑定 terminal 义务 OB4']
```

### 3E 夹具 E（末问 prompt 脱离题干）：退出码 0 无 fail，R6 terminal_convergence review 正向触发 — PASS

- 命令/方式：`python scripts/check_student_task.py proj --json repair_fixtures/out/gate-E.json`
- 关键输出摘录：

```
rc=0; fails=无; R6 review=['【分类: terminal_convergence，hash: e2150464】teaching/generated/P001-student-task.json 末位小问 prompt 与题面 stem 的归一化字符 bigram Jaccard 重叠 0.000 < 0.15：须人工确认末问是否收束回原题所问']
```

### 4 无骨架回归：P002（无 solution_skeleton、无 obligation_refs）不产生任何 F10b/F13/R6 相关 fail/review（行为与修复前一致） — PASS

- 命令/方式：`python scripts/check_student_task.py proj --json repair_fixtures/out/gate-A-regress.json`
- 关键输出摘录：

```
rc=0; P002 相关 fail=无; P002 骨架类 fail/review=无
```

### 4b 回归守卫静态断言：skel 缺失→None→F10b/F13/R6 全组跳过 — PASS

- 命令/方式：`静态读取 check_student_task.py:418-424`
- 关键输出摘录：

```
守卫代码存在：skel=(packet or {}).get("solution_skeleton")；非 dict 即置 None，后续全组分支以 skel/target 为 None 跳过
```

### 5 T2 自审派发单 context 含 teacher_packet_ref 且 teacher-solution packet 真实落盘（runtime） — PASS

- 命令/方式：`python 内调用 node_t2_review_request(minimal state)`
- 关键输出摘录：

```
routing_action=PROCEED; context.teacher_packet_ref=staging/run-fix-a/packets/task-P001-teacher-solution.json; 落盘=True; 教师 packet input_digests=['research/brief.json', 'research/problems/P001/probe.json', 'research/problems/P001/solution.json', 'research/problems/P001/statement.md']; 派发单文件=['dsp-t2-review-2dfb52a5b8.json']
```

## 结论

全部 14 项 PASS。W1–W4 修复在夹具工程上端到端联动正确；未发现缺陷。
