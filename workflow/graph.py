"""graph.py — StateGraph assembly for Courseware Pipeline.
Assembles all nodes, linear transitions, conditional edges, and persistence checkpointers.

责任边界：
  * 确定性节点（门禁、broker、编译、交付）由图自动执行；
  * 认知工位（架构计划、学生任务、重新研学、定点返修）由
    :mod:`workflow.nodes.agent_nodes` 派发——注入了认知 worker 就地执行，
    否则抛出带 checkpoint 的人工中断，等待产物后再继续；
  * 因此“research-back / repair”不再是原样重跑：没有产物变化就中止并报错。
"""

from typing import Optional, Any
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from .state import CoursewareState
from .nodes import (
    node_r1_screen_problems,
    node_r2_deep_research,
    node_r4_freeze_brief,
    node_phase0_breakpoint,
    node_t0_init_project,
    node_t1_request_architect,
    node_t1_await_architect,
    node_t1_architect,
    node_t2_broker_issue,
    node_t2_request_student_task,
    node_t2_await_student_task,
    node_t2_review_request,
    node_t2_review_await,
    node_t2_student_task,
    node_t2_broker_submit_and_check,
    node_t2_request_subquestion_solutions,
    node_t2_await_subquestion_solutions,
    node_t2_check_subquestion_solutions,
    node_t3_latex_build,
    node_t4_promote_and_deliver,
    node_repair_small_output,
    node_repair_small_output_await,
    node_research_back,
    node_rb_probe_request,
    node_rb_probe_await,
    node_rb_solver_request,
    node_rb_solver_await
)
from .edges import (
    route_after_research,
    route_after_freeze,
    route_after_phase0,
    route_after_init,
    route_after_t1_await,
    route_after_architect,
    route_after_t2_await,
    route_after_t2_review_await,
    route_after_t2_verify,
    route_after_t2_check,
    route_after_subquestion_solution_await,
    route_after_subquestion_solution_gate,
    route_after_build,
    route_after_repair,
    route_after_repair_await,
    route_after_research_back,
    route_after_rb_probe_await,
    route_after_rb_solver_await
)


def create_courseware_graph(checkpointer: Optional[Any] = None) -> Any:
    """Build and compile the courseware StateGraph."""
    workflow = StateGraph(CoursewareState)

    # 1. Register all nodes
    workflow.add_node("r1_screen_problems", node_r1_screen_problems)
    workflow.add_node("r2_deep_research", node_r2_deep_research)
    workflow.add_node("r4_freeze_brief", node_r4_freeze_brief)
    workflow.add_node("phase0_breakpoint", node_phase0_breakpoint)
    workflow.add_node("t0_init_project", node_t0_init_project)

    # T1: 派发架构计划 -> 等待产物 -> 门禁校验
    workflow.add_node("t1_request_architect", node_t1_request_architect)
    workflow.add_node("t1_await_architect", node_t1_await_architect)
    workflow.add_node("t1_architect", node_t1_architect)

    # T2: 建立 run/签发窄包 -> 派发学生任务 -> 等待产物 -> 只读核验 -> 提交渲染与门禁
    workflow.add_node("t2_broker_issue", node_t2_broker_issue)
    workflow.add_node("t2_request_student_task", node_t2_request_student_task)
    workflow.add_node("t2_await_student_task", node_t2_await_student_task)
    # T2 自审（可选，config.yaml enable_t2_self_review=true 时启用）
    workflow.add_node("t2_review_request", node_t2_review_request)
    workflow.add_node("t2_review_await", node_t2_review_await)
    workflow.add_node("t2_student_task", node_t2_student_task)
    workflow.add_node("t2_broker_submit_and_check", node_t2_broker_submit_and_check)

    # T2.5: 逐小问参考解答派发、等待与门禁
    workflow.add_node("t2_request_subquestion_solutions", node_t2_request_subquestion_solutions)
    workflow.add_node("t2_await_subquestion_solutions", node_t2_await_subquestion_solutions)
    workflow.add_node("t2_check_subquestion_solutions", node_t2_check_subquestion_solutions)

    workflow.add_node("t3_latex_build", node_t3_latex_build)
    workflow.add_node("t4_promote_and_deliver", node_t4_promote_and_deliver)

    # 返修与研究打回：先派发具体修改任务，等待产物变化，再重跑对应门禁
    workflow.add_node("repair_small_output", node_repair_small_output)
    workflow.add_node("repair_await", node_repair_small_output_await)
    workflow.add_node("research_back", node_research_back)
    # Research-Back 两阶段拆分：先重做探针/坑点，再基于修正结果重新研学
    workflow.add_node("rb_probe_request", node_rb_probe_request)
    workflow.add_node("rb_probe_await", node_rb_probe_await)
    workflow.add_node("rb_solver_request", node_rb_solver_request)
    workflow.add_node("rb_solver_await", node_rb_solver_await)

    # 2. Register flow edges
    workflow.add_edge(START, "r1_screen_problems")
    workflow.add_edge("r1_screen_problems", "r2_deep_research")

    # R2 -> R4 or Research Back
    workflow.add_conditional_edges(
        "r2_deep_research",
        route_after_research,
        {
            "r4_freeze_brief": "r4_freeze_brief",
            "research_back": "research_back",
            END: END
        }
    )

    # R4 -> Phase 0 Breakpoint
    workflow.add_conditional_edges(
        "r4_freeze_brief",
        route_after_freeze,
        {
            "phase0_breakpoint": "phase0_breakpoint",
            END: END
        }
    )

    # Phase 0 Breakpoint -> T0 Init
    workflow.add_conditional_edges(
        "phase0_breakpoint",
        route_after_phase0,
        {
            "t0_init_project": "t0_init_project",
            "phase0_breakpoint": "phase0_breakpoint"
        }
    )

    # T0 -> T1 architect dispatch
    workflow.add_conditional_edges(
        "t0_init_project",
        route_after_init,
        {
            "t1_request_architect": "t1_request_architect",
            END: END
        }
    )

    # T1 dispatch -> wait -> gate
    workflow.add_edge("t1_request_architect", "t1_await_architect")
    workflow.add_conditional_edges(
        "t1_await_architect",
        route_after_t1_await,
        {
            "t1_architect": "t1_architect",
            END: END
        }
    )

    # T1 -> T2 Broker Issue
    workflow.add_conditional_edges(
        "t1_architect",
        route_after_architect,
        {
            "t2_broker_issue": "t2_broker_issue",
            END: END
        }
    )

    # T2 Broker Issue -> Student Task dispatch -> wait -> verify -> submit & check
    workflow.add_edge("t2_broker_issue", "t2_request_student_task")
    workflow.add_edge("t2_request_student_task", "t2_await_student_task")
    workflow.add_conditional_edges(
        "t2_await_student_task",
        route_after_t2_await,
        {
            "t2_review_request": "t2_review_request",
            "t2_student_task": "t2_student_task",
            END: END
        }
    )
    workflow.add_edge("t2_review_request", "t2_review_await")
    workflow.add_conditional_edges(
        "t2_review_await",
        route_after_t2_review_await,
        {
            "t2_student_task": "t2_student_task",
            END: END
        }
    )
    workflow.add_conditional_edges(
        "t2_student_task",
        route_after_t2_verify,
        {
            "t2_broker_submit_and_check": "t2_broker_submit_and_check",
            END: END
        }
    )

    # T2 Quality Gates -> T2.5 Subquestion Solutions / Repair / Research Back
    workflow.add_conditional_edges(
        "t2_broker_submit_and_check",
        route_after_t2_check,
        {
            "t2_request_subquestion_solutions": "t2_request_subquestion_solutions",
            "repair_small_output": "repair_small_output",
            "research_back": "research_back",
            END: END
        }
    )

    # T2.5 Subquestion Solutions: dispatch -> wait -> check -> T3 Build / Repair
    workflow.add_edge("t2_request_subquestion_solutions", "t2_await_subquestion_solutions")
    workflow.add_conditional_edges(
        "t2_await_subquestion_solutions",
        route_after_subquestion_solution_await,
        {
            "t2_check_subquestion_solutions": "t2_check_subquestion_solutions",
            END: END
        }
    )
    workflow.add_conditional_edges(
        "t2_check_subquestion_solutions",
        route_after_subquestion_solution_gate,
        {
            "t3_latex_build": "t3_latex_build",
            "repair_small_output": "repair_small_output",
            END: END
        }
    )

    # T3 Build -> T4 Deliver or Repair
    workflow.add_conditional_edges(
        "t3_latex_build",
        route_after_build,
        {
            "t4_promote_and_deliver": "t4_promote_and_deliver",
            "repair_small_output": "repair_small_output",
            END: END
        }
    )

    # Repair: dispatch -> wait -> re-run gates
    workflow.add_conditional_edges(
        "repair_small_output",
        route_after_repair,
        {
            "repair_await": "repair_await",
            END: END
        }
    )
    workflow.add_conditional_edges(
        "repair_await",
        route_after_repair_await,
        {
            "t2_broker_submit_and_check": "t2_broker_submit_and_check",
            "t2_check_subquestion_solutions": "t2_check_subquestion_solutions",
            END: END
        }
    )

    # Research Back: 撤销冻结 -> 探针重做 -> 重新研学 -> R2 Deep Research
    workflow.add_conditional_edges(
        "research_back",
        route_after_research_back,
        {
            "rb_probe_request": "rb_probe_request",
            END: END
        }
    )
    workflow.add_edge("rb_probe_request", "rb_probe_await")
    workflow.add_conditional_edges(
        "rb_probe_await",
        route_after_rb_probe_await,
        {
            "rb_solver_request": "rb_solver_request",
            END: END
        }
    )
    workflow.add_edge("rb_solver_request", "rb_solver_await")
    workflow.add_conditional_edges(
        "rb_solver_await",
        route_after_rb_solver_await,
        {
            "r2_deep_research": "r2_deep_research",
            END: END
        }
    )

    # T4 -> END
    workflow.add_edge("t4_promote_and_deliver", END)

    # 3. Checkpointing for persistence and pause/resume
    saver = checkpointer if checkpointer is not None else MemorySaver()
    return workflow.compile(checkpointer=saver)
