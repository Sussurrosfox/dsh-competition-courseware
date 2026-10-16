"""runner.py — CLI and API driver for the LangGraph Courseware Pipeline.
Supports executing the graph, handling Phase 0 interrupts, inspecting checkpoints, and exporting diagrams.

断点恢复默认落在 SQLite（``workflow/checkpoints/sqlite_saver.py``），因此
``--run`` 与 ``--resume`` 可以分属不同 Python 进程：检查点与 interrupt 都持久
保存，按 ``--thread-id`` 原地恢复。只有不传 ``--checkpoint-db`` 时才退回进程内
内存检查点（仅用于查看/导出，进程退出即丢失）。
"""

import argparse
import json
import os
import sys
from typing import Dict, Any, Optional

from langgraph.types import Command

from .graph import create_courseware_graph
from .nodes.human_nodes import verify_phase0_grant
from .state import CoursewareState

DEFAULT_DB_NAME = "courseware_checkpoints.sqlite"

# 进程内缓存：同一路径复用同一 SqliteSaver（每线程一个连接）
_SAVER_CACHE: Dict[str, Any] = {}


def default_checkpoint_db(project_root: str) -> str:
    """检查点数据库默认落在项目根的 .langgraph/ 下，避免污染教学产物目录。"""
    return os.path.join(os.path.abspath(project_root), ".langgraph", DEFAULT_DB_NAME)


def get_checkpointer(db_path: Optional[str] = None):
    """返回一个可跨进程恢复的 SQLite checkpointer。

    未指定路径时退回进程内内存 checkpointer（仅供查看/导出，进程退出即丢失）。
    同一路径在进程内缓存复用，避免重复打开数据库。
    """
    if not db_path:
        from langgraph.checkpoint.memory import MemorySaver
        saver = _SAVER_CACHE.get("memory")
        if saver is None:
            saver = MemorySaver()
            _SAVER_CACHE["memory"] = saver
        return saver

    key = os.path.abspath(db_path)
    saver = _SAVER_CACHE.get(key)
    if saver is None:
        from .checkpoints import SqliteSaver
        saver = SqliteSaver(key)
        _SAVER_CACHE[key] = saver
    return saver


def get_graph(db_path: Optional[str] = None):
    """返回编译后的图。

    默认用内存 checkpointer（仅供查看/导出，无副作用）；CLI 与需要跨进程恢复的
    调用方必须显式传 ``db_path``，走 SQLite 持久化检查点。
    """
    if db_path:
        return create_courseware_graph(checkpointer=get_checkpointer(db_path))
    return create_courseware_graph()


def run_pipeline(
    project_root: str,
    selected_problems: Optional[list] = None,
    user_spec: Optional[Dict[str, Any]] = None,
    deliver_teacher: bool = False,
    thread_id: str = "courseware_session",
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """Execute or advance the courseware pipeline."""
    graph = get_graph(db_path)
    config = {"configurable": {"thread_id": thread_id}}

    init_history = ["Runner: Starting pipeline execution."]

    # G4.2 零信任门禁：调用方（API/脚本）预置 user_spec.confirmed=True 时，必须
    # 持有有效的“已消费令牌”凭据（phase0_grant.json + 摘要一致 + grant_id 一致），
    # 否则剥离 confirmed 并记录告警，Phase 0 强制走真人令牌确认。
    if user_spec and user_spec.get("confirmed") is True:
        if not verify_phase0_grant(os.path.abspath(project_root), user_spec):
            user_spec = dict(user_spec)
            user_spec["confirmed"] = False
            warning = (
                "Runner: [G4.2] 调用方预置 confirmed 但无有效 phase0 grant 凭据，"
                "已剥离 confirmed；Phase 0 将强制挂起等待真人授权码确认。"
            )
            init_history.append(warning)
            print("[!] " + warning, file=sys.stderr)

    init_state: CoursewareState = {
        "project_root": os.path.abspath(project_root),
        "stage": "INIT",
        "selected_problems": selected_problems or [],
        "user_spec": user_spec,
        "deliver_teacher": deliver_teacher,
        "history": init_history
    }

    # Invoke graph
    result = graph.invoke(init_state, config=config)
    return result


def resume_pipeline(
    user_response: Dict[str, Any],
    deliver_teacher: Optional[bool] = None,
    thread_id: str = "courseware_session",
    db_path: Optional[str] = None
) -> Dict[str, Any]:
    """Resume pipeline from a Phase 0 human interrupt."""
    graph = get_graph(db_path)
    config = {"configurable": {"thread_id": thread_id}}

    if deliver_teacher is not None:
        user_response = dict(user_response)
        user_response["deliver_teacher"] = deliver_teacher

    # Resume graph using Command(resume=...)
    resume_cmd = Command(resume=user_response)
    result = graph.invoke(resume_cmd, config=config)
    return result


def get_current_state(
    thread_id: str = "courseware_session",
    db_path: Optional[str] = None
) -> Any:
    """Inspect current state and pending interrupts."""
    graph = get_graph(db_path)
    config = {"configurable": {"thread_id": thread_id}}
    return graph.get_state(config)


def export_mermaid() -> str:
    """Return mermaid representation of the graph."""
    graph = get_graph()
    return graph.get_graph().draw_mermaid()


def _print_result(res: Dict[str, Any]) -> None:
    print(f"\n[+] 阶段: {res.get('stage')}")
    err = res.get("error_message")
    if err:
        print(f"[x] 错误: {err}")
    for h in res.get("history", []):
        print(f"    {h}")


def main():
    parser = argparse.ArgumentParser(description="LangGraph 竞赛教研与学案制作流水线驱动器")
    parser.add_argument("--project", default=".", help="项目根目录")
    parser.add_argument("--run", action="store_true", help="启动流水线")
    parser.add_argument("--resume", action="store_true", help="从断点恢复执行")
    parser.add_argument("--title", help="学案主标题（用于 Phase 0 确认）")
    parser.add_argument("--mode", choices=["guided"],
                        default="guided", help="教学模式（唯一取值 guided 分步引导）")
    parser.add_argument("--status", action="store_true", help="打印当前状态与断点")
    parser.add_argument("--visualize", action="store_true", help="输出 Mermaid 流程图")
    parser.add_argument("--thread-id", default="session_1", help="会话线程 ID")
    parser.add_argument("--checkpoint-db", default=None,
                        help="检查点 SQLite 路径（默认 <project>/.langgraph/%s）" % DEFAULT_DB_NAME)
    parser.add_argument("--select", action="append", default=None,
                        help="入选题号（可重复）；必须与冻结 brief 的 selected_problem_ids 一致")
    parser.add_argument("--auth-token", default=None,
                        help="Phase 0 人工授权码（由真人用户从 <project>/.langgraph/phase0_token.txt 查阅后提供）")
    parser.add_argument("--deliver-teacher", action="store_true", default=None,
                        help="交付 teacher.tex 与 teacher.pdf（默认关闭）")
    parser.add_argument("--no-deliver-teacher", dest="deliver_teacher", action="store_false",
                        help="显式声明不交付 teacher.tex 与 teacher.pdf")

    args = parser.parse_args()

    if args.visualize:
        print("\n=== StateGraph Mermaid Diagram ===\n")
        print(export_mermaid())
        print("\n==================================\n")
        return

    db_path = args.checkpoint_db or default_checkpoint_db(args.project)

    if args.run:
        print(f"[*] 启动 LangGraph 流水线: 项目={args.project}, Thread={args.thread_id} ...")
        print(f"[*] 检查点数据库: {db_path}")
        spec = None
        if args.title:
            # Candidate suggestions only; must remain unconfirmed so Phase 0 breakpoint strictly interrupts for human confirmation
            spec = {"main_title": args.title, "lesson_mode": args.mode, "confirmed": False}

        deliver_teacher = bool(args.deliver_teacher) if args.deliver_teacher is not None else False
        res = run_pipeline(args.project, selected_problems=args.select,
                           user_spec=spec, deliver_teacher=deliver_teacher,
                           thread_id=args.thread_id, db_path=db_path)

        # Check if paused at interrupt
        state_snap = get_current_state(args.thread_id, db_path)
        if state_snap.tasks and any(t.interrupts for t in state_snap.tasks):
            print("\n[!] 流水线安全挂起，等待人工 / Agent 产物：")
            for task in state_snap.tasks:
                for intr in task.interrupts:
                    val = intr.value or {}
                    print(f"    - 挂起原因: {val.get('description')}")
                    if val.get("artifacts"):
                        print(f"    - 待产产物: {json.dumps(val['artifacts'], ensure_ascii=False)}")
                    if val.get("request_ref"):
                        print(f"    - 派发单: {val['request_ref']}")
                    if val.get("command"):
                        print(f"    - 操作指引: {val['command']}")
                    if val.get("token_file"):
                        print(f"    - 人工授权码文件: {val['token_file']}（仅供真人查阅）")
                    print(f"    - 候选参数: {json.dumps(val.get('fields') or {}, ensure_ascii=False, indent=2)}")
            print("\n[人工介入指引] 以下恢复命令仅供真人用户确认后使用；")
            print("Agent 必须先调用 ask_question 获得真人明确确认与授权码，严禁自行读取授权码文件或静默恢复！")
            print(f'python -m workflow.runner --project {args.project} --resume --title "<确认的标题>" --mode {args.mode} --thread-id {args.thread_id} --checkpoint-db "{db_path}" --auth-token "<真人提供的授权码>"')
        else:
            _print_result(res)

    elif args.resume:
        if not args.title:
            print("[x] 错误: 恢复执行必须提供 --title 参数")
            sys.exit(1)
        # Phase 0 物理门禁：恢复必须携带真人授权码，缺失或不匹配一律拒绝，
        # 防止执行端 Agent 未经真人确认越权代签（静默恢复）。
        token_path = os.path.join(os.path.abspath(args.project), ".langgraph", "phase0_token.txt")
        expected_token = None
        if os.path.exists(token_path):
            with open(token_path, "r", encoding="utf-8") as f:
                expected_token = f.read().strip() or None
        if expected_token is not None:
            if not args.auth_token or args.auth_token.strip() != expected_token:
                print("[PERMISSION_DENIED] Phase 0 resume requires explicit interactive confirmation.")
                print(f"    人工授权码位于 {token_path}，请真人用户查阅后通过 --auth-token 提供。")
                print("    Agent 严禁自行读取授权码文件；必须先向真人用户确认标题、模式并索取授权码。")
                sys.exit(2)
        elif not args.auth_token:
            # 兼容无授权码文件的旧检查点：仍要求显式声明，杜绝静默恢复。
            print("[PERMISSION_DENIED] Phase 0 resume requires explicit interactive confirmation.")
            print("    未找到授权码文件；请真人用户确认后通过 --auth-token 显式提供授权声明。")
            sys.exit(2)
        resp = {"main_title": args.title, "lesson_mode": args.mode, "confirmed": True,
                "auth_token": args.auth_token.strip()}
        if args.deliver_teacher is not None:
            resp["deliver_teacher"] = args.deliver_teacher
        print(f"[*] 恢复 LangGraph 流水线 (Thread={args.thread_id}): {resp} ...")
        print(f"[*] 检查点数据库: {db_path}")
        res = resume_pipeline(resp, deliver_teacher=args.deliver_teacher,
                              thread_id=args.thread_id, db_path=db_path)
        _print_result(res)

    elif args.status:
        state_snap = get_current_state(args.thread_id, db_path)
        print(f"[*] 当前 Thread: {args.thread_id}")
        print(f"[*] 检查点数据库: {db_path}")
        if state_snap.values:
            print(f"    - 当前 Stage: {state_snap.values.get('stage')}")
            print(f"    - 历史事件: {len(state_snap.values.get('history', []))} 项")
            for h in state_snap.values.get("history", []):
                print(f"      * {h}")
            if state_snap.values.get("error_message"):
                print(f"    - 错误: {state_snap.values['error_message']}")
        else:
            print("    (无活跃状态)")


if __name__ == "__main__":
    main()
