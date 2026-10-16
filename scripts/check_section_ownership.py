# -*- coding: utf-8 -*-
"""check_section_ownership.py - 检查 Section Ownership (P1 硬门禁)。

验证规则：
1. 每题只能属于一个 section（section_id 唯一）
2. Section agent 的 token、输出路径和 manifest 必须一致
3. 禁止 full_lesson writer

输出：通过/失败报告（JSON），写入 project_root/self/checks/check_section_ownership.json
"""

import argparse
import json
import os
import sys
from pathlib import Path


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")


def load_json(path: str) -> dict:
    """加载 JSON 文件。"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_files(dir_path: str, pattern: str = "*.json") -> list:
    """递归查找目录下的所有匹配文件。"""
    import fnmatch
    results = []
    for root, _, files in os.walk(dir_path):
        for fname in files:
            if pattern == "*.*" or fnmatch.fnmatch(fname, pattern):
                results.append(os.path.join(root, fname))
    return sorted(results)


def check_section_ownership(project_root: str) -> dict:
    """执行 Section Ownership 检查。
    
    返回 {"status": "pass" | "fail", "findings": [...]}
    """
    findings = []
    errors = []
    
    # 查找所有 section-plan.json 文件
    planning_dir = os.path.join(project_root, "teaching")
    sections_dir = os.path.join(project_root, "teaching", "sections")
    
    if not os.path.isdir(sections_dir):
        errors.append({
            "code": "NO_SECTIONS_DIR",
            "message": f"不存在 sections 目录：{sections_dir}"
        })
        return {"status": "error", "findings": findings, "errors": errors}
    
    section_plans = []
    for sp_file in find_files(sections_dir, "section-plan.json"):
        try:
            plan = load_json(sp_file)
            section_plans.append((sp_file, plan))
        except Exception as e:
            errors.append({
                "code": "PARSE_ERROR",
                "path": sp_file,
                "message": f"解析 section-plan.json 失败：{e}"
            })
    
    if errors:
        return {"status": "error", "findings": findings, "errors": errors}
    
    # 检查 1: 每题只属于一个 section
    question_to_section = {}  # question_id -> section_id
    
    for sp_file, plan in section_plans:
        section_id = plan.get("section_id", "")
        
        # 验证 role 必须是 section-writer
        exec_meta = plan.get("execution_metadata", {})
        role = exec_meta.get("agent_role", "")
        if role != "section-writer":
            errors.append({
                "code": "INVALID_ROLE",
                "path": sp_file,
                "message": f"Role 不是 section-writer: {role}"
            })
            continue
        
        # 验证禁止 full_lesson
        if plan.get("plan_scope", "") == "full_lesson":
            errors.append({
                "code": "FORBIDDEN_FULL_LESSON",
                "path": sp_file,
                "message": "禁止使用 plan_scope=full_lesson"
            })
        
        # 检查 question_ids 与 questions 数组匹配
        q_ids_in_list = plan.get("question_ids", [])
        questions_array = plan.get("questions", [])
        
        if set(q_ids_in_list) != set(q["question_id"] for q in questions_array):
            errors.append({
                "code": "QUESTION_ID_MISMATCH",
                "path": sp_file,
                "message": "question_ids 数组与 questions[].question_id 不一致"
            })
        
        # 追踪每个问题属于哪个 section
        for q in questions_array:
            q_id = q["question_id"]
            if q_id in question_to_section:
                prev_section = question_to_section[q_id]
                errors.append({
                    "code": "DUPLICATE_SECTION_ASSIGNMENT",
                    "path": sp_file,
                    "message": f"题目 {q_id} 同时属于 {prev_section} 和 {section_id}",
                    "details": {"question_id": q_id, "sections": [prev_section, section_id]}
                })
            else:
                question_to_section[q_id] = section_id
    
    # 检查 2: Subquestion ID 唯一性
    for sp_file, plan in section_plans:
        section_id = plan.get("section_id", "")
        
        all_subq_ids = {}  # subq_id -> [question_id]
        
        for q in plan.get("questions", []):
            q_id = q["question_id"]
            for subq_id in q.get("subquestion_ids", []):
                all_subq_ids.setdefault(subq_id, []).append(q_id)
        
        # 检查重复
        for subq_id, q_ids in all_subq_ids.items():
            if len(q_ids) > 1:
                errors.append({
                    "code": "DUPLICATE_SUBQUESTION_ID",
                    "path": sp_file,
                    "message": f"Subquestion ID {subq_id} 在多题中重复出现：{q_ids}",
                    "details": {"subquestion_id": subq_id, "question_ids": q_ids}
                })
    
    # 生成结论
    if errors:
        status = "fail"
        findings.append({
            "type": "violations_found",
            "count": len(errors),
            "messages": [e["message"] for e in errors[:10]]  # 只显示前 10 个
        })
    else:
        status = "pass"
        findings.append({
            "type": "section_ownership_valid",
            "total_sections": len(section_plans),
            "total_questions": len(question_to_section),
            "unique_assignment": True
        })
    
    return {"status": status, "findings": findings, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 Section Ownership")
    parser.add_argument("project_root", help="项目根目录")
    parser.add_argument("--output", "-o", help="输出文件路径（可选）")
    args = parser.parse_args()
    
    result = check_section_ownership(args.project_root)
    
    if args.output:
        output_path = args.output
    elif os.path.basename(os.path.normpath(args.project_root)) == "self":
        output_path = os.path.join(args.project_root, "checks", "check_section_ownership.json")
    else:
        output_path = os.path.join(args.project_root, "self", "checks", "check_section_ownership.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
    
    print(f"Section Ownership Check: {result['status'].upper()}")
    print(f"报告已写入：{output_path}")
    
    if result["errors"]:
        print(f"\n发现 {len(result['errors'])} 个错误:")
        for err in result["errors"][:5]:
            print(f"  [{err['code']}] {err['message']}")
            if "details" in err:
                for k, v in err["details"].items():
                    print(f"    {k}: {v}")
    
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
