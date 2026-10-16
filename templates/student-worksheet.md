# 课题：{{lesson_title}} —— 从特例探究到结构理解
姓名：___________ 班级：___________ 目标：高联二试 / 强基拔尖

<!-- TeX 对应：本文件为 Markdown 说明性参照，实际切片见 competition-case-template.tex -->

---

## 一、特例试算与观察 (Probe Warmup)
<!-- 对应 block: task-pXX-warmup  →  teaching/content/task-pXX-warmup.tex -->
面对结构对称的代数式，我们先通过代入特殊值验证直觉，再判断对称中心是否取极值。

### 【动手测算】
1. 请代入对称点 {{symmetry_point}}，计算原式的值：  
   左式 = ___________________ ≈ _________ (保留两位小数)。它等于目标值 {{target_bound}} 吗？
2. 请代入一组极端退化边界值：令 {{boundary_point}}，计算原式的值：  
   左式 ≈ ___________________.

### 【试算与观察】
通过上面的试算，你发现了什么现象？等号究竟在什么时候可能达到？
__________________________________________________________________________
__________________________________________________________________________

---

## 二、常见错解辨析 (Pitfall Challenge)【当前不启用】
<!-- 对应 block: task-pXX-pitfall  →  teaching/content/task-pXX-pitfall.tex -->
<!-- 注：契约 2.4.0 下学生窄包白名单仅含 statement_ref + probe_ref + brief，pitfall 内容不进学生包，本节暂无输入源、当前不启用；保留作历史参照。 -->
以下是某位选手的竞赛草稿，请找出其中的关键错误。

### [错解草稿]
```latex
\begin{proof}[错解草稿]
{{pseudo_proof_latex}}
\end{proof}
```

### 【诊断记录单】
1. 这份解答在第几步首次出现了方向性错误或逻辑破裂？为什么？  
   ____________________________________________________________________
2. 在草稿纸上算到哪一步特征时，你就应该立即警觉并放弃该思路？  
   ____________________________________________________________________

---

## 三、分步提示与思考（按需参考）(Scaffolding Hints)【当前不启用】
<!-- 对应 block: info-pXX-hint1 / info-pXX-hint2（separate_hint 渠道，不印在初始答卷正文）-->
<!-- 注：契约 2.4.0 下 student-task.json 已删除 hint 输出字段（学生任务仅含 subquestion_id / prompt / answer_space_mm），三级提示暂无输入源、当前不启用；学生学案中的引导已融入递进设问。本节保留作历史参照。 -->
> 提示：**尝试独立作答后再参考提示。** 从 Level 1 开始，逐步获取方向。

### Level 1（微光线索 · 代数特征）
<!-- info-pXX-hint1，available_after: initial，delivery_channel: in_main -->
- [按需揭示] {{hint_level_1}}

### Level 2（方向指引 · 目标形态）
<!-- info-pXX-hint2，available_after: hint，delivery_channel: separate_hint -->
- [按需揭示] {{hint_level_2}}

### Level 3（核心钥匙 · 算子操作）
<!-- info-pXX-hint3，available_after: hint，delivery_channel: separate_hint -->
- [按需揭示] {{hint_level_3}}

---

## 四、规范解答与总结 (Independent Rigorous Proof)
<!-- 对应 block: task-pXX-proof（含 \ansspace{12cm}）  →  teaching/content/task-pXX-proof.tex -->
请在下方空白处工整写出本题的严谨证明过程，并专门书写关于"等号成立条件"的论证：

[留白：15 行答题线（对应 \ansspace{12cm}）]

---

### 【反思与通法总结】
<!-- 对应 block: info-pXX-review（teacher,review-pXX 视图）-->
本题破局的思维钥匙是什么？该方法适用于何种代数特征？
__________________________________________________________________________
