"""launcher.py — Unified Launcher for LangGraph Competition Courseware.
Supports launching the interactive Web UI workbench, or running in CLI mode.

Usage:
    # 1. Launch Web UI (default, opens browser automatically)
    python launcher.py

    # 2. Specify custom port or host
    python launcher.py --port 8080 --host 0.0.0.0

    # 3. Fast headless mode without opening browser
    python launcher.py --no-browser

    # 4. Fallback to CLI mode
    python launcher.py --cli --run --title "Lucas定理"
"""

import argparse
import os
import sys

# Ensure current directory is in sys.path
_CUR_DIR = os.path.dirname(os.path.abspath(__file__))
if _CUR_DIR not in sys.path:
    sys.path.insert(0, _CUR_DIR)

# Fix Windows console UTF-8 output & title cleanly
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        import ctypes
        ctypes.windll.kernel32.SetConsoleTitleW("LangGraph 竞赛教研流水线工作台")
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="LangGraph 竞赛教研流水线启动器 (Launcher)")
    parser.add_argument("--port", type=int, default=7860, help="Web UI 服务端口 (默认: 7860)")
    parser.add_argument("--host", default="127.0.0.1", help="Web UI 服务监听地址 (默认: 127.0.0.1)")
    parser.add_argument("--no-browser", action="store_true", help="不自动拉起默认浏览器")
    parser.add_argument("--cli", action="store_true", help="切换到经典 CLI 命令行模式")
    
    parser.add_argument("--input", dest="input_dir", help="输入文件夹路径 (包含选题、Markdown或既有research)")
    parser.add_argument("--output", dest="output_dir", help="输出工作区路径 (用于生成teaching/与双源PDF)")
    
    # CLI pass-through arguments
    parser.add_argument("--project", default=".", help="CLI模式：项目根目录")
    parser.add_argument("--run", action="store_true", help="CLI模式：启动流水线")
    parser.add_argument("--resume", action="store_true", help="CLI模式：从断点恢复执行")
    parser.add_argument("--title", help="CLI模式：学案主标题")
    parser.add_argument("--mode", choices=["guided"],
                        default="guided", help="CLI模式：教学模式（唯一取值 guided 分步引导）")
    parser.add_argument("--status", action="store_true", help="CLI模式：打印状态")
    parser.add_argument("--visualize", action="store_true", help="CLI模式：输出Mermaid图")
    parser.add_argument("--solution", action="store_true", help="CLI模式：生成并编译学生解答版 (student_solution.tex & pdf)")
    parser.add_argument("--thread-id", default="session_1", help="会话线程 ID")
    parser.add_argument("--deliver-teacher", action="store_true", default=None, help="CLI模式：交付 teacher.tex 与 teacher.pdf（默认关闭）")
    parser.add_argument("--no-deliver-teacher", dest="deliver_teacher", action="store_false", help="CLI模式：显式声明不交付 teacher.tex 与 teacher.pdf")
    parser.add_argument("--select", nargs="*", default=None, help="CLI模式：入选题号")
    parser.add_argument("--auth-token", help="CLI模式：Phase 0 人工授权码")

    args, unknown = parser.parse_known_args()

    if args.cli:
        print("[*] 切换至 CLI 命令行运行模式...")
        project_arg = args.output_dir or args.input_dir or args.project
        if args.solution:
            from scripts.gen_student_solution import generate_student_solution_tex, compile_tex
            p = os.path.abspath(project_arg)
            generate_student_solution_tex(p)
            compile_tex(p)
            return

        from workflow.runner import main as cli_main
        # Reconstruct sys.argv for runner.py
        cli_argv = [sys.argv[0]]
        project_arg = args.output_dir or args.input_dir or args.project
        if project_arg: cli_argv.extend(["--project", project_arg])
        if args.run: cli_argv.append("--run")
        if args.resume: cli_argv.append("--resume")
        if args.title: cli_argv.extend(["--title", args.title])
        if args.mode: cli_argv.extend(["--mode", args.mode])
        if args.status: cli_argv.append("--status")
        if args.visualize: cli_argv.append("--visualize")
        if args.thread_id: cli_argv.extend(["--thread-id", args.thread_id])
        if args.auth_token: cli_argv.extend(["--auth-token", args.auth_token])
        if args.deliver_teacher is True: cli_argv.append("--deliver-teacher")
        elif args.deliver_teacher is False: cli_argv.append("--no-deliver-teacher")
        if args.select:
            for s in args.select:
                for item in str(s).split(","):
                    item = item.strip()
                    if item:
                        cli_argv.extend(["--select", item])
        cli_argv.extend(unknown)
        sys.argv = cli_argv
        cli_main()
        return

    # Default: Launch Modern Web UI Workbench
    print("=" * 68)
    print("  [LangGraph 竞赛教研流水线 · 可视化控制台启动器]")
    print(f"  服务地址: http://{args.host}:{args.port}")
    if args.input_dir:
        print(f"  预设输入: {args.input_dir}")
    if args.output_dir:
        print(f"  预设输出: {args.output_dir}")
    print("=" * 68)

    from workflow.ui.app import launch
    launch(
        server_name=args.host,
        server_port=args.port,
        inbrowser=not args.no_browser,
        default_input_dir=args.input_dir,
        default_output_dir=args.output_dir
    )


if __name__ == "__main__":
    main()
