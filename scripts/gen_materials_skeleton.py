#!/usr/bin/env python3
# -*- coding: utf-8 -*-
TOOL_VERSION = "2.4.0"
r"""
gen_materials_skeleton.py — 自动化骨架生成与统一内容源装配组件（问题 6 核心套件）

打通教研输出（brief.json / freeze.json / architect-plan.json）向教学执行端（materials.json /
切片 sec*.tex / 双轨内容 content/*.tex / classroom-plan.md）的无缝流转，消除手工介入。

功能：
  1. 读取 brief.json 和 freeze.json，获取入选题目（selected_problem_ids）与版本绑定；
  2. 自动生成符合 schemas/materials.schema.json 的 teaching/materials.json（含 blocks 与 tasks）；
  3. 自动生成规范内容文件：
     - teaching/content/task-<qid>.tex（学生任务/题面，纯净无解答泄露，L0）
     - teaching/content/blk-<qid>-proof.tex（教师完整证明/解答，包裹在 \begin{solution} 中，L2）
  4. 自动生成/同步符合 check_numbering.py 规范的切片文件 sec1.tex ~ secN.tex（带 %% qid、%% block 和 \ViewBlock）；
  5. 自动生成 teaching/classroom-plan.md 课堂互动节奏文件；
  6. 自动对齐 main.tex 中的切片装配清单。

**默认保护人工产物**：不带 `--overwrite` 时，只创建缺失的文件；已存在的
materials.json / sec*.tex / main.tex / content/*.tex 一律保留（打印保留清单），
绝不静默覆盖用户或 Agent 已编辑的内容。确需重新生成时才传 `--overwrite`，
并由调用方负责先备份、后记录覆盖清单（见 workflow/nodes/teaching_nodes.py）。

用法：
  python scripts/gen_materials_skeleton.py <project_root> [--overwrite] [--dry-run]
"""

import argparse
import json
import os
import re
import shutil
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)
import contract  # noqa: E402

BRIEF_REL = "research/brief.json"
FREEZE_REL = "research/freeze.json"
ARCHITECT_REL = "teaching/architect-plan.json"
MATERIALS_REL = "teaching/materials.json"
CLASSROOM_PLAN_REL = "teaching/classroom-plan.md"
MAIN_TEX_REL = "main.tex"
# 自检报告：level 规范化拒绝事件落盘（仅供审计与 repair 流程消费；
# 不在 broker/deliver 的固定门禁清单内，不参与放行判定）
GEN_REPORT_REL = "teaching/checks/gen_materials_skeleton.json"


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(path, data, dry_run=False):
    if dry_run:
        print("  [dry-run] 将写入 %s" % path)
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    print("  [gen_materials] 已写入 %s" % path)


def write_text(path, text, dry_run=False):
    if dry_run:
        print("  [dry-run] 将写入 %s" % path)
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print("  [gen_materials] 已写入 %s" % path)


MATH_COMMANDS = {
    r'\binom', r'\frac', r'\sum', r'\prod', r'\equiv', r'\pmod', r'\le', r'\ge',
    r'\neq', r'\times', r'\cdot', r'\implies', r'\iff', r'\to', r'\left', r'\right',
    r'\underbrace', r'\overbrace', r'\sqrt', r'\forall', r'\exists', r'\in',
    r'\subset', r'\cup', r'\cap', r'\mathbb', r'\mathcal', r'\operatorname',
    r'\dots', r'\cdots', r'\mid', r'\nmid', r'\quad', r'\qquad', r'\sim', r'\approx',
    r'\lfloor', r'\rfloor', r'\lceil', r'\rceil', r'\blacksquare'
}


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


def clean_flamboyant_words(text: str) -> str:
    """清理浮夸用词，回归严谨简明的数学竞赛教研文风。"""
    if not text:
        return ""
    for old, new in FLAMBOYANT_REPLACEMENTS:
        text = text.replace(old, new)
    return text


def is_naked_math_line(s):
    """检测单行是否为未包裹 $ 或 $$ 的独立数学公式行。"""
    if not s:
        return False
    if s.startswith(('%', '#', r'\begin', r'\end', r'\par', r'\noindent', r'\[', r'\item')):
        return False
    if (s.startswith('$') and s.endswith('$') and s.count('$') == 2) or (s.startswith(r'\[') and s.endswith(r'\]')):
        return False
    if re.match(r'^[-*+]\s+', s) or re.match(r'^\d+\.\s+', s):
        return False
    stripped = re.sub(r'\\text\{[^}]*\}', '', s)
    if re.search(r'[\u4e00-\u9fff]', stripped):
        return False
    has_math_cmd = any(cmd in stripped for cmd in MATH_COMMANDS)
    has_sub_sup = bool(re.search(r'[_^]', stripped))
    has_relation = bool(re.search(r'[=<>]', stripped)) and bool(re.search(r'[0-9a-zA-Z\\]', stripped))
    return has_math_cmd or has_sub_sup or has_relation


def markdown_to_tex(text):
    """将 Markdown 内容转换为安全、合法的 LaTeX 代码。
    支持自动识别裸露公式行、多行/单行 $$ 块、Markdown 粗体/行内代码、
    文本模式下下划线与特殊字符安全转义、行间公式排版疏松化、以及未闭合数学环境自动闭合。
    """
    if not text:
        return ""
    text = clean_flamboyant_words(text)
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

        # 忽略 Markdown 水平分割线（避免在 TeX 中产出杂乱破折号）
        if re.match(r"^[-*_]{3,}$", s):
            if in_display_math:
                lines.append(r"\]")
                in_display_math = False
            lines.append(r"\par\vspace{1ex}")
            continue

        # 忽略代码块栅栏
        if s.startswith("```"):
            continue

        # 过滤顶部题目元标记
        if re.match(r"^#{1,6}\s+题目\s*\(.*?\)", s):
            continue

        # 去除行首无序列表符号（如 * 或 -），转化为自然段落排版
        if re.match(r"^[-*+]\s+", s):
            s = re.sub(r"^[-*+]\s+", "", s).strip()

        # 处理标题行：# 标题 -> \par\noindent\textbf{标题}\par
        m_head = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m_head:
            if in_display_math:
                lines.append(r"\]")
                in_display_math = False
            title = m_head.group(2).strip()
            title = title.replace("#", r"\#")
            title = re.sub(r"\*\*([^*]+)\*\*", r"\\textbf{\1}", title)
            title = re.sub(r"`([^`]+)`", r"\\texttt{\1}", title)
            chars = []
            in_m = False
            for c in title:
                if c == "$":
                    in_m = not in_m
                    chars.append(c)
                elif c == "_" and not in_m:
                    chars.append(r"\_")
                elif c == "&" and not in_m:
                    chars.append(r"\&")
                elif c == "%" and not in_m:
                    chars.append(r"\%")
                else:
                    chars.append(c)
            lines.append(r"\par\noindent\textbf{%s}\par\medskip" % "".join(chars))
            continue
        # 单行封闭的 $$ ... $$ (两端都有 $$)
        if s.startswith("$$") and s.endswith("$$") and len(s) > 2:
            inner = s[2:-2].strip()
            lines.append(r"\[ %s \]" % inner)
            continue

        # 多行 $$ 开启：以 $$ 开头但结尾不是 $$
        if s.startswith("$$") and not s.endswith("$$"):
            if in_display_math:
                lines.append(r"\]")
            lines.append(r"\[")
            in_display_math = True
            rest = s[2:].strip()
            if rest:
                lines.append(rest)
            continue

        # 纯独立 $$ 切换
        if s == "$$":
            if in_display_math:
                lines.append(r"\]")
                in_display_math = False
            else:
                lines.append(r"\[")
                in_display_math = True
            continue

        # 当前处于多行 display math 块内
        if in_display_math:
            if s.endswith("$$"):
                rest = s[:-2].strip()
                if rest:
                    lines.append(rest)
                lines.append(r"\]")
                in_display_math = False
            elif s == "$$":
                lines.append(r"\]")
                in_display_math = False
            else:
                lines.append(line)
            continue
        # 单行纯独立公式 $ ... $ 自动提升为行间公式 \[ ... \]，使排版疏松
        if s.startswith("$") and s.endswith("$") and s.count("$") == 2 and len(s) > 2:
            inner = s[1:-1].strip()
            lines.append(r"\[ %s \]" % inner)
            continue
        # 冒号后接的长推导公式自动分行并提升为行间公式
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
        # 自动补全未闭合的数学定界符 $
        if in_m:
            new_chars.append("$")
        lines.append("".join(new_chars))
    if in_display_math:
        lines.append(r"\]")
    return "\n".join(lines)


def extract_problem_stem(project_root, qid):
    """从源文件或 probe.json 提取题目题面。"""
    stem = contract.extract_problem_stem(project_root, qid)
    if stem:
        return markdown_to_tex(stem)
    probe_cand = os.path.join(project_root, "research", "problems", "%s" % qid, "probe.json")
    if os.path.isfile(probe_cand):
        try:
            with open(probe_cand, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            stem = data.get("problem_stem") or data.get("statement") or ""
            if stem:
                return markdown_to_tex(stem.strip())
        except Exception:
            pass
    return "设正整数 $n$ 满足相关条件，证明对应组合数或同余式成立。"


def extract_solution_body(project_root, qid):
    """尝试从 research/problems/<qid>/solution.md 或源题文件提取严谨解答正文。"""
    sol_md = os.path.join(project_root, "research", "problems", "%s" % qid, "solution.md")
    if os.path.isfile(sol_md):
        try:
            with open(sol_md, "r", encoding="utf-8-sig", errors="replace") as f:
                content = f.read().strip()
                # 截断元对话与追问说明（如 "这是一个费解的证明...请为我解释"）
                for cut_marker in ("这是一个费解的证明", "请为我解释", "如何想到这个证明"):
                    if cut_marker in content:
                        content = content.split(cut_marker)[0].strip()
                # 若含有已包裹好的 solution 环境，优先提取首个 solution 块内正文
                m_sol = re.search(r"\\begin\{solution\}([\s\S]*?)\\end\{solution\}", content)
                if m_sol:
                    inner_sol = m_sol.group(1).strip()
                    if inner_sol:
                        return markdown_to_tex(inner_sol)
                # 剔除顶部的题目复述与元标题
                content = re.sub(r"^##\s+题目.*?\n+", "", content, flags=re.MULTILINE)
                content = re.sub(r"^【(?:预|例)\d+】.*?\n+", "", content, flags=re.MULTILINE)
                odd_count = sum(1 for line in content.splitlines() if line.count('$') % 2 != 0)
                # 若 solution.md 数学语法健康（无大面积环境变量破坏），优先使用
                if odd_count <= 2 and content:
                    return markdown_to_tex(content)
        except Exception:
            pass

    # 回退到源题目文件 sources/problems/<qid>.md 或 <qid>.md 中的权威详细解答
    candidates = [
        os.path.join(project_root, "%s.md" % qid),
        os.path.join(project_root, "sources", "problems", "%s.md" % qid),
    ]
    for cand in candidates:
        if os.path.isfile(cand):
            try:
                with open(cand, "r", encoding="utf-8-sig", errors="replace") as f:
                    txt = f.read()
                m = re.search(r"####\s+(?:详细严谨解答|详细解答|严谨解答|解答)([\s\S]*)$", txt)
                if m and m.group(1).strip():
                    return markdown_to_tex(m.group(1).strip())
            except Exception:
                pass

    sol_json = os.path.join(project_root, "research", "problems", "%s" % qid, "solution.json")
    if os.path.isfile(sol_json):
        try:
            with open(sol_json, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            steps = data.get("solution_steps") or data.get("key_deductions") or []
            if steps:
                return markdown_to_tex("\n\n".join(str(s) for s in steps))
        except Exception:
            pass

    return "由 Kummer 定理与相关引理推导可知，本题结论成立。完整证明细节见教研卷。"


def staging_submitted_runs(root, task_id):
    """列出 staging 中该 task 已存在 broker 提交凭证的 run_id（fail-closed）。

    凭证判定（任一命中即视为已经过 broker submit，禁止离线改写其规范副本）：
      - staging/<run>/receipts/<task_id>.json 存在（broker 已签发签名 receipt）；
      - run.json 中该任务 output_digest 非空或 state 已越过 issued；
      - run.json 存在但不可解析（解析异常按已提交处理，绝不放行改写）。
    """
    staging_dir = os.path.join(root, "staging")
    hits = []
    if not os.path.isdir(staging_dir):
        return hits
    try:
        run_names = sorted(os.listdir(staging_dir))
    except OSError:
        return hits
    for run_name in run_names:
        run_dir = os.path.join(staging_dir, run_name)
        if not os.path.isdir(run_dir):
            continue
        receipt_path = os.path.join(run_dir, "receipts", "%s.json" % task_id)
        if os.path.isfile(receipt_path):
            hits.append(run_name)
            continue
        run_json_path = os.path.join(run_dir, "run.json")
        if not os.path.isfile(run_json_path):
            continue
        try:
            with open(run_json_path, "r", encoding="utf-8-sig") as f:
                run_data = json.load(f)
        except Exception:
            hits.append(run_name)  # fail-closed：凭证不可读按已提交处理
            continue
        entry = (run_data.get("tasks") or {}).get(task_id) or {}
        if entry.get("output_digest") or entry.get("state") not in (
                None, "pending", "issued"):
            hits.append(run_name)
    return hits


def build_materials_skeleton(project_root, overwrite=False, dry_run=False):
    root = os.path.abspath(project_root)
    brief_path = os.path.join(root, BRIEF_REL.replace("/", os.sep))
    freeze_path = os.path.join(root, FREEZE_REL.replace("/", os.sep))

    if not os.path.isfile(brief_path):
        print("[gen_materials] 错误: brief 文件不存在: %s" % brief_path, file=sys.stderr)
        return 1

    brief = load_json(brief_path)
    freeze = load_json(freeze_path) if os.path.isfile(freeze_path) else {}

    brief_id = brief.get("brief_id", "brief_01")
    revision = freeze.get("revision") or brief.get("revision", 1)

    selected_ids = list(brief.get("selection", {}).get("selected_problem_ids", []))
    if not selected_ids:
        print("[gen_materials] 警告: brief 中 selected_problem_ids 为空，尝试扫描 research/problems 目录")
        probs_dir = os.path.join(root, "research", "problems")
        if os.path.isdir(probs_dir):
            selected_ids = sorted([d for d in os.listdir(probs_dir) if os.path.isdir(os.path.join(probs_dir, d))])

    if not selected_ids:
        selected_ids = ["1"]

    print("[gen_materials] 目标题目列表: %s" % selected_ids)

    blocks = []
    tasks = []
    gen_issues = []  # 防篡改闭环：level 规范化拒绝事件（写入自检报告）

    content_dir = os.path.join(root, "teaching", "content")
    os.makedirs(content_dir, exist_ok=True)

    # 默认保留已存在的 materials.json（人工/Agent 编辑过的统一内容源）
    mat_path = os.path.join(root, MATERIALS_REL.replace("/", os.sep))
    existing_materials = None
    if os.path.isfile(mat_path) and not overwrite:
        try:
            existing_materials = load_json(mat_path)
            print("  [gen_materials] %s 已存在，保留（未覆盖；确需重新生成请加 "
                  "--overwrite）" % MATERIALS_REL)
        except (OSError, json.JSONDecodeError) as exc:
            print("  [gen_materials] 警告: %s 已存在但不是合法 JSON（%s），"
                  "不覆盖也不复用" % (MATERIALS_REL, exc))

    for qid in selected_ids:
        task_id = "task-%s" % qid
        blk_task_id = "blk-task-%s" % qid
        blk_proof_id = "blk-%s-proof" % qid

        # 检查可用的 source_refs
        src_candidates = [
            "sources/problems/%s.md" % qid,
            "research/problems/%s/probe.json" % qid,
            "research/problems/%s/solution.json" % qid,
            "research/problems/%s/solution.md" % qid,
        ]
        valid_srcs = [s for s in src_candidates if os.path.isfile(os.path.join(root, s.replace("/", os.sep)))]
        if not valid_srcs:
            valid_srcs = ["research/brief.json"]

        proof_src = "research/problems/%s/solution.md" % qid
        if not os.path.isfile(os.path.join(root, proof_src.replace("/", os.sep))):
            proof_src = "research/brief.json"

        # 1. 规范内容路径
        task_content_rel = "teaching/content/task-%s.tex" % qid
        proof_content_rel = "teaching/content/blk-%s-proof.tex" % qid

        task_content_abs = os.path.join(root, task_content_rel.replace("/", os.sep))
        proof_content_abs = os.path.join(root, proof_content_rel.replace("/", os.sep))

        # 2. 生成/同步 task 内容文件
        gen_task_tex = os.path.join(root, "teaching", "generated", "task-%s.tex" % qid)
        if os.path.isfile(gen_task_tex) and (overwrite or not os.path.isfile(task_content_abs)):
            if not dry_run:
                shutil.copyfile(gen_task_tex, task_content_abs)
            print("  [gen_materials] 已同步 broker 渲染任务: %s -> %s" % (
                os.path.basename(gen_task_tex), task_content_rel
            ))
        elif os.path.isfile(gen_task_tex):
            print("  [gen_materials] %s 已存在，保留（broker 渲染件未覆盖；确需同步请加 "
                  "--overwrite）" % task_content_rel)
        elif overwrite or not os.path.isfile(task_content_abs):
            stem_text = extract_problem_stem(root, qid)
            # 题面纯净无 solution
            task_tex_content = (
                "%% task-%(qid)s.tex — 学生任务与题面（L0，初始发放）\n"
                "\\begin{problem}\n"
                "%(stem)s\n"
                "\\end{problem}\n\n"
                "\\begin{task}\n"
                "记录关键推导步骤，写出核心同余关系，并尝试完成独立证明。\n"
                "\\end{task}\n"
            ) % {"qid": qid, "stem": stem_text}
            write_text(task_content_abs, task_tex_content, dry_run)
        else:
            print("  [gen_materials] %s 已存在，保留" % task_content_rel)

        # 3. 生成 proof 内容文件（若不存在或 overwrite）
        if overwrite or not os.path.isfile(proof_content_abs):
            sol_body = extract_solution_body(root, qid)
            sol_body = re.sub(r"^\s*\\begin\{solution\}\s*", "", sol_body)
            sol_body = re.sub(r"\s*\\end\{solution\}\s*$", "", sol_body)

            # 智能格式微调：规范小问标号 (1) 解、(2) 证明 或 1. 2. 3. 步骤段落
            # 将步骤提升为清晰的逐小问解析标记
            sol_body = re.sub(r"(?:^|\n)\s*([1-9])\.\s+", r"\n\n\\par\\noindent\\textbf{(\1) 证明与推导：}\\par\\smallskip\n", sol_body)
            sol_body = re.sub(r"(\\textbf\{\s*\([1-9一二三四]\)\s*(?:解|证明)[：:]\s*\})", r"\n\\par\\medskip\n\1\n", sol_body)

            proof_tex_content = (
                "%% blk-%(qid)s-proof.tex — 教师专属证明与解析（L2，仅教师视图）\n"
                "\\begin{solution}\n"
                "%(sol)s\n"
                "\\end{solution}\n"
            ) % {"qid": qid, "sol": sol_body.strip()}
            write_text(proof_content_abs, proof_tex_content, dry_run)
        else:
            print("  [gen_materials] %s 已存在，保留" % proof_content_rel)

        # 4. 构建 materials.json 的 block 条目
        task_block = {
            "block_id": blk_task_id,
            "problem_id": str(qid),
            "task_id": task_id,
            "question_id": str(qid),
            "subquestion_ids": ["sq-%s-1" % qid],
            "source_refs": valid_srcs,
            "brief_revision": revision,
            "content_ref": task_content_rel,
            "level": "L0",
            "audience": ["student", "teacher"],
            "available_after": "initial",
            "delivery_channel": "in_main",
            "views": ["student_initial", "teacher"],
            "cognitive_role": "task",
            "forbidden_feature_refs": [],
            "block_type": "content"
        }

        proof_block = {
            "block_id": blk_proof_id,
            "problem_id": str(qid),
            "task_id": task_id,
            "question_id": str(qid),
            "subquestion_ids": ["sq-%s-1" % qid],
            "source_refs": [proof_src],
            "brief_revision": revision,
            "content_ref": proof_content_rel,
            "level": "L2",
            "audience": ["teacher"],
            "available_after": "initial",
            "delivery_channel": "teacher_only",
            "views": ["teacher"],
            "cognitive_role": "teacher_solution",
            "forbidden_feature_refs": [],
            "block_type": "content"
        }

        blocks.append(task_block)
        blocks.append(proof_block)

        # 5. 构建 materials.json 的 tasks 映射
        tasks.append({
            "task_id": task_id,
            "problem_id": str(qid),
            "block_ids": [blk_task_id, blk_proof_id],
            "views": ["student_initial", "teacher"]
        })

    # 6. 写入 teaching/materials.json（默认保留已有文件）
    materials_doc = {
        "schema_version": "2.4.0",
        "brief_id": brief_id,
        "brief_revision": revision,
        "blocks": blocks,
        "tasks": tasks
    }

    # 验证 schema 契约
    schema_errs = contract.validate(materials_doc, "materials")
    if schema_errs:
        print("[gen_materials] Schema 错误:", file=sys.stderr)
        for e in schema_errs:
            print("  %s [%s]: %s" % (e["path"], e["validator"], e["message"]), file=sys.stderr)
        return 1

    mat_path = os.path.join(root, MATERIALS_REL.replace("/", os.sep))
    if existing_materials is not None:
        # 保留人工/Agent 版本；切片装配以磁盘上的 materials.json 为准，
        # 避免“保留 materials 却按 brief 重写切片”造成双向不可追溯
        materials_doc = existing_materials
    else:
        write_json(mat_path, materials_doc, dry_run)

    # 6a. 确保 config.yaml 包含 views: 视图声明（防止 XeLaTeX 条件丢弃）
    cfg_path = os.path.join(root, "config.yaml")
    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8-sig") as f:
                cfg_text = f.read()
            if "views:" not in cfg_text:
                views_block = (
                    "\nviews:\n"
                    "  - id: student_initial\n"
                    "    entry: student\n"
                    "    audience: student\n"
                    "    channel: in_main\n"
                    "    teacher_flags: false\n"
                    "  - id: teacher\n"
                    "    entry: teacher\n"
                    "    audience: teacher\n"
                    "    channel: in_main\n"
                    "    teacher_flags: true\n"
                )
                if not dry_run:
                    with open(cfg_path, "a", encoding="utf-8") as f:
                        f.write(views_block)
                print("  [gen_materials] 已自动为 config.yaml 补充 views: 视图声明")
        except Exception as exc:
            print("  [gen_materials] 检查 config.yaml 异常: %s" % exc)

    # 6b. 规范化 teaching/generated/<qid>-student-task.json 文件名与 level: L0
    # 【防篡改闭环】level 规范化只允许作用于规范目录副本（teaching/generated/），
    # 且仅在该 task 尚未经 broker submit（staging 中无对应提交凭证）时；
    # 已提交任务的 level 修正必须走 repair 流程（broker 重新 issue_packet /
    # submit_task 并过门禁），本脚本绝不改写 staging/<run>/tasks/ 产物与
    # run.json 的 output_digest（历史版本曾在此重算摘要回写 run.json，
    # 制度化绕过 broker「提交后改动即拒收」，该逻辑已删除）。
    gen_dir = os.path.join(root, "teaching", "generated")
    if os.path.isdir(gen_dir):
        for qid in selected_ids:
            canonical_path = os.path.join(gen_dir, "%s-student-task.json" % qid)
            legacy_path = os.path.join(gen_dir, "task-%s-student-task.json" % qid)
            if not os.path.isfile(canonical_path) and os.path.isfile(legacy_path):
                if dry_run:
                    print("  [dry-run] 将规范化重命名 %s -> %s" % (legacy_path, canonical_path))
                else:
                    try:
                        os.replace(legacy_path, canonical_path)
                        print("  [gen_materials] 已自动规范化中间文件: %s -> %s" % (
                            os.path.basename(legacy_path), os.path.basename(canonical_path)
                        ))
                    except Exception as e:
                        print("  [gen_materials] 重命名失败: %s" % e)
            elif os.path.isfile(canonical_path) and os.path.isfile(legacy_path):
                if not dry_run:
                    try:
                        os.remove(legacy_path)
                    except Exception:
                        pass

            # 确保 student-task.json 严格遵循契约 2.4.0（学生初始任务必须为 L0）
            if os.path.isfile(canonical_path):
                try:
                    with open(canonical_path, "r", encoding="utf-8-sig") as f:
                        task_data = json.load(f)
                except Exception as e:
                    print("  [gen_materials] 校验 student-task.json 异常: %s" % e)
                    task_data = None
                if not isinstance(task_data, dict):
                    continue
                if task_data.get("level") == "L0":
                    continue
                task_id = "task-%s" % qid
                submitted_runs = staging_submitted_runs(root, task_id)
                if submitted_runs:
                    msg = (
                        "拒绝规范化 %s 的 level（%r -> L0）：staging 中已存在 "
                        "%s 的 broker 提交凭证（run: %s），离线改写会绕过 "
                        "broker 防篡改闭环；请走 repair 流程（重新 "
                        "issue_packet/submit_task 并过门禁）修正，本脚本不改写 "
                        "staging 与 run.json。" % (
                            os.path.basename(canonical_path),
                            task_data.get("level"), task_id,
                            ", ".join(submitted_runs))
                    )
                    print("  [gen_materials] %s" % msg, file=sys.stderr)
                    gen_issues.append({
                        "severity": "error",
                        "path": "teaching/generated/%s-student-task.json" % qid,
                        "task_id": task_id,
                        "message": msg,
                        "hint": "走 repair 流程：broker issue_packet -> worker "
                                "修复 -> submit_task -> run_gates -> promote",
                    })
                    continue
                task_data["level"] = "L0"
                try:
                    if not dry_run:
                        with open(canonical_path, "w", encoding="utf-8") as f:
                            json.dump(task_data, f, ensure_ascii=False, indent=2)
                    print("  [gen_materials] 已规范化 %s 级别为 L0" % os.path.basename(canonical_path))
                except Exception as e:
                    print("  [gen_materials] 规范化写入失败: %s" % e)

    # 7. 生成切片装配文件 sec1.tex ~ secN.tex（默认保留已有切片）
    # 块 ID 以磁盘上的 materials.json 为准：人工改过块 ID 时不能按 brief 重写切片
    registered_blocks = {
        (b.get("problem_id"), b.get("cognitive_role")): b
        for b in (materials_doc.get("blocks") or []) if isinstance(b, dict)
    }
    for idx, qid in enumerate(selected_ids, 1):
        sec_name = "sec%d.tex" % idx
        sec_path = os.path.join(root, sec_name)
        blk_task_id = "blk-task-%s" % qid
        blk_proof_id = "blk-%s-proof" % qid
        task_ref = "teaching/content/task-%s.tex" % qid
        proof_ref = "teaching/content/blk-%s-proof.tex" % qid

        task_meta = registered_blocks.get((str(qid), "task"))
        if task_meta and task_meta.get("block_id"):
            blk_task_id = task_meta["block_id"]
            blk_task_id = str(blk_task_id)
        if task_meta and task_meta.get("content_ref"):
            task_ref = str(task_meta["content_ref"])
        proof_meta = registered_blocks.get((str(qid), "teacher_solution"))
        if proof_meta and proof_meta.get("block_id"):
            blk_proof_id = str(proof_meta["block_id"])
        if proof_meta and proof_meta.get("content_ref"):
            proof_ref = str(proof_meta["content_ref"])

        sec_content = (
            "%% sec%(idx)d.tex — 题目 %(qid)s 双轨装配切片\n"
            "%% qid: %(qid)s\n\n"
            "%% block: %(blk_task)s\n"
            "\\ViewBlock{student_initial,teacher}{\\input{%(task_ref)s}}\n\n"
            "%% block: %(blk_proof)s\n"
            "\\ViewBlock{teacher}{\\input{%(proof_ref)s}}\n"
        ) % {
            "idx": idx,
            "qid": qid,
            "blk_task": blk_task_id,
            "task_ref": task_ref,
            "blk_proof": blk_proof_id,
            "proof_ref": proof_ref,
        }
        if os.path.isfile(sec_path) and not overwrite:
            print("  [gen_materials] %s 已存在，保留（未覆盖；确需重新生成请加 "
                  "--overwrite）" % sec_name)
            continue
        write_text(sec_path, sec_content, dry_run)

    # 8. 同步更新 main.tex（保证引入所有切片；默认不覆盖已有 main.tex）
    main_path = os.path.join(root, MAIN_TEX_REL)
    if os.path.isfile(main_path) and not overwrite:
        print("  [gen_materials] %s 已存在，保留（未覆盖；确需重新生成请加 "
              "--overwrite）" % MAIN_TEX_REL)
    else:
        # 排版规范：每题一栏均匀排布，各题切片之间插入 \pdColumnBreak
        sec_inputs = "\n\\pdColumnBreak\n".join(r"\input{sec%d.tex}" % (i + 1) for i in range(len(selected_ids)))
        main_tex_content = (
            "%% main.tex — 双轨学案主装配骨架（排版规范：每题一栏均匀排布）\n"
            "\\input{header.tex}\n\n"
            "\\begin{document}\n"
            "\\begin{center}\n"
            "  {\\Huge \\textbf{\\ifdefined\\pdViewTitle\\pdViewTitle\\else\\pdMainTitle\\fi}}\n"
            "\\end{center}\n"
            "\\raggedcolumns\n"
            "\\pdBeginColumns\n\n"
            "%(sec_inputs)s\n\n"
            "\\input{footer.tex}\n"
            "\\end{document}\n"
        ) % {"sec_inputs": sec_inputs}
        write_text(main_path, main_tex_content, dry_run)

    # 9. 生成 teaching/classroom-plan.md（若不存在）
    plan_md_path = os.path.join(root, CLASSROOM_PLAN_REL.replace("/", os.sep))
    if overwrite or not os.path.isfile(plan_md_path):
        classroom_plan_text = (
            "# 课堂双轨教学与互动节奏计划\n\n"
            "## 教学节奏设计\n"
            "- **阶段一：特例破冰与直观感知（5-8分钟）**\n"
            "  - 引导学生代入小素数（如 $p=2, 3$）观察组合数同余规律；\n"
            "  - 教师适时提示关注 $p$-进赋值与进位机制。\n\n"
            "- **阶段二：独立探究与停笔留白（8-12分钟）**\n"
            "  - 课堂进入停笔静思时间，学生在学案作答留白区书写核心论证；\n"
            "  - 教师巡视观察学生典型尝试与卡点。\n\n"
            "- **阶段三：研讨辨析与关键推导（10-15分钟）**\n"
            "  - 聚焦关键引理与算子，完成严谨证明；\n"
            "  - 辨析易错边界（如等号成立条件、分母整除性）。\n\n"
            "- **阶段四：变式巩固与复盘归纳（5-8分钟）**\n"
            "  - 迁移至同类型竞赛题，总结同余式处理通用战术。\n"
        )
        write_text(plan_md_path, classroom_plan_text, dry_run)

    # 10. 自检报告：level 规范化拒绝事件落盘（审计用；fail-closed——
    # 有拒绝事件时本脚本以非零退出，提示调用方走 repair 流程）
    if not dry_run:
        try:
            report_abs = os.path.join(root, GEN_REPORT_REL.replace("/", os.sep))
            contract.write_check_report(
                report_abs, "fail" if gen_issues else "pass",
                "gen_materials_skeleton", gen_issues)
        except Exception as exc:
            print("[gen_materials] 写入自检报告失败: %s" % exc, file=sys.stderr)

    print("[gen_materials] 骨架生成完毕！共配置 %d 道题目，%d 个内容块。" % (len(selected_ids), len(blocks)))
    if gen_issues:
        print("[gen_materials] 存在 %d 项已提交任务的规范化拒绝（详见 %s），"
              "请走 repair 流程" % (len(gen_issues), GEN_REPORT_REL),
              file=sys.stderr)
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser(description="自动生成 teaching/materials.json 与切片骨架（问题 6 套件）")
    ap.add_argument("project_root", help="项目工作区根目录")
    ap.add_argument("--overwrite", action="store_true", help="强制覆盖已存在的内容文件与 materials.json")
    ap.add_argument("--dry-run", action="store_true", help="只预览将要生成的文件，不实际写入")
    args = ap.parse_args()

    return build_materials_skeleton(args.project_root, overwrite=args.overwrite, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
