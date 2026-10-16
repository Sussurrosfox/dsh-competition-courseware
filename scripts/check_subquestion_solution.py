#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_subquestion_solution.py — 针对各小问解答的零信任覆盖与质量门禁（契约 2.5.0）。

检查项：
  1. 覆盖率守恒：每个入选题必须存在规范的 solution-task.json；
  2. G1 同构守恒：solution-task 中的小问 ID 序列与 prompt 必须与对应 student-task.json 严格 1:1 一致；
  3. G2 逐问质量：
     - detailed_solution 字符数必须 >= MIN_SOLUTION_CHARS (40)；
     - 严禁命中边界感知敷衍退化禁词（如“同上”、“见前问结论”、“同理”、“代入即得”、“略”等）；
     - LaTeX 行内 ($) 与行间 (\\[ \\]) 公式定界符必须成对配平；
  4. G3 反集中检测：相邻小问解答文本相似度不得超过 0.90（防止将同一份整解复制到各小问）；
  5. Schema 合规：符合 solution-task.schema.json。

输出：teaching/checks/check_subquestion_solution.json（支持 --json）。
退出码：0=pass；1=fail；2=error。
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
sys.path.insert(0, _SCRIPT_DIR)
import contract  # noqa: E402

MIN_SOLUTION_CHARS = contract.MIN_SOLUTION_CHARS
FORBIDDEN_RULES = contract.FORBIDDEN_SOLUTION_RULES


def discover_expected_qids(project_root: str) -> list:
    """发现入选的题目 ID 列表。"""
    # 1. 尝试 teaching/materials.json
    mat_path = os.path.join(project_root, "teaching", "materials.json")
    if os.path.isfile(mat_path):
        try:
            with open(mat_path, "r", encoding="utf-8-sig") as f:
                mat = json.load(f)
            qids = []
            for b in mat.get("blocks", []):
                qid = b.get("question_id") or b.get("problem_id")
                if qid and str(qid) not in qids:
                    qids.append(str(qid))
            if qids:
                return qids
        except Exception:
            pass

    # 2. 尝试 research/freeze.json
    freeze_path = os.path.join(project_root, "research", "freeze.json")
    if os.path.isfile(freeze_path):
        try:
            with open(freeze_path, "r", encoding="utf-8-sig") as f:
                fz = json.load(f)
            cand = fz.get("selected_problems") or fz.get("selected_problem_ids") or []
            if cand:
                return [str(q) for q in cand]
        except Exception:
            pass

    # 3. 兜底扫描 teaching/generated/*-student-task.json
    gen_dir = os.path.join(project_root, "teaching", "generated")
    if os.path.isdir(gen_dir):
        qids = []
        for fn in os.listdir(gen_dir):
            m = re.match(r"^(\d+)-student-task\.json$", fn)
            if m:
                qids.append(m.group(1))
        if qids:
            return sorted(qids, key=lambda x: int(x) if x.isdigit() else str(x))

    return []


def check_subquestion_solutions(project_root: str, run_id: str = None):
    root = os.path.abspath(project_root)
    qids = discover_expected_qids(root)
    if not qids:
        return [], ["未在项目中发现任何入选题目的 materials/freeze 记录"], ["无法发现入选题目清单"]

    fails, reviews, fatal = [], [], []

    for qid in qids:
        contract.require_valid_qid(qid)
        task_id = f"task-{qid}"

        # 定位 student-task.json
        student_task_path = os.path.join(root, "teaching", "generated", f"{qid}-student-task.json")
        if not os.path.isfile(student_task_path) and run_id:
            staged = os.path.join(root, "staging", run_id, "tasks", task_id, "student-task.json")
            if os.path.isfile(staged):
                student_task_path = staged

        if not os.path.isfile(student_task_path):
            fails.append(f"题目 {qid}: 缺少对应的 student-task.json，无法进行设问同构对比")
            continue

        try:
            with open(student_task_path, "r", encoding="utf-8-sig") as f:
                student_task = json.load(f)
        except Exception as exc:
            fails.append(f"题目 {qid}: student-task.json 无法读取或 JSON 损坏: {exc}")
            continue

        # 定位 solution-task.json
        sol_task_path = os.path.join(root, "teaching", "generated", f"{qid}-solution-task.json")
        if not os.path.isfile(sol_task_path) and run_id:
            staged_sol = os.path.join(root, "staging", run_id, "tasks", task_id, "solution-task.json")
            if os.path.isfile(staged_sol):
                sol_task_path = staged_sol

        if not os.path.isfile(sol_task_path):
            fails.append(f"题目 {qid}: 缺失逐小问解答文件 ({os.path.relpath(sol_task_path, root)})")
            continue

        try:
            with open(sol_task_path, "r", encoding="utf-8-sig") as f:
                sol_task = json.load(f)
        except Exception as exc:
            fails.append(f"题目 {qid}: solution-task.json 无法读取或 JSON 损坏: {exc}")
            continue

        # 1. 运行 preflight_solution_task 综合校验
        verdict = contract.preflight_solution_task(
            sol_task,
            project_root=root,
            task_id=task_id,
            student_task=student_task
        )
        if not verdict.get("ok"):
            err_hints = verdict.get("hint", "")
            fails.append(f"题目 {qid} 预检失败:\n{err_hints}")

    return fails, reviews, fatal


def main():
    ap = argparse.ArgumentParser(description="竞赛学案逐小问解答质量统一门禁（契约 2.5.0）")
    ap.add_argument("project_root", help="项目根目录")
    ap.add_argument("--run-id", help="可选 run ID（用于核对 staging 产物）")
    ap.add_argument("--json", "-j", help="报告输出路径（默认 teaching/checks/check_subquestion_solution.json）")
    args = ap.parse_args()

    project_root = os.path.abspath(args.project_root)
    fails, reviews, fatal = check_subquestion_solutions(project_root, run_id=args.run_id)

    if fatal:
        status = "error"
    elif fails:
        status = "fail"
    elif reviews:
        status = "review"
    else:
        status = "pass"

    findings = []
    for m in fatal:
        findings.append({"severity": "error", "message": m, "category": "FATAL"})
    for m in fails:
        findings.append({"severity": "fail", "message": m})
    for m in reviews:
        findings.append({"severity": "review", "message": m})

    out_rel = args.json or "teaching/checks/check_subquestion_solution.json"
    out_abs = contract.resolve_path(project_root, out_rel) or os.path.join(project_root, out_rel)
    contract.write_check_report(out_abs, status, "小问解答", findings)

    print(f"[check_subquestion_solution] 检查完成: status={status}, {len(fails)} fails, {len(reviews)} reviews -> {out_rel}")
    if fails:
        for f in fails[:10]:
            print(f"  [fail] {f}")
        return 1
    if fatal:
        for ft in fatal:
            print(f"  [error] {ft}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
