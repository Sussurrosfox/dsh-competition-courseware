import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

import json
import uuid
import hmac
import hashlib
import pytest
from unittest.mock import patch, MagicMock

import contract
from workflow.nodes import agent_evidence
from workflow.nodes import agent_nodes


def test_build_request_dispatch_token(tmp_path):
    root = str(tmp_path)
    state = {"project_root": root, "run_id": "run-001"}
    art = "teaching/architect-plan.json"
    req = agent_nodes.build_request(
        state,
        stage="T1_ARCHITECT",
        role="architect",
        prompt_file="architect-agent.md",
        schema_file="architect.schema.json",
        artifacts=[art],
        instruction="test instruction",
        command="test command",
        scope="prob1",
    )
    assert req["dispatch_id"].startswith("dsp-t1-architect-prob1-")
    assert "dispatch_token" in req

    token_payload = {
        "artifacts": [art],
        "before": {art: None},
        "dispatch_id": req["dispatch_id"],
    }
    expected_token = hmac.new(
        contract.broker_key(),
        contract.canonical_json(token_payload).encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    assert req["dispatch_token"] == expected_token


def test_interrupt_payload_protocol(tmp_path):
    root = str(tmp_path)
    state = {"project_root": root, "run_id": "run-001"}
    req = agent_nodes.build_request(
        state,
        stage="T1_ARCHITECT",
        role="architect",
        prompt_file="architect-agent.md",
        schema_file="architect.schema.json",
        artifacts=["teaching/architect-plan.json"],
        instruction="test",
        command="test",
    )
    payload = agent_nodes._interrupt_payload(req)
    assert payload["dispatch_protocol"] == agent_evidence.DISPATCH_PROTOCOL
    assert payload["required_tool"] == agent_evidence.REQUIRED_DISPATCH_TOOL
    assert payload["forbidden"] == list(agent_evidence.FORBIDDEN_DIRECT_WRITES)


def test_run_dispatch_bad_protocol_violation(tmp_path):
    root = str(tmp_path)
    state = {"project_root": root, "run_id": "run-001", "history": [], "dispatch_results": {}}
    req = agent_nodes.build_request(
        state,
        stage="T1_ARCHITECT",
        role="architect",
        prompt_file="architect-agent.md",
        schema_file="architect.schema.json",
        artifacts=["teaching/architect-plan.json"],
        instruction="test",
        command="test",
    )
    agent_nodes.write_request_file(root, req)

    # Mock interrupt returning bad dispatch_protocol
    with patch("workflow.nodes.agent_nodes.interrupt", return_value={"dispatch_protocol": "unauthorized-v0"}):
        res = agent_nodes.run_dispatch(state, req, success_stage="SUCCESS", success_history="done")
        assert res["routing_action"] == "HALT"
        assert res["stage"] == "ABORTED"
        assert "BAD_DISPATCH_PROTOCOL" in res["error_message"]


def test_run_dispatch_ok_unattested_rejected_by_default(tmp_path):
    root = str(tmp_path)
    art_rel = "teaching/architect-plan.json"
    state = {"project_root": root, "run_id": "run-001", "history": [], "dispatch_results": {}}
    req = agent_nodes.build_request(
        state,
        stage="T1_ARCHITECT",
        role="architect",
        prompt_file="architect-agent.md",
        schema_file="architect.schema.json",
        artifacts=[art_rel],
        instruction="test",
        command="test",
    )
    agent_nodes.write_request_file(root, req)

    # Mock interrupt returning valid protocol and files, but NO evidence on disk (unattested write)
    with patch("workflow.nodes.agent_nodes.interrupt", return_value={
        "dispatch_protocol": "host-subagent-v1",
        "files": {art_rel: '{"test": "content"}'}
    }):
        res = agent_nodes.run_dispatch(state, req, success_stage="SUCCESS", success_history="done")
        assert res["routing_action"] == "HALT"
        assert "缺少代理证据" in res["error_message"]
        assert res["dispatch_results"][req["dispatch_id"]]["status"] == "ok_unattested"


def test_run_dispatch_with_valid_evidence(tmp_path):
    root = str(tmp_path)
    art_rel = "teaching/architect-plan.json"
    state = {"project_root": root, "run_id": "run-001", "history": [], "dispatch_results": {}}
    req = agent_nodes.build_request(
        state,
        stage="T1_ARCHITECT",
        role="architect",
        prompt_file="architect-agent.md",
        schema_file="architect.schema.json",
        artifacts=[art_rel],
        instruction="test",
        command="test",
    )
    agent_nodes.write_request_file(root, req)

    # Actually write the artifact and signed evidence
    art_abs = os.path.join(root, "teaching", "architect-plan.json")
    os.makedirs(os.path.dirname(art_abs), exist_ok=True)
    content = '{"valid": true}'
    with open(art_abs, "w", encoding="utf-8") as f:
        f.write(content)

    h = hashlib.sha256(content.encode("utf-8")).hexdigest()
    agent_evidence.write_evidence(
        root,
        dispatch_id=req["dispatch_id"],
        worker="subagent:courseware-001",
        artifacts_written=[art_rel],
        output_digests={art_rel: h},
        status="ok"
    )

    with patch("workflow.nodes.agent_nodes.interrupt", return_value={"dispatch_protocol": "host-subagent-v1"}):
        res = agent_nodes.run_dispatch(state, req, success_stage="SUCCESS", success_history="done")
        assert res["routing_action"] == "PROCEED"
        assert res["stage"] == "SUCCESS"
        assert res["dispatch_results"][req["dispatch_id"]]["status"] == "ok"
        assert res["dispatch_results"][req["dispatch_id"]]["verified_via"] == "agent"


def test_run_dispatch_attested_mismatch_rejected(tmp_path):
    root = str(tmp_path)
    art_rel = "teaching/architect-plan.json"
    state = {"project_root": root, "run_id": "run-001", "history": [], "dispatch_results": {}}
    req = agent_nodes.build_request(
        state,
        stage="T1_ARCHITECT",
        role="architect",
        prompt_file="architect-agent.md",
        schema_file="architect.schema.json",
        artifacts=[art_rel],
        instruction="test",
        command="test",
    )
    agent_nodes.write_request_file(root, req)

    # Write evidence with digest 'abc'
    agent_evidence.write_evidence(
        root,
        dispatch_id=req["dispatch_id"],
        worker="subagent:courseware-001",
        artifacts_written=[art_rel],
        output_digests={art_rel: "abc0000000000000000000000000000000000000000000000000000000000000"},
        status="ok"
    )

    # But file on disk has different digest
    art_abs = os.path.join(root, "teaching", "architect-plan.json")
    os.makedirs(os.path.dirname(art_abs), exist_ok=True)
    with open(art_abs, "w", encoding="utf-8") as f:
        f.write('{"tampered": true}')

    with patch("workflow.nodes.agent_nodes.interrupt", return_value={"dispatch_protocol": "host-subagent-v1"}):
        res = agent_nodes.run_dispatch(state, req, success_stage="SUCCESS", success_history="done")
        assert res["routing_action"] == "HALT"
        assert "产物与代理证据不符" in res["error_message"]
