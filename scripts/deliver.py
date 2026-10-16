#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
deliver.py — 交付总门禁（T4；meet.md B6 八条逐项核对）

固定检查顺序（meet.md B6，2.4.0 起接入统一学生任务门禁）：

  check_research -> 校验 freeze
  -> check_architect / check_teaching / check_student_task
  / check_student_visibility / check_numbering（纯读取，并行）
  -> build -> compare_versions(PDF 比对) -> make_review_pack
  -> 独立审查记录核对（student_read / teacher_use / math_recheck）
  -> deliver 判定

纯读取无依赖的检查并行执行；构建与读取正在生成的日志/PDF 串行
（build -> compare -> make_review_pack 严格按序）。

**报告结论与脚本状态分开**（references/handoff.md 第 4 节）：
脚本 pass 只是结构检查通过，不自动构成审查 PASS；审查结论
PASS/REVISE/NEEDS_EVIDENCE 由独立审查者凭材料证据给出；本脚本读取
各报告的 status 字段判定，不只看进程退出码。

deliver 逐项核对（meet.md B6 八条）：

  1. 交接包当前摘要与研究冻结记录一致（contract.compute_brief_digest
     + exclude_refs 循环排除约定重算，对比 freeze.json 的
     brief_digest 与 dependency_digests）；
  2. 教学正文绑定同一 brief（materials.brief_revision ==
     freeze.revision），实际构建对应当前 production_digest
     （teaching/build/build.json 构建记录）；
  3. 入选题、补充材料和跨题设计都有审查覆盖：三 scope
     （student_read / teacher_use / math_recheck）各有绑定当前
     production_digest 的 PASS 记录，且 coverage 并集覆盖 brief 全部
     selected_problem_ids + supplements + brief_id（跨题设计）；
  4. issues/ 下所有 issue 逐项有具体处理与复验引用：open/accepted
     拒绝；resolved 须有非空 resolution_log 且复验引用文件存在；
     rejected 须有拒绝理由记录；
  5. review 级疑似项逐项处置：脚本报告的 review 级 finding 须在
     review-notes 中有逐条对应的处置记录，非空文件概括不放行；
  6. 各报告覆盖自己的 scope：research/teaching/qid/可见性/版面各
     环节报告齐全且可解读，任一局部 PASS 不能代表全课通过；
   7. 缺依赖、跳过构建或证据不齐时，不产出宣称完成的发布记录：
      --skip-build 只允许技术测试场景，仅写 deliver 报告（result=
      technical_test_only，且报告 status 不得为 pass），**不得创建、
      覆盖或更新 release/manifest.json，也不得复制正式 release PDF**；
      正式交付必须在干净工作区不带该参数重跑；
  8. pilot_status 可为 not_run，不阻止交付。

通过后生成 release/：student.pdf、teacher.pdf、release-later/（有
separate_hint / separate_review 产物才生成）、manifest.json（含
brief_digest / production_digest / build_digest / 审查记录引用 /
pilot_status / schema_version）。

摘要失效按依赖计算（contract.affected_set）：改动版面只使对应构建与
版面检查失效；教学措辞使该内容块审查失效；题面/证明/模式使 brief 与
依赖它的教学审查失效。摘要比对不一致时，本脚本列出具体变动的引用
文件与受影响节点，主 agent 按依赖重做，不无限重跑全部 agent。

退出码：0=交付成功，1=拒绝（逐项列出拒绝原因），2=用法错误/输入错误。

 用法：python deliver.py <项目根> [--skip-build] [--json PATH]
       [--pilot-status STATUS] [--run-id RUN_ID]
       python deliver.py --verify-manifest <manifest.json>（G7.2 验签，
       退出码 0=通过，1=失败）
"""
import argparse
import concurrent.futures
import datetime
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_SCHEMAS_DIR = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "schemas"))
sys.path.insert(0, os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "..",
                                                 "shared")))
sys.path.insert(0, _SCRIPT_DIR)
import contract  # noqa: E402
import make_review_pack as mrp  # noqa: E402  （复用 sec_names 与摘要口径）
from gen_config import load_config_full, validate_views  # noqa: E402

TOOL_VERSION = "1.0"

# check_research.py 的解析顺序（功能代码，勿删）：
# 优先并置的 competition-research 仓库 scripts/（多仓库布局）；
# 不存在时回退本目录（本仓库 scripts/ 自带 check_research.py）。
_RESEARCH_SCRIPTS = os.path.normpath(os.path.join(
    _SCRIPT_DIR, "..", "..", "competition-research", "scripts"))
if not os.path.isdir(_RESEARCH_SCRIPTS):
    _RESEARCH_SCRIPTS = _SCRIPT_DIR

BRIEF_REL = "research/brief.json"
FREEZE_REL = "research/freeze.json"
MATERIALS_REL = "teaching/materials.json"
BUILD_RECORD_REL = "teaching/build/build.json"
REVIEWS_DIR_REL = "teaching/reviews"
ISSUES_DIR_REL = "issues"
REVIEW_PACK_MANIFEST_REL = "teaching/review-pack/manifest.json"
DEFAULT_REPORT = "teaching/checks/deliver.json"

# G7.2：manifest 完整性签名字段（HMAC-SHA256，密钥口径与 broker 一致）
MANIFEST_HMAC_FIELD = "manifest_hmac"

# §2.2 review 级疑似项分类（check_review_dispositions 与 G7.1 阻断判定共用）：
# 高危分类须逐条处置；自动降级分类已降为 info，无需逐条处置。
REVIEW_HIGH_RISK_CATEGORIES = ("cross_problem_leak", "forbidden_feature_match",
                               "outline_imperative", "char_budget_warn",
                               "layout_warn")
REVIEW_AUTO_DOWNGRADE_CATEGORIES = ("boilerplate_overlap", "same_problem_reuse")

# 独立审查必需的三个 scope（结构与版面由脚本检查覆盖，见第 6 条）
REQUIRED_SCOPES = ("student_read", "teacher_use", "math_recheck")

# 纯读取检查（可并行）：(名称, 脚本绝对路径, 报告项目相对路径)
# 2.4.0：check_architect / check_student_visibility 为必经检查；
# 旧 check_small_outputs 的检查由 check_student_task 统一承担。
# check_student_task 不在本并行组：其版面核对依赖实际构建产物
# （student.log / student.pdf），必须在 build 之后串行运行。
def _read_checks():
    return [
        ("check_research",
         os.path.join(_RESEARCH_SCRIPTS, "check_research.py"),
         "research/checks/check_research.json"),
        ("check_architect",
         os.path.join(_SCRIPT_DIR, "check_architect.py"),
         "teaching/checks/check_architect.json"),
        ("check_teaching",
         os.path.join(_SCRIPT_DIR, "check_teaching.py"),
         "teaching/checks/check_teaching.json"),
        ("check_student_visibility",
         os.path.join(_SCRIPT_DIR, "check_student_visibility.py"),
         "teaching/checks/check_student_visibility.json"),
        ("check_numbering",
         os.path.join(_SCRIPT_DIR, "check_numbering.py"),
         "teaching/checks/check_numbering.json"),
        ("check_subquestion_solution",
         os.path.join(_SCRIPT_DIR, "check_subquestion_solution.py"),
         "teaching/checks/check_subquestion_solution.json"),
    ]

# 构建后串行检查（依赖实际 PDF/日志）
STUDENT_TASK_CHECK = ("check_student_task",
                      os.path.join(_SCRIPT_DIR, "check_student_task.py"),
                      "teaching/checks/check_student_task.json")

# 构建后串行检查
COMPARE_REPORT_REL = "teaching/checks/compare_versions.json"
BUILD_REPORT_REL = "build/build-report.json"

CONVERSION_NOTE = ("报告结论 PASS/REVISE/NEEDS_EVIDENCE 与脚本状态 "
                   "pass/fail/review/error 是两套词汇，转换规则见 "
                   "references/handoff.md 第 4 节；不能只看进程"
                   "退出码，也不能把脚本状态词写进审查记录。")


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def load_json(root, rel):
    abs_path = contract.resolve_path(root, rel)
    if abs_path is None or not os.path.isfile(abs_path):
        return None, "文件不存在: %s" % rel
    try:
        with open(abs_path, "r", encoding="utf-8-sig") as f:
            return json.load(f), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, "不是合法 JSON: %s (%s)" % (rel, exc)


def run_script(script, root, report_rel, use_json=True):
    """运行检查脚本并读取其结构化报告。返回 (exit_code, report_or_None,
    error_or_None)。判定以报告 status 字段为准，退出码仅作执行参考。
    use_json=False 时不传 --json（如 make_review_pack 的产物即其报告）。"""
    if not os.path.isfile(script):
        return None, None, "缺少依赖脚本: %s" % script
    cmd = [sys.executable, script, root]
    report_abs = None
    if use_json:
        report_abs = contract.resolve_path(root, report_rel)
        os.makedirs(os.path.dirname(report_abs), exist_ok=True)
        cmd += ["--json", report_abs]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    rep = None
    if report_abs and os.path.isfile(report_abs):
        try:
            with open(report_abs, encoding="utf-8-sig") as f:
                rep = json.load(f)
        except (OSError, json.JSONDecodeError):
            rep = None
    tail = (r.stdout or "").strip().splitlines()
    for line in tail[-2:]:
        print("  [%s] %s" % (os.path.basename(script), line))
    return r.returncode, rep, None


def report_status(rep):
    """统一读取报告状态与 findings（check_* 用 findings，build/compare
    用 issues）。"""
    if not isinstance(rep, dict):
        return None, []
    findings = rep.get("findings")
    if findings is None:
        findings = rep.get("issues", [])
    return rep.get("status"), findings


# ---------------------------------------------------------------------------
# G7.2：manifest 完整性签名与 release/ 陈旧文件隔离
# ---------------------------------------------------------------------------

def sign_manifest(manifest):
    """manifest 签名：HMAC-SHA256(SKILLS_BROKER_KEY, canonical_json(body))。

    body 为剔除 manifest_hmac 字段后的 manifest——对同一文档重复签名幂等，
    与 verify_manifest 口径严格一致；密钥读取沿用 contract.broker_key()
    （env SKILLS_BROKER_KEY 优先，其次 ~/.skills-broker/broker.key）。
    """
    body = {k: v for k, v in manifest.items() if k != MANIFEST_HMAC_FIELD} \
        if isinstance(manifest, dict) else manifest
    return hmac.new(contract.broker_key(),
                    contract.canonical_json(body).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def verify_manifest(path):
    """验签 manifest 文件；解析失败/缺签名/签名不符一律 False（fail-closed）。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return False
    if not isinstance(doc, dict):
        return False
    sig = doc.get(MANIFEST_HMAC_FIELD)
    if not isinstance(sig, str) or not sig:
        return False
    return hmac.compare_digest(sign_manifest(doc), sig)


def _quarantine_stale_release_files(rel_dir, keep_names):
    """把 release/ 顶层不在本轮交付清单内的陈旧文件/目录移入
    release/.quarantine-<ts>/（先隔离可审计，不静默删除）；既有
    .quarantine-* 目录原地保留不递归处理。

    返回 (moved, failed)：moved 为已隔离的顶层名称列表；failed 为隔离失败
    仍残留原位的名称列表（调用方须 fail-closed 拒绝交付）。
    """
    moved, failed = [], []
    if not os.path.isdir(rel_dir):
        return moved, failed
    keep = set(keep_names)
    stale = [e for e in sorted(os.listdir(rel_dir))
             if e not in keep and not e.startswith(".quarantine-")]
    if not stale:
        return moved, failed
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    qdir = os.path.join(rel_dir, ".quarantine-%s" % ts)
    suffix = 1
    while os.path.exists(qdir):
        suffix += 1
        qdir = os.path.join(rel_dir, ".quarantine-%s-%d" % (ts, suffix))
    os.makedirs(qdir, exist_ok=True)
    for name in stale:
        try:
            shutil.move(os.path.join(rel_dir, name),
                        os.path.join(qdir, name))
            moved.append(name)
        except (OSError, shutil.Error) as exc:
            print("[deliver] [警告] 陈旧文件隔离失败: %s (%s)" % (name, exc))
            failed.append(name)
    return moved, failed


def load_views(root):
    """读取 config.yaml views；返回规范化视图列表（无 views 时 None）。"""
    cfg = os.path.join(root, "config.yaml")
    if not os.path.isfile(cfg):
        return None
    try:
        _flat, raw = load_config_full(cfg)
    except (OSError, ValueError):
        return None
    if raw is None:
        return None
    views, errors = validate_views(raw)
    return views if not errors else None


def view_pdf_map(root, views):
    """视图 id -> 入口 PDF 名（与 make_review_pack 同口径）。"""
    view_pdf = {}
    for v in views or []:
        view_pdf[v["id"]] = v["entry"] + ".pdf"
    view_pdf.setdefault("student_initial", "student.pdf")
    view_pdf.setdefault("teacher", "teacher.pdf")
    return view_pdf


def current_production_digest(root, materials, views):
    """重算 production_digest，口径与 make_review_pack.py 完全一致
    （装配顺序敏感：extra_refs 顺序必须相同）。返回 digest 结果字典，
    附带 extra_refs 供依赖图诊断使用。"""
    secs = mrp.sec_names(root)
    extra = ["config.yaml", "config.tex", "config-class.tex",
             "header.tex", "main.tex"] + secs + ["footer.tex"]
    vp = view_pdf_map(root, views)
    for vid in sorted(vp):
        entry_tex = vp[vid][:-4] + ".tex"
        if os.path.isfile(os.path.join(root, entry_tex)):
            extra.append(entry_tex)
    info = contract.compute_production_digest(root, materials,
                                              extra_refs=extra)
    info["extra_refs"] = extra
    return info


def changed_inputs(root, recorded):
    """recorded: {项目相对路径: 旧摘要}；返回内容已变动/缺失的路径列表。"""
    changed = []
    for rel, old in sorted((recorded or {}).items()):
        abs_path = contract.resolve_path(root, rel)
        now = contract.sha256_file(abs_path) if abs_path else None
        if now != old:
            changed.append(rel)
    return changed


def build_dep_graph(root, brief, materials, extra_refs):
    """失效诊断用依赖图：节点 -> 它直接依赖的节点（项目相对路径）。"""
    graph = {}
    brief_refs = contract.collect_refs(brief) if brief else []
    excluded = set()
    if brief:
        excluded = {contract.normalize_relpath(r) for r in
                    brief.get("freeze_basis", {}).get(
                        "research_review_refs", [])}
    graph[BRIEF_REL] = [r for r in brief_refs if r not in excluded]
    if materials:
        mrefs = set(contract.collect_refs(materials))
        for b in materials.get("blocks", []):
            if b.get("content_ref"):
                mrefs.add(b["content_ref"])
            mrefs.update(b.get("source_refs", []))
        graph[MATERIALS_REL] = sorted(mrefs)
        graph["production"] = sorted(mrefs) + list(extra_refs or [])
    else:
        graph["production"] = list(extra_refs or [])
    graph["build"] = ["production"]
    graph["release"] = ["build"]
    for scope in REQUIRED_SCOPES:
        graph["review:%s" % scope] = ["production"]
    graph["freeze"] = [BRIEF_REL]
    return graph


# ---------------------------------------------------------------------------
# 各核对项
# ---------------------------------------------------------------------------

def check_freeze(root, brief, refusals, evidence):
    """第 1 条：交接包当前摘要与研究冻结记录一致。"""
    freeze, ferr = load_json(root, FREEZE_REL)
    if ferr:
        refusals.append((1, "缺少有效教研冻结：%s" % ferr))
        return None
    for e in contract.validate(freeze, "freeze"):
        refusals.append((1, "freeze.json 未通过 schema 校验: %s [%s] %s"
                         % (e["path"], e["validator"], e["message"])))
        return None
    if freeze.get("brief_id") != brief.get("brief_id"):
        refusals.append((1, "freeze.brief_id=%r 与 brief.brief_id=%r 不一致"
                         % (freeze.get("brief_id"), brief.get("brief_id"))))
    if freeze.get("revision") != brief.get("revision"):
        refusals.append((1, "freeze.revision=%r 与 brief.revision=%r 不一致"
                         % (freeze.get("revision"), brief.get("revision"))))
    review_refs = brief.get("freeze_basis", {}).get("research_review_refs",
                                                    [])
    info = contract.compute_brief_digest(root, brief,
                                         exclude_refs=review_refs)
    evidence["brief_digest"] = info["digest"]
    for m in info["missing"]:
        refusals.append((1, "brief_digest 引用文件缺失: %s" % m))
    if info["digest"] != freeze.get("brief_digest"):
        changed = changed_inputs(root, freeze.get("dependency_digests"))
        refusals.append(
            (1, "交接包当前摘要与研究冻结记录不一致：重算 brief_digest=%s，"
                "freeze 记录 %s；变动引用文件: %s。研究包过期，禁止沿用旧 "
                "PASS；须由教研侧重新复核冻结或恢复研究文件"
                % (info["digest"], freeze.get("brief_digest"),
                   "、".join(changed) or "（无法定位，可能 brief.json 自身"
                                         "变动）")))
    elif info["inputs"] != freeze.get("dependency_digests"):
        refusals.append((1, "freeze.dependency_digests 与当前重算的引用"
                         "摘要集合不一致（freeze 记录本身不完整）"))
    return freeze


def write_build_record(root, brief_digest, prod_digest, views,
                       build_exit):
    """构建后写 teaching/build/build.json（build.schema.json）。"""
    artifacts = []
    for vid, pdf in sorted(view_pdf_map(root, views).items()):
        abs_pdf = contract.resolve_path(root, pdf)
        digest = contract.sha256_file(abs_pdf) if abs_pdf else None
        if digest:
            artifacts.append({"view_id": vid, "output_ref": pdf,
                              "output_digest": digest})
    record = {
        "schema_version": contract.CONTRACT_VERSION,
        "brief_digest": brief_digest,
        "production_digest": prod_digest,
        "artifacts": artifacts,
        "commands": [{
            "command": "%s %s %s --json %s" % (
                sys.executable, os.path.join(_SCRIPT_DIR, "build.py"),
                root, BUILD_REPORT_REL),
            "exit_code": build_exit,
            "log_ref": BUILD_REPORT_REL,
        }],
        "result": "success" if build_exit == 0 else "failure",
        "built_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
    }
    errs = contract.validate(record, "build")
    if errs:
        return None, ["构建记录未通过 build.schema.json 校验: %s"
                      % errs[0]["message"]]
    abs_path = contract.resolve_path(root, BUILD_RECORD_REL)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(record, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    print("[deliver] 构建记录已写入 %s" % BUILD_RECORD_REL)
    return record, []


def check_build_binding(root, freeze, prod_digest, skip_build, refusals,
                        unverified):
    """第 2 条：教学正文绑定同一 brief，实际构建对应当前
    production_digest。"""
    materials, merr = load_json(root, MATERIALS_REL)
    if merr:
        refusals.append((2, "缺少教学统一内容源：%s" % merr))
        return
    if freeze is not None:
        if materials.get("brief_revision") != freeze.get("revision"):
            refusals.append((2, "materials.brief_revision=%r 与当前 "
                             "freeze.revision=%r 不一致（stale，须重新"
                             "绑定并重审受影响块）"
                             % (materials.get("brief_revision"),
                                freeze.get("revision"))))
    record, rerr = load_json(root, BUILD_RECORD_REL)
    if rerr:
        msg = "缺少构建记录 %s（%s）" % (BUILD_RECORD_REL, rerr)
        (unverified if skip_build else refusals).append(
            (2, msg + ("；--skip-build 技术测试：构建绑定未验证"
                       if skip_build else "")))
        return
    berrs = contract.validate(record, "build")
    if berrs:
        refusals.append((2, "构建记录未通过 build.schema.json 校验: %s"
                         % berrs[0]["message"]))
        return
    problems = []
    if record.get("result") != "success":
        problems.append("构建记录 result=%r" % record.get("result"))
    if freeze is not None and record.get("brief_digest") \
            != freeze.get("brief_digest"):
        problems.append("构建记录的 brief_digest 与 freeze 不一致")
    if record.get("production_digest") != prod_digest:
        problems.append("构建记录的 production_digest=%s 与当前 %s 不一致"
                        "（构建过期：受审内容在上次构建后已改动，须重建并"
                        "重审）" % (record.get("production_digest"),
                                   prod_digest))
    for art in record.get("artifacts", []):
        abs_pdf = contract.resolve_path(root, art.get("output_ref", ""))
        now = contract.sha256_file(abs_pdf) if abs_pdf else None
        if now != art.get("output_digest"):
            problems.append("构建产物 %s 的当前摘要与构建记录不一致"
                            % art.get("output_ref"))

    # 契约 2.5.0 (F5): 若存在 student_solution.pdf，必须核验 solution-build.json 与当前 production_digest
    sol_pdf_path = os.path.join(root, "student_solution.pdf")
    sol_build_path = os.path.join(root, "teaching", "build", "solution-build.json")
    if os.path.isfile(sol_pdf_path):
        if not os.path.isfile(sol_build_path):
            problems.append("存在 student_solution.pdf 但缺少 teaching/build/solution-build.json 摘要绑定记录")
        else:
            try:
                with open(sol_build_path, "r", encoding="utf-8-sig") as f:
                    sol_record = json.load(f)
                if sol_record.get("production_digest") != prod_digest:
                    problems.append("student_solution.pdf 的构建记录 production_digest 与当前不一致（解答源已改动，需重新编译）")
                sol_pdf_hash = contract.sha256_file(sol_pdf_path)
                if sol_record.get("pdf_sha256") and sol_record.get("pdf_sha256") != sol_pdf_hash:
                    problems.append("student_solution.pdf 文件 SHA256 与构建记录不一致")
            except Exception as e:
                problems.append("读取 teaching/build/solution-build.json 失败: %s" % e)

    for p in problems:
        if skip_build:
            unverified.append((2, p + "（--skip-build 技术测试：未重验）"))
        else:
            refusals.append((2, p))


def check_reviews(root, brief, prod_digest, refusals, evidence):
    """第 3 条：入选题、补充材料和跨题设计都有对应审查覆盖。"""
    rdir = contract.resolve_path(root, REVIEWS_DIR_REL)
    records = []
    if os.path.isdir(rdir):
        for fn in sorted(os.listdir(rdir)):
            if not fn.endswith(".json"):
                continue
            rel = "%s/%s" % (REVIEWS_DIR_REL, fn)
            doc, err = load_json(root, rel)
            if err:
                refusals.append((3, "审查记录不可读: %s" % err))
                continue
            verrs = contract.validate(doc, "review")
            if verrs:
                concl = doc.get("conclusion") if isinstance(doc, dict) \
                    else None
                hint = ("（conclusion=%r 是脚本状态词而非审查结论；%s）"
                        % (concl, CONVERSION_NOTE)) if isinstance(
                            concl, str) and concl in contract.CHECK_STATUSES \
                    else ""
                refusals.append((3, "审查记录 %s 未通过 review.schema.json "
                                 "校验: %s [%s] %s%s"
                                 % (rel, verrs[0]["path"],
                                    verrs[0]["validator"],
                                    verrs[0]["message"], hint)))
                continue
            compat_vers = getattr(contract, "COMPATIBLE_CONTRACT_VERSIONS", {contract.CONTRACT_VERSION})
            if doc.get("schema_version") \
                    and doc["schema_version"] not in compat_vers:
                refusals.append((3, "审查记录 %s 的 schema_version=%r 与契约"
                                 "版本 %s 不匹配"
                                 % (rel, doc["schema_version"],
                                    ", ".join(sorted(compat_vers)))))
                continue
            records.append((rel, doc))
    evidence["review_records"] = [rel for rel, _d in records]

    current = [(rel, d) for rel, d in records
               if d["subject_digest"] == prod_digest]
    stale = [(rel, d) for rel, d in records
             if d["subject_digest"] != prod_digest]

    # 绑定当前摘要的未通过结论 → 阻断
    for rel, d in current:
        if d["conclusion"] != "PASS":
            refusals.append((3, "审查记录 %s（scope=%s）绑定当前 "
                             "production_digest 但结论为 %s，须先按依赖"
                             "定位修订并重跑受影响检查"
                             % (rel, d["scope"], d["conclusion"])))
        open_f = [f["finding_id"] for f in d.get("findings", [])
                  if f.get("status") == "open"]
        if open_f:
            refusals.append((5, "审查记录 %s 存在未处置疑似项: %s；review "
                             "级疑似项须逐项处置（status=resolved 或转为 "
                             "issue），不能用一份非空“已复核”文件概括放行"
                             % (rel, "、".join(open_f))))

    passing = {}
    for rel, d in current:
        if d["conclusion"] == "PASS":
            passing.setdefault(d["scope"], []).append((rel, d))

    for scope in REQUIRED_SCOPES:
        if scope in passing:
            continue
        stale_hit = [rel for rel, d in stale if d["scope"] == scope]
        if stale_hit:
            refusals.append((3, "缺少 scope=%s 的有效 PASS 审查记录：%s "
                             "绑定的是过期 production_digest（受审内容在"
                             "审查后已改动，构建与审查结论过期，须重新"
                             "审查绑定当前摘要 %s…）"
                             % (scope, "、".join(stale_hit),
                                prod_digest[:12])))
        else:
            refusals.append((3, "缺少 scope=%s 的独立审查记录（覆盖缺口）："
                             "入选题、补充材料和跨题设计须由未参与写作的"
                             "审查者在新上下文中凭材料证据审查"
                             % scope))

    covered = set()
    for rel, d in current:
        covered.update(d.get("coverage", []))
    required_ids = list(brief["selection"]["selected_problem_ids"])
    required_ids += [s.get("supplement_id")
                     for s in brief.get("supplements", [])]
    required_ids.append(brief["brief_id"])  # brief 级跨题设计
    missing_cov = [i for i in required_ids if i not in covered]
    if not refusals_for_item(refusals, 3) and missing_cov:
        refusals.append((3, "审查 coverage 并集未覆盖: %s（须覆盖 brief "
                         "全部 selected_problem_ids + supplements + "
                         "brief_id 跨题设计）" % "、".join(missing_cov)))
    evidence["review_coverage"] = sorted(covered)
    return {scope: [rel for rel, _d in lst] for scope, lst in passing.items()}


def refusals_for_item(refusals, item):
    return [m for i, m in refusals if i == item]


def check_issues(root, refusals, evidence):
    """第 4 条：所有影响交付的 issue 逐项有具体处理与复验引用。"""
    idir = contract.resolve_path(root, ISSUES_DIR_REL)
    stats = {"total": 0, "open": 0, "accepted": 0, "resolved": 0,
             "rejected": 0}
    if not os.path.isdir(idir):
        evidence["issues"] = stats
        return
    for fn in sorted(os.listdir(idir)):
        if not fn.endswith(".json"):
            continue
        rel = "%s/%s" % (ISSUES_DIR_REL, fn)
        doc, err = load_json(root, rel)
        if err:
            refusals.append((4, "issue 记录不可读: %s" % err))
            continue
        ierrs = contract.validate(doc, "issue")
        if ierrs:
            refusals.append((4, "issue %s 未通过 issue.schema.json 校验: "
                             "%s [%s] %s" % (rel, ierrs[0]["path"],
                                             ierrs[0]["validator"],
                                             ierrs[0]["message"])))
            continue
        stats["total"] += 1
        stats[doc["status"]] += 1
        iid = doc["issue_id"]
        if doc["status"] in ("open", "accepted"):
            refusals.append((4, "issue %s（%s，route=%s）status=%s：影响"
                             "交付的 issue 未处置完成；%s"
                             % (iid, rel, doc["route"], doc["status"],
                                doc.get("reason", "")[:80])))
            continue
        log = doc.get("resolution_log") or []
        if not log:
            refusals.append((4, "issue %s status=%s 但没有处理记录"
                             "（resolution_log 为空）；逐项具体处理与复验"
                             "引用是交付前提" % (iid, doc["status"])))
            continue
        for j, entry in enumerate(log):
            for eref in entry.get("evidence_refs", []):
                abs_e = contract.resolve_path(root, eref)
                if abs_e is None or not os.path.isfile(abs_e):
                    refusals.append((4, "issue %s 处理记录 #%d 的复验引用"
                                     "不存在: %s" % (iid, j + 1, eref)))
    evidence["issues"] = stats


def _pending_review_findings(rep, rel):
    """提取 status=review 报告中需逐条处置的高危 review 级疑似项。

    返回 [(report_rel, message, category)]；自动降级分类（§2.2）不阻断。
    """
    status, findings = report_status(rep)
    if status != "review":
        return []
    pending = []
    for f in findings:
        if isinstance(f, dict) and f.get("severity") == "review":
            msg = f.get("message", "")
            # 从消息中提取 category（格式：【分类: xxx，hash: yyy】）
            cat_m = re.search(r"【分类:\s*(\w+)", msg)
            category = cat_m.group(1) if cat_m else ""
            # 自动降级的分类不阻断
            if category in REVIEW_AUTO_DOWNGRADE_CATEGORIES:
                continue
            pending.append((rel, msg, category))
    return pending


def _load_review_dispositions(root):
    """读取 research/teaching 两级 review-notes.json 的处置记录列表。"""
    dispositions = []
    for rn_rel in ("research/review-notes.json",
                   "teaching/review-notes.json"):
        doc, err = load_json(root, rn_rel)
        if err:
            continue
        if isinstance(doc, dict):
            dispositions.extend(doc.get("dispositions", []))
    return dispositions


def _match_disposition(dispositions, rel, msg):
    """在处置记录中寻找与 (report, finding 消息) 匹配的条目。"""
    for d in dispositions:
        if not isinstance(d, dict):
            continue
        if d.get("report") != rel:
            continue
        if not d.get("finding") or d["finding"] not in msg:
            continue
        if not d.get("action"):
            continue
        return d
    return None


def _disposition_valid(d):
    """§2.2 处置记录完整性：category 非空且非自动降级项、finding_hash
    非空、evidence_refs 非空。"""
    if not isinstance(d, dict):
        return False
    if d.get("category") is None:
        return False
    if d.get("category") in REVIEW_AUTO_DOWNGRADE_CATEGORIES:
        return False
    if not d.get("finding_hash"):
        return False
    if not d.get("evidence_refs"):
        return False
    return True


def review_report_blocking(root, rep, rel):
    """G7.1：status=review 的中间报告一律视为拒绝（与 broker promote 语义
    对齐），除非全部高危 review 级疑似项已在 review-notes.json 逐条处置
    且 disposition 校验通过。

    返回阻断原因列表（空列表 = 不阻断）。fail-closed：报告自称 review
    却未列出任何 finding 时同样阻断（无法核对处置依据）。
    """
    status, findings = report_status(rep)
    if status != "review":
        return []
    pending = _pending_review_findings(rep, rel)
    if not pending:
        if not findings:
            return ["报告 status=review 但未列出任何 finding，无法核对"
                    "处置依据（fail-closed 拒绝）"]
        return []  # 仅剩自动降级项（已降为 info），无需逐条处置
    dispositions = _load_review_dispositions(root)
    blocking = []
    for _rel, msg, _cat in pending:
        matched = _match_disposition(dispositions, rel, msg)
        if matched is None:
            blocking.append("疑似项无逐条处置记录: %s" % msg[:80])
        elif not _disposition_valid(matched):
            blocking.append("处置记录不完整（缺 category/finding_hash/"
                            "evidence_refs 或 category 为自动降级项）: %s"
                            % msg[:80])
    return blocking


def check_review_dispositions(root, reports, refusals):
    """第 5 条：脚本报告 review 级疑似项逐项处置（§2.2 升级）。

    处置记录约定（workflow.md T3）：<stage>/review-notes.json，含
    dispositions 列表，每项须包含：
      - "report": 报告相对路径
      - "finding": 对应 finding 消息子串
      - "action": 处置动作描述
      - "category": 分类（§2.2 四类之一）
      - "finding_hash": 稳定哈希（§2.2）
      - "evidence_refs": 非空证据引用列表

    §2.2 升级：
    - boilerplate_overlap / same_problem_reuse：自动降为 info，无需逐条处置
    - cross_problem_leak / forbidden_feature_match：保留 review，未处置→拒绝交付
    - 任何处置记录缺少 category/finding_hash/evidence_refs→拒绝交付
    """
    pending = []
    for name, rep, rel in reports:
        pending.extend(_pending_review_findings(rep, rel))
    if not pending:
        return
    dispositions = _load_review_dispositions(root)
    for rel, msg, category in pending:
        matched = _match_disposition(dispositions, rel, msg)
        if not matched:
            refusals.append((5, "报告 %s 的 review 级疑似项无逐条处置记录"
                             "（review-notes.json 中须有一项 report/finding"
                             "/action/category/finding_hash/evidence_refs 与该条对应；"
                             "非空\"已复核\"文件概括不放行）: %s" % (rel, msg[:120])))
            continue
        # §2.2：处置记录完整性检查
        missing_fields = []
        if matched.get("category") is None:
            missing_fields.append("category")
        if not matched.get("finding_hash"):
            missing_fields.append("finding_hash")
        if not matched.get("evidence_refs"):
            missing_fields.append("evidence_refs（不得为空列表）")
        if missing_fields:
            refusals.append((5, "报告 %s 的处置记录缺少必填字段（§2.2 升级）：%s。"
                             "未来 cross_problem_leak/forbidden_feature_match 类 finding"
                             "必须逐条提供 category+finding_hash+evidence_refs"
                             % (rel, "、".join(missing_fields))))
        # §2.2：若处置记录属于自动降级项，说明状态管理失调，提示内部错误
        if matched.get("category") in REVIEW_AUTO_DOWNGRADE_CATEGORIES:
            refusals.append((5, "报告 %s 的处置记录 category=%s 为自动降级项（如 boilerplate_overlap/same_problem_reuse），"
                             "已降级为 info，不应作为未解决的 review 处置项出现（请检查 "
                             "check_student_task.py）"
                             % (rel, matched.get("category"))))


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="交付总门禁（T4；meet.md B6 八条逐项核对）")
    ap.add_argument("project_root", nargs="?", default=None,
                    help="项目根（含 research/ 与 teaching/ 的目录）；"
                         "--verify-manifest 模式下可省略")
    ap.add_argument("--verify-manifest", metavar="PATH", default=None,
                    help="仅验签指定 manifest.json 的 manifest_hmac"
                         "（G7.2；退出码 0=验签通过，1=验签失败/文件不可读）")
    ap.add_argument("--skip-build", action="store_true",
                    help="跳过 PDF 构建（仅技术测试场景）：不运行 build.py，"
                         "构建绑定降级为未验证项，manifest 如实标明 "
                         "build_skipped: true 且 result="
                         "technical_test_only，不写“发布成功”语义字段")
    ap.add_argument("--json", metavar="PATH", default=None,
                    help="deliver 报告落盘路径（默认 %s）" % DEFAULT_REPORT)
    ap.add_argument("--pilot-status", metavar="STATUS", default="not_run",
                    help="真实试教状态（默认 not_run；not_run 不阻止交付）")
    ap.add_argument("--run-id", default=None,
                    help="可选：本轮 broker run_id。提供时写入 release/manifest.json "
                         "的 run_id 字段，供调用方核对 manifest 确由本轮交付生成"
                         "（防止把上一次运行的 manifest 当成本轮产物）")
    ap.add_argument("--deliver-teacher", action="store_true", default=None,
                    help="是否交付 teacher.tex 与 teacher.pdf（默认关闭；不交付时仅交付 student.pdf 及延迟发放页）")
    ap.add_argument("--no-deliver-teacher", dest="deliver_teacher", action="store_false",
                    help="显式声明不交付 teacher.tex 与 teacher.pdf")
    args = ap.parse_args()
    # G7.2：manifest 验签子命令（供 workflow 侧调用，避免改动 script_runner）
    if args.verify_manifest:
        ok = verify_manifest(args.verify_manifest)
        print("[deliver] --verify-manifest %s：验签%s"
              % (args.verify_manifest, "通过" if ok else "失败"
                 "（签名缺失/不符或文件不可读，fail-closed）"))
        return 0 if ok else 1
    if not args.project_root:
        print("[deliver] 缺少项目根参数（或改用 --verify-manifest <path>）")
        return 2
    root = os.path.abspath(args.project_root)
    if not os.path.isdir(root):
        print("[deliver] 项目根不存在: %s" % root)
        return 2
    if not os.path.isdir(os.path.join(root, "research")) \
            or not os.path.isfile(os.path.join(
                    root, MATERIALS_REL.replace("/", os.sep))):
        print("[deliver] 缺少 research/ 或 %s：本门禁面向 v2 两阶段项目；"
              "旧项目请使用旧版 deliver" % MATERIALS_REL)
        return 2

    brief, berr = load_json(root, BRIEF_REL)
    if berr:
        print("[deliver] 缺少有效教研交接包：%s" % berr)
        return 2
    berrs = contract.validate(brief, "brief")
    if berrs:
        print("[deliver] brief.json 未通过 schema 校验: %s"
              % berrs[0]["message"])
        return 2
    materials, merr = load_json(root, MATERIALS_REL)
    if merr:
        print("[deliver] " + merr)
        return 2
    views = load_views(root)

    refusals = []    # (条目号, 消息)
    unverified = []  # (条目号, 消息)——仅 --skip-build 技术测试
    evidence = {"skip_build": bool(args.skip_build),
                "pilot_status": args.pilot_status}
    # 门禁代码摘要：报告与 manifest 绑定实际执行的检查代码版本
    evidence["code_digest"] = contract.code_digest(
        [_SCRIPT_DIR, _SCHEMAS_DIR])["digest"]
    reports = {}     # name -> (report, report_rel)

    # ---- 固定顺序 1—3：check_research / 校验 freeze / 教学结构检查 ----
    # 纯读取无依赖的检查并行；报告写各自 --json 路径，互不冲突。
    print("[deliver] 阶段 1：check_research + 教学结构检查（并行，纯读取）")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(run_script, script, root, rel): (name, rel)
                   for name, script, rel in _read_checks()}
        for fut in concurrent.futures.as_completed(futures):
            name, rel = futures[fut]
            code, rep, err = fut.result()
            if err:
                refusals.append((7, err + "（缺依赖不产出宣称完成的发布"
                                 "记录）"))
            elif rep is None:
                refusals.append((7, "%s 未产出可解读的结构化报告 %s（退出"
                                 "码 %s；执行状态不得解读为内容无问题）"
                                 % (name, rel, code)))
            else:
                reports[name] = (rep, rel)

    # 校验 freeze（第 1 条）
    print("[deliver] 阶段 2：校验 freeze（重算 brief_digest）")
    freeze = check_freeze(root, brief, refusals, evidence)

    # ---- 固定顺序 4：build（串行；与读取正在生成的日志/PDF 不可并行）----
    prod_info = current_production_digest(root, materials, views)
    prod_digest = prod_info["digest"]
    evidence["production_digest"] = prod_digest
    for m in prod_info["missing"]:
        refusals.append((2, "production_digest 摘要输入缺失: %s" % m))

    # 失效诊断：与上次审查包/构建记录记录的快照比较，按依赖定位
    old_pack, _perr = load_json(root, REVIEW_PACK_MANIFEST_REL)
    recorded = dict((old_pack or {}).get("digest_inputs") or {})
    if freeze:
        recorded.update({k: v for k, v in
                         (freeze.get("dependency_digests") or {}).items()
                         if k not in recorded})
    changed = changed_inputs(root, recorded)
    if changed:
        graph = build_dep_graph(root, brief, materials,
                                prod_info.get("extra_refs"))
        affected = contract.affected_set(root, changed, graph)
        evidence["changed_inputs"] = changed
        evidence["affected_nodes"] = affected
        print("[deliver] [诊断] 检测到 %d 个引用文件自上次快照后变动: %s"
              % (len(changed), "、".join(changed)))
        print("[deliver] [诊断] 按依赖图受影响节点: %s（按 handoff.md 第 3 "
              "节失效规则重跑受影响检查，不无限重跑全部 agent）"
              % "、".join(affected))

    if args.skip_build:
        print("[deliver] 阶段 3：--skip-build（仅技术测试场景）：不运行 "
              "build.py，构建绑定降级为未验证项")
        evidence["build_skipped"] = True
    else:
        print("[deliver] 阶段 3：build（同一工作目录串行构建）")
        code, rep, err = run_script(
            os.path.join(_SCRIPT_DIR, "build.py"), root, BUILD_REPORT_REL)
        if err:
            refusals.append((7, err))
        else:
            reports["build"] = (rep, BUILD_REPORT_REL)
            status, _f = report_status(rep)
            if status != "pass":
                refusals.append((6, "构建未通过（status=%s，退出码 %s）；"
                                 "编译只是手段，验收才是目的"
                                 % (status, code)))
        if freeze and not refusals_for_item(refusals, 6):
            _rec, werrs = write_build_record(
                root, freeze["brief_digest"], prod_digest, views, code)
            refusals.extend((2, m) for m in werrs)

    # ---- 固定顺序 4b：check_student_task（构建后串行；版面以实际 PDF 为准）----
    print("[deliver] 阶段 3b：check_student_task（学生任务质量统一门禁）")
    _st_name, _st_script, _st_rel = STUDENT_TASK_CHECK
    code, rep, err = run_script(_st_script, root, _st_rel)
    if err:
        refusals.append((7, err))
    elif rep is None:
        (unverified if args.skip_build else refusals).append(
            (6, "check_student_task 未产出结构化报告（退出码 %s）" % code))
    else:
        reports[_st_name] = (rep, _st_rel)

    # ---- 固定顺序 5—6：PDF 比对 -> make_review_pack（串行读已生成产物）----
    print("[deliver] 阶段 4：compare_versions（PDF 比对）")
    code, rep, err = run_script(
        os.path.join(_SCRIPT_DIR, "compare_versions.py"), root,
        COMPARE_REPORT_REL)
    if err:
        refusals.append((7, err))
    elif rep is None:
        (unverified if args.skip_build else refusals).append(
            (6, "compare_versions 未产出结构化报告（退出码 %s）" % code))
    else:
        reports["compare_versions"] = (rep, COMPARE_REPORT_REL)

    print("[deliver] 阶段 5：make_review_pack（T3 受控审查输入）")
    code, _rep, err = run_script(
        os.path.join(_SCRIPT_DIR, "make_review_pack.py"), root,
        REVIEW_PACK_MANIFEST_REL, use_json=False)
    if err or code != 0:
        msg = "make_review_pack 未成功（%s）" % (err or "退出码 %s" % code)
        if args.skip_build:
            unverified.append((6, msg + "；--skip-build 技术测试记为未验证"
                               "（production_digest 已按同口径在进程内"
                               "重算）"))
        else:
            refusals.append((6, msg))
    else:
        pack, _ = load_json(root, REVIEW_PACK_MANIFEST_REL)
        if isinstance(pack, dict) \
                and pack.get("production_digest") != prod_digest:
            refusals.append((2, "make_review_pack 重算的 "
                             "production_digest 与交付门禁不一致（摘要口"
                             "径漂移，属协议问题，不得放行）"))

    # ---- 第 2 条：构建绑定 ----
    check_build_binding(root, freeze, prod_digest, args.skip_build,
                        refusals, unverified)

    # ---- 各检查报告状态判定（转换规则：看 status 字段，不只看退出码）----
    print("[deliver] 阶段 6：独立审查记录与 issue 核对")
    scope_of = {"check_research": "research", "check_architect": "architect",
                "check_teaching": "teaching",
                "check_student_task": "学生任务",
                "check_student_visibility": "可见性",
                "check_numbering": "qid/装配",
                "check_subquestion_solution": "小问解答",
                "compare_versions": "可见性(PDF)",
                "build": "版面/构建"}
    for name, (rep, rel) in sorted(reports.items()):
        status, _f = report_status(rep)
        # 报告必须绑定当前契约版本或兼容版本（旧版本报告不得当作新门禁证据）
        rep_ver = rep.get("schema_version") if isinstance(rep, dict) else None
        compat_vers = getattr(contract, "COMPATIBLE_CONTRACT_VERSIONS", {contract.CONTRACT_VERSION})
        if rep_ver is not None and rep_ver not in compat_vers:
            refusals.append((6, "%s 的 schema_version=%r 与契约版本 %s "
                             "不匹配（旧版本报告不得当作新门禁证据）"
                             % (rel, rep_ver, ", ".join(sorted(compat_vers)))))
            continue
        if status == "fail":
            refusals.append((6, "%s（scope=%s）报告 fail：对应范围不能通"
                             "过，修复后重跑该检查（任一局部 PASS 不能代表"
                             "全课通过，任一局部 fail 也不能被其他 PASS "
                             "抵消）" % (rel, scope_of.get(name, name))))
        elif status == "review":
            # G7.1：review 一律视为拒绝（与 broker promote 语义对齐），除非
            # 对应 finding 已在 review-notes.json 逐条处置且 disposition
            # 校验通过（沿用第 5 条既有处置机制）
            blocking = review_report_blocking(root, rep, rel)
            if blocking:
                refusals.append((6, "%s（scope=%s）报告 status=review：%s"
                                 "（review 级疑似项须在 review-notes.json "
                                 "逐条处置后才可交付）"
                                 % (rel, scope_of.get(name, name),
                                    "；".join(blocking))))
        elif status == "error":
            target = unverified if (args.skip_build
                                    and name == "compare_versions") \
                else refusals
            target.append((6, "%s 报告 error：属执行状态，按错误处理，"
                           "不得解读为内容无问题%s"
                           % (rel, "；--skip-build 技术测试记为未验证"
                              if target is unverified else "")))
    evidence["reports"] = {rel: report_status(rep)[0]
                           for name, (rep, rel) in reports.items()}

    # ---- 第 3 条：独立审查记录 ----
    review_refs = check_reviews(root, brief, prod_digest, refusals, evidence)

    # ---- 第 4 条：issues ----
    check_issues(root, refusals, evidence)

    # ---- 第 5 条：脚本 review 级疑似项逐项处置 ----
    check_review_dispositions(
        root, [(n, rep, rel) for n, (rep, rel) in reports.items()],
        refusals)

    # ---- 第 7 条：skip-build 语义 ----
    if args.skip_build and not unverified:
        unverified.append((7, "--skip-build：本次未重新构建，PDF 产物与"
                           "构建绑定未在交付时重验"))

    # ---- 判定 ----
    report_path = args.json or os.path.join(root, DEFAULT_REPORT)
    os.makedirs(os.path.dirname(os.path.abspath(report_path)),
                exist_ok=True)
    if refusals:
        findings = [{"severity": "fail",
                     "message": "[第 %d 条] %s" % (i, m)}
                    for i, m in sorted(refusals)]
        rep = contract.write_check_report(report_path, "fail", "deliver",
                                          findings)
        rep["evidence"] = evidence
        with open(report_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        print("[deliver] 拒绝交付（%d 项，逐条如下）：" % len(refusals))
        for i, m in sorted(refusals):
            print("  [第 %d 条] %s" % (i, m))
        print("[deliver] 报告已写入 %s" % report_path)
        return 1

    # ---- 通过：生成 release/（--skip-build 不写 release） ----
    report_path = args.json or os.path.join(root, DEFAULT_REPORT)
    os.makedirs(os.path.dirname(os.path.abspath(report_path)),
                exist_ok=True)
    if args.skip_build:
        # G7.3：--skip-build 仅为技术测试——result 恒为 technical_test_only
        # 且报告 status 不得为 pass；只写技术测试记录，不创建/覆盖/更新
        # release/manifest.json，不复制正式 release PDF（正式交付须不带
        # --skip-build 在干净工作区重跑）。
        record = {
            "schema_version": contract.CONTRACT_VERSION,
            "tool": "deliver.py",
            "tool_version": TOOL_VERSION,
            "generated_at": datetime.datetime.now(
                datetime.timezone.utc).isoformat(),
            "result": "technical_test_only",
            "build_skipped": True,
            "brief_id": brief["brief_id"],
            "brief_revision": brief["revision"],
            "note": ("本次为 --skip-build 技术测试：未重新构建，未生成/更新 "
                     "release/manifest.json，未复制正式 release PDF；本记录"
                     "不构成发布成功声明，正式交付须不带 --skip-build 重跑。"),
        }
        rep = contract.write_check_report(report_path, "review", "deliver",
                                          [{"severity": "info",
                                            "message": "[第 %d 条] 未验证"
                                            "（--skip-build）: %s"
                                            % (i, m)}
                                            for i, m in sorted(unverified)])
        rep["result"] = "technical_test_only"
        # G7.3：技术测试记录不得携带任何 pass 字样——子检查状态映射
        # （evidence["reports"]）可能含子检查的 pass 结论，易从技术测试
        # 记录误读为交付通过，故本记录不收录（各子报告原文仍可查阅）。
        rep["evidence"] = {k: v for k, v in evidence.items()
                           if k != "reports"}
        rep["technical_test_record"] = record
        with open(report_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(rep, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        print("[deliver] 技术测试完成（result=technical_test_only，报告 "
              "status=review 而非 pass，build_skipped=true，未验证项 %d 条；"
              "未写 release/，本记录不构成发布成功声明）"
              % len(unverified))
        print("[deliver] 报告已写入 %s" % report_path)
        return 0

    rel_dir = os.path.join(root, "release")
    os.makedirs(rel_dir, exist_ok=True)
    artifacts = {}
    missing_pdfs = []

    # 判定是否交付教师版（teacher.tex 与 teacher.pdf）：默认关闭，仅在显式声明时开启
    deliver_teacher = args.deliver_teacher
    if deliver_teacher is None:
        cfg_path = os.path.join(root, "config.yaml")
        if os.path.isfile(cfg_path):
            try:
                import yaml
                with open(cfg_path, "r", encoding="utf-8-sig") as f:
                    cfg_doc = yaml.safe_load(f)
                if isinstance(cfg_doc, dict) and "deliver_teacher" in cfg_doc:
                    deliver_teacher = bool(cfg_doc["deliver_teacher"])
            except Exception:
                pass
    if deliver_teacher is None:
        deliver_teacher = False

    required_pdfs = ["student.pdf"]
    if deliver_teacher:
        required_pdfs.append("teacher.pdf")

    for pdf in required_pdfs:
        src = os.path.join(root, pdf)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(rel_dir, pdf))
            artifacts[pdf] = contract.sha256_file(
                os.path.join(rel_dir, pdf))
        else:
            missing_pdfs.append(pdf)
    if missing_pdfs:
        # 构建通过但主 PDF 缺失：证据不齐，不产出宣称完成的发布记录
        print("[deliver] 缺少 %s（证据不齐，拒绝产出发布记录）"
              % "、".join(missing_pdfs))
        return 1

    # 教师版交付：仅在显式声明时同步 teacher.tex；未声明交付 teacher 时
    # release/ 中可能残留的 teacher.pdf/teacher.tex 不再静默删除，统一由
    # 下方 G7.2 陈旧文件隔离步骤处理（移入 release/.quarantine-<ts>/，
    # 可审计）。
    if deliver_teacher:
        # 显式声明交付 teacher 时，若根目录下存在 teacher.tex，也一并交付至 release/teacher.tex
        t_tex_src = os.path.join(root, "teacher.tex")
        if os.path.isfile(t_tex_src):
            shutil.copy2(t_tex_src, os.path.join(rel_dir, "teacher.tex"))
            artifacts["teacher.tex"] = contract.sha256_file(
                os.path.join(rel_dir, "teacher.tex"))

    # 增补交付产物：student_solution.pdf（若缺失则自动编译，确保交付至 release/）
    sol_pdf = "student_solution.pdf"
    sol_src = os.path.join(root, sol_pdf)
    if not os.path.isfile(sol_src):
        try:
            from gen_student_solution import generate_student_solution_tex, compile_tex
            generate_student_solution_tex(root)
            compile_tex(root)
        except Exception as exc:
            print("[deliver] 自动编译 student_solution.pdf 提示: %s" % exc)

    if os.path.isfile(sol_src):
        shutil.copy2(sol_src, os.path.join(rel_dir, sol_pdf))
        artifacts[sol_pdf] = contract.sha256_file(
            os.path.join(rel_dir, sol_pdf))
        print("[deliver] 成功交付学生参考解答: %s" % sol_pdf)
    later = []
    for v in views or []:
        if v["channel"] in ("separate_hint", "separate_review"):
            pdf = v["entry"] + ".pdf"
            src = os.path.join(root, pdf)
            if os.path.isfile(src):
                later.append(pdf)
    if later:
        later_dir = os.path.join(rel_dir, "release-later")
        os.makedirs(later_dir, exist_ok=True)
        for pdf in later:
            shutil.copy2(os.path.join(root, pdf),
                         os.path.join(later_dir, pdf))
            artifacts["release-later/" + pdf] = contract.sha256_file(
                os.path.join(later_dir, pdf))

    artifact_refs = []
    for _vid, pdf in sorted(view_pdf_map(root, views).items()):
        if os.path.isfile(os.path.join(root, pdf)):
            artifact_refs.append(pdf)
            log = pdf[:-4] + ".log"
            if os.path.isfile(os.path.join(root, log)):
                artifact_refs.append(log)
    bd = contract.compute_build_digest(root, prod_digest, artifact_refs)
    build_digest = bd["digest"]

    # G7.2：隔离 release/ 中不在本轮交付清单内的陈旧文件（先移入
    # release/.quarantine-<ts>/ 再视同清理，可审计；隔离失败 fail-closed，
    # 不产出宣称完成的发布记录）
    keep_names = {"manifest.json"}
    for _name in artifacts:
        keep_names.add(_name.split("/")[0])
    quarantined, q_failed = _quarantine_stale_release_files(rel_dir,
                                                            keep_names)
    if q_failed:
        print("[deliver] release/ 陈旧文件隔离失败: %s（fail-closed，拒绝"
              "产出发布记录，请人工清理后重跑）" % "、".join(q_failed))
        return 1
    if quarantined:
        print("[deliver] 已隔离 release/ 陈旧文件 %d 项（.quarantine，可审"
              "计）: %s" % (len(quarantined), "、".join(quarantined)))

    manifest = {
        "schema_version": contract.CONTRACT_VERSION,
        "tool": "deliver.py",
        "tool_version": TOOL_VERSION,
        "generated_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(),
        "result": "delivered",
        "build_skipped": False,
        "brief_id": brief["brief_id"],
        "brief_revision": brief["revision"],
        "brief_digest": evidence.get("brief_digest"),
        "production_digest": prod_digest,
        "build_digest": build_digest,
        "review_refs": {
            "research": (freeze or {}).get("research_review_refs", []),
            "student_read": review_refs.get("student_read", []),
            "teacher_use": review_refs.get("teacher_use", []),
            "math_recheck": review_refs.get("math_recheck", []),
        },
        "reports": evidence["reports"],
        "code_digest": evidence.get("code_digest"),
        "issues": evidence.get("issues"),
        "pilot_status": args.pilot_status,
        "deliver_teacher": deliver_teacher,
        "artifacts": artifacts,
        # G7.2：交付清单（含 student_solution.pdf，若存在；缺失不阻断），
        # 每项含 sha256 供接收方核验
        "files": [{"path": _name, "sha256": _digest}
                  for _name, _digest in sorted(artifacts.items())],
        "quarantined": quarantined,
    }
    if args.run_id:
        manifest["run_id"] = args.run_id
    # G7.2：manifest 完整性签名（最后计算，覆盖除签名外全部字段）
    manifest[MANIFEST_HMAC_FIELD] = sign_manifest(manifest)
    with open(os.path.join(rel_dir, "manifest.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")

    rep = contract.write_check_report(report_path, "pass", "deliver", [])
    rep["evidence"] = evidence
    rep["release_manifest"] = "release/manifest.json"
    with open(report_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")

    print("[deliver] 全部门禁通过，发布目录: %s" % rel_dir)
    print("[deliver] 注意：本结论意为“已完成的检查范围内未发现问"
           "题”，不代表整章教学效果已被证明；pilot_status=%s"
           % args.pilot_status)
    print("[deliver] 报告已写入 %s" % report_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
