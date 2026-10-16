#!/usr/bin/env python3
# -*- coding: utf-8 -*-
TOOL_VERSION = "3.0"
r"""
build.py — XeLaTeX 多视图编译 + 日志验收（v2 单源多视图版）

编译只是手段，验收才是目的：返回码为 0 不代表可交付。

视图顺序与串行构建：
  * config.yaml 含 views 时（视图模式），构建目标 = views 声明的入口
    （按声明顺序；separate_* 视图无入口文件——无内容块时 gen_config 不生成
    空入口——自动跳过）；否则为旧版默认 [student, teacher]（可用
    --targets 覆盖）。
  * **同一工作目录中构建必须串行**：各视图共享 main.tex / config.tex /
    sec*.tex 与辅助文件（.aux/.out），并行构建会互相覆盖。本脚本按顺序
    逐视图构建；如需并行多个构建，必须隔离工作目录（复制项目副本），
    不得在同一目录内并行。

对每个目标执行：

  1. 编译：xelatex -no-shell-escape -halt-on-error -interaction=nonstopmode
     （G1.2：显式禁用 shell-escape；日志若仍出现 \write18 启用迹象判失败），每遍均检查返回码
     并设 --timeout 超时，成功后自动第二遍（稳定 \pageref{LastPage} 等交叉
     引用）；超时属执行故障可按 --max-retry 重试，确定性 TeX 错误不重试；
  2. 产物校验：PDF 必须生成，且 PDF / LOG 的修改时间不早于本次编译启动时间
     （防止旧产物造成“假成功”）；
  3. 日志告警扫描（按严重度分级，超过可配置阈值即判失败）：
     - undefined reference / citation      → 严重，任何一处即失败；
     - Package problem-driven Warning      → 严重（\fillin 缺参数等内容缺陷）；
     - mdframed "bad break"                → 严重，任何一处即失败
       （可用 --allow-mdframed-breaks 降级为警告）；
     - Overfull \hbox                      → 超出 --max-overfull-pt（默认 10pt）
       的溢出判失败，其余按警告报告；
     - Underfull \hbox/\vbox               → 计数报告；超过 --max-underfull
       判失败。学生侧视图默认不限制（答题留白产生 underfull 属正常），
       教师视图默认 --max-underfull-teacher=3；
     - Font Warning                        → 警告；--strict 下判失败；
     - multicol 收到 1 栏并自动改双栏       → 严重（版式被宏包静默改变）；
  4. 逐视图一致性核对（P0）：解析日志中的
     [problem-driven] SUMMARY solution=N, teacherNote=N, fillin=N, view=V。
     - 旧项目（无 views）：学生/教师两版 solution、teacherNote、fillin 计数
       必须完全一致（旧双版条件宏语义）；教师版 solution 必须 > 0；
       学生版日志必须包含“已剥离”标记行；
     - 视图模式（teaching/materials.json 存在）：各视图的期望计数由
       materials 推导（该视图全部块的 content_ref 正文中 \begin{solution}/
       \begin{teacherNote}/\fillin 出现次数之和），SUMMARY 必须与期望值
       一致；非教师视图日志必须包含“已剥离”标记行。
     计数一致只是必要条件：逐块一致性与视图装配由 check_numbering.py 负责。
     页数与文本量仅作诊断（见 compare_versions.py），不作为正确性判据。

  补充约定：每一遍编译均检查返回码并设 --timeout 超时；config.yaml 比
  config.tex 新时先自动重新生成配置与视图入口；--json 输出统一结构化
  检查结果。

用法:
  python build.py [工作目录] [选项]
退出码: 0=通过, 1=编译或验收失败, 2=缺少依赖或输入。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_config import load_config_full, validate_views  # noqa: E402
from render_student_task import _validate_math_segment  # noqa: E402  # G1.4 复用 G1.1 白名单

RE_UNDEF_REF = re.compile(
    r"LaTeX Warning: (?:Reference|Citation) `[^']*' .*undefined|"
    r"There were undefined (?:references|citations)")
RE_PD_WARNING = re.compile(r"Package problem-driven Warning")
RE_MDFRAMED = re.compile(r"Package mdframed Warning: You got a bad break")
RE_OVERFULL = re.compile(r"Overfull \\hbox \(([\d.]+)pt too wide\)")
RE_UNDERFULL = re.compile(r"Underfull \\[hv]box")
RE_FONT_WARN = re.compile(r"(?:LaTeX )?Font Warning|Missing character:")
RE_MISSING_CHAR = re.compile(r"Missing character: There is no (.) in font")
RE_MULTICOL_1COL = re.compile(
    r"Package multicol[cs]? Warning: Using `1' columns")
RE_SUMMARY = re.compile(
    r"\[problem-driven\] SUMMARY solution=(\d+),\s*"
    r"teacherNote=(\d+),\s*fillin=(\d+)")
RE_VIEW = re.compile(r"\[problem-driven\] VIEW ([^\s]+)")
RE_STRIPPED = re.compile(r"\[problem-driven\] 学生版已剥离")
# G1.2：日志中出现 write18/shell-escape 启用迹象即判失败
RE_WRITE18_ENABLED = re.compile(
    r"\\write18 enabled|shell escape (?:is )?enabled", re.IGNORECASE)
MATERIALS_REL = "teaching/materials.json"

# ---- G1.4：student_solution 源（task-*-solution.tex）安全扫描 ----
SOLUTION_TEX_RE = re.compile(r"^task-[A-Za-z0-9_-]+-solution\.tex$")
# 解答源中禁止的危险控制序列（\begin{solution} 等属合法内容，不在此列；
# 用 (?![A-Za-z]) 而非 \b，确保 \write18 命中而 \includegraphics 不误伤）
RE_SOLUTION_DANGEROUS_CS = re.compile(
    r"\\(input|include|write|openout|closeout|immediate|csname|endcsname|"
    r"def|edef|gdef|xdef|let|futurelet|catcode|read|closein|"
    r"newread|newwrite|newcommand|renewcommand|providecommand|"
    r"usepackage|RequirePackage|directlua|ExplSyntaxOn|ExplSyntaxOff)"
    r"(?![A-Za-z])")
# 解答源可见性：不得引用教师侧教研路径或教师专属环境
RE_SOLUTION_VISIBILITY = re.compile(
    r"research/problems/|teacherNote|教师专用", re.IGNORECASE)


def write_json(path, report):
    """写结构化检查结果（批次交付证据统一格式）。"""
    if not path:
        return
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("[build] 结构化报告已写入 %s" % path)


def scan_log(log_text, is_teacher, args):
    """扫描单个日志，返回 (errors, warnings) 两个消息列表。"""
    errors, warnings = [], []

    n_undef = len(RE_UNDEF_REF.findall(log_text))
    if n_undef:
        errors.append("未定义引用/文献 %d 处（交叉引用未闭合或编译遍数不足）" % n_undef)

    n_pd = len(RE_PD_WARNING.findall(log_text))
    if n_pd:
        errors.append("Package problem-driven 警告 %d 处"
                      "（\\fillin 缺少宽度或参考答案，详见日志）" % n_pd)

    n_md = len(RE_MDFRAMED.findall(log_text))
    if n_md:
        msg = "mdframed bad break %d 处（knowledgebox 跨页断裂，需拆框或允许分页）" % n_md
        if args.allow_mdframed_breaks:
            warnings.append(msg)
        else:
            errors.append(msg)

    overs = [float(x) for x in RE_OVERFULL.findall(log_text)]
    serious = [x for x in overs if x > args.max_overfull_pt]
    if serious:
        errors.append("严重 Overfull \\hbox %d 处（最大 %.1fpt > 阈值 %.1fpt）"
                      % (len(serious), max(serious), args.max_overfull_pt))
    minor = len(overs) - len(serious)
    if minor:
        warnings.append("轻微 Overfull \\hbox %d 处（均 ≤ %.1fpt）"
                        % (minor, args.max_overfull_pt))

    n_under = len(RE_UNDERFULL.findall(log_text))
    limit = (args.max_underfull_teacher if is_teacher
             else args.max_underfull_student)
    if n_under:
        if limit is not None and n_under > limit:
            errors.append("Underfull \\hbox/\\vbox %d 处，超过阈值 %d"
                          % (n_under, limit))
        else:
            note = "（学生侧留白属正常，仅诊断）" if not is_teacher else ""
            warnings.append("Underfull \\hbox/\\vbox %d 处%s" % (n_under, note))

    n_font = len(RE_FONT_WARN.findall(log_text))
    if n_font:
        missing_chars = RE_MISSING_CHAR.findall(log_text)
        char_note = ("，缺字字符：%s" % "".join(sorted(set(missing_chars)))
                     if missing_chars else "")
        msg = ("字体警告/缺字 %d 处（字体回退可能导致字形替换%s；"
               "印刷交付前须替换字体或改写含缺字的段落）" % (n_font, char_note))
        # --strict：印刷级交付目标，字体缺字升级为 blocker
        (errors if args.strict else warnings).append(msg)

    n_mc = len(RE_MULTICOL_1COL.findall(log_text))
    if n_mc:
        errors.append("multicol 收到 1 栏并自动改为双栏 %d 处"
                      "（版式被宏包静默改变；栏数为 1 时应改用 \\pdBeginColumns）"
                      % n_mc)

    # G1.2：即便加了 -no-shell-escape，日志若仍出现 \write18 启用迹象
    # （或 runsystem 真正执行而非 disabled）一律判失败
    n_w18 = len(RE_WRITE18_ENABLED.findall(log_text))
    run_lines = [ln.strip() for ln in log_text.splitlines()
                 if "runsystem(" in ln and "disabled" not in ln.lower()]
    if n_w18 or run_lines:
        detail = ("；".join(run_lines[:3])) if run_lines else ""
        errors.append("检测到 \\write18/shell-escape 启用迹象 %d 处%s"
                      "（编译命令必须带 -no-shell-escape，且日志不得出现 "
                      "write18 启用）%s"
                      % (n_w18 + len(run_lines),
                         "" if not detail else "：", detail))

    return errors, warnings


def parse_summary(log_text):
    m = RE_SUMMARY.search(log_text)
    if not m:
        return None
    mv = RE_VIEW.search(log_text)
    return {"solution": int(m.group(1)),
            "teacherNote": int(m.group(2)),
            "fillin": int(m.group(3)),
            "view": mv.group(1) if mv else None}


def run_xelatex(cmd, workdir, timeout):
    """执行一遍编译，返回 (结果或None, 是否超时)。"""
    try:
        r = subprocess.run(cmd, cwd=workdir, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, encoding="utf-8",
                           errors="replace", timeout=timeout)
        return r, False
    except subprocess.TimeoutExpired as e:
        print("[build] 编译超过 %d 秒被中止（执行故障，可重试）" % timeout)
        out = e.stdout or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", errors="replace")
        r = subprocess.CompletedProcess(cmd, 124, out)
        return r, True


def collect_issue(report, target, kind, msg):
    report["issues"].append({"target": target, "severity": kind,
                             "message": msg})


def check_env():
    """环境预检：列出缺失项与版本，避免生成完全部正文才发现无法编译。"""
    ok = True
    for tool, ver_args in (("xelatex", ["--version"]),
                           ("pdftotext", ["-v"]),
                           ("kpsewhich", ["--version"])):
        if not shutil.which(tool):
            print("[env] 缺少 %s" % tool)
            ok = False
            continue
        r = subprocess.run([tool] + ver_args, capture_output=True,
                           text=True, encoding="utf-8", errors="replace")
        out = (r.stdout or r.stderr or "").splitlines()
        print("[env] %s: %s" % (tool, out[0].strip() if out else "可用"))
    if shutil.which("kpsewhich"):
        for pkg in ("ctexart.cls", "exam-zh-choices.sty", "mdframed.sty",
                    "multicol.sty"):
            r = subprocess.run(["kpsewhich", pkg], capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
            if r.returncode != 0 or not r.stdout.strip():
                print("[env] 缺少宏包/类文件: %s" % pkg)
                ok = False
            else:
                print("[env] %s: %s" % (pkg, r.stdout.strip()))
    print("[env] 预检%s" % ("通过" if ok else "未通过"))
    return 0 if ok else 2


def load_views(workdir):
    """读取 config.yaml 的 views；返回 (规范化视图列表或 None, 错误消息)。"""
    cfg_path = os.path.join(workdir, "config.yaml")
    if not os.path.isfile(cfg_path):
        return None, None
    try:
        _flat, raw = load_config_full(cfg_path)
    except (OSError, ValueError) as exc:
        return None, "config.yaml 解析失败: %s" % exc
    if raw is None:
        return None, None
    views, errors = validate_views(raw)
    if errors:
        return None, "config.yaml views 校验失败: " + "；".join(errors)
    return views, None


def expected_counts_by_view(workdir):
    """视图模式期望计数：{视图id: {"solution": n, "teacherNote": n, "fillin": n}}。

    由 teaching/materials.json 推导：该视图全部块的 content_ref 正文中
    \\begin{solution} / \\begin{teacherNote} / \\fillin 出现次数之和。
    materials.json 缺失返回 None（调用方走旧双版核对）。
    """
    path = os.path.join(workdir, MATERIALS_REL.replace("/", os.sep))
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            materials = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    counts = {}
    for b in materials.get("blocks", []):
        cref = b.get("content_ref")
        if not cref:
            continue
        p = os.path.join(workdir, cref.replace("/", os.sep))
        if not os.path.isfile(p):
            continue
        with open(p, encoding="utf-8", errors="replace") as f:
            text = f.read()
        n_sol = len(re.findall(r"\\begin\{solution\}", text))
        n_note = len(re.findall(r"\\begin\{teacherNote\}", text))
        n_fill = len(re.findall(r"\\fillin\b", text))
        for v in b.get("views", []):
            c = counts.setdefault(v, {"solution": 0, "teacherNote": 0,
                                      "fillin": 0})
            c["solution"] += n_sol
            c["teacherNote"] += n_note
            c["fillin"] += n_fill
    return counts


def scan_student_solution_sources(workdir):
    """G1.4：student_solution.pdf 源（teaching/content/task-*-solution.tex）
    安全扫描。返回问题消息列表（空即通过）。

    gen_student_solution.py 直写 release/ 的 student_solution.pdf 不经
    check_student_visibility；build 在检测到将生成 student_solution.pdf 时，
    把其源 tex 纳入两层校验（fail-closed，任一命中即判失败）：
      1. 数学段控制序列白名单（复用 G1.1 的 _validate_math_segment）；
      2. 危险控制序列黑名单（\\input/\\def/\\write18/\\csname 等）与
         可见性扫描（不得引用 research/problems/ 教师侧材料、teacherNote）。
    manifest 归属由 G7 在 deliver.py 侧处理，此处不动。
    """
    problems = []
    content_dir = os.path.join(workdir, "teaching", "content")
    if not os.path.isdir(content_dir):
        problems.append("检测到将生成 student_solution.pdf 但缺少 "
                        "teaching/content 目录，无法校验解答源（fail-closed）")
        return problems
    files = sorted(f for f in os.listdir(content_dir)
                   if SOLUTION_TEX_RE.match(f))
    for fname in files:
        path = os.path.join(content_dir, fname)
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError as exc:
            problems.append("%s 读取失败：%s（fail-closed）" % (fname, exc))
            continue
        for m in RE_SOLUTION_DANGEROUS_CS.finditer(text):
            problems.append("%s 含危险控制序列 \\%s（TeX 注入风险）"
                            % (fname, m.group(1)))
        m = RE_SOLUTION_VISIBILITY.search(text)
        if m:
            problems.append("%s 含教师侧材料引用/环境标记 %r（可见性违规）"
                            % (fname, m.group(0)))
        for issue in _validate_math_segment(text):
            problems.append("%s 数学段校验失败：%s"
                            % (fname, issue.get("message", "")))
    return problems


def compile_target(workdir, target, is_teacher, view_id, expected, args,
                   report=None):
    """编译单个目标并验收日志。返回 (ok, summary_or_None)。"""
    tex = os.path.join(workdir, target + ".tex")
    pdf = os.path.join(workdir, target + ".pdf")
    log = os.path.join(workdir, target + ".log")
    if not os.path.exists(tex):
        print("[%s] 缺少 %s.tex" % (target, target))
        if report is not None:
            collect_issue(report, target, "error", "缺少 %s.tex" % target)
        return False, None

    cmd = ["xelatex", "-no-shell-escape", "-halt-on-error",
           "-interaction=nonstopmode", target + ".tex"]
    good = False
    passes = 0
    t0 = time.time()
    for attempt in range(args.max_retry + 1):
        r, timed_out = run_xelatex(cmd, workdir, args.timeout)
        passes += 1
        if r.returncode != 0:
            if timed_out:
                continue  # 超时属执行故障，可重试
            print("[%s] 第 %d 次编译第一遍失败（-halt-on-error 中止于首个错误；"
                  "确定性 TeX 错误不重试，请修正正文）：" % (target, attempt + 1))
            for line in r.stdout.splitlines():
                if line.startswith("!"):
                    print("    " + line)
                    break
            break
        # 第二遍稳定交叉引用 / LastPage；返回码同样必须检查，
        # 失败时不得继承第一遍的成功状态。
        t0 = time.time()
        r2, timed_out2 = run_xelatex(cmd, workdir, args.timeout)
        passes += 1
        if r2.returncode == 0:
            good = True
            break
        if timed_out2:
            continue
        print("[%s] 第 %d 次编译第二遍失败：" % (target, attempt + 1))
        for line in r2.stdout.splitlines():
            if line.startswith("!"):
                print("    " + line)
                break
        break

    print("[%s] 编译%s（%d 遍）" % (target, "成功" if good else "失败", passes))
    if not good:
        if report is not None:
            collect_issue(report, target, "error", "编译失败")
        return False, None

    # ---- 产物时效校验：防止旧 PDF / 旧日志造成假成功 ----
    stale = []
    for path, what in ((pdf, "PDF"), (log, "日志")):
        if not os.path.exists(path):
            stale.append("%s 未生成（%s）" % (what, os.path.basename(path)))
        elif os.path.getmtime(path) < t0 - 1:
            stale.append("%s 不是本次编译产物（%s 修改时间早于编译启动）"
                         % (what, os.path.basename(path)))
    for msg in stale:
        print("[%s] [错误] %s" % (target, msg))
        if report is not None:
            collect_issue(report, target, "error", msg)
    if stale:
        return False, None

    with open(log, encoding="utf-8", errors="replace") as f:
        log_text = f.read()

    errors, warnings = scan_log(log_text, is_teacher, args)
    for msg in warnings:
        print("[%s] [警告] %s" % (target, msg))
        if report is not None:
            collect_issue(report, target, "warning", msg)
    for msg in errors:
        print("[%s] [错误] %s" % (target, msg))
        if report is not None:
            collect_issue(report, target, "error", msg)

    summary = parse_summary(log_text)
    if summary is None:
        print("[%s] [错误] 日志缺少 [problem-driven] SUMMARY 行"
              "（header.tex 被改动或编译提前中止？）" % target)
        errors.append("缺少 SUMMARY")
    else:
        print("[%s] SUMMARY solution=%d, teacherNote=%d, fillin=%d, view=%s"
              % (target, summary["solution"], summary["teacherNote"],
                 summary["fillin"], summary["view"] or "?"))
        if view_id and summary["view"] and summary["view"] != view_id:
            print("[%s] [错误] 日志视图标记 %s 与入口声明视图 %s 不一致"
                  % (target, summary["view"], view_id))
            errors.append("视图标记不一致")
        if expected is not None:
            for key in ("solution", "teacherNote", "fillin"):
                if summary[key] != expected[key]:
                    print("[%s] [错误] 视图 %s 的 %s 计数 %d 与 materials "
                          "期望 %d 不一致（块装配或剥离异常）"
                          % (target, view_id or target, key, summary[key],
                             expected[key]))
                    errors.append("视图 %s 计数不一致" % key)
        elif is_teacher and summary["solution"] == 0:
            print("[%s] [错误] 教师版 solution=0，参考解答缺失" % target)
            errors.append("教师版无 solution")
        if not is_teacher and not RE_STRIPPED.search(log_text):
            print("[%s] [错误] 学生侧视图日志缺少“已剥离”标记，"
                  "solution/teacherNote 可能未被剥离" % target)
            errors.append("学生侧剥离标记缺失")

    return not errors, summary


def main():
    ap = argparse.ArgumentParser(description="XeLaTeX 多视图编译 + 日志验收")
    ap.add_argument("workdir", nargs="?", default=".")
    ap.add_argument("--targets", nargs="+", default=None,
                    help="覆盖构建目标（旧项目默认 student teacher；views "
                         "模式默认按 views 声明顺序）")
    ap.add_argument("--max-retry", type=int, default=1)
    ap.add_argument("--max-overfull-pt", type=float, default=10.0,
                    help="单处 Overfull \\hbox 允许的最大溢出点数（默认 10pt）")
    ap.add_argument("--max-underfull-teacher", type=int, default=3,
                    help="教师视图允许的 Underfull 数量（默认 3）")
    ap.add_argument("--max-underfull-student", type=int, default=None,
                    help="学生侧视图允许的 Underfull 数量（默认不限制：留白属正常）")
    ap.add_argument("--allow-mdframed-breaks", action="store_true",
                    help="将 mdframed bad break 从失败降级为警告")
    ap.add_argument("--strict", action="store_true",
                    help="字体警告也判为失败")
    ap.add_argument("--timeout", type=int, default=300,
                    help="单遍编译超时秒数（默认 300；超时属执行故障可重试）")
    ap.add_argument("--json", metavar="PATH",
                    help="将结构化检查结果写入 PATH（统一交付证据格式）")
    ap.add_argument("--check-env", action="store_true",
                    help="只做环境预检（xelatex/pdftotext/宏包），不编译")
    args = ap.parse_args()
    if args.check_env:
        return check_env()
    workdir = os.path.abspath(args.workdir)
    if not os.path.isdir(workdir):
        print("[build] 工作目录不存在: %s" % workdir)
        return 2

    # ---- 视图与目标确定（同一工作目录串行构建） ----
    views, verr = load_views(workdir)
    if verr:
        print("[build] " + verr)
        return 2
    view_by_entry = {}
    if views is not None:
        for v in views:
            view_by_entry[v["entry"]] = v

    # ---- 配置时效：config.yaml 比生成文件新、或视图模式下入口缺失时，
    # 先重新生成 config.tex/config-class.tex 与视图入口 ----
    cfg_yaml = os.path.join(workdir, "config.yaml")
    cfg_tex = os.path.join(workdir, "config.tex")
    need_gen = os.path.exists(cfg_yaml) and (
        not os.path.exists(cfg_tex)
        or os.path.getmtime(cfg_yaml) > os.path.getmtime(cfg_tex))
    if not need_gen and views is not None:
        for v in views:
            if v["channel"] == "in_main" and not os.path.exists(
                    os.path.join(workdir, v["entry"] + ".tex")):
                need_gen = True
    if need_gen:
        gen = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "gen_config.py")
        print("[build] config.yaml 已更新，重新生成 config.tex/config-class.tex"
              "与视图入口")
        gr = subprocess.run([sys.executable, gen, "--project", workdir],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
        print(gr.stdout.strip())
        if gr.returncode != 0:
            print(gr.stderr.strip())
            return 2

    targets = args.targets or ([v["entry"] for v in views]
                               if views is not None
                               else ["student", "teacher"])
    # separate_* 视图无入口文件（无内容块）时自动跳过，不算错误
    skipped = []
    if views is not None:
        kept = []
        for t in targets:
            if os.path.exists(os.path.join(workdir, t + ".tex")):
                kept.append(t)
            elif view_by_entry.get(t, {}).get("channel") in (
                    "separate_hint", "separate_review"):
                skipped.append(t)
            else:
                kept.append(t)  # 让 compile_target 报“缺少 .tex”
        targets = kept
    for t in skipped:
        print("[build] 视图入口 %s.tex 不存在且无内容块（未生成空入口），跳过"
              % t)

    expected_by_view = expected_counts_by_view(workdir)

    report = {"tool": "build.py", "tool_version": TOOL_VERSION,
              "scope": {"workdir": workdir, "targets": targets,
                        "views_mode": views is not None},
              "status": "pass", "issues": [], "evidence": {"skipped": skipped}}

    if not shutil.which("xelatex"):
        print("error: 未找到 xelatex")
        report["status"] = "error"
        report["issues"].append("缺少 xelatex 依赖")
        write_json(args.json, report)
        return 2

    ok = True

    # ---- G1.4：检测到将生成 student_solution.pdf（student_solution.tex
    # 入口存在或显式列入 --targets）时，先把 task-*-solution.tex 源纳入
    # 数学段白名单校验与可见性扫描；不通过则整体判失败并跳过其编译 ----
    sol_entry = os.path.join(workdir, "student_solution.tex")
    if "student_solution" in targets or os.path.isfile(sol_entry):
        sol_msgs = scan_student_solution_sources(workdir)
        for msg in sol_msgs:
            print("[build] [错误] student_solution 源校验：%s" % msg)
            collect_issue(report, "student_solution", "error", msg)
        if sol_msgs:
            ok = False
            if "student_solution" in targets:
                targets = [t for t in targets if t != "student_solution"]
                report["scope"]["targets"] = targets
                print("[build] student_solution 源未通过安全校验，"
                      "跳过其编译（fail-closed）")

    summaries = {}
    for t in targets:
        v = view_by_entry.get(t)
        is_teacher = (v["teacher_flags"] == "true") if v else (t == "teacher")
        view_id = v["id"] if v else None
        expected = (expected_by_view.get(view_id)
                    if (expected_by_view is not None and view_id) else None)
        t_ok, t_summary = compile_target(workdir, t, is_teacher, view_id,
                                         expected, args, report)
        ok &= t_ok
        if t_summary:
            summaries[t] = t_summary

    # ---- 旧双版 SUMMARY 一致性核对（视图模式已由逐视图期望核对取代） ----
    if views is None and "student" in summaries and "teacher" in summaries:
        s, t = summaries["student"], summaries["teacher"]
        for key in ("solution", "teacherNote", "fillin"):
            if s[key] != t[key]:
                print("[一致性] [错误] 学生版 %s=%d 与教师版 %s=%d 不一致"
                      "（题链在两版中出现次数不同，双版本条件宏可能被破坏）"
                      % (key, s[key], key, t[key]))
                collect_issue(report, "both", "error",
                              "双版 %s 计数不一致（%d != %d）"
                              % (key, s[key], t[key]))
                ok = False
                break
        else:
            print("[一致性] 双版 SUMMARY 计数一致：solution=%d, "
                  "teacherNote=%d, fillin=%d（计数一致只是必要条件；"
                  "逐题一致性与题号身份由 check_numbering.py 负责）"
                  % (s["solution"], s["teacherNote"], s["fillin"]))

    print("[build] 总体结果：%s（同一工作目录内各视图串行构建；"
          "并行多个构建必须隔离工作目录）" % ("通过" if ok else "未通过"))
    report["status"] = "pass" if ok else "fail"
    report["evidence"]["summaries"] = summaries
    write_json(args.json, report)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
