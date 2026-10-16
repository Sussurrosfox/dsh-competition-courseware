"""CoursewareState: Strongly-typed state definition for LangGraph Courseware Pipeline.
"""

from typing import TypedDict, List, Dict, Any, Optional, Literal


class GateIssue(TypedDict, total=False):
    category: str
    severity: Literal["HIGH_RISK", "WARN", "INFO"]
    message: str
    hash: Optional[str]
    file: Optional[str]


class GateReport(TypedDict, total=False):
    gate_name: str
    status: Literal["PASS", "REVIEW", "REJECT", "ERROR"]
    passed: bool
    issues: List[GateIssue]
    raw_report: Dict[str, Any]
    # --- 本轮可追溯性（防止把上一次运行的报告当成本轮证据）---
    exit_code: int
    report_path: Optional[str]
    report_generated_at: Optional[str]   # 报告文件 mtime（ISO，本地时区）
    report_is_fresh: bool                # 报告是否确由本次调用生成
    run_id: Optional[str]


class CoursewareState(TypedDict, total=False):
    # --- Context & Environment ---
    project_root: str
    run_id: str
    stage: Literal[
        "INIT",
        "R0_CANDIDATE",
        "R1_SCORED",
        "R2_RESEARCHED",
        "R4_FROZEN",
        "PHASE0_BREAKPOINT",
        "T0_READY",
        "T1_ARCHITECTED",
        "T1_AGENT_PENDING",
        "T2_STAGED",
        "T2_CHECKED",
        "T2_AGENT_PENDING",
        "T2_SOLUTIONS_PENDING",
        "T2_SOLUTIONS_CHECKED",
        "T3_BUILT",
        "T4_DELIVERED",
        "T4_FAILED",
        "REPAIR_LOOP",
        "RESEARCH_BACK_LOOP",
        "ABORTED"
    ]

    # --- R0 & R1: Problems & Screening ---
    candidate_problems: List[str]
    selected_problems: List[str]
    problem_scores: Dict[str, Any]
    # 界面/CLI 选题与冻结 brief 的核对结果（不一致即拒绝推进）
    brief_selected_problem_ids: List[str]
    selection_mismatch: Optional[str]

    # --- R2: Deep Research Pipeline ---
    research_results: Dict[str, Dict[str, Any]]  # pid -> {probe, solution, audit}
    research_gate: GateReport

    # --- R4: Brief & Freeze ---
    # 注：R3（学生障碍/补充材料研究）不是流水线状态，而是默认关闭的条件
    # 执行环节（config.yaml enable_obstacle_study，默认 false），图中无
    # R3 节点；本状态模型不含 R3 字段。
    teaching_brief: Dict[str, Any]
    brief_frozen: bool
    freeze_digest: Optional[str]

    # --- Phase 0: Human Confirmation (Interrupt) ---
    user_spec: Optional[Dict[str, Any]]
    human_interrupted: bool

    # --- T0 & T1: Project & Architecture ---
    architect_plan: Optional[Dict[str, Any]]
    architect_gate: GateReport

    # --- T2: Broker & Staged Tasks ---
    expected_tasks: List[str]
    issued_packets: Dict[str, Any]      # task_id -> packet info
    staged_submissions: Dict[str, Any]  # task_id -> json path / content
    rendered_tasks: Dict[str, Any]      # task_id -> tex path / digest
    t2_gates: Dict[str, GateReport]     # check_student_task, check_visibility, check_teaching
    # T2 学生任务自审开关（config.yaml enable_t2_self_review，默认开启，
    # 显式 false 才关闭；自审持有 teacher-solution packet 视野）
    t2_self_review: bool
    # broker 子命令执行结果（签发/提交/渲染）：任一失败都必须留下可诊断记录
    broker_failures: List[Dict[str, Any]]

    # --- T2.5: Subquestion Solutions ---
    solution_packets_issued: Dict[str, Any]
    solution_task_gates: Dict[str, GateReport]
    subquestion_solution_passed: bool

    # --- Agent / Antigravity 派发（认知工位）---
    # 待办产物派发单：谁、做什么、产出哪些文件、派发前这些文件的摘要
    pending_dispatch: Optional[Dict[str, Any]]
    # 批次派发：每题/每子任务一份派发单（pending_dispatch 保留用于单派发）
    pending_dispatch_batch: Optional[List[Dict[str, Any]]]
    # 批次所属阶段标识，如 "T2_STUDENT_TASK" / "RB_PROBE" / "RB_SOLVER"
    batch_stage: Optional[str]
    # dispatch_id -> {status, produced, missing, unchanged, detail}
    dispatch_results: Dict[str, Any]
    # 是否注入了可用的认知 worker（LLM/子 Agent）；False 时只能走人工中断
    worker_available: bool
    # research-back 两阶段进度标记：probe_done / solver_done
    rb_phase: Optional[str]

    # --- T3: Build ---
    build_results: Dict[str, Any]
    build_passed: bool

    # --- T4: Release & Gates ---
    deliver_teacher: Optional[bool]     # 是否交付 teacher.tex 与 teacher.pdf（默认关闭/False）
    run_gates_summary: Dict[str, Any]
    promoted: bool
    delivery_manifest: Optional[Dict[str, Any]]
    # deliver 门禁独立结论（与 promote、manifest 校验分开记录）
    deliver_gate: GateReport
    # {promote: {...}, deliver: {...}, manifest: {...}} 逐项可诊断结果
    delivery_result: Dict[str, Any]

    # --- Flow Control & Diagnostics ---
    routing_action: Literal["PROCEED", "REPAIR_TASK", "RESEARCH_BACK", "HALT"]
    active_repair_task: Optional[str]
    error_message: Optional[str]
    retry_count: int
    research_back_count: int

    # --- 教学骨架重新生成（显式 opt-in，默认保护人工产物）---
    regenerate_materials: bool
    materials_overwrite_log: List[Dict[str, Any]]

    # --- Execution Trace ---
    history: List[str]
