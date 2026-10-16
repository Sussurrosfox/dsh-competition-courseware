"""human_nodes.py — Human-in-the-loop breakpoint nodes for LangGraph.
Implements Phase 0 Breakpoint using LangGraph native interrupt() mechanism.
"""

import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from langgraph.types import interrupt
from ..state import CoursewareState


def _phase0_token_path(project_root: str) -> str:
    """Phase 0 人工授权码文件路径（仅供真人查阅，Agent 严禁自行读取）。"""
    return os.path.join(project_root, ".langgraph", "phase0_token.txt")


def _ensure_phase0_token(project_root: str) -> str:
    """确保授权码存在：不存在则生成 8 位短码落盘；已存在则复用（避免重复
    进入断点时真人手中持有的授权码失效）。返回授权码字符串。"""
    path = _phase0_token_path(project_root)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            token = f.read().strip()
        if token:
            return token
    os.makedirs(os.path.dirname(path), exist_ok=True)
    token = secrets.token_hex(4)
    with open(path, "w", encoding="utf-8") as f:
        f.write(token)
    return token


def _consume_phase0_token(project_root: str) -> None:
    """确认成功后删除授权码（一次性失效）。"""
    try:
        os.remove(_phase0_token_path(project_root))
    except OSError:
        pass


# ---------------------------------------------------------------------------
# G4.2 “已消费令牌”凭据（phase0_grant.json）
#
# 设计要点：
#   * 令牌校验成功后写入 phase0_grant.json（grant_id / confirmed spec 摘要 /
#     时间戳），作为“曾经完成过一次真人令牌确认”的唯一凭据；
#   * 预置 confirmed=True 的快捷路径只有在 grant 文件存在、摘要与当前 spec
#     一致、且 grant_id 与 state（user_spec.phase0_grant_id）中记录的一致时
#     才放行；三者任一不满足都强制走 interrupt + 新令牌确认；
#   * research-back 环路重入（research_back_count>0 或 rb_phase 标记）时快捷
#     路径一律失效，确认后刷新 grant（新的 grant_id）。
# ---------------------------------------------------------------------------

def _phase0_grant_path(project_root: str) -> str:
    """已消费令牌凭据文件路径。"""
    return os.path.join(project_root, ".langgraph", "phase0_grant.json")


def _spec_digest(user_spec: Dict[str, Any]) -> str:
    """confirmed spec 的规范摘要（SHA-256），只覆盖语义字段，忽略来源/时间戳。"""
    canon = json.dumps(
        {
            "main_title": str(user_spec.get("main_title") or ""),
            "lesson_mode": str(user_spec.get("lesson_mode") or ""),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def _write_phase0_grant(project_root: str, user_spec: Dict[str, Any]) -> str:
    """写入已消费令牌凭据，返回新的 grant_id。"""
    grant = {
        "grant_id": secrets.token_hex(8),
        "spec_digest": _spec_digest(user_spec),
        "confirmed_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    path = _phase0_grant_path(project_root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(grant, f, ensure_ascii=False, indent=2)
    return grant["grant_id"]


def _load_phase0_grant(project_root: str) -> Optional[Dict[str, Any]]:
    """读取凭据；缺失/畸形一律返回 None（fail-closed）。"""
    path = _phase0_grant_path(project_root)
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            grant = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(grant, dict):
        return None
    if not isinstance(grant.get("grant_id"), str) or not grant["grant_id"]:
        return None
    if not isinstance(grant.get("spec_digest"), str) or not grant["spec_digest"]:
        return None
    return grant


def verify_phase0_grant(project_root: str, user_spec: Dict[str, Any]) -> bool:
    """校验预置 confirmed 的 user_spec 是否持有有效的已消费令牌凭据。

    放行条件（全部满足，fail-closed）：
      1. phase0_grant.json 存在且结构完整；
      2. grant 中的 spec 摘要与当前 user_spec 一致；
      3. grant_id 与 user_spec 中记录的 phase0_grant_id 一致。
    """
    grant = _load_phase0_grant(project_root)
    if grant is None:
        return False
    recorded = str(user_spec.get("phase0_grant_id") or "")
    if not recorded or not hmac.compare_digest(recorded, str(grant["grant_id"])):
        return False
    return hmac.compare_digest(_spec_digest(user_spec), str(grant["spec_digest"]))


def write_user_alignment_to_config(project_root: str, user_spec: Dict[str, Any]) -> bool:
    """Write/replace the user_alignment block in config.yaml from a confirmed user spec.

    gen_config.py refuses to run unless config.yaml contains
    user_alignment.confirmed: true with a non-null main_title. The block is
    written as plain YAML text (no PyYAML dependency), preserving the rest of
    the file untouched. Returns True when the file was updated.
    """
    cfg_path = os.path.join(project_root, "config.yaml")
    if not os.path.exists(cfg_path):
        return False

    title = str(user_spec.get("main_title") or "")
    lesson_mode = str(user_spec.get("lesson_mode") or "guided")
    confirmed_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")

    block = (
        "user_alignment:\n"
        "  confirmed: true\n"
        f"  main_title: {json.dumps(title, ensure_ascii=False)}\n"
        f"  lesson_mode: {lesson_mode}\n"
        f"  confirmed_at: {json.dumps(confirmed_at)}\n"
    )

    with open(cfg_path, "r", encoding="utf-8-sig") as f:
        text = f.read()

    # Replace an existing top-level user_alignment block (key line plus its indented body)
    pattern = re.compile(r"(?m)^user_alignment:[ \t]*\n(?:[ \t]+[^\n]*\n)*")
    if pattern.search(text):
        text = pattern.sub(block, text)
    else:
        if not text.endswith("\n"):
            text += "\n"
        text += "\n" + block

    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(text)
    return True


def node_phase0_breakpoint(state: CoursewareState) -> Dict[str, Any]:
    """Phase 0 Breakpoint: Strictly pause execution until user confirms title and lesson_mode.
    
    Uses LangGraph native interrupt() to halt graph execution safely.
    """
    project_root = state.get("project_root", ".")
    user_spec = state.get("user_spec") or {}

    # G4.2：research-back 环路重入时快捷路径一律失效（凭据随冻结撤销而作废），
    # 必须重新走 interrupt + 新令牌确认。
    research_backed = bool(
        (state.get("research_back_count") or 0) > 0 or state.get("rb_phase")
    )

    # 快捷路径放行条件收紧：预置 confirmed 之外，还必须持有有效的已消费令牌
    # 凭据（grant 文件存在 + 摘要一致 + grant_id 与 state 记录一致）。
    if (
        user_spec.get("confirmed") is True
        and user_spec.get("main_title")
        and user_spec.get("lesson_mode")
        and not research_backed
        and verify_phase0_grant(project_root, user_spec)
    ):
        user_spec.setdefault("source", "human_nodes")
        user_spec.setdefault("confirmed_at", datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"))
        user_spec_path = os.path.join(project_root, "teaching", "user-spec.json")
        os.makedirs(os.path.dirname(user_spec_path), exist_ok=True)
        with open(user_spec_path, "w", encoding="utf-8") as f:
            json.dump(user_spec, f, ensure_ascii=False, indent=2)

        # Sync user_alignment into config.yaml when it already exists
        write_user_alignment_to_config(project_root, user_spec)

        history = list(state.get("history", []))
        history.append(f"Phase 0: Resumed with confirmed spec: title={user_spec.get('main_title')}, mode={user_spec.get('lesson_mode')}")

        return {
            "stage": "T0_READY",
            "user_spec": user_spec,
            "human_interrupted": False,
            "routing_action": "PROCEED",
            "history": history
        }

    # Otherwise, trigger native LangGraph interrupt to wait for human input
    interrupt_payload = {
        "type": "USER_SPEC_CONFIRMATION",
        "description": "Phase 0 物理对齐断点：请确认学案主标题（教学模式固定为 guided 分步引导）",
        "fields": {
            "main_title": user_spec.get("main_title", "竞赛数学专题：组合数与同余式"),
            "lesson_mode": "guided",
            "available_modes": [
                "guided",       # 分步引导（唯一模式）
            ]
        }
    }

    # Mint / reuse the one-time human authorization code BEFORE suspending.
    # The token value is never put into the interrupt payload (which the agent
    # can see); it only lives in the token file for the human to retrieve.
    token_path = _phase0_token_path(project_root)
    _ensure_phase0_token(project_root)
    interrupt_payload["token_file"] = token_path
    interrupt_payload["auth_notice"] = (
        "人工授权码已写入 token_file。恢复执行必须在 resume 参数中携带匹配的 "
        "auth_token（真人用户查阅后提供）；Agent 严禁自行读取该文件或静默恢复。"
    )
    if user_spec.get("confirmed") is True:
        # G4.2：调用方预置了 confirmed，但凭据校验未通过，必须如实告知。
        if research_backed:
            _reason = "research-back 环路重入，既有授权凭据已作废"
        else:
            _reason = "缺少有效的已消费令牌凭据（phase0_grant.json 缺失/摘要不符/grant_id 不符）"
        interrupt_payload["grant_notice"] = (
            "[GRANT_REJECTED] 预置 confirmed 未被采信：%s；"
            "Phase 0 必须由真人持新授权码重新确认。" % _reason
        )

    # This call pauses graph execution. When resumed via graph.invoke(Command(resume=...)),
    # the returned value will be user_response. Resume values without a matching
    # auth_token are rejected and the node re-suspends (no silent advance).
    user_response = interrupt(interrupt_payload)
    while True:
        if not isinstance(user_response, dict):
            user_response = {}
        expected = _ensure_phase0_token(project_root)
        provided = str(user_response.get("auth_token") or "")
        if provided and hmac.compare_digest(provided, expected):
            break
        retry_payload = dict(interrupt_payload)
        retry_payload["auth_status"] = (
            "[AUTH_FAILED] 授权码缺失或不匹配，Phase 0 断点继续保持挂起。"
            "请真人用户查阅 token_file 中的授权码并随 resume 一并提供。"
        )
        user_response = interrupt(retry_payload)

    # Token matched: consume it (one-time) and process the confirmed response.
    _consume_phase0_token(project_root)

    # Process user response upon resume
    resolved_spec = {
        "confirmed": True,
        "source": "human_nodes",
        "main_title": user_response.get("main_title") or interrupt_payload["fields"]["main_title"],
        "lesson_mode": "guided",
        "confirmed_at": user_response.get("confirmed_at") or datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    }

    # G4.2：令牌校验成功后写入“已消费令牌”凭据（research-back 重入时即刷新），
    # grant_id 随 user_spec 一并记入 state，供后续快捷路径比对。
    grant_id = _write_phase0_grant(project_root, resolved_spec)
    resolved_spec["phase0_grant_id"] = grant_id

    # Persist to canonical path teaching/user-spec.json
    user_spec_path = os.path.join(project_root, "teaching", "user-spec.json")
    os.makedirs(os.path.dirname(user_spec_path), exist_ok=True)
    with open(user_spec_path, "w", encoding="utf-8") as f:
        json.dump(resolved_spec, f, ensure_ascii=False, indent=2)

    # Sync user_alignment into config.yaml when it already exists
    write_user_alignment_to_config(project_root, resolved_spec)

    history = list(state.get("history", []))
    history.append(f"Phase 0: User confirmed spec: {resolved_spec}")

    ret = {
        "stage": "T0_READY",
        "user_spec": resolved_spec,
        "human_interrupted": False,
        "routing_action": "PROCEED",
        "history": history
    }
    if "deliver_teacher" in user_response:
        ret["deliver_teacher"] = bool(user_response["deliver_teacher"])
    return ret
