#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""render_student_task.py — 题级确定性渲染器（契约 2.4.0）。

审计整改要点：
- 修正 TeX 转义与数学公式处理：`$...$` / `\(...\)` / `\[...\]` 数学片段
  原样透传，不再把 `$` 当普通文本转义；文本段才做 TeX 特殊字符转义；
- G1.1：数学片段不再整体豁免——`_validate_math_segment` 对数学段内部做
  控制序列白名单校验（`\frac \sqrt aligned` 等常用数学宏放行，
  `\input \def \write18 \csname` 等一律 fail-closed 报 issue），
  题干 stem 回写前同样过该校验；段外保留黑名单校验，两层都过才渲染；
- 输出只能落在 broker 指定的 staging 目标
  （staging/<run_id>/rendered/<task_id>.tex），CLI 拒绝任意输出路径；
- 生成固定的机器标记：renderer 版本、input digest、run/task/question 绑定，
  检查器据此重算摘要，不信任 JSON 中自报的摘要；
- 不接受 TeX 宏注入：题干/小问文本中的 `\begin`、`\item`、`\ansspace` 等
  控制序列在数学段之外一律转义。

输入：student-task.json（符合 student-task.schema.json 2.4.0）
输出：TeX 片段（按 question/subquestion 组织，每问独立 \ansspace 留白）

用法（经 broker 调度）：
    python render_student_task.py --project . --run-id run-... \
        --task-id task-P441 --input staging/run-.../tasks/task-P441/student-task.json
"""
import argparse
import json
import os
import re
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCHEMAS_DIR = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "schemas"))
if _SCHEMAS_DIR not in sys.path:
    sys.path.insert(0, _SCHEMAS_DIR)
sys.path.insert(0, _SCRIPT_DIR)
import contract  # noqa: E402

RENDERER_VERSION = "2.4.0"
EXPECTED_SCHEMA_VERSION = "2.4.0"

# 数学片段：$...$ / \(...\) / \[...\]（非贪婪，允许跨行仅限 \[ \]）
_MATH_SEGMENT_RE = re.compile(
    r"(\$\$[^$]*\$\$|\$[^$\n]*\$|\\\([^)]*\\\)|\\\[.*?\\\])", re.DOTALL)

# 文本段中禁止出现的控制序列（数学段除外）——防 TeX 宏注入
_FORBIDDEN_CS_RE = re.compile(
    r"\\(begin|end|item|ansspace|fillin|section|subsection|input|include)\b")

# ---- G1.1：数学段控制序列白名单（fail-closed）----
# 数学段（$...$ / \(...\) / \[...\]）不再整体豁免：内部只允许下列控制序列，
# 其余任何 \ 开头的序列一律拒绝（报 issue，不静默删除）。
_ALLOWED_MATH_CS = frozenset("""
    frac dfrac tfrac cfrac sqrt binom dbinom tbinom
    sum prod int iint iiint iiiint oint bigcup bigcap bigoplus bigotimes
    bigodot bigvee bigwedge
    cdot cdots ldots ddots vdots dots dotsc dotsm times div pm mp ast star
    circ bullet oplus ominus otimes oslash
    leq le geq ge lt gt ll gg neq ne prec succ preceq succeq
    approx approxeq sim simeq cong equiv propto asymp doteq not
    in ni notin subset supset subseteq supseteq sqsubseteq sqsupseteq
    nsubseteq nsupseteq cup cap uplus sqcup sqcap setminus smallsetminus
    mid nmid parallel nparallel perp vdash dashv models bowtie
    rightarrow leftarrow longrightarrow longleftarrow Rightarrow Leftarrow
    Leftrightarrow leftrightarrow uparrow downarrow updownarrow
    nearrow searrow swarrow nwarrow mapsto longmapsto hookrightarrow
    to gets implies iff impliedby xrightarrow xleftarrow
    infty partial nabla aleph hbar ell wp Re Im prime emptyset varnothing
    angle measuredangle triangle square diamond lozenge surd top bot
    flat natural sharp
    alpha beta gamma delta epsilon varepsilon zeta eta theta vartheta iota
    kappa lambda mu nu xi pi varpi rho varrho sigma varsigma tau upsilon
    phi varphi chi psi omega
    Gamma Delta Theta Lambda Xi Pi Sigma Upsilon Phi Psi Omega
    sin cos tan cot sec csc arcsin arccos arctan sinh cosh tanh coth
    log ln lg exp lim limsup liminf sup inf max min arg deg det dim gcd ker
    Pr hom
    pmod bmod mod pod
    left right big Big bigg Bigg bigl bigr Bigl Bigr biggl biggr Biggl Biggr
    bigm Bigm biggm Biggm
    lfloor rfloor lceil rceil langle rangle lbrace rbrace vert Vert
    lvert rvert lVert rVert
    quad qquad hspace thinspace medspace thickspace negthinspace
    text textbf textit textrm textsf textnormal mbox
    mathrm mathbf mathit mathsf mathtt mathcal mathbb mathfrak boldsymbol
    mathop operatorname mathrel mathbin mathopen mathclose mathpunct
    mathord mathinner
    overline underline widehat widetilde hat tilde bar vec dot ddot
    overbrace underbrace overset underset overrightarrow overleftarrow
    stackrel boxed smash phantom
    displaystyle textstyle scriptstyle scriptscriptstyle limits nolimits
""".split())

# 允许出现的数学环境（\begin{env} / \end{env}；允许一个尾随 * 变体）
_ALLOWED_MATH_ENVS = frozenset("""
    aligned align gathered gather multline split cases
    matrix pmatrix bmatrix Bmatrix vmatrix Vmatrix smallmatrix
    array equation alignedat
""".split())

# 允许的控制符号（\ 后跟单个非字母字符），如 \\ \, \; \: \! \{ \} \& \_
_ALLOWED_MATH_SYMBOLS = frozenset("\\,;:!{}&#_%$| ")

# \text 类命令：花括号参数内容递归校验（防 $\text{\input{x}}$ 绕过）
_MATH_TEXTLIKE_CS = frozenset(
    ("text", "textbf", "textit", "textrm", "textsf", "textnormal", "mbox"))

_MATH_CS_NAME_RE = re.compile(r"[A-Za-z]+")
_MATH_MAX_DEPTH = 8


def _find_group_end(s: str, open_idx: int):
    """返回与 s[open_idx]=='{' 配对的 '}' 下标；找不到返回 None。"""
    depth = 0
    i = open_idx
    while i < len(s):
        ch = s[i]
        if ch == "\\":
            i += 2  # 跳过控制符号（如 \{ \}），不参与配对
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return None


def _validate_math_body(body: str, issues: list, depth: int = 0) -> None:
    """逐字符扫描单个数学片段，校验控制序列白名单（就地累积 issue）。"""
    if depth > _MATH_MAX_DEPTH:
        issues.append({"code": "FORBIDDEN_MATH_CONTROL_SEQUENCE",
                       "message": "数学段嵌套过深，按解析失败处理（fail-closed）"})
        return
    i = 0
    n = len(body)
    while i < n:
        if body[i] != "\\":
            i += 1
            continue
        m = _MATH_CS_NAME_RE.match(body, i + 1)
        if m is None:
            # 控制符号：\ 后跟单个非字母字符
            sym = body[i + 1:i + 2]
            if sym and sym in _ALLOWED_MATH_SYMBOLS:
                i += 2
                continue
            issues.append({"code": "FORBIDDEN_MATH_CONTROL_SEQUENCE",
                           "message": "数学段含未授权控制符号 \\%s" % sym})
            i += 2
            continue
        name = m.group(0)
        i = m.end()
        if name in ("begin", "end"):
            j = i
            while j < n and body[j] in " \t":
                j += 1
            if j >= n or body[j] != "{":
                issues.append({"code": "FORBIDDEN_MATH_CONTROL_SEQUENCE",
                               "message": "\\%s 缺少环境名（解析失败，"
                                          "fail-closed）" % name})
                continue
            end = _find_group_end(body, j)
            if end is None:
                issues.append({"code": "FORBIDDEN_MATH_CONTROL_SEQUENCE",
                               "message": "\\%s 环境名花括号未闭合"
                                          "（解析失败，fail-closed）" % name})
                return
            env = body[j + 1:end].strip()
            env_base = env[:-1] if env.endswith("*") else env
            if not env_base or env_base not in _ALLOWED_MATH_ENVS:
                issues.append({"code": "FORBIDDEN_MATH_CONTROL_SEQUENCE",
                               "message": "数学段含未授权环境 %s（\\%s）"
                                          % (env or "<空>", name)})
            i = end + 1
            continue
        if name not in _ALLOWED_MATH_CS:
            issues.append({"code": "FORBIDDEN_MATH_CONTROL_SEQUENCE",
                           "message": "数学段含未授权控制序列 \\%s" % name})
            continue
        if name in _MATH_TEXTLIKE_CS:
            j = i
            while j < n and body[j] in " \t":
                j += 1
            if j >= n or body[j] != "{":
                issues.append({"code": "FORBIDDEN_MATH_CONTROL_SEQUENCE",
                               "message": "\\%s 缺少花括号参数（解析失败，"
                                          "fail-closed）" % name})
                continue
            end = _find_group_end(body, j)
            if end is None:
                issues.append({"code": "FORBIDDEN_MATH_CONTROL_SEQUENCE",
                               "message": "\\%s 参数花括号未闭合（解析失败，"
                                          "fail-closed）" % name})
                return
            _validate_math_body(body[j + 1:end], issues, depth + 1)
            i = end + 1


def _validate_math_segment(text: str) -> list:
    """校验文本中全部数学段的控制序列白名单；返回 issue 列表（空即通过）。

    fail-closed：未知控制序列、未授权环境、解析失败一律报 issue，
    不静默删除或放行。
    """
    issues = []
    if not isinstance(text, str):
        return issues
    for seg in _MATH_SEGMENT_RE.findall(text):
        # 剥离定界符本身（$$...$$ / $...$ / \(...\) / \[...\]），只校验内容
        if seg.startswith("$$"):
            body = seg[2:-2]
        elif seg.startswith("$"):
            body = seg[1:-1]
        else:  # \(...\) 或 \[...\]
            body = seg[2:-2]
        _validate_math_body(body, issues)
    return issues

_TEX_ESCAPES = (
    ("\\", "\\textbackslash{}"),
    ("&", "\\&"),
    ("%", "\\%"),
    ("$", "\\$"),
    ("#", "\\#"),
    ("_", "\\_"),
    ("{", "\\{"),
    ("}", "\\}"),
)


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def escape_text(text: str) -> str:
    """转义纯文本段（调用方已保证其中不含数学片段）。"""
    for old, new in _TEX_ESCAPES:
        text = text.replace(old, new)
    return text


def tex_escape(text: str) -> str:
    """分段转义：数学片段原样透传，文本段转义特殊字符。"""
    if not isinstance(text, str):
        return ""
    out = []
    for i, part in enumerate(_MATH_SEGMENT_RE.split(text)):
        if i % 2 == 1:
            out.append(part)  # 数学片段原样保留
        else:
            out.append(escape_text(part))
    return "".join(out)


def validate_input(task_data: dict) -> list:
    """渲染前校验（schema + 业务规则）。错误列表为空即通过。"""
    errors = []
    if task_data.get("schema_version") != EXPECTED_SCHEMA_VERSION:
        errors.append({
            "code": "INVALID_SCHEMA_VERSION",
            "message": "schema_version 必须是 %s，得到 %r"
                       % (EXPECTED_SCHEMA_VERSION,
                          task_data.get("schema_version"))})
    for field in ("run_id", "question_id", "section_id", "task_id", "title",
                  "stem", "subquestions", "level", "source_refs",
                  "brief_id", "brief_revision", "packet_digest",
                  "receipt_ref"):
        if field not in task_data:
            errors.append({"code": "MISSING_REQUIRED_FIELD", "field": field,
                           "message": "缺少必填字段：%s" % field})
    if errors:
        return errors

    subquestions = task_data.get("subquestions", [])
    if not isinstance(subquestions, list) or not (3 <= len(subquestions) <= 6):
        errors.append({
            "code": "INVALID_SUBQUESTION_COUNT",
            "count": len(subquestions) if isinstance(subquestions, list)
                     else None,
            "message": "subquestions 数量必须 3-6 个"})
        return errors

    seen = set()
    for i, sq in enumerate(subquestions):
        sid = sq.get("subquestion_id", "[index %d]" % i)
        if sid in seen:
            errors.append({"code": "DUPLICATE_SUBQUESTION_ID",
                           "subquestion_id": sid,
                           "message": "重复的 subquestion_id: %s" % sid})
        seen.add(sid)
        for req in ("prompt", "answer_space_mm"):
            if req not in sq:
                errors.append({"code": "MISSING_SUBQUESTION_FIELD",
                               "subquestion_id": sid, "field": req,
                               "message": "小问 %s 缺少必填字段：%s"
                                          % (sid, req)})
        if sq.get("hint_level") and sq.get("hint_level") not in ("observation", "direction",
                                        "verification"):
            errors.append({"code": "INVALID_HINT_LEVEL",
                           "subquestion_id": sid,
                           "message": "无效的 hint_level: %r"
                                      % sq.get("hint_level")})
        if sq.get("reveal") and sq.get("reveal") not in ("initial", "on_demand"):
            errors.append({"code": "INVALID_REVEAL_VALUE",
                           "subquestion_id": sid,
                           "message": "无效的 reveal: %r"
                                      % sq.get("reveal")})
        ans = sq.get("answer_space_mm")
        if not isinstance(ans, int) or isinstance(ans, bool) \
                or not (16 <= ans <= 60):
            errors.append({"code": "INVALID_ANSWER_SPACE",
                           "subquestion_id": sid,
                           "message": "answer_space_mm 必须在 16-60 之间，"
                                      "得到 %r" % ans})
        for field in ("prompt", "hint", "expected_evidence"):
            value = sq.get(field)
            if not isinstance(value, str):
                continue
            if _FORBIDDEN_CS_RE.search(_MATH_SEGMENT_RE.sub("", value)):
                errors.append({
                    "code": "FORBIDDEN_CONTROL_SEQUENCE",
                    "subquestion_id": sid, "field": field,
                    "message": "字段 %s 的文本段含有禁止的控制序列"
                               "（TeX 宏必须由渲染器生成，不得由 JSON 注入）"
                               % field})
            for issue in _validate_math_segment(value):
                errors.append(dict(issue, subquestion_id=sid, field=field))
    for field in ("title", "stem"):
        value = task_data.get(field)
        if not isinstance(value, str):
            continue
        if _FORBIDDEN_CS_RE.search(_MATH_SEGMENT_RE.sub("", value)):
            errors.append({"code": "FORBIDDEN_CONTROL_SEQUENCE",
                           "field": field,
                           "message": "字段 %s 的文本段含有禁止的控制序列"
                                      % field})
        # G1.1：题干 stem 回写（逐字保留）之前必须通过数学段白名单校验；
        # 含非法控制序列的 stem 视为门禁失败，不得“为保一致而放行”。
        for issue in _validate_math_segment(value):
            errors.append(dict(issue, field=field))
    if task_data.get("question_id") \
            and task_data.get("task_id") != "task-%s" % task_data["question_id"]:
        errors.append({"code": "BAD_TASK_ID",
                       "message": "task_id=%r 应为 task-%s"
                                  % (task_data.get("task_id"),
                                     task_data["question_id"])})
    return errors


def render_student_task(task_data: dict, input_digest: str = "") -> str:
    """完整渲染学生任务为 TeX（确定性：同一 JSON 必得同一输出）。
    每题作成一个大题（题号 1. 2. 3.，由 probchain 承载），
    各问作成小问（题号 (1) (2) (3)，由二级 enumerate 承载），
    删除提示与应留证据。
    """
    qid = task_data["question_id"]
    title = (task_data.get("title") or "").strip()
    raw_stem = task_data.get("stem", "")
    clean_stem = contract.strip_numbering_and_source(raw_stem)
    stem = tex_escape(clean_stem)

    lines = [
        "% === RENDERED BY render_student_task.py（机器标记，勿手改） ===",
        "%% renderer_version: %s" % RENDERER_VERSION,
        "%% contract_version: %s" % contract.CONTRACT_VERSION,
        "%% run_id: %s" % task_data.get("run_id", ""),
        "%% task_id: %s" % task_data.get("task_id", ""),
        "%% question_id: %s" % qid,
        "%% section_id: %s" % task_data.get("section_id", ""),
        "%% input_digest: %s" % (input_digest or "(unbound)"),
        "% === QUESTION BLOCK START ===",
        "",
        "\\begin{probchain}",
    ]
    if title:
        lines.append("%% title: %s" % tex_escape(title))
    lines.append("\\item %s" % stem)
    lines.extend([
        "",
        "\\begin{enumerate}",
        "",
    ])
    for i, sq in enumerate(task_data["subquestions"], start=1):
        lines.append("\\item %s" % tex_escape(sq["prompt"]))
        # 留白必须带单位（header.tex 的 \ansspace 直接交给 \vspace）
        lines.append("\\ansspace{%dmm}  %% subquestion_id: %s"
                     % (sq.get("answer_space_mm", 22),
                        tex_escape(sq.get("subquestion_id", ""))))
        lines.append("")
    lines.append("\\end{enumerate}")
    lines.append("\\end{probchain}")
    lines.append("")
    lines.append("% === QUESTION BLOCK END ===")
    lines.append("%% level: %s" % task_data.get("level", "L0"))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="渲染 student-task.json 为 TeX 块（2.4.0，仅输出到 "
                    "broker 指定的 staging 目标）")
    parser.add_argument("--project", required=True, help="项目根目录")
    parser.add_argument("--run-id", required=True, help="broker 签发的 run ID")
    parser.add_argument("--task-id", required=True, help="任务 ID")
    parser.add_argument("--input", "-i", required=True,
                        help="staging/<run_id>/tasks/<task_id>/student-task.json")
    args = parser.parse_args()

    project_root = os.path.abspath(args.project)
    try:
        task_data = load_json(args.input)
    except Exception as exc:
        print("ERROR 读取输入文件：%s" % exc)
        return 1

    errors = validate_input(task_data)
    if errors:
        print("ERROR 输入验证失败 (%d 个错误):" % len(errors))
        for err in errors[:10]:
            print("  [%s] %s" % (err.get("code", "UNKNOWN"),
                                 err.get("message", "")))
        return 1

    # 输出只能落在 staging/<run_id>/rendered/<task_id>.tex
    out_rel = "staging/%s/rendered/%s.tex" % (args.run_id, args.task_id)
    out_abs = contract.resolve_path(project_root, out_rel)
    if out_abs is None:
        print("ERROR 输出路径越出项目根: %s" % out_rel)
        return 2
    input_digest = contract.sha256_file(os.path.abspath(args.input))
    tex_content = render_student_task(task_data, input_digest=input_digest)
    os.makedirs(os.path.dirname(out_abs), exist_ok=True)
    with open(out_abs, "w", encoding="utf-8", newline="\n") as f:
        f.write(tex_content)
        f.write("\n")
    print("已渲染到：%s（renderer=%s，input_digest=%s）"
          % (out_rel, RENDERER_VERSION, (input_digest or "")[:16]))
    print("  - 题目：%s" % task_data["question_id"])
    print("  - 小问数：%d" % len(task_data["subquestions"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
