#!/usr/bin/env python3
# -*- coding: utf-8 -*-
TOOL_VERSION = "1.0"
r"""
check_research.py — 教研结构检查（R2/R3/R4 产物）

**定位**：本脚本只做**结构与证据引用**检查，不接管 math-audit 的数学
判断（脚本不宣称自动证明竞赛题），也不代替独立研究复核（review 的
PASS/REVISE/NEEDS_EVIDENCE 由审查者凭材料证据给出）。

检查项（对应 meet.md 第四节冻结条件中可机器核验的部分）：

  1. research/run.json、research/selection.json、research/brief.json
     及每个入选题的 research/problems/<pid>/dossier.json 通过对应
     schema 校验（错误 JSON / 缺字段 / 未知字段 / 非法枚举 → fail，
     不猜字段继续）；
  2. 引用解析：brief 与各 dossier 中的 *_ref / *_refs / path 引用文件
     必须存在；problem_id / claim_id / obligation_id / obstacle_id /
     supplement_id / task_id / info_id 不得重复；
     brief.selection.selected_problem_ids 与 selection.json 的
     selected_ids 必须一致（selected_ids 只由主 agent 写入）；
  3. brief 采用的 claims 的 verification 状态（used_by 指向入选题）：
     - refuted / pending → fail；
     - teaching_hypothesis 允许未核验，但必须如实标注（kind 为
       teaching_hypothesis 且未 verified → review 提示保持标注）；
     - classroom_observation 必须有真实试教来源：evidence_refs 非空
       且至少一条引用路径含 pilot/trial（真实试教记录目录约定），
       否则 → fail（防虚构课堂事实）；
  3.9. R3 孤儿产物门禁（enable_obstacle_study 默认关闭）：LangGraph 图中
      没有 R3 节点，且 student.pdf / student_solution.pdf 交付链路均不
      消费 dossier 的 obstacles/supplements（学生窄包白名单只有
      statement_ref/probe_ref/brief）；开关未开启时对任一非空
      dossier.obstacles / dossier.supplements / brief.supplements
      fail-closed（[ORPHAN_R3_DISABLED]）；开启时本项不新增 fail，
      沿用第 4 条原有逻辑；
  4. brief 采用的补充材料：verification 为 pending/refuted → fail；
     须在对应 dossier 的 supplements 中登记且 review_refs 非空
     （新增数学内容进入正式交接前需独立复核）；
  5. 采用的证明义务（math_baselines 的 proof_obligation_refs）须在
     dossier 中存在且 status=verified（进入交接包的数学内容必须
     闭合）；
  6. 任务时长：brief 中已安排任务 time_minutes 总和 ≤ 课程预算
     total_time_minutes；缺预算或缺时长 → review 提示，不擅自通过；
  7. 研究报告覆盖：freeze_basis.research_review_refs 引用的
     scope=research 审查记录的 coverage 并集，必须覆盖全部入选题
     problem_id 与 brief_id（跨题设计约定以 brief_id 列入 coverage
     表示）；审查结论有效性由 freeze_brief.py 核验；
  8. open_items 中 blocks_handoff=true → review 提示（机器无法判定
     "相应部分"范围，须人工/审查逐项处置）。

输出：write_check_report 格式（status ∈ pass/fail/review/error），
默认落盘 research/checks/check_research.json，--json 可覆盖。

退出码：0 = pass 或 review（review 退出码 0 但报告标明）；1 = fail；
2 = error。

用法：python check_research.py <项目根> [--json PATH]
"""
import argparse
import json
import os
import re
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

# contract.py 与本文件同目录（scripts/），import 实际由脚本目录
# sys.path[0] 解析。下方 sys.path.insert 指向的 ../../shared 是历史仓库
# 布局残留（本仓库无该目录，插入为空操作），仅保留行为不变，未删除。
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "..",
                                                 "shared")))
import contract  # noqa: E402

RESEARCH_DIR = "research"
DEFAULT_REPORT = os.path.join(RESEARCH_DIR, "checks", "check_research.json")
# 真实试教来源的路径约定（classroom_observation 的证据必须来自此类路径）
PILOT_TOKENS = ("pilot", "trial")

# ---- 子 Agent 物理验证 ----
# RFC 4122 UUID 格式（全小写十六进制，8-4-4-4-12）
_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
)
# Antigravity 子会话本地存储根目录（每次真实 invoke_subagent 均在此创建子目录）
# 可用环境变量 SKILLS_BRAIN_DIR 覆盖根目录（测试/CI 隔离；默认仍为平台目录）。
_AGY_BRAIN_DIR = os.environ.get(
    "SKILLS_BRAIN_DIR",
    os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-cli",
                 "brain"))


def _verify_subagent_id(conv_id):
    """验证 subagent_conversation_id 的格式合法性、本地物理存在性与真实执行日志。

    返回 (ok: bool, reason: str)：
      - ok=True,  reason="ok"                          → 验证通过
      - ok=False, reason="non_uuid_format"              → 格式不是合法 UUID
      - ok=False, reason="no_local_session_dir"         → 本地不存在对应子会话目录
      - ok=False, reason="empty_session_or_no_log"      → 子会话目录为空或缺少真实 transcript 日志（mkdir 伪造）
    """
    if not _UUID_RE.match(str(conv_id).lower()):
        return False, "non_uuid_format"
    session_path = os.path.join(_AGY_BRAIN_DIR, conv_id)
    if not os.path.isdir(session_path):
        return False, "no_local_session_dir"

    # 【零信任架构加固】：穿透校验子会话内真实的 transcript.jsonl 日志
    log_candidates = [
        os.path.join(session_path, ".system_generated", "logs", "transcript.jsonl"),
        os.path.join(session_path, "transcript.jsonl"),
    ]
    found_log = None
    for lp in log_candidates:
        if os.path.isfile(lp) and os.path.getsize(lp) > 0:
            found_log = lp
            break
    if not found_log:
        return False, "empty_session_or_no_log"

    try:
        found_model = False
        with open(found_log, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if ('"source":"MODEL"' in line or '"source": "MODEL"' in line
                        or '"MODEL"' in line or '"PLANNER_RESPONSE"' in line
                        or '"subagent"' in line.lower()):
                    found_model = True
                    break
                if i >= 100:
                    break
        if not found_model:
            return False, "empty_session_or_no_log"
    except OSError:
        return False, "empty_session_or_no_log"

    return True, "ok"


def load_json(root, rel):
    """读取 JSON 文件；返回 (doc, error)。文件缺失或 JSON 错误为输入错误。"""
    abs_path = contract.resolve_path(root, rel)
    if abs_path is None or not os.path.isfile(abs_path):
        return None, "文件不存在: %s" % rel
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            return json.load(f), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, "不是合法 JSON: %s (%s)" % (rel, exc)


def schema_errors(doc, schema_name, rel):
    return ["%s: %s [%s] %s" % (rel, e["path"], e["validator"], e["message"])
            for e in contract.validate(doc, schema_name)]


def check(root):
    """返回 (fails, reviews, evidence, fatal_errors)。

    fatal_errors 非空表示输入缺失/不可读（status=error，退出码 2）。
    """
    fails, reviews, fatal = [], [], []
    evidence = {"selected_problem_ids": [], "reviews_checked": [],
                "claims_checked": 0, "supplements_checked": 0,
                "task_time_total": None}

    rdir = os.path.join(root, RESEARCH_DIR)
    if not os.path.isdir(rdir):
        fatal.append("缺少 research/ 目录: %s" % rdir)
        return fails, reviews, evidence, fatal

    # ---- 1. schema 校验 ----
    docs = {}
    for rel, schema in (("research/run.json", "run"),
                        ("research/selection.json", "selection"),
                        ("research/brief.json", "brief")):
        doc, err = load_json(root, rel)
        if err:
            fatal.append(err)
            continue
        docs[schema] = doc
        fails.extend(schema_errors(doc, schema, rel))
    if fatal:
        return fails, reviews, evidence, fatal
    brief = docs["brief"]
    selection = docs["selection"]

    # 若 brief/selection schema 校验已失败，继续深度检查会猜字段，直接返回。
    if fails:
        return fails, reviews, evidence, fatal

    selected = brief["selection"]["selected_problem_ids"]
    brief_id = brief["brief_id"]
    evidence["selected_problem_ids"] = list(selected)

    dup = contract.check_stable_ids(
        [{"id": p} for p in selected], "id")
    for d in dup:
        fails.append("brief.selection.selected_problem_ids 中 problem_id "
                     "重复: %s" % d)

    sel_ids = selection.get("selected_ids", [])
    if list(sel_ids) != list(selected):
        fails.append("brief.selection.selected_problem_ids 与 "
                     "selection.json 的 selected_ids 不一致（%r != %r）；"
                     "selected_ids 只由主 agent 写入，两处必须同步"
                     % (selected, sel_ids))
    cand = selection.get("candidates", [])
    for d in contract.check_stable_ids(cand, "problem_id"):
        fails.append("selection.json candidates 中 problem_id 重复: %s" % d)
    known_pids = {c.get("problem_id") for c in cand}
    for pid in selected:
        if pid not in known_pids:
            fails.append("入选题 %r 未在 selection.json candidates 中登记"
                         % pid)

    # ---- 每题 dossier：存在性 + schema + 重复 ID ----
    dossiers = {}
    for pid in selected:
        rel = "research/problems/%s/dossier.json" % pid
        doc, err = load_json(root, rel)
        if err:
            fails.append("入选题 %s 缺少研究档案: %s" % (pid, err))
            continue
        dossiers[pid] = (rel, doc)
        fails.extend(schema_errors(doc, "dossier", rel))
        if doc.get("problem_id") != pid:
            fails.append("%s: problem_id %r 与目录名 %r 不一致"
                         % (rel, doc.get("problem_id"), pid))
        for field, idf in (("claims", "claim_id"),
                           ("proof_obligations", "obligation_id"),
                           ("obstacles", "obstacle_id"),
                           ("supplements", "supplement_id")):
            for d in contract.check_stable_ids(doc.get(field, []), idf):
                fails.append("%s: %s 重复: %s" % (rel, idf, d))
    for d in contract.check_stable_ids(brief.get("supplements", []),
                                       "supplement_id"):
        fails.append("brief.supplements 中 supplement_id 重复: %s" % d)
    for dec in brief.get("teaching_decisions", []):
        for d in contract.check_stable_ids(dec.get("key_tasks", []),
                                           "task_id"):
            fails.append("brief.teaching_decisions(%s).key_tasks 中 "
                         "task_id 重复: %s" % (dec.get("problem_id"), d))
    for d in contract.check_stable_ids(brief.get("reveal_plan", []),
                                       "info_id"):
        fails.append("brief.reveal_plan 中 info_id 重复: %s" % d)

    # ---- 1b. 子 Agent 认知隔离核验（三重门禁）----
    # 【v2.3 Secure Upgrade】对每个入选题的三件套（probe / pitfall / solution）：
    #   第一层：UUID 格式校验 — 非 RFC 4122 UUID 字符串直接判定伪造
    #   第二层：本地物理存在性校验 — Brain 目录无对应子会话目录 → 判定伪造
    #   第三层：互不重复校验 — 三件套 ID 相同 → 判定主进程代劳
    # 【CRITICAL】字段不存在或 null → 不再跳过，直接强制失败！删除所有兼容旁路。
    for pid in selected:
        _conv_ids = {}
        _execution_missing = False
        
        for _role, _rel in (
            ("probe",   "research/problems/%s/probe.json" % pid),
            ("pitfall", "research/problems/%s/pitfall.json" % pid),
            ("solver",  "research/problems/%s/solution.json" % pid),
        ):
            _doc, _err = load_json(root, _rel)
            if _err or _doc is None:
                continue
            
            # === CRITICAL: 强制执行 metadata 存在性检查 ===
            _meta = _doc.get("execution_metadata")
            if not isinstance(_meta, dict) or not _meta:
                fails.append(
                    "[MISSING_EXECUTION_METADATA] 题目 %s (%s) 缺少 execution_metadata 字段。"
                    "§9 v2.3 Secure Upgrade: 所有输出必须包含真实子 Agent 调用证据。"
                    "请通过 invoke_subagent 生成该文件，并填充 subagent_conversation_id 为真实 UUID。"
                    % (pid, _role)
                )
                _execution_missing = True
                continue  # 跳过后续检查，因为基础元数据缺失
            
            _cid = _meta.get("subagent_conversation_id")
            
            # === CRITICAL: 禁止空值或空字符串 ===
            if not _cid or str(_cid).strip() == "":
                fails.append(
                    "[EMPTY_SUBAGENT_CONVERSATION_ID] 题目 %s (%s) 的 execution_metadata.subagent_conversation_id 为空。"
                    "§9 v2.3 Secure Upgrade: 必须填写真实 invoke_subagent 生成的 RFC 4122 UUID。"
                    "不允许填写 null、空字符串或占位符（如 \"self\"、\"main\"）。"
                    % (pid, _role)
                )
                _execution_missing = True
                continue

            # === 第一层：UUID 格式校验 ===
            _ok, _reason = _verify_subagent_id(_cid)
            if not _ok:
                if _reason == "non_uuid_format":
                    fails.append(
                        "[FORGED_SUBAGENT_CALL] 题目 %s (%s) 的 "
                        "subagent_conversation_id=%r 不是合法 UUID 格式 "
                        "（期望 8-4-4-4-12 十六进制，RFC 4122）。"
                        "主进程直接撰写此文件并填写伪造 ID，门禁拒绝通过。"
                        % (pid, _role, _cid)
                    )
                elif _reason == "no_local_session_dir":
                    # === 第二层：本地物理存在性校验 ===
                    fails.append(
                        "[FORGED_SUBAGENT_CALL] 题目 %s (%s) 的 "
                        "subagent_conversation_id=%r 在本地 Brain 目录下 "
                        "不存在对应子会话目录（%s/%s）。"
                        "该 ID 从未经由真实 invoke_subagent 创建，门禁拒绝通过。"
                        % (pid, _role, _cid, _AGY_BRAIN_DIR, _cid)
                    )
                elif _reason == "empty_session_or_no_log":
                    # === 第二层加固：穿透校验 transcript 真实性（防 mkdir 空目录） ===
                    fails.append(
                        "[FORGED_SUBAGENT_CALL] 题目 %s (%s) 的 "
                        "subagent_conversation_id=%r 对应的会话目录存在但缺少真实 "
                        "transcript.jsonl 日志或 MODEL 执行证据（疑似通过 mkdir 伪造空目录）。"
                        "必须由真实 invoke_subagent 执行并产生日志，门禁拒绝通过。"
                        % (pid, _role, _cid)
                    )
                continue  # 格式或物理校验已失败，不计入重复检测集合

            _conv_ids[_role] = _cid

        # === 第三层：互不重复校验 ===
        if len(set(_conv_ids.values())) < len(_conv_ids):
            _dup_roles = sorted(_conv_ids.keys())
            fails.append(
                "[FORGED_SUBAGENT_CALL] 题目 %s 的三件套 "
                "subagent_conversation_id 存在重复（%s）；"
                "probe/pitfall/solver 必须由互相独立的子会话执行，"
                "主进程代劳将导致 ID 相同，门禁拒绝通过。"
                % (pid, {r: _conv_ids[r] for r in _dup_roles})
            )

    for rel, doc in [("research/run.json", docs["run"]),
                     ("research/selection.json", selection),
                     ("research/brief.json", brief)]:
        for p in contract.resolve_refs(root, doc):
            fails.append("%s: 引用文件%s: %s"
                         % (rel, "缺失" if p["reason"] == "missing"
                            else "越出项目根", p["ref"]))
    for pid, (rel, doc) in dossiers.items():
        for p in contract.resolve_refs(root, doc):
            fails.append("%s: 引用文件%s: %s"
                         % (rel, "缺失" if p["reason"] == "missing"
                            else "越出项目根", p["ref"]))

    # ---- 3. brief 采用的 claims 的 verification 状态 ----
    selected_set = set(selected)
    for pid, (rel, doc) in dossiers.items():
        for claim in doc.get("claims", []):
            if not selected_set.intersection(claim.get("used_by", [])):
                continue
            evidence["claims_checked"] += 1
            cid, ver, kind = (claim.get("claim_id"), claim.get("verification"),
                              claim.get("kind"))
            tag = "%s: claim %s（kind=%s，used_by 指向入选题）" % (rel, cid,
                                                                  kind)
            if ver == "refuted":
                fails.append("%s 的 verification=refuted，不得进入采用集"
                             % tag)
            elif ver == "pending" and kind != "teaching_hypothesis":
                fails.append("%s 的 verification=pending，不得进入采用集"
                             % tag)
            if kind == "teaching_hypothesis" and ver != "verified":
                reviews.append("%s 为未核验教学假设（%s），允许交接但必须"
                               "保持如实标注，试教前不得升级为事实"
                               % (tag, ver))
            if kind == "classroom_observation":
                refs = claim.get("evidence_refs", [])
                pilot = [r for r in refs
                         if any(tok in r.lower() for tok in PILOT_TOKENS)]
                if not pilot:
                    fails.append("%s 为 classroom_observation，但没有真实"
                                 "试教来源（evidence_refs 中无 pilot/trial "
                                 "路径）；仅凭推断不得填写课堂观察"
                                 % tag)

    # ---- 3.9 R3 孤儿产物门禁（enable_obstacle_study 默认关闭）----
    # R3（obstacle-study / supplement-study）在 LangGraph 图中没有对应节点，
    # 且 student.pdf / student_solution.pdf 两条交付链路均不消费 dossier 的
    # obstacles/supplements（学生窄包白名单只有 statement_ref+probe_ref+
    # brief）。开关未开启（默认）时，任何非空 R3 产物都是"无交付通道的孤儿
    # 产物"，还会给 deliver.py 追加审查覆盖义务，fail-closed 拒收；开关开启
    # 时本小节不产生新 fail（沿用 §4 原有逻辑，孤儿引用闭合由
    # check_teaching §3d 负责）。只对成功装载的 dossier 检查。
    r3_enabled = contract.read_project_flag(root, "enable_obstacle_study",
                                            default=False)
    if not r3_enabled:
        _r3_hint = ("enable_obstacle_study 未开启（默认关闭）：R3 产物无交"
                    "付通道（student.pdf/student_solution.pdf 均不消费），"
                    "且会给 deliver 追加审查覆盖义务。如确需启用障碍/补充材"
                    "料研究，请在 config.yaml 设置 enable_obstacle_study: "
                    "true 并为其打通 materials.json 消费链（check_teaching "
                    "将做孤儿引用闭合检查）。")
        for pid, (rel, doc) in dossiers.items():
            n_obs = len(doc.get("obstacles", []))
            if n_obs:
                fails.append("[ORPHAN_R3_DISABLED] 题 %s 的 dossier."
                             "obstacles 含 %d 条障碍研究产物，但 %s"
                             % (pid, n_obs, _r3_hint))
            n_sup = len(doc.get("supplements", []))
            if n_sup:
                fails.append("[ORPHAN_R3_DISABLED] 题 %s 的 dossier."
                             "supplements 含 %d 条补充材料产物，但 %s"
                             % (pid, n_sup, _r3_hint))
        n_bsup = len(brief.get("supplements", []))
        if n_bsup:
            fails.append("[ORPHAN_R3_DISABLED] brief.supplements 含 %d 条补"
                         "充材料采用记录，但 %s" % (n_bsup, _r3_hint))

    # ---- 4. brief 采用的补充材料 ----
    dossier_sups = {}
    for pid, (rel, doc) in dossiers.items():
        for sup in doc.get("supplements", []):
            dossier_sups[sup.get("supplement_id")] = (pid, rel, sup)
    for sup in brief.get("supplements", []):
        evidence["supplements_checked"] += 1
        sid, ver = sup.get("supplement_id"), sup.get("verification")
        if ver in ("pending", "refuted"):
            fails.append("brief.supplements %s verification=%s，无证据补充"
                         "材料不得进入已采用数学内容" % (sid, ver))
        hit = dossier_sups.get(sid)
        if hit is None:
            fails.append("brief.supplements %s 未在任何入选题 dossier 的 "
                         "supplements 中登记" % sid)
            continue
        _pid, drel, dsup = hit
        if dsup.get("verification") != ver:
            fails.append("补充材料 %s 的 verification 在 brief（%s）与 "
                         "%s（%s）之间不一致" % (sid, ver, drel,
                                                 dsup.get("verification")))
        if not dsup.get("review_refs"):
            fails.append("补充材料 %s（%s）无独立复核记录（review_refs 为"
                         "空）；新增数学内容进入正式交接前需独立复核"
                         % (sid, drel))

    # ---- 5. 采用的证明义务必须闭合 ----
    obligations = {}
    for pid, (rel, doc) in dossiers.items():
        for ob in doc.get("proof_obligations", []):
            obligations[ob.get("obligation_id")] = (pid, rel, ob)
    for mb in brief.get("math_baselines", []):
        pid = mb.get("problem_id")
        if pid not in dossiers:
            fails.append("brief.math_baselines 引用的题 %s 无 dossier" % pid)
        for oid in mb.get("proof_obligation_refs", []):
            hit = obligations.get(oid)
            if hit is None:
                fails.append("brief.math_baselines(%s) 采用的证明义务 %s "
                             "未在任何 dossier 中登记" % (pid, oid))
                continue
            _p, drel, ob = hit
            if ob.get("status") != "verified":
                fails.append("采用的证明义务 %s（%s）status=%s；进入交接包"
                             "的数学内容必须闭合（verified）"
                             % (oid, drel, ob.get("status")))

    # ---- 5.1 四 Agent 认知资产完整性检查 ----
    cognitive_enabled = brief.get("cognitive_workflow", {}).get("enabled", True)
    for mb in brief.get("math_baselines", []):
        pid = mb.get("problem_id")
        p_ref = mb.get("probe_ref")
        f_ref = mb.get("pitfall_ref")
        s_ref = mb.get("solution_ref")

        if cognitive_enabled:
            if not p_ref:
                fails.append("brief.math_baselines(%s) 缺少 probe_ref（认知工作流必须包含探针报告）" % pid)
            if not f_ref:
                fails.append("brief.math_baselines(%s) 缺少 pitfall_ref（认知工作流必须包含错解报告）" % pid)
            if not s_ref:
                fails.append("brief.math_baselines(%s) 缺少 solution_ref（认知工作流必须包含严谨解答报告）" % pid)

        # 若提供了对应引用，核验文件存在性及一致性
        if p_ref:
            p_abs = contract.resolve_path(root, p_ref)
            if not p_abs or not os.path.isfile(p_abs):
                fails.append("brief.math_baselines(%s) 的 probe_ref 文件不存在: %s" % (pid, p_ref))
        if f_ref:
            f_abs = contract.resolve_path(root, f_ref)
            if not f_abs or not os.path.isfile(f_abs):
                fails.append("brief.math_baselines(%s) 的 pitfall_ref 文件不存在: %s" % (pid, f_ref))
        if s_ref:
            s_abs = contract.resolve_path(root, s_ref)
            if not s_abs or not os.path.isfile(s_abs):
                fails.append("brief.math_baselines(%s) 的 solution_ref 文件不存在: %s" % (pid, s_ref))

    # ---- 6. 任务时长 ≤ 课程预算 ----
    budget = brief.get("course_constraints", {}).get("total_time_minutes")
    total = 0
    missing_time = False
    for dec in brief.get("teaching_decisions", []):
        for task in dec.get("key_tasks", []):
            t = task.get("time_minutes")
            if t is None:
                missing_time = True
            else:
                total += t
    evidence["task_time_total"] = total
    if budget is None:
        reviews.append("brief 缺课程预算 total_time_minutes，无法核对任务"
                       "时长；不得擅自通过")
    else:
        if total > budget:
            fails.append("已安排任务时间总和 %d 分钟超过课程预算 %d 分钟"
                         % (total, budget))
        if missing_time:
            reviews.append("存在缺 time_minutes 的关键任务，时间核对不完"
                           "整；不得擅自通过")

    # ---- 7. 研究报告覆盖 ----
    covered = set()
    review_refs = brief.get("freeze_basis", {}).get("research_review_refs",
                                                    [])
    if not review_refs:
        fails.append("brief.freeze_basis.research_review_refs 为空；研究"
                     "复核须覆盖全部入选题和跨题设计")
    for rref in review_refs:
        doc, err = load_json(root, rref)
        if err:
            fails.append("研究复核记录不可读: %s" % err)
            continue
        rerrs = schema_errors(doc, "review", rref)
        fails.extend(rerrs)
        if rerrs:
            continue
        evidence["reviews_checked"].append(rref)
        if doc.get("scope") != "research":
            fails.append("%s: scope=%s，冻结依据须为 scope=research 的研究"
                         "复核" % (rref, doc.get("scope")))
        covered.update(doc.get("coverage", []))
    for pid in selected:
        if pid not in covered:
            fails.append("入选题 %s 未被任何研究复核记录的 coverage 覆盖"
                         % pid)
    if brief_id not in covered:
        fails.append("跨题设计（brief_id=%s）未被任何研究复核记录的 "
                     "coverage 覆盖" % brief_id)

    # ---- 8. 开放项阻断标记 ----
    for item in brief.get("open_items", []):
        if item.get("blocks_handoff"):
            reviews.append("open_items 中 %s 标记 blocks_handoff=true：%s；"
                           "须人工/研究复核逐项处置，机器无法判定阻断范围"
                           % (item.get("item_id"), item.get("description")))

    return fails, reviews, evidence, fatal


def main():
    ap = argparse.ArgumentParser(description="教研结构检查（不接写数学判断）")
    ap.add_argument("project_root", help="项目根（含 research/ 的目录）")
    ap.add_argument("--json", metavar="PATH", default=None,
                    help="报告落盘路径（默认 research/checks/"
                         "check_research.json）")
    args = ap.parse_args()
    root = os.path.abspath(args.project_root)
    if not os.path.isdir(root):
        print("[check_research] 项目根不存在: %s" % root)
        return 2

    fails, reviews, evidence, fatal = check(root)
    if fatal:
        status = "error"
    elif fails:
        status = "fail"
    elif reviews:
        status = "review"
    else:
        status = "pass"

    findings = ([{"severity": "error", "message": m} for m in fatal]
                + [{"severity": "fail", "message": m} for m in fails]
                + [{"severity": "review", "message": m} for m in reviews])
    report_path = args.json or os.path.join(root, DEFAULT_REPORT)
    report = contract.write_check_report(report_path, status, "research",
                                         findings)
    report["evidence"] = evidence
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")

    for m in fatal:
        print("[check_research] [错误] " + m)
    for m in fails:
        print("[check_research] [失败] " + m)
    for m in reviews:
        print("[check_research] [待复核] " + m)
    print("[check_research] status=%s，报告已写入 %s" % (status, report_path))
    if status == "error":
        return 2
    if status == "fail":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
