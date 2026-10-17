"""维护模式（maintenance mode）状态的本地持久化层。

状态存本地 SQLite（标准库 ``sqlite3``，无新依赖），与业务库解耦：维护模式
必须在业务库不可用时仍能读写、且不能因业务库故障而丢失，所以这里不碰任何
repo / engine，也不依赖 startup hook（惰性建表，首次访问自建）。
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import closing

# 后端根目录：本文件位于 db/ 下，向上两级即后端根。刻意不用 cwd ——
# systemd 的 WorkingDirectory 可能变，相对路径会指向别处。
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STATE_DB_PATH = os.environ.get("ZVMS_SYSTEM_DB") or os.path.join(
    _BACKEND_ROOT, "system_state.db"
)

# is_enabled() 每个请求都会被中间件调一次，靠进程内 TTL 缓存把它压成廉价读取，
# 避免每请求打一次 SQLite。
CACHE_TTL = 2.0

_CREATE_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS maintenance_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  enabled INTEGER NOT NULL DEFAULT 0,
  reason TEXT,
  announcement TEXT,
  since REAL,
  updated_by TEXT,
  updated_at REAL
)
"""

# 除 Mongo 日志外再留一份本地审计，Mongo 写失败时不至于空白。
_CREATE_EVENTS_TABLE = """
CREATE TABLE IF NOT EXISTS maintenance_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at REAL NOT NULL,
  actor_oid TEXT,
  enabled INTEGER NOT NULL,
  reason TEXT,
  announcement TEXT
)
"""

_UPSERT_STATE = """
INSERT INTO maintenance_state (
  id, enabled, reason, announcement, since, updated_by, updated_at
) VALUES (1, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
  enabled = excluded.enabled,
  reason = excluded.reason,
  announcement = excluded.announcement,
  since = excluded.since,
  updated_by = excluded.updated_by,
  updated_at = excluded.updated_at
"""

_init_lock = threading.Lock()
_initialized_paths: set[str] = set()

_cache_lock = threading.Lock()
_cache_path: str | None = None
_cache_value: bool = False
_cache_at: float = 0.0


def _db_path() -> str:
    """每次读环境变量：测试用 monkeypatch.setenv 隔离，运行期改动即刻生效。"""
    return os.environ.get("ZVMS_SYSTEM_DB") or STATE_DB_PATH


def _connect() -> sqlite3.Connection:
    """新建短连接（不做全局单例，避免多线程共用连接）。"""
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=3000")
    return conn


def init_db() -> None:
    """惰性建表，幂等。首次访问时自动调用，不依赖任何 startup hook。"""
    path = _db_path()
    if path in _initialized_paths:
        return
    with _init_lock:
        if path in _initialized_paths:
            return
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        # 内层 `conn` 负责提交/回滚，`closing` 负责关闭（with sqlite3.connect
        # 本身只提交不关闭）。
        with closing(_connect()) as conn, conn:
            conn.execute(_CREATE_STATE_TABLE)
            conn.execute(_CREATE_EVENTS_TABLE)
        _initialized_paths.add(path)


def get_state() -> dict:
    """读当前状态；无行时返回全默认值（刻意不写行，读操作保持只读）。"""
    init_db()
    with closing(_connect()) as conn:
        row = conn.execute(
            "SELECT enabled, since, reason, announcement, updated_by, updated_at "
            "FROM maintenance_state WHERE id = 1"
        ).fetchone()
    if row is None:
        return {
            "enabled": False,
            "since": None,
            "reason": None,
            "announcement": None,
            "updated_by": None,
            "updated_at": None,
        }
    return {
        "enabled": bool(row["enabled"]),
        "since": row["since"],
        "reason": row["reason"],
        "announcement": row["announcement"],
        "updated_by": row["updated_by"],
        "updated_at": row["updated_at"],
    }


def set_state(
    enabled: bool,
    reason: str | None,
    announcement: str | None,
    actor_oid: str,
) -> dict:
    """写状态（单行 id=1 UPSERT）+ 追一条本地审计，返回新状态。

    ``since`` 只在 False→True 时更新为当前时间，True→False 时清空；已经是
    开启状态时再 set 不会改写 ``since``（幂等）。
    """
    init_db()
    now = time.time()
    with closing(_connect()) as conn, conn:
        row = conn.execute(
            "SELECT enabled, since FROM maintenance_state WHERE id = 1"
        ).fetchone()
        previous_enabled = bool(row["enabled"]) if row is not None else False
        if enabled and not previous_enabled:
            since = now
        elif enabled:
            since = row["since"]
        else:
            since = None

        conn.execute(
            _UPSERT_STATE,
            (
                1 if enabled else 0,
                reason,
                announcement,
                since,
                actor_oid,
                now,
            ),
        )
        conn.execute(
            "INSERT INTO maintenance_events "
            "(at, actor_oid, enabled, reason, announcement) VALUES (?, ?, ?, ?, ?)",
            (now, actor_oid, 1 if enabled else 0, reason, announcement),
        )
    invalidate_cache()
    return get_state()


def is_enabled() -> bool:
    """当前是否维护模式，进程内 TTL 缓存兜住读路径。"""
    path = _db_path()
    now = time.monotonic()
    with _cache_lock:
        # 缓存按路径区分：换库（测试隔离）时不会读到上一个库的值。
        if _cache_path == path and (now - _cache_at) < CACHE_TTL:
            return _cache_value
    value = bool(get_state()["enabled"])
    with _cache_lock:
        _set_cache_locked(path, value)
    return value


def invalidate_cache() -> None:
    """供测试或外部直接改库后手动失效缓存。"""
    with _cache_lock:
        _set_cache_locked(None, False)


def _set_cache_locked(path: str | None, value: bool) -> None:
    global _cache_path, _cache_value, _cache_at
    _cache_path = path
    _cache_value = value
    _cache_at = time.monotonic()
