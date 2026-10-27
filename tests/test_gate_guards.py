# -*- coding: utf-8 -*-
"""
tests/test_gate_guards.py — 门禁输入守卫与 broker 干净副本回归用例

覆盖验收报告 NB3/NB5 修复：
  - check_research / check_teaching / check_agent_audit 对"非项目根"目录
    一律 status=error 且不写报告（拒绝在任意目录留下 teaching/checks 空壳）；
  - broker run_gates 的干净代码副本必须随带 workflow/，
    使 check_agent_audit / check_research 的台账对账在隔离副本内可 import。
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

import contract  # noqa: E402
from workflow.nodes import agent_evidence  # noqa: E402
from scripts import check_agent_audit  # noqa: E402
from scripts import check_research  # noqa: E402
from scripts import check_teaching  # noqa: E402

ARTIFACT = "teaching/architect-plan.json"


@pytest.fixture
def attested_project(monkeypatch):
    d = tempfile.mkdtemp(prefix="cc_guard_ok_")
    monkeypatch.setenv("SKILLS_BROKER_KEY", "gate-guard-secret-31")
    for sub in ("teaching/agent-calls", "teaching/agent-requests", "teaching/checks"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    with open(os.path.join(d, ARTIFACT), "w", encoding="utf-8") as f:
        json.dump({"title": "guard"}, f)
    agent_evidence.write_evidence(
        d, dispatch_id="dsp-guard-1", worker="subagent:courseware-guard-1",
        provider="anthropic", model="claude-3-7-sonnet",
        artifacts_written=[ARTIFACT],
        output_digests={ARTIFACT: contract.sha256_file(os.path.join(d, ARTIFACT))},
        status="ok")
    with open(os.path.join(d, "teaching/agent-requests/dsp-guard-1.json"), "w",
              encoding="utf-8") as f:
        json.dump({"dispatch_id": "dsp-guard-1", "stage": "T1", "scope": "architect",
                   "artifacts": [ARTIFACT], "before": {ARTIFACT: None}}, f)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_check_agent_audit_bare_dir_no_side_effect(tmp_path):
    rc = check_agent_audit.main([str(tmp_path)])
    assert rc == 2
    assert not (tmp_path / "teaching").exists()


def test_check_agent_audit_attested_project_passes(attested_project):
    rc = check_agent_audit.main([attested_project])
    assert rc == 0
    rep = json.load(open(os.path.join(attested_project, "teaching", "checks",
                                      "check_agent_audit.json"), encoding="utf-8"))
    assert rep["status"] == "pass"


def _run_main(mod, root):
    """check_research/check_teaching 的 main() 无 argv 参数，走 sys.argv。"""
    old = sys.argv
    sys.argv = [mod.__file__ if getattr(mod, "__file__", None) else "gate", str(root)]
    try:
        return mod.main()
    finally:
        sys.argv = old


def test_check_research_bare_dir_no_side_effect(tmp_path):
    rc = _run_main(check_research, str(tmp_path))
    assert rc == 2
    assert not (tmp_path / "research").exists()


def test_check_teaching_bare_dir_is_fatal_error(tmp_path):
    fails, reviews, infos, evidence, fatal = check_teaching.check(str(tmp_path))
    assert fatal and fails == []
    rc = _run_main(check_teaching, str(tmp_path))
    assert rc == 2
    assert not (tmp_path / "teaching" / "checks").exists()


def test_staging_clean_copy_imports_agent_evidence(attested_project, tmp_path):
    """broker 干净副本（scripts/+schemas/+workflow/）内可跑台账审计。"""
    code_dir = str(tmp_path / "code")
    for sub in ("scripts", "schemas", "workflow"):
        shutil.copytree(os.path.join(_PLUGIN_ROOT, sub), os.path.join(code_dir, sub),
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    r = subprocess.run([sys.executable,
                        os.path.join(code_dir, "scripts", "check_agent_audit.py"),
                        attested_project],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    assert r.returncode == 0, r.stderr[-400:]
    assert "Traceback" not in r.stderr


def test_broker_run_gates_copies_workflow_into_staging():
    """broker.py cmd_run_gates 必须把 workflow/ 一并复制进干净副本。"""
    src = open(os.path.join(_SCRIPTS_DIR, "broker.py"), encoding="utf-8").read()
    marker = 'for sub in ("scripts", "schemas", "workflow"):'
    assert marker in src, "broker run_gates 干净副本缺少 workflow/（台账对账将 ImportError）"
