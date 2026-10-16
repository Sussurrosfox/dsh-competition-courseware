"""build_nodes.py — Build (T3) and Delivery (T4) nodes for LangGraph.
Covers XeLaTeX dual-source compilation, gate synthesis, atomic promotion, and release delivery.
"""

import json
import os
from typing import Dict, Any, List, Optional
from ..state import CoursewareState
from ..adapters.script_runner import ScriptRunner

DELIVER_REPORT_REL = "teaching/checks/deliver.json"
MANIFEST_REL = "release/manifest.json"


def _truncate(text: str, limit: int = 500) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "..."


def _read_manifest(project_root: str) -> Optional[Dict[str, Any]]:
    path = os.path.join(project_root, MANIFEST_REL.replace("/", os.sep))
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _manifest_belongs_to_run(manifest: Optional[Dict[str, Any]], run_id: str) -> bool:
    """manifest 必须确由本轮交付生成，否则不得当成本轮产物返回。"""
    if not isinstance(manifest, dict):
        return False
    # 语义字段：技术测试记录（--skip-build）不构成发布成功
    if manifest.get("result") != "delivered":
        return False
    if manifest.get("build_skipped") is not False:
        return False
    if run_id:
        stamped = manifest.get("run_id")
        if stamped and stamped != run_id:
            return False
        # deliver 未 stamp run_id 时无法从内容上归属，交由新鲜度校验兜底
    return True


def validate_manifest(
    manifest: Optional[Dict[str, Any]],
    run_id: str,
    *,
    existed_before: bool,
    mtime_before: Optional[float],
    mtime_after: Optional[float]
) -> List[str]:
    """核对 manifest 是否确由本轮 deliver 生成。返回问题列表（空 = 通过）。"""
    problems: List[str] = []
    if manifest is None:
        return ["deliver 通过但未找到 %s（拒绝把缺失当成交付成功）" % MANIFEST_REL]

    if mtime_after is None:
        problems.append("%s 不是可读取的文件" % MANIFEST_REL)
    elif existed_before and mtime_before is not None and mtime_after <= mtime_before:
        problems.append(
            "%s 未被本轮 deliver 重写（mtime %s 未变；疑似沿用上一次运行的 manifest）"
            % (MANIFEST_REL, mtime_after)
        )

    if manifest.get("run_id") and run_id and manifest.get("run_id") != run_id:
        problems.append(
            "manifest.run_id=%r 与当前 run_id=%r 不一致（旧 manifest 不得充当本轮产物）"
            % (manifest.get("run_id"), run_id)
        )
    if manifest.get("result") != "delivered":
        problems.append(
            "manifest.result=%r 不是 delivered（技术测试记录不构成发布成功）"
            % manifest.get("result")
        )
    if manifest.get("build_skipped") is not False:
        problems.append(
            "manifest.build_skipped=%r：跳过构建的记录不是正式交付"
            % manifest.get("build_skipped")
        )
    return problems


def node_t3_latex_build(state: CoursewareState) -> Dict[str, Any]:
    """T3: Dual-output XeLaTeX compilation (student.pdf & teacher.pdf)."""
    project_root = state.get("project_root", ".")
    runner = ScriptRunner(project_root)

    # 1. Generate config and layout (gen_config.py requires --project)
    code_cfg, out_cfg, err_cfg = runner.run_script("gen_config.py", ["--project", project_root])
    history = list(state.get("history", []))

    if code_cfg != 0:
        cfg_msg = _truncate(out_cfg or err_cfg or "gen_config.py failed")
        history.append(f"T3: gen_config.py failed (exit {code_cfg}): {cfg_msg}")
        return {
            "stage": "T3_BUILT",
            "build_results": {"status": "REJECT", "errors": [cfg_msg]},
            "build_passed": False,
            "routing_action": "REPAIR_TASK",
            "active_repair_task": None,
            "history": history
        }

    # 2. Compile via build.py
    report_rel = "teaching/checks/build.json"
    build_gate = runner.run_gate(
        gate_name="build",
        script_name="build.py",
        args=[project_root, "--json", report_rel],
        report_rel_path=report_rel,
        timeout=360,
        run_id=state.get("run_id")
    )

    build_passed = build_gate.get("passed", False)
    history.append(f"T3: LaTeX build finished. Passed={build_passed}")

    # 当构建通过时，自动生成并编译学生解答版 student_solution.pdf
    if build_passed:
        try:
            code_sol, out_sol, err_sol = runner.run_script("gen_student_solution.py", [project_root, "--compile"])
            if code_sol == 0:
                history.append("T3: student_solution.pdf compiled successfully.")
            else:
                history.append(f"T3: student_solution.pdf notice: {_truncate(err_sol or out_sol, 150)}")
        except Exception as e:
            history.append(f"T3: student_solution.pdf generation error: {e}")

    routing_action = "PROCEED" if build_passed else "REPAIR_TASK"

    return {
        "stage": "T3_BUILT",
        "build_results": build_gate.get("raw_report", {}),
        "build_passed": build_passed,
        "routing_action": routing_action,
        "active_repair_task": None if not build_passed else state.get("active_repair_task"),
        "history": history
    }


def node_t4_promote_and_deliver(state: CoursewareState) -> Dict[str, Any]:
    """T4: Final gates, broker atomic promotion, and delivery packaging.

    promote / deliver / manifest 三个结论互相独立记录；只有本轮 deliver 通过
    且 manifest 确由本轮生成时才返回 T4_DELIVERED + PROCEED。任何一步失败都
    返回明确的失败阶段与错误，并阻止把磁盘上遗留的旧 manifest 当成本轮产物。
    """
    project_root = state.get("project_root", ".")
    run_id = state.get("run_id", "")
    runner = ScriptRunner(project_root)

    history = list(state.get("history", []))
    delivery_result: Dict[str, Any] = {}

    # 0. 记录 deliver 之前的 manifest 状态：用于判断它是否被本轮重写
    manifest_path = os.path.join(project_root, MANIFEST_REL.replace("/", os.sep))
    manifest_existed_before = os.path.isfile(manifest_path)
    manifest_mtime_before = (
        os.path.getmtime(manifest_path) if manifest_existed_before else None
    )

    # 1. Run full clean gates in broker — a gate failure must block promotion
    code_g, out_g, err_g = runner.run_script("broker.py", [project_root, "run_gates", "--run-id", run_id])
    delivery_result["run_gates"] = {
        "exit_code": code_g,
        "passed": code_g == 0,
        "detail": _truncate(out_g or err_g or "broker run_gates failed")
    }

    if code_g != 0:
        gate_msg = _truncate(out_g or err_g or "broker run_gates failed")
        history.append(f"T4: broker run_gates FAILED (exit {code_g}), promotion blocked: {gate_msg}")
        return {
            "stage": "T4_FAILED",
            "promoted": False,
            "delivery_manifest": None,
            "deliver_gate": {
                "gate_name": "deliver",
                "status": "ERROR",
                "passed": False,
                "issues": [{
                    "category": "RUN_GATES_FAILED",
                    "severity": "HIGH_RISK",
                    "message": gate_msg
                }],
                "raw_report": {},
                "exit_code": code_g,
                "run_id": run_id,
                "report_is_fresh": False,
            },
            "delivery_result": delivery_result,
            "routing_action": "HALT",
            "error_message": f"T4 final gates rejected promotion: {gate_msg}",
            "history": history
        }

    # 2. Promote from staging to canonical
    code_p, out_p, err_p = runner.run_script("broker.py", [project_root, "promote", "--run-id", run_id])
    promoted = (code_p == 0)
    promote_detail = _truncate(err_p or out_p or "")
    delivery_result["promote"] = {
        "exit_code": code_p,
        "passed": promoted,
        "detail": promote_detail
    }
    if not promoted:
        history.append(f"T4: broker promote failed (exit {code_p}): {promote_detail}")
        return {
            "stage": "T4_FAILED",
            "promoted": False,
            "delivery_manifest": None,
            "deliver_gate": {
                "gate_name": "deliver",
                "status": "ERROR",
                "passed": False,
                "issues": [{
                    "category": "PROMOTE_FAILED",
                    "severity": "HIGH_RISK",
                    "message": promote_detail
                }],
                "raw_report": {},
                "exit_code": code_p,
                "run_id": run_id,
                "report_is_fresh": False,
            },
            "delivery_result": delivery_result,
            "routing_action": "HALT",
            "error_message": f"T4 broker promote failed (exit {code_p}): {promote_detail}",
            "history": history
        }

    history.append("T4: broker promote succeeded.")

    # 3. Final delivery gate (independent from promote)
    deliver_args = [project_root, "--json", DELIVER_REPORT_REL, "--run-id", run_id]
    if state.get("deliver_teacher"):
        deliver_args.append("--deliver-teacher")
    else:
        deliver_args.append("--no-deliver-teacher")

    deliver_gate = runner.run_gate(
        gate_name="deliver",
        script_name="deliver.py",
        args=deliver_args,
        report_rel_path=DELIVER_REPORT_REL,
        timeout=360,
        run_id=run_id
    )
    delivery_result["deliver"] = {
        "exit_code": deliver_gate.get("exit_code"),
        "status": deliver_gate.get("status"),
        "passed": bool(deliver_gate.get("passed")),
        "report_is_fresh": bool(deliver_gate.get("report_is_fresh")),
        "detail": "; ".join(
            i.get("message", "") for i in deliver_gate.get("issues", [])[:3]
        )
    }

    # 4. Manifest 校验：必须本轮生成、语义为正式交付、归属本轮 run
    manifest_data = _read_manifest(project_root)
    manifest_problems = validate_manifest(
        manifest_data,
        run_id,
        existed_before=manifest_existed_before,
        mtime_before=manifest_mtime_before,
        mtime_after=(
            os.path.getmtime(manifest_path) if os.path.isfile(manifest_path) else None
        ),
    )
    manifest_ok = not manifest_problems
    delivery_result["manifest"] = {
        "passed": manifest_ok,
        "path": MANIFEST_REL,
        "belongs_to_run": _manifest_belongs_to_run(manifest_data, run_id) and manifest_ok,
        "problems": manifest_problems,
    }

    deliver_passed = bool(deliver_gate.get("passed"))
    delivered = deliver_passed and promoted and manifest_ok

    if delivered:
        history.append(
            "T4: Delivered successfully. Promoted=%s, DeliverStatus=%s, manifest run_id=%s"
            % (promoted, deliver_gate.get("status"), manifest_data.get("run_id") or "(unstamped)")
        )
        return {
            "stage": "T4_DELIVERED",
            "promoted": True,
            "delivery_manifest": manifest_data,
            "deliver_gate": deliver_gate,
            "delivery_result": delivery_result,
            "routing_action": "PROCEED",
            "history": history
        }

    # 失败：明确阶段 + 错误 + 不返回旧 manifest
    if not deliver_passed:
        reason = "; ".join(
            i.get("message", "") for i in deliver_gate.get("issues", [])[:5]
        ) or "deliver gate failed (exit %s)" % deliver_gate.get("exit_code")
        error_message = "T4 deliver gate rejected delivery: %s" % reason
    else:
        error_message = "T4 manifest verification failed: %s" % "; ".join(manifest_problems)

    history.append(f"T4: Delivery FAILED. Promoted={promoted}, reason={error_message}")
    return {
        "stage": "T4_FAILED",
        "promoted": promoted,
        "delivery_manifest": None,
        "deliver_gate": deliver_gate,
        "delivery_result": delivery_result,
        "routing_action": "HALT",
        "error_message": error_message,
        "history": history
    }
