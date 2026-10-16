"""edges.py — Conditional routing functions for LangGraph courseware workflow.
Decouples state transition logic from node definitions.

约定：`routing_action == "HALT"` 一律结束本轮执行，并把 `error_message` 留给
UI/CLI 展示；不带错误信息的“静默结束”不允许出现。

G2.2 fail-closed 约定：`routing_action` 缺失或取异常值时一律路由 END
（拒绝 fail-open 默认放行）；仅在显式合法值（"PROCEED" / "REPAIR_TASK" /
"RESEARCH_BACK"）时按映射前进。
"""

from langgraph.graph import END
from .state import CoursewareState


def route_after_research(state: CoursewareState) -> str:
    """Route after R2 deep research: proceeds to freeze if gate passed, otherwise research back."""
    # G2.2 fail-closed：缺省/异常值一律 END，不再默认放行冻结
    action = state.get("routing_action")
    if action == "PROCEED":
        return "r4_freeze_brief"
    elif action == "RESEARCH_BACK":
        return "research_back"
    return END


def route_after_freeze(state: CoursewareState) -> str:
    """Route after R4 brief freeze: proceeds to Phase 0 Breakpoint if frozen."""
    if state.get("brief_frozen", False):
        return "phase0_breakpoint"
    return END


def route_after_phase0(state: CoursewareState) -> str:
    """Route after Phase 0 breakpoint: proceeds to T0 if user confirmed."""
    spec = state.get("user_spec") or {}
    if spec.get("confirmed", False):
        return "t0_init_project"
    return "phase0_breakpoint"


def route_after_init(state: CoursewareState) -> str:
    """Route after T0 init: proceeds to T1 architect dispatch."""
    if state.get("routing_action") == "PROCEED":
        return "t1_request_architect"
    return END


def route_after_architect(state: CoursewareState) -> str:
    """Route after T1 gate: proceeds to T2 broker only when the plan passed the gate."""
    if state.get("routing_action") == "HALT":
        return END
    gate = state.get("architect_gate") or {}
    if gate.get("passed", False):
        return "t2_broker_issue"
    return END


def route_after_t1_await(state: CoursewareState) -> str:
    """Route after the T1 dispatch node: artifacts ready -> gate; failure -> END."""
    if state.get("routing_action") == "HALT":
        return END
    return "t1_architect"


def route_after_t2_check(state: CoursewareState) -> str:
    """Route after T2 broker submit and quality gates."""
    # G2.2 fail-closed：缺省/异常值一律 END，不再默认进入 T3 构建
    action = state.get("routing_action")
    if action == "PROCEED":
        # 契约 2.5.0 (T2.5): 学生任务门禁通过后，强制进入小问解答派发环节
        return "t2_request_subquestion_solutions"
    elif action == "REPAIR_TASK":
        return "repair_small_output"
    elif action == "RESEARCH_BACK":
        return "research_back"
    return END


def route_after_subquestion_solution_await(state: CoursewareState) -> str:
    """Route after waiting for subquestion solutions: artifacts ready -> check."""
    if state.get("routing_action") == "HALT":
        return END
    return "t2_check_subquestion_solutions"


def route_after_subquestion_solution_gate(state: CoursewareState) -> str:
    """Route after T2.5 subquestion solution gate: pass -> build; fail -> repair."""
    action = state.get("routing_action")
    if action == "PROCEED":
        return "t3_latex_build"
    elif action == "REPAIR_TASK":
        return "repair_small_output"
    return END


def route_after_t2_await(state: CoursewareState) -> str:
    """Route after the T2 student-task dispatch node: 可选自审 -> verify."""
    if state.get("routing_action") == "HALT":
        return END
    if state.get("t2_self_review", False):
        return "t2_review_request"
    return "t2_student_task"


def route_after_t2_review_await(state: CoursewareState) -> str:
    """Route after the T2 self-review dispatch node: artifacts ready -> verify."""
    if state.get("routing_action") == "HALT":
        return END
    return "t2_student_task"


def route_after_t2_verify(state: CoursewareState) -> str:
    """Route after the read-only T2 verification node: all present -> submit & check."""
    if state.get("routing_action") == "HALT":
        return END
    return "t2_broker_submit_and_check"


def route_after_build(state: CoursewareState) -> str:
    """Route after T3 LaTeX build: proceeds to delivery if build passed."""
    if state.get("build_passed", False):
        return "t4_promote_and_deliver"
    # G2.2 fail-closed：构建失败时仅显式 REPAIR_TASK 进入返修，缺省/异常一律 END
    if state.get("routing_action") == "REPAIR_TASK":
        return "repair_small_output"
    return END


def route_after_repair(state: CoursewareState) -> str:
    """Route after the repair request node: wait for the dispatched artifacts."""
    # G2.2 fail-closed：仅显式 PROCEED 才等待返修产物，缺省/异常一律 END
    if state.get("routing_action") == "PROCEED":
        return "repair_await"
    return END


def route_after_repair_await(state: CoursewareState) -> str:
    """Route after repair artifacts arrived: re-run the corresponding gates, or abort."""
    # G2.2 fail-closed：仅显式 PROCEED 才重跑门禁，缺省/异常一律 END
    if state.get("routing_action") == "PROCEED":
        t2_gates = state.get("t2_gates") or {}
        t2_passed = all(g.get("passed", False) for g in t2_gates.values()) if t2_gates else False
        if t2_passed and not state.get("subquestion_solution_passed", False):
            return "t2_check_subquestion_solutions"
        return "t2_broker_submit_and_check"
    return END


def route_after_research_back(state: CoursewareState) -> str:
    """Route after the research-back wrapper: enter the two-phase probe dispatch."""
    # G2.2 fail-closed：仅显式 PROCEED 才进入探针重做，缺省/异常一律 END
    if state.get("routing_action") == "PROCEED":
        return "rb_probe_request"
    return END


def route_after_rb_probe_await(state: CoursewareState) -> str:
    """Route after probe re-do artifacts arrived: dispatch solver re-research, or abort."""
    if state.get("routing_action") == "HALT":
        return END
    return "rb_solver_request"


def route_after_rb_solver_await(state: CoursewareState) -> str:
    """Route after re-research artifacts arrived: back to R2, or abort on circuit break."""
    if state.get("routing_action") == "HALT":
        return END
    return "r2_deep_research"
