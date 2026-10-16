# -*- coding: utf-8 -*-
"""skills-c-v2 两阶段（教研/教学）skill 体系的交接与版本契约（v2.4.0）。

规范来源：meet.md 第四、六、七节与批次 B1；字段与枚举以 schemas/ 为准，
人类可读协议见 references/handoff.md。

v2.4.0 变更（相对 2.3.0）：
- schema 唯一目录为 skill 根 schemas/（删除 scripts/schemas/ 重复副本）；
- Architect 默认结构为“每题一个 task-<qid>，小问属于 task 内部”，
  旧 warmup_task / subquestion_tasks 结构已删除，不保留 legacy schema；
- packet / student-task / task-result 增加 run 绑定（run_id、
  packet_digest、receipt_ref）与签名 receipt 验签；
- preflight 增加来源白名单、禁止角色/路径与输出白名单检查。

设计约束：
- 纯 Python 标准库 + jsonschema，无其他依赖；
- Windows 兼容；项目相对路径统一为正斜杠 POSIX 风格；
- 机器记录使用 UTF-8 JSON；摘要使用 SHA-256；
- 摘要元数据保存在 freeze/build/review 记录中，文件自己的 digest
  不纳入自身计算（避免循环）；不比较时间戳判断失效。
"""

from __future__ import annotations

import difflib
import hashlib
import hmac
import json
import os
import re
import sys

import jsonschema

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and not _stream.isatty():
        _stream.reconfigure(encoding="utf-8", errors="replace")

CONTRACT_VERSION = "2.5.0"
COMPATIBLE_CONTRACT_VERSIONS = {"2.4.0", "2.5.0"}

# 逐小问解答单点常量与反退化规则（F2/F3）
MIN_SOLUTION_CHARS = 40

FORBIDDEN_SOLUTION_RULES = [
    (r"(?<![一-鿿])同上(?![一-鿿])|同上(?:推导|结论|步骤|可得|可知|即得)", "同上类敷衍引用"),
    (r"见前问(?:结论|推导)?", "跨问引用未展开"),
    (r"同理(?:可得|可知|即得)", "同理类省略"),
    (r"代入即得", "缺少代入后的验证步骤"),
    (r"(?<![一-鿿])略[。．\s]|[。．\s]略$", "整段省略"),
]

# schema 唯一权威目录：skill 根 schemas/（本文件位于 scripts/ 下）。
_SCHEMA_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "schemas"))

CHECK_STATUSES = ("pass", "fail", "review", "error")

# 视为“引用字段”的键名：xxx_ref / xxx_refs / content_ref / path / log_ref 等。
_REF_KEY_RE = re.compile(r"(^|_)(ref|refs|path)$")


# ---------------------------------------------------------------------------
# 路径工具
# ---------------------------------------------------------------------------

def normalize_relpath(path: str) -> str:
    """把路径统一为正斜杠 POSIX 风格。"""
    return path.replace("\\", "/")


def is_rel_path(value) -> bool:
    """判断一个字符串是否像项目相对路径引用（含 / 或文件扩展名）。"""
    if not isinstance(value, str) or "\\" in value or not value:
        return False
    if value.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", value):
        return False
    return "/" in value or "." in os.path.basename(value)


def resolve_path(project_root: str, rel_path: str):
    """把项目相对路径解析为绝对路径；越出项目根时返回 None。"""
    root = os.path.abspath(project_root)
    abs_path = os.path.abspath(os.path.join(root, rel_path.replace("/", os.sep)))
    if os.path.commonpath([root, abs_path]) != root:
        return None
    return abs_path


# ---------------------------------------------------------------------------
# 安全标识符与 qid 路径（G5.1/G5.2 路径穿越加固）
# ---------------------------------------------------------------------------

# qid / run_id 统一安全 pattern：首字符必须字母数字，仅允许 [A-Za-z0-9_-]，
# 长度 1-64——从字符集上排除 "."、"/"、"\"、":" 与空白，杜绝路径穿越载荷。
SAFE_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
_SAFE_ID_RE = re.compile(SAFE_ID_PATTERN)


def validate_qid(qid) -> bool:
    """校验 qid 是否为安全标识符（True=安全）。

    非字符串、空串、含路径分隔符/点号/冒号或超长一律 False（fail-closed）。
    """
    return isinstance(qid, str) and bool(_SAFE_ID_RE.fullmatch(qid))


def require_valid_qid(qid) -> str:
    """校验 qid 是否为安全标识符；非法时抛 ValueError（fail-closed）。"""
    if not validate_qid(qid):
        raise ValueError(f"非法的 question_id: {qid!r}")
    return qid


def safe_qid_path(root, *parts):
    """拼接 root 与 parts 并解析为绝对路径；越出 root 时返回 None（fail-closed）。

    内部走 realpath 解析（含符号链接）+ commonpath 根包含检查；
    Windows 跨盘（ValueError）与其他 OS 异常一律捕获并返回 None。
    调用方必须把 None 视为拒绝，不得回退到裸 os.path.join。
    """
    try:
        root_abs = os.path.realpath(root)
        candidate = os.path.realpath(
            os.path.join(root_abs, *(str(p) for p in parts)))
        if os.path.commonpath([root_abs, candidate]) != root_abs:
            return None
    except (ValueError, OSError):
        return None
    return candidate


# ---------------------------------------------------------------------------
# Schema 校验
# ---------------------------------------------------------------------------

def load_schema(name: str) -> dict:
    """按名字加载 schemas/<name>.schema.json（name 可带或不带后缀）。"""
    base = name[:-len(".schema.json")] if name.endswith(".schema.json") else name
    if not re.fullmatch(r"[A-Za-z0-9_-]+", base):
        raise ValueError("非法 schema 名称: %r" % name)
    path = os.path.join(_SCHEMA_DIR, base + ".schema.json")
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def validate(doc, schema_name: str) -> list:
    """校验文档，返回结构化错误列表（无错误时为空列表），不向调用方抛裸异常。

    每个错误为 {"path": "$.a.b[0]", "validator": ..., "message": ...}。
    文档本身不是合法 JSON / schema 缺失等输入错误以单项错误返回。
    """
    try:
        schema = load_schema(schema_name)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [{"path": "$", "validator": "schema", "message": str(exc)}]
    validator = jsonschema.Draft202012Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path)):
        path = "$" + "".join(
            "[%d]" % p if isinstance(p, int) else ".%s" % p
            for p in err.absolute_path
        )
        errors.append({
            "path": path,
            "validator": err.validator,
            "message": err.message,
        })
    return errors


# ---------------------------------------------------------------------------
# 引用解析与稳定 ID
# ---------------------------------------------------------------------------

def _iter_ref_values(node, key=None):
    """递归产出引用字段的字符串值（键名匹配 _REF_KEY_RE 且值像路径）。"""
    if isinstance(node, dict):
        for k, v in node.items():
            yield from _iter_ref_values(v, key=k)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_ref_values(item, key=key)
    elif isinstance(node, str):
        if key and _REF_KEY_RE.search(key) and is_rel_path(node):
            yield node


def collect_refs(doc, ref_fields=None) -> list:
    """收集文档中的引用路径。

    ref_fields 为 None 时按键名规则（*_ref / *_refs / path）自动识别；
    否则只收集指定字段名。返回去重排序后的列表。
    """
    refs = set()
    if ref_fields is None:
        refs.update(_iter_ref_values(doc))
    else:
        wanted = set(ref_fields)

        def walk(node, key=None):
            if isinstance(node, dict):
                for k, v in node.items():
                    walk(v, key=k)
            elif isinstance(node, list):
                for item in node:
                    walk(item, key=key)
            elif isinstance(node, str) and key in wanted and is_rel_path(node):
                refs.add(node)

        walk(doc)
    return sorted(refs)


def resolve_refs(project_root: str, doc, ref_fields=None) -> list:
    """检查引用文件存在性，返回缺失引用列表（项目相对路径，正斜杠）。

    越出项目根的引用按缺失报告。结果按路径排序，每项为
    {"ref": ..., "reason": "missing" | "escapes_project"}。
    """
    problems = []
    for ref in collect_refs(doc, ref_fields):
        abs_path = resolve_path(project_root, ref)
        if abs_path is None:
            problems.append({"ref": normalize_relpath(ref), "reason": "escapes_project"})
        elif not os.path.isfile(abs_path):
            problems.append({"ref": normalize_relpath(ref), "reason": "missing"})
    problems.sort(key=lambda p: p["ref"])
    return problems


def check_stable_ids(items, id_field: str = "id") -> list:
    """检查列表中 ID 的唯一性，返回重复 ID 列表（去重排序）。

    适用于 candidates(problem_id) / blocks(block_id) / claims(claim_id) 等。
    """
    seen, dup = set(), set()
    for item in items:
        if not isinstance(item, dict):
            continue
        value = item.get(id_field)
        if value is None:
            continue
        if value in seen:
            dup.add(value)
        seen.add(value)
    return sorted(dup)


# ---------------------------------------------------------------------------
# 摘要
# ---------------------------------------------------------------------------

def sha256_file(path: str):
    """计算文件 SHA-256；文件不存在返回 None。"""
    if not os.path.isfile(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json(obj) -> str:
    """规范 JSON：sort_keys、ensure_ascii=False、紧凑分隔符、UTF-8。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))


def sha256_json(obj) -> str:
    """对任意可 JSON 序列化对象计算规范 JSON 的 SHA-256。"""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def _hash_inputs(project_root: str, refs):
    """计算引用文件摘要，返回 (inputs, missing)。

    inputs: {项目相对路径: sha256}；missing: 缺失或越界的引用列表。
    """
    inputs, missing = {}, []
    for ref in sorted(set(refs)):
        norm = normalize_relpath(ref)
        abs_path = resolve_path(project_root, norm)
        digest = sha256_file(abs_path) if abs_path else None
        if digest is None:
            missing.append(norm)
        else:
            inputs[norm] = digest
    return inputs, missing


def _digest_result(label: str, core, inputs, missing) -> dict:
    """组合摘要结果。digest 由 core 对象与输入文件摘要共同决定。"""
    payload = {"contract": "skills-c-v2", "label": label,
               "core": core, "inputs": inputs}
    return {
        "digest": sha256_json(payload),
        "inputs": inputs,
        "missing": missing,
    }


def compute_brief_digest(project_root: str, brief: dict, exclude_refs=None) -> dict:
    """brief 摘要：brief 本身 + 其引用的研究文件与必要输入。

    返回 {"digest", "inputs", "missing"}。freeze 记录保存 digest 与
    inputs（dependency_digests）；brief.json 自己的 digest 不纳入自身计算。

    exclude_refs：不参与 inputs 文件摘要的项目相对路径（仍保留在 core 的
    引用列表中，绑定关系有效）。约定：freeze_basis.research_review_refs 指向
    的研究复核记录自身必须排除——审查记录的 subject_digest 绑定本摘要，
    若其内容再参与本摘要计算则不存在不动点（见 references/handoff.md）。
    """
    refs = collect_refs(brief)
    excluded = {normalize_relpath(r) for r in (exclude_refs or [])}
    refs = [r for r in refs if r not in excluded]
    inputs, missing = _hash_inputs(project_root, refs)
    return _digest_result("brief_digest", brief, inputs, missing)


def compute_production_digest(project_root: str, materials: dict,
                              extra_refs=None) -> dict:
    """生产摘要：materials + 各块正文 + 额外引用（配置、模板、装配顺序、资源）。

    extra_refs 为项目相对路径列表（如 config.yaml、header.tex、main.tex
    及图片资源）；装配顺序由调用方以有序列表传入（顺序影响摘要）。
    契约 2.5.0 (F4)：自动包含 teaching/generated/*-solution-task.json 与
    teaching/content/task-*-solution.tex，确保任何小问解答变更都会导致旧审查记录失效。
    """
    refs = list(collect_refs(materials))
    if extra_refs:
        refs.extend([normalize_relpath(r) for r in extra_refs])

    # 契约 2.5.0 (F4)：生产摘要必须覆盖逐小问解答源文件与 TeX 块
    root = os.path.abspath(project_root)
    sol_refs = []
    gen_dir = os.path.join(root, "teaching", "generated")
    if os.path.isdir(gen_dir):
        for fn in sorted(os.listdir(gen_dir)):
            if fn.endswith("-solution-task.json"):
                sol_refs.append(normalize_relpath(os.path.join("teaching", "generated", fn)))
    content_dir = os.path.join(root, "teaching", "content")
    if os.path.isdir(content_dir):
        for fn in sorted(os.listdir(content_dir)):
            if fn.endswith("-solution.tex"):
                sol_refs.append(normalize_relpath(os.path.join("teaching", "content", fn)))

    for sr in sol_refs:
        if sr not in refs:
            refs.append(sr)

    inputs, missing = _hash_inputs(project_root, refs)
    # 装配顺序敏感：保留 refs 的出现顺序参与摘要。
    core = {"materials": materials, "assembly_order": refs}
    return _digest_result("production_digest", core, inputs, missing)


def compute_build_digest(project_root: str, production_digest: str,
                         artifact_refs) -> dict:
    """构建摘要：对应 PDF、日志等产物 + 构建所依据的 production_digest。"""
    refs = [normalize_relpath(r) for r in artifact_refs]
    inputs, missing = _hash_inputs(project_root, refs)
    core = {"production_digest": production_digest}
    return _digest_result("build_digest", core, inputs, missing)


# ---------------------------------------------------------------------------
# 受影响依赖集合
# ---------------------------------------------------------------------------

def affected_set(project_root: str, changed_files, dep_graph) -> list:
    """计算受影响依赖集合。

    dep_graph: {节点: [该节点直接依赖的节点]}，节点为项目相对路径
    （正斜杠）。changed_files 中任一文件改变时，所有（传递）依赖它的
    节点进入受影响集合。候选池中未被任何节点依赖的文件改变，不影响
    任何题目证明——依赖图决定失效范围，而不是“一改全失效”。

    返回排序后的受影响节点列表（含 changed_files 本身规范后的路径）。
    """
    del project_root  # 节点均为项目相对路径，无需解析；保留参数以统一调用形态。
    # 反向边：dependency -> dependents
    reverse = {}
    for node, deps in dep_graph.items():
        n = normalize_relpath(node)
        for d in deps:
            reverse.setdefault(normalize_relpath(d), set()).add(n)
    affected = set()
    stack = [normalize_relpath(f) for f in changed_files]
    while stack:
        cur = stack.pop()
        if cur in affected:
            continue
        affected.add(cur)
        stack.extend(reverse.get(cur, ()))
    return sorted(affected)



# ---------------------------------------------------------------------------
# 升级 §3.3：写入前 Schema 预检（preflight）API
# ---------------------------------------------------------------------------

# broker 签名密钥来源：环境变量 SKILLS_BROKER_KEY 优先；否则使用用户主目录
# 下的工作区外密钥文件 ~/.skills-broker/broker.key（首次使用自动生成）。
# 该密钥严禁进入工作区；生产环境应同时以 ACL/容器限制主 agent 读取。
_BROKER_KEY_ENV = "SKILLS_BROKER_KEY"
_BROKER_KEY_REL = os.path.join("~", ".skills-broker", "broker.key")


def broker_key() -> bytes:
    """读取 broker 签名密钥（env 优先，其次工作区外密钥文件）。"""
    env = os.environ.get(_BROKER_KEY_ENV)
    if env:
        return env.encode("utf-8")
    path = os.path.expanduser(_BROKER_KEY_REL)
    if os.path.isfile(path):
        with open(path, "rb") as f:
            data = f.read().strip()
        if data:
            return data
    os.makedirs(os.path.dirname(path), exist_ok=True)
    key = hashlib.sha256(os.urandom(64)).hexdigest().encode("ascii")
    with open(path, "wb") as f:
        f.write(key)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return key


def sign_payload(payload: dict) -> str:
    """对 receipt/summary 负载签名：HMAC-SHA256(key, canonical_json(body))。

    body 为剔除 "signature" 字段后的负载——对同一文档重复签名是幂等的，
    与 verify_payload 的口径严格一致。
    """
    body = {k: v for k, v in payload.items() if k != "signature"} \
        if isinstance(payload, dict) else payload
    return hmac.new(broker_key(), canonical_json(body).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def verify_payload(payload: dict, signature) -> bool:
    """验签；payload 中可含 "signature" 字段（自动剔除后验签）。"""
    if not isinstance(signature, str) or not signature:
        return False
    body = {k: v for k, v in payload.items() if k != "signature"} \
        if isinstance(payload, dict) else payload
    expected = sign_payload(body)
    return hmac.compare_digest(expected, signature)


def run_record_hmac(run: dict) -> str:
    """run.json 记录签名（G5.2）：HMAC-SHA256(broker_key, canonical_json(body))。

    body 为剔除 "run_hmac" 字段后的 run 记录——与 broker 写入/验签两侧严格
    同口径（同一 canonical_json：sort_keys、紧凑分隔符、ensure_ascii=False），
    对同一文档重复计算幂等。外部工具/测试在确需重写 run.json 字段时可用
    本函数重签（等价于持有 broker 密钥的受信身份）。
    """
    body = {k: v for k, v in run.items() if k != "run_hmac"} \
        if isinstance(run, dict) else run
    return hmac.new(broker_key(), canonical_json(body).encode("utf-8"),
                    hashlib.sha256).hexdigest()


def verify_receipt_file(project_root: str, receipt_ref: str,
                        run_id: str = "", task_id: str = "",
                        packet_digest: str = "") -> dict:
    """读取并验签 receipt 文件。

    返回 {"ok": bool, "receipt": dict|None, "reason": str}。
    run_id / task_id / packet_digest 非空时同时核对绑定字段。
    """
    abs_path = resolve_path(project_root, receipt_ref) if receipt_ref else None
    if abs_path is None or not os.path.isfile(abs_path):
        return {"ok": False, "receipt": None,
                "reason": "receipt 文件不存在: %r" % receipt_ref}
    try:
        with open(abs_path, "r", encoding="utf-8-sig") as f:
            receipt = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "receipt": None,
                "reason": "receipt 不是合法 JSON: %s" % exc}
    if not isinstance(receipt, dict):
        return {"ok": False, "receipt": None, "reason": "receipt 不是对象"}
    signature = receipt.get("signature")
    if not verify_payload(receipt, signature):
        return {"ok": False, "receipt": receipt,
                "reason": "receipt 验签失败（签名不匹配或缺失）"}
    if run_id and receipt.get("run_id") != run_id:
        return {"ok": False, "receipt": receipt,
                "reason": "receipt.run_id=%r 与当前 run %r 不一致"
                          % (receipt.get("run_id"), run_id)}
    if task_id and receipt.get("task_id") != task_id:
        return {"ok": False, "receipt": receipt,
                "reason": "receipt.task_id=%r 与当前 task %r 不一致"
                          % (receipt.get("task_id"), task_id)}
    if packet_digest and receipt.get("packet_digest") != packet_digest:
        return {"ok": False, "receipt": receipt,
                "reason": "receipt.packet_digest 与 packet 摘要不一致"}
    return {"ok": True, "receipt": receipt, "reason": ""}


def packet_body_digest(packet: dict) -> str:
    """重算 packet 摘要：剔除 packet_digest 与 size_bytes 后的规范 JSON 摘要。

    用于校验 packet 文件未被事后篡改（broker 签发时同口径计算）。
    """
    body = {k: v for k, v in packet.items()
            if k not in ("packet_digest", "size_bytes")}
    return sha256_json(body)


def code_digest(paths) -> dict:
    """门禁代码摘要：对脚本/schema 目录计算 {relpath: sha256} 与总摘要。

    用于把检查报告绑定到实际执行的门禁代码版本；主 agent 改动工作区中的
    脚本副本不会改变已记录摘要（由外部 verifier 固定代码时尤其重要）。
    """
    files = {}
    for base in paths:
        base = os.path.abspath(base)
        if os.path.isfile(base):
            digest = sha256_file(base)
            if digest:
                files[normalize_relpath(os.path.basename(base))] = digest
            continue
        for dp, _dns, fns in os.walk(base):
            if "__pycache__" in dp:
                continue
            for fn in sorted(fns):
                if fn.endswith((".pyc", ".pyo")):
                    continue
                abs_path = os.path.join(dp, fn)
                digest = sha256_file(abs_path)
                if digest:
                    rel = normalize_relpath(os.path.relpath(abs_path, base))
                    files[normalize_relpath(os.path.basename(base)) + "/" + rel] = digest
    return {"digest": sha256_json(files), "files": files}


def preflight(doc, schema_name: str, context: str = "") -> dict:
    """写入前 Schema 预检（§3.3）。

    在 Agent 返回结果写入共享文件前调用此函数校验；校验失败时返回
    {"ok": False, "errors": [...], "hint": "..."} 并附修复建议。
    校验通过时返回 {"ok": True, "errors": []}。
    不抛异常——由调用方决定是否阻断写入。

    参数：
    - doc: 待写入的 Python 对象（已从 JSON 解析或直接构造）；
    - schema_name: schema 名称（如 "task-result", "materials", "review"）；
    - context: 可选上下文描述（如 task_id），用于错误信息定位。

    示例用法：
        result = preflight(task_result_doc, "task-result", context=task_id)
        if not result["ok"]:
            # 不写入共享文件，记录错误
            raise ValueError(result["hint"])
    """
    errors = validate(doc, schema_name)
    if not errors:
        return {"ok": True, "errors": []}

    # 生成修复建议：提取枚举/格式错误
    hints = []
    for err in errors[:5]:  # 只取前 5 条避免信息爆炸
        path = err["path"]
        validator = err["validator"]
        message = err["message"]
        if validator == "enum":
            # 提取允许值
            allowed = err.get("message", "")
            hints.append("[%s] %s — 检查允许枚举: %s" % (path, message, allowed))
        elif validator == "required":
            missing = message.replace("'", "").split("is a required property")[0].strip()
            hints.append("[%s] 缺少必填字段: %s" % (path, missing))
        elif validator == "type":
            hints.append("[%s] 类型错误: %s" % (path, message))
        else:
            hints.append("[%s] [%s] %s" % (path, validator, message))

    ctx_prefix = ("(%s) " % context) if context else ""
    hint_text = (
        "%s%s schema 预检失败（%d 个错误）：\n%s\n"
        "提示：校验失败的 task-result 不得写入共享文件（见 §3.3）；"
        "请按上述错误修复后重新提交。"
        % (ctx_prefix, schema_name, len(errors), "\n".join("  - " + h for h in hints))
    )
    return {"ok": False, "errors": errors, "hint": hint_text}


def preflight_task_result(task_result: dict, task_id: str = "",
                         project_root: str = "") -> dict:
    """task-result.schema.json 专用预检，附加业务规则验证。

    除 schema 校验外，额外检查：
    - candidate_conclusion = "completed" 时 outputs 不得为空；
    - candidate_conclusion ∈ ("no_useful_result", "failed") 时 conclusion_reason 必填；
    - input_digests 非空（schema minProperties 保证，这里给可读提示）；
    - run_id / packet_digest / receipt_ref 绑定：提供 project_root 时验签
      receipt，并核对 run/task/packet 三方一致；
    - status/artifacts/claims_* 等统一返回协议字段存在性（§3.4）。
    """
    result = preflight(task_result, "task-result", context=task_id)
    if not result["ok"]:
        return result

    extra = []
    concl = task_result.get("candidate_conclusion", "")
    if concl == "completed" and not task_result.get("outputs"):
        extra.append("candidate_conclusion=completed 但 outputs 为空，"
                     "已完成的任务必须提交产物引用")
    if concl in ("no_useful_result", "failed") \
            and not task_result.get("conclusion_reason"):
        extra.append("candidate_conclusion=%s 时 conclusion_reason 必填，"
                     "说明无结果/失败的理由" % concl)
    if not task_result.get("input_digests"):
        extra.append("input_digests 为空：输入摘要必须非空，且由 broker 从实际"
                     "读取文件重算")
    if project_root:
        receipt_ref = task_result.get("receipt_ref", "")
        checked = verify_receipt_file(project_root, receipt_ref,
                                      run_id=task_result.get("run_id", ""),
                                      task_id=task_result.get("task_id", ""),
                                      packet_digest=task_result.get(
                                          "packet_digest", ""))
        if not checked["ok"]:
            extra.append("receipt 校验失败：%s" % checked["reason"])
    if extra:
        return {"ok": False, "errors": [],
                "hint": "task-result 业务规则预检失败：\n"
                        + "\n".join("  - " + e for e in extra)}
    return {"ok": True, "errors": []}


# student-task 输出字段白名单（与 packet.output_fields_white_list 对应）
STUDENT_TASK_FIELDS = (
    "schema_version", "run_id", "question_id", "section_id", "task_id",
    "title", "stem", "subquestions", "level", "source_refs", "evidence",
    "brief_id", "brief_revision", "packet_digest", "receipt_ref",
    "execution_metadata",
)

RE_SOLUTION_SPLIT = re.compile(
    r"(?:\n|\r\n)(?:"
    r"####\s+(?:详细严谨解答|详细解答|严谨解答|解答|解析|证明|答案)|"
    r"###\s+(?:详细严谨解答|详细解答|严谨解答|解答|解析|证明|答案)|"
    r"##\s+(?:详细严谨解答|详细解答|严谨解答|解答|解析|证明|答案)|"
    r"\\begin\{solution\}|"
    r"【(?:解答|解析|证明|答案)】|"
    r"\*\*(?:解答|解析|证明|答案)\*\*|"
    r"(?:证|证明)\s*[:：]"
    r")",
    re.IGNORECASE
)


def strip_numbering_and_source(text: str) -> str:
    """去除题干开头的原书中序号（如 例 4.141. / 【例 1】 / 【预1】）、小问标号（如 (1) / (2)）与出处标签（如 (伊朗TST2012) / (Fine定理,1947) / (Kvant 1355)）。"""
    if not text:
        return ""
    s = text.replace("\r\n", "\n").strip()
    s = re.sub(r"(?m)^[ \t]*>[ \t]?", "", s).strip()
    changed = True
    while changed:
        before = s
        # 1. 标题行（如 ### 【例 1】...）
        s = re.sub(r"^(?:#{1,6}\s+[^\n]*\n+)", "", s)
        # 2. 原书序号/例题/预备题前缀（如 例 4.141. / 【例 1】 / 【预1】 / 【预4】 / 例题 1 / 习题 2.1 等）
        s = re.sub(r"^\s*(?:【(?:例|预|习题|例题|预备题|预习|训练|思考|探索|拓展|引例)[^】]*】|例\s*题?\s*\d+(?:\.\d+)*[.:：、]?|习题\s*\d+(?:\.\d+)*[.:：、]?|预\s*题?\s*\d+(?:\.\d+)*[.:：、]?|问题\s*\d+(?:\.\d+)*[.:：、]?|第\s*[0-9一二三四五六七八九十]+\s*[题例][.:：、]?)\s*", "", s)
        # 3. **题目** / **例题** 标记
        s = re.sub(r"^\s*\*\*(?:题目|例题|预备题|例)\*\*\s*", "", s)
        # 4. 原书小问标号（如 (1) / (2) / （1） / （2） / 1. / 2. / ① / ②）
        s = re.sub(r"^\s*(?:\([1-9一二三四]\)|（[1-9一二三四]）|[1-9]\.|[①②③④⑤⑥⑦⑧⑨⑩])\s*", "", s)
        # 5. 紧随其后的出处标签（如 (伊朗TST2012)、(Fine定理,1947)、(Kvant 1355)、（第 14 届 IMO 1972 第 3 题）等）
        s = re.sub(r"^\s*(?:\(|（|\[|【)(?:[^\n)\]】]*(?:IMO|USAMO|TST|Putnam|CMO|CGMO|CWMO|IMC|Kvant|kvant|竞赛|联赛|冬令营|奥林匹克|集训队|高考|定理|\b\d{4}\b)[^\n)\]】]*)(?:\)|）|\]|】)\s*", "", s)
        s = s.strip()
        changed = (s != before)
    return s


def clean_problem_stem(text: str) -> str:
    """从源题文本中截取纯净题干（剔除后续的解答、解析、证明部分与开头的原书序号、题目出处标签）。"""
    if not text:
        return ""
    normalized = text.replace("\r\n", "\n")
    m = RE_SOLUTION_SPLIT.search(normalized)
    if m:
        normalized = normalized[:m.start()]
    return strip_numbering_and_source(normalized.strip())


def extract_problem_stem(project_root: str, qid_or_ref: str) -> str:
    """从项目工作区读取并提取题目的纯净题干。"""
    candidates = []
    if qid_or_ref:
        p1 = resolve_path(project_root, qid_or_ref)
        if p1 and os.path.isfile(p1):
            candidates.append(p1)
        p2 = os.path.join(project_root, qid_or_ref)
        if os.path.isfile(p2):
            candidates.append(p2)
        p3 = resolve_path(project_root, "sources/problems/%s.md" % qid_or_ref)
        if p3 and os.path.isfile(p3):
            candidates.append(p3)
        p4 = resolve_path(project_root, "%s.md" % qid_or_ref)
        if p4 and os.path.isfile(p4):
            candidates.append(p4)

    for cand in candidates:
        try:
            with open(cand, "r", encoding="utf-8-sig", errors="replace") as f:
                content = f.read()
            stem = clean_problem_stem(content)
            if stem:
                return stem
        except OSError:
            pass
    return ""


def preflight_student_task(task: dict, project_root: str = "",
                           packet: dict = None, run: dict = None,
                           task_id: str = "") -> dict:
    """student-task.schema.json 专用预检（§3.3 + §4/§5.2 沙箱规则）。

    除 schema 校验外：
    - 输出字段必须是 packet.output_fields_white_list 的子集；
    - source_refs 必须落在 packet.allowed_source_refs 白名单内，且文件
      实际摘要与 packet.input_digests 一致；
    - run_id / brief_id / brief_revision / packet_digest / receipt_ref 与
      当前 run 和已签发 packet/receipt 绑定（验签）；
    - task_id 规范值 task-<question_id>；
    - 题干（stem）必须与所提供题目的题干（packet.problem_stem）保持完全一致。
    """
    result = preflight(task, "student-task", context=task_id)
    if not result["ok"]:
        return result

    extra = []
    qid = task.get("question_id", "")
    if task_id and task.get("task_id") != task_id:
        extra.append("task_id=%r 与 broker 任务 %r 不一致"
                     % (task.get("task_id"), task_id))
    if qid and task.get("task_id") != "task-%s" % qid:
        extra.append("task_id=%r 应为 task-%s（每题唯一规范任务 ID）"
                     % (task.get("task_id"), qid))

    if packet is not None:
        raw_packet_stem = (packet.get("problem_stem") or "").strip()
        packet_stem = strip_numbering_and_source(raw_packet_stem)
        if packet_stem:
            raw_task_stem = (task.get("stem") or "").strip()
            task_stem = strip_numbering_and_source(raw_task_stem)
            if not task_stem:
                extra.append("缺少题干 stem 字段")
            elif task_stem.replace("\r\n", "\n") != packet_stem.replace("\r\n", "\n"):
                extra.append(
                    "题干 stem 与所提供题目的题干 (packet.problem_stem) 不一致。"
                    "最终交付产物中题干必须与所提供题目的题干保持完全一致，严禁擅自改写或变更！"
                )
        whitelist = set(packet.get("output_fields_white_list", []))
        if whitelist:
            unknown = sorted(set(task) - whitelist)
            if unknown:
                extra.append("输出字段越出 packet 白名单: %s" % "、".join(unknown))
        allowed = set(packet.get("allowed_source_refs", []))
        digests = packet.get("input_digests", {})
        for ref in task.get("source_refs", []):
            norm = normalize_relpath(ref)
            if allowed and norm not in allowed:
                extra.append("source_ref %r 不在 packet 输入白名单内" % ref)
            if project_root:
                abs_path = resolve_path(project_root, norm)
                now = sha256_file(abs_path) if abs_path else None
                if now is None:
                    extra.append("source_ref %r 文件不存在或越出项目根" % ref)
                elif norm in digests and digests[norm] != now:
                    extra.append("source_ref %r 的当前摘要与 packet 记录不一致"
                                 "（输入版本漂移，须重新签发 packet）" % ref)
        if task.get("packet_digest") and packet \
                and task["packet_digest"] != packet_body_digest(packet):
            extra.append("task.packet_digest 与实际签发 packet 摘要不一致")

    if run is not None:
        if run.get("run_id") and task.get("run_id") != run["run_id"]:
            extra.append("run_id=%r 与当前 run %r 不一致"
                         % (task.get("run_id"), run["run_id"]))
        if run.get("brief_id") and task.get("brief_id") != run["brief_id"]:
            extra.append("brief_id=%r 与冻结 brief %r 不一致"
                         % (task.get("brief_id"), run["brief_id"]))
        if run.get("brief_revision") is not None \
                and task.get("brief_revision") != run["brief_revision"]:
            extra.append("brief_revision=%r 与冻结 brief revision %r 不一致"
                         % (task.get("brief_revision"),
                            run["brief_revision"]))
        expected = run.get("tasks", {}).get(task_id or task.get("task_id", ""))
        if expected and expected.get("state") not in ("issued", "verified"):
            extra.append("任务 %s 当前状态为 %r，不能提交"
                         % (task_id, expected.get("state")))

    if project_root and task.get("receipt_ref"):
        checked = verify_receipt_file(project_root, task["receipt_ref"],
                                      run_id=task.get("run_id", ""),
                                      task_id=task.get("task_id", ""),
                                      packet_digest=task.get("packet_digest",
                                                             ""))
        if not checked["ok"]:
            extra.append("receipt 校验失败：%s" % checked["reason"])

    if extra:
        return {"ok": False, "errors": [],
                "hint": "student-task 沙箱/绑定预检失败：\n"
                        + "\n".join("  - " + e for e in extra)}
    return {"ok": True, "errors": []}


def text_similarity(s1: str, s2: str) -> float:
    """计算两段文本的字符级相似度 (0.0 ~ 1.0)。"""
    if not s1 and not s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    return difflib.SequenceMatcher(None, s1.strip(), s2.strip()).ratio()


def preflight_solution_task(sol_doc: dict, project_root: str = "",
                            packet: dict = None, run: dict = None,
                            task_id: str = "", student_task: dict = None) -> dict:
    """solution-task.schema.json 专用预检（契约 2.5.0，F2/F3/F11）。
    
    除 schema 校验外：
    - task_id 与 broker 任务一致，且形如 task-<qid>；
    - subquestion_solutions 与 student_task 或 packet 的 subquestions 严格 1:1 同构；
    - 逐小问 detailed_solution >= MIN_SOLUTION_CHARS，且不包含边界感知敷衍退化禁词；
    - 公式定界符成对配平；
    - run_id / brief_id / brief_revision / packet_digest 一致；
    - receipt 验签通过。
    """
    result = preflight(sol_doc, "solution-task", context=task_id)
    if not result["ok"]:
        return result

    extra = []
    qid = sol_doc.get("question_id", "")
    tid = sol_doc.get("task_id", "")
    if task_id and tid != task_id:
        extra.append("task_id=%r 与 broker 任务 %r 不一致" % (tid, task_id))
    if qid and tid != "task-%s" % qid:
        extra.append("task_id=%r 应为 task-%s" % (tid, qid))

    # 核对小问同构
    ref_subqs = []
    if student_task and student_task.get("subquestions"):
        ref_subqs = student_task["subquestions"]
    elif packet and packet.get("subquestions"):
        ref_subqs = packet["subquestions"]

    sol_subqs = sol_doc.get("subquestion_solutions", [])
    if ref_subqs:
        ref_ids = [sq.get("subquestion_id") for sq in ref_subqs]
        sol_ids = [sq.get("subquestion_id") for sq in sol_subqs]
        if ref_ids != sol_ids:
            extra.append("小问 ID 序列不同构！设问为 %s，解答为 %s" % (ref_ids, sol_ids))
        for r_sq, s_sq in zip(ref_subqs, sol_subqs):
            r_prompt = (r_sq.get("prompt") or "").strip()
            s_prompt = (s_sq.get("prompt") or "").strip()
            if r_prompt and s_prompt and r_prompt != s_prompt:
                extra.append("小问 %s prompt 回显不一致（设问: %r vs 回显: %r）"
                             % (s_sq.get("subquestion_id"), r_prompt, s_prompt))

    # 逐小问解答语义自足性与禁词
    texts = []
    for sq in sol_subqs:
        sid = sq.get("subquestion_id", "unknown")
        body = (sq.get("detailed_solution") or "").strip()
        texts.append(body)
        if len(body) < MIN_SOLUTION_CHARS:
            extra.append("小问 %s 解答正文过短（%d < %d 字）"
                         % (sid, len(body), MIN_SOLUTION_CHARS))
        for pat, why in FORBIDDEN_SOLUTION_RULES:
            if re.search(pat, body):
                extra.append("小问 %s 命中敷衍禁词（%s，规则: %s）"
                             % (sid, why, pat))
        if body.count("$") % 2 != 0:
            extra.append("小问 %s 行内公式定界符 $ 未配平" % sid)
        if body.count(r"\[") != body.count(r"\]"):
            extra.append("小问 %s 行间公式定界符 \\[ 与 \\] 未配平" % sid)
        # 常见排版环境配平检查（保证列表与高级数学公式不中断编译）
        for env_name in ("enumerate", "itemize", "align*", "align", "cases"):
            begin_cnt = len(re.findall(r"\\begin\{" + re.escape(env_name) + r"\}", body))
            end_cnt = len(re.findall(r"\\end\{" + re.escape(env_name) + r"\}", body))
            if begin_cnt != end_cnt:
                extra.append("小问 %s 的 \\begin{%s} (%d) 与 \\end{%s} (%d) 环境未配平"
                             % (sid, env_name, begin_cnt, env_name, end_cnt))

    # G3 反集中检测：相邻小问解答相似度 > 0.9 判定为整解复制
    if len(texts) > 1:
        for i in range(len(texts) - 1):
            sim = text_similarity(texts[i], texts[i + 1])
            if sim > 0.90:
                extra.append("小问 %s 与 %s 解答高度雷同（相似度 %.2f > 0.90，疑似整解重复粘贴）"
                             % (sol_subqs[i].get("subquestion_id"),
                                sol_subqs[i + 1].get("subquestion_id"), sim))

    if packet is not None:
        if packet.get("packet_digest") and sol_doc.get("packet_digest") != packet["packet_digest"]:
            extra.append("packet_digest 与签发的 solution packet 不一致")

    if run is not None:
        if run.get("run_id") and sol_doc.get("run_id") != run["run_id"]:
            extra.append("run_id=%r 与当前 run %r 不一致" % (sol_doc.get("run_id"), run["run_id"]))
        if run.get("brief_id") and sol_doc.get("brief_id") != run["brief_id"]:
            extra.append("brief_id=%r 与冻结 brief %r 不一致" % (sol_doc.get("brief_id"), run["brief_id"]))
        if run.get("brief_revision") is not None and sol_doc.get("brief_revision") != run["brief_revision"]:
            extra.append("brief_revision=%r 与冻结 brief revision %r 不一致"
                         % (sol_doc.get("brief_revision"), run["brief_revision"]))

    if project_root and sol_doc.get("receipt_ref"):
        checked = verify_receipt_file(project_root, sol_doc["receipt_ref"],
                                      run_id=sol_doc.get("run_id", ""),
                                      task_id=sol_doc.get("task_id", ""),
                                      packet_digest=sol_doc.get("packet_digest", ""))
        if not checked["ok"]:
            extra.append("receipt 校验失败：%s" % checked["reason"])

    if extra:
        return {"ok": False, "errors": [],
                "hint": "solution-task 业务规则预检失败：\n"
                        + "\n".join("  - " + e for e in extra)}
    return {"ok": True, "errors": []}


# ---------------------------------------------------------------------------
# 检查报告与写入范围
# ---------------------------------------------------------------------------

def write_check_report(path: str, status: str, scope: str, findings) -> dict:
    """写入脚本检查报告（UTF-8 JSON）。status ∈ pass/fail/review/error。

    返回写入的报告对象；status 非法抛 ValueError（调用方编程错误）。
    """
    if status not in CHECK_STATUSES:
        raise ValueError("status 必须是 %s 之一，得到 %r"
                         % ("/".join(CHECK_STATUSES), status))
    report = {
        "schema_version": CONTRACT_VERSION,
        "kind": "check_report",
        "status": status,
        "scope": scope,
        "findings": list(findings),
    }
    abs_path = os.path.abspath(path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    with open(abs_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    return report


def validate_write_scope(project_root: str, path: str, allowed_roots) -> dict:
    """项目产物写入范围验证。

    目标路径必须位于项目根内，且落在 allowed_roots（项目相对前缀，
    如 ["research/", "teaching/", "issues/", "release/"]）之一下。
    返回 {"ok": bool, "path": 规范化项目相对路径或 None, "reason": str}。
    """
    root = os.path.abspath(project_root)
    abs_path = os.path.abspath(os.path.join(root, str(path).replace("/", os.sep)))
    if os.path.commonpath([root, abs_path]) != root:
        return {"ok": False, "path": None,
                "reason": "路径越出项目根: %r" % path}
    rel = normalize_relpath(os.path.relpath(abs_path, root))
    for allowed in allowed_roots:
        prefix = normalize_relpath(str(allowed)).rstrip("/")
        if rel == prefix or rel.startswith(prefix + "/"):
            return {"ok": True, "path": rel, "reason": ""}
    return {"ok": False, "path": rel,
            "reason": "路径不在允许的写入范围 %s 内: %s"
                      % (list(allowed_roots), rel)}


# ---------------------------------------------------------------------------
# 项目配置开关读取
# ---------------------------------------------------------------------------

def read_project_flag(project_root: str, key: str, default: bool = False) -> bool:
    """读取项目根 config.yaml 的顶层布尔开关（缺失/异常时返回 default）。

    用途：供各检查脚本读取"默认关闭、显式开启"的可选功能开关（如
    enable_obstacle_study：R3 障碍/补充材料研究在交付链路无消费通道，
    check_research 据此对非空 R3 产物 fail-closed）。

    优先 PyYAML（yaml.safe_load）；PyYAML 缺失或解析失败时退回逐行扁平
    正则解析（口径与 workflow/nodes/agent_nodes.py 的 _read_project_flag
    一致，仅匹配无缩进的顶层键行）；任何异常均按 default 处理，绝不向
    检查链路抛裸异常。
    """
    cfg_path = os.path.join(project_root, "config.yaml")
    if not os.path.isfile(cfg_path):
        return default
    try:
        with open(cfg_path, encoding="utf-8-sig") as f:
            text = f.read()
    except OSError:
        return default
    try:
        import yaml
        data = yaml.safe_load(text)
        if isinstance(data, dict) and key in data:
            val = data.get(key)
            return default if val is None else bool(val)
    except Exception:
        pass
    try:
        pattern = re.compile(r"^%s\s*:\s*(\S+)" % re.escape(key))
        for line in text.splitlines():
            if line[:1] in (" ", "\t"):
                continue  # 只认顶层键（无缩进行）
            stripped = line.split("#", 1)[0].strip()
            m = pattern.match(stripped)
            if m:
                return m.group(1).lower() in ("true", "yes", "on", "1")
    except Exception:
        pass
    return default


# ---------------------------------------------------------------------------
# 小型 CLI
# ---------------------------------------------------------------------------

def _load_json_file(path: str):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 2:
        print("用法:\n"
              "  python contract.py validate <schema> <file>   校验 JSON 文档\n"
              "  python contract.py preflight <schema> <file>  写入前预检（§3.3）\n"
              "  python contract.py digest <file>              计算文件 SHA-256",
              file=sys.stderr)
        return 2
    cmd = argv[0]
    if cmd == "validate" and len(argv) == 3:
        schema_name, file_path = argv[1], argv[2]
        try:
            doc = _load_json_file(file_path)
        except (OSError, json.JSONDecodeError) as exc:
            print("ERROR 输入不是合法 JSON: %s" % exc)
            return 1
        errors = validate(doc, schema_name)
        if errors:
            print("FAIL %d 个错误:" % len(errors))
            for err in errors:
                print("  %s [%s] %s" % (err["path"], err["validator"],
                                        err["message"]))
            return 1
        print("PASS %s 符合 %s" % (file_path, schema_name))
        return 0
    if cmd == "digest" and len(argv) == 2:
        digest = sha256_file(argv[1])
        if digest is None:
            print("ERROR 文件不存在: %s" % argv[1])
            return 1
        print("%s  %s" % (digest, normalize_relpath(argv[1])))
        return 0
    if cmd == "preflight" and len(argv) == 3:
        schema_name, file_path = argv[1], argv[2]
        try:
            doc = _load_json_file(file_path)
        except (OSError, json.JSONDecodeError) as exc:
            print("ERROR 输入不是合法 JSON: %s" % exc)
            return 1
        result = preflight(doc, schema_name, context=file_path)
        if result["ok"]:
            print("PASS %s 符合 %s（preflight 通过，可写入共享文件）"
                  % (file_path, schema_name))
            return 0
        print(result["hint"])
        return 1
    print("ERROR 未知子命令或参数数量不对", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
