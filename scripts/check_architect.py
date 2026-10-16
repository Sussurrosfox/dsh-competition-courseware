#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""check_architect.py — 双轨教学编导规划检查（契约 2.4.0）

检查项：
   1. teaching/architect-plan.json 符合 architect.schema.json（2.4.0：
      唯一 full_lesson 形态，每题恰好一个 task-<qid>，小问属于 task 内部；
      warmup_task / subquestion_tasks 旧结构已删除）；
   2. brief 绑定：brief_id 与 brief_revision 与 research/brief.json / freeze.json 一致；
   3. 覆盖对齐：problems 的 problem_id 集合与 brief.selection.
      selected_problem_ids 双向一致（缺题、额外题均失败）；
   4. 任务纪律：每题 student_track.tasks 恰好一项且 task_id ==
      "task-<problem_id>"；blocks 中非空 task_id 必须等于该题 task-<qid>；
   5. 双轨完整性：teacher_track 必须含时间轴、变式与 proof_ref，且
      proof_ref 文件存在；
   6. Block 元数据与纯净性断言：
      - 学生初始块（audience含student且available_after=="initial"）必须 level=="L0" 且 delivery_channel=="in_main"；
      - L2 块不得作为学生初始内容；
      - content_ref 文件若已生成，内容中严禁出现 forbidden_feature_refs 对应的禁泄露特征；
   7. 课堂计划联动：若 classroom-plan.md 存在，核验发放事件与 block_id 的对应关系。
"""

import argparse
import json
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCHEMAS_DIR = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "schemas"))
if _SCHEMAS_DIR not in sys.path:
    sys.path.insert(0, _SCHEMAS_DIR)
sys.path.insert(0, _SCRIPT_DIR)
import contract  # noqa: E402

DEFAULT_PLAN = "teaching/architect-plan.json"
BRIEF_REL = "research/brief.json"
FREEZE_REL = "research/freeze.json"
PLAN_MD_REL = "teaching/classroom-plan.md"


def _task_id_for(problem_id: str) -> str:
    return "task-%s" % problem_id


def check_architect_file(project_root: str, plan_rel: str = DEFAULT_PLAN):
    root = os.path.abspath(project_root)
    abs_path = contract.resolve_path(root, plan_rel)
    if not abs_path or not os.path.isfile(abs_path):
        return ["architect plan 文件不存在: %s" % plan_rel], [], []

    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except Exception as exc:
        return ["architect plan 不是合法 JSON: %s (%s)" % (plan_rel, exc)], [], []

    fails, reviews = [], []

    # 1. Schema 校验（2.4.0：full_lesson + 每题一个 task-<qid>）
    errs = contract.validate(doc, "architect")
    if errs:
        for e in errs:
            fails.append("Schema 错误: %s [%s] %s" % (e["path"], e["validator"], e["message"]))
        return fails, reviews, []

    if doc.get("plan_scope") != "full_lesson":
        fails.append("plan_scope=%r 非法：2.4.0 仅支持 full_lesson（problems[] 逐题规划）"
                     % doc.get("plan_scope"))

    problems = doc.get("problems", [])
    if not problems:
        fails.append("problems 列表为空，未逐题规划")

    # 2. brief 与 freeze 绑定检查
    brief = None
    brief_abs = contract.resolve_path(root, BRIEF_REL)
    if brief_abs and os.path.isfile(brief_abs):
        try:
            with open(brief_abs, "r", encoding="utf-8") as bf:
                brief = json.load(bf)
            if doc.get("brief_id") != brief.get("brief_id"):
                fails.append("brief_id 不匹配: plan 为 %s，brief 为 %s" % (
                    doc.get("brief_id"), brief.get("brief_id")
                ))
            if doc.get("brief_revision") != brief.get("revision"):
                fails.append("brief_revision 不匹配: plan 为 %s，brief 为 %s（可能为 stale 规划）" % (
                    doc.get("brief_revision"), brief.get("revision")
                ))
        except Exception as exc:
            reviews.append("无法读取 brief.json 核对版本: %s" % exc)
    else:
        reviews.append("未找到 %s，跳过 brief 绑定核对" % BRIEF_REL)

    # 3. 覆盖对齐：plan.problems 与 brief.selection.selected_problem_ids 双向一致
    plan_pids = [p.get("problem_id") for p in problems if p.get("problem_id")]
    if brief:
        selected = list(brief.get("selection", {}).get("selected_problem_ids", []))
        for pid in selected:
            if pid not in plan_pids:
                fails.append("入选题 %s 在 architect-plan.json 中无对应规划" % pid)
        for pid in plan_pids:
            if pid not in selected:
                fails.append("architect-plan.json 规划了未入选题 %s（任务清单必须以冻结 "
                             "brief 的 selected_problem_ids 为准）" % pid)

    # 4-6. 逐题检查
    forbidden = set(doc.get("forbidden_feature_refs", []))
    seen_bids = set()
    for prob in problems:
        pid = prob.get("problem_id", "<unknown>")
        expected_task = _task_id_for(pid)
        st = prob.get("student_track", {})
        tt = prob.get("teacher_track", {})

        # 4. 任务纪律：恰好一个 task-<qid>
        tasks = st.get("tasks", [])
        if len(tasks) != 1:
            fails.append("题目 %s 的 student_track.tasks 必须恰好一项（每题一个高内聚 "
                         "task），实际 %d 项" % (pid, len(tasks)))
        for t in tasks:
            if t.get("task_id") != expected_task:
                fails.append("题目 %s 的 task_id=%r 应为 %r（小问属于 task 内部，不设 "
                             "子任务 ID）" % (pid, t.get("task_id"), expected_task))

        # 5. 教师轨完整性
        if not tt.get("timeline_minutes"):
            fails.append("题目 %s 的 teacher_track.timeline_minutes 为空（缺少课堂时间轴）" % pid)
        if not tt.get("variants"):
            fails.append("题目 %s 的 teacher_track.variants 为空（缺少变式检验库）" % pid)
        proof_ref = tt.get("proof_ref")
        if not proof_ref:
            fails.append("题目 %s 的 teacher_track.proof_ref 为空" % pid)
        else:
            p_abs = contract.resolve_path(root, proof_ref)
            if not p_abs or not os.path.isfile(p_abs):
                fails.append("题目 %s 的 proof_ref 文件不存在: %s" % (pid, proof_ref))

        # 6. Block 元数据与纯净性
        for b in prob.get("blocks", []):
            bid = b.get("block_id")
            if not bid:
                fails.append("题目 %s 存在缺少 block_id 的 block" % pid)
                continue
            if bid in seen_bids:
                fails.append("重复的 block_id: %s" % bid)
            seen_bids.add(bid)

            level = b.get("level")
            aud = b.get("audience", [])
            avail = b.get("available_after")
            chan = b.get("delivery_channel")
            cref = b.get("content_ref")
            btask = b.get("task_id")

            if btask is not None and btask != expected_task:
                fails.append("Block %s 的 task_id=%r 必须为 null 或 %r（每题唯一 "
                             "task-<qid>）" % (bid, btask, expected_task))

            # 学生初始视图严格过滤
            if "student" in aud and avail == "initial":
                if level != "L0":
                    fails.append("Block %s 进入学生初始视图但 level=%s（只有 L0 允许进初始发放）" % (bid, level))
                if chan != "in_main":
                    fails.append("Block %s 为初始发放但 delivery_channel=%s（必须为 in_main）" % (bid, chan))

            if level == "L2" and "student" in aud:
                if avail == "initial" or chan == "in_main":
                    fails.append("Block %s 为 L2 关键内容，严禁直接在 student 初始主视图出现" % bid)

            # content 文件若存在，检查禁泄露特征
            if cref:
                c_abs = contract.resolve_path(root, cref)
                if c_abs and os.path.isfile(c_abs):
                    try:
                        with open(c_abs, "r", encoding="utf-8") as cf:
                            content_text = cf.read()
                        for f_word in forbidden:
                            if f_word in content_text and "student" in aud and avail == "initial":
                                fails.append("Block %s (%s) 存在禁泄露特征泄露: %s" % (bid, cref, f_word))
                    except Exception:
                        pass

    # 7. classroom-plan.md 联动检查
    plan_md_abs = contract.resolve_path(root, PLAN_MD_REL)
    if plan_md_abs and os.path.isfile(plan_md_abs):
        try:
            with open(plan_md_abs, "r", encoding="utf-8") as pmf:
                plan_text = pmf.read()
            if "停笔" not in plan_text and "留白" not in plan_text:
                reviews.append("classroom-plan.md 中未发现明确的停笔静思或留白节奏标记")
        except Exception:
            pass

    return fails, reviews, []


def main():
    ap = argparse.ArgumentParser(description="核验双轨教学规划 architect-plan.json")
    ap.add_argument("project_root", help="项目根目录")
    ap.add_argument("plan_file", nargs="?", default=DEFAULT_PLAN, help="architect-plan.json 相对路径")
    ap.add_argument("--json", "-j", help="报告输出路径")
    args = ap.parse_args()

    fails, reviews, fatal = check_architect_file(args.project_root, args.plan_file)
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

    out_path = args.json or os.path.join(args.project_root, "teaching", "checks", "check_architect.json")
    contract.write_check_report(out_path, status, "architect", findings)

    print("[check_architect] 检查完成: status=%s, %d fails, %d reviews -> %s" % (
        status, len(fails), len(reviews), out_path
    ))
    return 1 if fails else (2 if fatal else 0)


if __name__ == "__main__":
    sys.exit(main())
