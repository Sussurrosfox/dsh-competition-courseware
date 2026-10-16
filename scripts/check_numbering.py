#!/usr/bin/env python3
# -*- coding: utf-8 -*-
TOOL_VERSION = "3.0"
r"""
check_numbering.py — 切片装配与题链结构检查（含 v2 materials 块对应检查）

扫描工作目录（以 main.tex 的 \input 顺序为准），检查：

  装配完整性（P0）：
  1. 磁盘上所有 sec*.tex 必须被 main.tex 引入（发现“文件存在但未装配”）；
  2. main.tex 引入的切片文件必须存在（--init 初始化模式下降级为警告，
     允许“骨架先行、暂无正文”的初始化检查）；
  3. 引入顺序必须与切片编号顺序一致（sec1, sec2, ... 单调递增）；
     重复引入同一切片、或不同文件名解析出相同编号，均判失败；
  4. main.tex 引入了切片但全部缺失时，按已发现问题判失败，
     不再以“未发现任何 sec*.tex”放行。

  题链结构（栈式校验，带行号）：
  5. TeX 感知的注释处理：\% 为转义字符，不会被误认为注释起点；
  6. \begin / \end 栈式配对：发现嵌套顺序错误、交叉闭合、未闭合环境；
  7. probchain 内必须至少有一个可见题目（其一级 enumerate 至少一个 \item），
     任何 enumerate/itemize 不得为空，\item 不得出现在列表环境之外，
     probchain 不得嵌套；
  8. [旧写法] series=probchain 必须恰好一次且位于首个含题链的文件，其余
     文件只能用 resume；series/resume 不得与 probchain 环境混用。

  逐题身份与答案关联（旧项目分支的切片级硬约束）：
  9. 切片不得改写全局状态：\setcounter{pdprob}、\def\TeacherVersion、
     \ifdefined\TeacherVersion 出现在 sec*.tex 中即失败
     （题号由装配顺序渲染，视图差异只能由模板宏与 \ViewBlock 产生）；
 10. [旧分支] probchain 每个一级 \item 题块必须恰好含一个 solution 和一个
     teacherNote 环境；[materials 分支] 此项由块装配与 build.py 的逐视图
     计数核对取代，不在此强制；
 11. 每个一级 \item 之前应有 `% qid: <稳定题ID>` 注释：qid 全章唯一
     （重复判失败；旧工程缺失记警告，competition_mode: true 或 materials
     模式下缺失判失败）。

  题目状态 practice_mode（旧字段兼容；旧分支轻量检查）：
 12. \practicemode 取值只能是 guided（缺省视为 guided），
     且必须出现在 probchain 题目块内、每块至多一次；
     注意：lesson_mode 由 brief 契约承载（唯一取值 guided），不映射到
     practice_mode。materials 模式下本项不适用
     （提示约束由 check_teaching.py 的揭示分发检查负责）。

  materials 分支（teaching/materials.json 存在时启用，新增）：
 14. 切片中每个 `% block: <block_id>` 注释（其后只允许空行/注释行）必须
     紧跟 \ViewBlock{<views>}，views 集合与 materials.json 该块的 views
     完全一致；block_id 必须在 materials.json 中存在；
 15. materials.json 中每个块必须在已装配切片中恰好出现一次 % block:
     注释；块的 content_ref 须以 \input 出现在对应 \ViewBlock 体内，
     同一 block 的 content_ref 跨视图一致（跨视图可追溯）；
 15b. 学生视图（config.yaml 中 audience=student 的视图，缺省
     student_initial）的 \ViewBlock 体内除 `% block:` 注释与
     `\input{<content_ref>}` 外不得出现任何非空白内联文字（G6.1
     装配层盲区修复：内联文字直接编进学生 PDF 且绕过内容文件门禁）；
     结构性容器须以 `% block_type: structural` 注释显式声明白名单；
 16. 切片 `% qid:` 与 materials 块的 problem_id 双向可回溯；
 17. 不带 % block: 注释的 \ViewBlock 视为结构性容器（如避免空题链的
     题组容器），只记提示不判失败。

   说明：逐视图 SUMMARY 计数一致性由 build.py 负责；数学正确性归教学验收；
   学生可见内容的泄露由 check_student_visibility.py
   与 check_student_task.py 负责，
   新版契约检查由 check_research.py / check_teaching.py 负责。

退出码: 0=通过, 1=发现问题, 2=工作目录不存在等输入错误。
用法: python check_numbering.py [工作目录] [--init] [--json PATH]
"""
import argparse
import json
import os
import re
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gen_config import load_config, load_config_full  # noqa: E402

SERIES = re.compile(r"\\begin\{enumerate\}\[[^\]]*series=probchain")
RESUME = re.compile(r"\\begin\{enumerate\}\[[^\]]*resume=probchain")
INPUT = re.compile(r"\\input\{([^{}]+)\}")
# 单趟合并扫描：按字符位置依次处理 begin/end、\item、\practicemode、提示标记
TOKEN = re.compile(
    r"\\(begin|end)\{([a-zA-Z*]+)\}"      # 1=kind 2=env
    r"|\\item\b"                           # 题目/列表项
    r"|\\practicemode\{([^{}]*)\}"         # 3=模式取值
    r"|\\hintline\b")                      # 提示行
PMODE_OK = ("guided",)
# 教师专属环境：环境栈内存在这些环境时，内容学生版不可见
TEACHER_ENVS = ("solution", "teacherNote")
# 切片禁止改写的全局状态（题号计数器与版本开关）
FORBIDDEN = re.compile(
    r"\\setcounter\{pdprob\}|\\def\\TeacherVersion\b"
    r"|\\ifdefined\\TeacherVersion\b")
QID = re.compile(r"^\s*%+\s*qid:\s*([^\s%]+)")
# 可使用 \item 的列表环境（probchain 内部自带一级 enumerate，
# 其直接 \item 就是题目；choices/tasks 为选择题宏包环境）
LIST_ENVS = ("enumerate", "itemize", "description", "choices", "tasks",
             "probchain")
# materials 模式：块注释与视图装配宏
BLOCK_RE = re.compile(r"^\s*%+\s*block:\s*([^\s%]+)")
VIEWBLOCK_RE = re.compile(r"\\ViewBlock\{([^{}]*)\}")
# 结构性容器声明注释（%% 和单 % 均允许；§2.3 升级）
STRUCTURAL_RE = re.compile(r"^\s*%+\s*block_type:\s*structural\b")
MATERIALS_REL = "teaching/materials.json"


def strip_comment(line):
    r"""去掉行内注释；\% 是转义字符，不作为注释起点。"""
    i = 0
    while True:
        j = line.find("%", i)
        if j < 0:
            return line
        k = j - 1
        while k >= 0 and line[k] == "\\":
            k -= 1
        if (j - 1 - k) % 2 == 1:  # 奇数个反斜杠 → \% 转义
            i = j + 1
        else:
            return line[:j]


def sec_inputs(workdir):
    r"""按 main.tex 的 \input 顺序返回切片文件名；无 main.tex 返回 None。"""
    main = os.path.join(workdir, "main.tex")
    if not os.path.exists(main):
        return None
    files = []
    with open(main, encoding="utf-8", errors="replace") as f:
        for line in f:
            for m in INPUT.finditer(strip_comment(line)):
                name = m.group(1)
                if re.match(r"sec[^/\\]*(\.tex)?$", name):
                    files.append(name if name.endswith(".tex")
                                 else name + ".tex")
    return files


def sec_key(name):
    m = re.search(r"(\d+)", name)
    return (int(m.group(1)) if m else 0, name)


def competition_mode_enabled(workdir):
    """读取 config.yaml 的 competition_mode；缺失或解析失败时按旧工程处理。"""
    path = os.path.join(workdir, "config.yaml")
    if not os.path.exists(path):
        return False
    try:
        cfg = load_config(path)
    except (OSError, ValueError):
        return False
    return str(cfg.get("competition_mode", "")).strip().lower() == "true"


def load_materials(workdir):
    """teaching/materials.json 存在时返回 (doc, None)，否则 (None, None)；
    存在但非法 JSON 时 (None, 错误消息)。"""
    path = os.path.join(workdir, MATERIALS_REL.replace("/", os.sep))
    if not os.path.isfile(path):
        return None, None
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, "%s 不是合法 JSON: %s" % (MATERIALS_REL, exc)


def check_file(workdir, name, problems, warnings, qid_seen,
               competition_enabled=False, materials_mode=False):
    """栈式校验单个切片文件；返回 (含probchain, series数, resume数, enum数)。"""
    path = os.path.join(workdir, name)
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()

    text = "\n".join(strip_comment(l) for l in lines)
    n_series = len(SERIES.findall(text))
    n_resume = len(RESUME.findall(text))
    has_probchain = "\\begin{probchain}" in text

    for m in FORBIDDEN.finditer(text):
        problems.append(
            "%s: 切片不得改写全局状态（%s）；题号由装配顺序渲染，"
            "视图差异只能由模板宏与 \\ViewBlock 产生" % (name, m.group(0)))

    # 栈式 begin/end 校验：帧 = [环境名, 起始行, 一级 \item 数]
    stack = []          # 环境帧
    prob_visible = {}   # probchain 帧起始行 -> 一级题目数
    # 当前题目块（probchain 一级 \item）状态，用于逐题与 practice_mode 检查
    cur_item = None
    pending_qid = None  # 最近看到的 % qid: 注释（归属于下一个一级题目）
    pending_qid_loc = None  # 该注释自身的位置（materials 模式预登记用）

    def finish_item(end_line):
        """结算当前题目块：逐题答案关联（旧分支）+ qid。"""
        nonlocal cur_item
        if not cur_item:
            return
        if not materials_mode:
            for env in TEACHER_ENVS:
                n = cur_item["envs"].get(env, 0)
                if n != 1:
                    problems.append(
                        "%s:%d: 题目块应恰好含一个 %s 环境（实际 %d 个）"
                        % (name, cur_item["line"], env, n))
        qid = cur_item["qid"]
        qid_loc = cur_item.get("qid_loc")
        item_loc = "%s:%d" % (name, cur_item["line"])
        if qid is None:
            msg = ("%s:%d: 题目块缺少 `%% qid: <稳定题ID>` 注释"
                   "（跨题关联与验收记录需要稳定身份）"
                   % (name, cur_item["line"]))
            if competition_enabled or materials_mode:
                problems.append(msg)
            else:
                warnings.append(msg)
        elif qid in qid_seen and qid_seen[qid] != qid_loc:
            problems.append(
                "%s:%d: qid “%s” 与 %s 重复，稳定题身份必须全章唯一"
                % (name, cur_item["line"], qid, qid_seen[qid]))
        else:
            # 题目块的身份以 \item 位置登记；materials 模式下由 `% qid:` 注释
            # 预登记的同一位置在此升级，不构成“同一注释被声明两次”
            qid_seen[qid] = item_loc
        cur_item = None

    for lineno, raw in enumerate(lines, 1):
        mq = QID.match(raw)
        if mq:
            pending_qid = mq.group(1)
            pending_qid_loc = "%s:%d" % (name, lineno)
            if materials_mode:
                # materials 模式：`% qid:` 注释本身就是一次身份声明（题块可以不
                # 用 probchain \item 承载，仅由 `% block:` + \ViewBlock 装配）。
                # 同一条注释随后被 \item 消费时不算重复；只有两条不同注释声明
                # 同一 qid 才判重复。
                if pending_qid in qid_seen \
                        and qid_seen[pending_qid] != pending_qid_loc:
                    problems.append(
                        "%s:%d: qid “%s” 与 %s 重复，稳定题身份必须全章唯一"
                        % (name, lineno, pending_qid, qid_seen[pending_qid]))
                else:
                    qid_seen[pending_qid] = pending_qid_loc
        line = strip_comment(raw)
        # 全部 token 按字符位置单趟处理（同一行内 \item 与 \practicemode
        # 的先后关系由位置决定，不受换行影响）
        for m in TOKEN.finditer(line):
            tok = m.group(0)
            if tok.startswith("\\item"):
                # \item 必须位于列表环境内；计入最内层列表的一级计数
                top_list = None
                for frame in reversed(stack):
                    if frame[0] in LIST_ENVS:
                        top_list = frame
                        break
                if top_list is None:
                    problems.append("%s:%d: \\item 出现在列表环境之外"
                                    % (name, lineno))
                else:
                    top_list[2] += 1
                    if top_list[0] == "probchain":
                        finish_item(lineno)
                        cur_item = {"line": lineno, "mode": "guided",
                                    "mode_set": False,
                                    "envs": {}, "qid": pending_qid,
                                    "qid_loc": pending_qid_loc}
                        pending_qid = None
                        pending_qid_loc = None
                continue
            if tok.startswith("\\hintline"):
                continue
            if tok.startswith("\\practicemode"):
                value = m.group(3).strip()
                if value not in PMODE_OK:
                    problems.append(
                        "%s:%d: \\practicemode 取值非法（“%s”），只能是 guided"
                        % (name, lineno, value))
                if cur_item is None:
                    problems.append(
                        "%s:%d: \\practicemode 出现在 probchain 题目块之外"
                        % (name, lineno))
                elif cur_item["mode_set"]:
                    problems.append(
                        "%s:%d: 同一题目块内 \\practicemode 重复出现（首次在第 %d 行）"
                        % (name, lineno, cur_item["line"]))
                else:
                    cur_item["mode"] = value
                    cur_item["mode_set"] = True
                continue
            kind, env = m.group(1), m.group(2)
            if kind == "begin":
                if env == "probchain":
                    for frame in stack:
                        if frame[0] == "probchain":
                            problems.append(
                                "%s:%d: probchain 嵌套于第 %d 行的 probchain 内"
                                % (name, lineno, frame[1]))
                if cur_item is not None and env in TEACHER_ENVS:
                    cur_item["envs"][env] = cur_item["envs"].get(env, 0) + 1
                stack.append([env, lineno, 0])
            else:  # end
                if not stack:
                    problems.append("%s:%d: \\end{%s} 没有配对的 \\begin"
                                    % (name, lineno, env))
                    continue
                if stack[-1][0] != env:
                    open_desc = ", ".join("%s(第%d行)" % (fr[0], fr[1])
                                          for fr in stack)
                    problems.append(
                        "%s:%d: \\end{%s} 与当前未闭合环境交叉闭合（栈: %s）"
                        % (name, lineno, env, open_desc))
                    # 尝试恢复：弹出到匹配位置
                    for k in range(len(stack) - 1, -1, -1):
                        if stack[k][0] == env:
                            del stack[k:]
                            break
                    continue
                frame = stack.pop()
                if frame[0] in LIST_ENVS and frame[0] != "probchain" \
                        and frame[2] == 0:
                    problems.append("%s:%d: %s 环境内没有任何 \\item"
                                    % (name, frame[1], frame[0]))
                if frame[0] == "probchain":
                    finish_item(lineno)
                    prob_visible[frame[1]] = frame[2]
    finish_item(lines and len(lines) or 0)
    for frame in stack:
        problems.append("%s: 环境 %s（第 %d 行 \\begin）到文件末尾未闭合"
                        % (name, frame[0], frame[1]))
    for start_line, n_items in prob_visible.items():
        if n_items == 0:
            problems.append("%s:%d: probchain 内没有可见题目"
                            "（一级 enumerate 缺少 \\item）"
                            % (name, start_line))
    n_enum = len(re.findall(r"\\begin\{enumerate\}", text))
    return has_probchain, n_series, n_resume, n_enum


# ---------------------------------------------------------------------------
# materials 分支：% block: 注释 ↔ materials.json 字面核对
# ---------------------------------------------------------------------------

def parse_viewblock_body(text, start):
    """从 text[start]（应为 '\\ViewBlock' 起始）解析两个花括号参数。

    返回 (views列表, body, 结束位置) 或 None（解析失败）。
    """
    m = re.compile(r"\\ViewBlock\{").match(text, start)
    if not m:
        return None

    def read_group(pos):
        # pos 指向 '{'
        depth = 0
        i = pos
        while i < len(text):
            c = text[i]
            if c == "\\":
                i += 2
                continue
            if c == "{":
                depth += 1
                if depth == 1:
                    body_start = i + 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[body_start:i], i + 1
            i += 1
        return None

    g1 = read_group(m.end() - 1)
    if not g1:
        return None
    views_raw, pos = g1
    while pos < len(text) and text[pos] in " \t\n%":
        if text[pos] == "%":  # 跳过参数间的注释行
            nl = text.find("\n", pos)
            pos = len(text) if nl < 0 else nl + 1
        else:
            pos += 1
    if pos >= len(text) or text[pos] != "{":
        return None
    g2 = read_group(pos)
    if not g2:
        return None
    body, end = g2
    views = sorted(v.strip() for v in views_raw.split(",") if v.strip())
    return views, body, end


def collect_blocks(workdir, name, problems, warnings):
    r"""收集切片中的 % block: 注释与紧随的 \ViewBlock。

    返回 [{block_id, views, body, line}]；结构性（无注释）\ViewBlock 记提示。
    """
    path = os.path.join(workdir, name)
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    text = "\n".join(strip_comment(l) for l in lines)
    blocks = []
    # 每个 % block: 注释 → 其后（只越空行/注释行）必须是 \ViewBlock
    for lineno, raw in enumerate(lines, 1):
        mb = BLOCK_RE.match(raw)
        if not mb:
            continue
        bid = mb.group(1).strip()
        # 定位注释在 strip 后文本中的位置：逐行偏移
        offset = sum(len(strip_comment(l)) + 1 for l in lines[:lineno - 1])
        # 从注释行之后找下一个非空、非纯注释内容
        pos = offset + len(strip_comment(raw)) + 1
        seg_lines = []
        ln = lineno  # 下一行（0 基：ln 为当前行号，1 基）
        ok = False
        while ln < len(lines):
            s = strip_comment(lines[ln]).strip()
            if not s:
                ln += 1
                continue
            if s.startswith("\\ViewBlock"):
                ok = True
            else:
                seg_lines.append(ln + 1)
            break
        if not ok:
            problems.append(
                "%s:%d: `%% block: %s` 注释之后（只允许空行/注释行）必须紧跟 "
                "\\ViewBlock{<views>}{...} 装配" % (name, lineno, bid))
            continue
        vpos = text.find("\\ViewBlock", pos)
        parsed = parse_viewblock_body(text, vpos)
        if parsed is None:
            problems.append("%s:%d: 块 %s 的 \\ViewBlock 参数解析失败"
                            "（花括号未配对？）" % (name, lineno, bid))
            continue
        views, body, _end = parsed
        blocks.append({"block_id": bid, "views": views, "body": body,
                       "line": lineno})
    # 结构性容器检查：\\ViewBlock 总数应等于块注释数 + 显式声明的容器数
    n_vb = len(VIEWBLOCK_RE.findall(text))
    # 显式 structural 声明：使用模块级 STRUCTURAL_RE（单/双百分号均允许）
    n_structural = sum(1 for l in lines if STRUCTURAL_RE.match(l))
    n_unlabeled = n_vb - len(blocks) - n_structural
    if n_unlabeled > 0:
        problems.append(
            "%s: 存在 %d 个 \\ViewBlock 既无 `%% block:` 内容块注释、也无 "
            "`%% block_type: structural` 结构性容器声明（升级 §2.3：此项\n"
            "已从警告升级为 error；内容块请补 `%% block:` 与 materials 登记，"
            "纯结构容器请在 \\ViewBlock 前加 `%% block_type: structural` 注释）"
            % (name, n_unlabeled))
    elif n_structural > 0:
        warnings.append(
            "%s: %d 个 \\ViewBlock 已声明为结构性容器（block_type: structural），"
            "确认其内容为装配用途、非可追溯内容块" % (name, n_structural))
    return blocks


def student_view_ids(workdir):
    """config.yaml 中 audience=student 的视图 id 集合；配置缺失/不可解析
    时回退默认 student_initial（fail-closed 口径，与
    check_student_visibility._student_view_ids 同逻辑）。"""
    path = os.path.join(workdir, "config.yaml")
    ids = set()
    if os.path.isfile(path):
        try:
            _flat, views = load_config_full(path)
        except Exception:
            views = None
        for v in (views or []):
            if isinstance(v, dict) and v.get("audience") == "student" \
                    and v.get("id"):
                ids.add(v["id"])
    return ids or {"student_initial"}


def _structural_viewblock_flags(lines):
    r"""逐行扫描，按出现顺序返回每个 \ViewBlock 是否由紧邻上方（只越空行/
    纯注释行）的 `% block_type: structural` 注释显式声明为结构性容器
    （15b 白名单）。

    返回 [bool, ...]，与文本中 \ViewBlock 的出现顺序一一对应。
    """
    flags = []
    pending = False
    for raw in lines:
        if STRUCTURAL_RE.match(raw):
            pending = True
            continue
        s = strip_comment(raw)
        if not s.strip():
            continue  # 空行/纯注释行不消费声明
        if "\\ViewBlock" in s:
            flags.append(pending)
        pending = False  # 首条实质内容不是 structural 声明则声明失效
    return flags


def check_student_view_inline(workdir, name, problems, student_views):
    r"""15b（G6.1 装配层盲区修复）：学生视图的 \ViewBlock 体内除
    `% block:` 注释与 `\input{<content_ref>}` 外不得出现任何非空白内联
    文字——内联文字会被直接编进学生 PDF，且绕过内容文件门禁。

    结构性容器（题组外壳等）须以 `% block_type: structural` 注释显式
    声明白名单；未声明的一律按内容块口径检查。
    """
    path = os.path.join(workdir, name)
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    text = "\n".join(strip_comment(ln) for ln in lines)
    flags = _structural_viewblock_flags(lines)
    pos = 0
    idx = 0  # 当前 \ViewBlock 的出现序号（与 flags 对齐）
    while True:
        m = re.search(r"\\ViewBlock\{", text[pos:])
        if not m:
            break
        start = pos + m.start()
        structural = flags[idx] if idx < len(flags) else False
        idx += 1
        parsed = parse_viewblock_body(text, start)
        if parsed is None:
            # 解析失败已由 collect_blocks 报告，此处跳过避免重复
            pos = start + len("\\ViewBlock{")
            continue
        views, body, end = parsed
        pos = end
        if not (set(views) & student_views):
            continue
        if structural:
            continue  # 显式声明的结构性容器白名单
        inline = re.sub(r"\\input\{[^{}]*\}", " ", body)
        if inline.strip():
            snippet = " ".join(inline.split())[:60]
            problems.append(
                "%s: 学生视图 \\ViewBlock 体内出现 \\input 之外的非空白"
                "内联文字 %r（学生视图块体只允许 `%% block:` 注释与 "
                "\\input{<content_ref>}；确为结构性容器须以 "
                "`%% block_type: structural` 注释显式声明白名单）"
                % (name, snippet))


def check_materials_mode(workdir, files, materials, problems, warnings):
    """materials 分支核心检查（第 14—17 项）。"""
    student_views = student_view_ids(workdir)
    for name in files:
        check_student_view_inline(workdir, name, problems, student_views)
    blocks_meta = {b.get("block_id"): b for b in materials.get("blocks", [])}
    seen = {}   # block_id -> 位置
    content_by_block = {}  # block_id -> content_ref（跨视图一致性）
    for name in files:
        for blk in collect_blocks(workdir, name, problems, warnings):
            bid = blk["block_id"]
            loc = "%s:%d" % (name, blk["line"])
            meta = blocks_meta.get(bid)
            if meta is None:
                problems.append("%s: 块 %s 未在 %s 登记" % (loc, bid,
                                                            MATERIALS_REL))
                continue
            if bid in seen:
                problems.append("%s: 块 %s 重复装配（首次在 %s）；同一内容块"
                                "只能装配一次" % (loc, bid, seen[bid]))
            else:
                seen[bid] = loc
            want = sorted(meta.get("views", []))
            if blk["views"] != want:
                problems.append(
                    "%s: 块 %s 的 \\ViewBlock 视图列表 %s 与 %s 登记的 %s "
                    "不一致" % (loc, bid, blk["views"], MATERIALS_REL, want))
            cref = meta.get("content_ref") or ""
            if bid in content_by_block and content_by_block[bid] != cref:
                problems.append("%s: 块 %s 的 content_ref 跨视图不一致（%s "
                                "与 %s）" % (loc, bid, content_by_block[bid],
                                             cref))
            content_by_block[bid] = cref
            # content_ref 须以 \input 出现在块体内（允许省略 .tex 后缀）
            stem = cref[:-4] if cref.endswith(".tex") else cref
            if cref and "\\input{%s}" % cref not in blk["body"] \
                    and "\\input{%s}" % stem not in blk["body"]:
                problems.append(
                    "%s: 块 %s 的 \\ViewBlock 体内未 \\input 其 content_ref "
                    "(%s)；单源装配要求块正文来自登记的内容文件"
                    % (loc, bid, cref))
    for bid, meta in blocks_meta.items():
        if bid not in seen:
            problems.append("%s: 块 %s 未在任何已装配切片中出现 `%% block:` "
                            "注释" % (MATERIALS_REL, bid))
    # qid ↔ problem_id 双向回溯（qid_seen 由调用方传入）
    return seen


def main():
    ap = argparse.ArgumentParser(description="切片装配与题链结构检查")
    ap.add_argument("workdir", nargs="?", default=".")
    ap.add_argument("--init", action="store_true",
                    help="初始化检查：允许引入的切片暂不存在（降级为警告）")
    ap.add_argument("--json", metavar="PATH", help="写入结构化检查结果")
    args = ap.parse_args()
    workdir = os.path.abspath(args.workdir)
    if not os.path.isdir(workdir):
        print("[check_numbering] 工作目录不存在: %s" % workdir)
        return 2
    problems = []
    warnings = []

    materials, merr = load_materials(workdir)
    if merr:
        print("[check_numbering] " + merr)
        return 2
    materials_mode = materials is not None

    inputs = sec_inputs(workdir)
    if inputs is None:
        print("[check_numbering] 未发现 main.tex，退化为文件名排序（"
              "无法检查装配完整性）。")
        inputs = sorted((f for f in os.listdir(workdir)
                         if re.fullmatch(r"sec.*\.tex", f)), key=sec_key)
    else:
        # ---- 装配完整性检查 ----
        on_disk = {f for f in os.listdir(workdir)
                   if re.fullmatch(r"sec.*\.tex", f)}
        not_assembled = sorted(on_disk - set(inputs), key=sec_key)
        for f in not_assembled:
            problems.append("切片 %s 存在但未在 main.tex 中装配（漏 \\input）"
                            % f)
        missing = [f for f in inputs
                   if not os.path.exists(os.path.join(workdir, f))]
        for f in missing:
            msg = "main.tex 引入的切片 %s 不存在" % f
            (warnings if args.init else problems).append(
                msg + ("（--init 模式，仅警告）" if args.init else ""))
        # 重复装配：同一路径出现多次，或不同文件名解析出相同编号
        seen_names = set()
        for f in inputs:
            if f in seen_names:
                problems.append("main.tex 重复装配同一切片 %s" % f)
            seen_names.add(f)
        keys = [sec_key(f)[0] for f in inputs]
        seen_keys = {}
        for f, k in zip(inputs, keys):
            if k in seen_keys:
                problems.append("切片编号 %d 被 %s 与 %s 同时占用"
                                % (k, seen_keys[k], f))
            seen_keys.setdefault(k, f)
        if keys != sorted(keys):
            problems.append("main.tex 的切片引入顺序与编号顺序不一致: %s"
                            % ", ".join(inputs))

    files = [f for f in inputs
             if os.path.exists(os.path.join(workdir, f))]
    if not files:
        # 先处理已发现的问题；只有真正空的工程才允许“跳过”。
        # 但跳过也必须落盘结构化报告——缺报告的门禁不得被解读为通过。
        status = "fail" if problems else "pass"
        if args.json:
            report = {"tool": "check_numbering.py",
                      "tool_version": TOOL_VERSION,
                      "scope": {"workdir": workdir, "files": []},
                      "status": status,
                      "issues": ([{"severity": "error", "message": p}
                                  for p in problems]
                                 + [{"severity": "warning", "message": w}
                                    for w in warnings]),
                      "evidence": {"skipped": True,
                                   "reason": "未发现任何 sec*.tex"}}
            _report_dir = os.path.dirname(os.path.abspath(args.json))
            if _report_dir:
                os.makedirs(_report_dir, exist_ok=True)
            with open(args.json, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print("[check_numbering] 结构化报告已写入 %s" % args.json)
        for w in warnings:
            print("[check_numbering] [警告] " + w)
        if problems:
            print("[check_numbering] 发现 %d 个问题:" % len(problems))
            for p in problems:
                print("  - " + p)
            return 1
        print("[check_numbering] 未发现任何 sec*.tex，跳过（已落盘报告）。")
        return 0

    series_seen = 0
    used_probchain_env = False
    used_legacy = False

    legacy_mode = False  # 已有文件使用 series/resume 旧写法
    qid_seen = {}        # qid -> 首次出现位置（全章唯一）

    competition_enabled = competition_mode_enabled(workdir)

    for idx, name in enumerate(files):
        has_pc, n_series, n_resume, n_enum = check_file(
            workdir, name, problems, warnings, qid_seen,
            competition_enabled=competition_enabled,
            materials_mode=materials_mode)
        if has_pc:
            used_probchain_env = True
        if legacy_mode and idx > 0 and n_enum and not has_pc \
                and n_series == 0 and n_resume == 0:
            problems.append("%s: 旧写法题链缺少 resume=probchain，"
                            "编号将重新从 1 开始" % name)
        if n_series or n_resume:
            used_legacy = True
            legacy_mode = True
            series_seen += n_series
            if n_series and idx != 0:
                problems.append("%s: series=probchain 出现在非首个切片文件中"
                                % name)
            if n_series > 1:
                problems.append("%s: series=probchain 出现 %d 次（应至多 1 次）"
                                % (name, n_series))

    if used_legacy and not used_probchain_env and series_seen == 0:
        problems.append("旧写法题链缺少 series=probchain 起点（首个切片未声明 series）")
    if used_legacy and used_probchain_env:
        problems.append("probchain 环境与 series/resume 旧写法混用，题号将错位，请统一为 probchain")

    # ---- materials 分支：块注释 ↔ materials.json 核对 + qid ↔ problem_id ----
    evidence = {"qid_count": len(qid_seen), "materials_mode": materials_mode}
    if materials_mode:
        check_materials_mode(workdir, files, materials, problems, warnings)
        mat_pids = {b.get("problem_id")
                    for b in materials.get("blocks", [])}
        for qid, loc in qid_seen.items():
            if qid not in mat_pids:
                problems.append("%s: 切片 qid “%s” 未在 %s 的 blocks 中作为 "
                                "problem_id 出现" % (loc, qid, MATERIALS_REL))
        for pid in sorted(p for p in mat_pids if p):
            if pid not in qid_seen:
                problems.append("%s: problem_id “%s” 未在任何已装配切片的 "
                                "`%% qid:` 中出现（题身份必须双向可回溯）"
                                % (MATERIALS_REL, pid))
        evidence["blocks_registered"] = len(materials.get("blocks", []))

    if args.json:
        report = {"tool": "check_numbering.py", "tool_version": TOOL_VERSION,
                  "scope": {"workdir": workdir, "files": files},
                  "status": "fail" if problems else "pass",
                  "issues": ([{"severity": "error", "message": p}
                              for p in problems]
                             + [{"severity": "warning", "message": w}
                                for w in warnings]),
                  "evidence": evidence}
        _report_dir = os.path.dirname(os.path.abspath(args.json))
        if _report_dir:
            os.makedirs(_report_dir, exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print("[check_numbering] 结构化报告已写入 %s" % args.json)

    for w in warnings:
        print("[check_numbering] [警告] " + w)
    if problems:
        print("[check_numbering] 发现 %d 个问题:" % len(problems))
        for p in problems:
            print("  - " + p)
        return 1
    mode_note = "materials 块对应检查" if materials_mode else "旧分支"
    print("[check_numbering] 通过：%d 个切片文件装配完整、题链结构一致"
          "（%s）。" % (len(files), mode_note))
    return 0


if __name__ == "__main__":
    sys.exit(main())
