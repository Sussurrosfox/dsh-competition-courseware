#!/usr/bin/env python3
# -*- coding: utf-8 -*-
TOOL_VERSION = "1.0"
r"""
check_agent_audit.py — 多 Agent 证据链门禁审计（零信任写入者身份校验）

背景：防止主 Agent 单会话直接在磁盘上撰写/修改教学研学产物（architect-plan, student-task,
solution-task, probe, pitfall, solution）。状态机与门禁双重拦截：状态机在运行时拒收，
本门禁作为交付前置硬卡点独立校验磁盘上的派发单与 HMAC 签名证据台账。

审计逻辑：
  1. 扫描 teaching/agent-requests/*.json 派发单；
  2. 若无派发单：
     - 若磁盘存在教学/任务核心产物，直接判 FAIL（UNATTESTED_ARTIFACTS）；
     - 若均无产物，判 PASS（空项目）；
  3. 对每个派发单对账其声明产物与 teaching/agent-calls/<dispatch_id>.json 证据：
     - ok: 证据在案、HMAC 验签通过、产物哈希与证据一致且与 before 有变化；
     - ok_unattested: 产物有变但无证据。若开启 allow_unattested（逃生阀）则记 WARN，否则 FAIL（EVIDENCE_MISSING）；
     - attested_mismatch: 证据在案但摘要与当前磁盘不符（证据落盘后产物被篡改），一律 FAIL（EVIDENCE_STALE）；
     - EVIDENCE_FORGED: HMAC 验签失败（伪造台账），一律 FAIL；
     - EVIDENCE_ID_MISMATCH: 内部 ID 与文件名不符，一律 FAIL；
     - missing_artifacts / no_change: 产物缺失或无修改，一律 FAIL。
  4. 支持 superseded 识别：旧派发单的产物若已由后续派发（如定点返修）重新认证，则旧派发单的失配降级为 warning。
"""

import argparse
import glob
import hashlib
import json
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
_PLUGIN_ROOT = os.path.abspath(os.path.join(_SCRIPT_DIR, ".."))
if _PLUGIN_ROOT not in sys.path:
    sys.path.insert(0, _PLUGIN_ROOT)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

import contract


def _import_agent_evidence():
    """导入 agent_evidence 模块。

    插件目录内运行（scripts/check_agent_audit.py）走包内 import；被复制到
    干净代码副本（broker run_gates 的 staging/<run_id>/gates/code/scripts/，
    只含 scripts/ 与 schemas/）时，按文件路径从插件根回退加载。
    """
    try:
        from workflow.nodes import agent_evidence as _ae
        return _ae
    except Exception:
        pass
    import importlib.util
    candidates = (
        os.path.join(_PLUGIN_ROOT, "workflow", "nodes", "agent_evidence.py"),
        os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "..", "workflow",
                                      "nodes", "agent_evidence.py")),
    )
    for cand in candidates:
        if os.path.isfile(cand):
            spec = importlib.util.spec_from_file_location(
                "competition_courseware_agent_evidence", cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise ImportError("agent_evidence 模块不可用（插件根: %s）" % _PLUGIN_ROOT)


agent_evidence = _import_agent_evidence()

DEFAULT_REPORT = os.path.join("teaching", "checks", "check_agent_audit.json")
REQUESTS_DIR_REL = os.path.join("teaching", "agent-requests")


def _finding(severity: str, message: str, code: str = "", dispatch_id: str = "") -> dict:
    f = {"severity": severity, "message": message}
    if code:
        f["code"] = code
    if dispatch_id:
        f["dispatch_id"] = dispatch_id
    return f


def _load_allow_unattested(project_root: str) -> bool:
    policy = agent_evidence.load_dispatch_policy(project_root)
    return bool(policy.get("allow_unattested", False))


def audit_agent_evidence(project_root: str, allow_unattested: bool = False):
    """对账全部派发单与证据台账，返回 (status, findings, evidence_dict)。"""
    root = os.path.abspath(project_root)
    req_dir = os.path.join(root, REQUESTS_DIR_REL)
    req_paths = sorted(glob.glob(os.path.join(req_dir, "*.json"))) if os.path.isdir(req_dir) else []

    all_evidence = agent_evidence.scan_evidence(root)
    # 建立当前磁盘已认证的产物集合：rel -> set(dispatch_id)
    attested_current = {}
    for ev in all_evidence:
        if str(ev.get("status") or "") != agent_evidence.EVIDENCE_STATUS_OK:
            continue
        eid = str(ev.get("dispatch_id") or "")
        digests = ev.get("output_digests") or {}
        for rel, ev_sha in digests.items():
            abs_p = contract.resolve_path(root, rel)
            if abs_p and os.path.isfile(abs_p):
                disk_sha = contract.sha256_file(abs_p)
                if disk_sha and disk_sha.lower() == str(ev_sha).lower():
                    attested_current.setdefault(rel, set()).add(eid)

    findings = []
    dispatches = []
    counts = {
        "dispatches": len(req_paths),
        "evidence_files": len(all_evidence),
        "attested": 0,
        "unattested": 0,
        "mismatch": 0,
        "forged": 0,
        "superseded": 0,
    }

    # 边界情况：无派发单
    if not req_paths:
        # 探测是否有核心认知产物存在
        core_artifacts = [
            "teaching/architect-plan.json",
            "teaching/materials.json",
        ]
        # 扫描 staging 下的 student-task.json / solution-task.json
        staging_dir = os.path.join(root, "staging")
        if os.path.isdir(staging_dir):
            for t_file in glob.glob(os.path.join(staging_dir, "**", "*-task.json"), recursive=True):
                rel = os.path.relpath(t_file, root).replace("\\", "/")
                core_artifacts.append(rel)

        existing_core = [rel for rel in core_artifacts if os.path.isfile(os.path.join(root, rel.replace("/", os.sep)))]
        if existing_core:
            findings.append(_finding(
                "fail",
                f"存在教学与任务产物（{', '.join(existing_core[:5])}），但未找到任何派发单记录（{REQUESTS_DIR_REL} 为空），"
                "产物疑似由主 Agent 未经派发直接生成。",
                code="UNATTESTED_ARTIFACTS"
            ))
            return "fail", findings, {
                "checked": counts,
                "allow_unattested": allow_unattested,
                "dispatches": [],
            }

        # 干净工作区无产物且无派发单：PASS
        findings.append(_finding("info", "工作区无待审计派发单与认知产物，审计通过。"))
        return "pass", findings, {
            "checked": counts,
            "allow_unattested": allow_unattested,
            "dispatches": [],
        }

    for req_path in req_paths:
        try:
            with open(req_path, "r", encoding="utf-8-sig") as f:
                request = json.load(f)
        except Exception as exc:
            findings.append(_finding(
                "fail",
                f"派发单不可解析: {os.path.basename(req_path)} ({exc})",
                code="CORRUPT_REQUEST"
            ))
            continue

        dispatch_id = str(request.get("dispatch_id") or os.path.splitext(os.path.basename(req_path))[0])
        stage = str(request.get("stage") or "")
        scope = str(request.get("scope") or "")
        artifacts = [str(a) for a in (request.get("artifacts") or []) if a]

        # 收集磁盘摘要
        disk_digests = {}
        for rel in artifacts:
            abs_p = contract.resolve_path(root, rel)
            disk_digests[rel] = contract.sha256_file(abs_p) if abs_p and os.path.isfile(abs_p) else None

        status, ev = agent_evidence.check_request_attested(
            root, request, disk_digests, allow_unattested=allow_unattested
        )

        # 检查是否被后续派发 superseded
        is_superseded = False
        if status in ("attested_mismatch", "ok_unattested", "missing_artifacts", "no_change"):
            # 若此单涉及的所有产物在当前盘面上均已被其它 status=ok 的派发单认证
            if artifacts and all(bool(attested_current.get(rel, set()) - {dispatch_id}) for rel in artifacts if disk_digests.get(rel)):
                is_superseded = True

        if is_superseded:
            counts["superseded"] += 1
            findings.append(_finding(
                "info",
                f"派发单 {dispatch_id} ({stage}/{scope}) 原结果为 {status}，但其产物已被后续派发重新认证 (superseded)。",
                code="SUPERSEDED",
                dispatch_id=dispatch_id
            ))
            dispatches.append({
                "dispatch_id": dispatch_id,
                "stage": stage,
                "scope": scope,
                "status": "superseded",
            })
            continue

        if status == "ok":
            counts["attested"] += 1
            dispatches.append({
                "dispatch_id": dispatch_id,
                "stage": stage,
                "scope": scope,
                "status": "ok",
                "worker": (ev or {}).get("worker"),
            })
        elif status == "ok_unattested":
            counts["unattested"] += 1
            msg = (
                f"派发单 {dispatch_id}（{stage}/{scope}）缺少代理证据台账"
                f"（teaching/agent-calls/{dispatch_id}.json），产物由主 Agent 单会话直写的嫌疑无法排除。"
                f"请使用 courseware_dispatch 工具派发子 Agent 生成。"
            )
            if allow_unattested:
                findings.append(_finding("review", msg + " [已启用 allow_unattested 逃生阀]", code="EVIDENCE_MISSING", dispatch_id=dispatch_id))
            else:
                findings.append(_finding("fail", msg, code="EVIDENCE_MISSING", dispatch_id=dispatch_id))
            dispatches.append({
                "dispatch_id": dispatch_id,
                "stage": stage,
                "scope": scope,
                "status": "ok_unattested",
            })
        elif status == "attested_mismatch":
            counts["mismatch"] += 1
            findings.append(_finding(
                "fail",
                f"派发单 {dispatch_id}（{stage}/{scope}）的产物与代理证据不符（证据在案但摘要对不上，疑似证据落盘后产物被篡改）。",
                code="EVIDENCE_STALE",
                dispatch_id=dispatch_id
            ))
            dispatches.append({
                "dispatch_id": dispatch_id,
                "stage": stage,
                "scope": scope,
                "status": "attested_mismatch",
            })
        elif status == "EVIDENCE_FORGED":
            counts["forged"] += 1
            findings.append(_finding(
                "fail",
                f"派发单 {dispatch_id}（{stage}/{scope}）的代理证据 HMAC 签名无效（疑似伪造或篡改密钥）。",
                code="EVIDENCE_FORGED",
                dispatch_id=dispatch_id
            ))
            dispatches.append({
                "dispatch_id": dispatch_id,
                "stage": stage,
                "scope": scope,
                "status": "EVIDENCE_FORGED",
            })
        elif status == "EVIDENCE_ID_MISMATCH":
            findings.append(_finding(
                "fail",
                f"派发单 {dispatch_id} 的证据内容 dispatch_id 与文件名不一致。",
                code="EVIDENCE_ID_MISMATCH",
                dispatch_id=dispatch_id
            ))
            dispatches.append({
                "dispatch_id": dispatch_id,
                "stage": stage,
                "scope": scope,
                "status": "EVIDENCE_ID_MISMATCH",
            })
        elif status == "missing_artifacts":
            findings.append(_finding(
                "fail",
                f"派发单 {dispatch_id} 声明的部分产物未在磁盘上生成: "
                + ", ".join([rel for rel in artifacts if not disk_digests.get(rel)]),
                code="MISSING_ARTIFACTS",
                dispatch_id=dispatch_id
            ))
            dispatches.append({
                "dispatch_id": dispatch_id,
                "stage": stage,
                "scope": scope,
                "status": "missing_artifacts",
            })
        elif status == "no_change":
            findings.append(_finding(
                "fail",
                f"派发单 {dispatch_id} 声明的产物与派发前完全一致（没有实际产出）。",
                code="NO_CHANGE",
                dispatch_id=dispatch_id
            ))
            dispatches.append({
                "dispatch_id": dispatch_id,
                "stage": stage,
                "scope": scope,
                "status": "no_change",
            })

    has_fail = any(f["severity"] == "fail" for f in findings)
    has_review = any(f["severity"] == "review" for f in findings)
    if has_fail:
        final_status = "fail"
    elif has_review:
        final_status = "review"
    else:
        final_status = "pass"

    return final_status, findings, {
        "checked": counts,
        "allow_unattested": allow_unattested,
        "dispatches": dispatches,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="多 Agent 证据链门禁审计 (check_agent_audit.py)")
    ap.add_argument("project_root", nargs="?", default=".", help="项目根目录（含 teaching/）")
    ap.add_argument("--json", metavar="PATH", default=None, help="写入结构化检查报告（默认 teaching/checks/check_agent_audit.json）")
    ap.add_argument("--allow-unattested", action="store_true", help="允许无证据台账通过（逃生阀，仅记 WARN 不 FAIL）")
    ap.add_argument("--fix", action="store_true", help="打印缺失证据派发单的补派发提示")
    args = ap.parse_args(argv)

    root = os.path.abspath(args.project_root)
    if not os.path.isdir(root):
        print(f"[check_agent_audit] 项目根目录不存在: {root}")
        return 2

    # NB5 输入守卫：既无 teaching/ 又无 research/ 的目标不像项目根，
    # 直接返回不写报告（拒绝在非项目目录生成 teaching/checks/ 空壳）。
    if not os.path.isdir(os.path.join(root, "teaching")) \
            and not os.path.isdir(os.path.join(root, "research")):
        print(f"[check_agent_audit] 目标目录不像竞赛教研项目根"
              f"（缺 teaching/ 与 research/）: {root}")
        return 2

    allow_unattested = args.allow_unattested or _load_allow_unattested(root)
    status, findings, evidence_dict = audit_agent_evidence(root, allow_unattested=allow_unattested)

    report_path = args.json or os.path.join(root, DEFAULT_REPORT)
    report = contract.write_check_report(report_path, status, "agent_audit", findings)
    report["evidence"] = evidence_dict
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")

    for f in findings:
        sev = f["severity"]
        prefix = "错误" if sev in ("error", "fail") else ("待复核" if sev == "review" else "说明")
        print(f"[check_agent_audit] [{prefix}] {f.get('code', '')}: {f['message']}")

    if args.fix:
        missing_ids = [
            d["dispatch_id"] for d in evidence_dict.get("dispatches", [])
            if d.get("status") in ("ok_unattested", "missing_artifacts")
        ]
        if missing_ids:
            print("\n[check_agent_audit --fix] 发现未认证或缺失产物的派发单，请运行下列命令进行补派发:")
            for mid in missing_ids:
                print(f"  courseware_dispatch request_ref=teaching/agent-requests/{mid}.json")

    checked = evidence_dict.get("checked", {})
    print(f"[check_agent_audit] status={status}（已审计 {checked.get('dispatches', 0)} 份派发单，{checked.get('attested', 0)} 通过，"
          f"{checked.get('unattested', 0)} 未认证，{checked.get('mismatch', 0)} 不匹配，{checked.get('forged', 0)} 伪造）")
    print(f"[check_agent_audit] 报告已写入: {report_path}")

    if status in ("fail", "error"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
