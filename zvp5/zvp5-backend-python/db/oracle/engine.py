"""Lazy async engine creation for Oracle Autonomous Database.

Importing this module must not create an engine or touch the network. The
engine is only built the first time :func:`get_engine` (or anything that
depends on it) is called.

Timeout policy (why both knobs exist)
-------------------------------------
* ``pool_timeout`` (``ZVMS_DB_POOL_TIMEOUT``, seconds, default 5) bounds the
  **checkout** wait. In SQLAlchemy's async pool the checkout is a *blocking*
  ``queue.get`` executed inside the greenlet — it does not yield to the event
  loop. Production runs a single worker, so an exhausted pool froze every
  request (including health checks and socket.io pings) for the full timeout.
  There is no way to make that wait cooperative here, so the only safe move is
  to keep the bound short and let the excess request fail fast (503) instead of
  freezing the process: **the timeout is an upper bound on a site-wide stall**.
* ``call_timeout`` (``ZVMS_DB_CALL_TIMEOUT``, seconds, default 60) is passed to
  python-oracledb thin mode in **milliseconds** and bounds a single round trip
  (``DPY-4024``/``ORA-01013``-style). Without it a half-open connection keeps a
  request — and its pool slot — pinned until TCP gives up, which is exactly how
  a saturated pool turns into the freeze described above.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from . import config as oracle_config
from .clob import install_lob_binds

logger = logging.getLogger(__name__)

_engine: Optional[AsyncEngine] = None


def _connect_args(cfg: oracle_config.OracleConfig) -> dict:
    """Build thin-mode connect args, wiring up the wallet when configured.

    ``config_dir`` and ``wallet_location`` are passed straight through to
    ``oracledb.connect`` (see python-oracledb docs). They are only added when
    a wallet directory is configured.

    ``wallet_password`` decrypts an encrypted ``ewallet.pem`` (thin mode) and
    is added only when non-empty, so the driver never falls back to an
    interactive "Enter PEM pass phrase" prompt.
    """
    args: dict = {
        # Never let a black-holed ADB endpoint pin the whole worker in the
        # blocking connect handshake. 5s is enough for a healthy ADB round trip
        # and short enough that a site-wide stall is bounded.
        "tcp_connect_timeout": float(
            os.environ.get("ZVMS_DB_TCP_CONNECT_TIMEOUT", "5")
        ),
    }
    # NOTE: the per-round-trip cap (``call_timeout``) is **not** a connect
    # keyword. python-oracledb 26.x exposes it as a connection *attribute*
    # only — ``oracledb.connect_async()`` has no such parameter and raises
    # ``TypeError: connect_async() got an unexpected keyword argument
    # 'call_timeout'`` when it is passed here (that is exactly how the
    # production service failed to boot on 2026-11-07). It is applied per
    # connection by :func:`_install_call_timeout` instead.
    if cfg.wallet_dir:
        args["config_dir"] = cfg.wallet_dir
        args["wallet_location"] = cfg.wallet_dir
    if cfg.wallet_password:
        args["wallet_password"] = cfg.wallet_password
    return args


_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _call_timeout_ms() -> int:
    """Per-round-trip cap in **milliseconds** (``ZVMS_DB_CALL_TIMEOUT`` seconds).

    A half-open connection without this keeps a request — and its pool slot —
    pinned until TCP gives up, which is how a saturated pool turns into a
    site-wide freeze. 60s is far above the 138ms p99 of a healthy ADB call
    and still short enough that a dead link frees the slot within a minute.
    """
    return int(float(os.environ.get("ZVMS_DB_CALL_TIMEOUT", "60")) * 1000)


def _install_call_timeout(engine: AsyncEngine) -> None:
    """Apply ``call_timeout`` to every new physical connection.

    python-oracledb keeps ``call_timeout`` as a connection attribute (not a
    connect keyword), and the async dialect hands SQLAlchemy's pool an
    *adapter* object rather than the driver connection, so the raw connection
    is reached through ``_connection``. Failures are logged and ignored: a
    driver that does not know the attribute must not stop the service from
    booting.
    """
    timeout_ms = _call_timeout_ms()
    if timeout_ms <= 0:
        return

    sync_engine = getattr(engine, "sync_engine", None)
    if sync_engine is None:
        return

    try:

        @event.listens_for(sync_engine, "connect")
        def _set_call_timeout(dbapi_connection, connection_record) -> None:  # noqa: ANN001
            raw = getattr(dbapi_connection, "_connection", dbapi_connection)
            try:
                raw.call_timeout = timeout_ms
            except Exception:  # pragma: no cover - 驱动差异
                logger.warning(
                    "call_timeout 未能应用（驱动不支持或连接不可写）", exc_info=True
                )

    except Exception:  # pragma: no cover - 事件注册失败不应阻断启动
        logger.warning("call_timeout 事件注册失败，连接超时保持未设置", exc_info=True)


def _env_bool(name: str, default: bool) -> bool:
    """把环境变量读成布尔值；未设置时返回 ``default``。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUTHY


def _pool_settings() -> dict:
    """Pool sizing and health-check policy, overridable without a code change.

    ``pool_timeout`` is deliberately short: checkout runs on the greenlet as a
    blocking ``queue.get``, so an exhausted pool freezes the *whole* event loop
    for the timeout instead of failing one request. Production has one worker,
    which turned a saturated pool into multi-minute site-wide stalls
    (``QueuePool limit of size 5 overflow 5 reached``), so waiting is capped at
    a few seconds and the excess request fails fast.

    连接健康度这块是**跨洋链路的成本权衡**，默认值与直觉相反，别随手改回去：

    * ``pool_pre_ping`` 默认关闭（``ZVMS_DB_PRE_PING`` 默认 ``0``）。pre_ping 在
      **每次** checkout 前都先发一条探测 SQL 验证连接没死，加州→春川单程 RTT
      实测 138ms，一次 checkout 因此从 2 个 RTT 涨到 3 个（279ms → 416ms），
      所有请求都按这个比例变慢。代价是：ADB 侧静默掐掉的连接（空闲超时、
      实例维护、网络抖动）会以 ``DPY-1001``/``ORA-03113`` 的形式砸到第一个
      撞上它的请求上。
    * ``pool_recycle`` 默认 600 秒（``ZVMS_DB_RECYCLE``）。关掉 pre_ping 后，
      「连接是否还活着」只能靠**主动老化**来保证：超过 recycle 的连接在下次
      checkout 时被静默丢弃重建，我们永远不去碰一条可能已经被对端掐断的
      连接。600 秒是保守值 —— 比 ADB 典型的空闲断连阈值短，又不至于让连接
      池频繁重建（池只有 8 条，重建一次就是一个握手 RTT 的成本）。
    * 什么时候该把 ``ZVMS_DB_PRE_PING=1`` 打开：链路本身不稳（丢包/重传明显）、
      ADB 侧的空闲超时被调到比 recycle 更短、或实例侧频繁做维护重启时。此时
      多付一个 RTT 换「不把死连接交给业务 SQL」是划算的；反之在稳定链路上
      它就是纯开销。
    """
    return {
        "pool_size": int(os.environ.get("ZVMS_DB_POOL_SIZE", "8")),
        "max_overflow": int(os.environ.get("ZVMS_DB_POOL_OVERFLOW", "8")),
        "pool_timeout": float(os.environ.get("ZVMS_DB_POOL_TIMEOUT", "5")),
        "pool_use_lifo": True,
        "pool_pre_ping": _env_bool("ZVMS_DB_PRE_PING", False),
        "pool_recycle": int(os.environ.get("ZVMS_DB_RECYCLE", "600")),
    }


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it on first use.

    Also installs the shared "large ``str`` -> CLOB LOB" bind hook (see
    :mod:`db.oracle.clob`) so both the runtime repos and the ETL bind long
    JSON/CLOB values correctly.
    """
    global _engine
    if _engine is None:
        cfg = oracle_config.load_config()
        _engine = create_async_engine(
            oracle_config.build_async_url(cfg),
            connect_args=_connect_args(cfg),
            # SQLAlchemy appends bound parameters to StatementError by default.
            # These routes bind password hashes, CLOB previews and personal
            # data; keep them out of exception strings / logs.
            hide_parameters=True,
            **_pool_settings(),
        )
        install_lob_binds(_engine)
        _install_call_timeout(_engine)
    return _engine


def create_sessionmaker() -> async_sessionmaker:
    """Build an ``async_sessionmaker`` bound to the shared engine."""
    return async_sessionmaker(bind=get_engine(), expire_on_commit=False)


async def dispose() -> None:
    """Dispose the shared engine and drop the reference. Safe to call twice."""
    global _engine
    if _engine is not None:
        engine, _engine = _engine, None
        await engine.dispose()


async def ping() -> bool:
    """Open a connection, run ``SELECT 1 FROM DUAL`` and return success.

    Everything is closed regardless of outcome. This is the only code path
    that talks to the database and it is never executed at import time.
    """
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT 1 FROM DUAL"))
        row = result.first()
    return bool(row and row[0] == 1)
