"""sqlite_saver.py — Persistent (cross-process) LangGraph checkpointer.

`MemorySaver` 只活在单个进程里：CLI 先跑 `--run`、再用新 Python 进程跑
`--resume` 时，新进程的 MemorySaver 是空的，既找不到 interrupt 也找不到
checkpoint，跨进程恢复必然失败。本模块用标准库 sqlite3 实现同一接口，把
checkpoint / metadata / pending writes / channel blobs 落盘，使 CLI 与 UI 在
进程退出甚至重启后仍能按 `thread_id` 原地恢复。

不引入 `langgraph-checkpoint-sqlite` 依赖：接口面很小，自己实现可以避免新增
第三方依赖；序列化直接复用 langgraph 的 serde（`dumps_typed`/`loads_typed`），
口径与内存版一致。

G4.3 完整性保护：三表（checkpoints / writes / blobs）每条记录附 HMAC-SHA256
签名（密钥文件 ``<db 同目录>/.checkpoint_key``，``secrets.token_hex(32)``，
首次使用时生成；Windows 下尽力设置隐藏属性。密钥文件只依赖操作系统同身份
账户的读保护，与数据库文件同级存放，请确保目录 ACL 不开放给其他账户）。
读取路径全部验签，验签失败或无签名的旧记录一律抛
:class:`CheckpointIntegrityError`（fail-closed，提示重建），不静默放行。
"""

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from langgraph.checkpoint.base import (
    BaseCheckpointSaver,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    ChannelVersions,
    RunnableConfig,
    WRITES_IDX_MAP,
    get_checkpoint_id,
    get_checkpoint_metadata,
)

# 空通道值的哨兵类型（对应 MemorySaver 的 ("empty", b"")）
EMPTY_TYPE = "empty"

KEY_FILENAME = ".checkpoint_key"

# 各表参与 HMAC 计算的列（顺序固定：表名‖主键‖value 全字段）
_HMAC_COLUMNS = {
    "checkpoints": [
        "thread_id", "checkpoint_ns", "checkpoint_id", "parent_checkpoint_id",
        "checkpoint_type", "checkpoint", "metadata_type", "metadata",
    ],
    "writes": [
        "thread_id", "checkpoint_ns", "checkpoint_id", "task_id", "idx",
        "channel", "value_type", "value", "task_path",
    ],
    "blobs": [
        "thread_id", "checkpoint_ns", "channel", "version",
        "value_type", "value",
    ],
}


class CheckpointIntegrityError(RuntimeError):
    """checkpoint 记录验签失败或缺失签名（fail-closed，提示重建）。"""


def _row_hmac(key: bytes, table: str, parts: Sequence[Any]) -> str:
    """HMAC-SHA256(key, 表名‖主键‖value)，None/bytes/int 统一编码。"""
    mac = hmac.new(key, digestmod=hashlib.sha256)
    mac.update(table.encode("utf-8"))
    for part in parts:
        mac.update(b"\x00")
        if part is None:
            mac.update(b"<null>")
        elif isinstance(part, (bytes, bytearray, memoryview)):
            mac.update(bytes(part))
        else:
            mac.update(str(part).encode("utf-8"))
    return mac.hexdigest()


def _hide_file_windows(path: str) -> None:
    """Windows 下尽力给密钥文件加隐藏属性（失败不阻断，属尽力而为）。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x2)  # FILE_ATTRIBUTE_HIDDEN
    except Exception:
        pass


def _load_or_create_key(db_path: str) -> bytes:
    """读取或首次生成 HMAC 密钥（<db 同目录>/.checkpoint_key）。

    fail-closed：密钥文件存在但畸形时直接抛完整性错误，绝不悄悄换新密钥
    （那会让既有签名记录全部被静默判死，掩盖篡改痕迹）。
    """
    path = os.path.join(os.path.dirname(os.path.abspath(db_path)), KEY_FILENAME)
    try:
        with open(path, "r", encoding="ascii") as f:
            hexkey = f.read().strip()
    except FileNotFoundError:
        hexkey = None
    except OSError as e:
        raise CheckpointIntegrityError(
            f"无法读取 checkpoint 密钥文件 {path}: {e}"
        ) from e

    if hexkey is not None:
        try:
            key = bytes.fromhex(hexkey)
        except ValueError as e:
            raise CheckpointIntegrityError(
                f"checkpoint 密钥文件 {path} 内容畸形，拒绝继续（请人工核查后重建）"
            ) from e
        if len(key) != 32:
            raise CheckpointIntegrityError(
                f"checkpoint 密钥文件 {path} 长度异常，拒绝继续（请人工核查后重建）"
            )
        return key

    hexkey = secrets.token_hex(32)
    try:
        # 独占创建，避免并发首启时互相覆盖密钥
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(hexkey)
        _hide_file_windows(path)
    except FileExistsError:
        with open(path, "r", encoding="ascii") as f:
            hexkey = f.read().strip()
    return bytes.fromhex(hexkey)


SCHEMA = """
CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    checkpoint_type TEXT NOT NULL,
    checkpoint BLOB NOT NULL,
    metadata_type TEXT NOT NULL,
    metadata BLOB NOT NULL,
    hmac TEXT,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);
CREATE TABLE IF NOT EXISTS writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL,
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    value_type TEXT NOT NULL,
    value BLOB NOT NULL,
    task_path TEXT NOT NULL DEFAULT '',
    hmac TEXT,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);
CREATE TABLE IF NOT EXISTS blobs (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL,
    channel TEXT NOT NULL,
    version TEXT NOT NULL,
    value_type TEXT NOT NULL,
    value BLOB NOT NULL,
    hmac TEXT,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);
"""


class SqliteSaver(BaseCheckpointSaver[str]):
    """SQLite-backed checkpointer.

    用法::

        saver = SqliteSaver("courseware_checkpoints.sqlite")
        graph = builder.compile(checkpointer=saver)

    同一路径可被多个进程安全打开（WAL 模式 + 每次操作独立事务）。
    """

    def __init__(self, path: str, *, serde=None):
        super().__init__(serde=serde)
        self.path = os.path.abspath(path)
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.RLock()
        self._local = threading.local()
        self._key = _load_or_create_key(self.path)
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(SCHEMA)
                # ALTER TABLE 兼容迁移：旧库缺 hmac 列时自动补列；
                # 存量无签名记录保持 NULL，读取时一律视为不可信并拒绝。
                for table in _HMAC_COLUMNS:
                    cols = [r[1] for r in conn.execute(
                        "PRAGMA table_info(%s)" % table).fetchall()]
                    if "hmac" not in cols:
                        conn.execute(
                            "ALTER TABLE %s ADD COLUMN hmac TEXT" % table)
                conn.commit()
            finally:
                # 初始化用的连接必须真正关闭并清掉线程缓存，
                # 否则后续 _connect() 会拿到一个已关闭的连接
                conn.close()
                self._local.conn = None

    # -- connection handling -------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    def __enter__(self) -> "SqliteSaver":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    # -- helpers -------------------------------------------------------------

    def _sign_row(self, table: str, values: Dict[str, Any]) -> str:
        """按 ``表名‖主键‖value`` 口径为一条记录计算 HMAC-SHA256 签名。"""
        return _row_hmac(self._key, table,
                         [values.get(c) for c in _HMAC_COLUMNS[table]])

    def _verify_row(self, table: str, values: Dict[str, Any],
                    stored_hmac: Any) -> None:
        """读取路径验签（fail-closed）。

        旧库迁移后存量的无签名记录（hmac 为 NULL）一律视为不可信，直接抛
        :class:`CheckpointIntegrityError` 并提示重建，绝不静默放行。
        """
        pk_desc = "/".join(str(values.get(c)) for c in _HMAC_COLUMNS[table][:3])
        if stored_hmac is None or not str(stored_hmac):
            raise CheckpointIntegrityError(
                f"checkpoint 表 {table} 记录 {pk_desc} 无完整性签名（旧格式/被剥离），"
                "该记录不可信；请人工核查后删除 checkpoint 数据库并重建。"
            )
        expected = self._sign_row(table, values)
        if not hmac.compare_digest(str(stored_hmac), expected):
            raise CheckpointIntegrityError(
                f"checkpoint 表 {table} 记录 {pk_desc} 完整性验签失败，"
                "数据可能已被篡改；请人工核查后删除 checkpoint 数据库并重建。"
            )

    @staticmethod
    def _ns(config: RunnableConfig) -> str:
        return config.get("configurable", {}).get("checkpoint_ns", "") or ""

    @staticmethod
    def _thread(config: RunnableConfig) -> str:
        return config["configurable"]["thread_id"]

    def _load_blobs(
        self, thread_id: str, checkpoint_ns: str, versions: ChannelVersions
    ) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        conn = self._connect()
        for k, ver in versions.items():
            row = conn.execute(
                "SELECT value_type, value, hmac FROM blobs WHERE thread_id=? "
                "AND checkpoint_ns=? AND channel=? AND version=?",
                (thread_id, checkpoint_ns, k, str(ver)),
            ).fetchone()
            if row is None:
                continue
            self._verify_row("blobs", {
                "thread_id": thread_id, "checkpoint_ns": checkpoint_ns,
                "channel": k, "version": str(ver),
                "value_type": row[0], "value": row[1],
            }, row[2])
            if row[0] == EMPTY_TYPE:
                continue
            result[k] = self.serde.loads_typed((row[0], row[1]))
        return result

    def _tuple(self, thread_id: str, checkpoint_ns: str,
               checkpoint_id: str) -> Optional[CheckpointTuple]:
        conn = self._connect()
        row = conn.execute(
            "SELECT checkpoint_type, checkpoint, metadata_type, metadata, "
            "parent_checkpoint_id, hmac FROM checkpoints "
            "WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
            (thread_id, checkpoint_ns, checkpoint_id),
        ).fetchone()
        if row is None:
            return None
        cp_type, cp_blob, md_type, md_blob, parent_id, cp_sig = row
        self._verify_row("checkpoints", {
            "thread_id": thread_id, "checkpoint_ns": checkpoint_ns,
            "checkpoint_id": checkpoint_id, "parent_checkpoint_id": parent_id,
            "checkpoint_type": cp_type, "checkpoint": cp_blob,
            "metadata_type": md_type, "metadata": md_blob,
        }, cp_sig)
        checkpoint_: Checkpoint = self.serde.loads_typed((cp_type, cp_blob))
        writes = conn.execute(
            "SELECT task_id, idx, channel, value_type, value, task_path, hmac "
            "FROM writes "
            "WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=? "
            "ORDER BY task_id, idx",
            (thread_id, checkpoint_ns, checkpoint_id),
        ).fetchall()
        pending = []
        for tid, widx, ch, vt, v, tpath, wsig in writes:
            self._verify_row("writes", {
                "thread_id": thread_id, "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint_id, "task_id": tid, "idx": widx,
                "channel": ch, "value_type": vt, "value": v,
                "task_path": tpath,
            }, wsig)
            pending.append((tid, ch, self.serde.loads_typed((vt, v))))
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id,
                }
            },
            checkpoint={
                **checkpoint_,
                "channel_values": self._load_blobs(
                    thread_id, checkpoint_ns, checkpoint_["channel_versions"]
                ),
            },
            metadata=self.serde.loads_typed((md_type, md_blob)),
            pending_writes=pending,
            parent_config=(
                {
                    "configurable": {
                        "thread_id": thread_id,
                        "checkpoint_ns": checkpoint_ns,
                        "checkpoint_id": parent_id,
                    }
                }
                if parent_id
                else None
            ),
        )

    # -- BaseCheckpointSaver API --------------------------------------------

    def get_tuple(self, config: RunnableConfig) -> Optional[CheckpointTuple]:
        thread_id = self._thread(config)
        checkpoint_ns = self._ns(config)
        with self._lock:
            if checkpoint_id := get_checkpoint_id(config):
                return self._tuple(thread_id, checkpoint_ns, checkpoint_id)
            conn = self._connect()
            row = conn.execute(
                "SELECT MAX(checkpoint_id) FROM checkpoints "
                "WHERE thread_id=? AND checkpoint_ns=?",
                (thread_id, checkpoint_ns),
            ).fetchone()
            if not row or not row[0]:
                return None
            return self._tuple(thread_id, checkpoint_ns, row[0])

    def list(
        self,
        config: Optional[RunnableConfig],
        *,
        filter: Optional[Dict[str, Any]] = None,
        before: Optional[RunnableConfig] = None,
        limit: Optional[int] = None,
    ) -> Iterator[CheckpointTuple]:
        with self._lock:
            conn = self._connect()
            select_cols = (
                "thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, "
                "checkpoint_type, checkpoint, metadata_type, metadata, hmac"
            )
            if config is not None:
                thread_id = self._thread(config)
                config_ns: Optional[str] = self._ns(config)
                config_cid = get_checkpoint_id(config)
                rows = conn.execute(
                    "SELECT %s FROM checkpoints "
                    "WHERE thread_id=? ORDER BY checkpoint_id DESC" % select_cols,
                    (thread_id,),
                ).fetchall()
            else:
                config_ns = None
                config_cid = None
                rows = conn.execute(
                    "SELECT %s FROM checkpoints "
                    "ORDER BY thread_id, checkpoint_id DESC" % select_cols
                ).fetchall()

            before_cid = get_checkpoint_id(before) if before else None
            emitted = 0
            for (tid, ns, cid, parent_id, cp_type, cp_blob,
                 md_type, md_blob, cp_sig) in rows:
                if config_ns is not None and ns != config_ns:
                    continue
                if config_cid and cid != config_cid:
                    continue
                if before_cid and cid >= before_cid:
                    continue
                # fail-closed：先验签再反序列化 metadata
                self._verify_row("checkpoints", {
                    "thread_id": tid, "checkpoint_ns": ns, "checkpoint_id": cid,
                    "parent_checkpoint_id": parent_id,
                    "checkpoint_type": cp_type, "checkpoint": cp_blob,
                    "metadata_type": md_type, "metadata": md_blob,
                }, cp_sig)
                metadata = self.serde.loads_typed((md_type, md_blob))
                if filter and not all(
                    v == metadata.get(k) for k, v in filter.items()
                ):
                    continue
                if limit is not None and emitted >= limit:
                    break
                emitted += 1
                tup = self._tuple(tid, ns, cid)
                if tup is not None:
                    yield tup

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        c = checkpoint.copy()
        thread_id = self._thread(config)
        checkpoint_ns = config.get("configurable", {}).get("checkpoint_ns", "")
        values: Dict[str, Any] = c.pop("channel_values")  # type: ignore[misc]
        with self._lock:
            conn = self._connect()
            for k, ver in new_versions.items():
                if k in values:
                    vtype, vblob = self.serde.dumps_typed(values[k])
                else:
                    vtype, vblob = EMPTY_TYPE, b""
                blob_sig = self._sign_row("blobs", {
                    "thread_id": thread_id, "checkpoint_ns": checkpoint_ns,
                    "channel": k, "version": str(ver),
                    "value_type": vtype, "value": vblob,
                })
                conn.execute(
                    "INSERT OR REPLACE INTO blobs "
                    "(thread_id, checkpoint_ns, channel, version, "
                    "value_type, value, hmac) VALUES (?,?,?,?,?,?,?)",
                    (thread_id, checkpoint_ns, k, str(ver), vtype, vblob,
                     blob_sig),
                )
            cp_type, cp_blob = self.serde.dumps_typed(c)
            md_type, md_blob = self.serde.dumps_typed(
                get_checkpoint_metadata(config, metadata)
            )
            parent_id = config.get("configurable", {}).get("checkpoint_id")
            cp_sig = self._sign_row("checkpoints", {
                "thread_id": thread_id, "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
                "parent_checkpoint_id": parent_id,
                "checkpoint_type": cp_type, "checkpoint": cp_blob,
                "metadata_type": md_type, "metadata": md_blob,
            })
            conn.execute(
                "INSERT OR REPLACE INTO checkpoints "
                "(thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, "
                "checkpoint_type, checkpoint, metadata_type, metadata, hmac) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    thread_id,
                    checkpoint_ns,
                    checkpoint["id"],
                    parent_id,
                    cp_type,
                    cp_blob,
                    md_type,
                    md_blob,
                    cp_sig,
                ),
            )
            conn.commit()
        return {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
                "checkpoint_id": checkpoint["id"],
            }
        }

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[Tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = self._thread(config)
        checkpoint_ns = config.get("configurable", {}).get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]

        with self._lock:
            conn = self._connect()
            for idx, (channel, value) in enumerate(writes):
                write_idx = WRITES_IDX_MAP.get(channel, idx)
                if write_idx >= 0:
                    existing = conn.execute(
                        "SELECT 1 FROM writes WHERE thread_id=? AND checkpoint_ns=? "
                        "AND checkpoint_id=? AND task_id=? AND idx=?",
                        (thread_id, checkpoint_ns, checkpoint_id, task_id, write_idx),
                    ).fetchone()
                    if existing:
                        continue
                vtype, vblob = self.serde.dumps_typed(value)
                w_sig = self._sign_row("writes", {
                    "thread_id": thread_id, "checkpoint_ns": checkpoint_ns,
                    "checkpoint_id": checkpoint_id, "task_id": task_id,
                    "idx": write_idx, "channel": channel,
                    "value_type": vtype, "value": vblob, "task_path": task_path,
                })
                conn.execute(
                    "INSERT OR REPLACE INTO writes "
                    "(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, "
                    "channel, value_type, value, task_path, hmac) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        thread_id, checkpoint_ns, checkpoint_id, task_id, write_idx,
                        channel, vtype, vblob, task_path, w_sig,
                    ),
                )
            conn.commit()

    def delete_thread(self, thread_id: str) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute("DELETE FROM checkpoints WHERE thread_id=?", (thread_id,))
            conn.execute("DELETE FROM writes WHERE thread_id=?", (thread_id,))
            conn.execute("DELETE FROM blobs WHERE thread_id=?", (thread_id,))
            conn.commit()

    def delete_for_runs(self, run_ids: Sequence[str]) -> None:
        # run_id 不由本 checkpointer 索引；保留接口兼容性
        return None

    def copy_thread(self, source_thread_id: str, target_thread_id: str) -> None:
        with self._lock:
            conn = self._connect()
            for table in ("checkpoints", "writes", "blobs"):
                cols = [r[1] for r in conn.execute(
                    "PRAGMA table_info(%s)" % table).fetchall()]
                rows = conn.execute(
                    "SELECT %s FROM %s WHERE thread_id=?"
                    % (",".join(cols), table), (source_thread_id,)
                ).fetchall()
                for row in rows:
                    record = dict(zip(cols, row))
                    record["thread_id"] = target_thread_id
                    # thread_id 参与签名，复制后必须按新主键重签，
                    # 否则目标线程的记录会验签失败。
                    if "hmac" in cols:
                        record["hmac"] = self._sign_row(table, record)
                    values = tuple(record[c] for c in cols)
                    conn.execute(
                        "INSERT OR REPLACE INTO %s (%s) VALUES (%s)"
                        % (table, ",".join(cols), ",".join("?" * len(cols))),
                        values,
                    )
            conn.commit()


# 供外部查询/清理用的小工具 ------------------------------------------------

def thread_ids(db_path: str) -> List[str]:
    """列出某个 checkpoint 数据库中保存过的所有 thread_id。"""
    if not os.path.isfile(db_path):
        return []
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id"
        ).fetchall()
        return [r[0] for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def dump_json(db_path: str) -> str:
    """人类可读的 checkpoint 概览（调试用）。"""
    if not os.path.isfile(db_path):
        return "{}"
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id "
            "FROM checkpoints ORDER BY thread_id, checkpoint_id"
        ).fetchall()
    except sqlite3.Error:
        return "{}"
    finally:
        conn.close()
    return json.dumps(
        [
            {
                "thread_id": t,
                "checkpoint_ns": ns,
                "checkpoint_id": cid,
                "parent_checkpoint_id": pid,
            }
            for t, ns, cid, pid in rows
        ],
        ensure_ascii=False,
        indent=2,
    )
