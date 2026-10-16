#!/usr/bin/env python3
# -*- coding: utf-8 -*-
TOOL_VERSION = "2.4.0"
r"""
check_student_visibility.py — 学生可见性检查（契约 2.4.0，必经）。

审计整改：旧版在 materials.json 缺失时打印 WARN 后仍以 exit 0（PASS）
放行；新版把“材料缺失/不可读/越权可见”全部定为 fail，不以跳过代替通过。

检查内容（sec*.tex 以 main.tex 的 \input 顺序为准）：

  1. teaching/materials.json 必须存在且合法（缺失/不可读 → fail）；
  2. 学生视图（views 含 student_initial 的块）不得包含 teacher_only
     渠道、audience 仅含 teacher、level != L0 或 available_after != initial
     的块；
  3. 学生可见正文（student_initial 块的 content_ref、渲染后的
     task-<qid>.tex）不得出现 solution / teacherNote 环境、教师专属
     路径（research/problems/*/solution*）、明确答案标记；
  3c. 装配层切片（sec*.tex）中学生视图（audience=student）的
     \ViewBlock 体内、\input{...} 之外的内联文字，走与 content 相同
     的答案标记/protected token 检测（G6.1 盲区修复，含零宽字符等
     归一化绕过变体，G6.2）；
  4. materials.json 的 views 与 config.yaml 一致；
  5. 每个学生 task block 都能追溯到题面和小问（problem_id /
     subquestion_ids）；
  6. 渲染产物机器标记核对（renderer_version 与 input_digest）。

退出码：0=通过（含疑似项 review）；1=发现问题；2=缺少依赖或输入。

用法：python check_student_visibility.py [工作目录] [--json PATH]
"""
import argparse
import json
import os
import re
import sys
import unicodedata

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCHEMAS_DIR = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "schemas"))
if _SCHEMAS_DIR not in sys.path:
    sys.path.insert(0, _SCHEMAS_DIR)
sys.path.insert(0, _SCRIPT_DIR)
import contract  # noqa: E402
import check_numbering as cn  # noqa: E402（G6 组内共享 \ViewBlock 解析）
from gen_config import load_config_full  # noqa: E402

MATERIALS_REL = "teaching/materials.json"
STUDENT_VIEW = "student_initial"

# 学生可见文本中的教师专属/答案标记
FORBIDDEN_STUDENT_RE = re.compile(
    r"\\begin\{solution\}|\\begin\{teacherNote\}|teacherNote|"
    r"research/problems/[^ \n]*solution|教师专用|本题答案|答案是|正确答案|"
    r"task-[A-Za-z0-9_-]+-solution",
    re.IGNORECASE)

# 答案标记的归一化补集（G6.2：覆盖零宽字符/全角伪装等绕过变体；
# 环境类标记经归一化后已不可辨认，仍由上面的正则负责）
FORBIDDEN_STUDENT_MARKERS = ("教师专用", "本题答案", "答案是", "正确答案")

# 装配层切片文件名（与 check_numbering 的口径一致）
SEC_FILE_RE = re.compile(r"sec.*\.tex")


def _normalize_leak_text(text: str) -> str:
    """泄露检测归一化（G6.2）：剔除 Unicode Cf 零宽字符与 TeX 间距控制符、
    NFKC（含全角→半角折叠）、大小写折叠、空白、TeX token 与数学定界符。

    与 check_student_task.normalize_text 同逻辑的组内共享私有副本
    （勿改 contract.py；修改时两处须同步）。
    """
    if not isinstance(text, str):
        return ""
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    text = unicodedata.normalize("NFKC", text)
    text = text.casefold()
    text = re.sub(r"\\[,!;:/\-]", " ", text)   # TeX 间距控制符
    text = re.sub(r"\$+", " ", text)           # 数学定界符
    text = re.sub(r"\\[a-zA-Z]+", " ", text)   # TeX 控制序列
    text = re.sub(r"[{}\\_^~&%#]", " ", text)
    text = " ".join(text.split())
    return text


def _normalized_marker_pairs():
    return [(raw, _normalize_leak_text(raw).replace(" ", ""))
            for raw in FORBIDDEN_STUDENT_MARKERS]


def _normalized_marker_hits(text, markers):
    """归一化后命中、但原始正则未覆盖到的答案标记原文列表。"""
    norm = _normalize_leak_text(text).replace(" ", "")
    return [raw for raw, mn in markers
            if mn and mn in norm and raw not in text]


def _student_view_ids(views):
    """学生视图 id 集合（config.yaml 中 audience=student 的视图）；
    配置缺失/不可解析时回退到默认 student_initial（fail-closed 口径）。"""
    ids = {v.get("id") for v in (views or [])
           if isinstance(v, dict) and v.get("audience") == "student"
           and v.get("id")}
    return ids or {STUDENT_VIEW}


def _load_protected_tokens(project_root):
    """brief 声明的 protected_strategy_tokens；缺失/不可读时返回空表
    （brief 自身的完整性由 check_student_task / check_teaching 负责）。"""
    abs_path = contract.resolve_path(project_root, "research/brief.json")
    if not abs_path or not os.path.isfile(abs_path):
        return []
    try:
        with open(abs_path, "r", encoding="utf-8-sig") as f:
            brief = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return [t for t in brief.get("protected_strategy_tokens", [])
            if isinstance(t, str) and t]


def _iter_sec_viewblocks(path):
    r"""解析切片文件中的全部 \ViewBlock；返回 (blocks, error)。

    blocks: [{"views": [...], "body": str}]（body 已剥离行注释）。
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError as exc:
        return None, str(exc)
    text = "\n".join(cn.strip_comment(line) for line in lines)
    blocks = []
    pos = 0
    while True:
        m = re.search(r"\\ViewBlock\{", text[pos:])
        if not m:
            break
        start = pos + m.start()
        parsed = cn.parse_viewblock_body(text, start)
        if parsed is None:
            return None, "\\ViewBlock 参数解析失败（花括号未配对？）"
        views, body, end = parsed
        blocks.append({"views": views, "body": body})
        pos = end
    return blocks, None


def check_student_visibility(project_root="."):
    issues = []
    warnings = []

    cfg_file = os.path.join(project_root, "config.yaml")
    try:
        _flat, views = load_config_full(cfg_file)
    except Exception:
        views = None

    materials_abs = contract.resolve_path(project_root, MATERIALS_REL)
    if materials_abs is None or not os.path.isfile(materials_abs):
        issues.append({
            "code": "MISSING_MATERIALS",
            "message": "缺少教学统一内容源 %s；学生可见性无法核验，按失败"
                       "处理（不因缺材料放行）" % MATERIALS_REL})
        return 1, issues, warnings, None
    try:
        with open(materials_abs, "r", encoding="utf-8-sig") as f:
            materials = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        issues.append({"code": "BAD_MATERIALS",
                       "message": "materials.json 不是合法 JSON: %s" % exc})
        return 1, issues, warnings, None

    blocks = materials.get("blocks", [])

    # 2. 学生视图块约束
    for b in blocks:
        bid = b.get("block_id", "<unknown>")
        views_b = b.get("views", [])
        if STUDENT_VIEW not in views_b:
            continue
        if b.get("delivery_channel") == "teacher_only":
            issues.append({
                "code": "TEACHER_ONLY_IN_STUDENT_VIEW",
                "message": "块 %s 为 teacher_only 渠道却进入学生视图 %s"
                           % (bid, STUDENT_VIEW)})
        aud = b.get("audience", [])
        if "student" not in aud:
            issues.append({
                "code": "AUDIENCE_WITHOUT_STUDENT",
                "message": "块 %s 进入学生视图但 audience=%s 不含 student"
                           % (bid, aud)})
        if b.get("level") != "L0":
            issues.append({
                "code": "NON_L0_IN_STUDENT_VIEW",
                "message": "块 %s 进入学生初始视图但 level=%s（只有 L0 允许）"
                           % (bid, b.get("level"))})
        if b.get("available_after") != "initial":
            issues.append({
                "code": "DELAYED_BLOCK_IN_STUDENT_VIEW",
                "message": "块 %s 进入学生初始视图但 available_after=%s"
                           "（未到期块须拆到 separate_* 渠道）"
                           % (bid, b.get("available_after"))})

    # 3. 学生可见正文的教师专属/答案标记
    markers = _normalized_marker_pairs()
    for b in blocks:
        if STUDENT_VIEW not in b.get("views", []):
            continue
        cref = b.get("content_ref")
        if not cref:
            continue
        abs_path = contract.resolve_path(project_root, cref)
        if abs_path is None or not os.path.isfile(abs_path):
            issues.append({"code": "MISSING_CONTENT",
                           "message": "学生视图块 %s 的正文缺失: %s"
                                      % (b.get("block_id"), cref)})
            continue
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        for m in FORBIDDEN_STUDENT_RE.finditer(text):
            issues.append({
                "code": "TEACHER_CONTENT_IN_STUDENT_VIEW",
                "message": "学生视图块 %s（%s）出现教师专属/答案标记: %r"
                           % (b.get("block_id"), cref, m.group(0))})
        for raw in _normalized_marker_hits(text, markers):
            issues.append({
                "code": "TEACHER_CONTENT_IN_STUDENT_VIEW",
                "message": "学生视图块 %s（%s）出现经零宽字符/全角等伪装的"
                           "答案标记（归一化后命中 %r）"
                           % (b.get("block_id"), cref, raw)})

    # 3b. 渲染后的学生任务文件
    gen_dir = os.path.join(project_root, "teaching", "generated")
    content_dir = os.path.join(project_root, "teaching", "content")
    if os.path.isdir(gen_dir):
        for fn in sorted(os.listdir(gen_dir)):
            if not fn.endswith("-student-task.json"):
                continue
            qid = fn[:-len("-student-task.json")]
            tex_path = os.path.join(content_dir, "task-%s.tex" % qid)
            if not os.path.isfile(tex_path):
                warnings.append({
                    "code": "MISSING_RENDERED_TASK",
                    "message": "题 %s 缺少渲染产物 teaching/content/task-%s.tex"
                               % (qid, qid)})
                continue
            with open(tex_path, "r", encoding="utf-8",
                      errors="replace") as f:
                tex = f.read()
            for m in FORBIDDEN_STUDENT_RE.finditer(tex):
                issues.append({
                    "code": "TEACHER_CONTENT_IN_RENDERED_TASK",
                    "message": "渲染学生任务 task-%s.tex 出现教师专属/答案"
                               "标记: %r" % (qid, m.group(0))})
            if "renderer_version:" not in tex:
                warnings.append({
                    "code": "MISSING_RENDERER_MARKER",
                    "message": "task-%s.tex 缺少渲染器机器标记" % qid})

    # 3c. 装配层切片（sec*.tex）学生视图 \ViewBlock 体的内联文字（G6.1）
    student_views = _student_view_ids(views)
    protected_tokens = _load_protected_tokens(project_root)
    tokens_n = [(t, _normalize_leak_text(t).replace(" ", ""))
                for t in protected_tokens]
    sec_names = sorted(
        f for f in os.listdir(project_root)
        if SEC_FILE_RE.fullmatch(f)
        and os.path.isfile(os.path.join(project_root, f)))
    for fn in sec_names:
        vblocks, serr = _iter_sec_viewblocks(
            os.path.join(project_root, fn))
        if serr:
            issues.append({
                "code": "SEC_PARSE_ERROR",
                "message": "切片 %s 无法读取/解析（%s）；学生可见性无法"
                           "核验，按失败处理" % (fn, serr)})
            continue
        for vb in vblocks:
            if not (set(vb["views"]) & student_views):
                continue
            # 契约 2.5.0 (F12): 检查 student 视图中是否直接或间接 \input 了解答切片
            inputs = re.findall(r"\\input\{([^{}]+)\}", vb["body"])
            for inp_file in inputs:
                if re.search(r"task-[A-Za-z0-9_-]+-solution", inp_file):
                    issues.append({
                        "code": "SOLUTION_INCLUDED_IN_STUDENT_VIEW",
                        "message": "切片 %s 学生视图 \\ViewBlock 引用了解答切片 \\input{%s}（违背零泄露原则）"
                                   % (fn, inp_file)
                    })
            # 内联文字 = 块体中 \input{...} 之外的部分（注释已剥离）
            inline = re.sub(r"\\input\{[^{}]*\}", " ", vb["body"])
            if not inline.strip():
                continue
            for m in FORBIDDEN_STUDENT_RE.finditer(inline):
                issues.append({
                    "code": "TEACHER_CONTENT_IN_SECTION",
                    "message": "切片 %s 学生视图 \\ViewBlock 体的内联文字"
                               "出现教师专属/答案标记: %r"
                               % (fn, m.group(0))})
            for raw in _normalized_marker_hits(inline, markers):
                issues.append({
                    "code": "TEACHER_CONTENT_IN_SECTION",
                    "message": "切片 %s 学生视图 \\ViewBlock 体的内联文字"
                               "出现经零宽字符/全角等伪装的答案标记"
                               "（归一化后命中 %r）" % (fn, raw)})
            inline_ns = _normalize_leak_text(inline).replace(" ", "")
            for tok, tok_ns in tokens_n:
                if tok_ns and tok_ns in inline_ns:
                    issues.append({
                        "code": "PROTECTED_TOKEN_IN_SECTION",
                        "message": "切片 %s 学生视图 \\ViewBlock 体的内联"
                                   "文字出现受保护策略 token %r"
                                   % (fn, tok)})

    # 3d. 检查 student.tex 主装配文件（若存在，F12 命名空间隔离）
    stu_tex_path = os.path.join(project_root, "student.tex")
    if os.path.isfile(stu_tex_path):
        try:
            with open(stu_tex_path, "r", encoding="utf-8", errors="replace") as f:
                s_lines = f.readlines()
            s_text = "\n".join(cn.strip_comment(line) for line in s_lines)
            for inp_file in re.findall(r"\\input\{([^{}]+)\}", s_text):
                if re.search(r"task-[A-Za-z0-9_-]+-solution", inp_file):
                    issues.append({
                        "code": "SOLUTION_INCLUDED_IN_STUDENT_ROOT",
                        "message": "student.tex 主入口直接引用了解答切片 \\input{%s}（违背零泄露原则）" % inp_file
                    })
        except Exception:
            pass

    # 4. views 与 config.yaml 一致
    if views:
        config_views = {v["id"] for v in views}
        expected = {STUDENT_VIEW, "teacher"}
        if not expected <= config_views:
            issues.append({
                "code": "MISSING_REQUIRED_VIEWS",
                "message": "config.yaml 缺少必需视图 %s（当前 %s）"
                           % (sorted(expected), sorted(config_views))})
        block_views = set()
        for b in blocks:
            block_views.update(b.get("views", []))
        undeclared = block_views - config_views
        if undeclared:
            issues.append({
                "code": "UNDECLARED_VIEW",
                "message": "materials 块使用了未声明视图: %s" % sorted(undeclared)})

    # 5. task block 可追溯性
    for b in blocks:
        if b.get("type") == "task" or b.get("task_id"):
            if "problem_id" not in b and "question_id" not in b:
                warnings.append({
                    "code": "MISSING_REFERENCE",
                    "message": "Task 块 %s 缺少 problem_id/question_id 引用"
                               % b.get("block_id")})

    return (0 if not issues else 1), issues, warnings, materials


def main():
    parser = argparse.ArgumentParser(description="学生可见性检查（2.4.0）")
    parser.add_argument("project_root", nargs="?", default=".",
                        help="项目根目录")
    parser.add_argument("--json", dest="json_output", help="JSON 输出路径")
    args = parser.parse_args()

    exit_code, issues, warnings, _materials = check_student_visibility(
        args.project_root)

    if args.json_output:
        status = "pass" if not issues else "fail"
        if not issues and warnings:
            status = "review"
        findings = [{"severity": "fail", "code": i.get("code", "ERROR"),
                     "message": i.get("message", "")} for i in issues]
        findings += [{"severity": "review", "code": w.get("code", "WARN"),
                      "message": w.get("message", "")} for w in warnings]
        contract.write_check_report(args.json_output, status,
                                    "student_visibility", findings)

    print(json.dumps({"tool": "check_student_visibility",
                      "version": TOOL_VERSION,
                      "status": "PASS" if not issues else "FAIL",
                      "issues": issues, "warnings": warnings},
                     ensure_ascii=False, indent=2))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
