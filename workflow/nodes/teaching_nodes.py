"""teaching_nodes.py — Teaching and Broker phase nodes (T0-T2) for LangGraph.
Covers project init, architectural planning, broker narrow packets, staging tasks, and gates.

约定：
  * 检查节点只读并验证，不静默改写人工产物；
  * 教学骨架（materials.json / content/*.tex / sec*.tex）只在显式初始化节点
    生成，默认保留已有文件；确需重新生成必须先备份并记录覆盖清单；
  * 所有 broker 子命令的退出码与 stderr 都必须逐项检查，失败即中止并留下
    可诊断记录。
"""

import json
import os
import re
import shutil
import time
from typing import Dict, Any, List, Optional

from ..state import CoursewareState, GateReport
from ..adapters.script_runner import ScriptRunner

MATERIALS_REL = "teaching/materials.json"
CONTENT_DIR_REL = "teaching/content"
SEC_GLOB = "sec*.tex"


def _truncate(text: str, limit: int = 400) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + "..."


def _record_broker_failure(
    failures: List[Dict[str, Any]],
    *,
    command: str,
    task_id: Optional[str],
    exit_code: int,
    stderr: str,
    stdout: str
) -> None:
    failures.append({
        "command": command,
        "task_id": task_id,
        "exit_code": exit_code,
        "stderr": _truncate(stderr, 400),
        "stdout": _truncate(stdout, 200),
    })


def _broker_failure_message(failures: List[Dict[str, Any]]) -> str:
    parts = []
    for f in failures:
        parts.append(
            "%s(task=%s) exit=%s stderr=%s"
            % (f["command"], f.get("task_id") or "-", f["exit_code"], f["stderr"])
        )
    return "broker 子命令失败 %d 项: %s" % (len(failures), " | ".join(parts))


def _regenerate_materials(
    runner: ScriptRunner,
    project_root: str,
    state: CoursewareState,
    history: List[str]
) -> None:
    """生成/同步教学骨架。

    默认（``regenerate_materials`` 未开启）只补缺失文件，绝不覆盖人工已编辑的
    materials.json、教师证明与内容文件。显式开启时才覆盖，且先备份、后覆盖，
    并把覆盖清单写进状态。
    """
    force = bool(state.get("regenerate_materials"))
    if not force:
        code, out, err = runner.run_script("gen_materials_skeleton.py", [project_root])
        if code != 0:
            history.append(
                "T1: gen_materials_skeleton.py 失败（退出 %s）：%s"
                % (code, _truncate(err or out))
            )
        else:
            history.append(
                "T1: 已装配教学骨架（默认保留已有文件，未覆盖人工产物）"
            )
        return

    # 显式重新生成：先备份，再覆盖，并记录覆盖清单
    stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime())
    backup_rel = "teaching/backups/materials-%s" % stamp
    backup_abs = os.path.join(project_root, backup_rel.replace("/", os.sep))
    overwritten: List[str] = []
    try:
        os.makedirs(backup_abs, exist_ok=True)
        for rel in [MATERIALS_REL]:
            src = os.path.join(project_root, rel.replace("/", os.sep))
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(backup_abs, os.path.basename(src)))
                overwritten.append(rel)
        content_abs = os.path.join(project_root, CONTENT_DIR_REL.replace("/", os.sep))
        if os.path.isdir(content_abs):
            shutil.copytree(content_abs, os.path.join(backup_abs, "content"),
                            dirs_exist_ok=True)
            overwritten.append(CONTENT_DIR_REL + "/*")
        if os.path.isdir(project_root):
            for name in sorted(os.listdir(project_root)):
                if not (name.startswith("sec") and name.endswith(".tex")):
                    continue
                src = os.path.join(project_root, name)
                if os.path.isfile(src):
                    shutil.copy2(src, os.path.join(backup_abs, name))
                    overwritten.append(name)
        main_tex = os.path.join(project_root, "main.tex")
        if os.path.isfile(main_tex):
            shutil.copy2(main_tex, os.path.join(backup_abs, "main.tex"))
            overwritten.append("main.tex")
        history.append("T1: 已备份将覆盖的教学产物到 %s" % backup_rel)
    except Exception as exc:
        history.append("T1: 备份失败（%s），放弃覆盖重新生成" % exc)
        return None

    code, out, err = runner.run_script(
        "gen_materials_skeleton.py", [project_root, "--overwrite"]
    )
    if code != 0:
        history.append(
            "T1: gen_materials_skeleton.py --overwrite 失败（退出 %s）：%s"
            % (code, _truncate(err or out))
        )
        return None

    history.append(
        "T1: 已按显式选项重新生成教学骨架（覆盖 %d 项，备份于 %s）"
        % (len(overwritten), backup_rel)
    )
    return {
        "at": stamp,
        "backup_dir": backup_rel,
        "overwritten": overwritten,
    }


def node_t0_init_project(state: CoursewareState) -> Dict[str, Any]:
    """T0: Initialize project workspace from frozen brief and user spec."""
    project_root = state.get("project_root", ".")
    runner = ScriptRunner(project_root)

    code, stdout, stderr = runner.run_script("init_project.py", [project_root])
    passed = (code == 0)

    history = list(state.get("history", []))
    history.append(f"T0: init_project finished. Success={passed}")

    # Write the confirmed user alignment block into config.yaml so that
    # gen_config.py's PHASE 0 verification (user_alignment.confirmed) passes.
    if passed:
        from .human_nodes import write_user_alignment_to_config
        user_spec = state.get("user_spec") or {}
        if user_spec.get("confirmed"):
            if write_user_alignment_to_config(project_root, user_spec):
                history.append("T0: user_alignment confirmed block written into config.yaml")

    return {
        "stage": "T0_READY",
        "routing_action": "PROCEED" if passed else "HALT",
        "error_message": None if passed else _truncate(stderr or stdout or "init_project.py failed"),
        "history": history
    }


def node_t1_architect(state: CoursewareState) -> Dict[str, Any]:
    """T1: Verify the architect plan (produced by the T1 dispatch nodes) and gate it."""
    project_root = state.get("project_root", ".")
    runner = ScriptRunner(project_root)

    report_rel = "teaching/checks/check_architect.json"
    gate = runner.run_gate(
        gate_name="check_architect",
        script_name="check_architect.py",
        args=[project_root, "--json", report_rel],
        report_rel_path=report_rel,
        run_id=state.get("run_id")
    )

    plan_path = os.path.join(project_root, "teaching", "architect-plan.json")
    plan_data = None
    if os.path.exists(plan_path):
        try:
            with open(plan_path, "r", encoding="utf-8-sig") as f:
                plan_data = json.load(f)
        except Exception:
            pass

    history = list(state.get("history", []))
    history.append(f"T1: Architect check status={gate.get('status')}")

    overwrite_log = list(state.get("materials_overwrite_log", []))
    entry = None
    if gate.get("passed"):
        # 教学骨架只在显式初始化节点生成：默认保留已有文件
        entry = _regenerate_materials(runner, project_root, state, history)
    if entry:
        overwrite_log.append(entry)

    return {
        "stage": "T1_ARCHITECTED",
        "architect_plan": plan_data,
        "architect_gate": gate,
        "routing_action": "PROCEED" if gate.get("passed") else "HALT",
        "error_message": None if gate.get("passed") else (
            "T1 architect gate failed: %s"
            % "; ".join(i.get("message", "") for i in gate.get("issues", [])[:3])
        ),
        "materials_overwrite_log": overwrite_log,
        "history": history
    }


def node_t2_broker_issue(state: CoursewareState) -> Dict[str, Any]:
    """T2: Broker creates run and issues narrow packets for each task."""
    project_root = state.get("project_root", ".")
    runner = ScriptRunner(project_root)
    failures: List[Dict[str, Any]] = []

    # 1. Create run via broker
    code, stdout, stderr = runner.run_script("broker.py", [project_root, "create_run"])
    if code != 0:
        history = list(state.get("history", []))
        msg = _truncate(stderr or stdout or "broker create_run failed")
        history.append(f"T2: broker create_run failed (exit {code}): {msg}")
        _record_broker_failure(failures, command="broker.py create_run",
                               task_id=None, exit_code=code,
                               stderr=stderr, stdout=stdout)
        return {
            "stage": "T2_STAGED",
            "routing_action": "HALT",
            "error_message": f"broker create_run failed (exit {code}): {msg}",
            "broker_failures": failures,
            "history": history
        }

    # Extract run_id from broker stdout (format: "[broker] run run-... 已建立...")
    run_id = None
    m = re.search(r"\[broker\] run (run-[A-Za-z0-9_-]+)", stdout + stderr)
    if m:
        run_id = m.group(1)
    else:
        # Fallback: pick the most recently modified run directory under staging/
        staging_dir = os.path.join(project_root, "staging")
        if os.path.isdir(staging_dir):
            runs = [d for d in os.listdir(staging_dir) if os.path.isdir(os.path.join(staging_dir, d))]
            if runs:
                run_id = max(runs, key=lambda d: os.path.getmtime(os.path.join(staging_dir, d)))

    if not run_id:
        run_id = "default_run"

    # Find expected tasks from staging/<run_id>/run.json (broker writes a "tasks" dict)
    expected_tasks = []
    run_json_path = os.path.join(project_root, "staging", run_id, "run.json")
    if os.path.exists(run_json_path):
        try:
            with open(run_json_path, "r", encoding="utf-8-sig") as f:
                rdata = json.load(f)
            tasks_obj = rdata.get("tasks", {})
            if isinstance(tasks_obj, dict):
                expected_tasks = sorted(tasks_obj.keys())
            elif isinstance(tasks_obj, list):
                expected_tasks = sorted(tasks_obj)
        except Exception:
            pass

    if not expected_tasks:
        # Fallback to selected_problems (keep original case: broker uses task-<qid> verbatim)
        expected_tasks = [f"task-{pid}" for pid in state.get("selected_problems", ["P01"])]

    # 2. Issue packets for each task — every subcommand result must be checked
    issued = {}
    for task_id in expected_tasks:
        c, out, err = runner.run_script("broker.py", [project_root, "issue_packet", "--run-id", run_id, "--task-id", task_id])
        issued[task_id] = (c == 0)
        if c != 0:
            _record_broker_failure(failures, command="broker.py issue_packet",
                                   task_id=task_id, exit_code=c,
                                   stderr=err, stdout=out)

    history = list(state.get("history", []))
    history.append(f"T2: Issued broker packets for {expected_tasks} in run {run_id}")

    if failures:
        msg = _broker_failure_message(failures)
        history.append(f"T2: {msg}")
        return {
            "stage": "T2_STAGED",
            "run_id": run_id,
            "expected_tasks": expected_tasks,
            "issued_packets": issued,
            "broker_failures": failures,
            "routing_action": "HALT",
            "error_message": msg,
            "history": history
        }

    return {
        "stage": "T2_STAGED",
        "run_id": run_id,
        "expected_tasks": expected_tasks,
        "issued_packets": issued,
        "broker_failures": [],
        "routing_action": "PROCEED",
        "history": history
    }


def node_t2_student_task(state: CoursewareState) -> Dict[str, Any]:
    """T2: Verify (read-only) that student-task.json exists for every expected task."""
    project_root = state.get("project_root", ".")
    run_id = state.get("run_id", "")
    expected_tasks = state.get("expected_tasks", [])

    staged = {}
    missing = []
    for task_id in expected_tasks:
        task_dir = os.path.join(project_root, "staging", run_id, "tasks", task_id)
        task_file = os.path.join(task_dir, "student-task.json")
        exists = os.path.exists(task_file)
        staged[task_id] = exists
        if not exists:
            missing.append(task_id)

    history = list(state.get("history", []))
    history.append(f"T2: Verified student task staging: {staged}")

    if missing:
        msg = (
            "T2 学生任务产物缺失: %s（须先完成认知工位派发，检查节点不代写产物）"
            % ", ".join(missing)
        )
        history.append(msg)
        return {
            "stage": "T2_STAGED",
            "staged_submissions": staged,
            "routing_action": "HALT",
            "error_message": msg,
            "history": history
        }

    return {
        "stage": "T2_STAGED",
        "staged_submissions": staged,
        "routing_action": "PROCEED",
        "history": history
    }


def _is_fatal_math_issue(issue: Dict[str, Any]) -> bool:
    """判定门禁 issue 是否属实质性数学错误（G2.1 路由修复）。

    约定（双通道）：check 脚本（check_teaching.py / check_solution.py）对
    证伪、条件被改、证明义务缺失、混合模式静默变更等实质性数学错误，在
    finding 中显式写入 category=RESEARCH_BACK/PROVED_FALSE，并在 message
    中保留同字样标记（对齐 check_teaching.py 既有"须走 RESEARCH_BACK"
    文案）。此处 category 子串优先、message 标记兜底；两者都不命中才由
    调用方降级为 REPAIR_TASK。
    """
    if not isinstance(issue, dict):
        return False
    cat = str(issue.get("category") or "")
    msg = str(issue.get("message") or "")
    return ("RESEARCH_BACK" in cat or "PROVED_FALSE" in cat
            or "RESEARCH_BACK" in msg or "PROVED_FALSE" in msg)


def node_t2_broker_submit_and_check(state: CoursewareState) -> Dict[str, Any]:
    """T2: Submit tasks, render via broker, and run all T2 quality gates.

    本节点只做“提交 + 渲染 + 门禁”，不再重新生成教学骨架（避免覆盖人工产物）。
    """
    project_root = state.get("project_root", ".")
    run_id = state.get("run_id", "")
    expected_tasks = state.get("expected_tasks", [])
    runner = ScriptRunner(project_root)
    failures: List[Dict[str, Any]] = []

    # 1. Submit and Render each task via broker — exit codes are authoritative
    for task_id in expected_tasks:
        task_json = os.path.join(project_root, "staging", run_id, "tasks", task_id, "student-task.json")
        if not os.path.exists(task_json):
            _record_broker_failure(
                failures,
                command="broker.py submit_task",
                task_id=task_id,
                exit_code=2,
                stderr="提交文件不存在: %s" % task_json,
                stdout=""
            )
            continue
        rel_input = os.path.relpath(task_json, project_root)
        c_sub, out_sub, err_sub = runner.run_script("broker.py", [
            project_root, "submit_task",
            "--run-id", run_id,
            "--task-id", task_id,
            "--input", rel_input
        ])
        if c_sub != 0:
            _record_broker_failure(failures, command="broker.py submit_task",
                                   task_id=task_id, exit_code=c_sub,
                                   stderr=err_sub, stdout=out_sub)
            continue
        c_ren, out_ren, err_ren = runner.run_script("broker.py", [
            project_root, "render_task",
            "--run-id", run_id,
            "--task-id", task_id
        ])
        if c_ren != 0:
            _record_broker_failure(failures, command="broker.py render_task",
                                   task_id=task_id, exit_code=c_ren,
                                   stderr=err_ren, stdout=out_ren)

    history = list(state.get("history", []))

    if failures:
        msg = _broker_failure_message(failures)
        history.append(f"T2: {msg}")
        return {
            "stage": "T2_CHECKED",
            "t2_gates": {},
            "broker_failures": failures,
            "routing_action": "HALT",
            "error_message": msg,
            "history": history
        }

    history.append("T2: 全部任务的 submit_task / render_task 均成功。")

    # 门禁前预置规范路径（供 check_student_task 与 check_teaching 核验）
    for task_id in expected_tasks:
        qid = task_id.replace("task-", "")
        src_task = os.path.join(project_root, "staging", run_id, "tasks", task_id, "student-task.json")
        src_tex = os.path.join(project_root, "staging", run_id, "rendered", f"{task_id}.tex")
        dst_task = os.path.join(project_root, "teaching", "generated", f"{qid}-student-task.json")
        dst_tex = os.path.join(project_root, "teaching", "content", f"task-{qid}.tex")
        if os.path.isfile(src_task):
            os.makedirs(os.path.dirname(dst_task), exist_ok=True)
            shutil.copy2(src_task, dst_task)
        if os.path.isfile(src_tex):
            os.makedirs(os.path.dirname(dst_tex), exist_ok=True)
            shutil.copy2(src_tex, dst_tex)

    # 2. Run Gates: check_student_task, check_student_visibility, check_teaching, check_numbering
    t2_gates: Dict[str, GateReport] = {}

    gate1 = runner.run_gate("check_student_task", "check_student_task.py", [project_root, "--json", "teaching/checks/check_student_task.json"], "teaching/checks/check_student_task.json", run_id=run_id)
    t2_gates["check_student_task"] = gate1

    gate2 = runner.run_gate("check_student_visibility", "check_student_visibility.py", [project_root, "--json", "teaching/checks/check_student_visibility.json"], "teaching/checks/check_student_visibility.json", run_id=run_id)
    t2_gates["check_student_visibility"] = gate2

    gate3 = runner.run_gate("check_teaching", "check_teaching.py", [project_root, "--json", "teaching/checks/check_teaching.json"], "teaching/checks/check_teaching.json", run_id=run_id)
    t2_gates["check_teaching"] = gate3

    gate4 = runner.run_gate("check_numbering", "check_numbering.py", [project_root, "--json", "teaching/checks/check_numbering.json"], "teaching/checks/check_numbering.json", run_id=run_id)
    t2_gates["check_numbering"] = gate4

    # Check routing action
    all_passed = all(g.get("passed", False) for g in t2_gates.values())

    if all_passed:
        routing_action = "PROCEED"
        active_repair = None
        history.append("T2: All T2 quality gates PASSED.")
    else:
        # Analyze issues to decide whether REPAIR_TASK or RESEARCH_BACK
        has_fatal_math = False
        repair_task = expected_tasks[0] if expected_tasks else None

        for g in t2_gates.values():
            for iss in g.get("issues", []):
                # G2.1：category 与 message 双通道判定（见 _is_fatal_math_issue）
                if _is_fatal_math_issue(iss):
                    has_fatal_math = True

        if has_fatal_math:
            routing_action = "RESEARCH_BACK"
            active_repair = None
            history.append("T2: Fatal mathematical flaw detected. Routing to RESEARCH_BACK.")
        else:
            routing_action = "REPAIR_TASK"
            active_repair = repair_task
            history.append(f"T2: Gate violations found. Routing to REPAIR_TASK for {repair_task}.")

    return {
        "stage": "T2_CHECKED",
        "t2_gates": t2_gates,
        "routing_action": routing_action,
        "active_repair_task": active_repair,
        "broker_failures": [],
        "history": history
    }


def node_t2_check_subquestion_solutions(state: CoursewareState) -> Dict[str, Any]:
    """T2.5: 验证并提交逐小问参考解答，预置规范路径，并运行 check_subquestion_solution 门禁。"""
    project_root = state.get("project_root", ".")
    run_id = state.get("run_id", "")
    expected_tasks = state.get("expected_tasks", [])
    runner = ScriptRunner(project_root)
    failures: List[Dict[str, Any]] = []
    history = list(state.get("history", []))

    # 1. Submit each solution task via broker
    for task_id in expected_tasks:
        sol_json = os.path.join(project_root, "staging", run_id, "tasks", task_id, "solution-task.json")
        if not os.path.exists(sol_json):
            _record_broker_failure(
                failures,
                command="broker.py submit_solution_task",
                task_id=task_id,
                exit_code=2,
                stderr="解答提交文件不存在: %s" % sol_json,
                stdout=""
            )
            continue
        rel_input = os.path.relpath(sol_json, project_root)
        c_sub, out_sub, err_sub = runner.run_script("broker.py", [
            project_root, "submit_solution_task",
            "--run-id", run_id,
            "--task-id", task_id,
            "--input", rel_input
        ])
        if c_sub != 0:
            _record_broker_failure(
                failures,
                command="broker.py submit_solution_task",
                task_id=task_id,
                exit_code=c_sub,
                stderr=err_sub,
                stdout=out_sub
            )

    if failures:
        msg = _broker_failure_message(failures)
        history.append(f"T2.5: {msg}")
        return {
            "stage": "T2_SOLUTIONS_CHECKED",
            "solution_task_gates": {},
            "subquestion_solution_passed": False,
            "broker_failures": failures,
            "routing_action": "HALT",
            "error_message": msg,
            "history": history
        }

    history.append("T2.5: 全部任务的 submit_solution_task 验证成功。")

    # 2. 门禁前预置规范路径
    for task_id in expected_tasks:
        qid = task_id.replace("task-", "")
        src_sol = os.path.join(project_root, "staging", run_id, "tasks", task_id, "solution-task.json")
        dst_sol = os.path.join(project_root, "teaching", "generated", f"{qid}-solution-task.json")
        if os.path.isfile(src_sol):
            os.makedirs(os.path.dirname(dst_sol), exist_ok=True)
            shutil.copy2(src_sol, dst_sol)

    # 3. 运行专用门禁：check_subquestion_solution
    solution_gates: Dict[str, GateReport] = {}
    gate_sol = runner.run_gate(
        "check_subquestion_solution",
        "check_subquestion_solution.py",
        [project_root, "--run-id", run_id, "--json", "teaching/checks/check_subquestion_solution.json"],
        "teaching/checks/check_subquestion_solution.json",
        run_id=run_id
    )
    solution_gates["check_subquestion_solution"] = gate_sol

    passed = gate_sol.get("passed", False)
    if passed:
        routing_action = "PROCEED"
        active_repair = None
        history.append("T2.5: 逐小问参考解答门禁 check_subquestion_solution PASSED。")
    else:
        # 分析定位失败小问或任务
        repair_task = expected_tasks[0] if expected_tasks else None
        for iss in gate_sol.get("issues", []):
            msg = iss.get("message", "")
            for tid in expected_tasks:
                if tid in msg or ("题目 %s" % tid.replace("task-", "")) in msg:
                    repair_task = tid
                    break
        routing_action = "REPAIR_TASK"
        active_repair = repair_task
        history.append(f"T2.5: 逐小问解答存在质量违规，路由至 REPAIR_TASK for {repair_task}。")

    return {
        "stage": "T2_SOLUTIONS_CHECKED",
        "solution_task_gates": solution_gates,
        "subquestion_solution_passed": passed,
        "routing_action": routing_action,
        "active_repair_task": active_repair,
        "broker_failures": [],
        "history": history
    }
