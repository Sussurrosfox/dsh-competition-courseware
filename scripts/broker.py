#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""broker.py — 可信执行边界（T1—T4 门禁链的唯一签发与提升入口）。

审计整改（question_it_aud.md 阶段 1）说明：真正的物理边界必须由主 agent
无法修改的 broker/CI/Hook/ACL 执行。本脚本实现该边界的**最小可机器判定
内核**；在同一 Windows 身份下它仍是“提高绕过成本”而非物理阻断，部署时
须以 ACL/容器/CI 限制主 agent 对 scripts/、schemas/、staging 之外的
teaching/generated/、teaching/content/、teaching/materials.json 与
release/ 的写权限（见 references/upgrade-2.4.0.md 的边界声明）。

操作（六条，全部经本脚本）：

  create_run      从冻结 brief 建立本轮任务清单（expected tasks）。
  issue_packet    读取允许的 L0 源，生成 packet、输入摘要与签名 receipt。
  submit_task     验证 staging 中提交的 student-task.json（schema、字段
                  白名单、来源摘要、packet/receipt 绑定）。
  render_task     只接受已验证 JSON，用固定版本渲染器输出并记录 digest。
  run_gates       在干净代码副本上运行全部必经检查并汇总。
  promote         仅在全部门禁通过后，把 staging 产物原子提升到规范路径。

目录约定（项目相对）：
  staging/<run_id>/run.json                 本轮状态与任务清单
  staging/<run_id>/packets/<task_id>.json   broker 签发的 packet
  staging/<run_id>/receipts/<task_id>.json  签名 receipt
  staging/<run_id>/tasks/<task_id>/         子 agent 提交区（唯一可写区）
  staging/<run_id>/rendered/<task_id>.tex   渲染产物
  staging/<run_id>/gates/                   门禁代码副本、报告与汇总
  staging/<run_id>/promotion.json           提升记录

主 agent 的允许写范围只包括 staging/<run_id>/tasks/<task_id>/ 与返修请求。

退出码：0=成功；1=拒绝/失败；2=用法或输入错误。
"""
import argparse
import datetime
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import uuid

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCHEMAS_DIR = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "schemas"))
if _SCHEMAS_DIR not in sys.path:
    sys.path.insert(0, _SCHEMAS_DIR)
sys.path.insert(0, _SCRIPT_DIR)
import contract  # noqa: E402
import make_small_packet as msp  # noqa: E402
import render_student_task as rst  # noqa: E402

TOOL_VERSION = "2.5.0"
BRIEF_REL = "research/brief.json"
FREEZE_REL = "research/freeze.json"
USER_SPEC_REL = "teaching/user-spec.json"
STAGING_REL = "staging"
CANONICAL_TASK_REL = "teaching/generated/%s-student-task.json"
CANONICAL_TEX_REL = "teaching/content/task-%s.tex"
CANONICAL_SOLUTION_TASK_REL = "teaching/generated/%s-solution-task.json"

# G5.1/G5.2：run_id 与 task_id 统一安全 pattern（与 contract.SAFE_ID_PATTERN
# 一致）。所有接受 --run-id / --task-id 的子命令入口一律先过该校验，
# 再做 resolve 后的 commonpath 根包含检查，双保险防路径穿越。
_RUN_ID_RE = re.compile(contract.SAFE_ID_PATTERN)
TASK_ID_PREFIX = "task-"
# promote 两阶段切换的临时目录名（位于 teaching/ 下，与目标同卷）
PROMOTE_TMP_FMT = ".promote-%s.tmp"


def _check_run_id(run_id):
    """统一 run_id 校验；合法返回 None，非法返回错误消息（fail-closed）。"""
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        return "run_id=%r 非法（须匹配 %s）" % (run_id,
                                                contract.SAFE_ID_PATTERN)
    return None


def _check_task_id(task_id):
    """统一 task_id 校验（task-<qid> 形态）；合法返回 None。"""
    if not isinstance(task_id, str) or not task_id.startswith(TASK_ID_PREFIX):
        return "task_id=%r 非法（须为 task-<qid> 形态）" % (task_id,)
    qid = task_id[len(TASK_ID_PREFIX):]
    if not contract.validate_qid(qid):
        return "task_id=%r 非法：qid 部分须匹配 %s" % (
            task_id, contract.SAFE_ID_PATTERN)
    return None

# run_gates 必经检查清单：(名称, 脚本名, 报告项目相对路径, 是否需 PDF 构建)
# check_agent_audit 置首位：零信任写入者身份是所有教学结论的前置硬卡点，
# 缺代理证据（ok_unattested / 伪造台账 / 摘要不符）直接记 overall=fail。
GATE_CHECKS = (
    ("check_agent_audit", "check_agent_audit.py",
     "teaching/checks/check_agent_audit.json", False),
    ("check_research", "check_research.py",
     "research/checks/check_research.json", False),
    ("check_architect", "check_architect.py",
     "teaching/checks/check_architect.json", False),
    ("check_teaching", "check_teaching.py",
     "teaching/checks/check_teaching.json", False),
    ("check_student_task", "check_student_task.py",
     "teaching/checks/check_student_task.json", False),
    ("check_student_visibility", "check_student_visibility.py",
     "teaching/checks/check_student_visibility.json", False),
    ("check_numbering", "check_numbering.py",
     "teaching/checks/check_numbering.json", False),
    ("check_subquestion_solution", "check_subquestion_solution.py",
     "teaching/checks/check_subquestion_solution.json", False),
)


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _load_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def _run_paths(root, run_id):
    try:
        base = contract.resolve_path(root, "%s/%s" % (STAGING_REL, run_id))
    except ValueError:
        # Windows 跨盘等 commonpath 异常：fail-closed
        return None
    if base is None:
        return None
    return {
        "base": base,
        "run": os.path.join(base, "run.json"),
        "packets": os.path.join(base, "packets"),
        "receipts": os.path.join(base, "receipts"),
        "tasks": os.path.join(base, "tasks"),
        "rendered": os.path.join(base, "rendered"),
        "gates": os.path.join(base, "gates"),
        "promotion": os.path.join(base, "promotion.json"),
        "solution_packets": os.path.join(base, "solution-packets"),
        "solution_receipts": os.path.join(base, "solution-receipts"),
    }


# ---------------------------------------------------------------------------
# run.json 完整性保护（G5.2）：写入附带 run_hmac，读取一律验签
# ---------------------------------------------------------------------------

def _run_record_hmac(run):
    """HMAC-SHA256(SKILLS_BROKER_KEY, canonical_json(不含 run_hmac 的 run_data))。

    口径唯一来源是 contract.run_record_hmac（写验两侧逐字节一致）。"""
    return contract.run_record_hmac(run)


def _write_run(paths, run):
    """写 run.json：每次写入前重算并附带 run_hmac。"""
    run["run_hmac"] = _run_record_hmac(run)
    _write_json(paths["run"], run)


def _verify_run_record(run):
    """验签 run.json；通过返回 None，否则返回错误消息（fail-closed）。"""
    sig = run.get("run_hmac")
    if not isinstance(sig, str) or not sig:
        return ("run.json 缺少 run_hmac 签名：存量无签名记录视为不可信，"
                "拒绝继续使用；请备份后删除对应 staging/<run_id> 目录并由 "
                "broker create_run 重建 run")
    if not hmac.compare_digest(_run_record_hmac(run), sig):
        return ("run.json 验签失败（记录被篡改），拒绝继续；"
                "请由 broker create_run 重建 run")
    return None


def _load_run(root, run_id):
    rerr = _check_run_id(run_id)
    if rerr:
        return None, None, rerr
    paths = _run_paths(root, run_id)
    if paths is None:
        return None, None, "run_id 路径越出项目根: %r" % run_id
    if not os.path.isfile(paths["run"]):
        return None, paths, "找不到 run 记录：staging/%s/run.json" % run_id
    try:
        run = _load_json(paths["run"])
    except (OSError, json.JSONDecodeError) as exc:
        return None, paths, "run.json 不是合法 JSON: %s" % exc
    if not isinstance(run, dict):
        return None, paths, "run.json 不是对象（不可信），请重建 run"
    verr = _verify_run_record(run)
    if verr:
        return None, paths, verr
    return run, paths, None


def _require_confirmed_user_spec(root):
    """lesson_mode 必须与用户确认摘要（user-spec.json）绑定。"""
    abs_path = contract.resolve_path(root, USER_SPEC_REL)
    if abs_path is None or not os.path.isfile(abs_path):
        return None, "缺少 %s（PHASE 0 BREAKPOINT 用户对齐凭证）" % USER_SPEC_REL
    try:
        spec = _load_json(abs_path)
    except (OSError, json.JSONDecodeError) as exc:
        return None, "%s 不是合法 JSON: %s" % (USER_SPEC_REL, exc)
    if not spec.get("confirmed"):
        return None, "%s 中 confirmed 不为 true：用户对齐尚未完成" % USER_SPEC_REL
    if not spec.get("lesson_mode"):
        return None, "%s 缺少 lesson_mode" % USER_SPEC_REL
    return spec, None


# ---------------------------------------------------------------------------
# create_run
# ---------------------------------------------------------------------------

def cmd_create_run(root, args):
    brief_abs = contract.resolve_path(root, BRIEF_REL)
    if brief_abs is None or not os.path.isfile(brief_abs):
        print("[broker] 缺少有效教研交接包：%s" % BRIEF_REL)
        return 2
    brief = _load_json(brief_abs)
    berrs = contract.validate(brief, "brief")
    if berrs:
        print("[broker] brief.json 未通过 schema 校验: %s" % berrs[0]["message"])
        return 2

    freeze_abs = contract.resolve_path(root, FREEZE_REL)
    if freeze_abs is None or not os.path.isfile(freeze_abs):
        print("[broker] 缺少有效教研冻结：%s（create_run 只从冻结 brief "
              "建立任务清单）" % FREEZE_REL)
        return 2
    freeze = _load_json(freeze_abs)
    review_refs = brief.get("freeze_basis", {}).get("research_review_refs", [])
    info = contract.compute_brief_digest(root, brief, exclude_refs=review_refs)
    if info["digest"] != freeze.get("brief_digest"):
        print("[broker] 拒绝建立 run：重算 brief_digest=%s 与 freeze 记录 %s "
              "不一致（研究包过期，禁止沿用旧任务清单）"
              % (info["digest"], freeze.get("brief_digest")))
        return 1
    if args.brief_digest and args.brief_digest != info["digest"]:
        print("[broker] 拒绝建立 run：--brief-digest 与冻结记录不一致")
        return 1

    spec, serr = _require_confirmed_user_spec(root)
    if serr:
        print("[broker] %s" % serr)
        return 2
    if spec.get("lesson_mode") != "guided":
        print("[broker] user-spec.lesson_mode=%r 非法（唯一取值 guided）"
              % spec.get("lesson_mode"))
        return 2

    run_id = args.run_id or ("run-%s-%s" % (
        datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S"),
        uuid.uuid4().hex[:8]))
    rerr = _check_run_id(run_id)
    if rerr:
        print("[broker] %s" % rerr)
        return 2
    paths = _run_paths(root, run_id)
    if paths is None:
        print("[broker] run_id 路径越出项目根")
        return 2
    if os.path.isfile(paths["run"]):
        print("[broker] run %s 已存在（拒绝覆盖既有 run）" % run_id)
        return 2

    # 任务清单：冻结 brief 的每个入选题恰好一个 task-<qid>
    tasks = {}
    for qid in brief["selection"]["selected_problem_ids"]:
        tid = "task-%s" % qid
        tasks[tid] = {
            "question_id": qid,
            "state": "pending",
            "packet_digest": None,
            "receipt_ref": None,
            "output_digest": None,
            "render_digest": None,
            "solution_state": "pending",
            "solution_packet_digest": None,
            "solution_receipt_ref": None,
            "solution_worker_session_id": None,
            "solution_output_digest": None,
        }
    run = {
        "run_id": run_id,
        "contract_version": contract.CONTRACT_VERSION,
        "broker_version": TOOL_VERSION,
        "brief_id": brief["brief_id"],
        "brief_revision": brief["revision"],
        "brief_digest": info["digest"],
        "lesson_mode": spec["lesson_mode"],
        "user_spec_digest": contract.sha256_file(
            contract.resolve_path(root, USER_SPEC_REL)),
        "created_at": _now(),
        "tasks": tasks,
    }
    _write_run(paths, run)
    os.makedirs(paths["packets"], exist_ok=True)
    os.makedirs(paths["receipts"], exist_ok=True)
    os.makedirs(paths["tasks"], exist_ok=True)
    os.makedirs(paths["rendered"], exist_ok=True)
    os.makedirs(paths["solution_packets"], exist_ok=True)
    os.makedirs(paths["solution_receipts"], exist_ok=True)
    print("[broker] run %s 已建立：%d 个任务（来自冻结 brief %s revision %s，"
          "lesson_mode=%s）" % (run_id, len(tasks), brief["brief_id"],
                               brief["revision"], spec["lesson_mode"]))
    for tid in sorted(tasks):
        print("  - %s（题 %s）" % (tid, tasks[tid]["question_id"]))
    return 0


def re_fullmatch_run_id(run_id):
    """已废弃：保留仅为向后兼容，统一校验请用 _check_run_id。"""
    return bool(re.fullmatch(r"run-[A-Za-z0-9_-]+", run_id or ""))


# ---------------------------------------------------------------------------
# issue_packet
# ---------------------------------------------------------------------------

def cmd_issue_packet(root, args):
    run, paths, err = _load_run(root, args.run_id)
    if err:
        print("[broker] %s" % err)
        return 2
    terr = _check_task_id(args.task_id)
    if terr:
        print("[broker] %s" % terr)
        return 2
    entry = run["tasks"].get(args.task_id)
    if entry is None:
        print("[broker] 任务 %s 不在本轮任务清单中" % args.task_id)
        return 2
    if entry["state"] not in ("pending", "issued"):
        print("[broker] 任务 %s 状态为 %r，不能重新签发 packet"
              % (args.task_id, entry["state"]))
        return 2

    out_rel = msp.default_packet_path(root, args.run_id, args.task_id,
                                      args.kind)
    rc = msp.main(["--project", root, "--run-id", args.run_id,
                   "--task-id", args.task_id, "--kind", args.kind,
                   "--out", out_rel])
    if rc != 0:
        return rc
    packet = _load_json(contract.resolve_path(root, out_rel))
    if contract.packet_body_digest(packet) != packet.get("packet_digest"):
        print("[broker] packet 摘要自校验失败（生成器缺陷）")
        return 2

    worker_session_id = "ws-%s-%s" % (args.run_id, uuid.uuid4().hex[:12])
    receipt_rel = "%s/%s/receipts/%s.json" % (STAGING_REL, args.run_id,
                                              args.task_id)
    receipt = {
        "receipt_id": "rcpt-%s-%s" % (args.run_id, args.task_id),
        "run_id": args.run_id,
        "task_id": args.task_id,
        "worker_session_id": worker_session_id,
        "packet_digest": packet["packet_digest"],
        "packet_ref": out_rel,
        "input_digests": packet.get("input_digests", {}),
        "allowed_source_refs": packet.get("allowed_source_refs", []),
        "contract_version": contract.CONTRACT_VERSION,
        "broker_version": TOOL_VERSION,
        "issued_at": _now(),
    }
    receipt["signature"] = contract.sign_payload(receipt)
    _write_json(contract.resolve_path(root, receipt_rel), receipt)

    entry["state"] = "issued"
    entry["packet_digest"] = packet["packet_digest"]
    entry["receipt_ref"] = receipt_rel
    entry["worker_session_id"] = worker_session_id
    _write_run(paths, run)
    print("[broker] packet 已签发：%s" % out_rel)
    print("[broker] receipt 已签发（签名）：%s" % receipt_rel)
    print("[broker] worker_session_id=%s（子 agent 必须写回 "
          "execution_metadata.worker_session_id）" % worker_session_id)
    return 0


# ---------------------------------------------------------------------------
# submit_task
# ---------------------------------------------------------------------------

def cmd_submit_task(root, args):
    run, paths, err = _load_run(root, args.run_id)
    if err:
        print("[broker] %s" % err)
        return 2
    terr = _check_task_id(args.task_id)
    if terr:
        print("[broker] %s" % terr)
        return 2
    entry = run["tasks"].get(args.task_id)
    if entry is None:
        print("[broker] 任务 %s 不在本轮任务清单中" % args.task_id)
        return 2
    if entry["state"] == "pending":
        print("[broker] 任务 %s 尚未签发 packet（state=%r）；submit 前必须"
              "先 issue_packet" % (args.task_id, entry["state"]))
        return 2
    if entry["state"] not in ("issued", "verified"):
        print("[broker] 任务 %s 状态为 %r，不能提交"
              % (args.task_id, entry["state"]))
        return 2

    # 提交路径必须落在 staging/<run_id>/tasks/<task_id>/ 内
    in_abs = os.path.abspath(os.path.join(root, args.input)) if not os.path.isabs(args.input) else os.path.abspath(args.input)
    task_dir = os.path.join(paths["tasks"], args.task_id)
    if os.path.commonpath([os.path.abspath(task_dir), in_abs]) \
            != os.path.abspath(task_dir):
        print("[broker] 拒绝提交：输入必须位于 staging/%s/tasks/%s/ 内: %s"
              % (args.run_id, args.task_id, args.input))
        return 2
    if not os.path.isfile(in_abs):
        print("[broker] 提交文件不存在: %s" % args.input)
        return 2

    # 返修循环：已 verified 的任务允许重新提交（内容未变时是幂等重放，
    # 内容有变时重新走一遍完整验证：schema/白名单/packet/receipt 绑定都会重跑）。
    # 真正的“无变更重试”防护在图上层（workflow/nodes/agent_nodes.py）：
    # 派发后产物没有变化就中止，不会反复重跑门禁。
    if entry["state"] == "verified":
        cur_digest = contract.sha256_file(in_abs)
        if cur_digest == entry.get("output_digest"):
            print("[broker] 任务 %s 重新提交（内容与已验证版本一致，幂等重放）"
                  % args.task_id)
        else:
            print("[broker] 任务 %s 重新提交更新后的产物（output_digest %s… -> %s…）"
                  % (args.task_id, (entry.get("output_digest") or "")[:16],
                     cur_digest[:16]))
    try:
        task = _load_json(in_abs)
    except (OSError, json.JSONDecodeError) as exc:
        print("[broker] 提交不是合法 JSON: %s" % exc)
        return 1

    packet_abs = contract.resolve_path(
        root, "%s/%s/packets/%s.json" % (STAGING_REL, args.run_id,
                                         args.task_id))
    packet = _load_json(packet_abs) if packet_abs and os.path.isfile(
        packet_abs) else None
    if packet is None:
        print("[broker] 找不到已签发 packet，拒绝提交")
        return 2
    if contract.packet_body_digest(packet) != packet.get("packet_digest"):
        print("[broker] packet 文件摘要与签发记录不一致（packet 被篡改），"
              "拒绝提交")
        return 1

    verdict = contract.preflight_student_task(
        task, project_root=root, packet=packet, run=run,
        task_id=args.task_id)
    submission = {
        "run_id": args.run_id,
        "task_id": args.task_id,
        "submitted_at": _now(),
        "verified": verdict["ok"],
        "reasons": [] if verdict["ok"] else [verdict["hint"]],
        "packet_digest": packet["packet_digest"],
    }
    if verdict["ok"]:
        submission["output_digest"] = contract.sha256_file(in_abs)
        # worker_session_id 必须与 receipt 一致
        wsid = (task.get("execution_metadata", {}) or {}).get(
            "worker_session_id", "")
        if wsid != entry.get("worker_session_id"):
            submission["verified"] = False
            submission["reasons"].append(
                "execution_metadata.worker_session_id=%r 与 broker 签发的 "
                "%r 不一致" % (wsid, entry.get("worker_session_id")))
        if not submission["verified"]:
            _write_json(os.path.join(task_dir, "submission.json"), submission)
            print("[broker] 提交被拒绝：")
            for r in submission["reasons"]:
                print("  - %s" % r)
            return 1
        entry["state"] = "verified"
        entry["output_digest"] = submission["output_digest"]
        _write_run(paths, run)
        _write_json(os.path.join(task_dir, "submission.json"), submission)
        print("[broker] 提交已验证并接受：%s（output_digest=%s）"
              % (args.task_id, submission["output_digest"][:16]))
        return 0

    _write_json(os.path.join(task_dir, "submission.json"), submission)
    print("[broker] 提交被拒绝（进入返修队列，不得写入规范目录）：")
    for r in submission["reasons"]:
        print("  - %s" % r)
    return 1


# ---------------------------------------------------------------------------
# render_task
# ---------------------------------------------------------------------------

def cmd_render_task(root, args):
    run, paths, err = _load_run(root, args.run_id)
    if err:
        print("[broker] %s" % err)
        return 2
    terr = _check_task_id(args.task_id)
    if terr:
        print("[broker] %s" % terr)
        return 2
    entry = run["tasks"].get(args.task_id)
    if entry is None:
        print("[broker] 任务 %s 不在本轮任务清单中" % args.task_id)
        return 2
    if entry["state"] != "verified":
        print("[broker] 任务 %s 未通过 submit_task 验证（state=%r），拒绝渲染"
              % (args.task_id, entry["state"]))
        return 2

    in_abs = os.path.join(paths["tasks"], args.task_id, "student-task.json")
    if not os.path.isfile(in_abs):
        print("[broker] 找不到已验证提交: %s" % in_abs)
        return 2
    task = _load_json(in_abs)
    if contract.sha256_file(in_abs) != entry.get("output_digest"):
        print("[broker] 提交文件摘要与验证记录不一致（提交后被改动），拒绝渲染")
        return 1

    out_rel = "%s/%s/rendered/%s.tex" % (STAGING_REL, args.run_id,
                                         args.task_id)
    out_abs = contract.resolve_path(root, out_rel)
    try:
        tex = rst.render_student_task(task)
    except Exception as exc:  # 渲染器缺陷按拒绝处理
        print("[broker] 渲染失败: %s" % exc)
        return 1
    os.makedirs(os.path.dirname(out_abs), exist_ok=True)
    with open(out_abs, "w", encoding="utf-8", newline="\n") as f:
        f.write(tex)
        f.write("\n")
    entry["render_digest"] = contract.sha256_file(out_abs)
    entry["render_ref"] = out_rel
    entry["renderer_version"] = rst.RENDERER_VERSION
    _write_run(paths, run)
    print("[broker] 渲染完成：%s（renderer=%s，digest=%s）"
          % (out_rel, rst.RENDERER_VERSION, entry["render_digest"][:16]))
    return 0


# ---------------------------------------------------------------------------
# T2.5: issue_solution_packets / submit_solution_task / migrate_t25
# ---------------------------------------------------------------------------

def cmd_issue_solution_packets(root, args):
    """T2.5: 为已验证 student-task 的任务签发 solution-packet 与签名 receipt。"""
    run, paths, err = _load_run(root, args.run_id)
    if err:
        print("[broker] %s" % err)
        return 2

    target_tasks = [args.task_id] if getattr(args, "task_id", None) else sorted(run.get("tasks", {}).keys())
    os.makedirs(paths["solution_packets"], exist_ok=True)
    os.makedirs(paths["solution_receipts"], exist_ok=True)

    issued_count = 0
    for tid in target_tasks:
        entry = run["tasks"].get(tid)
        if not entry:
            print("[broker] 任务 %s 不在本轮任务清单中" % tid)
            return 2
        qid = entry["question_id"]

        # 读取已提交并验证通过的 student-task.json
        st_file = os.path.join(paths["tasks"], tid, "student-task.json")
        if not os.path.isfile(st_file):
            st_file = contract.resolve_path(root, CANONICAL_TASK_REL % qid)
        if not st_file or not os.path.isfile(st_file):
            print("[broker] 任务 %s 尚未产出并通过 student-task 验证，无法签发解答包" % tid)
            return 2

        try:
            st_doc = _load_json(st_file)
        except Exception as exc:
            print("[broker] 读取 %s 失败: %s" % (st_file, exc))
            return 2

        subqs = st_doc.get("subquestions", [])
        stem = contract.strip_numbering_and_source(st_doc.get("stem", ""))

        # 读取 R2 全局解答底稿
        sol_md_rel = "research/problems/%s/solution.md" % qid
        sol_md_abs = contract.resolve_path(root, sol_md_rel)
        sol_content = ""
        if sol_md_abs and os.path.isfile(sol_md_abs):
            try:
                with open(sol_md_abs, "r", encoding="utf-8-sig", errors="replace") as f:
                    sol_content = f.read().strip()
            except Exception:
                pass
        if not sol_content:
            cand = contract.resolve_path(root, "teaching/content/blk-%s-proof.tex" % qid)
            if cand and os.path.isfile(cand):
                try:
                    with open(cand, "r", encoding="utf-8-sig", errors="replace") as f:
                        sol_content = f.read().strip()
                except Exception:
                    pass

        # 构造并签署 solution packet
        packet_rel = "%s/%s/solution-packets/%s.json" % (STAGING_REL, args.run_id, tid)
        packet_abs = contract.resolve_path(root, packet_rel)

        packet_body = {
            "schema_version": contract.CONTRACT_VERSION,
            "run_id": args.run_id,
            "task_id": tid,
            "question_id": qid,
            "problem_stem": stem,
            "subquestions": [
                {
                    "subquestion_id": sq.get("subquestion_id"),
                    "prompt": sq.get("prompt"),
                }
                for sq in subqs
            ],
            "global_research_solution_ref": sol_md_rel,
            "global_research_solution_content": sol_content,
            "issued_at": _now(),
        }
        pkt_digest = hashlib.sha256(contract.canonical_json(packet_body).encode("utf-8")).hexdigest()
        packet_doc = dict(packet_body)
        packet_doc["packet_digest"] = pkt_digest
        _write_json(packet_abs, packet_doc)

        worker_session_id = "ws-sol-%s-%s" % (args.run_id, uuid.uuid4().hex[:12])
        receipt_rel = "%s/%s/solution-receipts/%s.json" % (STAGING_REL, args.run_id, tid)
        receipt_abs = contract.resolve_path(root, receipt_rel)
        receipt = {
            "receipt_id": "rcpt-sol-%s-%s" % (args.run_id, tid),
            "run_id": args.run_id,
            "task_id": tid,
            "worker_session_id": worker_session_id,
            "packet_digest": pkt_digest,
            "packet_ref": packet_rel,
            "contract_version": contract.CONTRACT_VERSION,
            "broker_version": TOOL_VERSION,
            "issued_at": _now(),
        }
        receipt["signature"] = contract.sign_payload(receipt)
        _write_json(receipt_abs, receipt)

        entry["solution_state"] = "issued"
        entry["solution_packet_digest"] = pkt_digest
        entry["solution_receipt_ref"] = receipt_rel
        entry["solution_worker_session_id"] = worker_session_id
        issued_count += 1
        print("[broker] solution-packet 已签发：%s（含 %d 个小问）" % (packet_rel, len(subqs)))

    _write_run(paths, run)
    print("[broker] 共完成 %d 个任务的 solution-packet 签发" % issued_count)
    return 0


def cmd_submit_solution_task(root, args):
    """T2.5: 验证并接受 staging 中的 solution-task.json。"""
    run, paths, err = _load_run(root, args.run_id)
    if err:
        print("[broker] %s" % err)
        return 2
    terr = _check_task_id(args.task_id)
    if terr:
        print("[broker] %s" % terr)
        return 2
    entry = run.get("tasks", {}).get(args.task_id)
    if entry is None:
        print("[broker] 任务 %s 不在本轮任务清单中" % args.task_id)
        return 2

    in_abs = os.path.abspath(os.path.join(root, args.input)) if not os.path.isabs(args.input) else os.path.abspath(args.input)
    task_dir = os.path.join(paths["tasks"], args.task_id)
    if os.path.commonpath([os.path.abspath(task_dir), in_abs]) != os.path.abspath(task_dir):
        print("[broker] 拒绝提交：输入必须位于 staging/%s/tasks/%s/ 内: %s"
              % (args.run_id, args.task_id, args.input))
        return 2
    if not os.path.isfile(in_abs):
        print("[broker] 提交文件不存在: %s" % args.input)
        return 2

    try:
        sol_doc = _load_json(in_abs)
    except (OSError, json.JSONDecodeError) as exc:
        print("[broker] 提交不是合法 JSON: %s" % exc)
        return 1

    packet_abs = contract.resolve_path(
        root, "%s/%s/solution-packets/%s.json" % (STAGING_REL, args.run_id, args.task_id))
    packet = _load_json(packet_abs) if packet_abs and os.path.isfile(packet_abs) else None
    if packet is None:
        print("[broker] 找不到已签发的 solution-packet，拒绝提交")
        return 2

    st_abs = os.path.join(paths["tasks"], args.task_id, "student-task.json")
    if not os.path.isfile(st_abs):
        st_abs = contract.resolve_path(root, CANONICAL_TASK_REL % entry["question_id"])
    st_doc = _load_json(st_abs) if st_abs and os.path.isfile(st_abs) else None

    verdict = contract.preflight_solution_task(
        sol_doc,
        project_root=root,
        packet=packet,
        run=run,
        task_id=args.task_id,
        student_task=st_doc
    )

    submission = {
        "run_id": args.run_id,
        "task_id": args.task_id,
        "submitted_at": _now(),
        "verified": verdict["ok"],
        "reasons": [] if verdict["ok"] else [verdict["hint"]],
        "packet_digest": packet.get("packet_digest"),
    }

    if verdict["ok"]:
        submission["output_digest"] = contract.sha256_file(in_abs)
        wsid = (sol_doc.get("execution_metadata", {}) or {}).get("worker_session_id", "")
        if wsid != entry.get("solution_worker_session_id"):
            submission["verified"] = False
            submission["reasons"].append(
                "execution_metadata.worker_session_id=%r 与 broker 签发的 %r 不一致"
                % (wsid, entry.get("solution_worker_session_id")))
        if not submission["verified"]:
            _write_json(os.path.join(task_dir, "solution-submission.json"), submission)
            print("[broker] solution-task 提交被拒绝：")
            for r in submission["reasons"]:
                print("  - %s" % r)
            return 1

        entry["solution_state"] = "verified"
        entry["solution_output_digest"] = submission["output_digest"]
        _write_run(paths, run)
        _write_json(os.path.join(task_dir, "solution-submission.json"), submission)
        print("[broker] solution-task 提交已验证并接受：%s（output_digest=%s）"
              % (args.task_id, submission["output_digest"][:16]))
        return 0

    _write_json(os.path.join(task_dir, "solution-submission.json"), submission)
    print("[broker] solution-task 提交被拒绝（进入返修队列）：")
    for r in submission["reasons"]:
        print("  - %s" % r)
    return 1


def cmd_migrate_t25(root, args):
    """历史 run 补开或迁移 T2.5 命令。"""
    run, paths, err = _load_run(root, args.run_id)
    if err:
        print("[broker] %s" % err)
        return 2

    updated = False
    for tid, entry in run.get("tasks", {}).items():
        if "solution_state" not in entry:
            entry["solution_state"] = "pending"
            entry["solution_packet_digest"] = None
            entry["solution_receipt_ref"] = None
            entry["solution_worker_session_id"] = None
            entry["solution_output_digest"] = None
            updated = True

    if updated:
        _write_run(paths, run)
        print("[broker] 已为 run %s 补全 T2.5 状态结构字段" % args.run_id)

    args.task_id = None
    return cmd_issue_solution_packets(root, args)


# ---------------------------------------------------------------------------
# run_gates
# ---------------------------------------------------------------------------

def _preseed_canonical(root, run, paths):
    """门禁前把已验证 staging 产物预置到规范路径（broker 独占写入）。

    返回 [(dst, backup)]；门禁失败时按此清单回滚，恢复旧文件或删除新文件，
    避免失败轮次在规范目录留下半成品。
    """
    seeded = []
    for tid in sorted(run.get("tasks", {})):
        entry = run["tasks"][tid]
        if entry.get("state") != "verified" or not entry.get("render_digest"):
            continue
        qid = entry["question_id"]
        src_task = os.path.join(paths["tasks"], tid, "student-task.json")
        src_tex = contract.resolve_path(root, entry["render_ref"])
        pairs = [
            (contract.resolve_path(root, CANONICAL_TASK_REL % qid),
             src_task),
            (contract.resolve_path(root, CANONICAL_TEX_REL % qid), src_tex),
        ]
        # 契约 2.5.0 (T2.5): 若存在逐小问解答提交，一并预置
        src_sol = os.path.join(paths["tasks"], tid, "solution-task.json")
        if os.path.isfile(src_sol):
            pairs.append((contract.resolve_path(root, CANONICAL_SOLUTION_TASK_REL % qid), src_sol))
        for dst, src in pairs:
            if dst is None or not src or not os.path.isfile(src):
                continue
            backup = None
            if os.path.isfile(dst):
                backup = dst + ".preseed-bak-%s" % uuid.uuid4().hex[:8]
                shutil.copyfile(dst, backup)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            tmp = dst + ".tmp-%s" % uuid.uuid4().hex[:8]
            shutil.copyfile(src, tmp)
            os.replace(tmp, dst)
            seeded.append((dst, backup))
    return seeded


def _rollback_canonical(seeded):
    for dst, backup in seeded:
        if backup and os.path.isfile(backup):
            try:
                os.replace(backup, dst)
            except OSError:
                pass
        elif backup is None and os.path.isfile(dst):
            try:
                os.remove(dst)
            except OSError:
                pass


def _cleanup_canonical_backups(seeded):
    for _, backup in seeded:
        if backup and os.path.isfile(backup):
            try:
                os.remove(backup)
            except OSError:
                pass


def cmd_run_gates(root, args):
    run, paths, err = _load_run(root, args.run_id)
    if err:
        print("[broker] %s" % err)
        return 2

    # 干净代码副本：scripts/ + schemas/ + workflow/ 复制到 staging/<run_id>/gates/code/
    # （workflow/ 必须随副本：check_agent_audit / check_research 的台账对账依赖
    #   workflow/nodes/agent_evidence.py，否则干净副本内 import 失败）
    code_dir = os.path.join(paths["gates"], "code")
    if os.path.isdir(code_dir):
        shutil.rmtree(code_dir)
    os.makedirs(code_dir, exist_ok=True)
    for sub in ("scripts", "schemas", "workflow"):
        src = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", sub))
        dst = os.path.join(code_dir, sub)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc"))
    digest_info = contract.code_digest([os.path.join(code_dir, "scripts"),
                                        os.path.join(code_dir, "schemas"),
                                        os.path.join(code_dir, "workflow")])
    print("[broker] 门禁代码副本摘要: %s" % digest_info["digest"][:16])

    # 门禁前预置规范路径（broker 独占写入）；失败轮次回滚
    seeded = _preseed_canonical(root, run, paths)
    if seeded:
        print("[broker] 已预置 %d 个规范文件供门禁核对（失败将回滚）"
              % len(seeded))

    # 门禁运行时重新核对 brief 冻结状态（run 建立后研究包不得静默变化）
    brief_digest_matches = False
    brief_abs = contract.resolve_path(root, BRIEF_REL)
    freeze_abs = contract.resolve_path(root, FREEZE_REL)
    if brief_abs and os.path.isfile(brief_abs) and freeze_abs \
            and os.path.isfile(freeze_abs):
        try:
            brief_now = _load_json(brief_abs)
            review_refs = brief_now.get("freeze_basis", {}).get(
                "research_review_refs", [])
            info_now = contract.compute_brief_digest(
                root, brief_now, exclude_refs=review_refs)
            freeze_now = _load_json(freeze_abs)
            brief_digest_matches = (
                info_now["digest"] == freeze_now.get("brief_digest")
                and info_now["digest"] == run.get("brief_digest"))
        except (OSError, json.JSONDecodeError, ValueError):
            brief_digest_matches = False
    if brief_digest_matches is not True:
        print("[broker] [gate] brief 冻结状态核对失败（研究包在 run 建立后"
              "发生变化），本轮门禁记为 fail")

    results = {}
    overall = "pass" if brief_digest_matches is True else "fail"

    # ---- 先构建（版面结论以实际 PDF/日志为准），再跑结构/质量检查 ----
    if not args.skip_build:
        build_script = os.path.join(code_dir, "scripts", "build.py")
        report_abs = contract.resolve_path(root, "build/build-report.json")
        cmd = [sys.executable, build_script, root]
        if report_abs:
            os.makedirs(os.path.dirname(report_abs), exist_ok=True)
            cmd += ["--json", report_abs]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        status = None
        if report_abs and os.path.isfile(report_abs):
            try:
                status = _load_json(report_abs).get("status")
            except (OSError, json.JSONDecodeError):
                status = None
        results["build"] = {"status": status, "exit_code": r.returncode,
                            "report": "build/build-report.json"}
        print("[broker] [gate] %-26s status=%-6s exit=%s"
              % ("build", status, r.returncode))
        if status != "pass":
            overall = "fail"

    for name, script, report_rel, needs_build in GATE_CHECKS:
        script_abs = os.path.join(code_dir, "scripts", script)
        report_abs = contract.resolve_path(root, report_rel)
        if report_abs:
            os.makedirs(os.path.dirname(report_abs), exist_ok=True)
        cmd = [sys.executable, script_abs, root]
        if report_abs:
            cmd += ["--json", report_abs]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        status = None
        if report_abs and os.path.isfile(report_abs):
            try:
                status = _load_json(report_abs).get("status")
            except (OSError, json.JSONDecodeError):
                status = None
        results[name] = {"status": status, "exit_code": r.returncode,
                         "report": report_rel}
        print("[broker] [gate] %-26s status=%-6s exit=%s"
              % (name, status, r.returncode))
        if status in ("fail", "error") or status is None:
            overall = "fail"
        elif status == "review":
            # review 是未完成状态：门禁汇总记 review，promote 拒绝
            if overall == "pass":
                overall = "review"

    summary = {
        "run_id": args.run_id,
        "checked_at": _now(),
        "contract_version": contract.CONTRACT_VERSION,
        "broker_version": TOOL_VERSION,
        "code_digest": digest_info["digest"],
        "code_files": digest_info["files"],
        "brief_digest_matches": brief_digest_matches,
        "results": results,
        "overall": overall,
        "skip_build": bool(args.skip_build),
    }
    # 汇总签名：防止事后篡改报告状态（mutation test 对抗项）
    summary["signature"] = contract.sign_payload(summary)
    _write_json(os.path.join(paths["gates"], "summary.json"), summary)
    if overall != "pass":
        _rollback_canonical(seeded)
        print("[broker] 门禁未全通过：已回滚本次预置的规范文件")
    else:
        _cleanup_canonical_backups(seeded)
    print("[broker] 门禁汇总: %s -> staging/%s/gates/summary.json"
          % (overall.upper(), args.run_id))
    return 0 if overall == "pass" else 1


# ---------------------------------------------------------------------------
# promote
# ---------------------------------------------------------------------------

def cmd_promote(root, args):
    run, paths, err = _load_run(root, args.run_id)
    if err:
        print("[broker] %s" % err)
        return 2
    summary_abs = os.path.join(paths["gates"], "summary.json")
    if not os.path.isfile(summary_abs):
        print("[broker] 拒绝提升：尚未运行 run_gates（无门禁汇总）")
        return 1
    summary = _load_json(summary_abs)
    # 汇总验签：篡改报告状态（把 fail 改成 pass、删除门禁调用）即拒绝
    if not contract.verify_payload(summary, summary.get("signature")):
        print("[broker] 拒绝提升：门禁汇总验签失败（报告被篡改）")
        return 1
    required_gates = {name for name, _s, _r, _b in GATE_CHECKS}
    missing_gates = required_gates - set(summary.get("results", {}))
    if missing_gates:
        print("[broker] 拒绝提升：门禁汇总缺少必经检查 %s（删除门禁调用"
              "不放过）" % "、".join(sorted(missing_gates)))
        return 1
    for name in sorted(required_gates):
        status = summary["results"][name].get("status")
        if status != "pass":
            print("[broker] 拒绝提升：门禁 %s 状态为 %r（不是 pass）"
                  % (name, status))
            return 1
    if summary.get("overall") != "pass":
        print("[broker] 拒绝提升：门禁汇总 overall=%r（存在 fail/error/缺失"
              "报告）" % summary.get("overall"))
        return 1
    if summary.get("brief_digest_matches") is not True:
        print("[broker] 拒绝提升：门禁运行时的 brief 摘要与当前不一致")
        return 1

    promoted = []
    # ---- 预校验阶段：全部任务状态与摘要核对通过后才触碰任何文件 ----
    plan = []  # [(tid, qid, dst_task_rel, dst_tex_rel, dst_task, dst_tex, src_task, src_tex)]
    for tid in sorted(run["tasks"]):
        entry = run["tasks"][tid]
        qid = entry["question_id"]
        if entry.get("state") != "verified" or not entry.get("render_digest"):
            print("[broker] 拒绝提升：任务 %s 未完成验证+渲染（state=%r）"
                  % (tid, entry.get("state")))
            return 1
        if not contract.validate_qid(qid):
            print("[broker] 拒绝提升：任务 %s 的 question_id=%r 非法" % (tid, qid))
            return 1
        src_task = os.path.join(paths["tasks"], tid, "student-task.json")
        src_tex = contract.resolve_path(root, entry["render_ref"])
        dst_task_rel = CANONICAL_TASK_REL % qid
        dst_tex_rel = CANONICAL_TEX_REL % qid
        dst_task = contract.resolve_path(root, dst_task_rel)
        dst_tex = contract.resolve_path(root, dst_tex_rel)
        if dst_task is None or dst_tex is None or src_tex is None:
            print("[broker] 拒绝提升：规范路径越出项目根")
            return 1
        if contract.sha256_file(src_task) != entry.get("output_digest"):
            print("[broker] 拒绝提升：%s 的提交摘要与验证记录不一致" % tid)
            return 1
        if contract.sha256_file(src_tex) != entry.get("render_digest"):
            print("[broker] 拒绝提升：%s 的渲染摘要与记录不一致" % tid)
            return 1

        # 契约 2.5.0 (T2.5): 若存在 verified 的逐小问解答，一并纳入提升计划
        src_sol = os.path.join(paths["tasks"], tid, "solution-task.json")
        dst_sol_rel = CANONICAL_SOLUTION_TASK_REL % qid
        dst_sol = contract.resolve_path(root, dst_sol_rel)
        has_sol = False
        if os.path.isfile(src_sol):
            if entry.get("solution_output_digest") and contract.sha256_file(src_sol) != entry.get("solution_output_digest"):
                print("[broker] 拒绝提升：%s 的 solution-task 摘要与记录不一致" % tid)
                return 1
            has_sol = True

        plan.append((tid, qid, dst_task_rel, dst_tex_rel, dst_sol_rel,
                     dst_task, dst_tex, dst_sol, src_task, src_tex, src_sol, has_sol))

    # ---- 第一阶段：全部源复制到 teaching/.promote-<run_id>.tmp/ ----
    # 复制阶段任何失败都只影响临时目录，规范目录保持零改动（fail-closed）。
    # 同阶段为已存在的目标做备份，供第二阶段中途失败时回滚。
    tmp_dir = contract.resolve_path(
        root, "teaching/%s" % (PROMOTE_TMP_FMT % args.run_id))
    if tmp_dir is None:
        print("[broker] 拒绝提升：临时目录路径越出项目根")
        return 1
    if os.path.isdir(tmp_dir):
        shutil.rmtree(tmp_dir, ignore_errors=True)
    staged = []  # [(dst, dst_rel, tmp_new, tmp_backup|None)]
    try:
        os.makedirs(tmp_dir, exist_ok=True)
        idx = 0
        for tid, qid, dst_task_rel, dst_tex_rel, dst_sol_rel, \
                dst_task, dst_tex, dst_sol, src_task, src_tex, src_sol, has_sol in plan:
            targets = [
                (dst_task, dst_task_rel, src_task),
                (dst_tex, dst_tex_rel, src_tex),
            ]
            if has_sol and dst_sol and os.path.isfile(src_sol):
                targets.append((dst_sol, dst_sol_rel, src_sol))

            for dst, dst_rel, src in targets:
                tmp_new = os.path.join(tmp_dir, "new-%04d" % idx)
                tmp_backup = None
                shutil.copyfile(src, tmp_new)
                if os.path.isfile(dst):
                    tmp_backup = os.path.join(tmp_dir, "bak-%04d" % idx)
                    shutil.copyfile(dst, tmp_backup)
                staged.append((dst, dst_rel, tmp_new, tmp_backup))
                idx += 1

            prom_entry = {
                "task_id": tid, "question_id": qid,
                "task_ref": dst_task_rel, "tex_ref": dst_tex_rel,
                "output_digest": run["tasks"][tid]["output_digest"],
                "render_digest": run["tasks"][tid]["render_digest"]
            }
            if has_sol:
                prom_entry["solution_ref"] = dst_sol_rel
                prom_entry["solution_output_digest"] = run["tasks"][tid].get("solution_output_digest")
            promoted.append(prom_entry)
    except OSError as exc:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print("[broker] 提升中止：暂存复制失败（规范目录未做任何改动）: %s" % exc)
        return 1

    # ---- 第二阶段：逐目标 os.replace 原子切换（同卷） ----
    switched = []  # 已切换的 staged 条目（供中途失败回滚）
    try:
        for dst, _dst_rel, tmp_new, tmp_backup in staged:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            os.replace(tmp_new, dst)  # 原子提升
            switched.append((dst, tmp_backup))
    except OSError as exc:
        # 回滚已切换目标：有备份则恢复，无备份（新建文件）则删除
        for dst, tmp_backup in switched:
            try:
                if tmp_backup and os.path.isfile(tmp_backup):
                    os.replace(tmp_backup, dst)
                elif os.path.isfile(dst):
                    os.remove(dst)
            except OSError:
                pass
        shutil.rmtree(tmp_dir, ignore_errors=True)
        print("[broker] 提升中止：原子切换失败并已回滚（规范目录恢复原状）: %s"
              % exc)
        return 1
    shutil.rmtree(tmp_dir, ignore_errors=True)

    record = {
        "run_id": args.run_id,
        "promoted_at": _now(),
        "broker_version": TOOL_VERSION,
        "code_digest": summary.get("code_digest"),
        "gates_summary": "%s/%s/gates/summary.json" % (STAGING_REL,
                                                       args.run_id),
        "promoted": promoted,
    }
    _write_json(paths["promotion"], record)
    print("[broker] 已提升 %d 个任务到规范路径（记录: staging/%s/"
          "promotion.json）" % (len(promoted), args.run_id))
    for p in promoted:
        print("  - %s -> %s / %s" % (p["task_id"], p["task_ref"],
                                     p["tex_ref"]))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="可信执行边界：packet 签发、提交验证、渲染、门禁与提升")
    ap.add_argument("project_root", help="项目根")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create_run", help="从冻结 brief 建立本轮任务清单")
    p.add_argument("--run-id", default=None)
    p.add_argument("--brief-digest", default=None,
                   help="可选：调用方声明的 brief_digest（与 freeze 核对）")

    p = sub.add_parser("issue_packet", help="签发窄包与签名 receipt")
    p.add_argument("--run-id", required=True)
    p.add_argument("--task-id", required=True)
    p.add_argument("--kind", default="student-task",
                   choices=["student-task", "teacher-solution"])

    p = sub.add_parser("submit_task", help="验证 staging 提交")
    p.add_argument("--run-id", required=True)
    p.add_argument("--task-id", required=True)
    p.add_argument("--input", required=True,
                   help="staging/<run_id>/tasks/<task_id>/student-task.json")

    p = sub.add_parser("render_task", help="渲染已验证任务")
    p.add_argument("--run-id", required=True)
    p.add_argument("--task-id", required=True)

    p = sub.add_parser("run_gates", help="在干净代码副本上运行全部门禁")
    p.add_argument("--run-id", required=True)
    p.add_argument("--skip-build", action="store_true",
                   help="跳过 PDF 构建（技术测试）")

    p = sub.add_parser("promote", help="门禁全过后原子提升到规范路径")
    p.add_argument("--run-id", required=True)

    p = sub.add_parser("issue_solution_packets", help="T2.5: 签发解答窄包与签名 receipt")
    p.add_argument("--run-id", required=True)
    p.add_argument("--task-id", default=None, help="可选：单个任务 ID，缺省时批量为所有已验证任务签发")

    p = sub.add_parser("submit_solution_task", help="T2.5: 验证 staging 中的逐小问解答提交")
    p.add_argument("--run-id", required=True)
    p.add_argument("--task-id", required=True)
    p.add_argument("--input", required=True,
                   help="staging/<run_id>/tasks/<task_id>/solution-task.json")

    p = sub.add_parser("migrate_t25", help="T2.5: 历史 run 补开或迁移解答签发")
    p.add_argument("--run-id", required=True)

    args = ap.parse_args()
    root = os.path.abspath(args.project_root)
    if not os.path.isdir(root):
        print("[broker] 项目根不存在: %s" % root)
        return 2

    handlers = {
        "create_run": cmd_create_run,
        "issue_packet": cmd_issue_packet,
        "submit_task": cmd_submit_task,
        "render_task": cmd_render_task,
        "run_gates": cmd_run_gates,
        "promote": cmd_promote,
        "issue_solution_packets": cmd_issue_solution_packets,
        "submit_solution_task": cmd_submit_solution_task,
        "migrate_t25": cmd_migrate_t25,
    }
    return handlers[args.command](root, args)


if __name__ == "__main__":
    sys.exit(main())
