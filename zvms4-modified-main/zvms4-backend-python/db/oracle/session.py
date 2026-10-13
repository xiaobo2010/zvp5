"""Session helpers for the Oracle async engine.

The sessionmaker is built lazily so importing this module performs no I/O.
Three entry points are provided:

* :func:`get_session` - a FastAPI dependency that yields a session and closes
  it afterwards.
* :func:`get_oracle_session` - a FastAPI dependency that yields a session
  (kept as a named alias for call sites expecting the Oracle-specific name).
* :func:`session_scope` - an async context manager for non-FastAPI callers.

ADB is the only backend, so no session entry point is conditional.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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


async def get_oracle_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yield a session and always close it.

    Alias of :func:`get_session`; kept under the Oracle-specific name for
    call sites that expect it. ADB is the only backend, so a session is
    always created.
    """
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
