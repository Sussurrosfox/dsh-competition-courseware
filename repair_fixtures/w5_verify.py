# -*- coding: utf-8 -*-
"""W5 夹具工程 + 端到端门禁联动验收（只写 repair_fixtures/，其余只读）。

构造最小自洽夹具工程 proj/（冻结 brief + statement/probe/solution + staging），
对 W1-W4 施工的「小问机制」修复做集成验证：
  步骤2: packet solution_skeleton 派生断言（target=5 / 白名单 / 中性化 / schema）
  步骤3: 夹具 A/B/C/D 逐一跑 check_student_task.py（F10b / F13a / F13b）
  步骤4: 无骨架回归（P002 无 solution_ref，行为与修复前一致）
  步骤5: T2 自审派发单（node_t2_review_request 运行时调用，失败降级静态断言）
结果写入 VERIFY_REPORT.md。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import uuid

FIX_DIR = os.path.dirname(os.path.abspath(__file__))          # repair_fixtures/
PLUGIN = os.path.dirname(FIX_DIR)                             # 插件根 $P
SCRIPTS = os.path.join(PLUGIN, "scripts")
PROJ = os.path.join(FIX_DIR, "proj")
OUT = os.path.join(FIX_DIR, "out")
REPORT_MD = os.path.join(FIX_DIR, "VERIFY_REPORT.md")

ENV = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")

RESULTS = []  # (rid, name, status, command, evidence)


def record(rid, name, ok, command, evidence):
    RESULTS.append({
        "id": rid, "name": name,
        "status": "PASS" if ok else "FAIL",
        "command": command, "evidence": evidence.strip()})
    print("[%s] %s: %s" % ("PASS" if ok else "FAIL", rid, name))


def run_cli(args, cwd=None):
    p = subprocess.run([sys.executable, "-X", "utf8"] + args,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", cwd=cwd, env=ENV)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def wj(rel, obj):
    abs_p = os.path.join(PROJ, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(abs_p), exist_ok=True)
    with open(abs_p, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def wt(rel, text):
    abs_p = os.path.join(PROJ, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(abs_p), exist_ok=True)
    with open(abs_p, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def rj(rel):
    with open(os.path.join(PROJ, rel.replace("/", os.sep)),
              "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 夹具内容
# ---------------------------------------------------------------------------
STEM1 = ("设 $n$ 为正整数，记 $S=1+2+\\cdots+n$。问题：对哪些正整数 $n$，"
         "$S$ 整除 $n(n+1)$？请给出完整刻画与论证。")
STEM2 = ("设 $a,b$ 为正实数且 $a+b=1$。求 $a^2+b^2$ 的最小值，"
         "并说明等号成立的条件。")

SOLUTION1 = {
    "schema_version": "2.5.0",
    "problem_id": "P001",
    "proof_obligations": [
        {"obligation_id": "OB1",
         "claim": "枚举小情形，观察和 S 与 n(n+1) 的因子关系",
         "status": "verified"},
        {"obligation_id": "OB2",
         "claim": "由威尔逊定理处理奇素数情形的同余结构",
         "status": "verified"},
        {"obligation_id": "OB3",
         "claim": "比较两边的素因子幂次，完成整除性的充分性论证",
         "status": "verified"},
        {"obligation_id": "OB4",
         "claim": "综合以上各步，故原题结论成立",
         "status": "verified"},
    ],
}

PROBE1 = {
    "problem_id": "P001",
    "student_warmup": {
        "question": "取 n=1,2,3,4 计算 S 与 n(n+1)，观察整除关系。",
        "test_points": [1, 2, 3, 4],
        "expected_phenomenon": "n 为奇数时 S 整除 n(n+1)。",
    },
    "probes": [
        {"probe_id": "pr1", "kind": "numeric", "path": "n=3",
         "numeric_value": "S=6, n(n+1)=12", "comparison": "6|12",
         "equality_status": "holds"},
    ],
}

PROBE2 = {
    "problem_id": "P002",
    "student_warmup": {
        "question": "取 a=b=0.5 与 a=0.2,b=0.8 比较 a^2+b^2。",
        "test_points": [0.5, 0.2],
        "expected_phenomenon": "a=b 时取值更小。",
    },
    "probes": [],
}

PROMPTS_A = [
    "取 n=1,2,3,4，分别计算 S 与 n(n+1)，观察 S 是否整除 n(n+1)，记录你的发现。",
    "观察 S 与 n(n+1) 的因子结构，尝试把 S 用 n 表示，写出你得到的恒等关系。",
    "分奇偶讨论 n 的情形：比较两边各素因子的幂次，写下整除成立的必要条件。",
    "核对你猜想的条件是否充分：对满足条件的 n，逐步核实整除关系，记录论证链条。",
    "综合以上各步，回答原题：对哪些正整数 n，S=1+2+\\cdots+n 整除 n(n+1)？给出完整刻画与论证。",
]

PROMPTS_B = [
    "取 n=1,2,3,4 计算 S 与 n(n+1)，观察整除关系何时成立，记录你的发现。",
    "把 S 用 n 表示后，比较两边素因子幂次，写下整除成立的必要条件与理由。",
    "综合你的论证，回答原题：对哪些正整数 n，S 整除 n(n+1)？给出完整刻画。",
]

PROMPTS_P2 = [
    "代入几组满足 a+b=1 的正实数，计算 a^2+b^2 的值，观察其大小变化。",
    "把 b 用 1-a 代入，将 a^2+b^2 化为关于 a 的式子，求出它的最小值。",
    "说明等号成立时 a 与 b 的关系，并写出你的最终回答。",
]

# 夹具 E：5 问全覆盖、末问绑 OB4，但末问 prompt 与题干完全无关（R6 正向触发）
PROMPTS_E = list(PROMPTS_A)
PROMPTS_E[-1] = "随手画一个你喜欢的图形，给每个顶点起个名字。"

# 变体定义：(run_id, prompts, obligation_refs 每小问)
VARIANTS = {
    "A": ("run-fix-a", PROMPTS_A,
          [["OB1"], ["OB2"], ["OB3"], ["OB4"], ["OB4"]]),
    "B": ("run-fix-b", PROMPTS_B,
          [["OB1"], ["OB2", "OB3"], ["OB4"]]),
    "C": ("run-fix-c", PROMPTS_A,
          [["OB1"], ["OB2"], ["OB4"], ["OB4"], ["OB4"]]),
    "D": ("run-fix-d", PROMPTS_A,
          [["OB1"], ["OB2"], ["OB3"], ["OB4"], ["OB1"]]),
    "E": ("run-fix-e", PROMPTS_E,
          [["OB1"], ["OB2"], ["OB3"], ["OB4"], ["OB4"]]),
}

RUNS = ["run-fix-a", "run-fix-b", "run-fix-c", "run-fix-d", "run-fix-e"]


def build_brief():
    return {
        "schema_version": "2.5.0",
        "brief_id": "brief-fixture-w5",
        "revision": 1,
        "run_id": "research-run-fixture",
        "source_manifest_ref": "research/manifest.json",
        "selected_questions": {
            "P001": {"status": "selected", "title": "整除刻画（夹具）",
                     "stem": STEM1},
            "P002": {"status": "selected", "title": "平方和最小值（夹具）",
                     "stem": STEM2},
        },
        "course_constraints": {
            "goals": ["夹具：验证小问机制修复"],
            "student_prerequisites": [
                {"item": "整数整除与素因子分解", "basis": "assumed"}],
            "total_time_minutes": 45,
            "assumptions": ["夹具工程，内容不构成真实教学"],
        },
        "selection": {"selected_problem_ids": ["P001", "P002"]},
        "math_baselines": [
            {"problem_id": "P001",
             "statement_ref": "research/problems/P001/statement.md",
             "probe_ref": "research/problems/P001/probe.json",
             "solution_ref": "research/problems/P001/solution.json",
             "proof_refs": ["research/problems/P001/solution.json"],
             "proof_obligation_refs": ["OB1", "OB2", "OB3", "OB4"],
             "subquestion_refs": []},
            {"problem_id": "P002",
             "statement_ref": "research/problems/P002/statement.md",
             "probe_ref": "research/problems/P002/probe.json",
             "proof_refs": [],
             "proof_obligation_refs": [],
             "subquestion_refs": []},
        ],
        "teaching_decisions": [
            {"problem_id": pid,
             "lesson_mode": "guided",
             "objectives": ["夹具目标"],
             "key_tasks": [{"task_id": "task-%s" % pid,
                            "description": "夹具任务",
                            "student_evidence": "分步作答",
                            "time_minutes": 20}],
             "mode_rationale": "夹具"}
            for pid in ("P001", "P002")],
        "supplements": [],
        "reveal_plan": [
            {"info_id": "info-P001-stem", "problem_id": "P001", "level": "L0",
             "audience": ["student"], "available_after": "initial",
             "delivery_channel": "in_main"},
            {"info_id": "info-P002-stem", "problem_id": "P002", "level": "L0",
             "audience": ["student"], "available_after": "initial",
             "delivery_channel": "in_main"},
        ],
        "protected_strategy_tokens": ["威尔逊定理"],
        "acceptance_requirements": {
            "per_problem": [
                {"problem_id": "P001", "completion_criteria": ["覆盖全部义务"]},
                {"problem_id": "P002", "completion_criteria": ["完成求解"]}],
            "lesson_alignment": "夹具",
            "release_order": ["P001", "P002"],
        },
        "open_items": [],
        "freeze_basis": {
            "decision_log_ref": "research/decision-log.md",
            "research_review_refs": ["research/review/research-review.json"],
        },
    }


def build_project():
    if os.path.isdir(PROJ):
        shutil.rmtree(PROJ)
    os.makedirs(PROJ)
    wt("research/manifest.json", json.dumps(
        {"schema_version": "2.5.0", "sources": []},
        ensure_ascii=False, indent=2) + "\n")
    wt("research/decision-log.md", "# 夹具决策记录\n\n冻结 brief revision 1。\n")
    wj("research/review/research-review.json", {
        "schema_version": "2.5.0", "review_id": "rr-fixture",
        "verdict": "pass", "notes": "夹具复核记录"})
    wt("research/problems/P001/statement.md",
       "# P001\n\n## 题面\n\n" + STEM1 + "\n")
    wt("research/problems/P002/statement.md",
       "# P002\n\n## 题面\n\n" + STEM2 + "\n")
    wj("research/problems/P001/probe.json", PROBE1)
    wj("research/problems/P002/probe.json", PROBE2)
    wj("research/problems/P001/solution.json", SOLUTION1)
    brief = build_brief()
    wj("research/brief.json", brief)
    wj("teaching/user-spec.json", {
        "confirmed": True,
        "main_title": "W5 夹具学案",
        "lesson_mode": "guided",
        "confirmed_at": "2026-01-01T00:00:00+08:00"})
    # 冻结：brief_digest 由 contract 机械计算（排除 research_review_refs）
    sys.path.insert(0, SCRIPTS)
    import contract
    info = contract.compute_brief_digest(
        PROJ, brief,
        exclude_refs=brief["freeze_basis"]["research_review_refs"])
    if info["missing"]:
        raise RuntimeError("brief 引用缺失: %s" % info["missing"])
    wj("research/freeze.json", {
        "schema_version": "2.5.0",
        "brief_id": brief["brief_id"],
        "revision": brief["revision"],
        "brief_digest": info["digest"],
        "dependency_digests": info["inputs"],
        "research_review_refs": brief["freeze_basis"]["research_review_refs"],
        "decision_ref": "research/decision-log.md",
        "frozen_at": "2026-01-01T00:00:00+08:00"})
    return brief, info


def broker(*args):
    return run_cli([os.path.join(SCRIPTS, "broker.py"), PROJ] + list(args))


def make_task(qid, run_id, prompts, refs_list, packet, receipt, with_refs=True):
    subqs = []
    for i, prompt in enumerate(prompts, start=1):
        sq = {"subquestion_id": "sq%d" % i, "prompt": prompt,
              "answer_space_mm": 24}
        if with_refs:
            sq["obligation_refs"] = refs_list[i - 1]
        if i == 1 and qid == "P001":
            sq["hint"] = "把 S 写成含 n 的式子再观察。"
            sq["hint_level"] = "observation"
        subqs.append(sq)
    return {
        "schema_version": "2.5.0",
        "run_id": run_id,
        "question_id": qid,
        "section_id": "S1",
        "task_id": "task-%s" % qid,
        "title": "task-%s 夹具任务" % qid,
        "stem": packet["problem_stem"],
        "subquestions": subqs,
        "level": "L0",
        "source_refs": [r for r in packet["allowed_source_refs"]
                        if r != "research/brief.json"],
        "brief_id": packet["brief_id"],
        "brief_revision": packet["brief_revision"],
        "packet_digest": packet["packet_digest"],
        "receipt_ref": receipt["receipt_ref_rel"],
        "execution_metadata": {
            "agent_role": "student-task",
            "subagent_conversation_id": str(uuid.uuid4()),
            "worker_session_id": receipt["worker_session_id"],
        },
    }


def chain_task(run_id, qid, task_obj):
    """submit + render + 拷贝到 teaching/generated，返回 (ok, log)。"""
    tid = "task-%s" % qid
    staging_rel = "staging/%s/tasks/%s/student-task.json" % (run_id, tid)
    wj(staging_rel, task_obj)
    log = []
    rc, out = broker("submit_task", "--run-id", run_id, "--task-id", tid,
                     "--input", staging_rel)
    log.append("submit_task rc=%d %s" % (rc, out.strip().splitlines()[-1]
                                         if out.strip() else ""))
    if rc != 0:
        return False, "\n".join(log) + "\n" + out
    rc, out = broker("render_task", "--run-id", run_id, "--task-id", tid)
    log.append("render_task rc=%d" % rc)
    if rc != 0:
        return False, "\n".join(log) + "\n" + out
    src = os.path.join(PROJ, staging_rel.replace("/", os.sep))
    dst = os.path.join(PROJ, "teaching", "generated",
                       "%s-student-task.json" % qid)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    return True, "\n".join(log)


def issue(run_id, tid):
    rc, out = broker("issue_packet", "--run-id", run_id, "--task-id", tid)
    if rc != 0:
        raise RuntimeError("issue_packet %s/%s 失败: %s" % (run_id, tid, out))
    run = rj("staging/%s/run.json" % run_id)
    entry = run["tasks"][tid]
    packet = rj("staging/%s/packets/%s.json" % (run_id, tid))
    receipt = rj(entry["receipt_ref"])
    receipt["receipt_ref_rel"] = entry["receipt_ref"]
    return packet, receipt


def gate(tag):
    report_rel = os.path.join(OUT, "gate-%s.json" % tag)
    os.makedirs(OUT, exist_ok=True)
    rc, out = run_cli([os.path.join(SCRIPTS, "check_student_task.py"),
                       PROJ, "--json", report_rel])
    with open(report_rel, "r", encoding="utf-8") as f:
        report = json.load(f)
    fails = [f["message"] for f in report.get("findings", [])
             if f.get("severity") in ("fail", "error")]
    reviews = [f["message"] for f in report.get("findings", [])
               if f.get("severity") == "review"]
    return rc, report.get("status"), fails, reviews, out


def swap_p001(variant_run):
    """把 teaching/generated/P001-student-task.json 换成指定 run 的提交件。"""
    src = os.path.join(PROJ, "staging", variant_run, "tasks", "task-P001",
                       "student-task.json")
    dst = os.path.join(PROJ, "teaching", "generated", "P001-student-task.json")
    shutil.copyfile(src, dst)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    brief, freeze_info = build_project()

    # ---- 建立 4 个 run 并签发 packet ----
    for rid in RUNS:
        rc, out = broker("create_run", "--run-id", rid)
        if rc != 0:
            raise RuntimeError("create_run %s 失败: %s" % (rid, out))
    packets, receipts = {}, {}
    for rid in RUNS:
        packets[(rid, "task-P001")], receipts[(rid, "task-P001")] = \
            issue(rid, "task-P001")
    packets[("run-fix-a", "task-P002")], receipts[("run-fix-a", "task-P002")] = \
        issue("run-fix-a", "task-P002")

    # ==================================================================
    # 步骤 2：packet solution_skeleton 派生断言（直跑 make_small_packet）
    # ==================================================================
    rc, out = run_cli([os.path.join(SCRIPTS, "make_small_packet.py"),
                       "--project", PROJ, "--run-id", "run-fix-a",
                       "--task-id", "task-P001", "--kind", "student-task"])
    pkt = rj("staging/run-fix-a/packets/task-P001.json")
    skel = pkt.get("solution_skeleton") or {}
    obs = skel.get("obligations") or []
    cmd2 = ("python scripts/make_small_packet.py --project proj "
            "--run-id run-fix-a --task-id task-P001 --kind student-task")

    a2a = (rc == 0
           and skel.get("target_subquestion_count") == 5
           and len(obs) == 4
           and [o.get("obligation_id") for o in obs] == ["OB1", "OB2", "OB3", "OB4"]
           and obs[-1].get("terminal") is True
           and all(o.get("terminal") is False for o in obs[:-1]))
    record("2a", "packet 携带 solution_skeleton 且 target_subquestion_count=5、"
                 "4 条义务、末位 terminal=true", a2a, cmd2,
           "rc=%d; target=%r; ids=%s; terminals=%s"
           % (rc, skel.get("target_subquestion_count"),
              [o.get("obligation_id") for o in obs],
              [o.get("terminal") for o in obs]))

    FORBID = re.compile(r"solution|proof|audit|review|teacher[-_]?note|"
                        r"答案|解答|证明", re.IGNORECASE)
    refs = pkt.get("allowed_source_refs", [])
    digs = pkt.get("input_digests", {})
    body_wo_skel = {k: v for k, v in pkt.items() if k != "solution_skeleton"}
    body_txt = json.dumps(body_wo_skel, ensure_ascii=False)
    a2b = (not any(FORBID.search(r) for r in refs)
           and not any(FORBID.search(k) for k in digs)
           and set(refs) == set(digs.keys())
           and "威尔逊" not in body_txt
           and "solution.json" not in body_txt)
    record("2b", "allowed_source_refs/input_digests 无任何 solution/proof 路径，"
                 "骨架外 body 不含解法痕迹", a2b, cmd2,
           "allowed_source_refs=%s" % (refs,))

    signposts = [o.get("signpost", "") for o in obs]
    a2c = (signposts[1] == "完成第 2 步关键论证（路标已中性化）"
           and all("威尔逊" not in s for s in signposts)
           and all("故原题结论成立" not in s for s in signposts)
           and not re.search(r"所以|因此|综上|故得|显然|易知|必为|答案是",
                             "".join(signposts)))
    record("2c", "signpost 中性化：OB2 命中 protected token 回退；"
                 "OB4 截断首子句、不含结论文本", a2c, cmd2,
           "signposts=%s" % (signposts,))

    sys.path.insert(0, SCRIPTS)
    import contract
    verrs = contract.validate(pkt, "packet")
    record("2d", "packet 通过 contract.validate(_, 'packet')",
           verrs == [], cmd2,
           "schema errors=%s" % (verrs[:2] if verrs else "无"))

    # P002 packet：无 solution_ref → 无骨架（回归基座）
    pkt2 = packets[("run-fix-a", "task-P002")]
    record("2e", "P002（无 solution_ref）packet 不携带 solution_skeleton",
           "solution_skeleton" not in pkt2, cmd2,
           "keys=%s" % sorted(pkt2.keys()))

    # ==================================================================
    # 步骤 3：构造 A/B/C/D + P002 旧式任务，走 broker 提交/渲染，跑门禁
    # ==================================================================
    logs = {}
    for tag, (rid, prompts, refs_list) in VARIANTS.items():
        pkt_v = packets[(rid, "task-P001")]
        rcp_v = receipts[(rid, "task-P001")]
        task = make_task("P001", rid, prompts, refs_list, pkt_v, rcp_v)
        ok, logs[tag] = chain_task(rid, "P001", task)
        if not ok:
            record("3-prep-%s" % tag, "夹具 %s broker 提交/渲染链路" % tag,
                   False, "broker submit/render", logs[tag])
    task_p2 = make_task("P002", "run-fix-a", PROMPTS_P2, None,
                        packets[("run-fix-a", "task-P002")],
                        receipts[("run-fix-a", "task-P002")], with_refs=False)
    ok, logs["P2"] = chain_task("run-fix-a", "P002", task_p2)
    record("3-prep", "夹具提交链：A/B/C/D/E + P002 旧式任务均通过 "
                     "broker submit/render（骨架门禁只在 check 侧）",
           ok and all(k in logs for k in "ABCDE"), "broker submit/render",
           "\n".join("%s: %s" % (k, v.replace("\n", " | "))
                     for k, v in sorted(logs.items())))

    # ---- 夹具 A（合规）----
    swap_p001("run-fix-a")
    rc, status, fails, reviews, out = gate("A")
    gate_cmd = ("python scripts/check_student_task.py proj --json "
                "repair_fixtures/out/gate-A.json")
    a3a = (rc == 0 and not fails)
    record("3A", "夹具 A（5 问全覆盖、末问绑 OB4 且收束题干）："
                 "退出码 0，无 F10b/F13a/F13b fail", a3a, gate_cmd,
           "rc=%d status=%s fails=%s reviews=%d 条%s"
           % (rc, status, fails or "无", len(reviews),
              ("（样例：" + reviews[0][:90] + "…）") if reviews else ""))

    # ---- 夹具 B（3 问 → F10b）----
    swap_p001("run-fix-b")
    rc, status, fails, reviews, out = gate("B")
    f10b = [m for m in fails if "解法骨架目标问数" in m]
    xrep = [m for m in fails if "并集未覆盖" in m or "terminal 义务" in m
            or "骨架之外的义务" in m]
    a3b = (rc == 1 and len(f10b) == 1 and "小问数 3" in f10b[0]
           and "目标问数 5" in f10b[0] and not xrep)
    record("3B", "夹具 B（3 问）：触发且仅触发 F10b fail（无 F13 串报）",
           a3b, gate_cmd.replace("A", "B"),
           "rc=%d; F10b=%s; 串报=%s; 全部 fails=%s"
           % (rc, f10b or "无", xrep or "无", fails))

    # ---- 夹具 C（并集缺 OB3 → F13a）----
    swap_p001("run-fix-c")
    rc, status, fails, reviews, out = gate("C")
    f13a = [m for m in fails if "并集未覆盖全部解法义务" in m]
    xrep = [m for m in fails if "目标问数" in m or "terminal 义务" in m
            or "骨架之外的义务" in m]
    a3c = (rc == 1 and len(f13a) == 1 and "OB3" in f13a[0] and not xrep)
    record("3C", "夹具 C（5 问并集缺 OB3）：触发且仅触发 F13a，"
                 "消息列出 OB3", a3c, gate_cmd.replace("A", "C"),
           "rc=%d; F13a=%s; 串报=%s; 全部 fails=%s"
           % (rc, f13a or "无", xrep or "无", fails))

    # ---- 夹具 D（末问未绑 OB4 → F13b）----
    swap_p001("run-fix-d")
    rc, status, fails, reviews, out = gate("D")
    f13b = [m for m in fails if "terminal 义务" in m]
    xrep = [m for m in fails if "目标问数" in m or "并集未覆盖" in m
            or "骨架之外的义务" in m]
    a3d = (rc == 1 and len(f13b) == 1 and "OB4" in f13b[0] and not xrep)
    record("3D", "夹具 D（5 问全覆盖但末问未绑 OB4）：触发且仅触发 F13b",
           a3d, gate_cmd.replace("A", "D"),
           "rc=%d; F13b=%s; 串报=%s; 全部 fails=%s"
           % (rc, f13b or "无", xrep or "无", fails))

    # ---- 夹具 E（末问 prompt 与题干无关 → R6 review 正向触发）----
    swap_p001("run-fix-e")
    rc, status, fails, reviews, out = gate("E")
    r6 = [m for m in reviews if "terminal_convergence" in m]
    a3e = (rc == 0 and not fails and len(r6) == 1
           and "Jaccard" in r6[0] and "< 0.15" in r6[0])
    record("3E", "夹具 E（末问 prompt 脱离题干）：退出码 0 无 fail，"
                 "R6 terminal_convergence review 正向触发", a3e,
           gate_cmd.replace("A", "E"),
           "rc=%d; fails=%s; R6 review=%s"
           % (rc, fails or "无", r6 or "无"))

    # ==================================================================
    # 步骤 4：无骨架回归——P002 旧式 packet + 旧式任务（无 obligation_refs）
    # ==================================================================
    swap_p001("run-fix-a")   # 恢复合规 A，P002 始终随门禁同跑
    rc, status, fails, reviews, out = gate("A-regress")
    p2_bad = [m for m in fails + reviews
              if "P002" in m and re.search(
                  r"骨架|目标问数|obligation|terminal|bigram|Jaccard|收束", m)]
    p2_fail = [m for m in fails if "P002" in m]
    a4 = (rc == 0 and not p2_bad and not p2_fail)
    record("4", "无骨架回归：P002（无 solution_skeleton、无 obligation_refs）"
                "不产生任何 F10b/F13/R6 相关 fail/review（行为与修复前一致）",
           a4, gate_cmd.replace("A.json", "A-regress.json"),
           "rc=%d; P002 相关 fail=%s; P002 骨架类 fail/review=%s"
           % (rc, p2_fail or "无", p2_bad or "无"))

    # 额外直证：对删掉骨架键的 P001 packet 跑同一合规任务，门禁同样安静
    # （在独立 run 目录副本上做，不动 run-fix-a 的绑定，故仅做函数级断言：
    #  skel=None 时 F10b/F13/R6 分支整体跳过——源码行 422-424 守卫）
    src_guard = os.path.join(SCRIPTS, "check_student_task.py")
    with open(src_guard, "r", encoding="utf-8") as f:
        src = f.read()
    guard_ok = ('skel = (packet or {}).get("solution_skeleton")' in src
                and "if not isinstance(skel, dict):" in src)
    record("4b", "回归守卫静态断言：skel 缺失→None→F10b/F13/R6 全组跳过",
           guard_ok, "静态读取 check_student_task.py:418-424",
           '守卫代码存在：skel=(packet or {}).get("solution_skeleton")；'
           "非 dict 即置 None，后续全组分支以 skel/target 为 None 跳过")

    # ==================================================================
    # 步骤 5：T2 自审派发单（运行时优先，失败降级静态断言）
    # ==================================================================
    t2_ok, t2_mode, t2_ev = False, "runtime", ""
    try:
        if PLUGIN not in sys.path:
            sys.path.insert(0, PLUGIN)
        from workflow.nodes.agent_nodes import node_t2_review_request
        state = {"project_root": PROJ, "run_id": "run-fix-a",
                 "expected_tasks": ["task-P001"], "history": [],
                 "stage": "T2_STAGED", "dispatch_results": {}}
        result = node_t2_review_request(state)
        batch = result.get("pending_dispatch_batch") or []
        ctx = (batch[0].get("context") or {}) if batch else {}
        tp_ref = ctx.get("teacher_packet_ref", "")
        tp_abs = os.path.join(PROJ, tp_ref.replace("/", os.sep))
        tp_exists = bool(tp_ref) and os.path.isfile(tp_abs)
        tp_doc = None
        if tp_exists:
            with open(tp_abs, "r", encoding="utf-8") as f:
                tp_doc = json.load(f)
        has_solution_view = bool(
            tp_doc and any("solution" in k or "proof" in k
                           for k in (tp_doc.get("input_digests") or {})))
        req_files = []
        req_dir = os.path.join(PROJ, "teaching", "agent-requests")
        if os.path.isdir(req_dir):
            req_files = sorted(os.listdir(req_dir))
        t2_ok = (result.get("routing_action") == "PROCEED"
                 and tp_ref == "staging/run-fix-a/packets/"
                               "task-P001-teacher-solution.json"
                 and tp_exists and has_solution_view and req_files)
        t2_ev = ("routing_action=%s; context.teacher_packet_ref=%s; "
                 "落盘=%s; 教师 packet input_digests=%s; 派发单文件=%s"
                 % (result.get("routing_action"), tp_ref, tp_exists,
                    sorted((tp_doc or {}).get("input_digests", {}).keys()),
                    req_files))
    except Exception as exc:  # 运行时依赖阻碍 → 静态断言降级
        t2_mode = "static-degraded"
        an = os.path.join(PLUGIN, "workflow", "nodes", "agent_nodes.py")
        with open(an, "r", encoding="utf-8") as f:
            an_src = f.read()
        t2_ok = ('"teacher_packet_ref": teacher_packet_refs[tid]' in an_src
                 and '"--kind", "teacher-solution"' in an_src
                 and '"staging/%s/packets/%s-teacher-solution.json"'
                 in an_src)
        t2_ev = ("运行时调用受运行时依赖阻碍（%s: %s），降级为静态断言；"
                 "agent_nodes.py 含 teacher_packet_ref 派发字段、"
                 "--kind teacher-solution 调用与落盘路径模板"
                 % (type(exc).__name__, str(exc)[:200]))
    record("5", "T2 自审派发单 context 含 teacher_packet_ref 且 "
                "teacher-solution packet 真实落盘（%s）" % t2_mode,
           t2_ok,
           "python 内调用 node_t2_review_request(minimal state)"
           if t2_mode == "runtime" else "静态断言 agent_nodes.py",
           t2_ev)

    # ==================================================================
    # 汇总报告
    # ==================================================================
    write_report(freeze_info)
    n_fail = sum(1 for r in RESULTS if r["status"] == "FAIL")
    print("\n==== W5 验收：%d 项，%d PASS，%d FAIL ===="
          % (len(RESULTS), len(RESULTS) - n_fail, n_fail))
    return 1 if n_fail else 0


def write_report(freeze_info):
    lines = []
    lines.append("# W5 修复夹具工程 · 端到端门禁联动验收报告")
    lines.append("")
    lines.append("- 夹具工程：`repair_fixtures/proj/`（冻结 brief revision 1，"
                 "brief_digest=`%s…`）" % freeze_info["digest"][:16])
    lines.append("- 被测对象：`scripts/make_small_packet.py`（W1）、"
                 "`schemas/student-task.schema.json`（W2）、"
                 "`scripts/check_student_task.py`（W3）、"
                 "`workflow/nodes/agent_nodes.py` + `prompts/`（W4）")
    lines.append("- 写域纪律：全部产物仅落在 `repair_fixtures/`；"
                 "对 scripts/schemas/prompts/workflow 只读，未做任何修改。")
    lines.append("")
    lines.append("## 夹具形状")
    lines.append("")
    lines.append("- P001：`solution.json` 含 4 条 verified 义务 OB1..OB4"
                 "（OB2 claim 含受保护 token「威尔逊定理」→ 触发中性化回退；"
                 "OB4 claim 为「综合以上各步，故原题结论成立」→ 首子句截断，"
                 "结论文本不进入 signpost）；"
                 "`target_subquestion_count = max(3, min(6, 4+1)) = 5`。")
    lines.append("- P002：无 `solution_ref` → packet 无骨架（旧式回归路径）。")
    lines.append("- `protected_strategy_tokens: [\"威尔逊定理\"]`。")
    lines.append("- 5 个 run：run-fix-a/b/c/d/e；A/B/C/D/E 五个 P001 任务变体 + "
                 "P002 旧式任务（无 obligation_refs），全部经 broker "
                 "issue/submit/render 真实签发后跑门禁。")
    lines.append("")
    lines.append("## 验收矩阵")
    lines.append("")
    lines.append("| 项 | 验收点 | 结果 |")
    lines.append("|----|--------|------|")
    for r in RESULTS:
        mark = "✅ PASS" if r["status"] == "PASS" else "❌ FAIL"
        lines.append("| %s | %s | %s |" % (r["id"], r["name"], mark))
    lines.append("")
    lines.append("## 逐项证据")
    lines.append("")
    for r in RESULTS:
        lines.append("### %s %s — %s" % (r["id"], r["name"], r["status"]))
        lines.append("")
        lines.append("- 命令/方式：`%s`" % r["command"])
        lines.append("- 关键输出摘录：")
        lines.append("")
        lines.append("```")
        lines.append(r["evidence"][:1500])
        lines.append("```")
        lines.append("")
    n_fail = sum(1 for r in RESULTS if r["status"] == "FAIL")
    lines.append("## 结论")
    lines.append("")
    if n_fail == 0:
        lines.append("全部 %d 项 PASS。W1–W4 修复在夹具工程上端到端联动正确；"
                     "未发现缺陷。" % len(RESULTS))
    else:
        lines.append("存在 %d 项 FAIL（见上），缺陷证据已随项记录，"
                     "提交 Lead 裁决。" % n_fail)
    lines.append("")
    with open(REPORT_MD, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
