# -*- coding: utf-8 -*-
"""render_teacher_block.py - 题级教师块渲染器 (v2.3 Section Guided)。

确定性渲染器：把 teacher-solution（符合 schemas/solution.schema.json）
渲染成教师视图 TeX 片段。证明义务、取等闭合、动机还原等**不得因压缩版面删除**
（见 templates/teacher.tex 约定），本脚本只负责排版，不改内容、不做数学判断。

与 render_student_task.py 成对：学生块只含题面/小问/按需提示；教师块含完整
证明义务清单、取等边界、动机逆向还原、探针对账与逐小问结论。

Usage:
    python render_teacher_block.py --input <solution.json> --output <teacher.tex>
"""

import argparse
import json
import os
import sys


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def escape_tex(text):
    """转义 TeX 特殊字符（数学模式外的纯文本安全化）。"""
    if text is None:
        return ""
    text = str(text)
    text = text.replace("\\", "\\textbackslash{}")
    for ch in ("&", "%", "#", "_"):
        text = text.replace(ch, "\\" + ch)
    text = text.replace("$", "\\$")
    text = text.replace("{", "\\{")
    text = text.replace("}", "\\}")
    return text


def validate_input(sol):
    """渲染前最小结构检查；返回错误列表（不阻断渲染时也可仅警告）。"""
    errors = []
    for field in ("problem_id", "proof_obligations", "verification_status"):
        if field not in sol:
            errors.append({"code": "MISSING_FIELD", "field": field,
                           "message": "教师解缺少必填字段 %s" % field})
    if sol.get("verification_status") not in (None, "verified"):
        errors.append({
            "code": "NOT_VERIFIED",
            "message": "verification_status=%s 非 verified，教师块不应发布"
                       % sol.get("verification_status")})
    for ob in sol.get("proof_obligations", []):
        if ob.get("status") != "verified":
            errors.append({
                "code": "OBLIGATION_UNVERIFIED",
                "obligation_id": ob.get("obligation_id"),
                "message": "证明义务 %s 未 verified（不得压缩删除，需补证）"
                           % ob.get("obligation_id")})
    return errors


def _render_obligations(sol):
    lines = ["\\subsection*{证明义务清单}", "\\begin{itemize}"]
    for ob in sol.get("proof_obligations", []):
        badge = {"verified": "$\\checkmark$", "pending": "待证",
                 "needs_evidence": "缺证", "refuted": "被驳"}.get(ob.get("status"), "?")
        lines.append("\\item \\textbf{%s} [%s]：%s（证据：%s）" % (
            escape_tex(ob.get("obligation_id", "")), badge,
            escape_tex(ob.get("claim", "")), escape_tex(ob.get("evidence_ref", ""))))
    lines.append("\\end{itemize}")
    return lines


def _render_equality_boundary(sol):
    eb = sol.get("equality_boundary")
    if not eb:
        return []
    return [
        "\\subsection*{取等与边界闭合}",
        "\\begin{description}",
        "\\item[条件] %s" % escape_tex(eb.get("condition", "")),
        "\\item[达成性] %s" % escape_tex(eb.get("attainability", "")),
        "\\item[极限过程] %s" % escape_tex(eb.get("limit_process", "")),
        "\\end{description}",
    ]


def _render_motivation(sol):
    trace = sol.get("motivation_trace", [])
    if not trace:
        return []
    lines = ["\\subsection*{动机逆向还原（通法教学）}", "\\begin{enumerate}"]
    for step in trace:
        lines.append("\\item \\textbf{%s} 触发：%s；目的：%s；核验：%s；边界：%s" % (
            escape_tex(step.get("step_id", "")),
            escape_tex(step.get("trigger_signal", "")),
            escape_tex(step.get("construction_goal", "")),
            escape_tex(step.get("verification", "")),
            escape_tex(step.get("boundary_scope", ""))))
    lines.append("\\end{enumerate}")
    return lines


def _render_probe_recon(sol):
    recon = sol.get("probe_reconciliation", [])
    if not recon:
        return []
    lines = ["\\subsection*{探针对账}", "\\begin{itemize}"]
    for r in recon:
        mark = {"consistent": "一致", "explains": "已解释", "conflict": "矛盾!"}.get(
            r.get("status"), "?")
        lines.append("\\item %s（%s）：%s" % (
            escape_tex(r.get("probe_id", "")), mark,
            escape_tex(r.get("explanation", ""))))
    lines.append("\\end{itemize}")
    return lines


def _render_subq_results(sol):
    subs = sol.get("subquestion_results", [])
    if not subs:
        return []
    lines = ["\\subsection*{逐小问教师全解}", "\\begin{enumerate}"]
    for sq in subs:
        item_text = "\\item \\textbf{%s}\\par \\textit{核心方法}：%s\\par \\textit{结论}：%s" % (
            escape_tex(sq.get("subquestion_id", "")),
            escape_tex(sq.get("core_method", "")),
            escape_tex(sq.get("result", ""))
        )
        if sq.get("detailed_solution"):
            # 如果包含小问独立详细推导，作为 solution 或推导块完整呈现
            det_sol = sq["detailed_solution"].strip()
            item_text += "\\par\\medskip\n\\begin{solution}[第 (%s) 问详细推导]\n%s\n\\end{solution}" % (
                escape_tex(sq.get("subquestion_id", "")),
                det_sol
            )
        lines.append(item_text)
    lines.append("\\end{enumerate}")
    return lines


def render_teacher_block(sol):
    parts = []
    parts.append("% === TEACHER BLOCK START ===")
    parts.append("%% problem_id: %s" % sol.get("problem_id", ""))
    parts.append("%% proof_ref: %s" % sol.get("proof_ref", ""))
    parts.append("%% statement_ref: %s" % sol.get("statement_ref", ""))
    parts.append("% === TEACHER BLOCK MIDDLE ===")
    parts.append("")
    parts.append("\\section*{教师全解：%s}" % escape_tex(sol.get("problem_id", "")))
    parts.append("")
    parts.extend(_render_subq_results(sol))
    parts.append("")
    parts.extend(_render_obligations(sol))
    parts.append("")
    parts.extend(_render_equality_boundary(sol))
    parts.append("")
    parts.extend(_render_motivation(sol))
    parts.append("")
    parts.extend(_render_probe_recon(sol))
    parts.append("")
    parts.append("% === TEACHER BLOCK END ===")
    parts.append("%% verification_status: %s" % sol.get("verification_status", ""))
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description="渲染 teacher-solution 为教师 TeX 块")
    parser.add_argument("--input", "-i", required=True)
    parser.add_argument("--output", "-o", required=True)
    parser.add_argument("--strict", action="store_true",
                        help="存在未 verified 义务/状态时以非零码退出")
    args = parser.parse_args()

    try:
        sol = load_json(args.input)
    except Exception as exc:
        print("ERROR 读取输入失败：%s" % exc)
        return 1

    errors = validate_input(sol)
    if errors and args.strict:
        print("ERROR 教师解未通过严格校验（%d 项）：" % len(errors))
        for e in errors[:10]:
            print("  [%s] %s" % (e.get("code"), e.get("message")))
        return 1
    for e in errors:
        print("WARN [%s] %s" % (e.get("code"), e.get("message")))

    tex = render_teacher_block(sol)
    out_dir = os.path.dirname(os.path.abspath(args.output))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(tex)
        f.write("\n")

    print("[OK] 教师块已渲染：%s" % args.output)
    print("  - 题目：%s" % sol.get("problem_id"))
    print("  - 证明义务：%d 项" % len(sol.get("proof_obligations", [])))
    print("  - 逐小问全解：%d 项" % len(sol.get("subquestion_results", [])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
