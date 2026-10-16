"""pipeline_manager.py — Core engine and adapter connecting LangGraph to the UI workbench.
Handles workspace preparation across decoupled input/output directories, graph execution streams,
interrupt states, checkpoints, gate reports, and deliverables.
"""

import json
import os
import time
import shutil
import glob
from typing import Dict, Any, List, Optional, Generator, Tuple
from langgraph.types import Command

from ..graph import create_courseware_graph
from ..state import CoursewareState
from ..nodes import (
    node_r1_screen_problems,
    node_r2_deep_research,
    node_r4_freeze_brief,
    node_t0_init_project,
    node_t1_architect,
    node_t2_broker_issue,
    node_t2_student_task,
    node_t2_broker_submit_and_check,
    node_t2_request_subquestion_solutions,
    node_t2_await_subquestion_solutions,
    node_t2_check_subquestion_solutions,
    node_rb_probe_request,
    node_rb_probe_await,
    node_rb_solver_request,
    node_rb_solver_await,
    node_t3_latex_build,
    node_t4_promote_and_deliver,
    check_selection_against_brief
)


class PipelineManager:
    def __init__(
        self,
        default_input_dir: Optional[str] = None,
        default_output_dir: Optional[str] = None,
        default_project_root: Optional[str] = None,
        engine_task_root: Optional[str] = None,
        checkpoint_db: Optional[str] = None
    ):
        if default_project_root:
            default_input_dir = default_input_dir or default_project_root
            default_output_dir = default_output_dir or default_project_root

        # 1. Locate the engine directory (where skills-langgraph itself lives)
        cur = os.path.abspath(os.path.dirname(__file__))
        self.engine_root = os.path.abspath(os.path.join(cur, "..", ".."))
        # AGY_TASK.md 的写入根：默认与 engine_root 一致；测试可注入独立目录，
        # 避免覆盖工作区里真实的启动任务书
        self.engine_task_root = os.path.abspath(engine_task_root) if engine_task_root else self.engine_root
        self.scripts_dir = os.path.join(self.engine_root, "scripts")
        self.schemas_dir = os.path.join(self.engine_root, "schemas")
        self.templates_dir = os.path.join(self.engine_root, "templates")
        self.prompts_dir = os.path.join(self.engine_root, "prompts")

        # 2. Intelligent initial paths
        if default_input_dir:
            self.default_input_dir = os.path.abspath(default_input_dir)
        else:
            # If current parent folder has research or teaching, default to parent, otherwise cwd
            parent_dir = os.path.abspath(os.path.join(self.engine_root, ".."))
            if os.path.exists(os.path.join(parent_dir, "research")) or os.path.exists(os.path.join(parent_dir, "teaching")):
                self.default_input_dir = parent_dir
            else:
                self.default_input_dir = os.getcwd()

        # Default output workspace: the "self" subfolder under the input directory
        # (created on demand by prepare_workspace when the pipeline runs)
        self.default_output_dir = os.path.abspath(default_output_dir) if default_output_dir else os.path.join(self.default_input_dir, "self")

        # 3. LangGraph engine instances
        self.checkpoint_db = os.path.abspath(checkpoint_db) if checkpoint_db else None
        self.checkpointer = self._make_checkpointer()
        self.graph = create_courseware_graph(checkpointer=self.checkpointer)
        self.active_sessions: Dict[str, Dict[str, Any]] = {}
        # thread_id -> 本轮启动时刻（用于识别“上一次运行的门禁报告”）
        self._run_started_at: Dict[str, float] = {}

    def _make_checkpointer(self):
        """UI 用持久化 checkpointer：刷新页面/重启进程后仍可按 thread_id 恢复。"""
        # 未显式指定时落盘到引擎任务根的 .langgraph/，避免重启丢失中断状态
        from ..checkpoints import SqliteSaver
        db = self.checkpoint_db or os.path.join(
            self.engine_task_root, ".langgraph", "ui_checkpoints.sqlite"
        )
        return SqliteSaver(db)

    def prepare_workspace(self, input_dir: str, output_dir: str) -> Tuple[bool, str]:
        """Prepare output_dir from input_dir and template assets if separate."""
        inp = os.path.abspath(input_dir)
        out = os.path.abspath(output_dir)

        if not os.path.exists(inp):
            return False, f"输入目录不存在: {inp}"

        os.makedirs(out, exist_ok=True)

        if inp == out:
            return True, "输入目录与输出工作区一致（原地构建模式）"

        # 1. Sync research folder (problems, brief, freeze, reviews)
        inp_research = os.path.join(inp, "research")
        out_research = os.path.join(out, "research")
        if os.path.isdir(inp_research):
            shutil.copytree(inp_research, out_research, dirs_exist_ok=True)

        # 2. Sync sources folder
        inp_sources = os.path.join(inp, "sources")
        out_sources = os.path.join(out, "sources")
        if os.path.isdir(inp_sources):
            shutil.copytree(inp_sources, out_sources, dirs_exist_ok=True)

        # 3. Sync teaching folder if present
        inp_teaching = os.path.join(inp, "teaching")
        out_teaching = os.path.join(out, "teaching")
        if os.path.isdir(inp_teaching):
            shutil.copytree(inp_teaching, out_teaching, dirs_exist_ok=True)

        # 4. Sync markdown problem files (*.md) and TeX configurations from input
        for fname in os.listdir(inp):
            fpath = os.path.join(inp, fname)
            if os.path.isfile(fpath) and (fname.endswith(".md") or fname.endswith(".tex") or fname in ["config.yaml", "config.tex"]):
                dest = os.path.join(out, fname)
                if not os.path.exists(dest):
                    try:
                        shutil.copy2(fpath, dest)
                    except Exception:
                        pass

        # 5. Ensure core LaTeX template headers exist in output if not provided
        for tname in ["header.tex", "footer.tex", "config.yaml", "config-class.tex", "main.tex"]:
            dest = os.path.join(out, tname)
            if not os.path.exists(dest):
                tpl_path = os.path.join(self.templates_dir, tname)
                if os.path.isfile(tpl_path):
                    try:
                        shutil.copy2(tpl_path, dest)
                    except Exception:
                        pass

        return True, f"工作区就绪：输入源[{os.path.basename(inp)}]已映射至输出区[{os.path.basename(out)}]"

    def scan_workspace(self, input_dir: str, output_dir: Optional[str] = None) -> Dict[str, Any]:
        """Scan input directory for problems and output directory for brief, gates, and deliverables."""
        inp = os.path.abspath(input_dir) if input_dir else ""
        # Default output workspace = "self" subfolder under the input directory
        out = os.path.abspath(output_dir) if output_dir else (os.path.join(inp, "self") if inp else "")

        problems = []
        if inp and os.path.isdir(inp):
            # A. Check research/problems
            prob_dir = os.path.join(inp, "research", "problems")
            if os.path.isdir(prob_dir):
                problems = sorted([d for d in os.listdir(prob_dir) if os.path.isdir(os.path.join(prob_dir, d))])

            # B. If no research/problems, check for markdown files (e.g. 1.md, P441.md)
            if not problems:
                md_probs = set()
                for f in os.listdir(inp):
                    if f.endswith(".md") and not f.endswith("_solution.md") and not f.startswith("README") and not f.startswith("SKILL"):
                        base = os.path.splitext(f)[0]
                        md_probs.add(base)
                if md_probs:
                    problems = sorted(list(md_probs))

        # Also check output_dir if different and input had none
        if not problems and out and os.path.isdir(out) and out != inp:
            out_prob_dir = os.path.join(out, "research", "problems")
            if os.path.isdir(out_prob_dir):
                problems = sorted([d for d in os.listdir(out_prob_dir) if os.path.isdir(os.path.join(out_prob_dir, d))])

        # Brief & Freeze (check output first, fallback to input)
        brief_data = None
        freeze_data = None
        for base in [out, inp]:
            if not base or not os.path.isdir(base):
                continue
            b_path = os.path.join(base, "research", "brief.json")
            f_path = os.path.join(base, "research", "freeze.json")
            if not brief_data and os.path.exists(b_path):
                try:
                    with open(b_path, "r", encoding="utf-8-sig") as f:
                        brief_data = json.load(f)
                except Exception:
                    pass
            if not freeze_data and os.path.exists(f_path):
                try:
                    with open(f_path, "r", encoding="utf-8-sig") as f:
                        freeze_data = json.load(f)
                except Exception:
                    pass

        # User spec in output (or input)
        user_spec_data = None
        for base in [out, inp]:
            if not base or not os.path.isdir(base):
                continue
            u_path = os.path.join(base, "teaching", "user-spec.json")
            if not user_spec_data and os.path.exists(u_path):
                try:
                    with open(u_path, "r", encoding="utf-8-sig") as f:
                        user_spec_data = json.load(f)
                except Exception:
                    pass

        # Artifacts in output
        student_pdf = os.path.join(out, "student.pdf") if out else ""
        teacher_pdf = os.path.join(out, "teacher.pdf") if out else ""
        manifest_path = os.path.join(out, "release", "manifest.json") if out else ""
        manifest_data = None
        if manifest_path and os.path.exists(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8-sig") as f:
                    manifest_data = json.load(f)
            except Exception:
                pass

        return {
            "input_dir": inp,
            "output_dir": out,
            "is_same_dir": (inp == out),
            "problems": problems,
            "has_brief": brief_data is not None,
            "brief_title": brief_data.get("main_title") if brief_data else None,
            "is_frozen": freeze_data is not None and bool(freeze_data.get("brief_digest") or freeze_data.get("freeze_digest")),
            "freeze_revision": freeze_data.get("revision") if freeze_data else None,
            "freeze_digest": (freeze_data.get("brief_digest") or freeze_data.get("freeze_digest")) if freeze_data else None,
            "user_spec": user_spec_data,
            "student_pdf_exists": bool(student_pdf and os.path.exists(student_pdf)),
            "teacher_pdf_exists": bool(teacher_pdf and os.path.exists(teacher_pdf)),
            "manifest_exists": manifest_data is not None,
            "manifest_data": manifest_data
        }

    def scan_project(self, project_root: str) -> Dict[str, Any]:
        """Backward-compatible scan method."""
        return self.scan_workspace(input_dir=project_root, output_dir=project_root)

    def run_pipeline_stream(
        self,
        input_dir: str,
        output_dir: Optional[str] = None,
        selected_problems: Optional[List[str]] = None,
        user_spec: Optional[Dict[str, Any]] = None,
        deliver_teacher: bool = False,
        thread_id: str = "ui_session"
    ) -> Generator[Dict[str, Any], None, None]:
        """Stream execution of courseware pipeline on prepared output_dir."""
        inp = os.path.abspath(input_dir)
        # Default output workspace = "self" subfolder under the input directory
        out = os.path.abspath(output_dir) if output_dir else os.path.join(inp, "self")

        # Prepare workspace if separate
        ok, prep_msg = self.prepare_workspace(inp, out)
        if not ok:
            yield {
                "event": "error",
                "thread_id": thread_id,
                "stage": "ERROR",
                "message": prep_msg,
                "history": [f"❌ 工作区准备失败: {prep_msg}"]
            }
            return

        config = {"configurable": {"thread_id": thread_id}}
        # 记录本轮启动时刻：门禁报告早于它即“上一次运行的遗留结论”
        self._run_started_at[thread_id] = time.time()
        init_state: CoursewareState = {
            "project_root": out,
            "stage": "INIT",
            "selected_problems": selected_problems or [],
            "user_spec": user_spec,
            "deliver_teacher": deliver_teacher,
            "history": [
                f"PipelineManager: Starting execution for thread '{thread_id}'",
                f"输入目录: {inp}",
                f"输出工作区: {out}"
            ]
        }

        try:
            for update in self.graph.stream(init_state, config=config, stream_mode="updates"):
                if "__interrupt__" in update:
                    interrupt_tuple = update["__interrupt__"]
                    intr = interrupt_tuple[0] if interrupt_tuple else None
                    intr_val = intr.value if intr else {}
                    yield {
                        "event": "interrupt",
                        "thread_id": thread_id,
                        "stage": "PHASE0_BREAKPOINT",
                        "node": "phase0_breakpoint",
                        "interrupt_data": intr_val,
                        "message": intr_val.get("description", "流程在 Phase 0 断点挂起，等待确认"),
                        "history": [f"⏸️ 流水线在 Phase 0 断点挂起: {intr_val.get('description')}"]
                    }
                    return

                for node_name, node_output in update.items():
                    stage = node_output.get("stage", "RUNNING")
                    action = node_output.get("routing_action", "PROCEED")
                    hist = node_output.get("history", [])
                    yield {
                        "event": "node_complete",
                        "thread_id": thread_id,
                        "node": node_name,
                        "stage": stage,
                        "routing_action": action,
                        "output": node_output,
                        "history": hist,
                        "message": f"节点 [{node_name}] 完成，状态: {stage}"
                    }

            # Check final state after stream finishes
            state_snap = self.graph.get_state(config)
            if state_snap.tasks and any(t.interrupts for t in state_snap.tasks):
                for t in state_snap.tasks:
                    for i in t.interrupts:
                        yield {
                            "event": "interrupt",
                            "thread_id": thread_id,
                            "stage": "PHASE0_BREAKPOINT",
                            "node": "phase0_breakpoint",
                            "interrupt_data": i.value,
                            "message": i.value.get("description", "流程在 Phase 0 断点挂起，等待确认"),
                            "history": ["⏸️ 流水线在 Phase 0 断点挂起"]
                        }
                        return

            final_vals = state_snap.values or {}
            yield {
                "event": "finish",
                "thread_id": thread_id,
                "stage": final_vals.get("stage", "COMPLETED"),
                "routing_action": final_vals.get("routing_action", "PROCEED"),
                "history": final_vals.get("history", []),
                "message": f"流水线运行结束。最终阶段: {final_vals.get('stage')}"
            }

        except Exception as e:
            yield {
                "event": "error",
                "thread_id": thread_id,
                "stage": "ERROR",
                "message": f"执行发生异常: {str(e)}",
                "history": [f"❌ 运行错误: {str(e)}"]
            }

    def resume_pipeline_stream(
        self,
        user_response: Dict[str, Any],
        thread_id: str = "ui_session"
    ) -> Generator[Dict[str, Any], None, None]:
        """Stream resume execution from Phase 0 interrupt."""
        config = {"configurable": {"thread_id": thread_id}}
        resume_cmd = Command(resume=user_response)

        try:
            for update in self.graph.stream(resume_cmd, config=config, stream_mode="updates"):
                if "__interrupt__" in update:
                    interrupt_tuple = update["__interrupt__"]
                    intr = interrupt_tuple[0] if interrupt_tuple else None
                    intr_val = intr.value if intr else {}
                    yield {
                        "event": "interrupt",
                        "thread_id": thread_id,
                        "stage": "PHASE0_BREAKPOINT",
                        "node": "phase0_breakpoint",
                        "interrupt_data": intr_val,
                        "message": intr_val.get("description", "流程在 Phase 0 断点挂起，等待确认"),
                        "history": ["⏸️ 流水线在 Phase 0 断点挂起"]
                    }
                    return

                for node_name, node_output in update.items():
                    stage = node_output.get("stage", "RUNNING")
                    action = node_output.get("routing_action", "PROCEED")
                    hist = node_output.get("history", [])
                    yield {
                        "event": "node_complete",
                        "thread_id": thread_id,
                        "node": node_name,
                        "stage": stage,
                        "routing_action": action,
                        "output": node_output,
                        "history": hist,
                        "message": f"节点 [{node_name}] 完成，状态: {stage}"
                    }

            state_snap = self.graph.get_state(config)
            final_vals = state_snap.values or {}
            yield {
                "event": "finish",
                "thread_id": thread_id,
                "stage": final_vals.get("stage", "COMPLETED"),
                "routing_action": final_vals.get("routing_action", "PROCEED"),
                "history": final_vals.get("history", []),
                "message": f"流水线恢复并推进结束。最终阶段: {final_vals.get('stage')}"
            }

        except Exception as e:
            yield {
                "event": "error",
                "thread_id": thread_id,
                "stage": "ERROR",
                "message": f"恢复执行发生异常: {str(e)}",
                "history": [f"❌ 恢复运行错误: {str(e)}"]
            }

    def get_current_state(self, thread_id: str = "ui_session") -> Dict[str, Any]:
        """Inspect current state snapshot and pending interrupts."""
        config = {"configurable": {"thread_id": thread_id}}
        snap = self.graph.get_state(config)
        vals = snap.values or {}
        has_interrupt = bool(snap.tasks and any(t.interrupts for t in snap.tasks))
        interrupt_info = None
        if has_interrupt:
            for t in snap.tasks:
                for intr in t.interrupts:
                    interrupt_info = intr.value

        return {
            "stage": vals.get("stage", "NOT_STARTED"),
            "next": snap.next,
            "has_interrupt": has_interrupt,
            "interrupt_info": interrupt_info,
            "routing_action": vals.get("routing_action", "NONE"),
            "history": vals.get("history", []),
            "values": vals
        }

    def check_selection(self, workspace_root: str,
                        selected_problems: Optional[List[str]]) -> Dict[str, Any]:
        """启动前核对界面选题与冻结 brief 是否一致。

        冻结 brief 是权威输入（broker create_run 只按 brief 建立任务清单），
        不一致时 UI 必须明确拒绝，而不是让用户以为勾选生效了。
        """
        ok, brief_ids, mismatch = check_selection_against_brief(
            workspace_root, selected_problems or []
        )
        return {
            "ok": ok,
            "brief_selected_problem_ids": brief_ids or [],
            "mismatch": mismatch,
        }

    def get_gate_reports(
        self,
        workspace_root: str,
        thread_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Collect and parse all gate check reports across research and teaching.

        “本轮未生成”与“通过/失败”是两件事：报告缺失记 ``NOT_RUN``；报告存在但
        早于本轮启动时刻记 ``STALE``（上一次运行的遗留结论），不得当成本轮证据。
        """
        p_root = os.path.abspath(workspace_root) if workspace_root else ""
        reports = []
        run_started_at = self._run_started_at.get(thread_id) if thread_id else None

        gate_configs = [
            {"gate_id": "score_problem_value", "label": "R1: 题目价值评分", "rel_path": "research/checks/score_problem_value.json", "stage": "R1"},
            {"gate_id": "check_research", "label": "R2: 深度研学检查", "rel_path": "research/checks/check_research.json", "stage": "R2"},
            {"gate_id": "check_solution", "label": "R2: 题解完整度检查", "rel_path": "research/checks/check_solution.json", "stage": "R2"},
            {"gate_id": "freeze", "label": "R4: 冻结交接包认证", "rel_path": "research/freeze.json", "stage": "R4"},
            {"gate_id": "check_architect", "label": "T1: 双轨架构设计门禁", "rel_path": "teaching/checks/check_architect.json", "stage": "T1"},
            {"gate_id": "check_student_task", "label": "T2: 学生探究小问门禁", "rel_path": "teaching/checks/check_student_task.json", "stage": "T2"},
            {"gate_id": "check_student_visibility", "label": "T2: 防泄露可见性门禁", "rel_path": "teaching/checks/check_student_visibility.json", "stage": "T2"},
            {"gate_id": "check_teaching", "label": "T2: 教学与双源绑定门禁", "rel_path": "teaching/checks/check_teaching.json", "stage": "T2"},
            {"gate_id": "check_numbering", "label": "T2: 题号与引用一致性", "rel_path": "teaching/checks/check_numbering.json", "stage": "T2"},
            {"gate_id": "check_subquestion_solution", "label": "T2.5: 逐小问解答合规门禁", "rel_path": "teaching/checks/check_subquestion_solution.json", "stage": "T2.5"},
            {"gate_id": "build", "label": "T3: XeLaTeX 双源排版编译", "rel_path": "teaching/checks/build.json", "stage": "T3"},
            {"gate_id": "deliver", "label": "T4: 打包交付门禁", "rel_path": "teaching/checks/deliver.json", "stage": "T4"}
        ]

        for gc in gate_configs:
            fpath = os.path.join(p_root, gc["rel_path"]) if p_root else ""
            if not fpath or not os.path.exists(fpath):
                reports.append({
                    "gate_id": gc["gate_id"],
                    "label": gc["label"],
                    "stage": gc["stage"],
                    "path": gc["rel_path"],
                    "status": "未生成 (NOT_RUN)",
                    "passed": None,
                    "issues_count": 0,
                    "issues": [],
                    "raw": {},
                    "generated_at": None,
                    "is_stale": False,
                })
                continue

            try:
                mtime = os.path.getmtime(fpath)
                with open(fpath, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)

                is_stale = bool(
                    run_started_at is not None and mtime < run_started_at
                )
                status_raw = str(data.get("status", "")).upper()
                passed = (status_raw in ["PASS", "SUCCESS"]) or (data.get("passed") is True)

                if gc["gate_id"] == "freeze":
                    has_digest = bool(data.get("brief_digest") or data.get("freeze_digest"))
                    status = "PASS (已冻结)" if has_digest else "FAIL"
                    passed = has_digest
                    issues = []
                else:
                    if is_stale:
                        status = "过期 (STALE: 上一次运行生成)"
                        passed = None
                    elif passed:
                        status = "PASS (通过)"
                    elif "REVIEW" in status_raw:
                        status = "REVIEW (需人工审核)"
                    elif status_raw in ["FAIL", "REJECT", "ERROR"]:
                        status = "REJECT (未通过)"
                    else:
                        status = status_raw or "未知"

                    issues = []
                    for fnd in data.get("findings", []):
                        issues.append({
                            "severity": fnd.get("severity", "fail").upper(),
                            "message": fnd.get("message", ""),
                            "file": fnd.get("file", "")
                        })
                    for iss in data.get("issues", []):
                        issues.append({
                            "severity": iss.get("severity", "WARN").upper(),
                            "message": iss.get("message", ""),
                            "file": iss.get("file", "")
                        })

                reports.append({
                    "gate_id": gc["gate_id"],
                    "label": gc["label"],
                    "stage": gc["stage"],
                    "path": gc["rel_path"],
                    "status": status,
                    "passed": passed,
                    "issues_count": len(issues),
                    "issues": issues,
                    "raw": data,
                    "generated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)),
                    "is_stale": is_stale,
                })
            except Exception as e:
                reports.append({
                    "gate_id": gc["gate_id"],
                    "label": gc["label"],
                    "stage": gc["stage"],
                    "path": gc["rel_path"],
                    "status": f"解析异常: {e}",
                    "passed": False,
                    "issues_count": 1,
                    "issues": [{"severity": "ERROR", "message": str(e), "file": gc["rel_path"]}],
                    "raw": {},
                    "generated_at": None,
                    "is_stale": False,
                })

        return reports

    def get_artifacts(self, workspace_root: str) -> Dict[str, Any]:
        """Get deliverable PDFs and json manifests from output workspace."""
        p_root = os.path.abspath(workspace_root) if workspace_root else ""
        
        def _get_file_info(fpath: str) -> Dict[str, Any]:
            if not fpath or not os.path.exists(fpath):
                return {"exists": False, "path": fpath, "size": 0, "size_str": "未生成", "mtime": None}
            sz = os.path.getsize(fpath)
            if sz < 1024:
                sz_str = f"{sz} B"
            elif sz < 1024 * 1024:
                sz_str = f"{sz / 1024:.1f} KB"
            else:
                sz_str = f"{sz / (1024 * 1024):.2f} MB"
            mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(os.path.getmtime(fpath)))
            return {"exists": True, "path": fpath, "size": sz, "size_str": sz_str, "mtime": mtime}

        return {
            "student_pdf": _get_file_info(os.path.join(p_root, "student.pdf") if p_root else ""),
            "teacher_pdf": _get_file_info(os.path.join(p_root, "teacher.pdf") if p_root else ""),
            "student_solution_pdf": _get_file_info(os.path.join(p_root, "student_solution.pdf") if p_root else ""),
            "manifest_json": _get_file_info(os.path.join(p_root, "release", "manifest.json") if p_root else ""),
            "architect_plan": _get_file_info(os.path.join(p_root, "teaching", "architect-plan.json") if p_root else ""),
            "classroom_plan": _get_file_info(os.path.join(p_root, "teaching", "classroom-plan.md") if p_root else ""),
            "user_spec": _get_file_info(os.path.join(p_root, "teaching", "user-spec.json") if p_root else ""),
            "freeze_json": _get_file_info(os.path.join(p_root, "research", "freeze.json") if p_root else "")
        }

    def run_single_node_direct(self, workspace_root: str, node_key: str) -> Dict[str, Any]:
        """Execute a single station/node directly for rapid debugging."""
        p_root = os.path.abspath(workspace_root)

        # Resolve the latest broker run under staging/ (needed by T2/T4 stations)
        def _latest_run() -> Optional[str]:
            staging_dir = os.path.join(p_root, "staging")
            if not os.path.isdir(staging_dir):
                return None
            runs = [d for d in os.listdir(staging_dir) if os.path.isdir(os.path.join(staging_dir, d))]
            if not runs:
                return None
            return max(runs, key=lambda d: os.path.getmtime(os.path.join(staging_dir, d)))

        def _t2_state() -> Dict[str, Any]:
            run_id = _latest_run()
            if not run_id:
                raise FileNotFoundError("staging/ 下没有任何 broker run，请先执行 T2 签发（t2_broker_issue）")
            expected: List[str] = []
            run_json_path = os.path.join(p_root, "staging", run_id, "run.json")
            if os.path.exists(run_json_path):
                with open(run_json_path, "r", encoding="utf-8-sig") as f:
                    rdata = json.load(f)
                tasks_obj = rdata.get("tasks", {})
                if isinstance(tasks_obj, dict):
                    expected = sorted(tasks_obj.keys())
            return {"project_root": p_root, "run_id": run_id, "expected_tasks": expected}

        def _t4_state() -> Dict[str, Any]:
            run_id = _latest_run()
            if not run_id:
                raise FileNotFoundError("staging/ 下没有任何 broker run，请先执行 T2 签发（t2_broker_issue）")
            return {"project_root": p_root, "run_id": run_id}

        def _selected_from_research() -> List[str]:
            """从 research/problems/ 子目录推导入选题目（供 RB 派发站使用）。"""
            probs_dir = os.path.join(p_root, "research", "problems")
            if not os.path.isdir(probs_dir):
                return []
            return sorted(
                d for d in os.listdir(probs_dir)
                if os.path.isdir(os.path.join(probs_dir, d))
            )

        def _rb_await_state(prefix: str) -> Dict[str, Any]:
            """从磁盘派发单重建 await 节点的批次状态（供单步工位直接等待产物）。"""
            req_dir = os.path.join(p_root, "teaching", "agent-requests")
            sheets: List[Dict[str, Any]] = []
            for fpath in sorted(glob.glob(os.path.join(req_dir, "dsp-%s-*.json" % prefix))):
                try:
                    with open(fpath, "r", encoding="utf-8-sig") as f:
                        sheets.append(json.load(f))
                except Exception:
                    pass
            return {
                "project_root": p_root,
                "pending_dispatch_batch": sheets,
                "batch_stage": prefix.upper().replace("-", "_")
            }

        node_map = {
            "r1": lambda: node_r1_screen_problems({"project_root": p_root}),
            "r2": lambda: node_r2_deep_research({"project_root": p_root}),
            "r4": lambda: node_r4_freeze_brief({"project_root": p_root}),
            "t0": lambda: node_t0_init_project({"project_root": p_root}),
            "t1": lambda: node_t1_architect({"project_root": p_root}),
            "t2_issue": lambda: node_t2_broker_issue(
                {"project_root": p_root, "history": []}),
            "t2_verify": lambda: node_t2_student_task(_t2_state()),
            "t2_check": lambda: node_t2_broker_submit_and_check(_t2_state()),
            "t2_sol_issue": lambda: node_t2_request_subquestion_solutions(_t2_state()),
            "t2_sol_await": lambda: node_t2_await_subquestion_solutions(_rb_await_state("t2-subquestion-solutions")),
            "t2_sol_check": lambda: node_t2_check_subquestion_solutions(_t2_state()),
            "t3": lambda: node_t3_latex_build({"project_root": p_root}),
            "t4": lambda: node_t4_promote_and_deliver(_t4_state()),
            # Research-Back 两阶段四节点：request 站重写派发单，await 站按磁盘派发单等待产物
            "rb_probe_request": lambda: node_rb_probe_request(
                {"project_root": p_root, "selected_problems": _selected_from_research()}),
            "rb_probe_await": lambda: node_rb_probe_await(_rb_await_state("rb-probe")),
            "rb_solver_request": lambda: node_rb_solver_request(
                {"project_root": p_root, "selected_problems": _selected_from_research()}),
            "rb_solver_await": lambda: node_rb_solver_await(_rb_await_state("rb-solver"))
        }

        fn = node_map.get(node_key)
        if not fn:
            return {"error": f"Unknown node_key: {node_key}"}

        try:
            res = fn()
            return {"success": True, "result": res}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def generate_antigravity_task(
        self,
        input_dir: str,
        output_dir: Optional[str] = None,
        selected_problems: Optional[List[str]] = None,
        thread_id: str = "session_gui_01",
        engine_task_root: Optional[str] = None
    ) -> Tuple[bool, str, str]:
        """Generate AGY_TASK.md with exact configured paths and instructions for Antigravity.

        Saves to:
          1. engine_task_root / AGY_TASK.md (directly referenceable as @[AGY_TASK.md])
          2. output_dir / AGY_TASK.md

        ``engine_task_root`` 默认取构造时的 ``engine_task_root``；测试可注入独立
        临时目录，避免覆盖工作区里真实的启动任务书。
        Returns (success: bool, filepath: str, content: str)
        """
        inp = os.path.abspath(input_dir) if input_dir else os.getcwd()
        out = os.path.abspath(output_dir) if output_dir else os.path.join(inp, "self")
        probs = selected_problems or ["P01"]
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        task_root = os.path.abspath(engine_task_root) if engine_task_root else self.engine_task_root

        # Prepare workspace structure in out if needed
        self.prepare_workspace(inp, out)

        probs_str = ", ".join(f"`{p}`" for p in probs)
        probs_json = json.dumps(probs, ensure_ascii=False)

        try:
            out_task_path_calc = os.path.join(out, "AGY_TASK.md")
            rel_task_ref = os.path.relpath(out_task_path_calc, os.getcwd()).replace("\\", "/")
        except Exception:
            rel_task_ref = "self/AGY_TASK.md"

        md_content = f"""# 🤖 Antigravity 竞赛教研启动任务书 (AGY_TASK.md)
> 本文件由 Web UI ({timestamp}) 自动生成。
> 用户在 Antigravity 对话框中发送：`请执行 @[{rel_task_ref}]` 即可直接启动全流程。

---

## 📌 一、 运行时配置参数 (Runtime Configuration)
- **输入目录 (Input Dir)**: `{inp}`
- **输出工作区 (Output Workspace)**: `{out}`
- **本次研学入选题目 (Selected Problems)**: {probs_str}
- **会话线程 (Thread ID)**: `{thread_id}`
- **生成时间**: `{timestamp}`

---

## 🎯 二、 Antigravity 核心执行指引 (Execution Instructions)

请 Antigravity（主 Agent）读取本文件参数后，严格遵循 `competition-courseware` / `skills-langgraph` 规范执行以下阶段任务：

### 阶段 1：多子 Agent 深度研学 (R2 Deep Research)
针对入选题目列表 {probs_json} 中的每道题目 `<pid>`：
1. **Probe Agent 派发**：
   - 研读 `prompts/probe-agent.md`；
   - 调用原生 `invoke_subagent(Role="Probe Agent", ...)` 派发子会话；
   - 提取特例探针、边界退化与等号可达性结论，写入 `{out}/research/problems/<pid>/probe.json`；
   - **关键要求**：必须在 JSON 中包含 `"execution_metadata": {{"subagent_conversation_id": "<子会话UUID>", "role": "probe"}}`。

2. **Solver Agent 派发**：
   - 研读 `prompts/solver-agent.md`；
   - 调用原生 `invoke_subagent(Role="Solver Agent", ...)` 派发独立子会话；
   - 完成零跳步严密证明推导，写入 `{out}/research/problems/<pid>/solution.json` 与 `solution.md`；
   - **关键要求**：必须包含真实且不与 Probe 重复的 `"subagent_conversation_id"`。

3. **Math-Audit Agent 派发**：
   - 研读 `prompts/math-audit.md`；
   - 调用原生 `invoke_subagent(Role="Math-Audit Agent", ...)` 审查代数与逻辑一致性；
   - 产物写入 `{out}/research/problems/<pid>/review_research_*.json`。

### 阶段 2：门禁核验与 brief 冻结 (R2 Gate & R4 Freeze)
1. **运行研学门禁校验**：
   在终端运行：`python scripts/check_research.py --project "{out}"`
   确保通过子会话真实性穿透校验（PASS）。
2. **冻结教学交接简报**：
   在终端运行：`python scripts/freeze_brief.py --project "{out}"`
   生成 `{out}/research/freeze.json`（锁定 SHA-256 摘要）。

### 阶段 3：认知工位产物（T1 架构计划 / T2 学生任务）
LangGraph 图会把这两个阶段作为**带 checkpoint 的派发单**交给主 Agent；只有产物
落盘后流程才会继续（没有产物变化时流水线会中止并报错，不会原样重跑门禁）。

1. **T1 双轨架构计划**：
   - 读取 `{out}/teaching/agent-requests/dsp-t1-architect-*.json` 派发单；
   - 按 `prompts/architect-agent.md` 生成 `{out}/teaching/architect-plan.json`
     （遵循 `schemas/architect.schema.json`）。
2. **T2 学生探究小问（按题批次派发，每题一份派发单）**：
   - 先由 broker 建立 run 并签发窄包：`python scripts/broker.py "{out}" create_run`；
   - 枚举 `{out}/teaching/agent-requests/dsp-t2-*.json` 派发单（每题一份）；
   - **每份派发单要求一次独立的 `invoke_subagent(Role="StudentTask Agent", ...)` 子会话**，
     逐题生成对应 `{out}/staging/<run_id>/tasks/<task_id>/student-task.json`，
     `execution_metadata.worker_session_id` 必须写回该题 receipt 中的值，
     prompt/hint 不得泄露 brief 的 `protected_strategy_tokens`；
   - **不要在一个子会话里生成多题**——每题一个独立子会话，逐题交付落盘。
3. **Research-Back 应对（研学门禁打回、resume 后才会出现）**：
   - 出现 `dsp-rb-probe-*` 派发单时：按 `prompts/probe-agent.md`
     **每题一个子会话**重做 `research/problems/<pid>/probe.json` 与 `pitfall.json`；
   - 出现 `dsp-rb-solver-*` 派发单时：按 `prompts/solver-agent.md`
     **每题一个子会话**重做 `research/problems/<pid>/solution.json` /
     `solution.md` / `dossier.json`；
   - probe 全部完成后才会出现 solver 派发单；probe 与 solver 两个角色的
     `subagent_conversation_id` 必须互不相同（各为全新会话 UUID）。
4. **恢复执行（人工介入指引，严禁静默恢复）**：产物写完后执行
   `python -m workflow.runner --project "{out}" --resume --title "<标题>" --mode guided --thread-id {thread_id} --checkpoint-db "{out}/.langgraph/courseware_checkpoints.sqlite" --auth-token "<真人提供的授权码>"`
   （检查点已持久化，换进程也能恢复）。
   - **红线**：凡终端输出 `[!] 流水线安全挂起`（尤其是 Phase 0 断点），主 Agent
     **必须立即停止自动推进**，调用 `ask_question` 向真人用户确认标题与授课模式，
     并请真人提供授权码（由系统写入 `{out}/.langgraph/phase0_token.txt`，真人可查）。
   - **严禁**主 Agent 自行读取授权码文件、伪造 `--auth-token` 或在未获真人明确
     答复前执行任何 `--resume` 命令；缺失/错误授权码将被 `[PERMISSION_DENIED]` 拒绝。

### 阶段 4：告知用户与后续流转
完成上述产物后，在聊天窗口向用户汇报完成情况。告知用户：
- 研学数据、架构计划与学生任务已写入 `{out}/`；
- 用户此时可直接在 Web UI 页面（`http://127.0.0.1:7860`）点击 **“🚀 一键启动流水线”**，进入 Phase 0 断点对齐并完成双源 XeLaTeX 编译交付！
"""

        # Write to engine_task_root/AGY_TASK.md
        root_task_path = os.path.join(task_root, "AGY_TASK.md")
        try:
            with open(root_task_path, "w", encoding="utf-8") as f:
                f.write(md_content)
        except Exception:
            pass

        # Write to out/AGY_TASK.md
        out_task_path = os.path.join(out, "AGY_TASK.md")
        try:
            with open(out_task_path, "w", encoding="utf-8") as f:
                f.write(md_content)
        except Exception:
            pass

        return True, root_task_path, md_content

