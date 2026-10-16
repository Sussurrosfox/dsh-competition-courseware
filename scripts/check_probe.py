#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_probe.py — 数值探针报告结构与证据检查

检查项：
  1. research/problems/<pid>/probe.json 符合 probe.schema.json；
  2. 变量定义域 domain 非空；
  3. 各探针 probe_id 唯一；
  4. exact_expression 存在且非空；
  5. numeric_value 为合法浮点数字符串且不是 NaN 或 Inf；
  6. comparison 与 equality_status 合法；
  7. student_warmup 字段完整，测试点非空；
  8. input_refs 文件存在性检查。
"""

import argparse
import json
import math
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "..", "shared")))
import contract  # noqa: E402


def check_probe_file(project_root: str, probe_path: str):
    root = os.path.abspath(project_root)
    abs_path = contract.resolve_path(root, probe_path)
    if not abs_path or not os.path.isfile(abs_path):
        return ["probe 文件不存在: %s" % probe_path], [], []

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except Exception as exc:
        return ["probe 文件不是合法 JSON: %s (%s)" % (probe_path, exc)], [], []

    fails, reviews = [], []

    # 1. Schema 校验
    errs = contract.validate(doc, "probe")
    if errs:
        for e in errs:
            fails.append("Schema 错误: %s [%s] %s" % (e["path"], e["validator"], e["message"]))
        return fails, reviews, []

    # 2. 域定义非空
    if not doc.get("domain", "").strip():
        fails.append("domain 字段为空")

    # 3. 探针 ID 唯一性与数值字段
    probes = doc.get("probes", [])
    if not probes:
        fails.append("probes 列表为空")

    seen_ids = set()
    for idx, p in enumerate(probes):
        pid = p.get("probe_id")
        if not pid:
            fails.append("probes[%d] 缺少 probe_id" % idx)
        elif pid in seen_ids:
            fails.append("重复的 probe_id: %s" % pid)
        seen_ids.add(pid)

        expr = p.get("exact_expression", "").strip()
        if not expr:
            fails.append("探针 %s 缺少 exact_expression" % pid)

        val_str = p.get("numeric_value", "").strip()
        try:
            val = float(val_str)
            if math.isnan(val) or math.isinf(val):
                fails.append("探针 %s 的 numeric_value 是 NaN 或 Inf" % pid)
        except ValueError:
            fails.append("探针 %s 的 numeric_value 不是合法浮点数: %r" % (pid, val_str))

    # 4. student_warmup 检查
    warmup = doc.get("student_warmup", {})
    if not warmup.get("question", "").strip():
        fails.append("student_warmup.question 为空")
    if not warmup.get("test_points"):
        fails.append("student_warmup.test_points 列表为空")

    # 5. input_refs 存在性
    for ref in doc.get("input_refs", []):
        ref_abs = contract.resolve_path(root, ref)
        if not ref_abs or not os.path.isfile(ref_abs):
            reviews.append("input_refs 文件不存在或未就绪: %s" % ref)

    return fails, reviews, []


def main():
    ap = argparse.ArgumentParser(description="核验探针报告 probe.json")
    ap.add_argument("project_root", help="项目根目录")
    ap.add_argument("probe_file", help="probe.json 项目相对路径")
    ap.add_argument("--json", "-j", help="报告输出路径")
    args = ap.parse_args()

    fails, reviews, fatal = check_probe_file(args.project_root, args.probe_file)
    if fatal:
        status = "error"
        findings = [{"severity": "error", "message": m} for m in fatal]
    elif fails:
        status = "fail"
        findings = [{"severity": "fail", "message": m} for m in fails]
    elif reviews:
        status = "review"
        findings = [{"severity": "review", "message": m} for m in reviews]
    else:
        status = "pass"
        findings = []

    out_path = args.json or os.path.join(args.project_root, "research", "checks", "check_probe.json")
    contract.write_check_report(out_path, status, "probe", findings)

    print("[check_probe] 检查完成: status=%s, %d fails, %d reviews -> %s" % (
        status, len(fails), len(reviews), out_path
    ))
    return 1 if fails else (2 if fatal else 0)


if __name__ == "__main__":
    sys.exit(main())
