#!/usr/bin/env python3
# -*- coding: utf-8 -*-
TOOL_VERSION = "1.0"
r"""
freeze_brief.py — 冻结教学交接包（R4 出口）

**定位**：本脚本核验冻结条件中**可机器核验**的部分，把 freeze 记录写入
research/freeze.json（符合 freeze.schema.json），旧 revision 归档到
research/revisions/，**不可静默覆盖**。人工条件（见下）由主 agent 在
冻结决策记录中逐项确认，本脚本只列出清单并核验决策记录文件存在。

机器核验项：

  1. research/brief.json 通过 brief.schema.json 校验；
  2. check_research 报告为 pass；若为 review，须提供非空的处置记录
     文件（--disposition-ref，review 级疑似项须逐项处置，不能用一份
     空泛文件概括放行）；
  3. 研究复核：freeze_basis.research_review_refs 中至少一条
     scope=research 且 conclusion=PASS 的审查记录，其 subject_digest
     与**当前** compute_brief_digest 计算的 brief_digest 一致
     （brief 或其引用文件改动后旧 PASS 自动失效）；且不存在
     subject_digest 与当前 brief_digest 一致但 conclusion 为
     REVISE / NEEDS_EVIDENCE 的审查记录；
  4. brief_digest 的引用文件无缺失（meet.md 冻结条件 5 的可核验部分）；
  5. 冻结决策记录（--decision-ref，默认 brief.freeze_basis.
     decision_log_ref）文件存在；
  6. revision 纪律：现有 freeze.json 的 revision >= brief.revision →
     拒绝覆盖；research/revisions/ 下已存在同 revision 归档 → 报错
     退出，不静默覆盖。

人工条件（meet.md 第四节冻结六条件中不可机器核验的部分），主 agent
必须在冻结决策记录中逐项确认：

  - 条件 1：入选题组和必要补充材料已经明确（复选完成）；
  - 条件 2：所用数学结论与证明没有未处理的实质缺陷（math-audit 与
    research-review 的 issues 均已处置）；
  - 条件 3：所有课程关键决策有主 agent 的明确记录；
  - 条件 5 的人工部分：来源状态诚实、信息发放约束语义上可执行
    （脚本只核文件存在与结构）；
  - 条件 6 的人工部分：研究复核确实覆盖全部入选题和跨题设计的
    内容（脚本只核 coverage ID 覆盖）。

产物：research/freeze.json；旧 freeze.json（较低 revision）与对应
brief.json 快照归档到 research/revisions/。

退出码：0 = 冻结成功；1 = 拒绝冻结（条件不满足）；2 = 输入错误。

用法：python freeze_brief.py <项目根> [--decision-ref PATH]
       [--disposition-ref PATH]
"""
import argparse
import datetime
import json
import os
import shutil
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "..",
                                                 "shared")))
sys.path.insert(0, _SCRIPT_DIR)
import contract  # noqa: E402
import check_research  # noqa: E402

FREEZE_REL = "research/freeze.json"
BRIEF_REL = "research/brief.json"
REVISIONS_DIR = "research/revisions"

HUMAN_CONDITIONS = [
    "条件 1：入选题组和必要补充材料已经明确（复选完成）",
    "条件 2：所用数学结论与证明没有未处理的实质缺陷（math-audit 与 "
    "research-review 的 issues 均已处置）",
    "条件 3：所有课程关键决策有主 agent 的明确记录",
    "条件 5（人工部分）：来源状态诚实、信息发放约束语义上可执行",
    "条件 6（人工部分）：研究复核确实覆盖全部入选题和跨题设计的内容",
]


def _load(root, rel):
    abs_path = contract.resolve_path(root, rel)
    if abs_path is None or not os.path.isfile(abs_path):
        return None, "文件不存在: %s" % rel
    try:
        with open(abs_path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, "不是合法 JSON: %s (%s)" % (rel, exc)


def main():
    ap = argparse.ArgumentParser(description="冻结教学交接包（不可静默覆盖）")
    ap.add_argument("project_root", help="项目根（含 research/ 的目录）")
    ap.add_argument("--decision-ref", default=None,
                    help="冻结决策记录（项目相对路径）；缺省用 brief."
                         "freeze_basis.decision_log_ref")
    ap.add_argument("--disposition-ref", default=None,
                    help="check_research 为 review 时的逐项处置记录"
                         "（项目相对路径，须存在且非空）")
    args = ap.parse_args()
    root = os.path.abspath(args.project_root)
    if not os.path.isdir(root):
        print("[freeze_brief] 项目根不存在: %s" % root)
        return 2
    if not os.path.isdir(os.path.join(root, "research")):
        print("[freeze_brief] 缺少 research/ 目录")
        return 2

    refuse = []

    # ---- 1. brief schema ----
    brief, err = _load(root, BRIEF_REL)
    if err:
        print("[freeze_brief] [错误] " + err)
        return 2
    berrs = contract.validate(brief, "brief")
    if berrs:
        for e in berrs:
            print("[freeze_brief] [拒绝] brief.json 未通过 schema 校验: "
                  "%s [%s] %s" % (e["path"], e["validator"], e["message"]))
        return 1

    # ---- 2. check_research ----
    fails, reviews, _evidence, fatal = check_research.check(root)
    if fatal:
        for m in fatal:
            print("[freeze_brief] [错误] check_research 输入错误: " + m)
        return 2
    if fails:
        for m in fails:
            refuse.append("check_research fail: " + m)
    elif reviews:
        disp = args.disposition_ref
        ok = False
        if disp:
            abs_disp = contract.resolve_path(root, disp)
            if abs_disp and os.path.isfile(abs_disp) \
                    and os.path.getsize(abs_disp) > 0:
                ok = True
        if not ok:
            refuse.append("check_research status=review（%d 条疑似项），"
                          "但未提供存在的非空处置记录（--disposition-ref）"
                          % len(reviews))
        else:
            print("[freeze_brief] check_research 为 review，已接受处置"
                  "记录: %s（%d 条疑似项）" % (disp, len(reviews)))
    # 落盘本次 check 报告（与独立运行 check_research.py 一致的位置）
    status = "fail" if fails else ("review" if reviews else "pass")
    findings = ([{"severity": "fail", "message": m} for m in fails]
                + [{"severity": "review", "message": m} for m in reviews])
    contract.write_check_report(
        os.path.join(root, check_research.DEFAULT_REPORT), status,
        "research", findings)

    # ---- 3/4. brief_digest 与研究复核 ----
    # 循环回避约定：research_review_refs 指向的审查记录本身**不参与**
    # brief_digest 的 inputs 文件摘要——否则审查记录的 subject_digest 要
    # 绑定一个包含自身文件内容的摘要，无不动点。审查记录对 brief 的绑定
    # 仍通过 core（brief 对象本身含 research_review_refs 列表）实现。
    # 独立复核（prompts/research-review.md）按同一约定计算 subject_digest。
    review_refs = brief["freeze_basis"]["research_review_refs"]
    digest_info = contract.compute_brief_digest(root, brief,
                                                exclude_refs=review_refs)
    brief_digest = digest_info["digest"]
    if digest_info["missing"]:
        for m in digest_info["missing"]:
            refuse.append("brief_digest 引用文件缺失: %s" % m)

    passing = []
    blocking = []
    for rref in review_refs:
        doc, rerr = _load(root, rref)
        if rerr:
            refuse.append("研究复核记录不可读: " + rerr)
            continue
        if contract.validate(doc, "review"):
            refuse.append("研究复核记录未通过 schema 校验: %s" % rref)
            continue
        if doc.get("scope") != "research":
            continue
        if doc.get("subject_digest") == brief_digest:
            if doc.get("conclusion") == "PASS":
                passing.append(rref)
            else:
                blocking.append((rref, doc.get("conclusion")))
    if blocking:
        for rref, concl in blocking:
            refuse.append("研究复核 %s 绑定当前 brief_digest 但结论为 %s，"
                          "须先处置" % (rref, concl))
    if not passing:
        refuse.append("没有 scope=research、conclusion=PASS 且 "
                      "subject_digest 与当前 brief_digest（%s…）一致的"
                      "研究复核记录；若 brief 或其引用文件在审查后改动过，"
                      "旧 PASS 已失效，须重新研究复核" % brief_digest[:12])

    # ---- 5. 冻结决策记录存在 ----
    decision_ref = args.decision_ref \
        or brief["freeze_basis"]["decision_log_ref"]
    abs_dec = contract.resolve_path(root, decision_ref)
    if abs_dec is None or not os.path.isfile(abs_dec):
        refuse.append("冻结决策记录不存在: %s（主 agent 须在决策记录中确认"
                      "人工条件）" % decision_ref)

    # ---- 6. revision 纪律与归档 ----
    revision = brief["revision"]
    old_freeze = None
    freeze_abs = contract.resolve_path(root, FREEZE_REL)
    if os.path.isfile(freeze_abs):
        old_freeze, ferr = _load(root, FREEZE_REL)
        if ferr:
            print("[freeze_brief] [错误] 现有 freeze.json 不可读: " + ferr)
            return 2
        if old_freeze.get("revision", 0) >= revision:
            refuse.append("现有 freeze.json 的 revision=%s 不低于 brief 的 "
                          "revision=%d；拒绝覆盖同 revision，修改须形成下"
                          "一 revision" % (old_freeze.get("revision"),
                                           revision))
    archive_freeze_rel = os.path.join(REVISIONS_DIR,
                                      "freeze-r%d.json" % revision)
    archive_brief_rel = os.path.join(REVISIONS_DIR,
                                     "brief-r%d.json" % revision)
    for arel in (archive_freeze_rel, archive_brief_rel):
        if os.path.isfile(contract.resolve_path(root, arel)):
            print("[freeze_brief] [错误] 归档 %s 已存在；不可静默覆盖，"
                  "退出" % arel)
            return 2
    if old_freeze is not None:
        old_rev = old_freeze.get("revision")
        for arel in (os.path.join(REVISIONS_DIR, "freeze-r%d.json" % old_rev),
                     os.path.join(REVISIONS_DIR, "brief-r%d.json" % old_rev)):
            if os.path.isfile(contract.resolve_path(root, arel)):
                print("[freeze_brief] [错误] 旧 revision 归档 %s 已存在；"
                      "不可静默覆盖，退出" % arel)
                return 2

    if refuse:
        print("[freeze_brief] 拒绝冻结（%d 项）:" % len(refuse))
        for m in refuse:
            print("  - " + m)
        return 1

    # ---- 写 freeze.json ----
    record = {
        "schema_version": contract.CONTRACT_VERSION,
        "brief_id": brief["brief_id"],
        "revision": revision,
        "brief_digest": brief_digest,
        "dependency_digests": digest_info["inputs"],
        "research_review_refs": list(review_refs),
        "decision_ref": contract.normalize_relpath(decision_ref),
        "frozen_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
    }
    rerrs = contract.validate(record, "freeze")
    if rerrs:
        for e in rerrs:
            print("[freeze_brief] [错误] freeze 记录未通过 schema 校验: "
                  "%s [%s] %s" % (e["path"], e["validator"], e["message"]))
        return 2

    rev_abs = contract.resolve_path(root, REVISIONS_DIR)
    os.makedirs(rev_abs, exist_ok=True)
    if old_freeze is not None:
        old_rev = old_freeze["revision"]
        shutil.move(freeze_abs, contract.resolve_path(
            root, os.path.join(REVISIONS_DIR, "freeze-r%d.json" % old_rev)))
        shutil.copy2(contract.resolve_path(root, BRIEF_REL),
                     contract.resolve_path(
                         root, os.path.join(REVISIONS_DIR,
                                            "brief-r%d.json" % old_rev)))
        print("[freeze_brief] 旧 revision %d 已归档到 %s/"
              % (old_rev, REVISIONS_DIR))
    with open(freeze_abs, "w", encoding="utf-8", newline="\n") as f:
        json.dump(record, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")

    print("[freeze_brief] 冻结成功：brief_id=%s revision=%d brief_digest="
          "%s" % (record["brief_id"], revision, brief_digest[:16] + "…"))
    print("[freeze_brief] 人工条件清单（主 agent 须已在 %s 中逐项确认）:"
          % decision_ref)
    for c in HUMAN_CONDITIONS:
        print("  - " + c)
    print("[freeze_brief] 提醒：主 agent 应将 research/run.json 的 "
          "current_state 更新为 BRIEF_FROZEN。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
