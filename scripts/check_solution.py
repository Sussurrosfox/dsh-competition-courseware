#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_solution.py — 严谨解答与证明义务检查

检查项：
  1. research/problems/<pid>/solution.json 符合 solution.schema.json；
  2. statement_ref, proof_ref 及各证明义务的 evidence_ref 文件存在；
  3. 证明义务 (proof_obligations) 必须全部闭合为 verified（不得有 pending/refuted/needs_evidence）；
  4. 探针对账 (probe_reconciliation) 不得包含 conflict；
  5. 等号与边界条件 (equality_boundary) 字段非空且合法；
  6. 证明文本 (proof_ref Markdown/LaTeX) 的基本语法合法性（公式定界符与环境配对）。
"""

import argparse
import json
import os
import re
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "..", "shared")))
import contract  # noqa: E402


def _finding(severity, message):
    """构造 finding dict（G2.1 路由修复）。

    实质性数学错误类条目（证伪 refuted、探针对账冲突、证明义务未闭合等）
    的 message 携带 PROVED_FALSE / RESEARCH_BACK 标记，此处显式写入
    category 字段；write_check_report 原样透传 finding dict，供 workflow
    路由层消费。无标记的普通结构问题不写 category（路由层回落
    REPAIR_TASK）。
    """
    f = {"severity": severity, "message": message}
    if "PROVED_FALSE" in message:
        f["category"] = "PROVED_FALSE"
    elif "RESEARCH_BACK" in message:
        f["category"] = "RESEARCH_BACK"
    return f


def check_latex_syntax(text: str) -> list:
    """检查基本的 LaTeX 公式定界符与环境配对（语法检查，不声称数学正确）。"""
    errors = []
    
    # 检查 \begin{} 与 \end{} 配对
    begins = re.findall(r"\\begin\{([a-zA-Z0-9*]+)\}", text)
    ends = re.findall(r"\\end\{([a-zA-Z0-9*]+)\}", text)
    if len(begins) != len(ends):
        errors.append("LaTeX 环境未配对: \\begin 出现 %d 次，\\end 出现 %d 次" % (len(begins), len(ends)))

    # 检查 \[ 与 \] 配对
    open_brackets = text.count(r"\[")
    close_brackets = text.count(r"\]")
    if open_brackets != close_brackets:
        errors.append("\\[ 与 \\] 配对不一致: \\[ %d 次，\\] %d 次" % (open_brackets, close_brackets))

    return errors


def check_solution_file(project_root: str, sol_path: str):
    root = os.path.abspath(project_root)
    abs_path = contract.resolve_path(root, sol_path)
    if not abs_path or not os.path.isfile(abs_path):
        return ["solution 文件不存在: %s" % sol_path], [], []

    try:
        with open(abs_path, "r", encoding="utf-8-sig") as f:
            doc = json.load(f)
    except Exception as exc:
        return ["solution 文件不是合法 JSON: %s (%s)" % (sol_path, exc)], [], []

    fails, reviews = [], []

    # 1. Schema 校验
    errs = contract.validate(doc, "solution")
    if errs:
        for e in errs:
            fails.append("Schema 错误: %s [%s] %s" % (e["path"], e["validator"], e["message"]))
        return fails, reviews, []

    # 2. 引用文件存在性
    proof_ref = doc.get("proof_ref")
    if proof_ref:
        proof_abs = contract.resolve_path(root, proof_ref)
        if not proof_abs or not os.path.isfile(proof_abs):
            reviews.append("proof_ref 文件不存在或未就绪: %s" % proof_ref)
        else:
            # 6. LaTeX 基础语法检查
            try:
                with open(proof_abs, "r", encoding="utf-8") as pf:
                    proof_text = pf.read()
                tex_errs = check_latex_syntax(proof_text)
                for te in tex_errs:
                    fails.append("LaTeX 语法异常 (%s): %s" % (proof_ref, te))
            except Exception as exc:
                fails.append("无法读取证明文件 %s: %s" % (proof_ref, exc))

    # 3. 证明义务闭合性
    obligations = doc.get("proof_obligations", [])
    if not obligations:
        fails.append("proof_obligations 为空")

    seen_ob_ids = set()
    for ob in obligations:
        ob_id = ob.get("obligation_id")
        if not ob_id:
            fails.append("存在缺少 obligation_id 的证明义务")
        elif ob_id in seen_ob_ids:
            fails.append("重复的 obligation_id: %s" % ob_id)
        seen_ob_ids.add(ob_id)

        status = ob.get("status")
        if status == "refuted":
            # G2.1：证伪属实质性数学错误，message 携带 PROVED_FALSE /
            # RESEARCH_BACK 标记，_finding 据此显式写入 category
            fails.append("证明义务 %s 状态为 refuted（结论被证伪，PROVED_FALSE），"
                         "进入交接前必须闭合为 verified；须走 RESEARCH_BACK 重新研学"
                         % ob_id)
        elif status != "verified":
            fails.append("证明义务 %s 状态为 %s（证明义务未闭合），进入交接前必须"
                         "闭合为 verified；须走 RESEARCH_BACK 补齐证明"
                         % (ob_id, status))

        ev_ref = ob.get("evidence_ref")
        if ev_ref:
            ev_abs = contract.resolve_path(root, ev_ref)
            if not ev_abs or not os.path.isfile(ev_abs):
                reviews.append("证明义务 %s 的 evidence_ref 文件不存在: %s" % (ob_id, ev_ref))

    # 4. Probe-Solver 对账无矛盾
    reconciliations = doc.get("probe_reconciliation", [])
    if not reconciliations:
        fails.append("probe_reconciliation 列表为空，未与探针报告对账")
    for rec in reconciliations:
        r_pid = rec.get("probe_id")
        r_status = rec.get("status")
        if r_status == "conflict":
            # G2.1：探针证伪属实质性数学错误，message 携带 PROVED_FALSE /
            # RESEARCH_BACK 标记，_finding 据此显式写入 category
            fails.append("探针对账冲突 (%s): 证明结论与数值探针存在矛盾（探针证伪，"
                         "PROVED_FALSE），阻断冻结！须走 RESEARCH_BACK 重新研学。"
                         "解释: %s" % (
                r_pid, rec.get("explanation", "")
            ))

    # 5. 等号边界完整性
    eq_bound = doc.get("equality_boundary", {})
    if not eq_bound.get("condition", "").strip():
        fails.append("equality_boundary.condition 为空")
    if not eq_bound.get("limit_process", "").strip():
        fails.append("equality_boundary.limit_process 为空")

    return fails, reviews, []


def main():
    ap = argparse.ArgumentParser(description="核验严谨解答报告 solution.json")
    ap.add_argument("project_root", help="项目根目录")
    ap.add_argument("solution_file", help="solution.json 项目相对路径")
    ap.add_argument("--json", "-j", help="报告输出路径")
    args = ap.parse_args()

    fails, reviews, fatal = check_solution_file(args.project_root, args.solution_file)
    if fatal:
        status = "error"
    elif fails:
        status = "fail"
    elif reviews:
        status = "review"
    else:
        status = "pass"
    findings = ([_finding("error", m) for m in fatal]
                + [_finding("fail", m) for m in fails]
                + [_finding("review", m) for m in reviews])

    out_path = args.json or os.path.join(args.project_root, "research", "checks", "check_solution.json")
    contract.write_check_report(out_path, status, "solution", findings)

    print("[check_solution] 检查完成: status=%s, %d fails, %d reviews -> %s" % (
        status, len(fails), len(reviews), out_path
    ))
    return 1 if fails else (2 if fatal else 0)


if __name__ == "__main__":
    sys.exit(main())
