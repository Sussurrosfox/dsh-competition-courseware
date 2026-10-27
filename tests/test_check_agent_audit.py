# -*- coding: utf-8 -*-
"""
tests/test_check_agent_audit.py — 测试 check_agent_audit.py 门禁审计器
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import pytest

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PLUGIN_ROOT = os.path.abspath(os.path.join(_TESTS_DIR, ".."))
_SCRIPTS_DIR = os.path.join(_PLUGIN_ROOT, "scripts")

for p in (_PLUGIN_ROOT, _SCRIPTS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import contract
from workflow.nodes import agent_evidence
from scripts import check_agent_audit


@pytest.fixture
def temp_project(monkeypatch):
    d = tempfile.mkdtemp(prefix="cc_audit_test_")
    monkeypatch.setenv("COURSEWARE_BROKER_KEY", "audit-test-secret-key-12345")
    # 创建最基础结构
    os.makedirs(os.path.join(d, "teaching", "checks"), exist_ok=True)
    os.makedirs(os.path.join(d, "teaching", "agent-requests"), exist_ok=True)
    os.makedirs(os.path.join(d, "teaching", "agent-calls"), exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_empty_workspace_passes(temp_project):
    """干净工作区无请求无产物，审计直接 PASS"""
    status, findings, data = check_agent_audit.audit_agent_evidence(temp_project)
    assert status == "pass"
    assert data["checked"]["dispatches"] == 0


def test_unattested_artifacts_detected(temp_project):
    """存在教学产物但无任何派发单记录，判定 FAIL (UNATTESTED_ARTIFACTS)"""
    arch_file = os.path.join(temp_project, "teaching", "architect-plan.json")
    with open(arch_file, "w", encoding="utf-8") as f:
        json.dump({"title": "Direct Write without dispatch"}, f)

    status, findings, data = check_agent_audit.audit_agent_evidence(temp_project)
    assert status == "fail"
    codes = [f.get("code") for f in findings]
    assert "UNATTESTED_ARTIFACTS" in codes


def test_valid_attested_dispatch(temp_project):
    """有效签名证据的派发单通过审计"""
    art_rel = "teaching/architect-plan.json"
    art_abs = os.path.join(temp_project, "teaching", "architect-plan.json")
    with open(art_abs, "w", encoding="utf-8") as f:
        json.dump({"title": "Valid Architect Plan"}, f)
    art_sha = contract.sha256_file(art_abs)

    dispatch_id = "req_audit_001"
    request = {
        "dispatch_id": dispatch_id,
        "stage": "T1",
        "scope": "architect",
        "artifacts": [art_rel],
        "before": {art_rel: None},
    }
    req_file = os.path.join(temp_project, "teaching", "agent-requests", f"{dispatch_id}.json")
    with open(req_file, "w", encoding="utf-8") as f:
        json.dump(request, f)

    agent_evidence.write_evidence(
        temp_project,
        dispatch_id=dispatch_id,
        worker="subagent",
        model="claude-3-7-sonnet",
        output_digests={art_rel: art_sha},
        status=agent_evidence.EVIDENCE_STATUS_OK,
    )

    status, findings, data = check_agent_audit.audit_agent_evidence(temp_project)
    assert status == "pass"
    assert data["checked"]["attested"] == 1
    assert data["checked"]["unattested"] == 0


def test_missing_evidence_fails_or_reviews(temp_project):
    """派发单产物已变但缺少证据台账：默认 FAIL；开启 allow_unattested 则 REVIEW"""
    art_rel = "teaching/architect-plan.json"
    art_abs = os.path.join(temp_project, "teaching", "architect-plan.json")
    with open(art_abs, "w", encoding="utf-8") as f:
        json.dump({"title": "Direct Main Agent Write"}, f)

    dispatch_id = "req_audit_missing"
    request = {
        "dispatch_id": dispatch_id,
        "stage": "T1",
        "scope": "architect",
        "artifacts": [art_rel],
        "before": {art_rel: None},
    }
    req_file = os.path.join(temp_project, "teaching", "agent-requests", f"{dispatch_id}.json")
    with open(req_file, "w", encoding="utf-8") as f:
        json.dump(request, f)

    # 1. 默认门禁拒绝
    status, findings, data = check_agent_audit.audit_agent_evidence(temp_project, allow_unattested=False)
    assert status == "fail"
    assert data["checked"]["unattested"] == 1
    assert any(f.get("code") == "EVIDENCE_MISSING" for f in findings)

    # 2. 逃生阀 allow_unattested=True
    status_esc, findings_esc, data_esc = check_agent_audit.audit_agent_evidence(temp_project, allow_unattested=True)
    assert status_esc == "review"
    assert data_esc["checked"]["unattested"] == 1


def test_tampered_and_forged_evidence(temp_project):
    """测试产物被篡改 (attested_mismatch) 与伪造签名 (EVIDENCE_FORGED)"""
    art_rel = "teaching/architect-plan.json"
    art_abs = os.path.join(temp_project, "teaching", "architect-plan.json")
    with open(art_abs, "w", encoding="utf-8") as f:
        json.dump({"title": "Original Plan"}, f)
    art_sha = contract.sha256_file(art_abs)

    dispatch_id = "req_audit_tampered"
    request = {
        "dispatch_id": dispatch_id,
        "stage": "T1",
        "scope": "architect",
        "artifacts": [art_rel],
        "before": {art_rel: None},
    }
    req_file = os.path.join(temp_project, "teaching", "agent-requests", f"{dispatch_id}.json")
    with open(req_file, "w", encoding="utf-8") as f:
        json.dump(request, f)

    agent_evidence.write_evidence(
        temp_project,
        dispatch_id=dispatch_id,
        worker="subagent",
        model="model-x",
        output_digests={art_rel: art_sha},
        status=agent_evidence.EVIDENCE_STATUS_OK,
    )

    # 1. 篡改文件内容
    with open(art_abs, "w", encoding="utf-8") as f:
        json.dump({"title": "Tampered Plan by Main Agent"}, f)

    status, findings, data = check_agent_audit.audit_agent_evidence(temp_project)
    assert status == "fail"
    assert data["checked"]["mismatch"] == 1
    assert any(f.get("code") == "EVIDENCE_STALE" for f in findings)

    # 2. 伪造签名
    call_file = os.path.join(temp_project, "teaching", "agent-calls", f"{dispatch_id}.json")
    with open(call_file, "r", encoding="utf-8") as f:
        ev_data = json.load(f)
    ev_data["signature"] = "forged_invalid_signature_hex"
    with open(call_file, "w", encoding="utf-8") as f:
        json.dump(ev_data, f)

    status_f, findings_f, data_f = check_agent_audit.audit_agent_evidence(temp_project)
    assert status_f == "fail"
    assert data_f["checked"]["forged"] == 1
    assert any(f.get("code") == "EVIDENCE_FORGED" for f in findings_f)


def test_superseded_dispatch(temp_project):
    """测试被后续派发覆盖的旧派发单 (superseded)"""
    art_rel = "teaching/architect-plan.json"
    art_abs = os.path.join(temp_project, "teaching", "architect-plan.json")
    with open(art_abs, "w", encoding="utf-8") as f:
        json.dump({"title": "Repaired Plan"}, f)
    art_sha = contract.sha256_file(art_abs)

    # 旧派发单 001（产物哈希不匹配当前磁盘）
    req1 = {
        "dispatch_id": "req_audit_001",
        "stage": "T1",
        "scope": "architect",
        "artifacts": [art_rel],
        "before": {art_rel: None},
    }
    with open(os.path.join(temp_project, "teaching", "agent-requests", "req_audit_001.json"), "w", encoding="utf-8") as f:
        json.dump(req1, f)
    agent_evidence.write_evidence(
        temp_project,
        dispatch_id="req_audit_001",
        worker="subagent",
        model="model-x",
        output_digests={art_rel: "old_outdated_sha256"},
        status=agent_evidence.EVIDENCE_STATUS_OK,
    )

    # 新派发单 002（定点修复，认证当前磁盘最新哈希）
    req2 = {
        "dispatch_id": "req_audit_002",
        "stage": "T1_repair",
        "scope": "architect",
        "artifacts": [art_rel],
        "before": {art_rel: "old_outdated_sha256"},
    }
    with open(os.path.join(temp_project, "teaching", "agent-requests", "req_audit_002.json"), "w", encoding="utf-8") as f:
        json.dump(req2, f)
    agent_evidence.write_evidence(
        temp_project,
        dispatch_id="req_audit_002",
        worker="subagent",
        model="model-x",
        output_digests={art_rel: art_sha},
        status=agent_evidence.EVIDENCE_STATUS_OK,
    )

    status, findings, data = check_agent_audit.audit_agent_evidence(temp_project)
    assert status == "pass"
    assert data["checked"]["superseded"] == 1
    assert data["checked"]["attested"] == 1


def test_cli_execution(temp_project):
    """测试命令行执行与退出码"""
    cmd = [sys.executable, os.path.join(_SCRIPTS_DIR, "check_agent_audit.py"), temp_project]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0
    assert "status=pass" in r.stdout
