#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_student_solution.py — 生成并编译学生参考解答学案 (student_solution.tex & pdf)

核心职责：
  1. 题级解答注入：提取各题严谨证明并提纯为学术级规范解答，去除浮夸文风与 Markdown 专用标记；
  2. 疏松排版优化：将关键推导方程、上下界与同余式自动提升为行间独立公式 (\\[ ... \\])，确保版面透气清晰；
  3. 自动生成 task-<qid>-solution.tex：保留题干及小问设问，剥离 \\ansspace 留白，注入 \\begin{solution}；
  4. 组织装配独立的 student_solution.tex 主入口；
  5. 调用 XeLaTeX 编译生成 student_solution.pdf，并自动同步交付至 release/ 目录。

用法：
  python scripts/gen_student_solution.py [项目根目录] [--compile] [--output student_solution.tex]
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


# G5.1：qid 路径穿越加固——所有含 qid 的路径拼接必须经 contract.safe_qid_path，
# 非法 qid 一律 fail-closed（CLI 退出码 2），禁止回退到裸 os.path.join。
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
import contract  # noqa: E402


class UnsafeQidError(ValueError):
    """question_id 不安全（路径穿越载荷等）——fail-closed，调用方必须拒绝。"""


def require_valid_qid(qid) -> str:
    """校验 qid 是否为安全标识符；非法时抛 UnsafeQidError（fail-closed）。"""
    if not contract.validate_qid(qid):
        raise UnsafeQidError(
            "非法 question_id %r（须匹配 %s，禁止路径分隔符与穿越序列）"
            % (qid, contract.SAFE_ID_PATTERN))
    return qid


def safe_join(root, *parts) -> str:
    """contract.safe_qid_path 的调用方收口：越界返回 None 时抛 UnsafeQidError。"""
    path = contract.safe_qid_path(root, *parts)
    if path is None:
        raise UnsafeQidError(
            "路径拼接越出项目根，拒绝: root=%r parts=%r" % (root, parts))
    return path


FLAMBOYANT_REPLACEMENTS = [
    ("神来之笔", "关键构造"),
    ("宛如神迹", "巧妙构造"),
    ("高深莫测", "抽象"),
    ("降维打击", "尺度压缩"),
    ("集中优势兵力打歼灭战", "集中突破"),
    ("打歼灭战", "突破核心环节"),
    ("思维跃迁", "思维推进"),
    ("迷思破解", "关键分析"),
    ("通法思考范式", "通性通法"),
    ("认知演进逻辑", "推导演进逻辑"),
    ("面对庞然大物，我们能做什么？", "分析数位特征："),
]

MATH_COMMANDS = [
    r"\sum", r"\prod", r"\int", r"\frac", r"\sqrt", r"\le", r"\ge",
    r"\neq", r"\approx", r"\equiv", r"\pmod", r"\binom", r"\dots",
    r"\cdots", r"\in", r"\subset", r"\cup", r"\cap", r"\alpha",
    r"\beta", r"\gamma", r"\lambda", r"\mu", r"\pi", r"\sigma",
    r"\theta", r"\infty", r"\mid", r"\nmid", r"\gcd", r"\quad",
    r"\qquad", r"\mathbf", r"\mathbb", r"\mathcal", r"\mathrm",
    r"\left", r"\right", r"\implies", r"\iff", r"\text", r"\overline",
]


def clean_flamboyant_words(text: str) -> str:
    """清理浮夸辞藻，回归严谨简明的竞赛数学学术文风。"""
    if not text:
        return ""
    for old, new in FLAMBOYANT_REPLACEMENTS:
        text = text.replace(old, new)
    return text


def is_naked_math_line(s: str) -> bool:
    """检测单行是否为未包裹 $ 或 $$ 的独立数学公式行。"""
    if not s:
        return False
    if s.startswith(('%', '#', r'\begin', r'\end', r'\par', r'\noindent', r'\[', r'\item')):
        return False
    if '$' in s or r'\[' in s or r'\]' in s:
        return False
    if re.match(r'^[-*+]\s+', s) or re.match(r'^\d+\.\s+', s):
        return False
    stripped = re.sub(r'\\text\{[^}]*\}', '', s)
    if re.search(r'[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]', stripped):
        return False
    has_math_cmd = any(cmd in stripped for cmd in MATH_COMMANDS)
    has_sub_sup = bool(re.search(r'[_^]', stripped))
    has_relation = bool(re.search(r'[=<>]', stripped)) and bool(re.search(r'[0-9a-zA-Z\\]', stripped))
    return has_math_cmd or has_sub_sup or has_relation


def purify_solution_text(text: str) -> str:
    """对提取出的解答 Markdown/TeX 文本进行深度纯净化与排版疏松化处理。

    处理规范：
      - 截断元对话与追问说明（如“这是一个费解的证明...请为我解释”）；
      - 提取首个有效 solution 块，杜绝嵌套 \\begin{solution}；
      - 剔除 Markdown 专用标记（--- 分割线、``` 栅栏、无序列表星号等）；
      - 提升公式排版疏松度：独立公式及冒号后推导关系转为行间公式 \\[ ... \\]；
      - 规范粗体与标题，清除浮夸用词。
    """
    if not text:
        return "本题结论成立，完整证明见教研卷。"

    # 1. 截断元对话与追问说明
    for cut_marker in ("这是一个费解的证明", "请为我解释", "如何想到这个证明", "第一层迷思破解", "破局动机"):
        if cut_marker in text:
            text = text.split(cut_marker)[0].strip()

    # 2. 若外部传入包含 \\begin{solution} ... \\end{solution}，提取内部真实正文
    m_sol = re.search(r"\\begin\{solution\}([\s\S]*?)\\end\{solution\}", text)
    if m_sol:
        text = m_sol.group(1).strip()

    # 3. 剔除顶部的题目复述与元标题标记
    text = re.sub(r"^##\s+题目.*?\n+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^【(?:预|例)\d+】.*?\n+", "", text, flags=re.MULTILINE)

    # 4. 浮夸词汇净化
    text = clean_flamboyant_words(text)

    # 5. 逐行解析与疏松化转换
    lines = []
    in_display_math = False
    for line in text.splitlines():
        s = line.strip()
        if not s:
            if in_display_math:
                lines.append(r"\]")
                in_display_math = False
            lines.append("")
            continue

        # 去除 Markdown 引用块前缀 >
        if s.startswith(">"):
            s = re.sub(r"^>+\s*", "", s).strip()
            if not s:
                continue

        # 忽略 Markdown 水平分割线
        if re.match(r"^[-*_]{3,}$", s):
            if in_display_math:
                lines.append(r"\]")
                in_display_math = False
            lines.append(r"\par\vspace{1ex}")
            continue

        # 忽略代码块栅栏
        if s.startswith("```"):
            continue

        # 剥离多余的 \\begin{solution} / \\end{solution}
        if s.startswith(r"\begin{solution}") or s.startswith(r"\end{solution}"):
            continue

        # 去除行首无序列表符号（如 * 或 -），转化为自然段落排版
        if re.match(r"^[-*+]\s+", s):
            s = re.sub(r"^[-*+]\s+", "", s).strip()

        # 处理标题行：# 标题 -> \par\noindent\textbf{标题}\par\medskip
        m_head = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m_head:
            if in_display_math:
                lines.append(r"\]")
                in_display_math = False
            title = m_head.group(2).strip()
            title = title.replace("#", r"\#")
            title = re.sub(r"\*\*([^*]+)\*\*", r"\\textbf{\1}", title)
            title = re.sub(r"`([^`]+)`", r"\\texttt{\1}", title)
            lines.append(r"\par\noindent\textbf{%s}\par\smallskip" % title)
            continue

        # 单行封闭的 $$ ... $$ (两端都有 $$)
        if s.startswith("$$") and s.endswith("$$") and len(s) > 2:
            inner = s[2:-2].strip()
            lines.append(r"\[ %s \]" % inner)
            continue

        # 单行封闭的 \[ ... \] (两端都有 \[ 和 \])
        if s.startswith(r"\[") and s.endswith(r"\]") and len(s) > 3:
            if in_display_math:
                lines.append(r"\]")
                in_display_math = False
            lines.append(s)
            continue

        # 多行 $$ 或 \[ 开启：以 $$ 或 \[ 开头但结尾不是
        if (s.startswith("$$") and not s.endswith("$$")) or (s.startswith(r"\[") and not s.endswith(r"\]")):
            if in_display_math:
                lines.append(r"\]")
            lines.append(r"\[")
            in_display_math = True
            rest = s[2:].strip()
            if rest:
                lines.append(rest)
            continue

        # 纯独立 $$ 或 \[
        if s in ("$$", r"\["):
            if in_display_math:
                lines.append(r"\]")
                in_display_math = False
            else:
                lines.append(r"\[")
                in_display_math = True
            continue

        # 纯独立 \]
        if s == r"\]":
            if in_display_math:
                lines.append(r"\]")
                in_display_math = False
            continue

        # 当前处于多行 display math 块内
        if in_display_math:
            if s.endswith("$$") or s.endswith(r"\]"):
                rest = s[:-2].strip()
                if rest:
                    lines.append(rest)
                lines.append(r"\]")
                in_display_math = False
            elif s in ("$$", r"\]"):
                lines.append(r"\]")
                in_display_math = False
            else:
                lines.append(line)
            continue

        # 单行纯独立公式 $ ... $ 或带末尾标点的 $ ... $[。，；,.] 自动提升为行间公式 \[ ... \]，使排版疏松
        m_single_math = re.match(r"^\s*\$([^$\n]+)\$(?:([。，；,.])\s*)?$", s)
        if m_single_math:
            inner = m_single_math.group(1).strip()
            punct = m_single_math.group(2) or ""
            lines.append(r"\[ %s%s \]" % (inner, punct))
            continue

        # 冒号后接的长推导/方程公式自动分行并提升为行间公式，增强排版疏松度
        m_colon_math = re.match(r"^(.*?[:：])\s*\$([^$\n]+)\$$", s)
        if m_colon_math and any(sym in m_colon_math.group(2) for sym in ("=", r"\le", r"\ge", r"\equiv", r"\implies", r"\pmod", "<", ">")):
            lead_text = m_colon_math.group(1).strip()
            inner_math = m_colon_math.group(2).strip()
            lead_proc = re.sub(r"\*\*([^*]+)\*\*", r"\\textbf{\1}", lead_text)
            lines.append(lead_proc)
            lines.append(r"\[ %s \]" % inner_math)
            continue

        # 裸露独立公式行自动包裹为 \[ ... \]
        if is_naked_math_line(s):
            inner = s.strip()
            if inner.startswith("$") and not inner.endswith("$"):
                inner = inner[1:].strip()
            elif inner.endswith("$") and not inner.startswith("$"):
                inner = inner[:-1].strip()
            lines.append(r"\[ %s \]" % inner)
            continue

        # 替换正文中的粗体 **foo** -> \textbf{foo}
        s_proc = re.sub(r"\*\*([^*]+)\*\*", r"\\textbf{\1}", s)
        # 替换反引号 `code` -> \texttt{code}
        s_proc = re.sub(r"`([^`]+)`", r"\\texttt{\1}", s_proc)

        # 转义非数学环境中的特殊字符：#、_、&、%
        new_chars = []
        in_m = False
        for char in s_proc:
            if char == "$":
                in_m = not in_m
                new_chars.append(char)
            elif char == "#" and not in_m:
                new_chars.append(r"\#")
            elif char == "_" and not in_m:
                new_chars.append(r"\_")
            elif char == "&" and not in_m:
                new_chars.append(r"\&")
            elif char == "%" and not in_m:
                new_chars.append(r"\%")
            else:
                new_chars.append(char)
        if in_m:
            new_chars.append("$")
        lines.append("".join(new_chars))

    if in_display_math:
        lines.append(r"\]")

    res = "\n".join(lines)
    # 格式微调：确保 (1) 解：、(2) 证明：等小问标题前有段落断行
    res = re.sub(r"(\\textbf\{\s*\([1-9一二三四]\)\s*(?:解|证明)[：:]\s*\})", r"\n\\par\\medskip\n\1\n", res)
    return res.strip()


def clean_proof_preamble(text: str) -> str:
    """去除顶部的 # 标题、**题目**、**证明**等公共引导词。"""
    t = text.strip()
    t = re.sub(r"^#\s+[^\n]+\n*", "", t)
    t = re.sub(r"^\*\*题目\*\*[\s\S]*?\n\s*\*\*证明\*\*[:：]?\s*\n*", "", t)
    t = re.sub(r"^\*\*题目\*\*[\s\S]*?\n\s*\*\*解\*\*[:：]?\s*\n*", "", t)
    t = re.sub(r"^\*\*证明\*\*[:：]?\s*\n*", "", t)
    t = re.sub(r"^\*\*解\*\*[:：]?\s*\n*", "", t)
    t = re.sub(r"^\\par\\noindent\\textbf\{解答与证明[^\}]*\}\s*\\par\\medskip\s*", "", t)
    t = re.sub(r"^\\textbf\{题目\}[\s\S]*?\\textbf\{证明\}[:：]?\s*", "", t)
    return t.strip()


def split_solution_by_subquestions(sol_text: str, num_subquestions: int, subquestion_prompts: list = None) -> list:
    """把整体证明正文解构为 num_subquestions 个小问的独立严谨解答列表。"""
    if num_subquestions <= 1:
        return [purify_solution_text(sol_text)]

    cleaned = clean_proof_preamble(sol_text)

    # 1. 尝试显式小问标记匹配：(1) 解、(2) 证明、### (1) 等
    explicit_pat = re.compile(
        r"(?:^|\n)\s*(?:###?\s*)?(?:\*\*)?(?:【|\()?([1-9一二三四])(?:\)|】|\.)\s*(?:解|证明|分析|计算)[：:]\s*(?:\*\*)?",
        re.MULTILINE
    )
    explicit_matches = list(explicit_pat.finditer(cleaned))
    if len(explicit_matches) >= num_subquestions:
        chunks = []
        for i in range(len(explicit_matches)):
            start = explicit_matches[i].start()
            end = explicit_matches[i + 1].start() if i + 1 < len(explicit_matches) else len(cleaned)
            chunk = cleaned[start:end].strip()
            chunks.append(chunk)
        if len(chunks) == num_subquestions:
            return [purify_solution_text(c) for c in chunks]

    # 2. 尝试数字序号列表匹配：1. ... 2. ... 3. ...
    num_pat = re.compile(r"(?:^|\n)\s*([1-9])\.\s+", re.MULTILINE)
    matches = list(num_pat.finditer(cleaned))
    if len(matches) >= 2:
        step_chunks = []
        for i in range(len(matches)):
            start = matches[i].start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(cleaned)
            step_chunks.append(cleaned[start:end].strip())

        M = len(step_chunks)
        N = num_subquestions
        if M == N:
            return [purify_solution_text(c) for c in step_chunks]
        elif M > N:
            grouped = [[] for _ in range(N)]
            if subquestion_prompts and len(subquestion_prompts) == N:
                def get_keywords(s):
                    return set(re.findall(r"[a-zA-Z\\]+|[\u4e00-\u9fa5]{2,}", s))

                current_subq = 0
                for step_idx, step_text in enumerate(step_chunks):
                    remaining_steps = M - step_idx
                    remaining_subqs = N - current_subq
                    if remaining_steps <= remaining_subqs:
                        current_subq = min(current_subq + 1, N - 1)
                        grouped[current_subq].append(step_text)
                    else:
                        if current_subq + 1 < N:
                            next_kw = get_keywords(subquestion_prompts[current_subq + 1])
                            curr_kw = get_keywords(subquestion_prompts[current_subq])
                            step_kw = get_keywords(step_text)
                            score_curr = len(step_kw & curr_kw)
                            score_next = len(step_kw & next_kw)
                            if score_next > score_curr and len(grouped[current_subq]) > 0:
                                current_subq += 1
                        grouped[current_subq].append(step_text)
            else:
                step_per_sub = M / N
                for idx, c in enumerate(step_chunks):
                    slot = min(int(idx / step_per_sub), N - 1)
                    grouped[slot].append(c)

            res = []
            for g in grouped:
                combined = "\n\n".join(g) if g else ""
                if not combined:
                    raise RuntimeError(f"小问解答切分产生空段落，无法满足严谨解答要求")
                res.append(purify_solution_text(combined))
            return res

    # 3. 契约 2.5.0 (F2/F11 零容忍)：严禁使用“首问放全解，后问写同上”的敷衍兜底，必须 fail-closed
    raise RuntimeError(
        f"无法从原题全解自动切分出 {num_subquestions} 个独立小问解答！"
        f"必须由 T2.5 subquestion-solver 子 Agent 为各小问分别生成严谨独立的 solution-task.json。"
    )


def parse_task_tex_structure(task_content: str):
    """解析 task-<qid>.tex 中的题干与各小问設问。"""
    # 提取题干
    m_stem = re.search(r"\\begin\{probchain\}[\s\S]*?\\item\s+([\s\S]*?)(?:\\begin\{enumerate\}|\\end\{probchain\})", task_content)
    raw_stem = m_stem.group(1).strip() if m_stem else ""
    stem = contract.strip_numbering_and_source(raw_stem)

    # 提取各个小问
    m_enum = re.search(r"\\begin\{enumerate\}([\s\S]*?)\\end\{enumerate\}", task_content)
    subquestions = []
    if m_enum:
        enum_body = m_enum.group(1).strip()
        raw_items = re.split(r"(?m)^\\item\s+", enum_body)
        for it in raw_items:
            it = it.strip()
            if not it:
                continue
            it_clean = re.sub(r"\\ansspace\{[^}]*\}[\s\S]*$", "", it).strip()
            m_sid = re.search(r"%+\s*subquestion_id:\s*(\S+)", it)
            sid = m_sid.group(1) if m_sid else ""
            subquestions.append({"prompt": it_clean, "subquestion_id": sid})

    # 提取可选的 title 注释
    m_title = re.search(r"%+\s*title:\s*([^\n]+)", task_content)
    title = m_title.group(1).strip() if m_title else ""

    return stem, subquestions, title


def load_subquestion_solutions_from_metadata(project_root: str, qid: str) -> list:
    """提取规范 solution-task.json 中经子 Agent 验证过的逐问解答（契约 2.5.0）。"""
    require_valid_qid(qid)
    # 1. 优先读取规范 solution-task.json
    cand_sols = [
        safe_join(project_root, "teaching", "generated", f"{qid}-solution-task.json"),
    ]
    # 扫描 staging 下的对应文件
    staging_dir = os.path.join(project_root, "staging")
    if os.path.isdir(staging_dir):
        for run_id in os.listdir(staging_dir):
            cand_sols.append(safe_join(staging_dir, run_id, "tasks", f"task-{qid}", "solution-task.json"))

    for cpath in cand_sols:
        if os.path.isfile(cpath):
            try:
                with open(cpath, "r", encoding="utf-8-sig") as f:
                    data = json.load(f)
                sq_sols = data.get("subquestion_solutions", [])
                if sq_sols and all(s.get("detailed_solution") for s in sq_sols):
                    return [purify_solution_text(s["detailed_solution"]) for s in sq_sols]
            except Exception:
                pass

    # 2. 备用尝试 research/problems/<qid>/solution.json
    sol_json = safe_join(project_root, "research", "problems", qid, "solution.json")
    if os.path.isfile(sol_json):
        try:
            with open(sol_json, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            sub_res = data.get("subquestion_results", [])
            if sub_res and all(sr.get("detailed_solution") for sr in sub_res):
                return [purify_solution_text(sr["detailed_solution"]) for sr in sub_res]
        except Exception:
            pass

    return None


def extract_solution_for_question(project_root: str, qid: str) -> str:
    """多层回退提取题目的严谨参考解答正文。"""
    require_valid_qid(qid)
    # 1. 优先从 research/problems/<qid>/solution.md
    sol_md = safe_join(project_root, "research", "problems", qid, "solution.md")
    if os.path.isfile(sol_md):
        try:
            with open(sol_md, "r", encoding="utf-8-sig", errors="replace") as f:
                content = f.read().strip()
                if content:
                    return purify_solution_text(content)
        except Exception:
            pass

    # 2. 次选 teaching/content/blk-<qid>-proof.tex
    proof_tex = safe_join(project_root, "teaching", "content", f"blk-{qid}-proof.tex")
    if os.path.isfile(proof_tex):
        try:
            with open(proof_tex, "r", encoding="utf-8-sig", errors="replace") as f:
                content = f.read().strip()
                if content:
                    return purify_solution_text(content)
        except Exception:
            pass

    # 3. 候选 sources/problems/<qid>.md 或项目根目录下的 <qid>.md / <qid>_solution.md
    for cand in [
        safe_join(project_root, f"{qid}_solution.md"),
        safe_join(project_root, f"{qid}.md"),
        safe_join(project_root, "sources", "problems", f"{qid}.md"),
        safe_join(project_root, "sources", "solutions", f"{qid}.md"),
    ]:
        if os.path.isfile(cand):
            try:
                with open(cand, "r", encoding="utf-8-sig", errors="replace") as f:
                    txt = f.read()
                m = re.search(r"####\s+(?:详细严谨解答|详细解答|严谨解答|解答)([\s\S]*)$", txt)
                if m and m.group(1).strip():
                    return purify_solution_text(m.group(1).strip())
                m2 = re.search(r"\\begin\{solution\}([\s\S]*?)\\end\{solution\}", txt)
                if m2 and m2.group(1).strip():
                    return purify_solution_text(m2.group(1).strip())
            except Exception:
                pass

    # 4. 从 solution.json 中提取
    sol_json = safe_join(project_root, "research", "problems", qid, "solution.json")
    if os.path.isfile(sol_json):
        try:
            with open(sol_json, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            steps = data.get("solution_steps") or data.get("key_deductions") or []
            if steps:
                return purify_solution_text("\n\n".join(str(s) for s in steps))
        except Exception:
            pass

    return "本题详细严谨解答请参考教研全案推导记录。"


def generate_task_solution_file(project_root: str, qid: str) -> str:
    """生成单题的学生参考解答文件 (teaching/content/task-<qid>-solution.tex)。
    全新架构：
      - 对每个小问（subquestion），紧跟对应小问的专属参考解答 \\begin{solution}[第 (i) 问参考解答] ... \\end{solution}；
      - 彻底消除整个大题末尾单一粗粒度大 solution 块；
      - 若无小问设问（单体题），回退为单一大题解答块。
    """
    require_valid_qid(qid)
    content_dir = safe_join(project_root, "teaching", "content")
    os.makedirs(content_dir, exist_ok=True)
    task_file = safe_join(content_dir, f"task-{qid}.tex")
    out_file = safe_join(content_dir, f"task-{qid}-solution.tex")

    task_content = ""
    if os.path.isfile(task_file):
        try:
            with open(task_file, "r", encoding="utf-8-sig", errors="replace") as f:
                task_content = f.read()
        except Exception:
            pass

    stem, subquestions, title = parse_task_tex_structure(task_content)

    if not stem and not subquestions:
        # 回退基础题干与全局解答
        sol_text = extract_solution_for_question(project_root, qid)
        tex_body = [
            f"% === task-{qid}-solution.tex — 学生参考解答版（自动注入纯正简明参考解答）===",
            f"% question_id: {qid}",
            "",
            r"\begin{problem}",
            f"题目 {qid} 进位制与数论核心探究题。",
            r"\end{problem}",
            "",
            r"\begin{solution}[参考解答]",
            sol_text,
            r"\end{solution}",
            ""
        ]
        with open(out_file, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(tex_body))
        return out_file

    # 1. 尝试获取逐小问解答
    subq_solutions = load_subquestion_solutions_from_metadata(project_root, qid)
    if not subq_solutions or len(subq_solutions) != len(subquestions):
        # 从整体解答中切分各小问解答
        sol_text = extract_solution_for_question(project_root, qid)
        prompts = [sq["prompt"] for sq in subquestions]
        subq_solutions = split_solution_by_subquestions(sol_text, len(subquestions), prompts)

    # 2. 组装逐小问嵌入的 TeX 文件
    tex_body = [
        f"% === task-{qid}-solution.tex — 学生参考解答版（逐小问嵌入严谨参考解答）===",
        f"% question_id: {qid}",
        "",
        r"\begin{probchain}",
    ]
    if title:
        tex_body.append(f"% title: {title}")
    tex_body.append(f"\\item {stem}")
    tex_body.append("")

    if subquestions:
        tex_body.append(r"\begin{enumerate}")
        tex_body.append("")
        for i, sq in enumerate(subquestions, start=1):
            prompt = sq["prompt"]
            ans_body = subq_solutions[i - 1] if i - 1 < len(subq_solutions) else "由前述推导即得。"
            tex_body.append(f"\\item {prompt}")
            tex_body.append(f"\\begin{{solution}}[第 ({i}) 问参考解答]")
            tex_body.append(ans_body)
            tex_body.append(r"\end{solution}")
            tex_body.append("")
        tex_body.append(r"\end{enumerate}")
    else:
        # 无小问的大题直接附带解答
        sol_text = extract_solution_for_question(project_root, qid)
        tex_body.append(r"\begin{solution}[参考解答]")
        tex_body.append(sol_text)
        tex_body.append(r"\end{solution}")

    tex_body.append(r"\end{probchain}")
    tex_body.append("")

    final_content = "\n".join(tex_body)
    with open(out_file, "w", encoding="utf-8", newline="\n") as f:
        f.write(final_content)

    return out_file


def discover_selected_question_ids(project_root: str) -> list:
    """多源探测本次教研入选题目 ID 列表，严格保持学案教学切片与设计编排顺序。"""
    # 1. 优先扫描 main.tex 中的切片装配顺序（确保与 student.tex / main.tex 呈现顺序绝对一致）
    main_tex = os.path.join(project_root, "main.tex")
    if os.path.isfile(main_tex):
        try:
            with open(main_tex, "r", encoding="utf-8-sig", errors="replace") as f:
                content = f.read()
            sec_matches = re.findall(r"\\input\{([^}]+)\}", content)
            qids_from_main = []
            for sec_name in sec_matches:
                sec_base = sec_name if sec_name.endswith(".tex") else sec_name + ".tex"
                sec_path = os.path.join(project_root, sec_base)
                if os.path.isfile(sec_path):
                    with open(sec_path, "r", encoding="utf-8-sig", errors="replace") as sf:
                        for sline in sf:
                            m_qid = re.search(r"^\s*%+\s*qid:\s*([^\s%]+)", sline)
                            if m_qid:
                                q = m_qid.group(1).strip()
                                if q and q not in qids_from_main:
                                    qids_from_main.append(q)
                                break
            if qids_from_main:
                return qids_from_main
        except Exception:
            pass

    # 2. 尝试 teaching/architect-plan.json 的 problems 顺序
    arch_path = os.path.join(project_root, "teaching", "architect-plan.json")
    if os.path.isfile(arch_path):
        try:
            with open(arch_path, "r", encoding="utf-8-sig") as f:
                arch = json.load(f)
            qids_arch = []
            for p in arch.get("problems", []):
                pid = p.get("problem_id")
                if pid and str(pid) not in qids_arch:
                    qids_arch.append(str(pid))
            if qids_arch:
                return qids_arch
        except Exception:
            pass

    # 3. 尝试 research/brief.json 中的 selected_problem_ids
    brief_path = os.path.join(project_root, "research", "brief.json")
    if os.path.isfile(brief_path):
        try:
            with open(brief_path, "r", encoding="utf-8-sig") as f:
                brief = json.load(f)
            sel = brief.get("selection", {}).get("selected_problem_ids", [])
            if sel:
                return [str(x) for x in sel]
        except Exception:
            pass

    # 4. 尝试 teaching/materials.json（按内容块首次出现顺序，保留教学编排，切忌无序排序）
    mat_path = os.path.join(project_root, "teaching", "materials.json")
    if os.path.isfile(mat_path):
        try:
            with open(mat_path, "r", encoding="utf-8-sig") as f:
                mat = json.load(f)
            qids_mat = []
            for b in mat.get("blocks", []):
                qid = b.get("question_id") or b.get("problem_id")
                if qid and str(qid) not in qids_mat:
                    qids_mat.append(str(qid))
            if qids_mat:
                return qids_mat
        except Exception:
            pass

    # 5. 兜底扫描 teaching/content/task-*.tex
    content_dir = os.path.join(project_root, "teaching", "content")
    if os.path.isdir(content_dir):
        qids = []
        for fn in os.listdir(content_dir):
            m = re.match(r"^task-(\d+)\.tex$", fn)
            if m:
                qids.append(m.group(1))
        if qids:
            return sorted(qids, key=lambda x: int(x) if x.isdigit() else str(x))

    return ["1"]


def find_task_files(project_root: str, ordered_qids: list = None):
    """扫描 teaching/content 目录下的 task-*-solution.tex，严格按照入选题目设计顺序排布。"""
    content_dir = os.path.join(project_root, "teaching", "content")
    if not os.path.isdir(content_dir):
        return []

    if ordered_qids:
        res = []
        for qid in ordered_qids:
            sol_name = f"task-{qid}-solution.tex"
            task_name = f"task-{qid}.tex"
            if os.path.isfile(os.path.join(content_dir, sol_name)):
                res.append(sol_name)
            elif os.path.isfile(os.path.join(content_dir, task_name)):
                res.append(task_name)
        return res

    tasks = []
    for f in os.listdir(content_dir):
        m = re.match(r"^task-(\d+)(?:-solution)?\.tex$", f)
        if m:
            qid = int(m.group(1))
            tasks.append((qid, f))

    tasks_by_qid = {}
    for qid, f in tasks:
        if qid not in tasks_by_qid:
            tasks_by_qid[qid] = f
        elif f.endswith("-solution.tex"):
            tasks_by_qid[qid] = f

    return [tasks_by_qid[q] for q in sorted(tasks_by_qid.keys())]


def generate_student_solution_tex(project_root: str, output_filename: str = "student_solution.tex"):
    """装配生成完整的 student_solution.tex 主入口。"""
    project_root = os.path.abspath(project_root)

    # 1. 首先确保各入选题目的 task-<qid>-solution.tex 均已生成
    qids = discover_selected_question_ids(project_root)
    # G5.1 fail-closed：任何来源探测到的非法 qid 一律拒绝（CLI 退出码 2），
    # 不得静默跳过或拼接进路径
    for qid in qids:
        require_valid_qid(qid)
    print(f"[gen_student_solution] 正在为题目 {qids} 生成解答块...")
    for qid in qids:
        generate_task_solution_file(project_root, qid)

    # 2. 扫描已生成的 task 列表（严格保持 qids 教学设计与装配顺序）
    tasks = find_task_files(project_root, ordered_qids=qids)
    if not tasks:
        print("[gen_student_solution] 警告: 未在 teaching/content 下找到任何 task 文件")

    lines = [
        "% -*- coding: utf-8 -*-",
        "% student_solution.tex — 学生参考解答版（自动装配各题及各小问 solution 环境）",
        "% 由 competition-courseware 流水线脚本自动生成与维护",
        "\\def\\StudentSolutionVersion{}",
        "\\input{header.tex}",
        "",
        "\\begin{document}",
        "\\begin{center}",
        "  \\vspace*{-1ex}",
        "  {\\huge \\textbf{\\ifdefined\\pdViewTitle\\pdViewTitle\\else\\pdMainTitle\\fi\\quad 参考解答}}",
        "  \\vspace*{-0.5ex}",
        "\\end{center}",
        "\\raggedcolumns",
        "\\pdBeginColumns",
        "",
    ]

    for i, t in enumerate(tasks):
        rel_path = f"teaching/content/{t}"
        lines.append(f"\\input{{{rel_path}}}")
        if i < len(tasks) - 1:
            lines.append(r"\pdColumnBreak")
        lines.append("")

    lines.append(r"\pdEndColumns")
    lines.append("\\input{footer.tex}")
    lines.append("")

    content = "\n".join(lines)
    out_path = os.path.join(project_root, output_filename)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[gen_student_solution] 成功装配解答源文件: {out_path} (共 {len(tasks)} 题)")
    return out_path


def compile_tex(project_root: str, tex_filename: str = "student_solution.tex", timeout: int = 180):
    """调用 XeLaTeX 编译生成 PDF，并自动同步至 release/ 交付目录。"""
    tex_path = os.path.join(project_root, tex_filename)
    if not os.path.isfile(tex_path):
        generate_student_solution_tex(project_root, tex_filename)

    cmd = ["xelatex", "-halt-on-error", "-interaction=nonstopmode", tex_filename]
    print(f"[gen_student_solution] 正在执行 XeLaTeX 编译 (工作目录: {project_root})...")

    # 第一遍编译
    p1 = subprocess.run(
        cmd,
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    if p1.returncode != 0:
        print("[gen_student_solution] 编译第一遍失败:")
        print(p1.stdout[-1500:] if p1.stdout else p1.stderr[-1500:])
        return False

    # 第二遍编译（稳定引用与页码）
    p2 = subprocess.run(
        cmd,
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        text=True,
        encoding="utf-8",
        errors="replace"
    )
    if p2.returncode != 0:
        print("[gen_student_solution] 编译第二遍失败:")
        print(p2.stdout[-1500:] if p2.stdout else p2.stderr[-1500:])
        return False

    base = os.path.splitext(tex_filename)[0]
    pdf_path = os.path.join(project_root, f"{base}.pdf")
    if not os.path.isfile(pdf_path):
        print(f"[gen_student_solution] 错误: 未找到目标产物 {pdf_path}")
        return False

    pdf_size = os.path.getsize(pdf_path)
    print(f"[gen_student_solution] 编译成功！产物: {pdf_path} (大小: {pdf_size:,} 字节)")

    # 契约 2.5.0 (F5): 记录 solution-build.json 摘要绑定（口径与 deliver.py 完全一致）
    try:
        mat_path = os.path.join(project_root, "teaching", "materials.json")
        materials = {}
        if os.path.isfile(mat_path):
            with open(mat_path, "r", encoding="utf-8-sig") as mf:
                materials = json.load(mf)
        try:
            import deliver
            _flat, views = deliver.load_config_full(os.path.join(project_root, "config.yaml")) if deliver.load_config_full else ({}, None)
            p_info = deliver.current_production_digest(project_root, materials, views)
        except Exception:
            p_info = contract.compute_production_digest(project_root, materials)

        sol_build_data = {
            "schema_version": contract.CONTRACT_VERSION,
            "kind": "solution_build_binding",
            "production_digest": p_info.get("digest"),
            "pdf_sha256": contract.sha256_file(pdf_path),
            "compiled_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")
        }
        build_dir = os.path.join(project_root, "teaching", "build")
        os.makedirs(build_dir, exist_ok=True)
        with open(os.path.join(build_dir, "solution-build.json"), "w", encoding="utf-8") as sbf:
            json.dump(sol_build_data, sbf, ensure_ascii=False, indent=2)
            sbf.write("\n")
        print(f"[gen_student_solution] 已写入构建绑定记录: teaching/build/solution-build.json (production_digest={sol_build_data['production_digest'][:16]}...)")
    except Exception as exc:
        print(f"[gen_student_solution] 警告: 写入 solution-build.json 失败: {exc}")

    # 若存在 release 目录，则同步交付
    rel_dir = os.path.join(project_root, "release")
    if os.path.isdir(rel_dir):
        dest_pdf = os.path.join(rel_dir, f"{base}.pdf")
        shutil.copy2(pdf_path, dest_pdf)
        dest_tex = os.path.join(rel_dir, tex_filename)
        shutil.copy2(tex_path, dest_tex)
        print(f"[gen_student_solution] 已同步至交付目录: {dest_pdf}")

    return True


def main():
    parser = argparse.ArgumentParser(description="生成并编译学生参考解答学案 (student_solution.tex & pdf)")
    parser.add_argument("project_root", nargs="?", default=".", help="流水线项目根目录（默认为当前目录）")
    parser.add_argument("--output", default="student_solution.tex", help="输出的主 TeX 文件名（默认 student_solution.tex）")
    parser.add_argument("--compile", action="store_true", help="是否立即调用 XeLaTeX 编译生成 PDF")
    parser.add_argument("--timeout", type=int, default=180, help="XeLaTeX 编译超时时间（秒）")
    args = parser.parse_args()

    project_root = os.path.abspath(args.project_root)
    try:
        generate_student_solution_tex(project_root, args.output)
    except UnsafeQidError as exc:
        # G5.1：非法 qid / 路径越界 —— fail-closed，退出码 2
        print(f"[gen_student_solution] 拒绝执行: {exc}", file=sys.stderr)
        sys.exit(2)

    if args.compile:
        success = compile_tex(project_root, args.output, args.timeout)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
