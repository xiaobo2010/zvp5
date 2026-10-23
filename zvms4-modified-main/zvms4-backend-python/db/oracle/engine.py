"""Lazy async engine creation for Oracle Autonomous Database.

Importing this module must not create an engine or touch the network. The
engine is only built the first time :func:`get_engine` (or anything that
depends on it) is called.
"""

from __future__ import annotations

import os
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from . import config as oracle_config
from .clob import install_lob_binds

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
    if cfg.wallet_dir:
        args["config_dir"] = cfg.wallet_dir
        args["wallet_location"] = cfg.wallet_dir
    if cfg.wallet_password:
        args["wallet_password"] = cfg.wallet_password
    return args


def _pool_settings() -> dict:
    """Pool sizing, overridable per deployment without a code change.

    ``pool_timeout`` is deliberately short: checkout runs on the greenlet as a
    blocking ``queue.get``, so an exhausted pool freezes the *whole* event loop
    for the timeout instead of failing one request. Production has one worker,
    which turned a saturated pool into multi-minute site-wide stalls
    (``QueuePool limit of size 5 overflow 5 reached``), so waiting is capped at
    a few seconds and the excess request fails fast.
    """
    return {
        "pool_size": int(os.environ.get("ZVMS_DB_POOL_SIZE", "8")),
        "max_overflow": int(os.environ.get("ZVMS_DB_POOL_OVERFLOW", "8")),
        "pool_timeout": float(os.environ.get("ZVMS_DB_POOL_TIMEOUT", "5")),
        "pool_use_lifo": True,
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
            pool_pre_ping=True,
            pool_recycle=1800,
            # SQLAlchemy appends bound parameters to StatementError by default.
            # These routes bind password hashes, CLOB previews and personal
            # data; keep them out of exception strings / logs.
            hide_parameters=True,
            **_pool_settings(),
        )
        install_lob_binds(_engine)
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
