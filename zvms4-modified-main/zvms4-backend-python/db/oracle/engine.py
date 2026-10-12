"""Lazy async engine creation for Oracle Autonomous Database.

Importing this module must not create an engine or touch the network. The
engine is only built the first time :func:`get_engine` (or anything that
depends on it) is called.
"""

from __future__ import annotations

from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from . import config as oracle_config

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
    args: dict = {}
    if cfg.wallet_dir:
        args["config_dir"] = cfg.wallet_dir
        args["wallet_location"] = cfg.wallet_dir
    if cfg.wallet_password:
        args["wallet_password"] = cfg.wallet_password
    return args


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it on first use."""
    global _engine
    if _engine is None:
        cfg = oracle_config.load_config()
        _engine = create_async_engine(
            oracle_config.build_async_url(cfg),
            connect_args=_connect_args(cfg),
            pool_size=5,
            max_overflow=5,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
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
