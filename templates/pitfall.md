# 错解与死胡同解剖切片 (Pitfall Slice)

> **题目编号**：`{{problem_id}}`  
> **探针依据引用**：`{{probe_ref}}`  
> **禁手特征列表**：`{{forbidden_feature_refs}}`  
> **警示**：本文件专供错解辨析与构建找茬学案，**严禁向学生泄露正解特征**。

---

## 1. 题面陈述
{{problem_statement}}

---

## 2. 学生高频冲动尝试 (Attempt Analysis)

### 尝试 1：{{attempt_1_title}}
- **死胡同分类**：`{{category_1}}`（`direction_inversion` / `over_bounding` / `equality_conflict` / `structural_collapse`）
- **冲动心理机制**：{{student_plausibility_1}}
- **纸面停止信号**：{{observable_stop_signal_1}}

---

## 3. 完整伪证明草稿 (Pseudo-Proof for Student Audit)
> 【阅卷官挑战】：以下解答看似步骤完整、结论闭合，但暗藏逻辑或代数硬伤。请找出第一处致命错误并说明理由。

```latex
\begin{proof}[伪证明草稿]
{{pseudo_proof_latex}}
\end{proof}
```

---

## 4. 病理切片诊断 (Pathology Diagnosis)
1. **第一处致命错误定位**：
   - 所在位置：第 {{first_invalid_step}} 步。
   - 错误根源：{{pathology_reason}}
2. **草稿纸红线（停止信号）**：
   - 算到哪一步必须立即放弃并调转方向：{{stop_signal_detail}}
3. **禁手规则清单**：
   - [禁手 1] {{forbidden_rule_1}}
   - [禁手 2] {{forbidden_rule_2}}
