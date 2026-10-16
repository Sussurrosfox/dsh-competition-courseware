"""research_nodes.py — Research phase nodes (R0-R4) for LangGraph.
Covers screening, deep research pipeline (probe, solver, math-audit), and freeze brief.
"""

import json
import os
from typing import Dict, Any, List, Optional, Tuple
from ..state import CoursewareState
from ..adapters.script_runner import ScriptRunner

BRIEF_REL = "research/brief.json"
FREEZE_REL = "research/freeze.json"


def read_frozen_selection(project_root: str) -> Tuple[Optional[List[str]], Optional[str]]:
    """读取冻结 brief 的 selected_problem_ids。

    返回 (ids, error)。brief 缺失或不可解析时返回 (None, 错误信息)。
    """
    path = os.path.join(project_root, BRIEF_REL.replace("/", os.sep))
    if not os.path.isfile(path):
        return None, "缺少 %s：无法核对界面选题与冻结 brief" % BRIEF_REL
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            brief = json.load(f)
    except Exception as exc:
        return None, "%s 不是合法 JSON: %s" % (BRIEF_REL, exc)
    ids = brief.get("selection", {}).get("selected_problem_ids")
    if not isinstance(ids, list) or not ids:
        return None, "%s 的 selection.selected_problem_ids 为空或非法" % BRIEF_REL
    return [str(i) for i in ids], None


def check_selection_against_brief(
    project_root: str,
    selected_problems: List[str]
) -> Tuple[bool, Optional[List[str]], Optional[str]]:
    """核对界面/CLI 选题与冻结 brief 是否一致。

    冻结 brief 是权威输入：T2 的 ``create_run`` 只按 brief 的
    ``selected_problem_ids`` 建立任务清单，界面勾选若不匹配就会被静默忽略。
    因此不一致时必须明确拒绝，而不是让用户以为自己的选择生效了。

    返回 (ok, brief_ids, mismatch_message)。
    """
    if not selected_problems:
        # 未显式选择时采用 brief 清单，不存在“不一致”
        brief_ids, err = read_frozen_selection(project_root)
        return True, brief_ids, err

    brief_ids, err = read_frozen_selection(project_root)
    if err:
        return False, None, err

    wanted = [str(p) for p in selected_problems]
    if sorted(wanted) == sorted(brief_ids):
        return True, brief_ids, None

    return False, brief_ids, (
        "界面/CLI 选题 %s 与冻结 brief 的 selected_problem_ids %s 不一致。"
        "冻结 brief 是权威输入（broker create_run 只按 brief 建立任务清单），"
        "本次选择不会生效；请重新冻结 brief，或在启动前把选择改回与 brief 一致。"
        % (sorted(wanted), sorted(brief_ids))
    )


def node_r1_screen_problems(state: CoursewareState) -> Dict[str, Any]:
    """R1: Score candidate problems and establish selection list."""
    project_root = state.get("project_root", ".")
    runner = ScriptRunner(project_root)

    # Score problem value
    report_rel = "research/checks/score_problem_value.json"
    score_gate = runner.run_gate(
        gate_name="score_problem_value",
        script_name="score_problem_value.py",
        args=[project_root, "--json", report_rel],
        report_rel_path=report_rel,
        run_id=state.get("run_id")
    )

    selected = state.get("selected_problems", [])
    brief_ids: Optional[List[str]] = None
    mismatch: Optional[str] = None

    if not selected:
        # Default fallback or scan research/problems
        problems_dir = os.path.join(project_root, "research", "problems")
        if os.path.isdir(problems_dir):
            selected = sorted([d for d in os.listdir(problems_dir) if os.path.isdir(os.path.join(problems_dir, d))])
        if not selected:
            # Fallback to P01
            selected = ["P01"]

    ok, brief_ids, mismatch = check_selection_against_brief(project_root, selected)
    if not ok:
        history = list(state.get("history", []))
        history.append(f"R1: {mismatch}")
        return {
            "stage": "ABORTED",
            "selected_problems": selected,
            "brief_selected_problem_ids": brief_ids or [],
            "selection_mismatch": mismatch,
            "problem_scores": score_gate.get("raw_report", {}),
            "routing_action": "HALT",
            "error_message": mismatch,
            "history": history
        }

    history = list(state.get("history", []))
    history.append(f"R1: Screened problems. Selected: {selected}")

    return {
        "stage": "R1_SCORED",
        "selected_problems": selected,
        "brief_selected_problem_ids": brief_ids or [],
        "selection_mismatch": None,
        "problem_scores": score_gate.get("raw_report", {}),
        "history": history
    }


def node_r2_deep_research(state: CoursewareState) -> Dict[str, Any]:
    """R2: Deep research pipeline and structural check."""
    project_root = state.get("project_root", ".")
    runner = ScriptRunner(project_root)

    # Run check_research.py
    report_rel = "research/checks/check_research.json"
    research_gate = runner.run_gate(
        gate_name="check_research",
        script_name="check_research.py",
        args=[project_root, "--json", report_rel],
        report_rel_path=report_rel,
        run_id=state.get("run_id")
    )

    history = list(state.get("history", []))
    history.append(f"R2: Ran check_research. Status={research_gate.get('status')}")

    routing_action = "PROCEED" if research_gate.get("passed") else "RESEARCH_BACK"

    return {
        "stage": "R2_RESEARCHED",
        "research_gate": research_gate,
        "routing_action": routing_action,
        "history": history
    }


def node_r4_freeze_brief(state: CoursewareState) -> Dict[str, Any]:
    """R4: Freeze teaching brief and lock digest."""
    project_root = state.get("project_root", ".")
    runner = ScriptRunner(project_root)

    freeze_json_path = os.path.join(project_root, "research", "freeze.json")
    freeze_digest = None
    brief_frozen = False

    # Check if already frozen
    if os.path.exists(freeze_json_path):
        try:
            with open(freeze_json_path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
                freeze_digest = data.get("brief_digest") or data.get("freeze_digest") or data.get("sha256")
            if freeze_digest:
                brief_frozen = True
        except Exception:
            pass

    # If not yet frozen, run freeze_brief.py
    if not brief_frozen:
        code, stdout, stderr = runner.run_script("freeze_brief.py", [project_root])
        brief_frozen = (code == 0)
        if os.path.exists(freeze_json_path):
            try:
                with open(freeze_json_path, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                    freeze_digest = data.get("freeze_digest") or data.get("sha256")
            except Exception:
                pass

    history = list(state.get("history", []))
    history.append(f"R4: Freeze brief completed. Frozen={brief_frozen}, Digest={freeze_digest}")

    return {
        "stage": "R4_FROZEN",
        "brief_frozen": brief_frozen,
        "freeze_digest": freeze_digest,
        "routing_action": "PROCEED" if brief_frozen else "HALT",
        "history": history
    }
