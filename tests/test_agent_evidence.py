"""test_agent_evidence.py — WP1 证据模块单测。"""

import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from uuid import uuid4

# 注入 scripts 和 workflow 路径
_plugin_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _plugin_root not in sys.path:
    sys.path.insert(0, _plugin_root)
_scripts_dir = os.path.join(_plugin_root, "scripts")
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

from workflow.nodes import agent_evidence
import contract


class TestAgentEvidence(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="cc-evidence-test-")
        self.orig_broker_key = os.environ.get("SKILLS_BROKER_KEY")
        os.environ["SKILLS_BROKER_KEY"] = f"test-key-{uuid4().hex}"

    def tearDown(self):
        if self.orig_broker_key is not None:
            os.environ["SKILLS_BROKER_KEY"] = self.orig_broker_key
        else:
            os.environ.pop("SKILLS_BROKER_KEY", None)
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _write_file(self, rel: str, content: str) -> str:
        abs_p = os.path.join(self.test_dir, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(abs_p), exist_ok=True)
        with open(abs_p, "w", encoding="utf-8") as f:
            f.write(content)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def test_write_and_load_evidence_ok(self):
        """1. 写证据→读证据→验签通过，status 判定 ok"""
        art_rel = "staging/tasks/t1/output.json"
        digest = self._write_file(art_rel, '{"ans": 42}')

        did = "dsp-test-001"
        ev_file = agent_evidence.write_evidence(
            self.test_dir,
            dispatch_id=did,
            worker=f"subagent:courseware-{did}",
            artifacts_written=[art_rel],
            output_digests={art_rel: digest},
            status="ok",
        )
        self.assertTrue(os.path.isfile(ev_file))

        loaded = agent_evidence.load_evidence(self.test_dir, did)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["dispatch_id"], did)
        self.assertIn("signature", loaded)

        req = {
            "dispatch_id": did,
            "artifacts": [art_rel],
            "before": {art_rel: "old-digest"},
        }
        res = agent_evidence.verify_requests_from_disk(self.test_dir, [req])
        self.assertEqual(len(res), 1)
        status, ev = res[0]
        self.assertEqual(status, "ok")
        self.assertIsNotNone(ev)

    def test_tampered_output_digests_forged(self):
        """2. 篡改 output_digests 任一值 → 验签失败 → EVIDENCE_FORGED"""
        art_rel = "staging/tasks/t1/output.json"
        digest = self._write_file(art_rel, '{"ans": 42}')

        did = "dsp-test-002"
        ev_file = agent_evidence.write_evidence(
            self.test_dir,
            dispatch_id=did,
            worker=f"subagent:courseware-{did}",
            artifacts_written=[art_rel],
            output_digests={art_rel: digest},
            status="ok",
        )

        # 直接在磁盘上篡改 output_digests
        with open(ev_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["output_digests"][art_rel] = "tampered-sha256"
        with open(ev_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        # load_evidence 返回 None
        self.assertIsNone(agent_evidence.load_evidence(self.test_dir, did))

        # load_evidence_with_status 返回 EVIDENCE_FORGED
        _, err = agent_evidence.load_evidence_with_status(self.test_dir, did)
        self.assertEqual(err, "EVIDENCE_FORGED")

        req = {
            "dispatch_id": did,
            "artifacts": [art_rel],
            "before": {art_rel: "old-digest"},
        }
        res = agent_evidence.verify_requests_from_disk(self.test_dir, [req])
        self.assertEqual(res[0][0], "EVIDENCE_FORGED")

    def test_missing_evidence_unattested(self):
        """3. 无证据文件 → ok_unattested（allow_unattested=False 时上层视为失败）"""
        art_rel = "staging/tasks/t1/output.json"
        self._write_file(art_rel, '{"ans": 100}')

        did = "dsp-test-003"
        req = {
            "dispatch_id": did,
            "artifacts": [art_rel],
            "before": {art_rel: "old-digest"},
        }
        res = agent_evidence.verify_requests_from_disk(self.test_dir, [req], allow_unattested=False)
        self.assertEqual(res[0][0], "ok_unattested")
        self.assertIsNone(res[0][1])

    def test_id_mismatch_unavailable(self):
        """4. 证据 dispatch_id 与请求不符 → 不可用"""
        art_rel = "staging/tasks/t1/output.json"
        digest = self._write_file(art_rel, '{"ans": 200}')

        did_file = "dsp-test-004-a"
        did_body = "dsp-test-004-b"
        ev_file = agent_evidence.write_evidence(
            self.test_dir,
            dispatch_id=did_body,
            worker=f"subagent:courseware-{did_body}",
            artifacts_written=[art_rel],
            output_digests={art_rel: digest},
            status="ok",
        )
        # 移动文件为 did_file.json
        target = os.path.join(agent_evidence.agent_calls_dir(self.test_dir), f"{did_file}.json")
        shutil.move(ev_file, target)

        self.assertIsNone(agent_evidence.load_evidence(self.test_dir, did_file))
        _, err = agent_evidence.load_evidence_with_status(self.test_dir, did_file)
        self.assertEqual(err, "EVIDENCE_ID_MISMATCH")

    def test_path_traversal_prevention(self):
        """5. evidence_path 对 dispatch_id="../x"、"a/b" 返回 ""（防穿越）"""
        self.assertEqual(agent_evidence.evidence_path(self.test_dir, "../x"), "")
        self.assertEqual(agent_evidence.evidence_path(self.test_dir, "a/b"), "")
        self.assertEqual(agent_evidence.evidence_path(self.test_dir, r"a\b"), "")
        self.assertEqual(agent_evidence.evidence_path(self.test_dir, "."), "")
        self.assertEqual(agent_evidence.evidence_path(self.test_dir, ".."), "")

    def test_dispatch_protocol_violation(self):
        """6. dispatch_protocol_violation({"dispatch_protocol": "wrong"}) 返回非 None；{} 返回 None"""
        self.assertIsNotNone(
            agent_evidence.dispatch_protocol_violation({"dispatch_protocol": "wrong"})
        )
        self.assertIsNone(
            agent_evidence.dispatch_protocol_violation({})
        )
        self.assertIsNone(
            agent_evidence.dispatch_protocol_violation(
                {"dispatch_protocol": "host-subagent-v1"}
            )
        )

    def test_verify_dispatch_token_unsorted_artifacts(self):
        """7. NB1：token 口径对齐——artifacts 非字典序时也必须验过（与 build_request/index.js 一致）"""
        import hmac as _hmac
        art_a = "staging/tasks/t1/zzz.json"
        art_b = "staging/tasks/t1/aaa.json"
        did = "dsp-token-unsorted"
        before = {art_a: "sha-a", art_b: None}
        payload = {
            "artifacts": [art_a, art_b],   # 故意非排序
            "before": before,
            "dispatch_id": did,
        }
        token = _hmac.new(
            contract.broker_key(),
            contract.canonical_json(payload).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        req = {"dispatch_id": did, "artifacts": [art_a, art_b],
               "before": before, "dispatch_token": token}
        self.assertTrue(agent_evidence.verify_dispatch_token(req))
        # 换序后旧 token 必须失效（防止签名与内容脱钩）
        req_swapped = dict(req, artifacts=[art_b, art_a])
        self.assertFalse(agent_evidence.verify_dispatch_token(req_swapped))
        # 篡改 dispatch_id 即失效
        self.assertFalse(agent_evidence.verify_dispatch_token(
            dict(req, dispatch_id="dsp-other")))
        # 缺 token / 非字符串
        self.assertFalse(agent_evidence.verify_dispatch_token(
            {"dispatch_id": did, "artifacts": [art_a], "before": before}))

    def test_load_dispatch_config_defaults_and_overrides(self):
        """8. NB4：config.yaml 派发配置解析（agent_dispatch 块 / 扁平键 / 兜底）"""
        root = self.test_dir
        # 无 config.yaml → 全默认
        cfg = agent_evidence.load_dispatch_config(root)
        self.assertEqual(cfg["allow_unattested"], False)
        self.assertEqual(cfg["max_concurrency"], 2)
        self.assertEqual(cfg["max_retries"], 2)
        self.assertEqual(cfg["model_map"], {})

        with open(os.path.join(root, "config.yaml"), "w", encoding="utf-8") as f:
            f.write(
                "allow_unattested: true\n"
                "agent_dispatch:\n"
                "  max_concurrency: 5\n"
                "  max_retries: 4\n"
                "  model_map:\n"
                "    T1: {provider: anthropic, model: claude-3-7-sonnet}\n"
                "    BROKEN: {provider: '', model: ''}\n"
                "    NO_MODEL: {provider: only-provider}\n"
            )
        cfg = agent_evidence.load_dispatch_config(root)
        self.assertEqual(cfg["allow_unattested"], True)
        self.assertEqual(cfg["max_concurrency"], 5)
        self.assertEqual(cfg["max_retries"], 4)
        self.assertEqual(cfg["model_map"],
                         {"T1": {"provider": "anthropic",
                                 "model": "claude-3-7-sonnet"}})
        # provider/model 不完整的条目一律丢弃（不派发到空路由）
        self.assertNotIn("BROKEN", cfg["model_map"])
        self.assertNotIn("NO_MODEL", cfg["model_map"])

        # 扁平键覆盖 + 非法值兜底
        with open(os.path.join(root, "config.yaml"), "w", encoding="utf-8") as f:
            f.write(
                "agent_max_concurrency: 0\n"      # <=0 → 默认 2
                "agent_max_retries: -3\n"         # <0 → 0
                "agent_model_map:\n"
                "  T2: {provider: openai, model: gpt-4o}\n"
            )
        cfg = agent_evidence.load_dispatch_config(root)
        self.assertEqual(cfg["max_concurrency"], 2)
        self.assertEqual(cfg["max_retries"], 0)
        self.assertEqual(cfg["model_map"]["T2"],
                         {"provider": "openai", "model": "gpt-4o"})
        # load_dispatch_policy 仍是 allow_unattested 薄封装
        self.assertEqual(agent_evidence.load_dispatch_policy(root),
                         {"allow_unattested": False})


if __name__ == "__main__":
    unittest.main()
