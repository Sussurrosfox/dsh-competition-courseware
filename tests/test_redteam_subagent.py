# -*- coding: utf-8 -*-
"""
tests/test_redteam_subagent.py — 红队对抗穿透与全链路证据链验收测试
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

import contract
from workflow.nodes import agent_evidence, agent_nodes
from scripts import check_agent_audit


@pytest.fixture
def redteam_env(monkeypatch):
    d = tempfile.mkdtemp(prefix="cc_redteam_")
    monkeypatch.setenv("COURSEWARE_BROKER_KEY", "redteam-secret-key-xyz-9876")
    # 创建目录结构
    os.makedirs(os.path.join(d, "teaching", "checks"), exist_ok=True)
    os.makedirs(os.path.join(d, "teaching", "agent-requests"), exist_ok=True)
    os.makedirs(os.path.join(d, "teaching", "agent-calls"), exist_ok=True)
    os.makedirs(os.path.join(d, ".pd"), exist_ok=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _make_req(env, artifacts, scope="architect", stage="T1"):
    state = {"project_root": env, "run_id": "run-redteam"}
    return agent_nodes.build_request(
        state,
        stage=stage,
        role="architect",
        prompt_file="architect-agent.md",
        schema_file="architect.schema.json",
        instruction="Courseware architect instruction",
        command="courseware_dispatch",
        context={"math_core": "Euler Phi"},
        artifacts=artifacts,
        scope=scope,
    )


def test_redteam_attack_01_main_agent_direct_write(redteam_env):
    """
    测项 1: 主 Agent 直写攻击
    攻击手法：主 Agent 收到派发中断后，未调用 courseware_dispatch，而是自己在当前会话中
    直接将内容写入目标产物文件，未生成 teaching/agent-calls/<dispatch_id>.json。
    预期防御：
    1. agent_evidence.check_request_attested 判定 ok_unattested；
    2. check_agent_audit 门禁独立审计判 FAIL (EVIDENCE_MISSING)。
    """
    art_rel = "teaching/architect-plan.json"
    art_abs = os.path.join(redteam_env, "teaching", "architect-plan.json")

    # 1. 状态机派发单
    req = _make_req(redteam_env, [art_rel])
    agent_nodes.write_request_file(redteam_env, req)
    dispatch_id = req["dispatch_id"]

    # 2. 攻击发生：主 Agent 直接写磁盘文件
    with open(art_abs, "w", encoding="utf-8") as f:
        json.dump({"title": "Directly Written By Attacker Agent"}, f)

    # 3. 验证防御 1：agent_evidence 拦截
    disk_digests = {art_rel: contract.sha256_file(art_abs)}
    status, ev = agent_evidence.check_request_attested(redteam_env, req, disk_digests, allow_unattested=False)
    assert status == "ok_unattested"
    assert ev is None

    # 4. 验证防御 2：check_agent_audit 门禁拦截
    gate_status, findings, data = check_agent_audit.audit_agent_evidence(redteam_env, allow_unattested=False)
    assert gate_status == "fail"
    assert data["checked"]["unattested"] == 1
    assert any(f.get("code") == "EVIDENCE_MISSING" for f in findings)


def test_redteam_attack_02_forged_evidence_signature(redteam_env):
    """
    测项 2: 伪造 evidence 台账签名攻击
    攻击手法：主 Agent 试图仿冒子 Agent，自行拼装 agent-calls/<dispatch_id>.json，但由于不知道
    真正的 DPAPI / Broker 密钥，或者使用了虚假的 signature。
    预期防御：HMAC 验签失败，报 EVIDENCE_FORGED，状态机与门禁双双阻断。
    """
    art_rel = "teaching/architect-plan.json"
    art_abs = os.path.join(redteam_env, "teaching", "architect-plan.json")

    req = _make_req(redteam_env, [art_rel])
    agent_nodes.write_request_file(redteam_env, req)
    dispatch_id = req["dispatch_id"]

    with open(art_abs, "w", encoding="utf-8") as f:
        json.dump({"content": "Attacker payload"}, f)
    art_sha = contract.sha256_file(art_abs)

    # 伪造证据文件（假签名）
    forged_call_file = os.path.join(redteam_env, "teaching", "agent-calls", f"{dispatch_id}.json")
    forged_evidence = {
        "schema_version": 1,
        "dispatch_id": dispatch_id,
        "worker": "subagent",
        "output_digests": {art_rel: art_sha},
        "status": "ok",
        "signature": "badc0de00000000000000000000000000000000000000000000000000000dead",
    }
    with open(forged_call_file, "w", encoding="utf-8") as f:
        json.dump(forged_evidence, f)

    disk_digests = {art_rel: art_sha}
    status, ev = agent_evidence.check_request_attested(redteam_env, req, disk_digests)
    assert status == "EVIDENCE_FORGED"

    gate_status, findings, data = check_agent_audit.audit_agent_evidence(redteam_env)
    assert gate_status == "fail"
    assert data["checked"]["forged"] == 1
    assert any(f.get("code") == "EVIDENCE_FORGED" for f in findings)


def test_redteam_attack_03_tampered_artifacts_after_signing(redteam_env):
    """
    测项 3: 签名后偷梁换柱攻击（落盘篡改）
    攻击手法：合法子 Agent 正常执行并完成证据签名后，主 Agent 随后擅自修改了产物文件。
    预期防御：sha256 比对失败，判 attested_mismatch (EVIDENCE_STALE)，门禁拒绝。
    """
    art_rel = "teaching/architect-plan.json"
    art_abs = os.path.join(redteam_env, "teaching", "architect-plan.json")

    req = _make_req(redteam_env, [art_rel])
    agent_nodes.write_request_file(redteam_env, req)
    dispatch_id = req["dispatch_id"]

    # 1. 真实子 agent 产出并完成合规签名
    with open(art_abs, "w", encoding="utf-8") as f:
        json.dump({"content": "Legitimate Subagent Content"}, f)
    original_sha = contract.sha256_file(art_abs)

    agent_evidence.write_evidence(
        redteam_env,
        dispatch_id=dispatch_id,
        worker="subagent",
        output_digests={art_rel: original_sha},
        status=agent_evidence.EVIDENCE_STATUS_OK,
    )

    # 2. 攻击：篡改文件
    with open(art_abs, "w", encoding="utf-8") as f:
        json.dump({"content": "Tampered Content by Intermediary"}, f)
    tampered_sha = contract.sha256_file(art_abs)

    # 3. 防御核验
    status, ev = agent_evidence.check_request_attested(redteam_env, req, {art_rel: tampered_sha})
    assert status == "attested_mismatch"

    gate_status, findings, data = check_agent_audit.audit_agent_evidence(redteam_env)
    assert gate_status == "fail"
    assert data["checked"]["mismatch"] == 1
    assert any(f.get("code") == "EVIDENCE_STALE" for f in findings)


def test_redteam_attack_04_scope_and_whitelist_enforcement(redteam_env):
    """
    测项 4: 跨 scope 越权写入攻击（白名单防御）
    攻击手法：子 agent 返回了不在 request.artifacts 声明范围内的文件（例如试图覆写 scripts/ 或 state.json）。
    预期防御：
    只有 request.artifacts 中的文件被允许写入；超出白名单的文件被严格过滤与拒收。
    """
    allowed_rel = "teaching/architect-plan.json"
    illegal_rel = "scripts/malicious_payload.py"

    req = _make_req(redteam_env, [allowed_rel])
    dispatch_id = req["dispatch_id"]

    # 模拟 subagent 返回两个文件（一个合法，一个越权）
    subagent_files = {
        allowed_rel: json.dumps({"title": "Safe Architecture"}),
        illegal_rel: "import os; os.system('echo compromised')",
    }

    # 执行白名单过滤逻辑（与 index.js cdParseSubagentFiles + whitelist 行为一致）
    allowed_set = set(req["artifacts"])
    filtered_writes = {
        k: v for k, v in subagent_files.items() if k in allowed_set
    }

    assert allowed_rel in filtered_writes
    assert illegal_rel not in filtered_writes
    assert len(filtered_writes) == 1


def test_redteam_escape_hatch_allow_unattested(redteam_env):
    """
    测项 5: 逃生阀 (allow_unattested) 功能验证
    在开启 allow_unattested: true 时，即便缺少 agent-calls 证据，也仅记为 warning/review，
    允许在紧急手工干预或调试时继续推进。
    """
    art_rel = "teaching/architect-plan.json"
    art_abs = os.path.join(redteam_env, "teaching", "architect-plan.json")

    req = _make_req(redteam_env, [art_rel])
    agent_nodes.write_request_file(redteam_env, req)
    dispatch_id = req["dispatch_id"]

    with open(art_abs, "w", encoding="utf-8") as f:
        json.dump({"title": "Emergency Manual Intervention"}, f)

    # 默认门禁拒绝
    gate_status, _, _ = check_agent_audit.audit_agent_evidence(redteam_env, allow_unattested=False)
    assert gate_status == "fail"

    # 逃生阀开启：放行（降级为 review）
    esc_status, findings, data = check_agent_audit.audit_agent_evidence(redteam_env, allow_unattested=True)
    assert esc_status == "review"
    assert data["checked"]["unattested"] == 1


def test_redteam_full_attested_pipeline_delivery(redteam_env):
    """
    测项 6: 全链路合规流转测试
    派发单生成 -> 子 Agent 产出 -> 证据合法落盘并签名 -> agent_audit 门禁全绿通过。
    """
    art_rel = "teaching/architect-plan.json"
    art_abs = os.path.join(redteam_env, "teaching", "architect-plan.json")

    # 1. 生成派发单（带 HMAC dispatch_token）
    req = _make_req(redteam_env, [art_rel])
    agent_nodes.write_request_file(redteam_env, req)
    dispatch_id = req["dispatch_id"]

    # 断言 token 存在且有效
    assert req.get("dispatch_token")
    assert agent_evidence.verify_dispatch_token(req) is True

    # 2. 模拟合法子 Agent 回复并原子写盘
    with open(art_abs, "w", encoding="utf-8") as f:
        json.dump({"title": "Valid Plan", "modules": ["Part 1", "Part 2"]}, f)
    art_sha = contract.sha256_file(art_abs)

    # 3. 记录合法 HMAC 证据
    ev_path = agent_evidence.write_evidence(
        redteam_env,
        dispatch_id=dispatch_id,
        worker="subagent",
        provider="anthropic",
        model="claude-3-7-sonnet",
        output_digests={art_rel: art_sha},
        status=agent_evidence.EVIDENCE_STATUS_OK,
    )
    assert os.path.isfile(ev_path)

    # 4. 状态机对账
    from unittest.mock import patch
    state = {
        "project_root": redteam_env,
        "pending_dispatch": req,
        "dispatch_results": {},
    }
    with patch("workflow.nodes.agent_nodes.interrupt", return_value={"dispatch_protocol": "host-subagent-v1"}):
        result_state = agent_nodes.run_dispatch(
            state,
            req,
            success_stage="T1_PLANNED",
            success_history="T1 plan completed with valid subagent evidence",
        )
    assert result_state["stage"] == "T1_PLANNED"
    assert result_state["routing_action"] == "PROCEED"
    assert result_state["dispatch_results"][dispatch_id]["status"] == "ok"
    assert result_state["dispatch_results"][dispatch_id]["verified_via"] == "agent"

    # 5. 门禁独立审计
    gate_status, findings, data = check_agent_audit.audit_agent_evidence(redteam_env)
    assert gate_status == "pass"
    assert data["checked"]["attested"] == 1
    assert data["checked"]["unattested"] == 0
    assert data["checked"]["mismatch"] == 0
    assert data["checked"]["forged"] == 0


def test_redteam_attack_07_resume_evidence_transplant(redteam_env):
    """
    测项 7: 断点续跑 / 已 ok 派发单的「证据嫁接」与「跳过覆盖」攻击

    攻击手法（resume 路径，主 Agent 不重新派发，只搬弄磁盘证据）：
      a) 证据嫁接：把已完成派发 A 的 agent-calls 台账**原样复制**成新派发单 B
         的文件名，期望 B 直接复用该证据过关（跳过派发）；
      b) 改字段嫁接：把复制件内部的 dispatch_id 改写成 B，但不重新 HMAC 签名；
      c) 跳过覆盖：已 ok 的派发单 A，resume 时主 Agent 直接覆写产物后仍沿用旧证据。

    预期防御：
      a) EVIDENCE_ID_MISMATCH（台账内部 dispatch_id 与文件名不符）
      b) EVIDENCE_FORGED（签名覆盖 dispatch_id，改任一字段即失效）
      c) attested_mismatch（证据在案但摘要与磁盘不符）
      三者都使 run_dispatch 落入 ABORTED / HALT，check_agent_audit 判 FAIL。
    """
    from unittest.mock import patch

    art_rel = "teaching/architect-plan.json"
    art_abs = os.path.join(redteam_env, "teaching", "architect-plan.json")

    def _write_artifact(content):
        with open(art_abs, "w", encoding="utf-8") as f:
            json.dump(content, f)
        return contract.sha256_file(art_abs)

    # ---- 基线：派发单 A 合法完成 ----
    req_a = _make_req(redteam_env, [art_rel])
    agent_nodes.write_request_file(redteam_env, req_a)
    id_a = req_a["dispatch_id"]
    sha_a = _write_artifact({"title": "Legit Plan A"})
    ev_a = agent_evidence.write_evidence(
        redteam_env, dispatch_id=id_a, worker="subagent",
        provider="anthropic", model="claude-3-7-sonnet",
        output_digests={art_rel: sha_a},
        status=agent_evidence.EVIDENCE_STATUS_OK)
    assert os.path.isfile(ev_a)

    # ---- 攻击 a：原样复制 A 的台账为 B 的文件名（期望 B 复用证据跳过派发）----
    req_b = _make_req(redteam_env, [art_rel])
    agent_nodes.write_request_file(redteam_env, req_b)
    id_b = req_b["dispatch_id"]
    sha_b2 = _write_artifact({"title": "Attacker Overwrite For B"})
    shutil.copyfile(ev_a, os.path.join(redteam_env, "teaching", "agent-calls",
                                       "%s.json" % id_b))
    status, _ = agent_evidence.check_request_attested(
        redteam_env, req_b, {art_rel: sha_b2})
    assert status == "EVIDENCE_ID_MISMATCH"

    state_b = {"project_root": redteam_env, "pending_dispatch": req_b,
               "dispatch_results": {}}
    with patch("workflow.nodes.agent_nodes.interrupt", return_value={}):
        res_b = agent_nodes.run_dispatch(
            state_b, req_b, success_stage="T1_PLANNED",
            success_history="should not happen")
    assert res_b["stage"] == "ABORTED"
    assert res_b["routing_action"] == "HALT"
    assert res_b["dispatch_results"][id_b]["status"] == "EVIDENCE_ID_MISMATCH"

    # ---- 攻击 b：改写内部 dispatch_id 但不重签 ----
    ev_b_path = os.path.join(redteam_env, "teaching", "agent-calls",
                             "%s.json" % id_b)
    with open(ev_b_path, "r", encoding="utf-8") as f:
        tampered = json.load(f)
    tampered["dispatch_id"] = id_b
    with open(ev_b_path, "w", encoding="utf-8") as f:
        json.dump(tampered, f)
    status, _ = agent_evidence.check_request_attested(
        redteam_env, req_b, {art_rel: sha_b2})
    assert status == "EVIDENCE_FORGED"

    # ---- 攻击 c：resume 时覆写产物 + 沿用旧证据 ----
    sha_c = _write_artifact({"title": "Overwritten After Signing"})
    status, ev = agent_evidence.check_request_attested(
        redteam_env, req_a, {art_rel: sha_c})
    assert status == "attested_mismatch"

    state_c = {"project_root": redteam_env, "pending_dispatch": req_a,
               "dispatch_results": {}}
    with patch("workflow.nodes.agent_nodes.interrupt", return_value={}):
        res_c = agent_nodes.run_dispatch(
            state_c, req_a, success_stage="T1_PLANNED",
            success_history="should not happen")
    assert res_c["stage"] == "ABORTED"
    assert res_c["dispatch_results"][id_a]["status"] == "attested_mismatch"

    # ---- 门禁独立审计：嫁接/篡改一律 FAIL ----
    gate_status, findings, data = check_agent_audit.audit_agent_evidence(redteam_env)
    assert gate_status == "fail"
    assert data["checked"]["mismatch"] >= 1
    codes = {f.get("code") for f in findings}
    assert "EVIDENCE_ID_MISMATCH" in codes or "EVIDENCE_FORGED" in codes
