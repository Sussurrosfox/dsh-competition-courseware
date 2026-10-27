"""agent_evidence.py — 多 Agent 证据链模块。

本模块把写入者身份变成可对账的磁盘证据：
    证据台账  <project_root>/teaching/agent-calls/<dispatch_id>.json
    写入方    courseware_dispatch（index.js，宿主 subagent 工具派发子 Agent）
              run_dispatch / run_batch_dispatch（进程内认知 worker 路径，worker="llm-worker:<注册名>"）
    校验方    verify_requests_from_disk / run_dispatch（磁盘重推导对账）
              scripts/check_agent_audit.py（交付门禁全量对账）
              scripts/check_teaching.py（教学门禁联合核验）

证据 schema（schema_version=1，字段全集）：
    schema_version     1
    dispatch_id        派发单 ID（须与 teaching/agent-requests/<dispatch_id>.json 一致）
    batch_id           批次 ID（可为 None 或字符串）
    worker             写入者身份："subagent:courseware-<dispatch_id>" 或 "llm-worker:<注册名>"
    provider / model   子 Agent 路由（可空串）
    started_at / finished_at / duration_ms
    artifacts_written  实际写入的产物相对路径列表
    output_digests     {rel: sha256hex}（写盘后立即计算）
    status             "ok"（其余值一律视为未完成派发）
    signature          HMAC-SHA256 签名（排除 signature 字段后的 canonical JSON）

check_request_attested 状态码：
    ok                 证据在案且验签通过、与磁盘变化产物逐项吻合
    ok_unattested      产物有变化但无证据文件（主线程直写嫌疑）
    attested_mismatch  证据在案但不吻合（摘要不符 / 状态非 ok / dispatch_id 不符 / worker 缺失）
    missing_artifacts  产物缺失
    no_change          产物全部未变化
    EVIDENCE_FORGED    证据存在但 HMAC-SHA256 验签失败
    EVIDENCE_ID_MISMATCH 证据 dispatch_id 与文件名或请求不符
"""

import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

try:
    import contract
except ImportError:
    _scripts_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
    if _scripts_dir not in sys.path:
        sys.path.insert(0, _scripts_dir)
    import contract

#: 证据台账目录（相对 project_root）
EVIDENCE_DIR_REL = os.path.join("teaching", "agent-calls")

#: 派发单目录（相对 project_root）
REQUEST_DIR_REL = os.path.join("teaching", "agent-requests")

#: 证据 schema 版本
EVIDENCE_SCHEMA_VERSION = 1

#: 证据中代表「派发成功完成」的 status 值
EVIDENCE_STATUS_OK = "ok"

# ---------------------------------------------------------------------------
# 中断载荷派发协议
# ---------------------------------------------------------------------------

#: 派发协议标识：载荷顶层必须携带，恢复侧据此识别「是否经 courseware_dispatch 派发」
DISPATCH_PROTOCOL = "host-subagent-v1"

#: 唯一合法派发工具名
REQUIRED_DISPATCH_TOOL = "courseware_dispatch"

#: 载荷顶层 forbidden 清单（提示主 Agent 的禁止事项）
FORBIDDEN_DIRECT_WRITES = [
    "主 Agent 本会话直接撰写 artifacts 声明的任何文件",
    "伪造/手写 teaching/agent-calls/ 台账",
    "读取 ~/.skills-broker/broker.key",
]

#: 恢复侧协议白名单：None 表示旧 checkpoint（不拦截，向后兼容）
ALLOWED_DISPATCH_PROTOCOLS = (None, DISPATCH_PROTOCOL)


def _iso_now() -> str:
    """ISO8601 UTC 时间戳（带时区）。"""
    return datetime.now(timezone.utc).isoformat()


def _artifact_list(request: Dict[str, Any]) -> List[str]:
    return [a for a in ((request or {}).get("artifacts") or []) if isinstance(a, str)]


def _sha256_file(path: str) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def _sha256_of(project_root: str, rel: str) -> Optional[str]:
    abs_path = os.path.join(project_root, rel.replace("/", os.sep))
    return _sha256_file(abs_path)


def agent_calls_dir(project_root: str) -> str:
    """证据台账目录（<project_root>/teaching/agent-calls）。"""
    return os.path.join(project_root or ".", EVIDENCE_DIR_REL)


def evidence_path(project_root: str, dispatch_id: str) -> str:
    """证据文件路径；dispatch_id 含路径分隔符或是 . / .. 时返回 ""（防穿越）。"""
    did = str(dispatch_id or "").strip()
    if not did:
        return ""
    if "/" in did or "\\" in did or did in (".", ".."):
        return ""
    return os.path.join(agent_calls_dir(project_root), did + ".json")


def load_evidence_with_status(
    project_root: str,
    dispatch_id: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """读取单条证据并返回 (data, error_code)。

    返回：
        (data, None)                  成功且签名有效
        (None, "EVIDENCE_MISSING")    文件不存在
        (None, "EVIDENCE_ID_MISMATCH") JSON 解析失败、非 dict 或 dispatch_id 不匹配
        (data, "EVIDENCE_FORGED")     签名验签失败
    """
    path = evidence_path(project_root, dispatch_id)
    if not path or not os.path.isfile(path):
        return None, "EVIDENCE_MISSING"
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None, "EVIDENCE_ID_MISMATCH"
    if not isinstance(data, dict):
        return None, "EVIDENCE_ID_MISMATCH"
    if str(data.get("dispatch_id") or "") != str(dispatch_id or "").strip():
        return None, "EVIDENCE_ID_MISMATCH"

    sig = data.get("signature")
    if not sig or not contract.verify_payload(data, sig):
        return data, "EVIDENCE_FORGED"

    return data, None


def load_evidence(project_root: str, dispatch_id: str) -> Optional[Dict[str, Any]]:
    """读取单条合法证据；任何无效/验签失败均返回 None。"""
    data, err = load_evidence_with_status(project_root, dispatch_id)
    if err is not None:
        return None
    return data


def evidence_matches(
    evidence: Any,
    request: Dict[str, Any],
    disk_digests: Dict[str, Optional[str]],
) -> bool:
    """证据与「磁盘现状 + 派发单」三方对账。

    条件：dispatch_id 一致 + status=="ok" + output_digests 在全部「相对 before
    发生变化的产物」上逐项等于磁盘 sha256。无变化产物时返回 False。
    """
    if not isinstance(evidence, dict) or not isinstance(request, dict):
        return False
    if str(evidence.get("dispatch_id") or "") != str(request.get("dispatch_id") or ""):
        return False
    if str(evidence.get("status") or "") != EVIDENCE_STATUS_OK:
        return False
    digests = evidence.get("output_digests")
    if not isinstance(digests, dict):
        return False
    before = dict(request.get("before") or {})
    changed = [
        rel for rel in _artifact_list(request)
        if disk_digests.get(rel) is not None and disk_digests.get(rel) != before.get(rel)
    ]
    if not changed:
        return False
    for rel in changed:
        if digests.get(rel) != disk_digests.get(rel):
            return False
    return True


def check_request_attested(
    project_root: str,
    request: Dict[str, Any],
    disk_digests: Dict[str, Optional[str]],
    allow_unattested: bool = False,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """对账单条派发请求的「产物-证据」一致性，返回 (status, evidence|None)。"""
    request = request or {}
    artifacts = _artifact_list(request)
    if not artifacts:
        return "missing_artifacts", None
    missing = [rel for rel in artifacts if not disk_digests.get(rel)]
    if missing:
        return "missing_artifacts", None
    before = dict(request.get("before") or {})
    changed = [
        rel for rel in artifacts
        if disk_digests.get(rel) is not None and disk_digests.get(rel) != before.get(rel)
    ]
    if not changed:
        return "no_change", None

    evidence, ev_err = load_evidence_with_status(project_root, request.get("dispatch_id"))
    if ev_err == "EVIDENCE_MISSING":
        return "ok_unattested", None
    if ev_err == "EVIDENCE_FORGED":
        return "EVIDENCE_FORGED", evidence
    if ev_err == "EVIDENCE_ID_MISMATCH":
        return "EVIDENCE_ID_MISMATCH", evidence
    if evidence is None:
        return "ok_unattested", None

    if evidence_matches(evidence, request, disk_digests):
        if not str(evidence.get("worker") or "").strip():
            return ("ok_unattested" if allow_unattested else "attested_mismatch"), evidence
        return "ok", evidence

    return "attested_mismatch", evidence


def write_evidence(
    project_root: str,
    *,
    dispatch_id: str,
    batch_id: Any = None,
    worker: str,
    provider: str = "",
    model: str = "",
    started_at: Optional[str] = None,
    finished_at: Optional[str] = None,
    duration_ms: Optional[int] = None,
    artifacts_written: Optional[List[str]] = None,
    output_digests: Optional[Dict[str, str]] = None,
    status: str = EVIDENCE_STATUS_OK,
) -> str:
    """原子写入带 HMAC 签名的证据台账（tmp + os.replace）。返回证据文件绝对路径，失败返回 ""。"""
    path = evidence_path(project_root, dispatch_id)
    if not path:
        return ""
    payload = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "dispatch_id": str(dispatch_id),
        "batch_id": batch_id,
        "worker": str(worker or ""),
        "provider": str(provider or ""),
        "model": str(model or ""),
        "started_at": started_at or _iso_now(),
        "finished_at": finished_at or _iso_now(),
        "duration_ms": int(duration_ms or 0),
        "artifacts_written": [str(a) for a in (artifacts_written or [])],
        "output_digests": {
            str(k): str(v) for k, v in (output_digests or {}).items() if v
        },
        "status": str(status or EVIDENCE_STATUS_OK),
    }
    payload["signature"] = contract.sign_payload(payload)

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "%s.tmp-%s" % (path, uuid4().hex[:8])
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except OSError:
        return ""
    return path


def scan_evidence(project_root: str) -> List[Dict[str, Any]]:
    """全量扫描证据台账（跳过坏 JSON / 验签失败 / dispatch_id 与文件名不符的条目）。"""
    directory = agent_calls_dir(project_root)
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(directory):
        return out
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        dispatch_id = name[:-5]
        ev = load_evidence(project_root, dispatch_id)
        if ev:
            out.append(ev)
    return out


def verify_requests_from_disk(
    project_root: str,
    requests: Any,
    allow_unattested: bool = False,
) -> List[Tuple[str, Optional[Dict[str, Any]]]]:
    """逐请求从磁盘重推导校验结果，返回 [(status, evidence|None), ...]。

    判定顺序：
        no_change → 无证据 → ok_unattested → 签名伪造 → EVIDENCE_FORGED
        → 摘要不符 → attested_mismatch → worker 为空 → attested_mismatch → ok
    """
    req_list = [requests] if isinstance(requests, dict) else list(requests or [])
    results: List[Tuple[str, Optional[Dict[str, Any]]]] = []
    for req in req_list:
        artifacts = _artifact_list(req)
        disk_digests: Dict[str, Optional[str]] = {}
        for rel in artifacts:
            disk_digests[rel] = _sha256_of(project_root, rel)
        status, ev = check_request_attested(
            project_root, req, disk_digests, allow_unattested=allow_unattested
        )
        results.append((status, ev))
    return results


def load_dispatch_config(project_root: str) -> Dict[str, Any]:
    """派发配置（与 index.js courseware_dispatch 同源）。

    读取 <project_root>/config.yaml：
      allow_unattested   无证据台账逃生阀（默认 False）
      agent_dispatch     可选块：{max_concurrency, max_retries, model_map: {阶段: {provider, model}}}
      另支持顶层扁平键 agent_max_concurrency / agent_max_retries / agent_model_map。
    """
    raw: Dict[str, Any] = {}
    try:
        cfg_path = os.path.join(project_root, "config.yaml")
        if os.path.isfile(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as f:
                import yaml
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                raw = data
    except Exception:
        raw = {}

    dispatch_map = raw.get("agent_dispatch")
    if not isinstance(dispatch_map, dict):
        dispatch_map = {}

    def _coerce_int(value, default):
        try:
            if value is None or isinstance(value, bool):
                return default
            return int(value)
        except Exception:
            return default

    model_map_raw = dispatch_map.get("model_map")
    if not isinstance(model_map_raw, dict):
        model_map_raw = raw.get("agent_model_map") if isinstance(raw.get("agent_model_map"), dict) else {}
    model_map: Dict[str, Any] = {}
    for stage, route in model_map_raw.items():
        if isinstance(route, dict) and route.get("provider") and route.get("model"):
            model_map[str(stage)] = {
                "provider": str(route["provider"]),
                "model": str(route["model"]),
            }

    max_concurrency = _coerce_int(
        raw.get("agent_max_concurrency") if raw.get("agent_max_concurrency") is not None
        else dispatch_map.get("max_concurrency"), 0)
    max_retries = _coerce_int(
        raw.get("agent_max_retries") if raw.get("agent_max_retries") is not None
        else dispatch_map.get("max_retries"), 2)

    return {
        "allow_unattested": bool(raw.get("allow_unattested", False)),
        "max_concurrency": max(1, max_concurrency) if max_concurrency > 0 else 2,
        "max_retries": max(0, max_retries),
        "model_map": model_map,
    }


def load_dispatch_policy(project_root: str) -> Dict[str, Any]:
    """派发策略（allow_unattested）——load_dispatch_config 的兼容薄封装。"""
    cfg = load_dispatch_config(project_root)
    return {"allow_unattested": cfg.get("allow_unattested", False)}


def dispatch_protocol_block() -> Dict[str, Any]:
    """中断载荷顶层的派发协议三件套（收到 AGENT_DISPATCH 必须调 courseware_dispatch）。"""
    return {
        "dispatch_protocol": DISPATCH_PROTOCOL,
        "required_tool": REQUIRED_DISPATCH_TOOL,
        "forbidden": list(FORBIDDEN_DIRECT_WRITES),
    }


def dispatch_protocol_violation(response: Any) -> Optional[str]:
    """恢复侧防伪造校验。

    中断响应声明的 dispatch_protocol 必须为 None（旧 checkpoint，不拦截）或
    host-subagent-v1；否则返回错误详情（调用方丢弃 files 并 HALT）。
    """
    if not isinstance(response, dict):
        return None
    protocol = response.get("dispatch_protocol")
    if protocol in ALLOWED_DISPATCH_PROTOCOLS:
        return None
    return (
        "派发协议校验失败 [BAD_DISPATCH_PROTOCOL]: 中断响应声明 %r，"
        "应为 %r（须经 %s 派发宿主子 Agent）"
        % (protocol, DISPATCH_PROTOCOL, REQUIRED_DISPATCH_TOOL)
    )


def verify_dispatch_token(request: Dict[str, Any]) -> bool:
    """校验派发单所附带的 HMAC dispatch_token。

    口径必须与 build_request()（agent_nodes.py）及 index.js verifyDispatchToken()
    逐字节一致：artifacts 保持派发单原始顺序（不排序），before 原样取用。
    """
    token = request.get("dispatch_token")
    if not token or not isinstance(token, str):
        return False
    try:
        import contract
        artifacts = [str(a) for a in (request.get("artifacts") or [])]
        payload = {
            "artifacts": artifacts,
            "before": request.get("before") or {},
            "dispatch_id": str(request.get("dispatch_id") or ""),
        }
        expected = hmac.new(
            contract.broker_key(),
            contract.canonical_json(payload).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(token.lower(), expected.lower())
    except Exception:
        return False

