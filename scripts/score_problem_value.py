#!/usr/bin/env python3
# -*- coding: utf-8 -*-
TOOL_VERSION = "2.0"
r"""
score_problem_value.py — 题目价值/疑似重复提示（初筛辅助，永不阻断）

**定位**：本脚本只是 R1 低成本初筛的辅助信号，供主 agent 与子初筛
agent 参考。它**不能代替教研选题**：不输出入选/排除结论，不计算总分，
输出至多为 severity=review 的"疑似重复/低增量"提示，是否合并、替换或
排除由主 agent 按 workflow（references/workflow.md R1）决定。

对题库中的题对做轻量相似度筛查，提示可能的重复或低增量题目：

  1. 数字规整（连续数字替换为 #）后题面完全相同；
  2. 去空白后的字符 8-gram Jaccard 相似度 >= 阈值（默认 0.6）；
  3. tools 集合与 structure_signals 完全相同（同工具同结构信号，
     训练增量存疑）。

任一条件命中即输出一条 severity=review 的提示（status=review），
永不因疑似重复判 fail，退出码恒为 0（工作目录不存在除外）。

输入布局（自动检测，可用参数覆盖）：

  * 新版（skills-c-v2 工程）：``research/problems/<problem_id>/``，
    每个子目录视为一道题，目录内 *.md/*.txt 文本合并后参与比对；
  * 旧版兼容：``problem-source/`` 下每个文件视为一道题；
  * 若存在题档 pipe-table（默认 ``maps/problem-dossiers.md``，可用
    --dossier 指定），优先按题档行取 problem_id/sources/tools/
    structure_signals。

退出码: 0=检查完成（pass 或 review）, 2=缺少依赖或输入。
用法:
  python score_problem_value.py [工作目录] [--json PATH]
       [--threshold 0.6]
       [--problems-dir research/problems]   # 显式指定题目目录（新版）
       [--problems-dir problem-source]      # 兼容旧版布局
       [--dossier maps/problem-dossiers.md] # 兼容旧版题档
"""
import argparse
import os
import re
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_DOSSIER = os.path.join("maps", "problem-dossiers.md")
NEW_PROBLEMS_DIR = os.path.join("research", "problems")
OLD_PROBLEMS_DIR = "problem-source"
DEFAULT_THRESHOLD = 0.6
NGRAM = 8
TEXT_EXT = (".md", ".txt", ".tex")
SEP_RE = re.compile(r"^:?-{2,}:?$")
DIGITS_RE = re.compile(r"\d+")
WS_RE = re.compile(r"\s+")


def parse_pipe_table(path):
    """解析首个 pipe table，返回 (表头, [{列名: 值}])；无表返回 (None, [])。"""
    header = None
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if not s.startswith("|"):
                if header is not None and rows:
                    break
                continue
            cells = [c.strip() for c in s.strip().strip("|").split("|")]
            if cells and all(SEP_RE.match(c) for c in cells if c):
                continue
            if header is None:
                header = cells
                continue
            while len(cells) < len(header):
                cells.append("")
            rows.append(dict(zip(header, cells)))
    return header, rows


def digit_normalize(text):
    """数字规整：连续数字替换为 #，压缩一切空白。"""
    return WS_RE.sub("", DIGITS_RE.sub("#", text))


def char_ngrams(text, n=NGRAM):
    t = WS_RE.sub("", text)
    if len(t) < n:
        return set()
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / float(len(a) + len(b) - inter)


def tool_set(tools):
    return frozenset(t.strip() for t in tools.split(";") if t.strip())


def rel_slashes(path, start):
    """项目相对路径，统一正斜杠（与 scripts/contract.py 的路径约定一致）。"""
    return os.path.relpath(path, start).replace(os.sep, "/")


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def load_problems_from_dir(pdir):
    """目录布局：子目录=一道题（合并其中文本文件），散文件=一道题。"""
    problems = []
    for name in sorted(os.listdir(pdir)):
        p = os.path.join(pdir, name)
        if os.path.isdir(p):
            parts = []
            for root, _dirs, files in os.walk(p):
                for fn in sorted(files):
                    if fn.lower().endswith(TEXT_EXT):
                        parts.append(read_text(os.path.join(root, fn)))
            if parts:
                problems.append({"pid": name, "text": "\n".join(parts),
                                 "tools": None, "signals": None})
        elif os.path.isfile(p) and name.lower().endswith(TEXT_EXT):
            problems.append({"pid": name, "text": read_text(p),
                             "tools": None, "signals": None})
    return problems


def detect_problems_dir(workdir, explicit):
    """返回实际使用的题目目录（绝对路径）或 None。"""
    if explicit:
        p = explicit if os.path.isabs(explicit) else os.path.join(workdir,
                                                                  explicit)
        return p if os.path.isdir(p) else None
    for cand in (NEW_PROBLEMS_DIR, OLD_PROBLEMS_DIR):
        p = os.path.join(workdir, cand)
        if os.path.isdir(p):
            return p
    return None


def score(workdir, threshold=DEFAULT_THRESHOLD, problems_dir=None,
          dossier=None):
    """返回 (reviews, evidence)。疑似项永不升级为失败。"""
    reviews = []
    evidence = {"problems": 0, "pairs": 0, "threshold": threshold,
                "problems_dir": None, "dossier": None}

    dossier_path = None
    if dossier:
        cand = dossier if os.path.isabs(dossier) else os.path.join(workdir,
                                                                  dossier)
        if os.path.exists(cand):
            dossier_path = cand
    else:
        cand = os.path.join(workdir, DEFAULT_DOSSIER)
        if os.path.exists(cand):
            dossier_path = cand

    pdir = detect_problems_dir(workdir, problems_dir)
    if dossier_path is None and pdir is None:
        return reviews, evidence

    problems = []  # [{pid, text, tools, signals}]
    if dossier_path is not None:
        evidence["dossier"] = rel_slashes(dossier_path, workdir)
        _header, rows = parse_pipe_table(dossier_path)
        for row in rows:
            pid = row.get("problem_id", "")
            if not pid:
                continue
            parts = []
            for src in [s.strip()
                        for s in row.get("sources", "").split(";")
                        if s.strip()]:
                p = src if os.path.isabs(src) else os.path.join(workdir, src)
                if os.path.exists(p):
                    parts.append(read_text(p))
            problems.append({
                "pid": pid,
                "text": "\n".join(parts),
                "tools": tool_set(row.get("tools", "")),
                "signals": WS_RE.sub(" ",
                                     row.get("structure_signals", "")).strip(),
            })
    else:
        evidence["problems_dir"] = rel_slashes(pdir, workdir)
        problems = load_problems_from_dir(pdir)
    evidence["problems"] = len(problems)

    for p in problems:
        p["digit_norm"] = digit_normalize(p["text"])
        p["ngrams"] = char_ngrams(p["text"])

    for i in range(len(problems)):
        for j in range(i + 1, len(problems)):
            a, b = problems[i], problems[j]
            evidence["pairs"] += 1
            reasons = []
            if a["digit_norm"] and a["digit_norm"] == b["digit_norm"]:
                reasons.append("数字规整后题面完全相同")
            sim = jaccard(a["ngrams"], b["ngrams"])
            if a["ngrams"] and b["ngrams"] and sim >= threshold:
                reasons.append("字符 %d-gram Jaccard=%.2f（>= %.2f）"
                               % (NGRAM, sim, threshold))
            if a["tools"] is not None and b["tools"] is not None \
                    and a["tools"] and a["tools"] == b["tools"] \
                    and a["signals"] == b["signals"]:
                reasons.append("tools 与 structure_signals 完全相同")
            if reasons:
                reviews.append("题 “%s” 与 “%s” 疑似重复/低增量：%s；"
                               "请主 agent 结合课程目标人工判断是否合并、"
                               "替换或排除（本提示不构成排除结论）"
                               % (a["pid"], b["pid"], "；".join(reasons)))
    return reviews, evidence


def main():
    ap = argparse.ArgumentParser(
        description="题目价值/疑似重复提示（初筛辅助，永不阻断）")
    ap.add_argument("workdir", nargs="?", default=".")
    ap.add_argument("--json", metavar="PATH", help="写入结构化检查结果")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                    help="8-gram Jaccard 提示阈值（默认 %.1f）"
                    % DEFAULT_THRESHOLD)
    ap.add_argument("--problems-dir", metavar="DIR", default=None,
                    help="题目目录（相对 workdir 或绝对路径）。缺省自动检测 "
                         "research/problems/（新版），回退 problem-source/"
                         "（旧版兼容）")
    ap.add_argument("--dossier", metavar="PATH", default=None,
                    help="题档 pipe-table 路径（旧版兼容参数）；缺省自动检测 "
                         "maps/problem-dossiers.md")
    args = ap.parse_args()
    workdir = os.path.abspath(args.workdir)
    if not os.path.isdir(workdir):
        print("[score_problem_value] 工作目录不存在: %s" % workdir)
        return 2
    reviews, evidence = score(workdir, args.threshold,
                              problems_dir=args.problems_dir,
                              dossier=args.dossier)

    status = "review" if reviews else "pass"
    if args.json:
        import json
        report = {"tool": "score_problem_value.py",
                  "tool_version": TOOL_VERSION,
                  "scope": {"workdir": workdir},
                  "status": status,
                  "issues": [{"severity": "review", "message": r}
                             for r in reviews],
                  "evidence": evidence}
        _report_dir = os.path.dirname(os.path.abspath(args.json))
        if _report_dir:
            os.makedirs(_report_dir, exist_ok=True)
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print("[score_problem_value] 结构化报告已写入 %s" % args.json)

    for r in reviews:
        print("[score_problem_value] [待复核] " + r)
    if reviews:
        print("[score_problem_value] 完成：%d 条疑似重复/低增量提示"
              "（status=review，初筛辅助信号，不阻断、不代替选题）。"
              % len(reviews))
    elif evidence["problems"]:
        print("[score_problem_value] 通过：%d 道题两两比对（%d 对）未见"
              "疑似重复。" % (evidence["problems"], evidence["pairs"]))
    else:
        print("[score_problem_value] 通过：无题目目录与题档输入，跳过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
