"""script_runner.py — Safe executor for underlying verification and build scripts.
Encapsulates script invocations, pathing, JSON report parsing, and GateReport synthesis.
"""

import json
import os
import shutil
import subprocess
import sys
import time
from typing import Dict, Any, List, Optional, Tuple
from ..state import GateReport, GateIssue

# 报告中被视为“通过”的状态词（contract.CHECK_STATUSES 口径：pass/fail/review/error）。
# review 是未完成状态，不能当作通过。
PASS_STATUSES = ("pass", "PASS")

# 报告目录下用于隔离上一次运行报告的存档子目录
STALE_DIRNAME = ".stale"


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


class ScriptRunner:
    def __init__(self, project_root: str, scripts_dir: Optional[str] = None):
        self.project_root = os.path.abspath(project_root)
        if scripts_dir:
            self.scripts_dir = os.path.abspath(scripts_dir)
        else:
            # Default to ../../scripts relative to this file
            current_dir = os.path.dirname(os.path.abspath(__file__))
            self.scripts_dir = os.path.abspath(os.path.join(current_dir, "..", "..", "scripts"))

    def _get_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        pythonpath = env.get("PYTHONPATH", "")
        # Add scripts_dir and its parent to PYTHONPATH
        schemas_dir = os.path.normpath(os.path.join(self.scripts_dir, "..", "schemas"))
        extra_paths = [self.scripts_dir, schemas_dir]
        if pythonpath:
            extra_paths.append(pythonpath)
        env["PYTHONPATH"] = os.pathsep.join(extra_paths)
        return env

    def run_script(
        self,
        script_name: str,
        args: List[str],
        timeout: int = 300,
        cwd: Optional[str] = None
    ) -> Tuple[int, str, str]:
        """Run a Python script in self.scripts_dir."""
        script_path = os.path.join(self.scripts_dir, script_name)
        if not os.path.exists(script_path):
            raise FileNotFoundError(f"Script not found: {script_path}")

        cmd = [sys.executable, script_path] + args
        work_dir = cwd or self.project_root

        try:
            proc = subprocess.run(
                cmd,
                cwd=work_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self._get_env(),
                timeout=timeout
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            return 124, "", f"Timeout expired after {timeout} seconds: {exc}"
        except Exception as exc:
            return 1, "", f"Execution failed: {exc}"

    # ------------------------------------------------------------------
    # 报告隔离：旧报告不得冒充本轮门禁证据
    # ------------------------------------------------------------------

    def _quarantine_report(self, full_report_path: str) -> Optional[str]:
        """把已存在的旧报告移到 <dir>/.stale/ 存档，返回存档路径。

        运行前隔离可保证：脚本在写报告前异常退出时，读取方不会拿到上一次
        运行的 PASS/issue 结论。
        """
        if not os.path.isfile(full_report_path):
            return None
        stale_dir = os.path.join(os.path.dirname(full_report_path), STALE_DIRNAME)
        try:
            os.makedirs(stale_dir, exist_ok=True)
            stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime())
            base = os.path.basename(full_report_path)
            archived = os.path.join(stale_dir, "%s.%s" % (base, stamp))
            n = 1
            while os.path.exists(archived):
                n += 1
                archived = os.path.join(stale_dir, "%s.%s.%d" % (base, stamp, n))
            shutil.move(full_report_path, archived)
            return archived
        except OSError:
            # 隔离失败（权限/占用）：退化为删除，绝不能留下可误读的旧报告
            try:
                os.remove(full_report_path)
            except OSError:
                pass
            return None

    def _read_report(self, full_report_path: str) -> Tuple[Dict[str, Any], Optional[str]]:
        """读取报告；返回 (report_dict, error_message)。"""
        try:
            with open(full_report_path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return {}, "报告不是 JSON 对象: %s" % full_report_path
            return data, None
        except Exception as e:
            return {}, "Failed to read report JSON: %s" % e

    def run_gate(
        self,
        gate_name: str,
        script_name: str,
        args: List[str],
        report_rel_path: Optional[str] = None,
        timeout: int = 180,
        run_id: Optional[str] = None
    ) -> GateReport:
        """Run a gate script and parse its JSON report.

        与“只看退出码”的旧行为不同，本方法：
        1. 运行前把上一次运行的报告隔离到 `.stale/`，避免旧结论被当成本轮证据；
        2. 运行后校验报告确由本次调用生成（mtime 不早于启动时刻）；
        3. 联合校验进程退出码与报告 status，二者不一致时判失败并留下诊断信息。
        """
        started_at = time.time()
        full_report_path = (
            os.path.join(self.project_root, report_rel_path) if report_rel_path else None
        )
        quarantined = None
        if full_report_path:
            quarantined = self._quarantine_report(full_report_path)

        code, stdout, stderr = self.run_script(script_name, args, timeout=timeout)

        raw_report: Dict[str, Any] = {}
        report_error: Optional[str] = None
        report_is_fresh = False
        report_generated_at: Optional[str] = None

        if full_report_path and os.path.isfile(full_report_path):
            mtime = os.path.getmtime(full_report_path)
            report_generated_at = _iso(mtime)
            # 允许 1s 时钟粒度误差
            report_is_fresh = mtime >= (started_at - 1.0)
            if report_is_fresh:
                raw_report, report_error = self._read_report(full_report_path)
            else:
                report_error = (
                    "报告 %s 的修改时间 %s 早于本次门禁启动时间 %s，"
                    "判定为上一次运行的遗留报告（已隔离到 %s）"
                    % (report_rel_path, report_generated_at, _iso(started_at),
                       quarantined or ".stale/")
                )
        elif full_report_path:
            report_error = (
                "本次调用未生成报告 %s（进程退出码 %s）；执行状态不得解读为内容无问题"
                % (report_rel_path, code)
            )

        report_status = raw_report.get("status")
        status_norm = str(report_status).strip().lower() if report_status is not None else None

        issues: List[GateIssue] = []
        diagnostics: List[str] = []
        if report_error:
            diagnostics.append(report_error)

        # 联合判定：退出码、报告是否本轮生成、报告 status 三者一致才可通过
        exit_ok = (code == 0)
        if not report_is_fresh:
            passed = False
            if full_report_path:
                diagnostics.append(
                    "门禁 %s 未在本轮生成可解读报告（退出码 %s）" % (gate_name, code)
                )
        elif status_norm is None:
            # 有报告但没有 status 字段：以退出码为准，但记录缺口
            passed = exit_ok
            if passed:
                diagnostics.append("报告缺少 status 字段，仅以退出码判定")
        else:
            status_ok = status_norm in PASS_STATUSES
            passed = exit_ok and status_ok
            if exit_ok != status_ok:
                diagnostics.append(
                    "退出码与报告状态不一致：exit_code=%s，report status=%r"
                    % (code, report_status)
                )

        if not passed:
            # Extract issues from raw_report if present
            if "issues" in raw_report and isinstance(raw_report["issues"], list):
                for it in raw_report["issues"]:
                    if isinstance(it, dict):
                        issues.append({
                            "category": it.get("category", "GATE_FAIL"),
                            "severity": it.get("severity", "HIGH_RISK"),
                            "message": it.get("message", str(it)),
                            "hash": it.get("hash"),
                            "file": it.get("file")
                        })
            elif "findings" in raw_report and isinstance(raw_report["findings"], list):
                for it in raw_report["findings"]:
                    if isinstance(it, dict):
                        issues.append({
                            "category": it.get("category", "GATE_FAIL"),
                            "severity": it.get("severity", "HIGH_RISK"),
                            "message": it.get("message", str(it)),
                            "hash": it.get("hash"),
                            "file": it.get("file")
                        })
            elif "errors" in raw_report and isinstance(raw_report["errors"], list):
                for err in raw_report["errors"]:
                    issues.append({
                        "category": "GATE_ERROR",
                        "severity": "HIGH_RISK",
                        "message": str(err)
                    })
            else:
                # Fallback to stderr or stdout
                msg = (stderr.strip() or stdout.strip() or f"{gate_name} failed with exit code {code}")
                # Truncate if very long
                if len(msg) > 500:
                    msg = msg[:500] + "..."
                issues.append({
                    "category": "EXECUTION_ERROR",
                    "severity": "HIGH_RISK",
                    "message": msg
                })
            for d in diagnostics:
                if not any(i["message"] == d for i in issues):
                    issues.append({
                        "category": "REPORT_TRACEABILITY",
                        "severity": "HIGH_RISK",
                        "message": d
                    })

        if passed:
            status_str = "PASS"
        elif status_norm in ("review", "REVIEW"):
            status_str = "REVIEW"
        elif not report_is_fresh and full_report_path:
            status_str = "ERROR"
        elif status_norm in ("error", "ERROR"):
            status_str = "ERROR"
        else:
            status_str = "REJECT"

        return {
            "gate_name": gate_name,
            "status": status_str,
            "passed": passed,
            "issues": issues,
            "raw_report": raw_report,
            "exit_code": code,
            "report_path": report_rel_path,
            "report_generated_at": report_generated_at,
            "report_is_fresh": report_is_fresh,
            "run_id": run_id
        }
