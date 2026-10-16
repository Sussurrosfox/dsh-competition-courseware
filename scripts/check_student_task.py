#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""check_student_task.py — 学生任务质量统一门禁（契约 2.4.0，必经）。

统一入口（审计阶段 4）：合并旧 check_prompt_shape / check_guidance_leak /
check_small_outputs 的文件发现与状态语义；旧脚本已删除，不再各自维护
不同的发现逻辑与放行语义。

硬失败规则（status=fail/error，默认失败，无 PASS 豁免）：
  F1  brief 缺失/不可读/不合法、expected 清单无法计算 → error（绝不 PASS）；
  F2  题目覆盖：冻结 brief 每个入选题必须恰好有一个规范文件
      teaching/generated/<qid>-student-task.json；缺题、额外题、备用名、
      无关文件均失败；
  F3  schema：每个 JSON 须通过 student-task.schema.json 校验
      （G6.3：schema 已屏蔽 subquestions[].solution，门禁另对携带
      solution 字段的产物显式硬失败）；
  F4  字段白名单：输出字段必须是 packet.output_fields_white_list
      （或契约 STUDENT_TASK_FIELDS）的子集；
  F5  来源 digest：source_refs 必须存在；有 packet/run 时须在白名单内且
      摘要与 packet.input_digests 一致；
  F6  渲染映射：receipt 验签、run/packet 绑定、渲染产物摘要一致
      （复用 check_teaching 的绑定核对；只有注释的手写 .tex 拒绝）；
  F7  禁止环境与答案宏：渲染后的学生 .tex 不得出现 solution/proof 环境、
      tcolorbox/mdframed/\fillin/答案标记等；
  F8  学生初始视图纯净：level 必须 L0；forbidden_feature_refs 不得出现在
      学生可见文本；source_refs 不得引用教师专属/解答路径；
  F9  黑话标签：归一化（剔除 Unicode Cf 零宽字符、TeX 间距控制符、
      NFKC 全角→半角、大小写折叠、空白、TeX token）后，brief 声明的
      protected_strategy_tokens 与内置黑名单不得出现在 hint /
      expected_evidence / prompt+结论同现；并在 prompt+hint+
      expected_evidence 拼接视图上检测跨字段/跨小问拆分（G6.2）；
  F10 结构：小问数 3--6、subquestion_id 题内唯一、题干非空、
      answer_space_mm 合法、reveal/hint_level 合法；
  F10b 目标问数：packet 携带 solution_skeleton 时，小问数须精确等于
      skeleton.target_subquestion_count（由 proof_obligations 推导）；
  F11 版面字符预算：动态阈值——硬上限 hard=max(1000, stem_len+80*t+160)，
      告警线 warn=max(850, stem_len+80*t+40)，渲染可见文本超 hard 硬失败、
      超 warn 记 review（t 为 packet solution_skeleton 的
      target_subquestion_count，无骨架时 t=3）；
  F13 义务覆盖：packet 携带 solution_skeleton 时，每个小问必填
      obligation_refs；全体小问并集须精确等于骨架义务全集（缺/多/
      未知 ID 均失败，F13a）；末位小问必须绑定 terminal=true 的终末
      义务（F13b，设问链未收束到终末证明义务即失败）；
  F12 实际 PDF：student.log 中 Overfull \hbox >10.0pt 失败；题目跨页
      （pdftotext 可用时按页文本检测）失败或显式人工批准例外。

待人工复核规则（status=review，须逐项处置后才可交付）：
  R1  prompt 是证明大纲式祈使句而非自然探索问题；
  R2  同义改写/拆分等式可能泄露策略；
  R3  题面已知条件的合法重复与方向提示的边界；
  R4  单一主线被多个平行解法打断；
  R5  题面（stem）出现受保护策略 token（可能是已知条件的合法复现）。
  R6  末问收束一致性：packet 携带 solution_skeleton 时，末位小问 prompt
      与题面 stem 归一化字符 bigram 的 Jaccard 重叠 <0.15，提示人工确认
      末问是否收束回原题所问。

版面预算以实际构建结果为准；字符数只是代理指标。版面例外必须记录在
teaching/layout-exceptions.json（理由、影响页面、审核人），否则不放行。

输出：teaching/checks/check_student_task.json（--json 可覆盖）。
退出码：0=pass/review；1=fail；2=error。
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import unicodedata

def make_review_entry(category: str, raw_msg: str) -> str:
    """【零信任加固】结构化分类与哈希标记，与 deliver.py 的逐项处置闭环完全对齐。"""
    h = hashlib.sha256(raw_msg.encode("utf-8")).hexdigest()[:8]
    return "【分类: %s，hash: %s】%s" % (category, h, raw_msg)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCHEMAS_DIR = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "schemas"))
if _SCHEMAS_DIR not in sys.path:
    sys.path.insert(0, _SCHEMAS_DIR)
sys.path.insert(0, _SCRIPT_DIR)
import contract  # noqa: E402
import check_teaching as ct  # noqa: E402（复用 run/receipt 绑定核对）
import render_student_task as rst  # noqa: E402

BRIEF_REL = "research/brief.json"
MATERIALS_REL = "teaching/materials.json"
ARCHITECT_REL = "teaching/architect-plan.json"
GENERATED_REL = "teaching/generated"
CANONICAL_TEX_REL = "teaching/content/task-%s.tex"
LAYOUT_EXCEPTIONS_REL = "teaching/layout-exceptions.json"
DEFAULT_REPORT = "teaching/checks/check_student_task.json"

CHAR_TARGET_MAX = 900      # 目标区间上界（600--900）
CHAR_WARN = 850            # 告警线（review）
CHAR_HARD_FAIL = 1000      # 硬失败线
OVERFULL_FAIL_PT = 10.0    # Overfull \hbox 超过该值失败

# 内置黑名单（第二道防线；主防线是 brief 声明的 protected_strategy_tokens）
DEFAULT_JARGON = (
    "wilson定理", "威尔逊定理", "lucas定理", "卢卡斯定理",
    "fine定理", "费恩定理", "kummer定理", "库默尔定理",
    "glaisher定理", "格莱舍定理",
    # 概念拆分与结构性暗示防御（§3 语义加固）
    "p进制展开", "p进制表示", "按位展开", "数位展开", "进位展开",
    "进制按位相乘", "各位数字展开",
)
# 结论标记：与受保护 token 同现即构成“指定定理+给结论”的硬失败
CONCLUSION_RE = re.compile(
    r"所以|因此|综上|故得|故而|于是可得|答案是|解为|结果为|可得结论|"
    r"therefore|thus|hence|it follows|the answer is|we conclude|"
    r"证毕|得证|q\.e\.d\.?|"
    r"显然|易知|恒等于|恒为|数值为|即可得|显然可得|直接得出|必为|"
    r"恒等于0|恒为0|余数是0|余数为0", re.IGNORECASE)
# 证明大纲式祈使句（review 级）
OUTLINE_IMPERATIVE_RE = re.compile(
    r"^请?(应用|使用|利用|根据|运用|借助)[^，。；]{0,20}(定理|引理|法则|公式|恒等式)|"
    r"^(证明|验证|推出|导出|试证)[：:，]|证明.{0,12}(成立|等价)", re.IGNORECASE)
# 教师专属/解答路径
FORBIDDEN_SOURCE_RE = re.compile(
    r"(solution|proof|audit|review|teacher[-_]?note|答案|解答|证明)",
    re.IGNORECASE)
# 渲染产物中的禁止环境/宏/答案标记
FORBIDDEN_TEX_RE = re.compile(
    r"\\begin\{solution\}|\\begin\{proof\}|\\begin\{answer\}|tcolorbox|"
    r"mdframed|\\fillin|answerkey|\\answer\b|答案是|本题答案|正确答案")
OVERFULL_RE = re.compile(r"Overfull \\hbox \(([0-9.]+)pt")
PAGES_RE = re.compile(r"Output written on [^ ]+ \((\d+) pages")


def normalize_text(text: str) -> str:
    """归一化：剔除 Unicode Cf（零宽字符）与 TeX 间距控制符、NFKC
    （含全角→半角折叠）、大小写折叠、空白、TeX token 与数学定界符。

    【G6.2 加固】零宽字符（U+200B 等 Cf 类）与 `\\,` `\\!` `\\;` `\\:`
    等间距控制符曾被用于绕过 protected token 的字面匹配；NFKC 同时完成
    全角→半角折叠。check_student_visibility.py 持有同逻辑副本（组内
    共享私有函数，修改时须两处同步）。
    """
    if not isinstance(text, str):
        return ""
    # 1. 剔除 Unicode Cf 类（零宽字符、软连接符等隐形格式字符）
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Cf")
    # 2. NFKC：兼容分解 + 全角→半角折叠（Ａ→A、１→1、U+3000→空格）
    text = unicodedata.normalize("NFKC", text)
    # 3. 大小写折叠
    text = text.casefold()
    text = re.sub(r"\\[,!;:/\-]", " ", text)   # TeX 间距控制符 \, \! \; \: \/ \-
    text = re.sub(r"\$+", " ", text)           # 数学定界符
    text = re.sub(r"\\[a-zA-Z]+", " ", text)   # TeX 控制序列
    text = re.sub(r"[{}\\_^~&%#]", " ", text)
    text = " ".join(text.split())
    return text


def char_bigrams(text: str) -> set:
    """字符 bigram 集合（R6 末问收束一致性的 Jaccard 重叠计算用）。"""
    if not isinstance(text, str):
        return set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def load_json(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def visible_text(task: dict) -> str:
    """渲染后的学生可见文本（字符预算口径，已删除提示与应留证据）。"""
    parts = [task.get("stem", "")]
    for sq in task.get("subquestions", []):
        parts.append(sq.get("prompt", ""))
    return "\n".join(p for p in parts if p)


def load_layout_exceptions(root):
    abs_path = contract.resolve_path(root, LAYOUT_EXCEPTIONS_REL)
    if abs_path and os.path.isfile(abs_path):
        try:
            doc = load_json(abs_path)
            return doc.get("exceptions", []) if isinstance(doc, dict) else []
        except (OSError, json.JSONDecodeError):
            return []
    return []


def check_pdf_layout(root, findings_fail, findings_review, exceptions):
    """版面预算：以实际构建日志/PDF 为准（审计阶段 4）。"""
    layout = {"checked": False, "reason": ""}
    log_abs = contract.resolve_path(root, "student.log")
    pdf_abs = contract.resolve_path(root, "student.pdf")
    if not log_abs or not os.path.isfile(log_abs):
        layout["reason"] = "未找到 student.log（未构建或未生成日志）：页级版面未验证"
        findings_review.append(
            make_review_entry("layout_warn",
                              "版面：未找到 student.log，无法按实际构建结果核对 "
                              "Overfull \\hbox 与页数（字符预算不构成版面结论）"))
        return layout
    with open(log_abs, "r", encoding="utf-8", errors="replace") as f:
        log = f.read()
    layout["checked"] = True
    overfulls = [(float(m.group(1)), m.start())
                 for m in OVERFULL_RE.finditer(log)]
    layout["overfull_hbox_count"] = len(overfulls)
    layout["overfull_hbox_max_pt"] = max((p for p, _ in overfulls), default=0.0)
    severe = [p for p, _ in overfulls if p > OVERFULL_FAIL_PT]
    approved = any(e.get("kind") == "overfull_hbox" for e in exceptions)
    if severe and not approved:
        findings_fail.append(
            "版面：student.log 有 %d 处 Overfull \\hbox 超过 %.1fpt（最大 "
            "%.1fpt）；长公式须用 aligned/split 折行、宽表加 \\small 与列宽；"
            "确需例外须在 %s 登记理由、影响页面与审核人"
            % (len(severe), OVERFULL_FAIL_PT, max(severe),
               LAYOUT_EXCEPTIONS_REL))
    elif severe:
        findings_review.append(
            make_review_entry("layout_warn",
                              "版面：%d 处严重 Overfull \\hbox 已登记人工例外（须确认理由与"
                              "影响页面）" % len(severe)))
    m = PAGES_RE.search(log)
    if m:
        layout["pages"] = int(m.group(1))

    # 页级文本：pdftotext 可用时检测题目跨页
    if pdf_abs and os.path.isfile(pdf_abs) and shutil.which("pdftotext"):
        try:
            r = subprocess.run(
                ["pdftotext", "-layout", pdf_abs, "-"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120)
            pages = r.stdout.split("\f")
            pages = [p for p in pages if p.strip()]
            layout["pdf_pages_extracted"] = len(pages)
            approved_split = any(e.get("kind") == "question_split_page"
                                 for e in exceptions)
            # 题目跨页启发式：某页题目起点数多于小问数，
            # 即有题目在该页开始、其小问留白被带到后续页（跨页断裂）。
            split_hits = []
            for i, page in enumerate(pages, start=1):
                starts = len(re.findall(r"(?:【[^】]+】|(?:\A|\s)\d+\.)", page))
                ends = len(re.findall(r"\(\d+\)", page))
                if starts > ends:
                    split_hits.append(i)
            if split_hits and not approved_split:
                findings_fail.append(
                    "版面：检测到题目跨页断裂（第 %s 页的题目起点多于留白"
                    "结束数）；题目不得跨页断裂，须调整留白/折行或在 %s "
                    "登记人工批准例外"
                    % ("、".join(map(str, split_hits)),
                       LAYOUT_EXCEPTIONS_REL))
            elif split_hits:
                findings_review.append(
                    make_review_entry("layout_warn",
                                      "版面：题目跨页已登记人工例外（须确认理由与影响页面）"))
        except (OSError, subprocess.SubprocessError) as exc:
            layout["pdf_extract_error"] = str(exc)
    else:
        layout["pdf_text_extraction"] = "pdftotext 不可用，跳过页级文本抽取"
    return layout


def check(root, initial_view="student_initial"):
    fails, reviews, infos, fatal = [], [], [], []
    evidence = {"questions_checked": 0, "tasks_checked": 0,
                "subquestions_checked": 0, "char_budget": {},
                "layout": {}, "contract_version": contract.CONTRACT_VERSION}

    # ---- F1：brief 与 expected 清单 ----
    brief, berr = ct.load_json(root, BRIEF_REL)
    if berr:
        fatal.append("缺少有效教研交接包：%s；学生任务门禁的 expected 清单"
                     "来自冻结 brief，无法计算时一律拒绝（绝不以空检查集"
                     "PASS）" % berr)
        return fails, reviews, infos, evidence, fatal
    berrs = contract.validate(brief, "brief")
    if berrs:
        fatal.append("brief.json 未通过 schema 校验: %s [%s] %s"
                     % (berrs[0]["path"], berrs[0]["validator"],
                        berrs[0]["message"]))
        return fails, reviews, infos, evidence, fatal
    expected_qids = list(brief["selection"]["selected_problem_ids"])
    evidence["expected_question_ids"] = expected_qids

    # 受保护策略 token：brief 声明为主，内置黑名单为第二道防线
    protected_tokens = [t for t in brief.get("protected_strategy_tokens", [])
                        if isinstance(t, str) and t]
    evidence["protected_strategy_tokens"] = protected_tokens

    # architect-plan 的 forbidden_feature_refs 与 materials 块级声明
    forbidden_features = set()
    arch_abs = contract.resolve_path(root, ARCHITECT_REL)
    if arch_abs and os.path.isfile(arch_abs):
        try:
            arch = load_json(arch_abs)
            forbidden_features.update(arch.get("forbidden_feature_refs", []))
        except (OSError, json.JSONDecodeError):
            reviews.append(
                make_review_entry("architect_read_skip",
                                  "architect-plan.json 不可读，forbidden_feature_refs "
                                  "核对跳过（须人工确认）"))
    materials, merr = ct.load_json(root, MATERIALS_REL)
    block_forbidden = {}
    if merr is None and isinstance(materials, dict):
        for b in materials.get("blocks", []):
            tid = b.get("task_id")
            if tid:
                block_forbidden.setdefault(tid, set()).update(
                    b.get("forbidden_feature_refs", []))

    # ---- F2：覆盖与规范文件名 ----
    gen_abs = contract.resolve_path(root, GENERATED_REL)
    canonical = {("%s-student-task.json" % qid): qid for qid in expected_qids}
    canonical_solutions = {("%s-solution-task.json" % qid): qid for qid in expected_qids}
    allowed_canonical = set(canonical.keys()) | set(canonical_solutions.keys())
    if gen_abs is None or not os.path.isdir(gen_abs):
        fails.append("缺少 %s/：每个入选题必须恰好有一个 <qid>-student-task.json"
                     "（缺题即失败，不以空检查集 PASS）" % GENERATED_REL)
        present = []
    else:
        present = sorted(fn for fn in os.listdir(gen_abs)
                         if fn.endswith(".json"))
        for qid in expected_qids:
            if ("%s-student-task.json" % qid) not in present:
                fails.append("入选题 %s 缺少规范文件 %s/%s-student-task.json"
                             % (qid, GENERATED_REL, qid))
        for fn in present:
            if fn not in allowed_canonical:
                fails.append("%s/ 中存在规范集之外的文件 %s（额外题或备用名"
                             "均失败；期望恰好: %s）"
                             % (GENERATED_REL, fn,
                                "、".join(sorted(allowed_canonical))))

    run_index = ct._load_runs(root)
    exceptions = load_layout_exceptions(root)

    for qid in expected_qids:
        evidence["questions_checked"] += 1  # 每题开始时递增（含缺题）
        rel = "%s/%s-student-task.json" % (GENERATED_REL, qid)
        abs_task = contract.resolve_path(root, rel)
        if abs_task is None or not os.path.isfile(abs_task):
            continue  # 缺题已在 F2 报告
        try:
            task = load_json(abs_task)
        except (OSError, json.JSONDecodeError) as exc:
            fails.append("%s 不是合法 JSON: %s" % (rel, exc))
            continue
        evidence["tasks_checked"] += 1
        tid = "task-%s" % qid

        # ---- G6.3：solution 字段禁令（schema 屏蔽之外的显式硬失败） ----
        if "solution" in task:
            fails.append("%s 携带禁止的顶层 solution 字段（学生任务契约不含"
                         "解答字段；解答属教师侧产物）" % rel)
        for _i, _sq in enumerate(task.get("subquestions") or [], start=1):
            if isinstance(_sq, dict) and "solution" in _sq:
                fails.append("%s 小问 %s 携带禁止的 solution 字段（学生任务"
                             "契约禁止输出解答；schema 已屏蔽该字段）"
                             % (rel, _sq.get("subquestion_id",
                                             "[#%d]" % _i)))

        # ---- F3：schema ----
        for e in contract.validate(task, "student-task"):
            fails.append("%s: %s [%s] %s"
                         % (rel, e["path"], e["validator"], e["message"]))
        if contract.validate(task, "student-task"):
            continue  # schema 已坏时后续核对无意义

        # ---- F10：结构 ----
        subqs = task.get("subquestions", [])
        evidence["subquestions_checked"] += len(subqs)
        seen = set()
        for i, sq in enumerate(subqs, start=1):
            sid = sq.get("subquestion_id", "[#%d]" % i)
            if sid in seen:
                fails.append("%s 小问 ID 重复: %s" % (rel, sid))
            seen.add(sid)
            if not (task.get("stem") or "").strip():
                fails.append("%s 题干为空" % rel)
            for field in ("prompt",):
                if not (sq.get(field) or "").strip():
                    fails.append("%s 小问 %s 的 %s 为空"
                                 % (rel, sid, field))
            hint_level = sq.get("hint_level")
            reveal = sq.get("reveal", "on_demand")
            if hint_level and hint_level == "verification" and reveal == "initial":
                fails.append("%s 小问 %s：verification 级提示不得进入初始"
                             "视图（reveal=initial）" % (rel, sid))
            ans = sq.get("answer_space_mm")
            if not isinstance(ans, int) or isinstance(ans, bool) \
                    or not (16 <= ans <= 60):
                fails.append("%s 小问 %s answer_space_mm=%r 非法（16--60）"
                             % (rel, sid, ans))

        # ---- F4/F5/F6：白名单、来源 digest、渲染映射 ----
        run = run_index.get(task.get("run_id", ""))
        packet = None
        if run is not None:
            entry = run.get("tasks", {}).get(tid, {})
            packet_ref = "%s/%s/packets/%s.json" % ("staging",
                                                    task.get("run_id"), tid)
            packet_abs = contract.resolve_path(root, packet_ref)
            if packet_abs and os.path.isfile(packet_abs):
                try:
                    packet = load_json(packet_abs)
                except (OSError, json.JSONDecodeError):
                    packet = None
        verdict = contract.preflight_student_task(
            task, project_root=root, packet=packet, run=run, task_id=tid)
        if not verdict["ok"]:
            fails.append("%s 沙箱/绑定预检失败：%s" % (rel, verdict["hint"]))
        ct._check_task_binding(root, rel, task, run_index, fails)

        # ---- F10b/F13/R6：解法骨架（solution_skeleton）驱动的结构契约 ----
        # 骨架由 make_small_packet.py 从冻结 solution.json 机械派生（派生
        # 字段，非输入源）。红线：无骨架（packet 为 None 或未携带该键）时
        # 本组检查全部跳过、不新增任何 fail，行为与骨架机制引入前完全一致。
        skel = (packet or {}).get("solution_skeleton")
        if not isinstance(skel, dict):
            skel = None
        target = skel.get("target_subquestion_count") if skel else None

        # ---- F10b：小问数与骨架目标问数精确一致 ----
        if target is not None and len(subqs) != target:
            fails.append(
                "%s 小问数 %d 与解法骨架目标问数 %d 不一致（小问数量由 "
                "proof_obligations 推导，须精确相等）"
                % (rel, len(subqs), target))

        if skel is not None:
            obligations = skel.get("obligations")
            if isinstance(obligations, list) and obligations:
                obs = {o.get("obligation_id") for o in obligations
                       if isinstance(o, dict) and o.get("obligation_id")}
                # ---- F13a：义务覆盖——每小问必填 obligation_refs；不得
                # 出现骨架之外的义务 ID；全体小问并集须精确等于义务全集 ----
                covered = set()
                unknown = set()
                for i, sq in enumerate(subqs, start=1):
                    sid = sq.get("subquestion_id", "[#%d]" % i)
                    refs = sq.get("obligation_refs")
                    if not isinstance(refs, list) or not refs:
                        fails.append(
                            "%s 小问 %s 缺少 obligation_refs（packet 携带解"
                            "法骨架时每个小问必须绑定至少一个证明义务）"
                            % (rel, sid))
                        continue
                    for r in refs:
                        if r in obs:
                            covered.add(r)
                        else:
                            unknown.add(r)
                if unknown:
                    fails.append(
                        "%s obligation_refs 出现骨架之外的义务 ID: %s"
                        % (rel, "、".join(sorted(str(u) for u in unknown))))
                missing = obs - covered
                if missing:
                    fails.append(
                        "%s 小问 obligation_refs 并集未覆盖全部解法义务，"
                        "缺失 ID: %s（∪ obligation_refs 必须精确等于骨架义"
                        "务全集）" % (rel, "、".join(sorted(missing))))
                # ---- F13b：末位小问必须绑定 terminal=true 的终末义务 ----
                terminal_id = next(
                    (o.get("obligation_id") for o in obligations
                     if isinstance(o, dict) and o.get("terminal")), None)
                if terminal_id is not None and subqs:
                    last_refs = subqs[-1].get("obligation_refs")
                    if not isinstance(last_refs, list) \
                            or terminal_id not in last_refs:
                        fails.append(
                            "%s 设问链未收束到终末证明义务：末位小问未绑定 "
                            "terminal 义务 %s" % (rel, terminal_id))
                # ---- R6：末问与原题收束一致性（review 级代理指标） ----
                if subqs:
                    last_prompt_n = normalize_text(
                        subqs[-1].get("prompt", ""))
                    stem_r6_n = normalize_text(task.get("stem", ""))
                    bp = char_bigrams(last_prompt_n)
                    bs = char_bigrams(stem_r6_n)
                    union = bp | bs
                    if union:
                        jaccard = len(bp & bs) / len(union)
                        if jaccard < 0.15:
                            reviews.append(
                                make_review_entry(
                                    "terminal_convergence",
                                    "%s 末位小问 prompt 与题面 stem 的归一化"
                                    "字符 bigram Jaccard 重叠 %.3f < 0.15："
                                    "须人工确认末问是否收束回原题所问"
                                    % (rel, jaccard)))

        expected_stem = None
        if packet and packet.get("problem_stem"):
            expected_stem = packet["problem_stem"].strip()
        else:
            expected_stem = contract.extract_problem_stem(root, qid)
        if expected_stem:
            expected_clean = contract.strip_numbering_and_source(expected_stem).strip()
            actual_clean = contract.strip_numbering_and_source((task.get("stem") or "").strip()).strip()
            if actual_clean.replace("\r\n", "\n") != expected_clean.replace("\r\n", "\n"):
                fails.append(
                    "%s 题干与所提供题目的题干不一致（最终交付产物中题干必须与所提供题目保持完全一致，严禁改写或变动）"
                    % rel
                )

        # ---- F7：渲染产物禁止环境与答案宏 ----
        tex_abs = contract.resolve_path(root, CANONICAL_TEX_REL % qid)
        if run is not None:
            entry = run.get("tasks", {}).get(tid, {})
            staged = entry.get("render_ref")
            if staged:
                staged_abs = contract.resolve_path(root, staged)
                if staged_abs and os.path.isfile(staged_abs):
                    tex_abs = staged_abs
        if tex_abs is None or not os.path.isfile(tex_abs):
            fails.append("%s 缺少渲染产物 %s（必须由 broker render_task 经"
                         "确定性渲染器生成）"
                         % (rel, CANONICAL_TEX_REL % qid))
        else:
            with open(tex_abs, "r", encoding="utf-8", errors="replace") as f:
                tex = f.read()
            for m in FORBIDDEN_TEX_RE.finditer(tex):
                fails.append("%s 渲染产物含禁止环境/宏/答案标记: %r"
                             % (rel, m.group(0)))
            # 每个小问必须恰好对应一个带单位的 \ansspace 留白
            ans_count = len(re.findall(r"\\ansspace\{\d+mm\}", tex))
            if ans_count != len(subqs):
                fails.append("%s 渲染产物 \\ansspace 数量 %d 与小问数 %d 不一致"
                             "（必须由确定性渲染器生成）"
                             % (rel, ans_count, len(subqs)))
            if ("renderer_version: %s" % rst.RENDERER_VERSION) not in tex:
                fails.append("%s 渲染产物缺少机器标记 renderer_version: %s"
                             "（非本渲染器输出）" % (rel, rst.RENDERER_VERSION))
            entry = (run or {}).get("tasks", {}).get(tid, {})
            if entry.get("render_digest") \
                    and contract.sha256_file(tex_abs) != entry["render_digest"]:
                fails.append("%s 渲染产物摘要与 broker 记录不一致（渲染后被"
                             "改动或手写 .tex）" % rel)

        # ---- F8：学生初始视图纯净 ----
        if task.get("level") != "L0":
            fails.append("%s level=%r：学生初始任务必须为 L0"
                         % (rel, task.get("level")))
        for ref in task.get("source_refs", []):
            if FORBIDDEN_SOURCE_RE.search(str(ref)):
                fails.append("%s 的 source_refs 引用受限路径: %s"
                             % (rel, ref))
        student_text = visible_text(task)
        norm = normalize_text(student_text)
        for feat in sorted(forbidden_features | block_forbidden.get(tid, set())):
            if feat and normalize_text(feat) in norm:
                fails.append("%s 学生可见文本出现禁泄露特征 %r" % (rel, feat))

        # ---- F9：黑话标签（归一化后匹配） ----
        for sq in subqs:
            sid = sq.get("subquestion_id", "?")
            hint_n = normalize_text(sq.get("hint", ""))
            evid_n = normalize_text(sq.get("expected_evidence", ""))
            prompt_n = normalize_text(sq.get("prompt", ""))
            for tok in protected_tokens + list(DEFAULT_JARGON):
                tok_n = normalize_text(tok)
                if not tok_n:
                    continue
                if tok_n in hint_n:
                    fails.append("%s 小问 %s 的 hint 出现受保护策略 token %r"
                                 "（提示不得指定定理）" % (rel, sid, tok))
                if tok_n in evid_n:
                    fails.append("%s 小问 %s 的 expected_evidence 出现受保护"
                                 "策略 token %r" % (rel, sid, tok))
                if tok_n in prompt_n and CONCLUSION_RE.search(prompt_n):
                    fails.append("%s 小问 %s 的 prompt 同时出现受保护 token "
                                 "%r 与结论标记（指定定理+给结论）"
                                 % (rel, sid, tok))
            if CONCLUSION_RE.search(hint_n):
                fails.append("%s 小问 %s 的 hint 含结论标记（提示不得给结论）"
                             % (rel, sid))
            # R1：证明大纲式祈使句
            if OUTLINE_IMPERATIVE_RE.search(sq.get("prompt", "")):
                reviews.append(
                    make_review_entry(
                        "outline_imperative",
                        "%s 小问 %s 的 prompt 呈证明大纲式祈使句，须人工确认是否为自然探索问题"
                        % (rel, sid)))
        # ---- F9b：跨字段拆分检测（拼接视图，G6.2） ----
        # 受保护 token 被拆到不同字段/不同小问（如一半在 prompt、一半在
        # hint）时逐字段匹配会漏检；改为拼接视图匹配。拼接视图含两部分：
        #   (a) 按字段分组拼接（prompt 全体 + hint 全体 + evidence 全体），
        #       覆盖跨小问同字段相邻拆分与字段块边界拆分；
        #   (b) 按小问拼接（同一小问的 prompt+hint+expected_evidence），
        #       覆盖同一小问内跨字段拆分（最典型的拆分形态）。
        # 拼接会在字段间引入空格，而 CJK 文本中空格无语义，故做空格剥离比对。
        prompt_joined = normalize_text(" ".join(
            str(sq.get("prompt", "") or "") for sq in subqs))
        hint_joined = normalize_text(" ".join(
            str(sq.get("hint", "") or "") for sq in subqs))
        evid_joined = normalize_text(" ".join(
            str(sq.get("expected_evidence", "") or "") for sq in subqs))
        per_sq_joined = normalize_text(" ".join(
            " ".join(str(sq.get(f, "") or "")
                     for f in ("prompt", "hint", "expected_evidence"))
            for sq in subqs))
        all_joined_ns = (prompt_joined + " " + hint_joined + " "
                         + evid_joined + " " + per_sq_joined
                         ).replace(" ", "")
        for tok in protected_tokens + list(DEFAULT_JARGON):
            tok_n = normalize_text(tok)
            tok_ns = tok_n.replace(" ", "")
            if not tok_ns or tok_ns not in all_joined_ns:
                continue
            # 单字段命中已由上面的逐字段规则报告（hint/evid 硬失败、
            # prompt+结论同现硬失败）；此处只补报跨字段/跨小问拆分。
            in_single_field = any(
                tok_ns in normalize_text(
                    str(sq.get(f, "") or "")).replace(" ", "")
                for sq in subqs
                for f in ("prompt", "hint", "expected_evidence"))
            if not in_single_field:
                fails.append("%s 受保护策略 token %r 被拆分到多个字段/小问"
                             "（拼接视图命中；跨字段拆分视同泄露）"
                             % (rel, tok))
        stem_n = normalize_text(task.get("stem", ""))
        for tok in protected_tokens + list(DEFAULT_JARGON):
            tok_n = normalize_text(tok)
            if tok_n and tok_n in stem_n:
                reviews.append(
                    make_review_entry(
                        "same_problem_reuse",
                        "%s 题面出现策略 token %r：可能是题面已知条件的合法复现，须人工确认（数学公共片段规则）"
                        % (rel, tok)))

        # ---- F11：字符预算（动态阈值；t = 骨架目标问数 target_subquestion_
        # count，无骨架时 t=3，不新增 fail、与旧路径兼容） ----
        n_chars = len(visible_text(task))
        evidence["char_budget"][qid] = n_chars
        src_stem_len = len(expected_stem or "")
        t = target or 3
        hard_limit = max(CHAR_HARD_FAIL, src_stem_len + 80 * t + 160)
        warn_limit = max(CHAR_WARN, src_stem_len + 80 * t + 40)
        if n_chars > hard_limit:
            fails.append("%s 渲染可见文本 %d 字符 > 动态硬上限 %d"
                         "（=max(1000, 题面长度 %d + 80×目标问数 %d + 160）；"
                         "超过硬上限的题面/小问组合须拆分或精炼）"
                         % (rel, n_chars, hard_limit, src_stem_len, t))
        elif n_chars > warn_limit:
            reviews.append(
                make_review_entry(
                    "char_budget_warn",
                    "%s 渲染可见文本 %d 字符超过动态告警线 %d"
                    "（=max(850, 题面长度 %d + 80×目标问数 %d + 40)）"
                    % (rel, n_chars, warn_limit, src_stem_len, t)))

    # ---- F12：版面预算（实际 PDF/日志） ----
    evidence["layout"] = check_pdf_layout(root, fails, reviews, exceptions)

    infos.append(
        "能力边界：本门禁做结构/字面/归一化匹配与代理指标检查；语义级泄露"
        "（同义改写、拆开等式、图表泄露策略）由独立学生阅读审查判定；版面"
        "结论以实际 PDF 与构建日志为准，字符数只是代理指标。")
    return fails, reviews, infos, evidence, fatal


def main():
    ap = argparse.ArgumentParser(
        description="学生任务质量统一门禁（2.4.0，必经；缺题/空清单默认失败）")
    ap.add_argument("project_root", help="项目根（含 research/ 与 teaching/）")
    ap.add_argument("--json", metavar="PATH", default=None,
                    help="报告落盘路径（默认 %s）" % DEFAULT_REPORT)
    ap.add_argument("--initial-view", default="student_initial",
                    help="首轮发放视图名（默认 student_initial）")
    args = ap.parse_args()
    root = os.path.abspath(args.project_root)
    if not os.path.isdir(root):
        print("[check_student_task] 项目根不存在: %s" % root)
        return 2

    fails, reviews, infos, evidence, fatal = check(root, args.initial_view)
    if fatal:
        status = "error"
    elif fails:
        status = "fail"
    elif reviews:
        status = "review"
    else:
        status = "pass"

    findings = ([{"severity": "error", "message": m} for m in fatal]
                + [{"severity": "fail", "message": m} for m in fails]
                + [{"severity": "review", "message": m} for m in reviews]
                + [{"severity": "info", "message": m} for m in infos])
    report_path = args.json or os.path.join(root, DEFAULT_REPORT)
    report = contract.write_check_report(report_path, status, "student_task",
                                         findings)
    report["evidence"] = evidence
    with open(report_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")

    for m in fatal:
        print("[check_student_task] [错误] " + m)
    for m in fails:
        print("[check_student_task] [失败] " + m)
    for m in reviews:
        print("[check_student_task] [待复核] " + m)
    for m in infos:
        print("[check_student_task] [说明] " + m)
    print("[check_student_task] 检查题数=%d，任务文件=%d，小问=%d，status=%s"
          % (evidence["questions_checked"], evidence["tasks_checked"],
             evidence["subquestions_checked"], status))
    print("[check_student_task] 报告已写入 %s" % report_path)
    if status == "error":
        return 2
    if status == "fail":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
