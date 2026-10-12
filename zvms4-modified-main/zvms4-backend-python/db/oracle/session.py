"""Session helpers for the Oracle async engine.

The sessionmaker is built lazily so importing this module performs no I/O.
Three entry points are provided:

* :func:`get_session` - a FastAPI dependency that yields a session and closes
  it afterwards.
* :func:`get_oracle_session` - a FastAPI dependency that yields a session only
  when Oracle is enabled globally, otherwise ``None`` (so unmigrated modules'
  Mongo code is unaffected).
* :func:`session_scope` - an async context manager for non-FastAPI callers.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import settings

from . import engine as oracle_engine

_sessionmaker: Optional[async_sessionmaker] = None


def get_sessionmaker() -> async_sessionmaker:
    """Return the process-wide sessionmaker, creating it on first use."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = oracle_engine.create_sessionmaker()
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yield a session and always close it."""
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        await session.close()


async def get_oracle_session() -> AsyncIterator[Optional[AsyncSession]]:
    """FastAPI dependency: yield an Oracle session when enabled, else ``None``.

    Reuses the shared sessionmaker, so a session is only ever created when the
    master switch is on. When Oracle is disabled this yields ``None`` without
    touching the engine or the network, keeping unmigrated modules on Mongo.
    """
    if not getattr(settings, "ORACLE_ENABLED", False):
        yield None
        return
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        await session.close()


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Async context manager yielding a session that is always closed."""
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        await session.close()
