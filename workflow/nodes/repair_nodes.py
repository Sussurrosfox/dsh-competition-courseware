"""repair_nodes.py — Repair and research-back recovery nodes for LangGraph.

返修与研究打回都不是“原样重跑”：每个节点先把门禁结论转成一份具体的派发单
（改哪个文件、按哪些 findings 改），由 :mod:`workflow.nodes.agent_nodes` 派发
给认知 worker 或人工中断，并校验产物确实发生变化。没有注入 worker、也没有
人工/Antigravity 产物时，流水线中止并报告错误，而不是无限重试同一个门禁。
"""

import os
from typing import Any, Dict

from ..state import CoursewareState
from .agent_nodes import (
    node_repair_request,
    node_repair_await,
)

MAX_REPAIR_RETRIES = 3
MAX_RESEARCH_BACKS = 3


def node_repair_small_output(state: CoursewareState) -> Dict[str, Any]:
    """Targeted repair loop: turn gate findings into a concrete dispatch request."""
    task_id = state.get("active_repair_task")
    retry_count = state.get("retry_count", 0) + 1

    history = list(state.get("history", []))
    history.append(f"Repair: Triggered repair loop #{retry_count} for task {task_id}")

    if retry_count > MAX_REPAIR_RETRIES:
        history.append(f"Repair: Max retry limit reached ({MAX_REPAIR_RETRIES}). Halting.")
        return {
            "stage": "ABORTED",
            "routing_action": "HALT",
            "error_message": (
                f"Exceeded max retries ({MAX_REPAIR_RETRIES}) for task {task_id}: "
                "自动返修无法闭环，请人工介入修改 staging 产物后重跑对应门禁"
            ),
            "retry_count": retry_count,
            "history": history
        }

    if not task_id:
        history.append("Repair: 没有 active_repair_task，无法定点返修。Halting.")
        return {
            "stage": "ABORTED",
            "routing_action": "HALT",
            "error_message": "repair requested without active_repair_task",
            "retry_count": retry_count,
            "history": history
        }

    out = node_repair_request({**state, "history": history})
    out["retry_count"] = retry_count
    return out


def node_repair_small_output_await(state: CoursewareState) -> Dict[str, Any]:
    """Wait for the dispatched repair artifacts and verify they changed."""
    return node_repair_await(state)


def node_research_back(state: CoursewareState) -> Dict[str, Any]:
    """Fatal research-back loop: revoke freeze and dispatch re-research work."""
    project_root = state.get("project_root", ".")
    history = list(state.get("history", []))
    research_back_count = state.get("research_back_count", 0) + 1
    history.append(
        f"Research-Back: Revoking freeze and routing back to R2 deep research. (loop #{research_back_count})"
    )

    # Circuit breaker: deterministic R2 failures would otherwise loop forever
    if research_back_count > MAX_RESEARCH_BACKS:
        history.append(
            f"Research-Back: Max research-back limit reached ({MAX_RESEARCH_BACKS}). Halting."
        )
        return {
            "stage": "ABORTED",
            "routing_action": "HALT",
            "error_message": (
                f"Exceeded max research-back loops ({MAX_RESEARCH_BACKS}): "
                "R2 research cannot be fixed automatically"
            ),
            "research_back_count": research_back_count,
            "history": history
        }

    # Remove freeze.json to unlock brief
    freeze_json = os.path.join(project_root, "research", "freeze.json")
    if os.path.exists(freeze_json):
        try:
            # Backup as freeze_revoked.json
            revoked_path = os.path.join(project_root, "research", "freeze_revoked.json")
            os.replace(freeze_json, revoked_path)
            history.append(f"Research-Back: Moved {freeze_json} to {revoked_path}")
        except Exception as e:
            history.append(f"Research-Back: Error revoking freeze: {e}")
    else:
        history.append("Research-Back: freeze.json 不存在（无需撤销）")

    # 确定性包装到此为止：清空上一轮子阶段标记，由图路由到 rb_probe_request
    # 进入两阶段派发（probe -> solver）
    return {
        "stage": "RESEARCH_BACK_LOOP",
        "rb_phase": None,
        "worker_available": state.get("worker_available", False),
        "routing_action": "PROCEED",
        "research_back_count": research_back_count,
        # A fresh research round resets the T2 repair retry budget
        "retry_count": 0,
        "history": history
    }
