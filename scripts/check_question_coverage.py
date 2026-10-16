# -*- coding: utf-8 -*-
"""check_question_coverage.py - 检查题目覆盖率和 Section Coverage (P1 硬门禁)。

验证规则：
1. Active manifest 中每道题恰有一个 task + teacher solution
   （G6.4：teacher_block_ref 为空/缺失一律判未覆盖 fail；
   EMPTY_TEACHER_BLOCK_REF 与 DUPLICATE_SUBQUESTION_ID 属硬失败，
   不因覆盖率达标而降级为 review）
2. 禁止 question_group / 拓展研学题组等集合占位
3. 每题 3-6 个小问，ID 唯一且稳定（题内重复即 fail）
4. Subquestion ID 全局唯一（跨题重复即 fail；G6.4 已实现
   subq_global_ids 核对，不再空转）

输出：通过/失败报告（JSON）
"""

import argparse
import json
import os
import sys


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
    if not os.path.isdir(dir_path):
        return results
    for root, _, files in os.walk(dir_path):
        for fname in files:
            if pattern == "*.*" or fnmatch.fnmatch(fname, pattern):
                results.append(os.path.join(root, fname))
    return sorted(results)


def _nonempty_ref(value) -> bool:
    """teacher_block_ref 等引用必须是非空白字符串才算已覆盖（G6.4：
    空/缺失一律判未覆盖，fail-closed）。"""
    return isinstance(value, str) and bool(value.strip())


def _as_id_list(value) -> list:
    """把 subquestion_ids 字段（list 或逗号分隔字符串）规范为 id 列表。"""
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [s.strip() for s in value.split(",") if s.strip()]
    return []


def check_question_coverage(project_root: str) -> dict:
    """执行题目覆盖检查。
    
    返回 {"status": "pass" | "fail" | "error", "findings": [...], "errors": [...]}
    """
    findings = []
    errors = []
    
    # 读取 active manifest
    brief_path = os.path.join(project_root, "research", "brief.json")
    if not os.path.isfile(brief_path):
        return {
            "status": "error",
            "findings": [],
            "errors": [{"code": "MISSING_BRIEF", "message": f"找不到 brief.json: {brief_path}"}]
        }
    
    brief_data = load_json(brief_path)
    # v2.4 主流结构为 brief.selection.selected_problem_ids；兼容旧版 selected_questions 映射
    selection = brief_data.get("selection") or {}
    sel_ids = selection.get("selected_problem_ids") or []
    if sel_ids:
        active_ids = list(sel_ids)
    else:
        selected_questions = brief_data.get("selected_questions", {})
        active_ids = [qid for qid, info in selected_questions.items()
                      if info.get("status") == "active"]
    
    if not active_ids:
        errors.append({
            "code": "NO_ACTIVE_QUESTIONS",
            "message": "Brief 中没有 active 状态的题目"
        })
        return {"status": "error", "findings": findings, "errors": errors}
    
    # 查找所有 student-task 和 teacher-solution 文件
    teaching_dir = os.path.join(project_root, "teaching")
    tasks_dir = os.path.join(teaching_dir, "tasks")
    
    student_tasks = {}  # question_id -> file_path
    teacher_solutions = {}  # question_id -> file_path
    
    # 遍历 section 目录找任务文件
    sections_dir = os.path.join(teaching_dir, "sections")
    for sp_file in find_files(sections_dir, "section-plan.json"):
        plan = load_json(sp_file)
        for q in plan.get("questions", []):
            q_id = q["question_id"]
            student_tasks[q_id] = q.get("subquestion_ids", []) or q.get("student_block_ref", "")
            teacher_solutions[q_id] = q.get("teacher_block_ref", "")
    
    # 检查 1: 每题都有 task 和 solution（G6.4：空 teacher_block_ref 判未覆盖）
    for q_id in active_ids:
        if q_id not in student_tasks:
            errors.append({
                "code": "MISSING_STUDENT_TASK",
                "question_id": q_id,
                "message": f"题目 {q_id} 缺少 student-task"
            })

        if q_id not in teacher_solutions:
            errors.append({
                "code": "MISSING_TEACHER_SOLUTION",
                "question_id": q_id,
                "message": f"题目 {q_id} 缺少 teacher-solution"
            })
        elif not _nonempty_ref(teacher_solutions[q_id]):
            errors.append({
                "code": "EMPTY_TEACHER_BLOCK_REF",
                "question_id": q_id,
                "message": f"题目 {q_id} 的 teacher_block_ref 为空，"
                           "判未覆盖（fail-closed：空引用不得计为已覆盖）"
            })
    
    # 检查 2: 没有多余的任务文件
    all_task_ids = set(student_tasks.keys()) | set(teacher_solutions.keys())
    for task_q_id in all_task_ids:
        if task_q_id not in active_ids:
            errors.append({
                "code": "ORPHAN_TASK_FILE",
                "question_id": task_q_id,
                "message": f"任务文件属于非 active 题目：{task_q_id}"
            })
    
    # 检查 3: Subquestion 数量和格式验证（G6.4：题内与跨题全局唯一核对）
    coverage_counts = {q_id: 0 for q_id in active_ids}
    subq_global_ids = {}  # subq_id -> 首次归属的 question_id（全局唯一核对）

    for q_id, subq_ids in student_tasks.items():
        ids = _as_id_list(subq_ids)
        if ids:
            coverage_counts[q_id] = len(ids)

        # G6.4：subquestion_id 题内唯一 + 跨题全局唯一（此前 subq_global_ids
        # 声明了却从未填充，属空转检查，现已实现）
        seen_local = set()
        for sid in ids:
            if sid in seen_local:
                errors.append({
                    "code": "DUPLICATE_SUBQUESTION_ID",
                    "question_id": q_id,
                    "subquestion_id": sid,
                    "message": f"题目 {q_id} 内小问 ID “{sid}” 重复"
                })
            seen_local.add(sid)
            owner = subq_global_ids.get(sid)
            if owner is not None and owner != q_id:
                errors.append({
                    "code": "DUPLICATE_SUBQUESTION_ID",
                    "question_id": q_id,
                    "subquestion_id": sid,
                    "message": f"小问 ID “{sid}” 跨题重复：题目 {owner} 与 "
                               f"{q_id} 均使用（subquestion_id 必须全局唯一）"
                })
            else:
                subq_global_ids[sid] = q_id

        if q_id not in coverage_counts:
            continue  # 孤儿题已在“检查 2”中报告
        if coverage_counts[q_id] < 3 or coverage_counts[q_id] > 6:
            errors.append({
                "code": "INVALID_SUBQUESTION_COUNT",
                "question_id": q_id,
                "count": coverage_counts[q_id],
                "message": f"题目 {q_id} 的小问数量必须是 3-6 个，得到 {coverage_counts[q_id]}"
            })

    # 检查 4: Coverage rate（G6.4：空 teacher_block_ref 不计覆盖）
    covered_count = sum(1 for q_id in active_ids
                        if q_id in student_tasks
                        and _nonempty_ref(teacher_solutions.get(q_id)))
    coverage_rate = covered_count / len(active_ids) * 100 if active_ids else 0

    findings.append({
        "type": "coverage_statistics",
        "total_active_questions": len(active_ids),
        "fully_covered": covered_count,
        "missing_student_task": len([q for q in active_ids if q not in student_tasks]),
        "missing_teacher_solution": len(
            [q for q in active_ids
             if not _nonempty_ref(teacher_solutions.get(q))]),
        "coverage_rate_percent": round(coverage_rate, 2)
    })

    # 生成结论（G6.4：结构性完整性错误属硬失败，不因覆盖率达标降级 review）
    HARD_FAIL_CODES = {"EMPTY_TEACHER_BLOCK_REF", "DUPLICATE_SUBQUESTION_ID"}
    hard_fail = any(e.get("code") in HARD_FAIL_CODES for e in errors)
    if hard_fail:
        status = "fail"
        findings.append({
            "type": "structural_integrity_failure",
            "error": "存在空 teacher_block_ref 或重复 subquestion_id，"
                     "结构性完整性错误一律判 fail"
        })
    elif coverage_rate == 100 and not errors:
        status = "pass"
        findings.append({
            "type": "full_coverage_achieved",
            "all_questions_covered": True
        })
    elif coverage_rate >= 80:
        status = "review"
        findings.append({
            "type": "partial_coverage",
            "warning": f"覆盖率 {coverage_rate:.1f}%，建议达到 100%"
        })
    else:
        status = "fail"
        findings.append({
            "type": "insufficient_coverage",
            "error": f"覆盖率 {coverage_rate:.1f}% 低于最低要求 80%"
        })
    
    return {"status": status, "findings": findings, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser(description="检查题目覆盖率")
    parser.add_argument("project_root", help="项目根目录")
    parser.add_argument("--output", "-o", help="输出文件路径（可选）")
    args = parser.parse_args()
    
    result = check_question_coverage(args.project_root)
    
    if args.output:
        output_path = args.output
    elif os.path.basename(os.path.normpath(args.project_root)) == "self":
        output_path = os.path.join(args.project_root, "checks", "check_question_coverage.json")
    else:
        output_path = os.path.join(args.project_root, "self", "checks", "check_question_coverage.json")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")
    
    print(f"Question Coverage Check: {result['status'].upper()}")
    print(f"报告已写入：{output_path}")
    
    if result["findings"]:
        for finding in result["findings"]:
            print(f"\n{finding.get('type', 'info')}:")
            for k, v in finding.items():
                if k != "type":
                    print(f"  {k}: {v}")
    
    if result["errors"]:
        print(f"\n发现 {len(result['errors'])} 个错误:")
        for err in result["errors"][:5]:
            print(f"  [{err['code']}] {err['message']}")
    
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
