"""维护模式（maintenance mode）状态的本地持久化层。

状态存本地 SQLite（标准库 ``sqlite3``，无新依赖），与业务库解耦：维护模式
必须在业务库不可用时仍能读写、且不能因业务库故障而丢失，所以这里不碰任何
repo / engine，也不依赖 startup hook（惰性建表，首次访问自建）。

崩溃自愈（清理编排把全站切进维护态后进程崩了，绝不能留下「永久 503」）：

* 不新增 schema 列 —— 复用 ``maintenance_state.updated_by`` 编码**自动来源**：
  值以 :data:`AUTO_ACTOR_PREFIX`（``auto:``）开头，就说明这一行是**进程内的
  自动流程**（数据库清理，``tasks/retention.py``）写的，而不是管理员手工开启的。
* 人工维护（``updated_by`` 是管理员 oid）**永不**自愈。
* 自动来源是否还活着由使用方通过 :func:`set_auto_source_probe` 注册的探针回答
  （``tasks/retention.py`` 在 import 时注册）。``enabled=1`` + 自动来源 + 探针
  返回 ``False`` ⇒ 上一次进程已经不在 ⇒ **判定为未维护**，并惰性清除该行
  （写 ``enabled=0``）+ 记一条 ``maintenance_events`` 审计。
* **核心前提：单 worker 进程内标志即真相。** 进程重启后不可能还有清理在跑，
  所以陈旧态必须自愈；反过来，多 worker / 多机部署会破坏该假设（A worker 的
  清理会被 B worker 的探针误判为陈旧）。实测生产为单 worker uvicorn
  （``systemd ExecStart: uvicorn main:app``，无 ``--workers``）。若将来多 worker，
  探针必须换成带租约的外部存储（如心跳行），不能再读进程内标志。
* 自愈与 :data:`CACHE_TTL`（2s）的交互：自愈走 :func:`set_state`，而它会
  :func:`invalidate_cache`，所以**本进程**下一次 :func:`is_enabled` 立刻读到
  ``False``；唯一窗口是「缓存里还压着 True 的那 2s 内」——与既有缓存语义一致。

读路径的故障边界：本模块的读函数在事件循环线程上被每请求调用（同步 sqlite），
任何 ``sqlite3.Error`` / ``OSError``（库被锁、只读盘、磁盘满）都必须就地消化，
否则一个本地故障会把全站每个 ``/api/`` 请求变成 500。读函数因此**永不抛异常**：
记一次告警（同类错误只告一次，避免日志风暴）后返回「未维护」这个安全默认值
（fail-open —— 本地状态库坏了，宁可放行也不要把站点钉死在 503）。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from contextlib import closing
from typing import Callable

_logger = logging.getLogger(__name__)

# 后端根目录：本文件位于 db/ 下，向上两级即后端根。刻意不用 cwd ——
# systemd 的 WorkingDirectory 可能变，相对路径会指向别处。
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

STATE_DB_PATH = os.environ.get("ZVMS_SYSTEM_DB") or os.path.join(
    _BACKEND_ROOT, "system_state.db"
)

# is_enabled() 每个请求都会被中间件调一次，靠进程内 TTL 缓存把它压成廉价读取，
# 避免每请求打一次 SQLite。
CACHE_TTL = 2.0

# 「准入复核」缓存（is_enabled_fresh）：维护中间件在缓存说 True 时会再复核一次，
# 若不复核就直读库，每个请求仍会打两次 SQLite。它必须**短**：这是「管理员开关
# 维护后多久生效」的上界，1s 内本进程与跨进程的改动都能看到。
FRESH_CACHE_TTL = 1.0

# 这些读发生在事件循环线程上（同步 sqlite），等待时间 1:1 变成事件循环停顿。
# WAL 下读几乎不会阻塞（写者不挡读者），300ms 只兜住 checkpoint / schema 锁这类
# 罕见争用；再大就会把一个本地锁变成全站延迟。
BUSY_TIMEOUT_MS = 300

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

_fresh_lock = threading.Lock()
_fresh_path: str | None = None
_fresh_value: bool = False
_fresh_at: float = 0.0

# 同类故障只告一次：状态库坏了会命中每个请求，逐请求告警等于日志风暴。
# 键是「操作 + 异常类型」，集合天然有界。
_warned_failures: set[str] = set()
_failure_lock = threading.Lock()

DEFAULT_STATE = {
    "enabled": False,
    "since": None,
    "reason": None,
    "announcement": None,
    "updated_by": None,
    "updated_at": None,
}

# 「自动来源」标记：``updated_by`` 以它开头即该维护态由进程内自动流程写入。
AUTO_ACTOR_PREFIX = "auto:"
# 自愈写审计时的 actor：也带该前缀（enabled=0，不会再被判定）。
AUTO_HEAL_ACTOR = "auto:heal"
# 自愈时写进 reason 的说明（state 行与 events 行都会带上）。
STALE_AUTO_REASON = "自动清理进程已退出，维护态自愈"

# 探针注册表：``None`` = 没有注册（例如只 import 本模块的测试）⇒ 无法判定
# 自动来源是否还活着 ⇒ **保守地不自愈**（宁可靠管理员手工关闭，也不误判）。
_probe_lock = threading.Lock()
_auto_source_probe: Callable[[], bool] | None = None

# 自愈写库要串行：多个请求同时发现陈旧态时必须只写一次库（一次 enabled=0 +
# 一条审计），而不是每个请求写一次。锁只圈住判断+写库，不跨任何 IO 等待。
_heal_lock = threading.Lock()


def _db_path() -> str:
    """每次读环境变量：测试用 monkeypatch.setenv 隔离，运行期改动即刻生效。"""
    return os.environ.get("ZVMS_SYSTEM_DB") or STATE_DB_PATH


def _connect() -> sqlite3.Connection:
    """新建短连接（不做全局单例，避免多线程共用连接）。"""
    conn = sqlite3.connect(_db_path(), timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    return conn


def _warn_once(op: str, exc: BaseException) -> None:
    """同类故障只记一条告警（键 = 操作 + 异常类型）。"""
    key = f"{op}:{type(exc).__name__}"
    with _failure_lock:
        if key in _warned_failures:
            return
        _warned_failures.add(key)
    _logger.warning(
        "system_state: %s failed (%s: %s) -> falling back to defaults",
        op,
        type(exc).__name__,
        exc,
    )


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
    """读当前状态；无行时返回全默认值（刻意不写行，读操作保持只读）。

    永不抛异常：本地状态库不可用时返回 :data:`DEFAULT_STATE`（未维护，fail-open）。
    """
    try:
        init_db()
        with closing(_connect()) as conn:
            row = conn.execute(
                "SELECT enabled, since, reason, announcement, updated_by, updated_at "
                "FROM maintenance_state WHERE id = 1"
            ).fetchone()
    except (sqlite3.Error, OSError) as exc:
        _warn_once("get_state", exc)
        return dict(DEFAULT_STATE)
    if row is None:
        return dict(DEFAULT_STATE)
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


def invalidate_cache() -> None:
    """供测试或外部直接改库后手动失效缓存（两级缓存一起失效）。"""
    with _cache_lock:
        _set_cache_locked(None, False)
    with _fresh_lock:
        _set_fresh_cache_locked(None, False)


def set_auto_source_probe(probe: Callable[[], bool] | None) -> None:
    """注册「自动维护来源是否仍在运行」的探针；``None`` 表示注销。

    由 :mod:`tasks.retention` 在 **import 时**注册（``main.py`` 已 import 该
    模块用于调度，因此探针在服务受理第一个请求之前就已就位），避免两个模块
    互相 import。探针必须是**不阻塞、不抛异常（抛了按未知处理）**的廉价调用，
    因为它可能出现在每个请求的准入路径上。
    """
    global _auto_source_probe
    with _probe_lock:
        _auto_source_probe = probe


def is_auto_actor(updated_by: str | None) -> bool:
    """``updated_by`` 是否标记为自动来源（见模块 docstring）。"""
    return bool(updated_by) and str(updated_by).startswith(AUTO_ACTOR_PREFIX)


def _auto_probe_result() -> bool | None:
    """探针结果：``True`` 活着 / ``False`` 已不在 / ``None`` 无法判定。"""
    with _probe_lock:
        probe = _auto_source_probe
    if probe is None:
        return None
    try:
        return bool(probe())
    except Exception:  # 探针坏了不能把准入路径带崩，按未知处理（不自愈）
        return None


def _state_with_auto_recovery() -> dict:
    """读状态；发现「自动来源的陈旧维护态」时惰性自愈，返回自愈后的状态。

    绝大多数调用都走前两个 return（非维护态 / 人工维护 / 探针活着），**不写库**；
    只有确认陈旧时才进入 :data:`_heal_lock` 写一次（双重检查，保证并发下也只写
    一次）。
    """
    state = get_state()
    if not state["enabled"] or not is_auto_actor(state["updated_by"]):
        return state

    if _auto_probe_result() is not False:
        # True = 自动流程还在跑；None = 无法判定（未注册探针 / 探针坏了）
        return state

    with _heal_lock:
        # 双重检查：可能在等锁期间已被别的线程自愈，或自动流程刚好又启动了。
        state = get_state()
        if not state["enabled"] or not is_auto_actor(state["updated_by"]):
            return state
        if _auto_probe_result() is not False:
            return state
        print(
            "maintenance: stale auto maintenance state detected "
            f"(updated_by={state['updated_by']!r}, probe=False) -> healing"
        )
        try:
            set_state(False, STALE_AUTO_REASON, None, AUTO_HEAL_ACTOR)
        except (sqlite3.Error, OSError) as exc:
            # 自愈是写操作，失败于读路径上是可容忍的：状态库刚才是可读的，
            # 如实返回读到的值，别把写失败升级成每请求 500。
            _warn_once("heal", exc)
            return state
        return get_state()


def is_enabled() -> bool:
    """当前是否维护模式，进程内 TTL 缓存兜住读路径。

    自动来源的陈旧维护态在这里自愈（见模块 docstring）：自愈写库会让缓存失效，
    所以本进程读到的立刻是 ``False``。
    """
    path = _db_path()
    now = time.monotonic()
    with _cache_lock:
        # 缓存按路径区分：换库（测试隔离）时不会读到上一个库的值。
        if _cache_path == path and (now - _cache_at) < CACHE_TTL:
            return _cache_value
    value = bool(_state_with_auto_recovery()["enabled"])
    with _cache_lock:
        _set_cache_locked(path, value)
    return value


def is_enabled_fresh() -> bool:
    """读当前是否维护模式，绕过 :data:`CACHE_TTL`，走 :data:`FRESH_CACHE_TTL`。

    给「准入类」判断用：缓存窗口内刚开启的维护模式不能被放进来，所以这里不看
    :func:`is_enabled` 的 2s 缓存；但准入路径每个请求都会走到，直读 SQLite 会把
    每请求的库读取翻倍。故用 1s 的短缓存，并由 :func:`invalidate_cache`（本进程
    每次 ``set_state`` 都会调）主动失效 —— 本进程开关即刻生效，跨进程最多 1s。
    """
    path = _db_path()
    now = time.monotonic()
    with _fresh_lock:
        if _fresh_path == path and (now - _fresh_at) < FRESH_CACHE_TTL:
            return _fresh_value
    value = bool(_state_with_auto_recovery()["enabled"])
    with _fresh_lock:
        _set_fresh_cache_locked(path, value)
    return value



def _set_cache_locked(path: str | None, value: bool) -> None:
    global _cache_path, _cache_value, _cache_at
    _cache_path = path
    _cache_value = value
    _cache_at = time.monotonic()


def _set_fresh_cache_locked(path: str | None, value: bool) -> None:
    global _fresh_path, _fresh_value, _fresh_at
    _fresh_path = path
    _fresh_value = value
    _fresh_at = time.monotonic()
