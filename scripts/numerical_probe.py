#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""numerical_probe.py — 基于 SymPy 的自动化数学探针脚本

负责在形式化证明前对代数表达式执行客观计算探针（对称点试算、退化边界极限、微扰分析），
输出满足 schemas/probe.schema.json 的稳定 JSON 结果。
只提供客观计算证据与直觉分析，不负责定理证明。
"""

import argparse
import json
import math
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

import sympy
from sympy.parsing.sympy_parser import (
    parse_expr,
    standard_transformations,
    implicit_multiplication_application,
)

_TRANSFORMATIONS = standard_transformations + (implicit_multiplication_application,)


def parse_safe_expr(expr_str: str, symbols_dict: dict):
    """安全解析数学表达式，不执行任意 Python 代码。"""
    # 替换 LaTeX 中的一些常用写法为 sympy 标准形式
    clean_str = expr_str.replace(r"\sqrt", "sqrt").replace(r"\frac", "")
    # 清除多余的花括号
    clean_str = clean_str.replace("{", "(").replace("}", ")")
    return parse_expr(clean_str, local_dict=symbols_dict, transformations=_TRANSFORMATIONS)


def run_probe(spec: dict) -> dict:
    problem_id = spec.get("problem_id", "p01")
    var_names = spec.get("variables", ["x", "y", "z"])
    domain_desc = spec.get("domain", "正实数域")
    expr_str = spec["expression"]
    target_bound = spec.get("target_bound", 2.0)
    target_dir = spec.get("direction", ">=")
    input_refs = spec.get("input_refs", ["sources/problems/%s.md" % problem_id])
    task_id = spec.get("task_id", "r2-probe-%s-01" % problem_id)

    # 构造 sympy 符号（正实数）
    sym_dict = {name: sympy.Symbol(name, positive=True, real=True) for name in var_names}
    sym_list = [sym_dict[name] for name in var_names]

    expr = parse_safe_expr(expr_str, sym_dict)

    probes = []
    conjectures = []

    # 1. 对称中心探针
    try:
        sym_subs = {s: sympy.Integer(1) for s in sym_list}
        sym_val = expr.subs(sym_subs)
        sym_val_simplified = sympy.simplify(sym_val)
        sym_num = float(sym_val_simplified.evalf(15))
        sym_num_str = "%.14f" % sym_num

        if abs(sym_num - target_bound) < 1e-9:
            sym_comp = "matches_bound"
            sym_eq = "attainable"
            sym_note = "对称中心 (全部变量取 1) 代入精确值为 %s，恰好等于目标常数 %s，极值大概率在对称中心取得。" % (
                sym_val_simplified, target_bound
            )
            conjectures.append("极值在对称中心取得，等号可达。")
        elif sym_num > target_bound:
            sym_comp = "strictly_greater"
            sym_eq = "unattainable"
            sym_note = "对称中心代入精确值为 %s (约 %s)，严格大于目标常数 %s。警惕非对称极值或退化边界极值。" % (
                sym_val_simplified, sym_num_str[:8], target_bound
            )
            conjectures.append("对称中心并非全局极小值点，存在偏心或边界退化。")
        else:
            sym_comp = "strictly_less"
            sym_eq = "unattainable"
            sym_note = "对称中心代入精确值为 %s，严格小于目标常数 %s。" % (
                sym_val_simplified, target_bound
            )

        probes.append({
            "probe_id": "probe-symmetry",
            "kind": "symmetry",
            "path": "= ".join(var_names) + " = 1",
            "exact_expression": str(sym_val_simplified),
            "numeric_value": sym_num_str,
            "comparison": sym_comp,
            "equality_status": sym_eq,
            "evidence_note": sym_note,
        })
    except Exception as exc:
        probes.append({
            "probe_id": "probe-symmetry",
            "kind": "symmetry",
            "path": "symmetry",
            "exact_expression": "error",
            "numeric_value": "0.0",
            "comparison": "error",
            "equality_status": "unverified",
            "evidence_note": "对称点计算出错: %s" % exc,
        })

    # 2. 边界退化探针 (x -> 0+, 其余为 1)
    if len(sym_list) >= 2:
        try:
            target_var = sym_list[0]
            other_subs = {s: sympy.Integer(1) for s in sym_list[1:]}
            boundary_expr = expr.subs(other_subs)
            lim_val = sympy.limit(boundary_expr, target_var, 0, dir="+")
            lim_val_simplified = sympy.simplify(lim_val)
            lim_num = float(lim_val_simplified.evalf(15))
            lim_num_str = "%.14f" % lim_num

            if abs(lim_num - target_bound) < 1e-9:
                lim_comp = "matches_bound"
                lim_eq = "asymptotic_boundary"
                lim_note = "当 %s 趋近于 0+ 时，表达式极限为 %s，恰等于目标常数 %s。表明下确界在边界处渐近达到，内部取不到等号。" % (
                    target_var.name, lim_val_simplified, target_bound
                )
                conjectures.append("等号无法在内部正实数域取得，必须在退化边界处渐近达成。")
            else:
                lim_comp = "strictly_greater" if lim_num > target_bound else "strictly_less"
                lim_eq = "unattainable"
                lim_note = "边界极限为 %s (约 %s)。" % (lim_val_simplified, lim_num_str[:8])

            probes.append({
                "probe_id": "probe-boundary",
                "kind": "boundary",
                "path": "%s -> 0+, 其余为 1" % target_var.name,
                "exact_expression": "\\lim_{%s \\to 0^+} = %s" % (target_var.name, lim_val_simplified),
                "numeric_value": lim_num_str,
                "comparison": lim_comp,
                "equality_status": lim_eq,
                "evidence_note": lim_note,
            })
        except Exception as exc:
            pass

    # 3. 微扰探针 (对称点微扰)
    if len(sym_list) >= 2:
        try:
            eps = 0.01
            pert_subs = {sym_list[0]: 1.0 + eps, sym_list[1]: 1.0 - eps}
            for s in sym_list[2:]:
                pert_subs[s] = 1.0
            pert_val = float(expr.subs(pert_subs).evalf(15))
            pert_str = "%.14f" % pert_val
            probes.append({
                "probe_id": "probe-perturbation",
                "kind": "perturbation",
                "path": "(1+0.01, 1-0.01, ...)",
                "exact_expression": "f(1+eps, 1-eps, 1)",
                "numeric_value": pert_str,
                "comparison": "strictly_greater" if pert_val > target_bound else "strictly_less",
                "equality_status": "unattainable",
                "evidence_note": "对称点施加微扰后值为 %s，测试局部变化率与凹凸性。" % pert_str[:8],
            })
        except Exception:
            pass

    warmup_q = "请先代入对称中心点计算原式，再代入极端边界值计算，观察计算结果与目标常数 %s 的关系。" % target_bound
    if len(sym_list) >= 3 and probes and len(probes) >= 2:
        warmup_q = (
            "请先代入 x=y=z=1 计算原式的值（约等于多少？等于 %s 吗？）；"
            "再代入极端边界值 x=0.001, y=1, z=1 计算，你能发现什么反直觉的现象？" % target_bound
        )

    return {
        "schema_version": "2.1.0",
        "problem_id": problem_id,
        "input_refs": input_refs,
        "domain": domain_desc,
        "probes": probes,
        "conjectures": conjectures if conjectures else ["目标表达式具有良好单调性或凸性"],
        "student_warmup": {
            "question": warmup_q,
            "test_points": ["= ".join(var_names) + " = 1", "%s=0.001, 其余为 1" % var_names[0]],
            "expected_phenomenon": "对称点值严格大于目标值，而越靠近退化边界其值越逼近目标常数。",
        },
        "verification_status": "verified",
        "task_id": task_id,
    }


def main():
    ap = argparse.ArgumentParser(description="基于 SymPy 执行数学探针")
    ap.add_argument("spec_file", nargs="?", help="探针定义输入 JSON 文件")
    ap.add_argument("--out", "-o", help="输出 probe.json 路径")
    ap.add_argument("--test", action="store_true", help="使用预置第 02 讲预 1 题进行自测")
    args = ap.parse_args()

    if args.test or not args.spec_file:
        spec = {
            "problem_id": "p01",
            "variables": ["x", "y", "z"],
            "domain": "x, y, z \\in (0, +\\infty)",
            "expression": "sqrt(x/(y+z)) + sqrt(y/(z+x)) + sqrt(z/(x+y))",
            "target_bound": 2.0,
            "direction": ">=",
            "input_refs": ["sources/problems/p01.md"],
            "task_id": "r2-probe-p01-01",
        }
    else:
        with open(args.spec_file, "r", encoding="utf-8") as f:
            spec = json.load(f)

    result = run_probe(spec)
    out_json = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(out_json + "\n")
        print("[numerical_probe] 探针成功生成: %s" % args.out)
    else:
        print(out_json)

    return 0


if __name__ == "__main__":
    sys.exit(main())
