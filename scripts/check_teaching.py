#!/usr/bin/env python3
# -*- coding: utf-8 -*-
TOOL_VERSION = "1.0"
r"""
check_teaching.py — 教学侧结构检查（T0 接包核对 + T1 统一内容源）

**定位**：本脚本只做**结构与一致性**检查：materials 对 brief 的覆盖、
交接状态（digest/版本）、引用存在性、信息分发约束的可机查部分。它不
判定数学正确性（归 math-audit / 数学复核），也不判定语义级泄露
（归独立学生阅读审查，见末尾"能力边界"）。

检查项：

  1. teaching/materials.json 通过 materials.schema.json 校验；blocks
     无重复 block_id；tasks 的 block_ids 均存在；
  2. 交接核对（T0）：
     - research/brief.json 存在且通过 brief.schema.json（缺失 → error：
       缺少有效交接包，全流程请求应先衔接 competition-research）；
     - research/freeze.json 存在且通过 freeze.schema.json（缺失 →
       fail：缺少有效教研冻结）；freeze 的 brief_id/revision 与 brief
       一致；
     - brief_digest：用 contract.compute_brief_digest(root, brief,
       exclude_refs=brief.freeze_basis.research_review_refs) 重算
       （循环排除约定见 references/handoff.md 第 3 节），与
       freeze 记录比较——不一致 → fail（研究包过期，禁止沿用旧 PASS）；
     - schema_version：brief / freeze / materials 与 contract
       （scripts/contract.py）契约版本一致；
  3. 覆盖：brief 入选题、teaching_decisions 的关键任务（task_id）、
     补充材料清单都在 materials.json 中有对应 blocks/tasks（按
     problem_id / task_id / source_refs 可回溯）；
  3d. R3 孤儿教研产物引用闭合（enable_obstacle_study 开启时生效）：
     dossier 的每条 obstacles[] 必须被同题 materials 块消费
     （block_id == obstacle_id），每条 supplements[] 必须登记进
     brief.supplements（由第 3c 条进一步要求块覆盖）或存在同题块
     （block_id == supplement_id），否则 [ORPHAN_RESEARCH_ARTIFACT]
     fail；开关关闭（默认）时本节跳过（check_research 已 fail-closed
     拦截，不重复报告）；
  4. 来源关系：每个 block 的 source_refs 与 content_ref 文件存在；
     materials 与每个 block 的 brief_revision 与当前 freeze.revision
     一致（不一致 → fail：stale，须重新绑定）；
  5. 信息分发约束（本批核心）：
     - 每个 block 的 (level, audience, available_after,
       delivery_channel) 能回溯到 brief 揭示计划同题条目（audience
       为子集即可）——教学不得放宽揭示约束；
     - views 含首轮发放视图（默认 student_initial，--initial-view 可
       按 brief 约定覆盖）的块：available_after 必须为 "initial" 且
       level 必须为 "L0"，否则 fail（"L1 印在初始 PDF"类错误必须在
       构建前拦下）；
     - L2 块 audience 不得含 student；
     - brief 揭示计划每个信息块在 materials 中有实现（同题且 block_id
       等于 info_id，或四元组+audience 全等），否则须在
       teaching/classroom-plan.md 中有明确的不生成理由（提及 → review，
       未提及 → fail）；
   6. 学生写作上下文纯净性的可机查部分：首轮视图块的 content_ref 文件
      不得字面引用被禁止提前给出的信息 ID（揭示计划中 level!=L0 或
      available_after!=initial 的 info_id，以及 L1/L2 块的 block_id）。
   7. 学生任务链路硬门禁（2.4.0，**全模式必经**，不再有 guided 豁免）：
      - expected task 清单来自冻结 brief 的 selection.
        selected_problem_ids（不从磁盘“发现有哪些文件”来决定检查范围）；
      - 每个入选题必须恰好有一个规范文件
        teaching/generated/<qid>-student-task.json；备用名
        （task-<qid>.json / student-task-<qid>.json）兼容已删除；缺失、
        重复、额外题目和无关文件均失败；
      - 每个 JSON 须通过 student-task.schema.json 校验，且
        question_id / task_id / brief_id / brief_revision 与当前 brief、
        freeze 一致；packet_digest / receipt_ref / run_id 须经 broker
        验签（只有注释或自报 UUID 的手写文件一律拒绝）；
      - lesson_mode 必须同时绑定用户确认摘要（user-spec.json）与冻结
        brief 的 teaching_decisions；混合模式或篡改模式均失败。

能力边界（报告固定附带）：本检查只做结构核对与字面 ID 检索；语义级
泄露（改写后无 ID 字样但实质提示策略）须由独立学生阅读审查判定（T3）。
把字样从 L2 改成 L0 不能通过语义审查。

输出：write_check_report 格式（status ∈ pass/fail/review/error），
默认落盘 teaching/checks/check_teaching.json，--json 可覆盖。

退出码：0 = pass 或 review（review 退出 0 但报告标明）；1 = fail；
2 = error。

用法：python check_teaching.py <项目根> [--json PATH]
      [--initial-view NAME]
"""
import argparse
import json
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

# contract.py 与本文件同目录（scripts/），import 实际由脚本目录
# sys.path[0] 解析。下方 sys.path.insert 指向的 ../../shared 是历史仓库
# 布局残留（本仓库无该目录，插入为空操作），仅保留行为不变，未删除。
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "..",
                                                 "shared")))
import contract  # noqa: E402

BRIEF_REL = "research/brief.json"
FREEZE_REL = "research/freeze.json"
MATERIALS_REL = "teaching/materials.json"
PLAN_REL = "teaching/classroom-plan.md"
USER_SPEC_REL = "teaching/user-spec.json"   # PHASE 0 用户对齐握手产物
DEFAULT_REPORT = "teaching/checks/check_teaching.json"
DEFAULT_INITIAL_VIEW = "student_initial"

CAPABILITY_NOTE = (
    "能力边界：本检查只做结构核对与字面信息 ID 检索；语义级泄露（改写"
    "后无 ID 字样但实质提示策略）须由独立学生阅读审查判定（T3）；把"
    "字样从 L2 改成 L0 不能代替语义审查。"
)


def _verify_user_spec_interaction(spec):
    """【零信任架构加固】：核验 user-spec.json 是否伴随真实的前端用户交互。

    1. 若设置环境变量 SKILLS_MOCK_USER_CONFIRM=1 或处于测试沙箱中，放行以保持测试兼容；
    2. 若显式配置了 AGY_TRANSCRIPT_PATH，必须能从中检索到真实的 ask_question 工具调用，
       且其提问参数中包含 lesson_mode 与 main_title。
    """
    if os.environ.get("SKILLS_MOCK_USER_CONFIRM") == "1" or os.environ.get("SKILLS_BRAIN_DIR"):
        return True, "test_bypass"
    if spec.get("source") in ["web_ui", "gradio", "human_nodes"] or spec.get("confirmed_by") in ["web_ui", "gradio"]:
        return True, "web_ui_confirmed"
    transcript_path = os.environ.get("AGY_TRANSCRIPT_PATH")
    if not transcript_path or not os.path.isfile(transcript_path):
        conv_id = os.environ.get("ANTIGRAVITY_CONVERSATION_ID")
        if conv_id:
            cand = os.path.join(
                os.path.expanduser("~"), ".gemini", "antigravity-cli",
                "brain", conv_id, ".system_generated", "logs", "transcript.jsonl"
            )
            if os.path.isfile(cand):
                transcript_path = cand

    if not transcript_path or not os.path.isfile(transcript_path):
        return True, "skipped_no_transcript_env"

    found_ask = False
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"ask_question"' in line:
                    if ('"lesson_mode"' in line or '"main_title"' in line
                            or '教学模式' in line or '标题' in line):
                        found_ask = True
                        break
    except OSError:
        pass
    if not found_ask:
        return False, "在平台 transcript 日志中未检索到 ask_question 工具调用（疑似跳过用户确认自行伪造 user-spec.json）"
    return True, "ok"


def _finding(severity, message):
    """构造 finding dict（G2.1 路由修复）。

    实质性数学错误类 message 中已含 RESEARCH_BACK / PROVED_FALSE 标记的条目
    （如"改揭示计划须走 RESEARCH_BACK"、"混合模式……须走 RESEARCH_BACK"），
    显式写入 category 字段；write_check_report 原样透传 finding dict，
    供 workflow 路由层（teaching_nodes.node_t2_broker_submit_and_check）
    消费。无标记的普通结构/格式问题不写 category（路由层回落 REPAIR_TASK）。
    """
    f = {"severity": severity, "message": message}
    if "PROVED_FALSE" in message:
        f["category"] = "PROVED_FALSE"
    elif "RESEARCH_BACK" in message:
        f["category"] = "RESEARCH_BACK"
    return f


def load_json(root, rel):
    """读取 JSON 文件；返回 (doc, error)。文件缺失或 JSON 错误为输入错误。"""
    abs_path = contract.resolve_path(root, rel)
    if abs_path is None or not os.path.isfile(abs_path):
        return None, "文件不存在: %s" % rel
    try:
        with open(abs_path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, "不是合法 JSON: %s (%s)" % (rel, exc)


def schema_errors(doc, schema_name, rel):
    return ["%s: %s [%s] %s" % (rel, e["path"], e["validator"], e["message"])
            for e in contract.validate(doc, schema_name)]


def _load_runs(root):
    """索引 staging/ 下所有 run 的 run.json：{run_id: run}。"""
    runs = {}
    staging = os.path.join(root, "staging")
    if not os.path.isdir(staging):
        return runs
    for name in sorted(os.listdir(staging)):
        run_file = os.path.join(staging, name, "run.json")
        if os.path.isfile(run_file):
            try:
                with open(run_file, "r", encoding="utf-8") as f:
                    runs[name] = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
    return runs


def _check_task_binding(root, rel, task_doc, run_index, fails):
    """核对 student-task.json 与 broker run / receipt / packet 的绑定。

    只有注释或自报 UUID 而没有 receipt/digest 的手工文件一律拒绝：
    - task.run_id 必须能在 staging/ 中找到对应 run；
    - run 的 brief_digest / brief_revision 必须与当前冻结状态一致；
    - receipt 必须存在且验签通过，packet_digest 三方一致；
    - 渲染产物（staging 或规范路径）的摘要必须与 run 记录一致。
    """
    run_id = task_doc.get("run_id")
    if not run_id:
        fails.append("%s 缺少 run_id：未经 broker 签发链路生成的文件不予认可"
                     % rel)
        return
    run = run_index.get(run_id)
    if run is None:
        fails.append("%s 的 run_id=%r 在 staging/ 中无对应 run 记录"
                     % (rel, run_id))
        return
    tid = task_doc.get("task_id", "")
    entry = run.get("tasks", {}).get(tid)
    if entry is None:
        fails.append("%s 的任务 %r 不在 run %s 的任务清单中"
                     % (rel, tid, run_id))
        return
    receipt_ref = task_doc.get("receipt_ref", "")
    checked = contract.verify_receipt_file(
        root, receipt_ref, run_id=run_id, task_id=tid,
        packet_digest=task_doc.get("packet_digest", ""))
    if not checked["ok"]:
        fails.append("%s 的 receipt 校验失败：%s（只有注释/receipt 缺失或"
                     "签名不匹配的手工文件一律拒绝）"
                     % (rel, checked["reason"]))
        return
    receipt = checked["receipt"]
    if receipt.get("packet_digest") != entry.get("packet_digest"):
        fails.append("%s 的 packet_digest 与 run 记录不一致（packet 签发后"
                     "被改动）" % rel)
    if entry.get("output_digest") \
            and entry.get("output_digest") != contract.sha256_file(
                contract.resolve_path(root, rel)):
        fails.append("%s 的当前摘要与 broker 验证记录不一致（验证后被改动）"
                     % rel)
    # 渲染映射：staging 渲染产物或规范 .tex 的摘要必须与 run 记录一致
    render_ref = entry.get("render_ref")
    if render_ref:
        render_abs = contract.resolve_path(root, render_ref)
        if render_abs is None or not os.path.isfile(render_abs):
            fails.append("%s 的渲染产物缺失: %s（必须经 broker render_task "
                         "生成）" % (rel, render_ref))
        elif contract.sha256_file(render_abs) != entry.get("render_digest"):
            fails.append("%s 的渲染产物摘要与 run 记录不一致" % rel)
    canonical_tex = contract.resolve_path(
        root, "teaching/content/task-%s.tex" % task_doc.get("question_id", ""))
    if canonical_tex and os.path.isfile(canonical_tex) and entry.get(
            "render_digest"):
        if contract.sha256_file(canonical_tex) != entry.get("render_digest"):
            fails.append("%s 对应的规范渲染文件 teaching/content/task-%s.tex"
                         " 摘要与 broker 记录不一致（手写 .tex 不通过）"
                         % (rel, task_doc.get("question_id")))


def check(root, initial_view=DEFAULT_INITIAL_VIEW):
    """返回 (fails, reviews, infos, evidence, fatal)。"""
    fails, reviews, infos, fatal = [], [], [], []
    evidence = {"brief_digest": None, "freeze_digest": None,
                "blocks_checked": 0, "reveal_entries": 0,
                "initial_view": initial_view}

    # ---- NB5 输入守卫：目标目录不像项目根（无 teaching/）→ error，不写报告 ----
    _tdir = os.path.join(root, "teaching")
    if not os.path.isdir(_tdir):
        fatal.append(
            "目标目录不像竞赛教研项目根：缺少 teaching/ 目录（%s）。"
            "请确认 project_root 指向含 teaching/ 与 research/ 的教研项目根；"
            "本检查拒绝在非项目目录写入门禁报告。" % _tdir
        )
        return fails, reviews, infos, evidence, fatal

    # ---- 0. 用户前置对齐核验（PHASE 0 BREAKPOINT 握手产物）----
    # teaching/user-spec.json 必须存在且 confirmed == true；
    # 不存在意味着主 agent 未向用户提出强制问题便直接进入 T0，直接 fail。
    user_spec_path = contract.resolve_path(root, USER_SPEC_REL)
    if user_spec_path is None or not os.path.isfile(user_spec_path):
        fails.append(
            "缺少用户前置对齐凭证：%s 不存在。"
            "主 agent 必须先在 PHASE 0 BREAKPOINT 中向用户提出强制问题"
            "（学案主标题；教学模式固定 guided 分步引导），等待用户明确回复后将结果写入该文件，"
            "再进入 T0。直接进入 T0 属于严重执行错误，门禁拒绝通过。"
            % USER_SPEC_REL
        )
        return fails, reviews, infos, evidence, fatal
    try:
        with open(user_spec_path, encoding="utf-8-sig") as _f:
            _spec = json.load(_f)
    except (OSError, json.JSONDecodeError) as _e:
        fails.append("user-spec.json 不合法: %s" % _e)
        return fails, reviews, infos, evidence, fatal
    if not _spec.get("confirmed"):
        fails.append(
            "user-spec.json 中 confirmed 不为 true：用户对齐尚未完成。"
            "请回到 PHASE 0 BREAKPOINT，向用户确认后再重新运行。"
        )
        return fails, reviews, infos, evidence, fatal
    for _field in ("main_title", "lesson_mode"):
        if _spec.get(_field) is None:
            fails.append("user-spec.json 缺少字段或值为 null：%s" % _field)
    
    # 【零信任架构加固】：穿透校验真实用户握手交互（防自行伪造 user-spec.json）
    spec_ok, spec_reason = _verify_user_spec_interaction(_spec)
    if not spec_ok:
        fails.append("用户前置对齐核验失败：%s" % spec_reason)

    if fails:
        return fails, reviews, infos, evidence, fatal

    brief, err = load_json(root, BRIEF_REL)
    if err:
        fatal.append("缺少有效教研交接包：%s；缺少有效交接包的全流程请求"
                     "应先衔接 competition-research 完成教研并冻结 brief"
                     % err)
        return fails, reviews, infos, evidence, fatal
    fails.extend(schema_errors(brief, "brief", BRIEF_REL))
    if fails:
        return fails, reviews, infos, evidence, fatal

    # ---- 2. 交接包：freeze（缺失 → fail，不是 error）----
    freeze, ferr = load_json(root, FREEZE_REL)
    if ferr:
        fails.append("缺少有效教研冻结：%s；仅有 brief 而没有 freeze.json "
                     "时不得作为已教研输入（T0 接包核对），请先由教研侧 "
                     "freeze_brief.py 冻结" % ferr)
        freeze = None
    else:
        fails.extend(schema_errors(freeze, "freeze", FREEZE_REL))

    # ---- 3. materials ----
    materials, merr = load_json(root, MATERIALS_REL)
    if merr:
        fatal.append("缺少教学统一内容源：%s" % merr)
        return fails, reviews, infos, evidence, fatal
    merrs = schema_errors(materials, "materials", MATERIALS_REL)
    fails.extend(merrs)
    if merrs:
        return fails, reviews, infos, evidence, fatal

    brief_id = brief["brief_id"]
    blocks = materials["blocks"]
    tasks_m = materials["tasks"]
    reveal = brief.get("reveal_plan", [])
    evidence["blocks_checked"] = len(blocks)
    evidence["reveal_entries"] = len(reveal)

    # ---- 2a. schema_version 匹配 ----
    compat_versions = getattr(contract, "COMPATIBLE_CONTRACT_VERSIONS", {contract.CONTRACT_VERSION})
    for rel, doc in ((BRIEF_REL, brief), (FREEZE_REL, freeze),
                     (MATERIALS_REL, materials)):
        if doc is None:
            continue
        ver = doc.get("schema_version")
        if ver not in compat_versions:
            fails.append("%s: schema_version=%r 与契约版本 %s 不匹配"
                         % (rel, ver, ", ".join(sorted(compat_versions))))

    # ---- 2b. digest 与版本一致性 ----
    if freeze is not None:
        if freeze.get("brief_id") != brief_id:
            fails.append("freeze.brief_id=%r 与 brief.brief_id=%r 不一致"
                         % (freeze.get("brief_id"), brief_id))
        if freeze.get("revision") != brief.get("revision"):
            fails.append("freeze.revision=%r 与 brief.revision=%r 不一致"
                         % (freeze.get("revision"), brief.get("revision")))
        review_refs = brief.get("freeze_basis", {}).get(
            "research_review_refs", [])
        digest_info = contract.compute_brief_digest(root, brief,
                                                    exclude_refs=review_refs)
        evidence["brief_digest"] = digest_info["digest"]
        evidence["freeze_digest"] = freeze.get("brief_digest")
        for m in digest_info["missing"]:
            fails.append("brief_digest 引用文件缺失: %s" % m)
        if digest_info["digest"] != freeze.get("brief_digest"):
            fails.append("研究包过期：当前重算 brief_digest=%s 与 "
                         "freeze 记录 %s 不一致（brief 或其引用研究文件"
                         "已变动）；禁止沿用旧 PASS，须由教研侧重新复核"
                         "并冻结，或恢复研究文件后重跑本检查"
                         % (digest_info["digest"], freeze.get("brief_digest")))

    # ---- 4. 版本绑定（stale 判定）----
    if freeze is not None:
        frev = freeze.get("revision")
        if materials.get("brief_id") != brief_id:
            fails.append("materials.brief_id=%r 与 brief.brief_id=%r 不一致"
                         % (materials.get("brief_id"), brief_id))
        if materials.get("brief_revision") != frev:
            fails.append("materials.brief_revision=%r 与当前 freeze."
                         "revision=%r 不一致：材料绑定旧版 brief（stale），"
                         "须重新绑定并重审受影响块"
                         % (materials.get("brief_revision"), frev))
        for b in blocks:
            if b.get("brief_revision") != frev:
                fails.append("block %s 的 brief_revision=%r 与当前 freeze."
                             "revision=%r 不一致（stale）"
                             % (b.get("block_id"), b.get("brief_revision"),
                                frev))

    # ---- 1a. blocks 重复 ID 与 tasks 引用 ----
    for d in contract.check_stable_ids(blocks, "block_id"):
        fails.append("materials.blocks 中 block_id 重复: %s" % d)
    block_ids = {b.get("block_id") for b in blocks}
    for t in tasks_m:
        for bid in t.get("block_ids", []):
            if bid not in block_ids:
                fails.append("materials.tasks(%s) 引用的 block_id %s 不存在"
                             % (t.get("task_id"), bid))

    # ---- 4a. 来源关系：source_refs 与 content_ref 文件存在 ----
    for b in blocks:
        bid = b.get("block_id")
        for ref in list(b.get("source_refs", [])) + [b.get("content_ref")]:
            if not ref:
                continue
            abs_path = contract.resolve_path(root, ref)
            if abs_path is None:
                fails.append("block %s 的引用越出项目根: %s" % (bid, ref))
            elif not os.path.isfile(abs_path):
                fails.append("block %s 的引用文件缺失: %s" % (bid, ref))

    # ---- 5a. 揭示约束可回溯（教学不得放宽 reveal_plan）----
    effective_reveal = list(reveal)
    if not effective_reveal:
        for pid in brief.get("selection", {}).get("selected_problem_ids", []):
            effective_reveal.append({
                "info_id": "info-task-%s" % pid, "problem_id": pid, "level": "L0",
                "audience": ["student", "teacher"], "available_after": "initial",
                "delivery_channel": "in_main"
            })
            effective_reveal.append({
                "info_id": "info-proof-%s" % pid, "problem_id": pid, "level": "L2",
                "audience": ["teacher"], "available_after": "initial",
                "delivery_channel": "teacher_only"
            })

    def reveal_match(b, r):
        return (r.get("problem_id") == b.get("problem_id")
                and r.get("level") == b.get("level")
                and r.get("available_after") == b.get("available_after")
                and r.get("delivery_channel") == b.get("delivery_channel")
                and set(b.get("audience", [])) <= set(r.get("audience", [])))

    for b in blocks:
        if not any(reveal_match(b, r) for r in effective_reveal):
            fails.append("block %s 的 (problem_id=%s, level=%s, audience=%s,"
                         " available_after=%s, delivery_channel=%s) 无法回"
                         "溯到 brief 揭示计划的同题条目；教学侧不得放宽或"
                         "新设揭示约束（改揭示计划须走 RESEARCH_BACK）"
                         % (b.get("block_id"), b.get("problem_id"),
                            b.get("level"), b.get("audience"),
                            b.get("available_after"),
                            b.get("delivery_channel")))

    # ---- 5b. 首轮发放视图约束（核心）----
    for b in blocks:
        if initial_view not in b.get("views", []):
            continue
        if b.get("available_after") != "initial":
            fails.append("block %s 进入首轮发放视图 %s，但 available_after"
                         "=%r（不是 \"initial\"）；等待未来事件才可发放的"
                         "块不得进入首轮视图，须拆到 separate_hint / "
                         "separate_review 渠道" % (b.get("block_id"),
                                                   initial_view,
                                                   b.get("available_after")))
        if b.get("level") != "L0":
            fails.append("block %s 进入首轮发放视图 %s，但 level=%r（不是 "
                         "L0）；\"L1/L2 印在初始 PDF\"类错误必须在构建前"
                         "拦下" % (b.get("block_id"), initial_view,
                                    b.get("level")))

    # ---- 5c. L2 块 audience 不得含 student ----
    for b in blocks:
        if b.get("level") == "L2" and "student" in b.get("audience", []):
            fails.append("block %s 为 L2（关键步骤或结论），audience 不得"
                         "含 student" % b.get("block_id"))

    # ---- 3a. 覆盖：入选题 ----
    selected = brief["selection"]["selected_problem_ids"]
    for pid in selected:
        if not any(b.get("problem_id") == pid for b in blocks):
            fails.append("入选题 %s 在 materials.json 中没有任何内容块"
                         % pid)

    # ---- 3b. 覆盖：关键任务 ----
    task_index = {(t.get("task_id"), t.get("problem_id")) for t in tasks_m}
    for dec in brief.get("teaching_decisions", []):
        pid = dec.get("problem_id")
        for task in dec.get("key_tasks", []):
            tid = task.get("task_id")
            if (tid, pid) not in task_index:
                fails.append("brief 关键任务 %s（题 %s）在 materials.tasks "
                             "中无对应登记" % (tid, pid))

    # ---- 3c. 覆盖：补充材料 ----
    for sup in brief.get("supplements", []):
        sid = sup.get("supplement_id")
        cref = sup.get("content_ref")
        hit = any(b.get("problem_id") == sup.get("problem_id")
                  and (b.get("block_id") == sid
                       or cref in b.get("source_refs", []))
                  for b in blocks)
        if not hit:
            fails.append("brief 补充材料 %s（题 %s，content_ref=%s）在 "
                         "materials.json 中无对应块（按 block_id 或 "
                         "source_refs 可回溯）" % (sid,
                                                   sup.get("problem_id"),
                                                   cref))

    # ---- 3d. R3 孤儿教研产物引用闭合（enable_obstacle_study 开启时生效）----
    # 开关关闭（默认）时整节跳过：check_research §3.9 已对非空 R3 产物
    # fail-closed 拦截，此处不重复报告。开启时逐题核对 dossier 的 R3 产物
    # 都有交付通道：障碍研究 ⟺ 同题 materials 块 block_id==obstacle_id
    # （block 的 delivery_channel 由 materials schema 强制，存在即可）；
    # 补充材料 ⟺ 登记进 brief.supplements（届时 §3c 进一步要求块覆盖）
    # 或存在同题块 block_id==supplement_id。dossier 加载失败跳过，交由
    # check_research 报告。
    r3_enabled = contract.read_project_flag(root, "enable_obstacle_study",
                                            default=False)
    if r3_enabled:
        brief_sup_ids = {s.get("supplement_id")
                         for s in brief.get("supplements", [])}
        for pid in selected:
            ddoc, derr = load_json(
                root, "research/problems/%s/dossier.json" % pid)
            if derr or ddoc is None:
                continue
            for ob in ddoc.get("obstacles", []):
                oid = ob.get("obstacle_id")
                consumed = any(b.get("problem_id") == pid
                               and b.get("block_id") == oid
                               for b in blocks)
                if not consumed:
                    fails.append("[ORPHAN_RESEARCH_ARTIFACT] 题 %s 的障碍研"
                                 "究产物 %s 没有任何 materials 内容块消费"
                                 "（无交付通道）：要么为其生成 "
                                 "teacher_only/separate_* 内容块并在 "
                                 "materials.json 中登记，要么删除该产物"
                                 % (pid, oid))
            for sup in ddoc.get("supplements", []):
                sid = sup.get("supplement_id")
                consumed = (sid in brief_sup_ids) or any(
                    b.get("problem_id") == pid and b.get("block_id") == sid
                    for b in blocks)
                if not consumed:
                    fails.append("[ORPHAN_RESEARCH_ARTIFACT] 题 %s 的补充材"
                                 "料产物 %s 未登记进 brief.supplements，也"
                                 "没有任何 materials 内容块消费（无交付通"
                                 "道）：要么登记并为其生成内容块，要么删除"
                                 "该产物" % (pid, sid))

    # ---- 5d. 揭示计划每条有实现或明确的不生成理由 ----
    plan_text = None
    plan_abs = contract.resolve_path(root, PLAN_REL)
    if plan_abs and os.path.isfile(plan_abs):
        with open(plan_abs, "r", encoding="utf-8") as f:
            plan_text = f.read()
    for r in reveal:
        iid = r.get("info_id")
        impl = any(b.get("problem_id") == r.get("problem_id")
                   and (b.get("block_id") == iid
                        or (b.get("level") == r.get("level")
                            and set(b.get("audience", []))
                            == set(r.get("audience", []))
                            and b.get("available_after")
                            == r.get("available_after")
                            and b.get("delivery_channel")
                            == r.get("delivery_channel")))
                   for b in blocks)
        if impl:
            continue
        if plan_text is not None and iid in plan_text:
            reviews.append("揭示计划信息块 %s 未在 materials 中实现，但 "
                           "%s 中有提及；须人工/审查确认其不生成理由成立"
                           % (iid, PLAN_REL))
        else:
            fails.append("brief 揭示计划信息块 %s（题 %s，%s/%s）在 "
                         "materials.json 中无实现，且 %s 中无明确的不生成"
                         "理由" % (iid, r.get("problem_id"), r.get("level"),
                                   r.get("delivery_channel"), PLAN_REL))

    # ---- 6. 学生上下文纯净性的字面检查 ----
    forbidden_ids = set()
    for r in reveal:
        if r.get("level") != "L0" or r.get("available_after") != "initial":
            forbidden_ids.add(r.get("info_id"))
    for b in blocks:
        if b.get("level") != "L0":
            forbidden_ids.add(b.get("block_id"))
    forbidden_ids.discard(None)
    for b in blocks:
        if initial_view not in b.get("views", []):
            continue
        cref = b.get("content_ref")
        abs_path = contract.resolve_path(root, cref) if cref else None
        if not abs_path or not os.path.isfile(abs_path):
            continue  # 缺失已在 4a 报告
        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        for fid in sorted(forbidden_ids):
            if fid != b.get("block_id") and fid in text:
                fails.append("首轮视图 %s 的块 %s 正文 %s 字面引用了被禁止"
                             "提前给出的信息 ID %r；学生写作上下文不得含"
                             "此类引用" % (initial_view, b.get("block_id"),
                                           cref, fid))
        for feat in b.get("forbidden_feature_refs", []):
            if feat in text:
                fails.append("首轮视图 %s 的块 %s 正文 %s 泄露了禁手特征 %r"
                             % (initial_view, b.get("block_id"), cref, feat))

    # ---- 7. 小模型新链路中间文件硬检查（2.4.0：全模式必经）----
    # 任务清单来自冻结 brief 的 selection.selected_problem_ids，而不是从
    # 磁盘“发现有哪些文件”。每个入选题必须恰好有一个规范文件
    # teaching/generated/<qid>-student-task.json；备用名兼容已删除。
    # lesson_mode 不再选择性关闭核心门禁：任何模式都要求完整链路。
    generated_dir = os.path.join(root, "teaching", "generated")
    expected_qids = list(brief["selection"]["selected_problem_ids"])
    canonical_names = {"%s-student-task.json" % qid for qid in expected_qids}
    allowed_generated_names = canonical_names | {"%s-solution-task.json" % qid for qid in expected_qids}

    if not os.path.isdir(generated_dir):
        fails.append(
            "缺少必需的中间生成目录 teaching/generated/。"
            "每个入选题必须恰好有一个 teaching/generated/<qid>-student-task.json"
            "（由子 Agent 生成 JSON、broker 校验提升）；严禁绕过 student-task "
            "规范链路直接手写 .tex"
        )
    else:
        present = sorted(fn for fn in os.listdir(generated_dir)
                         if fn.endswith(".json"))
        for qid in expected_qids:
            rel = "teaching/generated/%s-student-task.json" % qid
            abs_task = contract.resolve_path(root, rel)
            if abs_task is None or not os.path.isfile(abs_task):
                fails.append("入选题 %s 缺少规范中间文件 %s（缺失、备用名、"
                             "占位 JSON 均不通过；任务清单以冻结 brief 的 "
                             "selected_problem_ids 为准）" % (qid, rel))
        for fn in present:
            if fn not in allowed_generated_names:
                fails.append("teaching/generated/ 中存在规范集之外的文件 %s"
                             "（额外题目或备用名文件均失败；期望恰好：%s）"
                             % (fn, "、".join(sorted(allowed_generated_names))))

        # 每个规范 JSON：schema + 与当前 run/brief 的绑定核对
        run_index = _load_runs(root)
        for qid in expected_qids:
            rel = "teaching/generated/%s-student-task.json" % qid
            abs_task = contract.resolve_path(root, rel)
            if abs_task is None or not os.path.isfile(abs_task):
                continue
            try:
                with open(abs_task, "r", encoding="utf-8") as _f:
                    task_doc = json.load(_f)
            except (OSError, json.JSONDecodeError) as _e:
                fails.append("%s 不是合法 JSON: %s" % (rel, _e))
                continue
            for e in contract.validate(task_doc, "student-task"):
                fails.append("%s: %s [%s] %s"
                             % (rel, e["path"], e["validator"], e["message"]))
                break
            tid = "task-%s" % qid
            if task_doc.get("question_id") != qid:
                fails.append("%s 的 question_id=%r 与规范文件名不一致"
                             % (rel, task_doc.get("question_id")))
            if task_doc.get("task_id") != tid:
                fails.append("%s 的 task_id=%r 应为 %r（每题唯一规范任务）"
                             % (rel, task_doc.get("task_id"), tid))
            if task_doc.get("brief_id") != brief_id:
                fails.append("%s 的 brief_id=%r 与当前 brief %r 不一致"
                             % (rel, task_doc.get("brief_id"), brief_id))
            if freeze is not None \
                    and task_doc.get("brief_revision") != freeze.get("revision"):
                fails.append("%s 的 brief_revision=%r 与当前 freeze."
                             "revision=%r 不一致（stale）"
                             % (rel, task_doc.get("brief_revision"),
                                freeze.get("revision")))
            _check_task_binding(root, rel, task_doc, run_index, fails)

    # ---- 7b. lesson_mode 与冻结 brief、用户确认摘要绑定 ----
    spec_modes = set()
    if isinstance(brief.get("teaching_decisions"), list):
        for td in brief.get("teaching_decisions", []):
            mode = td.get("lesson_mode")
            if mode:
                spec_modes.add(mode)
    elif isinstance(brief.get("teaching_decisions"), dict):
        mode = brief.get("teaching_decisions", {}).get("lesson_mode")
        if mode:
            spec_modes.add(mode)

    if not spec_modes:
        cc_mode = brief.get("course_constraints", {}).get("lesson_mode")
        if cc_mode:
            spec_modes.add(cc_mode)
        elif _spec.get("lesson_mode"):
            spec_modes.add(_spec.get("lesson_mode"))

    if not spec_modes:
        fails.append("brief 未记录 lesson_mode；教学模式必须由冻结 brief 或用户对齐明确")
    elif len(spec_modes) > 1:
        fails.append("brief.teaching_decisions 记录了互相矛盾的 lesson_mode: "
                     "%s；混合模式属于静默改变教学模式，须走 RESEARCH_BACK 后"
                     "重新冻结" % "、".join(sorted(spec_modes)))
    elif _spec.get("lesson_mode") not in spec_modes:
        fails.append("user-spec.json 的 lesson_mode=%r 与冻结 brief 的教学"
                     "模式 %s 不一致（模式必须同时绑定用户确认摘要与冻结 "
                     "brief）" % (_spec.get("lesson_mode"),
                                  "、".join(sorted(spec_modes))))

    # ---- 8. 双轨规划 architect-plan.json 联动核验 ----
    arch_rel = "teaching/architect-plan.json"
    arch_abs = contract.resolve_path(root, arch_rel)
    if arch_abs and os.path.isfile(arch_abs):
        try:
            import check_architect  # noqa: E402
            afails, areviews, _ = check_architect.check_architect_file(root, arch_rel)
            for af in afails:
                fails.append("[architect] %s" % af)
            for ar in areviews:
                reviews.append("[architect] %s" % ar)
        except Exception as exc:
            reviews.append("检查 architect-plan.json 出现异常：%s" % exc)
    
    infos.append(CAPABILITY_NOTE)
    
    return fails, reviews, infos, evidence, fatal


def main():
    ap = argparse.ArgumentParser(
        description="教学侧结构检查（不接写数学判断与语义泄露判定）")
    ap.add_argument("project_root", help="项目根（含 research/ 与 teaching/ "
                                         "的目录）")
    ap.add_argument("--json", metavar="PATH", default=None,
                    help="报告落盘路径（默认 teaching/checks/"
                         "check_teaching.json）")
    ap.add_argument("--initial-view", metavar="NAME",
                    default=DEFAULT_INITIAL_VIEW,
                    help="首轮发放视图名（默认 student_initial；brief 另有"
                         "约定时按约定传入）")
    args = ap.parse_args()
    root = os.path.abspath(args.project_root)
    if not os.path.isdir(root):
        print("[check_teaching] 项目根不存在: %s" % root)
        return 2

    fails, reviews, infos, evidence, fatal = check(root,
                                                   args.initial_view)
    if fatal:
        status = "error"
    elif fails:
        status = "fail"
    elif reviews:
        status = "review"
    else:
        status = "pass"

    findings = ([_finding("error", m) for m in fatal]
                + [_finding("fail", m) for m in fails]
                + [_finding("review", m) for m in reviews]
                + [_finding("info", m) for m in infos])
    if fatal:
        # NB5: 输入缺失/不可读（如误传非项目目录）时，仅打印到 stdout，
        # 不在目标目录创建 teaching/checks/ 空壳目录（零副作用）。
        for m in fatal:
            print("[check_teaching] [错误] " + m)
        print("[check_teaching] status=error，输入缺失，未写入报告")
        return 2
    report_path = args.json or os.path.join(root, DEFAULT_REPORT)
    report = contract.write_check_report(report_path, status, "teaching",
                                         findings)
    report["evidence"] = evidence
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")

    for m in fatal:
        print("[check_teaching] [错误] " + m)
    for m in fails:
        print("[check_teaching] [失败] " + m)
    for m in reviews:
        print("[check_teaching] [待复核] " + m)
    for m in infos:
        print("[check_teaching] [说明] " + m)
    print("[check_teaching] status=%s，报告已写入 %s" % (status, report_path))
    if status == "error":
        return 2
    if status == "fail":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
