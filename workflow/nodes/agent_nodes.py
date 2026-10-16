"""agent_nodes.py — Cognitive-stage dispatch nodes (Agent / Antigravity).

每个需要认知工位的阶段都被拆成一对节点：

  * ``*_request``  写入一份机器可读的派发单（谁做、做什么、产出哪些文件、
                    派发前这些文件的摘要），落到 state 与
                    ``<project>/teaching/agent-requests/<dispatch_id>.json``；
  * ``*_await``    真正等待产物：注入了认知 worker 就地派发，否则抛出带
                    checkpoint 的 LangGraph ``interrupt()``，由人工 / Antigravity
                    会话产出文件后恢复执行。

两个节点分开是为了让“派发前摘要”能跨 interrupt 存活（interrupt 会丢弃所在
节点的返回值），从而可以判断产物是否真的发生变化。

产物没有出现、或与派发前完全一致时，节点返回 HALT 并给出明确错误，而不是
假装返修成功、原样重跑同一个门禁。
"""

import hashlib
import json
import os
import re
import uuid
from typing import Any, Dict, List, Optional

try:
    import contract
except ImportError:
    import sys
    _scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
    if _scripts_dir not in sys.path:
        sys.path.insert(0, _scripts_dir)
    import contract

from langgraph.types import interrupt

from ..state import CoursewareState
from ..adapters.llm_worker import LLMWorkerAdapter
from ..adapters.worker_registry import worker_registry
from ..adapters.script_runner import ScriptRunner

REQUEST_DIR_REL = "teaching/agent-requests"


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def sha256_file(path: str) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def snapshot(root: str, rel_paths: List[str]) -> Dict[str, Optional[str]]:
    """{项目相对路径: sha256 或 None（不存在）}"""
    out: Dict[str, Optional[str]] = {}
    for rel in rel_paths:
        out[rel] = sha256_file(os.path.join(root, rel.replace("/", os.sep)))
    return out


def _safe_join(root: str, rel: str) -> Optional[str]:
    """把项目相对路径解析为绝对路径；越出项目根或含 `..` 时拒绝。"""
    if not rel or rel.startswith("/") or rel.startswith("\\"):
        return None
    parts = rel.replace("\\", "/").split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    abs_path = os.path.abspath(os.path.join(root, *parts))
    if os.path.commonpath([os.path.abspath(root), abs_path]) != os.path.abspath(root):
        return None
    return abs_path


def build_request(
    state: CoursewareState,
    *,
    stage: str,
    role: str,
    prompt_file: str,
    schema_file: str,
    artifacts: List[str],
    instruction: str,
    command: str,
    context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """构造一份派发单（含派发前产物摘要）。"""
    root = state.get("project_root", ".")
    dispatch_id = "dsp-%s-%s" % (
        stage.lower().replace("_", "-"), uuid.uuid4().hex[:10]
    )
    return {
        "dispatch_id": dispatch_id,
        "stage": stage,
        "role": role,
        "prompt_file": prompt_file,
        "schema_file": schema_file,
        "artifacts": list(artifacts),
        "instruction": instruction,
        "command": command,
        "context": context or {},
        "run_id": state.get("run_id", ""),
        "before": snapshot(root, artifacts),
    }


def write_request_file(root: str, request: Dict[str, Any]) -> str:
    """把派发单落盘，供 Antigravity / 人工直接引用。"""
    rel = "%s/%s.json" % (REQUEST_DIR_REL, request["dispatch_id"])
    abs_path = _safe_join(root, rel)
    if abs_path is None:
        return ""
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(request, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return rel


def _read_project_flag(project_root: str, key: str, default: bool = False) -> bool:
    """读取项目 config.yaml 的顶层布尔开关（默认 default，未传参时为 False，行为不变）。

    优先 PyYAML；缺失或解析失败时退回逐行扁平解析（与 human_nodes.py 的
    无 PyYAML 依赖写法保持一致），任何异常都按 default 处理。

    口径与 scripts/contract.py 的 read_project_flag 一致：逐行回退解析
    只匹配无缩进的顶层键行（缩进的嵌套键一律跳过），适用于
    enable_t2_self_review / enable_obstacle_study 等顶层开关。
    """
    cfg_path = os.path.join(project_root, "config.yaml")
    if not os.path.exists(cfg_path):
        return default
    try:
        with open(cfg_path, encoding="utf-8-sig") as f:
            text = f.read()
    except OSError:
        return default
    try:
        import yaml
        data = yaml.safe_load(text)
        if isinstance(data, dict) and key in data:
            return bool(data.get(key))
    except Exception:
        pass
    pattern = re.compile(r"^%s\s*:\s*(\S+)" % re.escape(key))
    for line in text.splitlines():
        if line[:1] in (" ", "\t"):
            continue  # 只认顶层键（无缩进行），与 contract.read_project_flag 口径一致
        stripped = line.split("#", 1)[0].strip()
        m = pattern.match(stripped)
        if m:
            return m.group(1).lower() in ("true", "yes", "on", "1")
    return default


def _write_worker_files(root: str, request: Dict[str, Any],
                        files: Dict[str, Any]) -> List[str]:
    """把 worker/人工返回的文件内容写盘；只允许写派发单声明的产物路径。"""
    written: List[str] = []
    allowed = set(request.get("artifacts", []))
    if not isinstance(files, dict):
        return written
    for rel, content in files.items():
        if rel not in allowed:
            continue
        abs_path = _safe_join(root, rel)
        if abs_path is None:
            continue
        # 题干一致性保障：对于 student-task.json，严格同步 packet 中的 problem_stem，防止模型擅自改写
        if rel.endswith("student-task.json"):
            packet_ref = request.get("context", {}).get("packet_ref")
            if packet_ref:
                packet_abs = _safe_join(root, packet_ref)
                if packet_abs and os.path.isfile(packet_abs):
                    try:
                        with open(packet_abs, "r", encoding="utf-8") as pf:
                            pkt = json.load(pf)
                        raw_stem = pkt.get("problem_stem")
                        if raw_stem:
                            stem = contract.strip_numbering_and_source(raw_stem)
                            if isinstance(content, str):
                                task_obj = json.loads(content)
                            elif isinstance(content, dict):
                                task_obj = dict(content)
                            else:
                                task_obj = None
                            if isinstance(task_obj, dict):
                                task_obj["stem"] = stem
                                content = json.dumps(task_obj, ensure_ascii=False, indent=2)
                    except Exception:
                        pass
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, indent=2)
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        tmp = abs_path + ".tmp-%s" % uuid.uuid4().hex[:8]
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
        os.replace(tmp, abs_path)
        written.append(rel)
    return written


def _interrupt_payload(request: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "AGENT_DISPATCH",
        "dispatch_id": request["dispatch_id"],
        "stage": request["stage"],
        "role": request["role"],
        "description": (
            "认知工位派发：%s。请产出下列文件后恢复执行；未产出或内容无变化时"
            "流水线将中止并报告错误（不会原样重跑门禁）。" % request["instruction"]
        ),
        "artifacts": request["artifacts"],
        "instruction": request["instruction"],
        "command": request["command"],
        "prompt_file": "prompts/%s" % request["prompt_file"],
        "schema_file": "schemas/%s" % request["schema_file"],
        "request_ref": "%s/%s.json" % (REQUEST_DIR_REL, request["dispatch_id"]),
        "before_digests": request["before"],
        "context": request.get("context", {}),
    }


def run_dispatch(
    state: CoursewareState,
    request: Dict[str, Any],
    *,
    success_stage: str,
    success_history: str
) -> Dict[str, Any]:
    """执行一份派发单：派发 worker 或人工中断，然后校验产物确已出现且已变化。"""
    root = state.get("project_root", ".")
    dispatch_id = request["dispatch_id"]
    artifacts: List[str] = request["artifacts"]
    history = list(state.get("history", []))
    results = dict(state.get("dispatch_results", {}))

    dispatched_via = "worker" if worker_registry.available_for_role(request["role"]) else "interrupt"

    if worker_registry.available_for_role(request["role"]):
        adapter = LLMWorkerAdapter()
        adapter.set_model_callable(
            lambda sys_prompt, user: worker_registry.call_for_role(
                request["role"], sys_prompt, user)
        )
        user_content = (
            "%s\n\n## 项目根\n%s\n\n## 派发前产物摘要\n%s\n\n"
            "## 附加上下文\n%s\n\n请以 JSON 返回："
            "{\"files\": {\"<项目相对路径>\": \"<完整文件内容>\"}}，"
            "只允许写入上面列出的产物路径。"
            % (request["instruction"], root,
               json.dumps(request["before"], ensure_ascii=False, indent=2),
               json.dumps(request.get("context", {}), ensure_ascii=False, indent=2))
        )
        output = adapter.run_worker(
            request["role"], request["prompt_file"], user_content,
            fallback_data=None
        )
        if isinstance(output, dict) and output.get("_error"):
            results[dispatch_id] = {
                "status": "worker_error", "detail": output["_error"],
                "dispatched_via": dispatched_via
            }
            history.append("Agent[%s]: worker 调用失败: %s"
                           % (request["stage"], output["_error"]))
            return _dispatch_failure(state, results, history, success_stage,
                                     "认知 worker 调用失败: %s" % output["_error"])
        if isinstance(output, dict):
            _write_worker_files(root, request, output.get("files") or {})
    else:
        payload = _interrupt_payload(request)
        history.append(
            "Agent[%s]: 未注入认知 worker，挂起等待人工 / Antigravity 产出 %s"
            % (request["stage"], ", ".join(artifacts))
        )
        response = interrupt(payload)
        if not isinstance(response, dict):
            response = {}
        if response.get("files"):
            _write_worker_files(root, request, response["files"])

    # ---- 校验：产物必须出现，且必须与派发前不同 ----
    after = snapshot(root, artifacts)
    missing = [rel for rel in artifacts if after[rel] is None]
    unchanged = [rel for rel in artifacts
                 if after[rel] is not None and after[rel] == request["before"].get(rel)]

    if missing:
        detail = "派发 %s 的产物未生成: %s" % (dispatch_id, ", ".join(missing))
        results[dispatch_id] = {
            "status": "missing_artifacts", "missing": missing,
            "dispatched_via": dispatched_via
        }
        history.append("Agent[%s]: %s" % (request["stage"], detail))
        return _dispatch_failure(state, results, history, success_stage, detail)

    if unchanged and len(unchanged) == len(artifacts):
        detail = (
            "派发 %s 的产物与派发前完全一致（%s）：没有实际修改，拒绝无变更重试"
            % (dispatch_id, ", ".join(unchanged))
        )
        results[dispatch_id] = {
            "status": "no_change", "unchanged": unchanged,
            "dispatched_via": dispatched_via
        }
        history.append("Agent[%s]: %s" % (request["stage"], detail))
        return _dispatch_failure(state, results, history, success_stage, detail)

    results[dispatch_id] = {
        "status": "ok",
        "produced": [rel for rel in artifacts if after[rel] is not None],
        "changed": [rel for rel in artifacts if after[rel] != request["before"].get(rel)],
        "dispatched_via": dispatched_via,
    }
    history.append("Agent[%s]: %s（派发方式 %s，产物 %s）"
                   % (request["stage"], success_history, dispatched_via,
                      ", ".join(artifacts)))

    return {
        "stage": success_stage,
        "pending_dispatch": None,
        "dispatch_results": results,
        "worker_available": worker_registry.available,
        "routing_action": "PROCEED",
        "history": history
    }


def _dispatch_failure(
    state: CoursewareState,
    results: Dict[str, Any],
    history: List[str],
    stage: str,
    detail: str
) -> Dict[str, Any]:
    return {
        "stage": "ABORTED",
        "pending_dispatch": None,
        "pending_dispatch_batch": None,
        "batch_stage": None,
        "dispatch_results": results,
        "worker_available": worker_registry.available,
        "routing_action": "HALT",
        "error_message": detail,
        "history": history
    }


# ---------------------------------------------------------------------------
# 批次派发引擎：逐单派发、逐单校验，全部完成才算成功
# ---------------------------------------------------------------------------

def _validate_sheet(root: str, sheet: Dict[str, Any]) -> Dict[str, List[str]]:
    """校验单份派发单的产物：必须出现，且不得全部与派发前一致。"""
    artifacts: List[str] = sheet["artifacts"]
    after = snapshot(root, artifacts)
    missing = [rel for rel in artifacts if after[rel] is None]
    unchanged = [rel for rel in artifacts
                 if after[rel] is not None and after[rel] == sheet["before"].get(rel)]
    return {"after": after, "missing": missing, "unchanged": unchanged}


def run_batch_dispatch(
    state: CoursewareState,
    requests: List[Dict[str, Any]],
    *,
    success_stage: str,
    success_history: str
) -> Dict[str, Any]:
    """执行一批派发单：批次内串行，逐单校验，任一失败即中止（已 ok 的保留）。"""
    root = state.get("project_root", ".")
    history = list(state.get("history", []))
    results = dict(state.get("dispatch_results", {}))

    # 断点续跑/部分完成：已 ok 的 sheet 跳过
    pending = [r for r in requests
               if results.get(r["dispatch_id"], {}).get("status") != "ok"]

    def _fail(sheet: Dict[str, Any], status: str, detail: str,
              extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        entry = {"status": status, "dispatched_via": dispatched_via}
        if extra:
            entry.update(extra)
        results[sheet["dispatch_id"]] = entry
        history.append("Agent[%s]: %s" % (sheet["stage"], detail))
        return _dispatch_failure(state, results, history, success_stage, detail)

    if pending and all(worker_registry.available_for_role(r["role"]) for r in pending):
        dispatched_via = "worker"
        for sheet in pending:
            adapter = LLMWorkerAdapter()
            adapter.set_model_callable(
                lambda sys_prompt, user, _role=sheet["role"]: worker_registry.call_for_role(
                    _role, sys_prompt, user)
            )
            user_content = (
                "%s\n\n## 项目根\n%s\n\n## 派发前产物摘要\n%s\n\n"
                "## 附加上下文\n%s\n\n请以 JSON 返回："
                "{\"files\": {\"<项目相对路径>\": \"<完整文件内容>\"}}，"
                "只允许写入上面列出的产物路径。"
                % (sheet["instruction"], root,
                   json.dumps(sheet["before"], ensure_ascii=False, indent=2),
                   json.dumps(sheet.get("context", {}), ensure_ascii=False, indent=2))
            )
            output = adapter.run_worker(
                sheet["role"], sheet["prompt_file"], user_content,
                fallback_data=None
            )
            if isinstance(output, dict) and output.get("_error"):
                return _fail(sheet, "worker_error",
                             "认知 worker 调用失败（派发 %s）: %s"
                             % (sheet["dispatch_id"], output["_error"]),
                             {"detail": output["_error"]})
            if isinstance(output, dict):
                _write_worker_files(root, sheet, output.get("files") or {})
            # 每 sheet 调用后立刻校验该 sheet 产物
            check = _validate_sheet(root, sheet)
            if check["missing"]:
                return _fail(sheet, "missing_artifacts",
                             "派发 %s 的产物未生成: %s"
                             % (sheet["dispatch_id"], ", ".join(check["missing"])),
                             {"missing": check["missing"]})
            if len(check["unchanged"]) == len(sheet["artifacts"]):
                return _fail(sheet, "no_change",
                             "派发 %s 的产物与派发前完全一致（%s）：没有实际修改，"
                             "拒绝无变更重试"
                             % (sheet["dispatch_id"], ", ".join(check["unchanged"])),
                             {"unchanged": check["unchanged"]})
            results[sheet["dispatch_id"]] = {
                "status": "ok",
                "produced": [rel for rel in sheet["artifacts"] if check["after"][rel] is not None],
                "changed": [rel for rel in sheet["artifacts"]
                            if check["after"][rel] != sheet["before"].get(rel)],
                "dispatched_via": dispatched_via,
            }
            history.append("Agent[%s]: 派发 %s 完成（派发方式 %s，产物 %s）"
                           % (sheet["stage"], sheet["dispatch_id"], dispatched_via,
                              ", ".join(sheet["artifacts"])))
    elif pending:
        dispatched_via = "interrupt"
        payload = {
            "type": "AGENT_DISPATCH_BATCH",
            "stage": state.get("batch_stage") or pending[0]["stage"],
            "sheets": [_interrupt_payload(r) for r in pending],
        }
        history.append(
            "Agent[%s]: 未注入认知 worker，挂起等待人工 / Antigravity 产出 %d 份派发单的产物"
            % (payload["stage"], len(pending))
        )
        response = interrupt(payload)
        if not isinstance(response, dict):
            response = {}
        files = response.get("files")
        for sheet in pending:
            if files:
                # _write_worker_files 按单 sheet 的产物白名单过滤，逐 sheet 调用
                _write_worker_files(root, sheet, files)
            check = _validate_sheet(root, sheet)
            if check["missing"]:
                return _fail(sheet, "missing_artifacts",
                             "派发 %s 的产物未生成: %s"
                             % (sheet["dispatch_id"], ", ".join(check["missing"])),
                             {"missing": check["missing"]})
            if len(check["unchanged"]) == len(sheet["artifacts"]):
                return _fail(sheet, "no_change",
                             "派发 %s 的产物与派发前完全一致（%s）：没有实际修改，"
                             "拒绝无变更重试"
                             % (sheet["dispatch_id"], ", ".join(check["unchanged"])),
                             {"unchanged": check["unchanged"]})
            results[sheet["dispatch_id"]] = {
                "status": "ok",
                "produced": [rel for rel in sheet["artifacts"] if check["after"][rel] is not None],
                "changed": [rel for rel in sheet["artifacts"]
                            if check["after"][rel] != sheet["before"].get(rel)],
                "dispatched_via": dispatched_via,
            }
            history.append("Agent[%s]: 派发 %s 完成（派发方式 %s，产物 %s）"
                           % (sheet["stage"], sheet["dispatch_id"], dispatched_via,
                              ", ".join(sheet["artifacts"])))

    history.append("Agent[%s]: %s" % (state.get("batch_stage") or "batch", success_history))
    return {
        "stage": success_stage,
        "pending_dispatch_batch": None,
        "batch_stage": None,
        "dispatch_results": results,
        "worker_available": worker_registry.available,
        "routing_action": "PROCEED",
        "history": history
    }


# ---------------------------------------------------------------------------
# T1：architect-plan.json
# ---------------------------------------------------------------------------

def node_t1_request_architect(state: CoursewareState) -> Dict[str, Any]:
    """T1: 派发双轨架构设计（architect-plan.json）。"""
    project_root = state.get("project_root", ".")
    request = build_request(
        state,
        stage="T1_ARCHITECT",
        role="architect",
        prompt_file="architect-agent.md",
        schema_file="architect.schema.json",
        artifacts=["teaching/architect-plan.json"],
        instruction=(
            "基于冻结 brief 生成本轮双轨架构计划 teaching/architect-plan.json"
            "（含 problems / blocks / tasks，遵循 schemas/architect.schema.json）。"
        ),
        command=(
            "按 prompts/architect-agent.md 生成 teaching/architect-plan.json"
        ),
        context={
            "brief_ref": "research/brief.json",
            "freeze_ref": "research/freeze.json",
            "selected_problems": state.get("selected_problems", []),
        }
    )
    request_ref = write_request_file(project_root, request)
    history = list(state.get("history", []))
    history.append("T1: 已派发架构设计任务 %s（派发单 %s）"
                   % (request["dispatch_id"], request_ref or "(未落盘)"))
    return {
        "stage": "T1_AGENT_PENDING",
        "pending_dispatch": request,
        "worker_available": worker_registry.available,
        "routing_action": "PROCEED",
        "history": history
    }


def node_t1_await_architect(state: CoursewareState) -> Dict[str, Any]:
    """T1: 等待架构计划产物并校验。"""
    request = state.get("pending_dispatch") or {}
    if not request:
        return {
            "stage": "ABORTED",
            "routing_action": "HALT",
            "error_message": "T1 缺少派发单（pending_dispatch 为空），无法等待产物",
            "history": list(state.get("history", []))
        }
    return run_dispatch(
        state, request,
        success_stage="T1_ARCHITECTED",
        success_history="架构计划已生成"
    )


# ---------------------------------------------------------------------------
# T2：学生探究小问任务 staging/<run>/tasks/<task>/student-task.json
# ---------------------------------------------------------------------------

def node_t2_request_student_task(state: CoursewareState) -> Dict[str, Any]:
    """T2: 为每个 expected task 单独派发一份学生探究小问设计（批次派发）。"""
    project_root = state.get("project_root", ".")
    run_id = state.get("run_id", "")
    expected_tasks = state.get("expected_tasks", [])
    if not expected_tasks:
        return {
            "stage": "ABORTED",
            "routing_action": "HALT",
            "error_message": "T2 没有 expected_tasks，无法派发学生任务",
            "history": list(state.get("history", []))
        }

    sheets: List[Dict[str, Any]] = []
    for tid in expected_tasks:
        sheet = build_request(
            state,
            stage="T2_STUDENT_TASK",
            role="student-task",
            prompt_file="student-task.md",
            schema_file="student-task.schema.json",
            artifacts=["staging/%s/tasks/%s/student-task.json" % (run_id, tid)],
            instruction=(
                "只为任务 %s 生成学生探究小问，写入 "
                "staging/%s/tasks/%s/student-task.json"
                "（题干 stem 必须与 packet 中的 problem_stem 保持完全一致，严禁改写或变动；"
                "execution_metadata.worker_session_id 必须写回该任务 receipt 中的值，"
                "prompt 不得泄露受保护策略 token；不要改动其他任务的产物）。"
                % (tid, run_id, tid)
            ),
            command=(
                "按 prompts/student-task.md 为 %s 生成 student-task.json" % tid
            ),
            context={
                "task_id": tid,
                "packet_ref": "staging/%s/packets/%s.json" % (run_id, tid),
                "receipts_dir": "staging/%s/receipts" % run_id,
            }
        )
        write_request_file(project_root, sheet)
        sheets.append(sheet)
    history = list(state.get("history", []))
    history.append("T2: 已按题派发学生任务设计（%d 份派发单，任务 %s）"
                   % (len(sheets), expected_tasks))
    # T2 自审开关：读取项目 config.yaml 的 enable_t2_self_review（默认开启，
    # 仅当 config.yaml 显式写 false 时才关闭）
    self_review = _read_project_flag(project_root, "enable_t2_self_review", default=True)
    if self_review:
        history.append("T2: enable_t2_self_review 默认开启（config.yaml 未显式关闭），"
                       "产物落盘后将先按题派发自审")
    else:
        history.append("T2: enable_t2_self_review 已被 config.yaml 显式置为 false，"
                       "本轮跳过学生任务自审")
    return {
        "stage": "T2_AGENT_PENDING",
        "pending_dispatch_batch": sheets,
        "batch_stage": "T2_STUDENT_TASK",
        "t2_self_review": self_review,
        "worker_available": worker_registry.available,
        "routing_action": "PROCEED",
        "history": history
    }


def node_t2_await_student_task(state: CoursewareState) -> Dict[str, Any]:
    """T2: 等待批次学生任务产物并逐单校验。"""
    sheets = state.get("pending_dispatch_batch") or []
    if not sheets:
        return {
            "stage": "ABORTED",
            "routing_action": "HALT",
            "error_message": "T2 缺少批次派发单（pending_dispatch_batch 为空），无法等待产物",
            "history": list(state.get("history", []))
        }
    return run_batch_dispatch(
        state, sheets,
        success_stage="T2_STAGED",
        success_history="学生任务已生成（%d 题）" % len(sheets)
    )


# ---------------------------------------------------------------------------
# T2 自审（默认开启，config.yaml 显式写 enable_t2_self_review: false 才关闭）：
# 逐题审查 student-task.json，输出 review-student.json，可定点修复；
# 审查前由可信脚本为每题生成 teacher-solution packet（含 solution_ref +
# proof_refs），使自审成为唯一能判定"设问链是否抵达解"的防线
# ---------------------------------------------------------------------------

def node_t2_review_request(state: CoursewareState) -> Dict[str, Any]:
    """T2 自审：为每个 expected task 派发一份 student-task.json 审查（批次派发）。

    派发前先为每题调用 make_small_packet.py --kind teacher-solution 生成
    教师视野 packet；任一失败即中止（fail-closed，绝不降级为盲审）。
    """
    project_root = state.get("project_root", ".")
    run_id = state.get("run_id", "")
    expected_tasks = state.get("expected_tasks", [])
    if not expected_tasks:
        return {
            "stage": "ABORTED",
            "routing_action": "HALT",
            "error_message": "T2 自审没有 expected_tasks，无法派发审查",
            "history": list(state.get("history", []))
        }

    # 1. 为每题生成 teacher-solution packet（可信确定性脚本，fail-closed：
    #    任一失败即 ABORT，不得降级为盲审）
    runner = ScriptRunner(project_root)
    history = list(state.get("history", []))
    teacher_packet_refs: Dict[str, str] = {}
    for tid in expected_tasks:
        code, stdout, stderr = runner.run_script(
            "make_small_packet.py",
            ["--project", project_root, "--run-id", run_id,
             "--task-id", tid, "--kind", "teacher-solution"])
        if code != 0:
            detail = ("T2 自审：任务 %s 的 teacher-solution packet 生成失败"
                      "（make_small_packet.py 退出码 %d: %s）；自审必须持有"
                      "完整解法视野，fail-closed 中止，拒绝降级为盲审"
                      % (tid, code, (stderr or stdout).strip()[:300]))
            history.append("Agent[T2_REVIEW]: %s" % detail)
            return _dispatch_failure(
                state, dict(state.get("dispatch_results", {})),
                history, state.get("stage", "T2_STAGED"), detail)
        teacher_packet_refs[tid] = (
            "staging/%s/packets/%s-teacher-solution.json" % (run_id, tid))
    history.append("T2 自审: 已为 %d 个任务生成 teacher-solution packet（%s）"
                   % (len(teacher_packet_refs),
                      ", ".join(teacher_packet_refs.values())))

    # 2. 组装批次派发单
    sheets: List[Dict[str, Any]] = []
    for tid in expected_tasks:
        sheet = build_request(
            state,
            stage="T2_REVIEW",
            role="student-task-review",
            prompt_file="student-task-review.md",
            schema_file="student-task.schema.json",
            artifacts=[
                "staging/%s/tasks/%s/review-student.json" % (run_id, tid),
                "staging/%s/tasks/%s/student-task.json" % (run_id, tid),
            ],
            instruction=(
                "审查 staging/%s/tasks/%s/student-task.json：泄露风险"
                "（prompt/hint 不得出现 brief 的 protected_strategy_tokens）、"
                "难度梯度、设问充分性（对照 teacher_packet_ref 指向的 "
                "teacher-solution packet 中 solution 的 proof_obligations，"
                "确认每条义务至少被一个小问的 obligation_refs 覆盖、末位小问"
                "收束到原题结论、小问梯度与证明依赖序一致）、schema 合规；"
                "审查结论写入 "
                "staging/%s/tasks/%s/review-student.json"
                "（含 conclusion: PASS/REVISE 与 findings 列表）。"
                "若发现可定点修复的问题（如泄露 token、hint 超长、"
                "设问未覆盖证明义务、schema 字段错误），"
                "可直接重写 student-task.json 的出错字段，其余字段逐字保留；"
                "绑定字段（run_id/packet_digest/receipt_ref/worker_session_id）"
                "不得改动；修复时不得把定理名/结论文本写入 prompt。不要改动其他任务的产物。"
                % (run_id, tid, run_id, tid)
            ),
            command=(
                "按 prompts/student-task-review.md 审查 %s 的 student-task.json"
                " 并输出 review-student.json" % tid
            ),
            context={
                "task_id": tid,
                "student_task_ref": "staging/%s/tasks/%s/student-task.json" % (run_id, tid),
                "packet_ref": "staging/%s/packets/%s.json" % (run_id, tid),
                "teacher_packet_ref": teacher_packet_refs[tid],
            }
        )
        write_request_file(project_root, sheet)
        sheets.append(sheet)
    history.append("T2 自审: 已按题派发学生任务审查（%d 份派发单，任务 %s）"
                   % (len(sheets), expected_tasks))
    return {
        "stage": "T2_AGENT_PENDING",
        "pending_dispatch_batch": sheets,
        "batch_stage": "T2_REVIEW",
        "worker_available": worker_registry.available,
        "routing_action": "PROCEED",
        "history": history
    }


def _consume_review_conclusions(
    state: CoursewareState,
    sheets: List[Dict[str, Any]],
    result: Dict[str, Any]
) -> Dict[str, Any]:
    """G2.3：消费 T2 自审结论（review-student.json 的 conclusion）。

    批次校验通过后逐题解析 staging/<run>/tasks/<tid>/review-student.json：
      * JSON 缺失/畸形 → HALT（fail-closed，自审结论必须可读）；
      * conclusion == "PASS" → 该题放行；
      * 其余一切取值（"REVISE"、缺失、枚举外值）一律视为 REVISE：
        student-task.json 哈希与派发前一致（未同步定点修复）→ HALT
        （"自审判定 REVISE 但未修复"）；已被改写（哈希变化）→ 视为已修复，放行。
    """
    root = state.get("project_root", ".")
    history = list(result.get("history", []))
    results = dict(result.get("dispatch_results", {}))
    stage = result.get("stage", "T2_STAGED")
    for sheet in sheets:
        review_rel = task_rel = None
        for rel in sheet.get("artifacts", []):
            if rel.endswith("review-student.json"):
                review_rel = rel
            elif rel.endswith("student-task.json"):
                task_rel = rel
        if review_rel is None:
            detail = ("T2 自审派发单 %s 未声明 review-student.json 产物，"
                      "无法消费自审结论" % sheet.get("dispatch_id"))
            history.append("Agent[T2_REVIEW]: %s" % detail)
            return _dispatch_failure(state, results, history, stage, detail)
        review_doc = None
        abs_review = _safe_join(root, review_rel)
        if abs_review and os.path.isfile(abs_review):
            try:
                with open(abs_review, "r", encoding="utf-8-sig") as f:
                    review_doc = json.load(f)
            except (OSError, json.JSONDecodeError):
                review_doc = None
        if not isinstance(review_doc, dict):
            detail = ("T2 自审结论 %s 缺失或不是合法 JSON：自审结论必须可被消费，"
                      "拒绝放行（fail-closed）" % review_rel)
            history.append("Agent[T2_REVIEW]: %s" % detail)
            return _dispatch_failure(state, results, history, stage, detail)
        conclusion = review_doc.get("conclusion")
        if conclusion == "PASS":
            history.append("Agent[T2_REVIEW]: %s 自审结论 PASS" % review_rel)
            continue
        # REVISE 及枚举外值（含 conclusion 缺失）一律按 REVISE 处理
        task_changed = False
        if task_rel is not None:
            abs_task = _safe_join(root, task_rel)
            after_digest = sha256_file(abs_task) if abs_task else None
            task_changed = (
                after_digest is not None
                and after_digest != (sheet.get("before") or {}).get(task_rel)
            )
        if not task_changed:
            detail = ("T2 自审判定 REVISE 但未修复：%s 的 conclusion=%r，而 %s 的"
                      "哈希与派发前一致（未同步定点修复）；自审结论必须被消费，"
                      "拒绝无变更放行" % (review_rel, conclusion, task_rel))
            history.append("Agent[T2_REVIEW]: %s" % detail)
            return _dispatch_failure(state, results, history, stage, detail)
        history.append("Agent[T2_REVIEW]: %s 自审结论 %r，student-task.json "
                       "已同步定点修复，放行" % (review_rel, conclusion))
    result["history"] = history
    return result


def node_t2_review_await(state: CoursewareState) -> Dict[str, Any]:
    """T2 自审：等待批次审查产物并逐单校验，随后消费 review-student.json 结论。"""
    sheets = state.get("pending_dispatch_batch") or []
    if not sheets:
        return {
            "stage": "ABORTED",
            "routing_action": "HALT",
            "error_message": "T2 自审缺少批次派发单（pending_dispatch_batch 为空），无法等待产物",
            "history": list(state.get("history", []))
        }
    result = run_batch_dispatch(
        state, sheets,
        success_stage="T2_STAGED",
        success_history="学生任务自审完成（%d 题）" % len(sheets)
    )
    if result.get("routing_action") != "PROCEED":
        return result
    # G2.3：批次校验通过后必须消费自审结论（REVISE 未修复 / 结论不可读 → HALT）
    return _consume_review_conclusions(state, sheets, result)


# ---------------------------------------------------------------------------
# research-back：两阶段拆分（先重做探针/坑点，再基于修正结果重新研学）
# ---------------------------------------------------------------------------

def node_rb_probe_request(state: CoursewareState) -> Dict[str, Any]:
    """R2 打回第一阶段：为每题派发探针与坑点重做（probe / pitfall）。"""
    project_root = state.get("project_root", ".")
    selected = state.get("selected_problems", []) or []
    if not selected:
        return {
            "stage": "ABORTED",
            "routing_action": "HALT",
            "error_message": "Research-Back 没有 selected_problems，无法派发探针重做",
            "history": list(state.get("history", []))
        }

    gate_issues = _gate_issue_messages(state.get("research_gate"))
    sheets: List[Dict[str, Any]] = []
    for qid in selected:
        sheet = build_request(
            state,
            stage="RB_PROBE",
            role="probe",
            prompt_file="probe-agent.md",
            schema_file="probe.schema.json",
            artifacts=[
                "research/problems/%s/probe.json" % qid,
                "research/problems/%s/pitfall.json" % qid,
            ],
            instruction=(
                "门禁判定本轮研学存在实质性数学问题，冻结已被撤销。请针对 %s "
                "重新完成探针与坑点分析（probe.json / pitfall.json）；"
                "execution_metadata.subagent_conversation_id 必须是新会话 UUID，"
                "且不得与任何 solver 会话重复。" % qid
            ),
            command="按 prompts/probe-agent.md 重做 %s 的 probe.json 与 pitfall.json" % qid,
            context={
                "research_gate_issues": gate_issues,
                "qid": qid,
            }
        )
        write_request_file(project_root, sheet)
        sheets.append(sheet)
    history = list(state.get("history", []))
    history.append("Research-Back: 已按题派发探针重做（%d 份派发单，题目 %s）"
                   % (len(sheets), selected))
    return {
        "stage": "RESEARCH_BACK_LOOP",
        "pending_dispatch_batch": sheets,
        "batch_stage": "RB_PROBE",
        "worker_available": worker_registry.available,
        "routing_action": "PROCEED",
        "history": history
    }


def node_rb_probe_await(state: CoursewareState) -> Dict[str, Any]:
    """R2 打回第一阶段：等待探针重做产物并逐单校验。"""
    sheets = state.get("pending_dispatch_batch") or []
    if not sheets:
        return {
            "stage": "ABORTED",
            "routing_action": "HALT",
            "error_message": "Research-Back 缺少探针批次派发单，无法等待产物",
            "history": list(state.get("history", []))
        }
    out = run_batch_dispatch(
        state, sheets,
        success_stage="RESEARCH_BACK_LOOP",
        success_history="探针重做完成（%d 题）" % len(sheets)
    )
    if out.get("routing_action") == "PROCEED":
        out["rb_phase"] = "probe_done"
    return out


def node_rb_solver_request(state: CoursewareState) -> Dict[str, Any]:
    """R2 打回第二阶段：基于修正后的 probe/pitfall 重新研学（solution / dossier）。"""
    project_root = state.get("project_root", ".")
    selected = state.get("selected_problems", []) or []
    if not selected:
        return {
            "stage": "ABORTED",
            "routing_action": "HALT",
            "error_message": "Research-Back 没有 selected_problems，无法派发重新研学",
            "history": list(state.get("history", []))
        }

    gate_issues = _gate_issue_messages(state.get("research_gate"))
    sheets: List[Dict[str, Any]] = []
    for qid in selected:
        sheet = build_request(
            state,
            stage="RB_SOLVER",
            role="solver",
            prompt_file="solver-agent.md",
            schema_file="solution.schema.json",
            artifacts=[
                "research/problems/%s/solution.json" % qid,
                "research/problems/%s/solution.md" % qid,
                "research/problems/%s/dossier.json" % qid,
            ],
            instruction=(
                "基于修正后的 probe/pitfall 为 %s 重新零跳步推导，重写 "
                "solution.json / solution.md / dossier.json；"
                "execution_metadata.subagent_conversation_id 必须是新会话 UUID，"
                "且与 probe 会话不同。" % qid
            ),
            command="按 prompts/solver-agent.md 重新推导并重写 research/problems/%s/ 三件套" % qid,
            context={
                "research_gate_issues": gate_issues,
                "qid": qid,
                "probe_ref": "research/problems/%s/probe.json" % qid,
                "pitfall_ref": "research/problems/%s/pitfall.json" % qid,
            }
        )
        write_request_file(project_root, sheet)
        sheets.append(sheet)
    history = list(state.get("history", []))
    history.append("Research-Back: 已按题派发重新研学（%d 份派发单，题目 %s）"
                   % (len(sheets), selected))
    return {
        "stage": "RESEARCH_BACK_LOOP",
        "pending_dispatch_batch": sheets,
        "batch_stage": "RB_SOLVER",
        "worker_available": worker_registry.available,
        "routing_action": "PROCEED",
        "history": history
    }


def node_rb_solver_await(state: CoursewareState) -> Dict[str, Any]:
    """R2 打回第二阶段：等待重新研学产物并逐单校验。"""
    sheets = state.get("pending_dispatch_batch") or []
    if not sheets:
        return {
            "stage": "ABORTED",
            "routing_action": "HALT",
            "error_message": "Research-Back 缺少研学批次派发单，无法等待产物",
            "history": list(state.get("history", []))
        }
    out = run_batch_dispatch(
        state, sheets,
        success_stage="RESEARCH_BACK_LOOP",
        success_history="重新研学完成（%d 题）" % len(sheets)
    )
    if out.get("routing_action") == "PROCEED":
        out["rb_phase"] = "solver_done"
    return out


# ---------------------------------------------------------------------------
# repair：定点返修（带门禁 findings 的具体修改任务）
# ---------------------------------------------------------------------------

def node_repair_request(state: CoursewareState) -> Dict[str, Any]:
    """定点返修：把门禁 findings 转成具体修改任务并派发。"""
    project_root = state.get("project_root", ".")
    run_id = state.get("run_id", "")
    task_id = state.get("active_repair_task")
    if not task_id:
        return {
            "stage": "ABORTED",
            "routing_action": "HALT",
            "error_message": "返修缺少 active_repair_task，无法派发具体修改任务",
            "history": list(state.get("history", []))
        }

    findings = _t2_findings(state)
    is_solution_repair = any(f.get("gate") == "check_subquestion_solution" for f in findings) or (state.get("stage") == "T2_SOLUTIONS_CHECKED")

    if is_solution_repair:
        artifacts = ["staging/%s/tasks/%s/solution-task.json" % (run_id, task_id)]
        schema_file = "solution-task.schema.json"
        prompt_file = "subquestion-solution.md"
        instruction = (
            "按门禁 findings 定点修改 %s 的 solution-task.json；"
            "各小问分别独立作答，严禁集中在某一问，严禁同上/同理/略等敷衍词；"
            "保持 packet/receipt 绑定与 schema 合规。" % task_id
        )
    else:
        artifacts = ["staging/%s/tasks/%s/student-task.json" % (run_id, task_id)]
        schema_file = "student-task.schema.json"
        prompt_file = "repair-small-output.md"
        instruction = (
            "按门禁 findings 定点修改 %s 的 student-task.json；只改必要字段，"
            "保持 packet/receipt 绑定与 schema 合规。" % task_id
        )

    request = build_request(
        state,
        stage="REPAIR",
        role="repair",
        prompt_file=prompt_file,
        schema_file=schema_file,
        artifacts=artifacts,
        instruction=instruction,
        command="按 prompts/%s 修改 %s" % (prompt_file, artifacts[0]),
        context={
            "task_id": task_id,
            "gate_findings": findings,
            "retry_count": state.get("retry_count", 0),
        }
    )
    request_ref = write_request_file(project_root, request)
    history = list(state.get("history", []))
    history.append("Repair: 已派发定点返修任务 %s（任务 %s，%d 条 findings，派发单 %s）"
                   % (request["dispatch_id"], task_id, len(findings),
                      request_ref or "(未落盘)"))
    return {
        "stage": "REPAIR_LOOP",
        "pending_dispatch": request,
        "worker_available": worker_registry.available,
        "routing_action": "PROCEED",
        "history": history
    }


def node_repair_await(state: CoursewareState) -> Dict[str, Any]:
    """定点返修：等待修改后的产物并校验。"""
    request = state.get("pending_dispatch") or {}
    if not request:
        return {
            "stage": "ABORTED",
            "routing_action": "HALT",
            "error_message": "返修缺少派发单，无法等待产物",
            "history": list(state.get("history", []))
        }
    return run_dispatch(
        state, request,
        success_stage="REPAIR_LOOP",
        success_history="返修产物已更新"
    )


# ---------------------------------------------------------------------------
# findings 提取（供派发单附带上文）
# ---------------------------------------------------------------------------

def _gate_issue_messages(gate: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(gate, dict):
        return []
    out = []
    for iss in gate.get("issues", []) or []:
        if isinstance(iss, dict):
            out.append({"category": iss.get("category"),
                        "message": iss.get("message")})
    return out


def _t2_findings(state: CoursewareState) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    gates = state.get("t2_gates") or {}
    if isinstance(gates, dict):
        for gate_name, gate in gates.items():
            if not isinstance(gate, dict) or gate.get("passed"):
                continue
            for iss in gate.get("issues", []) or []:
                if isinstance(iss, dict):
                    findings.append({
                        "gate": gate_name,
                        "category": iss.get("category"),
                        "message": iss.get("message"),
                        "file": iss.get("file"),
                    })
    sol_gates = state.get("solution_task_gates") or {}
    if isinstance(sol_gates, dict):
        for gate_name, gate in sol_gates.items():
            if not isinstance(gate, dict) or gate.get("passed"):
                continue
            for iss in gate.get("issues", []) or []:
                if isinstance(iss, dict):
                    findings.append({
                        "gate": gate_name,
                        "category": iss.get("category"),
                        "message": iss.get("message"),
                        "file": iss.get("file"),
                    })
    return findings


# ---------------------------------------------------------------------------
# T2.5: Subquestion Solutions Dispatch Nodes
# ---------------------------------------------------------------------------

def node_t2_request_subquestion_solutions(state: CoursewareState) -> Dict[str, Any]:
    """T2.5: 强制为各题的小问设问批次派发独立子 Agent，生成详尽参考解答。"""
    project_root = state.get("project_root", ".")
    run_id = state.get("run_id", "")
    expected_tasks = state.get("expected_tasks", [])
    history = list(state.get("history", []))

    if not expected_tasks:
        return {
            "stage": "ABORTED",
            "routing_action": "HALT",
            "error_message": "T2.5 没有 expected_tasks，无法派发小问参考解答",
            "history": history
        }

    # 1. 调用 broker 签发 solution packets
    runner = ScriptRunner(project_root)
    code, stdout, stderr = runner.run_script("broker.py", [
        project_root, "issue_solution_packets", "--run-id", run_id
    ])
    if code != 0:
        msg = f"签发解答包失败: {stderr or stdout}"
        history.append(f"T2.5: {msg}")
        return {
            "stage": "ABORTED",
            "routing_action": "HALT",
            "error_message": msg,
            "history": history
        }

    # 2. 组装批次派发单（Sheets）
    sheets: List[Dict[str, Any]] = []
    for tid in expected_tasks:
        sheet = build_request(
            state,
            stage="T2_SUBQUESTION_SOLUTIONS",
            role="subquestion-solver",
            prompt_file="subquestion-solution.md",
            schema_file="solution-task.schema.json",
            artifacts=["staging/%s/tasks/%s/solution-task.json" % (run_id, tid)],
            instruction=(
                f"为任务 {tid} 中的每一个探究小问分别编写严谨、独立的参考解答，"
                f"写入 staging/{run_id}/tasks/{tid}/solution-task.json。"
                f"严禁集中在某一问回答，严禁出现'同上'、'同理'等省略词！"
                f"各小问 subquestion_id 与 prompt 必须与设问严格一致。"
            ),
            command=f"按 prompts/subquestion-solution.md 为 {tid} 生成各小问参考解答",
            context={
                "task_id": tid,
                "packet_ref": f"staging/{run_id}/solution-packets/{tid}.json",
                "student_task_ref": f"staging/{run_id}/tasks/{tid}/student-task.json",
                "receipts_dir": f"staging/{run_id}/solution-receipts",
            }
        )
        write_request_file(project_root, sheet)
        sheets.append(sheet)

    history.append(f"T2.5: 已为 {len(sheets)} 个任务派发各小问参考解答撰写单")
    return {
        "stage": "T2_SOLUTIONS_PENDING",
        "pending_dispatch_batch": sheets,
        "batch_stage": "T2_SUBQUESTION_SOLUTIONS",
        "worker_available": worker_registry.available_for_role("subquestion-solver") or worker_registry.available,
        "routing_action": "PROCEED",
        "history": history
    }


def node_t2_await_subquestion_solutions(state: CoursewareState) -> Dict[str, Any]:
    """T2.5: 等待批次逐小问参考解答产物并逐单校验。"""
    sheets = state.get("pending_dispatch_batch") or []
    if not sheets:
        return {
            "stage": "ABORTED",
            "routing_action": "HALT",
            "error_message": "T2.5 缺少批次派发单（pending_dispatch_batch 为空），无法等待产物",
            "history": list(state.get("history", []))
        }
    return run_batch_dispatch(
        state, sheets,
        success_stage="T2_SOLUTIONS_CHECKED",
        success_history="各小问参考解答已生成（%d 题）" % len(sheets)
    )
