# -*- coding: utf-8 -*-
"""
tests/test_check_research_evidence.py — research 阶段子 Agent 证据红队用例

覆盖验收报告 NB7 缺口：WP5 台账化改造（check_research.py §1b）的攻击面。
攻击者模型：主 Agent 在 resume 路径上伪造/搬弄 execution_metadata 与证据台账，
试图让未经 courseware_dispatch 派发的 probe/pitfall/solution 通过 research 门禁。
"""

import json
import os
import shutil
import sys
import tempfile

import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_SCRIPTS_DIR = os.path.join(_PLUGIN_ROOT, "scripts")

for p in (_PLUGIN_ROOT, _SCRIPTS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import contract  # noqa: E402
from workflow.nodes import agent_evidence  # noqa: E402
from scripts import check_research  # noqa: E402

PID = "P2024-001"


@pytest.fixture
def research_env(monkeypatch):
    d = tempfile.mkdtemp(prefix="cc_research_ev_")
    monkeypatch.setenv("SKILLS_BROKER_KEY", "research-evidence-secret-key-42")
    for sub in ("research/problems/%s" % PID, "teaching/agent-calls",
                "teaching/agent-requests", "teaching/checks"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _write(root, rel, doc):
    abs_p = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(abs_p), exist_ok=True)
    with open(abs_p, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    return abs_p


def _write_top_level(root):
    _write(root, "research/run.json", {"run_id": "run-research"})
    _write(root, "research/selection.json",
           {"selected_ids": [PID], "candidates": [{"problem_id": PID}]})
    _write(root, "research/brief.json",
           {"brief_id": "brief-1",
            "selection": {"selected_problem_ids": [PID]}})
    _write(root, "research/problems/%s/dossier.json" % PID,
           {"problem_id": PID})


def _triplet(root, meta):
    """写三件套；meta 为 execution_metadata（None 表示缺字段）。"""
    docs = {}
    for role in ("probe", "pitfall", "solution"):
        rel = "research/problems/%s/%s.json" % (PID, role)
        doc = {"problem_id": PID, "role": role}
        if meta is not None:
            doc["execution_metadata"] = meta
        _write(root, rel, doc)
        docs[role] = rel
    return docs


def _sign_evidence(root, dispatch_id, rel=None, sha=None,
                   worker="subagent:courseware-x",
                   provider="anthropic", model="claude-3-7-sonnet",
                   output_digests=None, artifacts_written=None):
    if output_digests is None:
        output_digests = {rel: sha}
    if artifacts_written is None:
        artifacts_written = list(output_digests.keys())
    return agent_evidence.write_evidence(
        root, dispatch_id=dispatch_id, batch_id=None, worker=worker,
        provider=provider, model=model, artifacts_written=artifacts_written,
        output_digests=output_digests, status=agent_evidence.EVIDENCE_STATUS_OK)


# ---------------------------------------------------------------------------
# 单元层：_verify_artifact_evidence 攻击面
# ---------------------------------------------------------------------------

def test_missing_execution_metadata_is_rejected(research_env):
    """攻击：主 Agent 直写产物且完全不写 execution_metadata。"""
    rel = "research/problems/%s/probe.json" % PID
    _write(research_env, rel, {"problem_id": PID})
    ok, reason, detail = check_research._verify_artifact_evidence(
        research_env, PID, "probe", rel, {"problem_id": PID})
    assert ok is False
    assert reason == "missing_execution_metadata"


def test_empty_dispatch_id_is_rejected(research_env):
    """攻击：execution_metadata 存在但 dispatch_id / subagent_conversation_id 皆空。"""
    rel = "research/problems/%s/probe.json" % PID
    _write(research_env, rel, {"problem_id": PID})
    ok, reason, _ = check_research._verify_artifact_evidence(
        research_env, PID, "probe", rel,
        {"execution_metadata": {"subagent_conversation_id": ""}})
    assert ok is False
    assert reason == "missing_dispatch_id"


def test_evidence_missing_is_rejected(research_env):
    """攻击：填了 dispatch_id 但 teaching/agent-calls 无对应台账。"""
    rel = "research/problems/%s/probe.json" % PID
    _write(research_env, rel, {"problem_id": PID})
    ok, reason, detail = check_research._verify_artifact_evidence(
        research_env, PID, "probe", rel,
        {"execution_metadata": {"dispatch_id": "disp-no-ledger"}})
    assert ok is False
    assert reason == "evidence_missing"
    assert detail["dispatch_id"] == "disp-no-ledger"


def test_forged_evidence_is_rejected(research_env):
    """攻击：自拼台账但签名无效。"""
    rel = "research/problems/%s/probe.json" % PID
    _write(research_env, rel, {"problem_id": PID})
    _write(research_env, "teaching/agent-calls/disp-forged.json", {
        "schema_version": 1, "dispatch_id": "disp-forged",
        "worker": "subagent:courseware-x",
        "output_digests": {rel: "0" * 64}, "status": "ok",
        "signature": "deadbeef" * 8,
    })
    ok, reason, _ = check_research._verify_artifact_evidence(
        research_env, PID, "probe", rel,
        {"execution_metadata": {"dispatch_id": "disp-forged"}})
    assert ok is False
    assert reason == "EVIDENCE_FORGED"


def test_evidence_id_mismatch_is_rejected(research_env):
    """攻击：把别的派发单台账改名复用（断点续跑嫁接）。"""
    rel = "research/problems/%s/probe.json" % PID
    abs_p = _write(research_env, rel, {"problem_id": PID})
    _sign_evidence(research_env, "disp-real", rel,
                   contract.sha256_file(abs_p))
    # 嫁接：把 disp-real 的台账原样复制成 disp-other.json（签名本身有效）
    shutil.copyfile(
        os.path.join(research_env, "teaching", "agent-calls", "disp-real.json"),
        os.path.join(research_env, "teaching", "agent-calls", "disp-other.json"))
    ok, reason, _ = check_research._verify_artifact_evidence(
        research_env, PID, "probe", rel,
        {"execution_metadata": {"dispatch_id": "disp-other"}})
    assert ok is False
    assert reason == "EVIDENCE_ID_MISMATCH"


def test_worker_must_be_subagent_identity(research_env):
    """攻击：台账 worker 写成 main / self（主进程代劳冒名）。"""
    rel = "research/problems/%s/probe.json" % PID
    abs_p = _write(research_env, rel, {"problem_id": PID})
    sha = contract.sha256_file(abs_p)
    _sign_evidence(research_env, "disp-worker", rel, sha, worker="main")
    ok, reason, _ = check_research._verify_artifact_evidence(
        research_env, PID, "probe", rel,
        {"execution_metadata": {"dispatch_id": "disp-worker"}})
    assert ok is False
    assert reason == "evidence_worker_invalid"


def test_artifact_not_recorded_in_ledger(research_env):
    """攻击：台账存在但没登记本产物。"""
    rel = "research/problems/%s/probe.json" % PID
    abs_p = _write(research_env, rel, {"problem_id": PID})
    other_rel = "research/problems/%s/pitfall.json" % PID
    _write(research_env, other_rel, {"problem_id": PID})
    _sign_evidence(research_env, "disp-other", other_rel,
                   contract.sha256_file(os.path.join(research_env,
                                                     other_rel.replace("/", os.sep))))
    ok, reason, _ = check_research._verify_artifact_evidence(
        research_env, PID, "probe", rel,
        {"execution_metadata": {"dispatch_id": "disp-other"}})
    assert ok is False
    assert reason == "evidence_artifact_not_recorded"


def test_stale_artifact_after_signing(research_env):
    """攻击：签名落盘后偷梁换柱改写产物。"""
    rel = "research/problems/%s/probe.json" % PID
    abs_p = _write(research_env, rel, {"problem_id": PID, "v": 1})
    sha = contract.sha256_file(abs_p)
    _sign_evidence(research_env, "disp-stale", rel, sha)
    with open(abs_p, "w", encoding="utf-8") as f:
        json.dump({"problem_id": PID, "v": 2}, f)
    ok, reason, _ = check_research._verify_artifact_evidence(
        research_env, PID, "probe", rel,
        {"execution_metadata": {"dispatch_id": "disp-stale"}})
    assert ok is False
    assert reason == "evidence_stale"


def test_status_not_ok_is_rejected(research_env):
    """攻击：台账 status=worker_error（派发未完成）却声称产物有效。"""
    rel = "research/problems/%s/probe.json" % PID
    abs_p = _write(research_env, rel, {"problem_id": PID})
    agent_evidence.write_evidence(
        research_env, dispatch_id="disp-err", worker="subagent:courseware-x",
        artifacts_written=[rel],
        output_digests={rel: contract.sha256_file(abs_p)},
        status="worker_error")
    ok, reason, _ = check_research._verify_artifact_evidence(
        research_env, PID, "probe", rel,
        {"execution_metadata": {"dispatch_id": "disp-err"}})
    assert ok is False
    assert reason == "evidence_status_not_ok"


def test_valid_attested_artifact_passes(research_env):
    """正样本：合法派发 + 签名台账 + 摘要一致 → 通过并回显审计明细。"""
    rel = "research/problems/%s/probe.json" % PID
    abs_p = _write(research_env, rel, {"problem_id": PID, "v": 1})
    sha = contract.sha256_file(abs_p)
    _sign_evidence(research_env, "disp-ok", rel, sha)
    ok, reason, detail = check_research._verify_artifact_evidence(
        research_env, PID, "probe", rel,
        {"execution_metadata": {"dispatch_id": "disp-ok"}})
    assert ok is True
    assert reason == "ok"
    assert detail == {"dispatch_id": "disp-ok",
                      "worker": "subagent:courseware-x",
                      "provider": "anthropic",
                      "model": "claude-3-7-sonnet"}


def test_legacy_brain_mode_requires_real_session_dir(research_env, monkeypatch):
    """legacy 旁路：仅显式 SKILLS_BRAIN_DIR 时启用，且仍要求真实 brain 会话目录。"""
    rel = "research/problems/%s/probe.json" % PID
    _write(research_env, rel, {"problem_id": PID})
    brain = os.path.join(research_env, "brain")
    monkeypatch.setattr(check_research, "_AGY_BRAIN_DIR_RAW", brain)
    monkeypatch.setattr(check_research, "_AGY_BRAIN_DIR", brain)
    uuid_id = "11111111-2222-3333-4444-555555555555"
    ok, reason, detail = check_research._verify_artifact_evidence(
        research_env, PID, "probe", rel,
        {"execution_metadata": {"dispatch_id": uuid_id}})
    assert ok is False
    assert reason == "no_local_session_dir"
    assert detail["legacy"] is True


# ---------------------------------------------------------------------------
# 门禁层：check() 端到端
# ---------------------------------------------------------------------------

def _run_check(env, monkeypatch):
    monkeypatch.setattr(check_research, "schema_errors",
                        lambda doc, name, rel: [])
    fails, reviews, evidence, fatal = check_research.check(env)
    return fails, reviews, evidence, fatal


def test_gate_fails_when_triplet_unattested(research_env, monkeypatch):
    """端到端：三件套全部由主 Agent 直写（无台账）→ research 门禁 fail。"""
    _write_top_level(research_env)
    _triplet(research_env, {"dispatch_id": "disp-never-existed"})
    fails, _reviews, evidence, fatal = _run_check(research_env, monkeypatch)
    assert fatal == []
    joined = "\n".join(fails)
    assert "[UNATTESTED_ARTIFACT]" in joined
    assert "courseware_dispatch" in joined
    assert "invoke_subagent" not in joined
    assert len(evidence["agent_evidence"]) == 0


def test_gate_passes_when_triplet_fully_attested(research_env, monkeypatch):
    """端到端正样本：三角色各自合法派发且摘要一致 → 无 §1b 失败。"""
    _write_top_level(research_env)
    for role in ("probe", "pitfall", "solution"):
        rel = "research/problems/%s/%s.json" % (PID, role)
        abs_p = _write(research_env, rel,
                       {"problem_id": PID, "role": role,
                        "execution_metadata": {"dispatch_id": "disp-%s" % role}})
        _sign_evidence(research_env, "disp-%s" % role, rel,
                       contract.sha256_file(abs_p))
    fails, _reviews, evidence, fatal = _run_check(research_env, monkeypatch)
    assert fatal == []
    assert not [f for f in fails if "EVIDENCE" in f or "SUBAGENT" in f
                or "EXECUTION_METADATA" in f or "UNATTESTED" in f]
    assert len(evidence["agent_evidence"]) == 3
    assert {e["dispatch_id"] for e in evidence["agent_evidence"]} == {
        "disp-probe", "disp-pitfall", "disp-solution"}


def test_gate_rejects_duplicate_dispatch_id_across_triplet(research_env,
                                                           monkeypatch):
    """攻击：三角色复用同一 dispatch_id（一次派发热蒸三份产物，违反认知隔离）。"""
    _write_top_level(research_env)
    digests = {}
    for role in ("probe", "pitfall", "solution"):
        rel = "research/problems/%s/%s.json" % (PID, role)
        abs_p = _write(research_env, rel,
                       {"problem_id": PID, "role": role,
                        "execution_metadata": {"dispatch_id": "disp-shared"}})
        digests[rel] = contract.sha256_file(abs_p)
    # 单一台账同时背书三件套（三个角色必须由互相独立的派发完成）
    _sign_evidence(research_env, "disp-shared", None, None,
                   output_digests=digests,
                   artifacts_written=sorted(digests.keys()))
    fails, _reviews, evidence, fatal = _run_check(research_env, monkeypatch)
    assert fatal == []
    assert any("dispatch_id 存在重复" in f for f in fails)
    # 三角色各自单独看都"合规"，靠互异检测才拦住
    assert len(evidence["agent_evidence"]) == 3
