#!/usr/bin/env python3
# -*- coding: utf-8 -*-
TOOL_VERSION = "1.0"
r"""
init_project.py — 一键初始化教研/教学项目工作区（§5.3 P2 升级）

从 brief 和 materials（若存在）自动生成配置、入口文件、视图清单及布局计划，
消除手工复制模板和编辑 config.yaml 的步骤。

功能：
  1. 创建 self/ 工作区目录结构（research/ teaching/ sources/ build/ release/）；
  2. 从 brief.json 生成 config.yaml 初稿（lesson_mode、
     selected_problem_ids 等关键字段）；
  3. 从 config.yaml 生成 main.tex 骨架与默认入口（student.tex、teacher.tex）；
  4. 创建 layout-plan.json（纸张尺寸、栏数、最小留白、按题分配）供 check_layout.py 使用；
  5. 写入 manifest.json（模板版本、脚本版本、Python 版本、XeLaTeX 版本、字体版本、
     输入摘要），保证另一台机器能复现同一结果；
  6. 如果 teaching/materials.json 已存在，补全视图入口（hint-pXX.tex 等），
     不覆盖已存在的正文文件。

用法：
  python init_project.py <项目根>
  python init_project.py <项目根> --brief <brief.json 路径>
  python init_project.py <项目根> --dry-run   # 只打印将要执行的操作，不实际写文件

退出码：0 = 成功，1 = 失败，2 = 输入错误。
"""
import argparse
import datetime
import json
import os
import platform
import re
import shutil
import subprocess
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_TEMPLATES_DIR = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "templates"))
# contract.py 与本文件同目录（scripts/），import 实际由脚本目录
# sys.path[0] 解析；_SHARED_DIR 指向的 ../../shared 是历史仓库布局残留
# （本仓库无该目录，插入为空操作），仅保留行为不变，未删除。
_SHARED_DIR = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "..", "shared"))
sys.path.insert(0, _SHARED_DIR)
try:
    import contract
except ImportError:
    contract = None

BRIEF_REL = "research/brief.json"
MATERIALS_REL = "teaching/materials.json"
CONFIG_REL = "config.yaml"
MAIN_TEX_REL = "main.tex"
LAYOUT_PLAN_REL = "teaching/layout-plan.json"
MANIFEST_REL = "teaching/checks/init-manifest.json"

# 标准工作区目录列表
WORK_DIRS = [
    "research",
    "research/checks",
    "research/reviews",
    "teaching",
    "teaching/architect",
    "teaching/checks",
    "teaching/content",
    "teaching/reviews",
    "teaching/build",
    "teaching/review-pack",
    "sources",
    "sources/solutions",
    "issues",
    "release",
]


def tool_version(cmd, *args):
    """获取工具版本字符串；失败时返回 '未知'。"""
    try:
        r = subprocess.run([cmd] + list(args), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=10)
        out = (r.stdout or r.stderr or "").strip().splitlines()
        return out[0] if out else "（已安装，版本解析失败）"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return "（未安装）"


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(path, doc, dry_run=False):
    if dry_run:
        print("  [dry-run] 将写入 %s" % path)
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    print("  [init] 已写入 %s" % path)


def write_text(path, content, dry_run=False):
    if dry_run:
        print("  [dry-run] 将写入 %s" % path)
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    print("  [init] 已写入 %s" % path)


def create_dirs(root, dry_run=False):
    for d in WORK_DIRS:
        path = os.path.join(root, d.replace("/", os.sep))
        if not os.path.isdir(path):
            if dry_run:
                print("  [dry-run] 将创建目录 %s" % path)
            else:
                os.makedirs(path, exist_ok=True)
                print("  [init] 已创建目录 %s" % d)
        else:
            print("  [init] 目录已存在，跳过：%s" % d)


def gen_config_yaml(root, brief, dry_run=False):
    """从 brief 生成 config.yaml 初稿。"""
    cfg_path = os.path.join(root, CONFIG_REL)
    if os.path.exists(cfg_path):
        print("  [init] config.yaml 已存在，跳过生成（保留现有配置）")
        return

    sel_ids = brief.get("selection", {}).get("selected_problem_ids", [])
    lesson_mode = brief.get("course_constraints", {}).get(
        "lesson_mode", "guided")
    # 构建视图列表
    views = []
    views.append("- id: student_initial\n"
                 "  entry: student\n"
                 "  audience: student\n"
                 "  channel: in_main\n"
                 "  teacher_flags: false")
    views.append("- id: teacher\n"
                 "  entry: teacher\n"
                 "  audience: teacher\n"
                 "  channel: in_main\n"
                 "  teacher_flags: true")
    for pid in sel_ids:
        views.append(
            "- id: hint-%s\n"
            "  entry: hint-%s\n"
            "  audience: student\n"
            "  channel: separate_hint\n"
            "  teacher_flags: false" % (pid, pid))

    content = """\
# config.yaml — 由 init_project.py 自动生成（§5.3）
# 请按实际教研交接包内容修改；字段说明见 competition-courseware/references/workflow.md

schema_version: "2.4.0"
brief_id: "{brief_id}"
brief_revision: {revision}
lesson_mode: {lesson_mode}
competition_mode: true

selected_problem_ids: [{sel_ids}]

views:
{views}

# 字体锁定（§5.2）：填写本项目使用的字体名称与版本
font_main: "FandolSong"        # 正文字体（修改此处）
font_math: "XITS Math"         # 数学字体（修改此处）
font_lock_note: "初始化时未锁定，请补充字体版本"

# R3 障碍研究/补充材料研究（可选；默认关闭）：交付链路不消费这些产物，
# 开启前须为其打通 materials.json 消费链（详见 templates/config.yaml 注释）
enable_obstacle_study: false
""".format(
        brief_id=brief.get("brief_id", "TODO"),
        revision=brief.get("revision", 1),
        lesson_mode=lesson_mode,
        sel_ids=", ".join(sel_ids),
        views="\n".join(views),
    )
    write_text(cfg_path, content, dry_run)


def gen_default_entry_files(root, dry_run=False):
    """确保 header.tex, footer.tex, student.tex, teacher.tex 存在（若不存在则从模板复制或生成标准骨架）。"""
    files_to_check = [
        ("header.tex", os.path.join(_TEMPLATES_DIR, "header.tex")),
        ("footer.tex", os.path.join(_TEMPLATES_DIR, "footer.tex")),
        ("student.tex", os.path.join(_TEMPLATES_DIR, "student.tex")),
        ("teacher.tex", os.path.join(_TEMPLATES_DIR, "teacher.tex")),
    ]
    for filename, tmpl_path in files_to_check:
        target_path = os.path.join(root, filename)
        if not os.path.exists(target_path):
            if os.path.isfile(tmpl_path):
                if dry_run:
                    print("  [dry-run] 将从模板复制 %s" % filename)
                else:
                    shutil.copy2(tmpl_path, target_path)
                    print("  [init] 已从模板复制 %s" % filename)
            else:
                if filename == "student.tex":
                    content = "\\def\\pdCurrentView{student_initial}\n\\input{main.tex}\n"
                elif filename == "teacher.tex":
                    content = "\\def\\TeacherVersion{}\n\\def\\pdCurrentView{teacher}\n\\input{main.tex}\n"
                else:
                    content = "% 占位文件: " + filename + "\n"
                write_text(target_path, content, dry_run)
        else:
            print("  [init] %s 已存在，跳过" % filename)


def gen_main_tex(root, brief, dry_run=False):
    """生成 main.tex 骨架（仅当不存在时）。"""
    main_path = os.path.join(root, MAIN_TEX_REL)
    if os.path.exists(main_path):
        print("  [init] main.tex 已存在，跳过生成")
        return
    sel_ids = brief.get("selection", {}).get("selected_problem_ids", [])
    tmpl_main = os.path.join(_TEMPLATES_DIR, "main.tex")
    if os.path.isfile(tmpl_main) and not sel_ids:
        if dry_run:
            print("  [dry-run] 将从模板复制 main.tex")
        else:
            shutil.copy2(tmpl_main, main_path)
            print("  [init] 已从模板复制 main.tex")
        return

    # 排版规范：每题一栏均匀排布，各题切片之间插入 \pdColumnBreak
    sec_inputs = "\n\\pdColumnBreak\n".join(
        r"\input{sec%d}" % (i + 1) for i in range(len(sel_ids))) if sel_ids else r"\input{sec1}"
    content = r"""\input{header}
\begin{document}
\begin{center}
  {\Huge \textbf{\ifdefined\pdViewTitle\pdViewTitle\else\pdMainTitle\fi}}
\end{center}
\raggedcolumns
\pdBeginColumns
%(sec_inputs)s
\input{footer}
\end{document}
""" % {"sec_inputs": sec_inputs}
    write_text(main_path, content, dry_run)


def gen_sections_tex(root, brief, dry_run=False):
    """生成各 sec*.tex 骨架（仅当文件不存在时生成初始占位，绝不覆盖已有内容）。"""
    sel_ids = brief.get("selection", {}).get("selected_problem_ids", [])
    if not sel_ids:
        sec1_path = os.path.join(root, "sec1.tex")
        if not os.path.exists(sec1_path):
            write_text(sec1_path,
                       "%% sec1 — 题目占位（由 init_project.py 初始化）\n"
                       "%% 内容请根据 teaching/materials.json 的块清单填写\n", dry_run)
        return

    for i, pid in enumerate(sel_ids, 1):
        sec_path = os.path.join(root, "sec%d.tex" % i)
        if not os.path.exists(sec_path):
            write_text(sec_path,
                       "%% sec%d — 题目 %s（由 init_project.py 初始化占位）\n"
                       "%%%% qid: %s\n"
                       "%% 内容请根据 teaching/materials.json 的块清单填写\n"
                       % (i, pid, pid), dry_run)
        else:
            print("  [init] sec%d.tex 已存在，跳过" % i)


def gen_layout_plan(root, brief, dry_run=False):
    """生成 teaching/layout-plan.json（版面目标清单，§5.1）。"""
    layout_path = os.path.join(root, LAYOUT_PLAN_REL.replace("/", os.sep))
    if os.path.exists(layout_path):
        print("  [init] layout-plan.json 已存在，跳过生成")
        return
    sel_ids = brief.get("selection", {}).get("selected_problem_ids", [])
    # 默认两页四栏（大 8 开竖版）
    plan = {
        "schema_version": "2.4.0",
        "paper": "B5",
        "columns": 4,
        "pages": 2,
        "min_answer_space_cm": 10.5,
        "max_answer_space_cm": 13.0,
        "column_assignments": [
            {"column": i + 1, "problem_id": pid}
            for i, pid in enumerate(sel_ids[:4])
        ],
        "allow_page_break_within_problem": False,
        "note": "由 init_project.py 生成；check_layout.py 将读取此文件验证 PDF 版面（§5.1）",
    }
    write_json(layout_path, plan, dry_run)


def gen_entry_files(root, materials, dry_run=False):
    """根据 materials.json 补全视图入口（hint-pXX.tex 等；§5.3）。"""
    if not materials:
        return
    views_seen = set()
    for b in materials.get("blocks", []):
        for v in b.get("views", []):
            views_seen.add(v)
    for v in sorted(views_seen):
        if v.startswith("hint-") or v.startswith("review-"):
            entry = v + ".tex"
            path = os.path.join(root, entry)
            if not os.path.exists(path):
                # 从模板生成入口
                tmpl = os.path.join(_TEMPLATES_DIR, "hint.tex")
                if os.path.isfile(tmpl):
                    if dry_run:
                        print("  [dry-run] 将从模板生成 %s" % entry)
                    else:
                        shutil.copy2(tmpl, path)
                        print("  [init] 已从模板生成 %s" % entry)
                else:
                    content = (r"\input{config}" + "\n"
                               r"\input{header}" + "\n"
                               r"\begin{document}" + "\n"
                               r"\input{config-class}" + "\n"
                               "%% 视图 %s 内容由 build.py 按 materials.json 装配\n"
                               r"\end{document}" + "\n") % v
                    write_text(path, content, dry_run)
            else:
                print("  [init] %s 已存在，跳过" % entry)


def gen_manifest(root, brief, dry_run=False):
    """写入初始化 manifest，记录版本与输入摘要（§5.3 可复现性）。"""
    manifest_path = os.path.join(root, MANIFEST_REL.replace("/", os.sep))
    brief_path = os.path.join(root, BRIEF_REL.replace("/", os.sep))
    brief_digest = (contract.sha256_file(brief_path)
                    if contract and os.path.isfile(brief_path) else None)
    manifest = {
        "schema_version": "2.4.0",
        "tool": "init_project.py",
        "tool_version": TOOL_VERSION,
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "python_version": platform.python_version(),
        "xelatex_version": tool_version("xelatex", "--version"),
        "platform": platform.platform(),
        "brief_id": brief.get("brief_id"),
        "brief_revision": brief.get("revision"),
        "brief_digest": brief_digest,
        "note": "§5.3：此 manifest 记录初始化时的环境版本，用于在另一台机器复现相同项目结构。",
    }
    write_json(manifest_path, manifest, dry_run)


def main():
    ap = argparse.ArgumentParser(
        description="一键初始化教研/教学项目工作区（§5.3 P2 升级）")
    ap.add_argument("project_root", help="项目根目录（self/ 目录的父级或本身）")
    ap.add_argument("--brief", metavar="PATH",
                    help="brief.json 路径（默认 <project_root>/research/brief.json）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只打印将要执行的操作，不实际写文件")
    args = ap.parse_args()

    root = os.path.abspath(args.project_root)
    if not os.path.isdir(root):
        print("[init_project] 项目根不存在: %s" % root)
        return 2

    brief_path = args.brief or os.path.join(
        root, BRIEF_REL.replace("/", os.sep))
    brief = {}
    if os.path.isfile(brief_path):
        try:
            brief = load_json(brief_path)
            print("[init_project] 读取 brief: %s" % brief_path)
        except (OSError, json.JSONDecodeError) as exc:
            print("[init_project] brief.json 解析失败: %s（将使用默认值）" % exc)
    else:
        print("[init_project] brief.json 不存在（%s），将生成占位配置" % brief_path)

    materials = None
    mat_path = os.path.join(root, MATERIALS_REL.replace("/", os.sep))
    if os.path.isfile(mat_path):
        try:
            materials = load_json(mat_path)
            print("[init_project] 读取 materials: %s" % mat_path)
        except (OSError, json.JSONDecodeError):
            pass

    print("[init_project] 开始初始化 (%s)..." % ("dry-run" if args.dry_run else "写入"))

    create_dirs(root, args.dry_run)
    gen_config_yaml(root, brief, args.dry_run)
    gen_default_entry_files(root, args.dry_run)
    gen_main_tex(root, brief, args.dry_run)
    gen_sections_tex(root, brief, args.dry_run)
    gen_layout_plan(root, brief, args.dry_run)
    if materials:
        gen_entry_files(root, materials, args.dry_run)
    gen_manifest(root, brief, args.dry_run)

    print("[init_project] 初始化完成。")
    print("[init_project] 后续步骤：")
    print("  1. 检查 config.yaml，补全 font_main/font_math 字体信息")
    print("  2. 运行 check_teaching.py 验证 materials.json 与 brief 绑定")
    print("  3. 运行 build.py 编译（build.py 默认在工作目录串行构建）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
