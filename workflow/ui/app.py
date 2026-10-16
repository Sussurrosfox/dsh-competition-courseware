"""app.py — Universal Gradio Web UI Workbench for LangGraph Courseware Pipeline.
Supports placing the workflow engine anywhere and choosing decoupled input and output directories
via native OS file manager dialogs.
"""

import os
import sys
from datetime import datetime, timezone
import gradio as gr
from typing import Dict, Any, List, Optional

# Fix Windows console UTF-8 output
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from .pipeline_manager import PipelineManager
from .folder_picker import browse_directory

# Initialize global pipeline manager
pm = PipelineManager()

# Custom modern CSS for elegant UI
CUSTOM_CSS = """
/* Global theme refinements */
.gradio-container {
    max-width: 1200px !important;
    margin: 0 auto !important;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, "Microsoft YaHei", sans-serif;
}

/* Header styling */
.header-banner {
    background: linear-gradient(120deg, #312e81 0%, #4f46e5 55%, #0891b2 100%);
    color: white;
    padding: 20px 28px;
    border-radius: 14px;
    margin-bottom: 18px;
    box-shadow: 0 10px 28px rgba(49, 46, 129, 0.25);
}
.header-banner h1 {
    font-size: 22px;
    font-weight: 700;
    margin: 0 0 4px 0;
    letter-spacing: -0.3px;
}
.header-banner p {
    font-size: 13px;
    opacity: 0.85;
    margin: 0;
}

/* Location badge */
.engine-badge {
    background: rgba(255, 255, 255, 0.14);
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    font-size: 11px;
    font-family: Consolas, monospace;
    margin-top: 10px;
}

/* Stage Progress Bar / Cards */
.stages-wrapper {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin: 12px 0;
}
.stage-chip {
    flex: 1 1 100px;
    padding: 8px 10px;
    border-radius: 10px;
    background: #ffffff;
    border: 1px solid #e5e7eb;
    text-align: center;
    transition: all 0.2s ease;
}
.stage-chip.active {
    background: #eef2ff;
    border: 1px solid #6366f1;
    box-shadow: 0 4px 14px rgba(99, 102, 241, 0.25);
    transform: translateY(-2px);
}
.stage-chip.passed {
    background: #f0fdf4;
    border: 1px solid #bbf7d0;
}
.stage-chip.passed .stage-chip-code { color: #15803d; }
.stage-chip.interrupt {
    background: #fffbeb;
    border: 1px solid #f59e0b;
    animation: pulse 1.5s infinite;
}
.stage-chip.failed {
    background: #fef2f2;
    border: 1px solid #fca5a5;
}
.stage-chip-code {
    font-weight: 700;
    font-size: 13px;
    color: #1f2937;
    margin-bottom: 2px;
}
.stage-chip.active .stage-chip-code { color: #4338ca; }
.stage-chip-title {
    font-size: 11px;
    color: #6b7280;
}

@keyframes pulse {
    0% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.35); }
    70% { box-shadow: 0 0 0 8px rgba(245, 158, 11, 0); }
    100% { box-shadow: 0 0 0 0 rgba(245, 158, 11, 0); }
}

/* Phase 0 Alert Card */
.phase0-card {
    background: #fffbeb;
    border: 1px solid #fbbf24;
    border-left: 5px solid #f59e0b;
    border-radius: 10px;
    padding: 16px 20px;
    margin: 12px 0;
}

/* Gate summary badges */
.gate-badge-pass {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    background: #dcfce7;
    color: #166534;
    font-weight: 600;
    font-size: 12px;
}
.gate-badge-fail {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    background: #fee2e2;
    color: #991b1b;
    font-weight: 600;
    font-size: 12px;
}
.gate-badge-wait {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 999px;
    background: #f1f5f9;
    color: #64748b;
    font-size: 12px;
}

/* PDF Deliverable Card */
.pdf-card {
    border: 1px solid #e5e7eb;
    border-radius: 12px;
    padding: 18px 16px;
    background: #ffffff;
    text-align: center;
    box-shadow: 0 2px 10px rgba(0, 0, 0, 0.05);
    transition: box-shadow 0.2s ease;
}
.pdf-card:hover {
    box-shadow: 0 6px 18px rgba(0, 0, 0, 0.08);
}
"""

STAGE_DEFINITIONS = [
    {"code": "R1", "name": "题目初筛", "stage_key": "R1_SCORED", "node": "r1_screen_problems"},
    {"code": "R2", "name": "深度研学", "stage_key": "R2_RESEARCHED", "node": "r2_deep_research"},
    {"code": "R4", "name": "冻结交接", "stage_key": "R4_FROZEN", "node": "r4_freeze_brief"},
    {"code": "P0", "name": "人机断点", "stage_key": "PHASE0_BREAKPOINT", "node": "phase0_breakpoint"},
    {"code": "T0", "name": "工程初始化", "stage_key": "T0_READY", "node": "t0_init_project"},
    {"code": "T1", "name": "双轨架构", "stage_key": "T1_ARCHITECTED", "node": "t1_architect"},
    {"code": "T2", "name": "任务与门禁", "stage_key": "T2_CHECKED", "node": "t2_broker_submit_and_check"},
    {"code": "T3", "name": "LaTeX编译", "stage_key": "T3_BUILT", "node": "t3_latex_build"},
    {"code": "T4", "name": "交付发布", "stage_key": "T4_DELIVERED", "node": "t4_promote_and_deliver"}
]


def render_stages_html(current_stage: str, active_node: str = "") -> str:
    """Generate HTML snippet visualizing pipeline stages and current active position."""
    chips = []
    stage_idx_map = {
        "INIT": 0,
        "R1_SCORED": 1,
        "R2_RESEARCHED": 2,
        "R4_FROZEN": 3,
        "PHASE0_BREAKPOINT": 4,
        "T0_READY": 5,
        "T1_ARCHITECTED": 6,
        "T2_STAGED": 7,
        "T2_CHECKED": 7,
        "T3_BUILT": 8,
        "T4_DELIVERED": 9,
        "COMPLETED": 10
    }
    cur_idx = stage_idx_map.get(current_stage, 0)

    for idx, s in enumerate(STAGE_DEFINITIONS, start=1):
        classes = ["stage-chip"]
        if current_stage == s["stage_key"] or active_node == s["node"]:
            if current_stage == "PHASE0_BREAKPOINT":
                classes.append("interrupt")
            else:
                classes.append("active")
        elif cur_idx > idx:
            classes.append("passed")
        elif cur_idx == 10:
            classes.append("passed")

        chips.append(f"""
        <div class="{' '.join(classes)}">
            <div class="stage-chip-code">{s['code']}</div>
            <div class="stage-chip-title">{s['name']}</div>
        </div>
        """)

    return f'<div class="stages-wrapper">{"".join(chips)}</div>'


def format_interrupt_status(intr_val: Any, dispatch_results: Optional[Dict[str, Any]] = None,
                            default: str = "⏸️ 流水线挂起，等待确认") -> str:
    """渲染挂起原因；批次派发（AGENT_DISPATCH_BATCH）展示派发单清单与完成状态。"""
    if not isinstance(intr_val, dict):
        return default
    if intr_val.get("type") == "AGENT_DISPATCH_BATCH":
        results = dispatch_results or {}
        sheets = intr_val.get("sheets", [])
        lines = ["⏸️ 批次派发挂起 [%s]：等待 %d 份派发单产物"
                 % (intr_val.get("stage", "?"), len(sheets))]
        for sheet in sheets:
            did = sheet.get("dispatch_id", "?")
            status = (results.get(did) or {}).get("status", "pending")
            arts = ", ".join(sheet.get("artifacts", []))
            lines.append("- %s（role=%s）：%s [%s]" % (did, sheet.get("role", "?"), arts, status))
        return "\n".join(lines)
    if intr_val.get("type") == "AGENT_DISPATCH":
        return "⏸️ 派发挂起 [%s]：%s" % (
            intr_val.get("stage", "?"),
            intr_val.get("description", "等待产物后恢复执行"))
    return default


def render_gate_reports_html(reports: List[Dict[str, Any]]) -> str:
    """Generate styled HTML table for gate check reports."""
    rows = []
    total = len(reports)
    passed_cnt = sum(1 for r in reports if r["passed"] is True)
    failed_cnt = sum(1 for r in reports if r["passed"] is False)
    unrun_cnt = total - passed_cnt - failed_cnt

    summary_bar = f"""
    <div style="display: flex; gap: 16px; margin-bottom: 14px;">
        <div style="flex:1; background:#f8fafc; border:1px solid #e2e8f0; border-radius:8px; padding:10px 14px; text-align:center;">
            <div style="font-size:11px; color:#64748b;">全部质量门禁</div>
            <div style="font-size:18px; font-weight:700; color:#1e293b;">{total} 项</div>
        </div>
        <div style="flex:1; background:#f0fdf4; border:1px solid #bbf7d0; border-radius:8px; padding:10px 14px; text-align:center;">
            <div style="font-size:11px; color:#15803d;">验证通过 (PASS)</div>
            <div style="font-size:18px; font-weight:700; color:#166534;">{passed_cnt} 项</div>
        </div>
        <div style="flex:1; background:#fef2f2; border:1px solid #fecaca; border-radius:8px; padding:10px 14px; text-align:center;">
            <div style="font-size:11px; color:#b91c1c;">拦截/待修复 (REJECT)</div>
            <div style="font-size:18px; font-weight:700; color:#991b1b;">{failed_cnt} 项</div>
        </div>
        <div style="flex:1; background:#f1f5f9; border:1px solid #e2e8f0; border-radius:8px; padding:10px 14px; text-align:center;">
            <div style="font-size:11px; color:#64748b;">未执行 / 待触发</div>
            <div style="font-size:18px; font-weight:700; color:#475569;">{unrun_cnt} 项</div>
        </div>
    </div>
    """

    for r in reports:
        if r["passed"] is True:
            badge = '<span class="gate-badge-pass">✔ PASS</span>'
        elif r["passed"] is False:
            badge = f'<span class="gate-badge-fail">✖ {r["status"]}</span>'
        else:
            badge = '<span class="gate-badge-wait">⚪ 未运行</span>'

        issues_summary = ""
        if r["issues"]:
            issue_items = []
            for iss in r["issues"][:3]:
                sev = iss.get("severity", "FAIL")
                color = "#dc2626" if sev in ["FAIL", "HIGH_RISK"] else "#ea580c"
                msg = iss.get("message", "")
                issue_items.append(f'<li style="margin-bottom:2px; font-size:12px;"><b style="color:{color}">[{sev}]</b> {msg}</li>')
            if len(r["issues"]) > 3:
                issue_items.append(f'<li style="font-size:11px; color:#64748b;">...以及额外 {len(r["issues"])-3} 条详细记录</li>')
            issues_summary = f'<ul style="margin:4px 0 0 0; padding-left:16px;">{"".join(issue_items)}</ul>'
        elif r["passed"] is True:
            issues_summary = '<span style="color:#16a34a; font-size:12px;">各项规范与盒模型核验无误</span>'
        else:
            issues_summary = '<span style="color:#94a3b8; font-size:12px;">尚未生成报告</span>'

        rows.append(f"""
        <tr style="border-bottom: 1px solid #f1f5f9;">
            <td style="padding: 10px 12px; font-weight: 600; font-size: 13px;">{r['label']}</td>
            <td style="padding: 10px 12px;">{badge}</td>
            <td style="padding: 10px 12px; font-size: 12px; color: #64748b; font-family: monospace;">{r['path']}</td>
            <td style="padding: 10px 12px;">{issues_summary}</td>
        </tr>
        """)

    table_html = f"""
    {summary_bar}
    <div style="border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; background: white;">
        <table style="width: 100%; border-collapse: collapse; text-align: left;">
            <thead>
                <tr style="background: #f8fafc; border-bottom: 1px solid #e2e8f0; font-size: 12px; color: #475569;">
                    <th style="padding: 10px 12px;">门禁名称</th>
                    <th style="padding: 10px 12px; width: 130px;">状态</th>
                    <th style="padding: 10px 12px; width: 260px;">核验报告路径</th>
                    <th style="padding: 10px 12px;">诊断与问题摘要</th>
                </tr>
            </thead>
            <tbody>
                {"".join(rows)}
            </tbody>
        </table>
    </div>
    """
    return table_html


def render_artifacts_html(artifacts: Dict[str, Any]) -> str:
    """Generate HTML cards for PDF deliverables."""
    stu = artifacts.get("student_pdf", {})
    tch = artifacts.get("teacher_pdf", {})

    def _badge(ex: bool, false_text="⚪ 未交付"):
        return '<span style="color:#16a34a; font-weight:600;">✔ 已交付</span>' if ex else f'<span style="color:#94a3b8;">{false_text}</span>'

    html = f"""
    <div style="display: flex; gap: 20px; margin: 12px 0;">
        <div class="pdf-card" style="flex: 1;">
            <div style="font-size: 28px; margin-bottom: 6px;">📘</div>
            <div style="font-size: 16px; font-weight: 700; color: #1e293b;">学生版学案 (student.pdf)</div>
            <div style="margin: 8px 0;">{_badge(stu.get('exists'), '⚪ 未交付')}</div>
            <div style="font-size: 12px; color: #64748b; margin-bottom: 4px;">文件大小: <b>{stu.get('size_str')}</b></div>
            <div style="font-size: 11px; color: #94a3b8; font-family: monospace;">{stu.get('path')}</div>
        </div>
        <div class="pdf-card" style="flex: 1;">
            <div style="font-size: 28px; margin-bottom: 6px;">📕</div>
            <div style="font-size: 16px; font-weight: 700; color: #1e293b;">教师版讲义 (teacher.pdf)</div>
            <div style="margin: 8px 0;">{_badge(tch.get('exists'), '⚪ 未交付（默认关闭，教研已就绪）')}</div>
            <div style="font-size: 12px; color: #64748b; margin-bottom: 4px;">文件大小: <b>{tch.get('size_str')}</b></div>
            <div style="font-size: 11px; color: #94a3b8; font-family: monospace;">{tch.get('path')}</div>
        </div>
    </div>
    """
    return html


def create_ui(default_input_dir: Optional[str] = None, default_output_dir: Optional[str] = None):
    """Build the Gradio Blocks UI layout with decoupled input/output folder pickers."""
    inp_init = os.path.abspath(default_input_dir) if default_input_dir else pm.default_input_dir
    # Default output workspace = "self" subfolder under the input folder
    out_init = os.path.abspath(default_output_dir) if default_output_dir else os.path.join(inp_init, "self")
    init_scan = pm.scan_workspace(inp_init, out_init)
    initial_problems = init_scan["problems"]
    initial_title = init_scan.get("brief_title") or "竞赛数学专题：组合数与同余式进阶"

    def _compute_cmd(inp_dir, out_dir, same_dir=False):
        target_out = inp_dir if same_dir else (out_dir or os.path.join(os.path.abspath(inp_dir), "self"))
        target_task = os.path.join(target_out, "AGY_TASK.md")
        try:
            rel = os.path.relpath(target_task, os.getcwd()).replace("\\", "/")
        except Exception:
            rel = "self/AGY_TASK.md" if not same_dir else "AGY_TASK.md"
        return f"请执行 @[{rel}]"

    initial_agy_cmd = _compute_cmd(inp_init, out_init, False)

    theme = gr.themes.Soft(primary_hue="indigo", neutral_hue="slate")
    with gr.Blocks(title="LangGraph 竞赛教研流水线工作台", css=CUSTOM_CSS, theme=theme) as demo:
        # Header Banner
        gr.HTML(f"""
        <div class="header-banner">
            <h1>⚡ LangGraph 竞赛教研与学案制作工作台</h1>
            <p>目录自由选配 · 状态机调度 · 人机断点确认 · 质量门禁校验</p>
            <div class="engine-badge">引擎位置: {pm.engine_root}</div>
        </div>
        """)

        # Workspace Directory Configuration (Decoupled Input & Output)
        with gr.Group():
            gr.Markdown("### 📂 工作区配置")

            # Input Directory Row
            with gr.Row():
                input_dir_input = gr.Textbox(
                    label="📥 输入文件夹",
                    value=inp_init,
                    scale=7,
                    placeholder="包含原始题目、Markdown 或既有 research/ 的目录"
                )
                browse_input_btn = gr.Button("浏览…", scale=2, variant="secondary")

            # In-place / Separate Output Toggle
            with gr.Row():
                same_dir_checkbox = gr.Checkbox(
                    label="输出与输入同目录（原地构建）",
                    value=(inp_init == out_init)
                )

            # Output Directory Row (Visible when same_dir_checkbox is False)
            with gr.Row(visible=(inp_init != out_init)) as output_row:
                output_dir_input = gr.Textbox(
                    label="📤 输出工作区",
                    value=out_init,
                    scale=7,
                    placeholder="默认为 <输入文件夹>/self；将生成 teaching/、build/、双 PDF 与 release/"
                )
                browse_output_btn = gr.Button("浏览…", scale=2, variant="secondary")

            # Quick helper toolbar
            with gr.Row():
                rescan_btn = gr.Button("🔄 重新扫描", variant="secondary", size="sm")
                set_cwd_btn = gr.Button("📌 设为当前目录", variant="secondary", size="sm")
                sync_output_btn = gr.Button("🔁 输出重置为默认 (输入/self)", variant="secondary", size="sm")

        # Problem Selector & Execution Bar
        with gr.Row():
            with gr.Column(scale=5):
                problems_checkbox = gr.CheckboxGroup(
                    label="🎯 入选题目",
                    choices=initial_problems,
                    value=initial_problems,
                    info="从输入目录自动扫描；勾选本次需要制作的题目"
                )
            with gr.Column(scale=3):
                thread_id_input = gr.Textbox(
                    label="🧵 会话线程 ID",
                    value="session_gui_01",
                    placeholder="session_01"
                )
                status_box = gr.Textbox(
                    label="📌 当前状态",
                    value="🟢 空闲就绪 (Ready)",
                    interactive=False
                )
                with gr.Row():
                    run_btn = gr.Button("🚀 一键启动流水线", variant="primary", scale=2)
                    refresh_btn = gr.Button("📊 刷新状态", variant="secondary", scale=1)

        # Antigravity Task Generator Card (Method 3: Prompt & Task Export)
        with gr.Accordion("🤖 Antigravity 协同：生成启动任务书 (AGY_TASK.md)", open=True):
            gr.Markdown("💡 **人机协同工作流**：在下方生成或更新任务书后，可一键复制对话执行指令，直接在 Antigravity 聊天窗口发送即可全自动派发推进。")
            with gr.Row(equal_height=True):
                agy_cmd_box = gr.Textbox(
                    value=initial_agy_cmd,
                    label="💬 对话执行指令（支持右侧按钮一键复制，或直接点击框内右上角复制图标）",
                    show_copy_button=True,
                    interactive=False,
                    scale=4
                )
                copy_cmd_btn = gr.Button("📋 一键复制指令", variant="secondary", scale=1)
            with gr.Row():
                with gr.Column(scale=2):
                    gen_agy_task_btn = gr.Button("📝 生成 / 更新 AGY_TASK.md", variant="primary")
                with gr.Column(scale=3):
                    agy_task_status = gr.Markdown("🟢 任务书准备就绪（点击左侧按钮生成/更新）")
            with gr.Accordion("👀 预览任务书内容", open=False):
                agy_task_preview = gr.Code(label="AGY_TASK.md 预览", language="markdown", interactive=False)

        # Stage Progress Visualizer
        stages_html = gr.HTML(render_stages_html("INIT"))

        # Phase 0 Breakpoint Dialog (Conditionally visible)
        with gr.Group(visible=False) as phase0_box:
            gr.HTML("""
            <div class="phase0-card">
                <div style="font-size: 16px; font-weight: 700; color: #92400e; margin-bottom: 6px;">
                    ⏸️ 流水线已在【Phase 0 人机物理断点】安全挂起，等待教师核准：
                </div>
                <div style="font-size: 13px; color: #b45309;">
                    根据零信任设计规范，研学交接包已完成哈希锁定。请指定本期学案主标题后恢复推进（教学模式固定为 guided 分步引导）。
                </div>
                <div style="font-size: 13px; color: #b45309; margin-top: 6px;">
                    🔑 人工授权码位于 <code>&lt;输出工作区&gt;/.langgraph/phase0_token.txt</code>，请<b>真人</b>查阅后手工填入下方授权码输入框；
                    <b>请勿让自动化代理代读、代填该授权码</b>（系统不会自动读取该文件）。
                </div>
            </div>
            """)
            with gr.Row():
                with gr.Column(scale=3):
                    main_title_input = gr.Textbox(
                        label="🏷️ 学案主标题",
                        value=initial_title,
                        placeholder="例如：4.5 包含有理数和组合数的同余式——Lucas定理深度探究"
                    )
                with gr.Column(scale=2):
                    lesson_mode_radio = gr.Radio(
                        label="🎓 教学模式",
                        choices=[
                            ("分步引导 (主探究线)", "guided"),
                        ],
                        value="guided"
                    )
                    deliver_teacher_cb = gr.Checkbox(
                        label="📕 交付教师版 (teacher.tex 与 teacher.pdf，默认关闭)",
                        value=False
                    )
                    auth_token_input = gr.Textbox(
                        label="🔑 人工授权码（必填，由真人查阅 .langgraph/phase0_token.txt 后手工输入）",
                        placeholder="8 位授权码；严禁自动化代理代读代填",
                        value="",
                        type="password"
                    )
            with gr.Row():
                confirm_resume_btn = gr.Button("✅ 确认配置并恢复流水线推进", variant="primary", size="lg")

        # Main Workbench Tabs
        with gr.Tabs():
            # Tab 1: Gate Inspector
            with gr.TabItem("🛡️ 质量门禁"):
                gate_reports_html = gr.HTML(render_gate_reports_html(pm.get_gate_reports(pm.default_output_dir)))

            # Tab 2: Artifacts & PDFs
            with gr.TabItem("📦 交付成果"):
                artifacts_html = gr.HTML(render_artifacts_html(pm.get_artifacts(pm.default_output_dir)))
                with gr.Row():
                    student_download = gr.File(
                        label="下载学生版 PDF (student.pdf)",
                        value=os.path.join(pm.default_output_dir, "student.pdf") if os.path.exists(os.path.join(pm.default_output_dir, "student.pdf")) else None,
                        interactive=False
                    )
                    teacher_download = gr.File(
                        label="下载教师版 PDF (teacher.pdf)",
                        value=os.path.join(pm.default_output_dir, "teacher.pdf") if os.path.exists(os.path.join(pm.default_output_dir, "teacher.pdf")) else None,
                        interactive=False
                    )
                with gr.Accordion("📄 查看发布交付清单 (release/manifest.json)", open=False):
                    manifest_viewer = gr.JSON(
                        value=init_scan.get("manifest_data") or {"info": "manifest.json 尚未生成"}
                    )

            # Tab 3: History & Live Logs
            with gr.TabItem("📜 执行日志"):
                history_output = gr.Textbox(
                    label="历史推进轨迹",
                    lines=14,
                    interactive=False,
                    placeholder="等待流水线运行..."
                )

            # Tab 4: Single-Step Debug Station
            with gr.TabItem("🛠️ 单步工位"):
                gr.Markdown("遇到门禁拦截或需定点重编译时，可直接针对当前输出工作区单独触发某个工位：")
                with gr.Row():
                    step_r1_btn = gr.Button("R1: 题目评分", size="sm")
                    step_r2_btn = gr.Button("R2: 研学核验", size="sm")
                    step_r4_btn = gr.Button("R4: 冻结Brief", size="sm")
                    step_t0_btn = gr.Button("T0: 工程初始化", size="sm")
                    step_t1_btn = gr.Button("T1: 架构门禁", size="sm")
                    step_t2_btn = gr.Button("T2: 任务与三重门禁", size="sm")
                    step_t3_btn = gr.Button("T3: XeLaTeX排版编译", size="sm")
                    step_t4_btn = gr.Button("T4: 交付打包", size="sm")
                single_step_output = gr.JSON(label="工位单步执行结果")

        # ---------------- Interaction Handlers ----------------

        def _allow_workspace_paths(*paths):
            """Register runtime-chosen workspace dirs so gr.File can serve PDFs outside cwd."""
            for p in paths:
                if not p:
                    continue
                try:
                    ap = os.path.abspath(p)
                    if ap not in demo.allowed_paths:
                        demo.allowed_paths.append(ap)
                except Exception:
                    pass

        def toggle_output_row(same_dir, inp):
            if same_dir:
                return gr.update(visible=False), inp
            else:
                # 取消原地构建时，输出默认回落到输入目录下的 self 子文件夹
                return gr.update(visible=True), os.path.join(os.path.abspath(inp), "self") if inp else inp

        same_dir_checkbox.change(
            fn=toggle_output_row,
            inputs=[same_dir_checkbox, input_dir_input],
            outputs=[output_row, output_dir_input],
            api_name=False
        )

        def on_browse_input(current_val):
            selected = browse_directory(current_val, title="请选择教研输入文件夹 (Input Folder)")
            return selected if selected else current_val

        browse_input_btn.click(
            fn=on_browse_input,
            inputs=[input_dir_input],
            outputs=[input_dir_input],
            api_name=False
        )

        def on_browse_output(current_val):
            selected = browse_directory(current_val, title="请选择学案输出工作区文件夹 (Output Workspace)")
            return selected if selected else current_val

        browse_output_btn.click(
            fn=on_browse_output,
            inputs=[output_dir_input],
            outputs=[output_dir_input],
            api_name=False
        )

        def on_sync_output(inp):
            # 输出重置为默认位置：输入目录下的 self 子文件夹
            return os.path.join(os.path.abspath(inp), "self") if inp else inp

        sync_output_btn.click(
            fn=on_sync_output,
            inputs=[input_dir_input],
            outputs=[output_dir_input],
            api_name=False
        )

        def on_set_cwd():
            cwd = os.getcwd()
            return cwd, os.path.join(cwd, "self")

        set_cwd_btn.click(
            fn=on_set_cwd,
            inputs=[],
            outputs=[input_dir_input, output_dir_input],
            api_name=False
        )

        def on_rescan(inp, out, same_dir):
            target_out = inp if same_dir else (out or os.path.join(os.path.abspath(inp), "self"))
            _allow_workspace_paths(inp, target_out)
            info = pm.scan_workspace(inp, target_out)
            probs = info["problems"]
            title = info.get("brief_title") or "竞赛数学专题：组合数与同余式进阶"
            gates_h = render_gate_reports_html(pm.get_gate_reports(target_out))
            arts_h = render_artifacts_html(pm.get_artifacts(target_out))
            s_pdf = os.path.join(target_out, "student.pdf") if info["student_pdf_exists"] else None
            t_pdf = os.path.join(target_out, "teacher.pdf") if info["teacher_pdf_exists"] else None
            m_data = info.get("manifest_data") or {"info": "manifest.json 尚未生成"}
            summary = f"扫描就绪: 输入源[{os.path.basename(inp)}] 发现 {len(probs)} 题 | 输出区[{os.path.basename(target_out)}] 已冻结={info['is_frozen']}"
            return (
                gr.update(choices=probs, value=probs),
                gr.update(value=title),
                gates_h,
                arts_h,
                s_pdf,
                t_pdf,
                m_data,
                summary
            )

        rescan_btn.click(
            fn=on_rescan,
            inputs=[input_dir_input, output_dir_input, same_dir_checkbox],
            outputs=[
                problems_checkbox,
                main_title_input,
                gate_reports_html,
                artifacts_html,
                student_download,
                teacher_download,
                manifest_viewer,
                status_box
            ],
            api_name=False
        )

        # Trigger auto-rescan when input_dir changes
        input_dir_input.change(
            fn=on_rescan,
            inputs=[input_dir_input, output_dir_input, same_dir_checkbox],
            outputs=[
                problems_checkbox,
                main_title_input,
                gate_reports_html,
                artifacts_html,
                student_download,
                teacher_download,
                manifest_viewer,
                status_box
            ],
            api_name=False
        )

        def on_gen_agy_task(inp, out, probs, th_id, same_dir):
            target_out = inp if same_dir else (out or os.path.join(os.path.abspath(inp), "self"))
            ok, path, content = pm.generate_antigravity_task(inp, target_out, probs, th_id)
            cmd = _compute_cmd(inp, out, same_dir)
            if ok:
                status = f"✅ **任务书已成功生成！**<br>📄 本地路径：`{os.path.join(target_out, 'AGY_TASK.md')}`<br>👉 **下一步**：已更新指令，点击上方【📋 一键复制指令】即可直接复制！"
            else:
                status = f"❌ 生成任务书失败: {path}"
            return status, content, cmd, "📋 一键复制指令"

        gen_agy_task_btn.click(
            fn=on_gen_agy_task,
            inputs=[input_dir_input, output_dir_input, problems_checkbox, thread_id_input, same_dir_checkbox],
            outputs=[agy_task_status, agy_task_preview, agy_cmd_box, copy_cmd_btn],
            api_name=False
        )

        copy_cmd_btn.click(
            fn=lambda: "✅ 已复制到剪贴板！",
            inputs=[],
            outputs=[copy_cmd_btn],
            js="""(cmd) => {
                const el = document.querySelector('label:has(span:contains("对话执行指令")) textarea') ||
                           document.querySelector('textarea[aria-label*="对话执行指令"]');
                const toCopy = (typeof cmd === 'string' && cmd.length > 0) ? cmd : (el ? (el.value || el.innerText || el) : '请执行 @[self/AGY_TASK.md]');
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(toCopy);
                } else {
                    const temp = document.createElement('textarea');
                    temp.value = toCopy;
                    document.body.appendChild(temp);
                    temp.select();
                    document.execCommand('copy');
                    document.body.removeChild(temp);
                }
            }""",
            api_name=False
        )

        def on_dir_change_update_cmd(inp, out, same_dir):
            return _compute_cmd(inp, out, same_dir), "📋 一键复制指令"

        for _comp in [input_dir_input, output_dir_input, same_dir_checkbox]:
            _comp.change(
                fn=on_dir_change_update_cmd,
                inputs=[input_dir_input, output_dir_input, same_dir_checkbox],
                outputs=[agy_cmd_box, copy_cmd_btn],
                api_name=False
            )

        def on_refresh(inp, out, same_dir, thread_id):
            target_out = inp if same_dir else (out or os.path.join(os.path.abspath(inp), "self"))
            _allow_workspace_paths(inp, target_out)
            snap = pm.get_current_state(thread_id)
            cur_stage = snap.get("stage", "INIT")
            has_intr = snap.get("has_interrupt", False)
            stages_h = render_stages_html(cur_stage)
            gates_h = render_gate_reports_html(pm.get_gate_reports(target_out, thread_id=thread_id))
            arts = pm.get_artifacts(target_out)
            arts_h = render_artifacts_html(arts)
            hist_str = "\n".join(snap.get("history", []))
            s_pdf = os.path.join(target_out, "student.pdf") if arts["student_pdf"]["exists"] else None
            t_pdf = os.path.join(target_out, "teacher.pdf") if arts["teacher_pdf"]["exists"] else None
            status_text = f"状态: {cur_stage}"
            if has_intr:
                status_text = format_interrupt_status(
                    snap.get("interrupt_info"),
                    (snap.get("values") or {}).get("dispatch_results"),
                    default="⏸️ 在 Phase 0 断点挂起，等待确认")
            return (
                status_text,
                stages_h,
                gr.update(visible=has_intr),
                gates_h,
                arts_h,
                s_pdf,
                t_pdf,
                hist_str
            )

        refresh_btn.click(
            fn=on_refresh,
            inputs=[input_dir_input, output_dir_input, same_dir_checkbox, thread_id_input],
            outputs=[
                status_box,
                stages_html,
                phase0_box,
                gate_reports_html,
                artifacts_html,
                student_download,
                teacher_download,
                history_output
            ],
            api_name=False
        )

        def on_run_pipeline(inp, out, same_dir, probs, thread_id):
            """Stream pipeline run generator on target output directory."""
            target_out = inp if same_dir else (out or os.path.join(os.path.abspath(inp), "self"))
            _allow_workspace_paths(inp, target_out)

            # 启动前核对界面选题与冻结 brief：不一致必须明确拒绝，
            # 不能等流水线跑到一半才发现勾选被 brief 静默忽略
            sel = pm.check_selection(target_out, probs)
            if not sel["ok"]:
                yield (
                    "❌ 启动被拒绝：界面选题与冻结 brief 不一致",
                    render_stages_html("INIT"),
                    gr.update(visible=False),
                    render_gate_reports_html(pm.get_gate_reports(target_out, thread_id=thread_id)),
                    render_artifacts_html(pm.get_artifacts(target_out)),
                    None,
                    None,
                    f"{sel['mismatch']}\n冻结 brief 入选题: {sel['brief_selected_problem_ids']}\n",
                )
                return

            status_text = "🚀 流水线启动中..."
            yield (
                status_text,
                render_stages_html("INIT"),
                gr.update(visible=False),
                render_gate_reports_html(pm.get_gate_reports(target_out, thread_id=thread_id)),
                render_artifacts_html(pm.get_artifacts(target_out)),
                None,
                None,
                f"正在准备工作区: 输入={inp} -> 输出={target_out}...\n"
            )

            history_log = []
            cur_stage = "INIT"
            gates_h = render_gate_reports_html(pm.get_gate_reports(target_out, thread_id=thread_id))
            arts_h = render_artifacts_html(pm.get_artifacts(target_out))
            s_pdf = None
            t_pdf = None

            for event in pm.run_pipeline_stream(
                input_dir=inp,
                output_dir=target_out,
                selected_problems=probs,
                user_spec=None,
                thread_id=thread_id
            ):
                ev_type = event.get("event")
                msg = event.get("message", "")
                cur_stage = event.get("stage", cur_stage)
                node_name = event.get("node", "")

                for h in event.get("history", []):
                    if h not in history_log:
                        history_log.append(h)

                hist_str = "\n".join(history_log)
                stages_h = render_stages_html(cur_stage, active_node=node_name)
                gates_h = render_gate_reports_html(pm.get_gate_reports(target_out, thread_id=thread_id))
                arts = pm.get_artifacts(target_out)
                arts_h = render_artifacts_html(arts)
                s_pdf = os.path.join(target_out, "student.pdf") if arts["student_pdf"]["exists"] else None
                t_pdf = os.path.join(target_out, "teacher.pdf") if arts["teacher_pdf"]["exists"] else None

                if ev_type == "interrupt":
                    _snap = pm.get_current_state(thread_id)
                    status_text = format_interrupt_status(
                        event.get("interrupt_data"),
                        (_snap.get("values") or {}).get("dispatch_results"),
                        default="⏸️ 在 Phase 0 断点挂起，等待教师确认")
                    yield (
                        status_text,
                        stages_h,
                        gr.update(visible=True),
                        gates_h,
                        arts_h,
                        s_pdf,
                        t_pdf,
                        hist_str
                    )
                    return

                elif ev_type == "error":
                    status_text = f"❌ 异常: {msg}"
                    yield (
                        status_text,
                        stages_h,
                        gr.update(visible=False),
                        gates_h,
                        arts_h,
                        s_pdf,
                        t_pdf,
                        hist_str
                    )
                    return

                else:
                    status_text = f"正在推进: 阶段={cur_stage} | 节点={node_name}"
                    yield (
                        status_text,
                        stages_h,
                        gr.update(visible=False),
                        gates_h,
                        arts_h,
                        s_pdf,
                        t_pdf,
                        hist_str
                    )

            # Final finish
            status_text = f"✅ 流水线推进结束: {cur_stage}"
            stages_h = render_stages_html(cur_stage)
            yield (
                status_text,
                stages_h,
                gr.update(visible=(cur_stage == "PHASE0_BREAKPOINT")),
                gates_h,
                arts_h,
                s_pdf,
                t_pdf,
                "\n".join(history_log)
            )

        run_btn.click(
            fn=on_run_pipeline,
            inputs=[input_dir_input, output_dir_input, same_dir_checkbox, problems_checkbox, thread_id_input],
            outputs=[
                status_box,
                stages_html,
                phase0_box,
                gate_reports_html,
                artifacts_html,
                student_download,
                teacher_download,
                history_output
            ],
            api_name=False
        )

        def on_confirm_resume(inp, out, same_dir, title, mode, deliver_teacher, thread_id, auth_token):
            """Stream resume generator."""
            target_out = inp if same_dir else (out or os.path.join(os.path.abspath(inp), "self"))
            _allow_workspace_paths(inp, target_out)

            # G4.1 零信任红线：后端绝不自行读取 phase0_token.txt，只透传真人在
            # 表单中手工输入的授权码；授权码为空时表单直接拒绝提交。
            token = (auth_token or "").strip()
            if not token:
                yield (
                    "❌ 恢复被拒绝：必须填写人工授权码（请真人查阅 .langgraph/phase0_token.txt 后手工输入，自动化代理严禁代填）",
                    render_stages_html("PHASE0_BREAKPOINT"),
                    gr.update(visible=True),
                    render_gate_reports_html(pm.get_gate_reports(target_out, thread_id=thread_id)),
                    render_artifacts_html(pm.get_artifacts(target_out)),
                    None,
                    None,
                    "缺少人工授权码，表单已拒绝提交。\n"
                )
                return

            status_text = "⏯️ 教师配置已确认，正在恢复流水线..."
            resp = {
                "main_title": title,
                "lesson_mode": mode,
                "deliver_teacher": bool(deliver_teacher),
                "confirmed": True,
                "source": "web_ui",
                "confirmed_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
                "auth_token": token,
            }

            yield (
                status_text,
                render_stages_html("PHASE0_BREAKPOINT"),
                gr.update(visible=False),
                render_gate_reports_html(pm.get_gate_reports(target_out, thread_id=thread_id)),
                render_artifacts_html(pm.get_artifacts(target_out)),
                None,
                None,
                f"正在恢复执行... (title={title}, mode={mode})\n"
            )

            history_log = []
            cur_stage = "PHASE0_BREAKPOINT"
            gates_h = render_gate_reports_html(pm.get_gate_reports(target_out, thread_id=thread_id))
            arts_h = render_artifacts_html(pm.get_artifacts(target_out))
            s_pdf = None
            t_pdf = None

            for event in pm.resume_pipeline_stream(resp, thread_id=thread_id):
                ev_type = event.get("event")
                msg = event.get("message", "")
                cur_stage = event.get("stage", cur_stage)
                node_name = event.get("node", "")

                for h in event.get("history", []):
                    if h not in history_log:
                        history_log.append(h)

                hist_str = "\n".join(history_log)
                stages_h = render_stages_html(cur_stage, active_node=node_name)
                gates_h = render_gate_reports_html(pm.get_gate_reports(target_out, thread_id=thread_id))
                arts = pm.get_artifacts(target_out)
                arts_h = render_artifacts_html(arts)
                s_pdf = os.path.join(target_out, "student.pdf") if arts["student_pdf"]["exists"] else None
                t_pdf = os.path.join(target_out, "teacher.pdf") if arts["teacher_pdf"]["exists"] else None

                if ev_type == "interrupt":
                    _snap = pm.get_current_state(thread_id)
                    status_text = format_interrupt_status(
                        event.get("interrupt_data"),
                        (_snap.get("values") or {}).get("dispatch_results"),
                        default="⏸️ 在新断点挂起，等待确认")
                    yield (
                        status_text,
                        stages_h,
                        gr.update(visible=True),
                        gates_h,
                        arts_h,
                        s_pdf,
                        t_pdf,
                        hist_str
                    )
                    return

                elif ev_type == "error":
                    status_text = f"❌ 异常: {msg}"
                    yield (
                        status_text,
                        stages_h,
                        gr.update(visible=False),
                        gates_h,
                        arts_h,
                        s_pdf,
                        t_pdf,
                        hist_str
                    )
                    return

                else:
                    status_text = f"正在推进: 阶段={cur_stage} | 节点={node_name}"
                    yield (
                        status_text,
                        stages_h,
                        gr.update(visible=False),
                        gates_h,
                        arts_h,
                        s_pdf,
                        t_pdf,
                        hist_str
                    )

            # Final finish
            status_text = f"✅ 流水线完成推进: 阶段={cur_stage}"
            stages_h = render_stages_html(cur_stage)
            yield (
                status_text,
                stages_h,
                gr.update(visible=False),
                gates_h,
                arts_h,
                s_pdf,
                t_pdf,
                "\n".join(history_log)
            )

        confirm_resume_btn.click(
            fn=on_confirm_resume,
            inputs=[input_dir_input, output_dir_input, same_dir_checkbox, main_title_input, lesson_mode_radio, deliver_teacher_cb, thread_id_input, auth_token_input],
            outputs=[
                status_box,
                stages_html,
                phase0_box,
                gate_reports_html,
                artifacts_html,
                student_download,
                teacher_download,
                history_output
            ],
            api_name=False
        )

        # Single step handlers targeting chosen output directory
        def make_step_handler(key):
            def _handler(inp, out, same_dir):
                target_out = inp if same_dir else (out or os.path.join(os.path.abspath(inp), "self"))
                pm.prepare_workspace(inp, target_out)
                res = pm.run_single_node_direct(target_out, key)
                return res
            return _handler

        step_inputs = [input_dir_input, output_dir_input, same_dir_checkbox]
        step_r1_btn.click(fn=make_step_handler("r1"), inputs=step_inputs, outputs=[single_step_output], api_name=False)
        step_r2_btn.click(fn=make_step_handler("r2"), inputs=step_inputs, outputs=[single_step_output], api_name=False)
        step_r4_btn.click(fn=make_step_handler("r4"), inputs=step_inputs, outputs=[single_step_output], api_name=False)
        step_t0_btn.click(fn=make_step_handler("t0"), inputs=step_inputs, outputs=[single_step_output], api_name=False)
        step_t1_btn.click(fn=make_step_handler("t1"), inputs=step_inputs, outputs=[single_step_output], api_name=False)
        step_t2_btn.click(fn=make_step_handler("t2_check"), inputs=step_inputs, outputs=[single_step_output], api_name=False)
        step_t3_btn.click(fn=make_step_handler("t3"), inputs=step_inputs, outputs=[single_step_output], api_name=False)
        step_t4_btn.click(fn=make_step_handler("t4"), inputs=step_inputs, outputs=[single_step_output], api_name=False)

    return demo


def launch(server_name="127.0.0.1", server_port=7860, inbrowser=True, default_input_dir=None, default_output_dir=None):
    """Launch the Gradio UI application."""
    demo = create_ui(default_input_dir=default_input_dir, default_output_dir=default_output_dir)
    # Allow serving deliverable PDFs from the (possibly decoupled) workspace dirs;
    # runtime-picked directories are registered dynamically via _allow_workspace_paths.
    allowed = [os.getcwd(), pm.default_input_dir, pm.default_output_dir]
    for p in (default_input_dir, default_output_dir):
        if p:
            allowed.append(os.path.abspath(p))
    allowed = list(dict.fromkeys(allowed))
    print(f"[*] 正在启动 LangGraph 竞赛教研流水线 UI 工作台: http://{server_name}:{server_port}")
    demo.launch(server_name=server_name, server_port=server_port, inbrowser=inbrowser, show_api=False, allowed_paths=allowed)


if __name__ == "__main__":
    launch()
