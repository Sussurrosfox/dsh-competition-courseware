# 严格破局与通法攻坚推导 (Rigorous Solution & Motivation)

> **题目编号**：`{{problem_id}}`  
> **题面引用**：`{{statement_ref}}`  
> **探针依据引用**：`{{probe_ref}}`  

---

## 1. 变量定义域与前置事实 (Domain & Pre-conditions)
- **变量范围**：{{domain}}
- **已知边界与探针约束**：{{probe_boundary_facts}}

---

## 2. 严谨形式化推导 (Step-by-Step Proof with Zero Skipped Steps)
> 要求：代数恒等变形零跳步，每步标注所引用的初等代数性质、不等式定理及成立条件。

```latex
\begin{proof}
{{rigorous_proof_steps_latex}}
\end{proof}
```

---

## 3. 等号与边界条件闭合论证 (Equality & Boundary Analysis)
1. **等号可达性性质**：`{{attainability}}`（`interior_attainable` / `boundary_asymptotic` / `unattainable`）
2. **取等条件 / 逼近路径**：
   - 条件方程：{{equality_condition}}
   - 极限证明过程：
   ```latex
   {{limit_proof_latex}}
   ```

---

## 4. 通法动机逆向解密 (Motivation Reverse-Engineering)
必须解答：“看似神来之笔的操作，究竟是如何想出来的？”

| 步骤 ID | 触发信号 (Trigger Signal) | 构造目的 (Construction Goal) | 核验机制 (Verification) | 适用边界 (Boundary Scope) |
| :--- | :--- | :--- | :--- | :--- |
| `step-1` | {{trigger_signal_1}} | {{construction_goal_1}} | {{verification_1}} | {{boundary_scope_1}} |

---

## 5. 探针对账表 (Probe Reconciliation)
核实本证明的全部理论推导与数值探针 `probe.json` 的客观测量数据无任何矛盾：

| 探针 ID | 测量事实 | 理论解释与对账状态 | 判定 (`consistent`/`explains`/`conflict`) |
| :--- | :--- | :--- | :--- |
| `probe-symmetry` | {{probe_symmetry_fact}} | {{probe_symmetry_explanation}} | `consistent` |
| `probe-boundary` | {{probe_boundary_fact}} | {{probe_boundary_explanation}} | `explains` |

---

## 6. 证明义务清单 (Proof Obligations Table)
所有列出的证明义务在交接前必须闭合为 `verified`：

| 义务 ID | 断言陈述 (Claim) | 证据来源文件 (Evidence Ref) | 状态 (`verified`/`needs_evidence`) |
| :--- | :--- | :--- | :--- |
| `obl-01` | {{obligation_01_statement}} | `{{obligation_01_evidence}}` | `verified` |
