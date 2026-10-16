#!/usr/bin/env python
# -*- coding: utf-8 -*-
r"""make_small_packet.py — broker 窄上下文输入包生成器（契约 2.4.0）。

安全模型（§4 / §5.2 + 审计整改）：
- packet 的允许输入由**冻结 brief** 计算出的白名单给出（statement_ref /
  probe_ref 等 L0 源），不接受调用方传入任意 source_paths；
- 路径解析检查真实路径、符号链接与项目根越界；solution / proof / audit /
  review / 教师备注等受限内容即使在读取层也不得进入 student packet；
- input_digests 非空，且由本脚本从实际读取的文件重算；
- 输出只能落在 staging/<run_id>/packets/ 下，拒绝任意项目外输出路径；
- packet 含 packet_kind / run_id / question_id / input_digests /
  allowed_source_refs / output_fields_white_list / size_bytes，并与
  broker 签发的 receipt 绑定。

命令示例：
  python make_small_packet.py --project . --run-id run-20260922-xxx \
      --task-id task-P441 --out staging/run-20260922-xxx/packets/task-P441.json
  python make_small_packet.py --project . --run-id run-... --task-id task-P441 \
      --kind teacher-solution --out staging/.../packets/task-P441-teacher.json
"""
import argparse
import json
import os
import re
import sys
import unicodedata

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCHEMAS_DIR = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "schemas"))
if _SCHEMAS_DIR not in sys.path:
    sys.path.insert(0, _SCHEMAS_DIR)
sys.path.insert(0, _SCRIPT_DIR)
import contract  # noqa: E402


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")


BRIEF_REL = "research/brief.json"
PACKET_MAX_BYTES = 6144
STEM_MAX_CHARS = 1200

# 受限内容关键词：命中即拒绝进入 student packet 输入白名单（第二道防线，
# 主防线是“只从冻结 brief 的 L0 引用构造白名单”）。
FORBIDDEN_PATH_RE = re.compile(
    r"(solution|proof|audit|review|teacher[-_]?note|answer|解答|答案|证明)",
    re.IGNORECASE)


# 内置黑名单（第二道防线；主防线是 brief 声明的 protected_strategy_tokens）
# 【同步】与 check_student_task.py / check_student_visibility.py 保持一致
DEFAULT_JARGON = (
    "wilson定理", "威尔逊定理", "lucas定理", "卢卡斯定理",
    "fine定理", "费恩定理", "kummer定理", "库默尔定理",
    "glaisher定理", "格莱舍定理",
    # 概念拆分与结构性暗示防御（§3 语义加固）
    "p进制展开", "p进制表示", "按位展开", "数位展开", "进位展开",
    "进制按位相乘", "各位数字展开",
)
# 结论标记：与受保护 token 同现即构成“指定定理+给结论”的硬失败
# 【同步】与 check_student_task.py / check_student_visibility.py 保持一致
CONCLUSION_RE = re.compile(
    r"所以|因此|综上|故得|故而|于是可得|答案是|解为|结果为|可得结论|"
    r"therefore|thus|hence|it follows|the answer is|we conclude|"
    r"证毕|得证|q\.e\.d\.?|"
    r"显然|易知|恒等于|恒为|数值为|即可得|显然可得|直接得出|必为|"
    r"恒等于0|恒为0|余数是0|余数为0", re.IGNORECASE)


# 【同步】与 check_student_task.py / check_student_visibility.py 保持一致
def _normalize_text(text: str) -> str:
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


def load_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def find_brief(project_root: str):
    for rel in ("research/brief.json", "brief.json"):
        p = contract.resolve_path(project_root, rel)
        if p and os.path.isfile(p):
            return p
    return None


def problem_entry(brief: dict, problem_id: str):
    """从冻结 brief 判定该题是否允许构造 packet。

    返回该题条目（dict）；brief 只有 selection.selected_problem_ids 而
    没有 selected_questions 明细时返回空 dict（题面回退到
    statement_ref）。不在入选题清单中返回 None。
    """
    sq = brief.get("selected_questions", {})
    if isinstance(sq, dict) and problem_id in sq:
        entry = sq[problem_id]
        return entry if isinstance(entry, dict) else {}
    if problem_id in brief.get("selection", {}).get(
            "selected_problem_ids", []):
        return {}
    return None


def math_baseline(brief: dict, problem_id: str):
    for mb in brief.get("math_baselines", []):
        if mb.get("problem_id") == problem_id:
            return mb
    return None


def real_relpath(project_root: str, rel: str):
    """解析并校验真实路径（符号链接解析后仍须在项目根内）。"""
    abs_path = contract.resolve_path(project_root, rel)
    if abs_path is None:
        return None
    real = os.path.realpath(abs_path)
    root_real = os.path.realpath(os.path.abspath(project_root))
    if os.path.commonpath([root_real, real]) != root_real:
        return None
    return real


def digest_of(project_root: str, rel: str):
    real = real_relpath(project_root, rel)
    if real is None or not os.path.isfile(real):
        return None
    return contract.sha256_file(real)


def read_text(project_root: str, rel: str, limit: int = 50000) -> str:
    real = real_relpath(project_root, rel)
    if real is None or not os.path.isfile(real):
        return ""
    with open(real, "r", encoding="utf-8", errors="replace") as f:
        return f.read(limit)


def sanitized_probe(probe: dict) -> dict:
    """只保留 L0 可观察事实：路径、数值、比较与等号状态。

    刻意剔除 evidence_note / conjectures / student_warmup 以外的任何
    解释性文字，避免把策略方向写进学生输入包。
    """
    out = {}
    for key in ("probe_id", "kind", "path", "numeric_value", "comparison",
                "equality_status"):
        if key in probe:
            out[key] = probe[key]
    return out


_SIGNPOST_SPLIT_RE = re.compile(r"[，。；,.;]")
_SIGNPOST_MAX_CHARS = 40


def _neutral_signpost(claim, order: int, protected_tokens) -> str:
    """从 obligation 的 claim 派生中性化路标（§0.2，确定性，无自由裁量）。

    命中 brief 的 protected_strategy_tokens / DEFAULT_JARGON / CONCLUSION_RE
    任一，或首个子句归一化后为空时，回退为通用路标。
    """
    fallback = "完成第 %d 步关键论证（路标已中性化）" % order
    if not isinstance(claim, str) or not claim.strip():
        return fallback
    norm_claim = _normalize_text(claim)
    for tok in list(protected_tokens) + list(DEFAULT_JARGON):
        tok_n = _normalize_text(tok)
        if tok_n and tok_n in norm_claim:
            return fallback
    if CONCLUSION_RE.search(norm_claim):
        return fallback
    first = _SIGNPOST_SPLIT_RE.split(claim)[0].strip()
    first_n = _normalize_text(first)
    if not first_n:
        return fallback
    for tok in list(protected_tokens) + list(DEFAULT_JARGON):
        tok_n = _normalize_text(tok)
        if tok_n and tok_n in first_n:
            return fallback
    if CONCLUSION_RE.search(first_n):
        return fallback
    if len(first) > _SIGNPOST_MAX_CHARS:
        first = first[:_SIGNPOST_MAX_CHARS]
    return first


def _derive_solution_skeleton(project_root: str, brief: dict, problem_id: str):
    """从冻结 solution.json 机械派生解法骨架（§0.1/§0.2）。

    solution.json 绝不进入 allowed_source_refs / input_digests；骨架是
    与 problem_stem 一样的派生 body 字段。任何一步失败（无 solution_ref、
    无/空 proof_obligations、无 verified 义务、读取或解析异常）一律
    返回 None：packet 省略该字段仍合法，绝不阻断 packet 生成。
    """
    try:
        mb = math_baseline(brief, problem_id)
        if not mb:
            return None
        solution_ref = mb.get("solution_ref")
        if not solution_ref:
            return None
        real = real_relpath(project_root, solution_ref)
        if real is None or not os.path.isfile(real):
            return None
        solution = load_json(real)
        if not isinstance(solution, dict):
            return None
        obligations = solution.get("proof_obligations")
        if not isinstance(obligations, list) or not obligations:
            return None
        verified = [o for o in obligations
                    if isinstance(o, dict) and o.get("status") == "verified"]
        if not verified:
            return None
        protected = [t for t in brief.get("protected_strategy_tokens", [])
                     if isinstance(t, str) and t]
        items = []
        for idx, ob in enumerate(verified, start=1):
            items.append({
                "obligation_id": str(ob.get("obligation_id", "")),
                "order": idx,
                "signpost": _neutral_signpost(
                    ob.get("claim", ""), idx, protected),
                "terminal": idx == len(verified),
            })
        count = len(verified)
        return {
            "source": "derived",
            "obligation_count": count,
            "obligations": items,
            "target_subquestion_count": max(3, min(6, count + 1)),
        }
    except Exception:
        # 骨架派生失败必须静默回退为省略该字段（packet 仍合法）
        return None


def build_student_task_packet(project_root: str, run: dict, task_id: str,
                              brief: dict, problem_id: str):
    """构造 student-task packet。返回 (packet, error_message)。"""
    entry = problem_entry(brief, problem_id)
    if entry is None:
        return None, ("题目 %s 不在冻结 brief 的 selection."
                      "selected_problem_ids 中；packet 只能由冻结 brief "
                      "允许的 L0 源构造" % problem_id)

    # ---- 白名单：冻结 brief 的 L0 引用（statement + probe）----
    whitelist = []
    mb = math_baseline(brief, problem_id)
    if mb:
        for key in ("statement_ref", "probe_ref"):
            ref = mb.get(key)
            if ref:
                whitelist.append(contract.normalize_relpath(ref))
    # brief 自身作为冻结交接的绑定源（不含答案）
    whitelist.append(BRIEF_REL)
    # 去重并保持顺序
    seen, ordered = set(), []
    for ref in whitelist:
        if ref not in seen:
            seen.add(ref)
            ordered.append(ref)

    for ref in ordered:
        if FORBIDDEN_PATH_RE.search(ref):
            return None, ("白名单路径 %r 命中受限内容关键词；student packet "
                          "不得包含 solution/proof/audit/review/教师备注" % ref)
        if digest_of(project_root, ref) is None:
            return None, "白名单来源文件缺失或越出项目根: %s" % ref

    input_digests = {ref: digest_of(project_root, ref) for ref in ordered}

    # ---- 题面：brief 条目优先，否则提取 statement_ref 纯净题干 ----
    stem = (entry or {}).get("stem", "") if isinstance(entry, dict) else ""
    if not stem and mb:
        stem = contract.extract_problem_stem(project_root, mb.get("statement_ref", ""))
    if not stem:
        stem = contract.extract_problem_stem(project_root, problem_id)
    if not stem and mb:
        stem = contract.clean_problem_stem(read_text(project_root, mb.get("statement_ref", "")))
    if not stem:
        return None, "无法取得题目 %s 的题面（brief.selected_questions 与 statement_ref 均未提供）" % problem_id

    # ---- 探针 L0 观察 ----
    probe_evidence = {}
    probe_rel = None
    if mb and mb.get("probe_ref"):
        probe_rel = contract.normalize_relpath(mb["probe_ref"])
    else:
        cand = contract.resolve_path(
            project_root, "research/problems/%s/probe.json" % problem_id)
        if cand and os.path.isfile(cand):
            probe_rel = "research/problems/%s/probe.json" % problem_id
    if probe_rel and digest_of(project_root, probe_rel) is not None:
        try:
            probe = load_json(real_relpath(project_root, probe_rel))
        except (OSError, json.JSONDecodeError):
            probe = None
        if isinstance(probe, dict):
            warmup = probe.get("student_warmup")
            if isinstance(warmup, dict):
                probe_evidence["student_warmup"] = {
                    "question": warmup.get("question", ""),
                    "test_points": warmup.get("test_points", []),
                    "expected_phenomenon": warmup.get("expected_phenomenon", ""),
                }
            probes = probe.get("probes")
            if isinstance(probes, list):
                probe_evidence["observations"] = [
                    sanitized_probe(p) for p in probes
                    if isinstance(p, dict)][:8]

    body = {
        "schema_version": contract.CONTRACT_VERSION,
        "packet_kind": "student-task",
        "run_id": run["run_id"],
        "brief_id": brief.get("brief_id", ""),
        "brief_revision": brief.get("revision"),
        "question_id": problem_id,
        "task_id": task_id,
        "problem_stem": stem,
        "probe_evidence": probe_evidence,
        "lesson_mode": run.get("lesson_mode", "guided"),
        "input_digests": input_digests,
        "allowed_source_refs": ordered,
        "forbidden_fields": ["solution", "answer", "proof", "teacher_note"],
        "output_fields_white_list": list(contract.STUDENT_TASK_FIELDS),
    }
    # ---- 解法骨架：机械派生 body 字段（非输入源；派生失败则整体省略） ----
    skeleton = _derive_solution_skeleton(project_root, brief, problem_id)
    if skeleton is not None:
        body["solution_skeleton"] = skeleton
    body["packet_digest"] = contract.packet_body_digest(body)
    canonical = contract.canonical_json(body).encode("utf-8")
    body["size_bytes"] = len(canonical)
    return body, None


def build_teacher_solution_packet(project_root: str, run: dict, task_id: str,
                                  brief: dict, problem_id: str):
    """teacher-solution packet：允许该题已核验证明证据（仅教师角色可见）。"""
    mb = math_baseline(brief, problem_id)
    if mb is None:
        return None, "题目 %s 在 brief.math_baselines 中无记录" % problem_id
    ordered = []
    for key in ("statement_ref", "probe_ref", "solution_ref"):
        ref = mb.get(key)
        if ref:
            ordered.append(contract.normalize_relpath(ref))
    ordered.extend(contract.normalize_relpath(r)
                   for r in mb.get("proof_refs", []))
    ordered.append(BRIEF_REL)
    seen, uniq = set(), []
    for ref in ordered:
        if ref not in seen:
            seen.add(ref)
            uniq.append(ref)
    for ref in uniq:
        if digest_of(project_root, ref) is None:
            return None, "来源文件缺失或越出项目根: %s" % ref
    input_digests = {ref: digest_of(project_root, ref) for ref in uniq}
    body = {
        "schema_version": contract.CONTRACT_VERSION,
        "packet_kind": "teacher-solution",
        "run_id": run["run_id"],
        "brief_id": brief.get("brief_id", ""),
        "brief_revision": brief.get("revision"),
        "question_id": problem_id,
        "task_id": task_id,
        "input_digests": input_digests,
        "allowed_source_refs": uniq,
        "output_fields_white_list": list(contract.STUDENT_TASK_FIELDS),
    }
    body["packet_digest"] = contract.packet_body_digest(body)
    body["size_bytes"] = len(contract.canonical_json(body).encode("utf-8"))
    return body, None


def default_packet_path(project_root: str, run_id: str, task_id: str,
                        kind: str) -> str:
    name = task_id if kind == "student-task" else "%s-%s" % (task_id, kind)
    return contract.normalize_relpath(os.path.join(
        "staging", run_id, "packets", name + ".json"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成窄上下文输入包（2.4.0）")
    parser.add_argument("--project", required=True, help="项目根目录")
    parser.add_argument("--run-id", required=True, help="broker 签发的 run ID")
    parser.add_argument("--task-id", required=True, help="任务 ID（task-<qid>）")
    parser.add_argument("--kind", default="student-task",
                        choices=["student-task", "teacher-solution"],
                        help="包类型（默认 student-task）")
    parser.add_argument("--out", default=None,
                        help="输出路径（默认 staging/<run_id>/packets/<task_id>.json；"
                             "必须落在 staging/<run_id>/packets/ 内）")
    args = parser.parse_args(argv)

    project_root = os.path.abspath(args.project)
    run_path = contract.resolve_path(
        project_root, "staging/%s/run.json" % args.run_id)
    if run_path is None or not os.path.isfile(run_path):
        print("ERROR: 找不到 run 记录 staging/%s/run.json；请先由 broker "
              "create_run 建立本轮任务清单" % args.run_id, file=sys.stderr)
        return 2
    try:
        run = load_json(run_path)
    except (OSError, json.JSONDecodeError) as exc:
        print("ERROR: run.json 不是合法 JSON: %s" % exc, file=sys.stderr)
        return 2

    brief_path = find_brief(project_root)
    if not brief_path:
        print("ERROR: 找不到 research/brief.json（冻结交接包）", file=sys.stderr)
        return 2
    try:
        brief = load_json(brief_path)
    except (OSError, json.JSONDecodeError) as exc:
        print("ERROR: brief.json 不是合法 JSON: %s" % exc, file=sys.stderr)
        return 2

    entry = run.get("tasks", {}).get(args.task_id)
    if entry is None:
        print("ERROR: 任务 %s 不在本轮 run 的任务清单中（任务清单来自冻结 "
              "brief，主 agent 不能临时增删）" % args.task_id, file=sys.stderr)
        return 2
    problem_id = entry["question_id"]

    if args.kind == "student-task":
        packet, err = build_student_task_packet(
            project_root, run, args.task_id, brief, problem_id)
    else:
        packet, err = build_teacher_solution_packet(
            project_root, run, args.task_id, brief, problem_id)
    if err:
        print("ERROR: %s" % err, file=sys.stderr)
        return 2

    errs = contract.validate(packet, "packet")
    if errs:
        print("ERROR: packet 未通过 schema 校验: %s" % errs[0]["message"],
              file=sys.stderr)
        return 2

    out_rel = args.out or default_packet_path(project_root, args.run_id,
                                              args.task_id, args.kind)
    out_abs = contract.resolve_path(project_root, out_rel)
    allowed_prefix = contract.normalize_relpath(os.path.join(
        "staging", args.run_id, "packets"))
    norm_out = contract.normalize_relpath(out_rel)
    if out_abs is None or not (norm_out == allowed_prefix
                               or norm_out.startswith(allowed_prefix + "/")):
        print("ERROR: 输出路径必须落在 %s/ 内（拒绝任意项目外输出路径）: %s"
              % (allowed_prefix, out_rel), file=sys.stderr)
        return 2
    if len(contract.canonical_json(packet).encode("utf-8")) > PACKET_MAX_BYTES:
        print("ERROR: [PACKET_TOO_LARGE] %s 窄包大小超限: %d 字节 > 上限 %d。"
              "主 Agent 需进一步裁剪输入字段。"
              % (args.kind, packet["size_bytes"], PACKET_MAX_BYTES),
              file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(out_abs), exist_ok=True)
    with open(out_abs, "w", encoding="utf-8", newline="\n") as f:
        f.write(contract.canonical_json(packet))
        f.write("\n")
    print("已生成包：%s（%d 字节，packet_digest=%s）"
          % (out_rel, packet["size_bytes"], packet["packet_digest"][:16]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
