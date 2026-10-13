"""Oracle implementation of :class:`db.repos.protocols.ImportsRepo`.

SQLAlchemy 2.0 async Core over the ``IMPORTS`` table (``zvms_new.imports``). The
Mongo ``date`` is an ISO string (mapped to the naive ``IMPORT_DATE``
``TIMESTAMP``); ``content`` is a BSON ``Binary``/``bytes`` payload mapped to the
``CONTENT`` ``BLOB``.

Session ownership mirrors :mod:`db.repos.oracle.users`. Nothing here opens a
connection at import time.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid
from db.models import imports as imports_tbl
from db.oracle.session import session_scope

Row = dict[str, Any]


@asynccontextmanager
async def _session_for(
    session: AsyncSession | None,
) -> AsyncIterator[tuple[AsyncSession, bool]]:
    """Yield ``(session, owns)``; ``owns`` is True only for repo-created ones."""
    if session is not None:
        yield session, False
        return
    async with session_scope() as owned:
        yield owned, True


def _parse_date(value: Any) -> datetime | None:
    """Parse the Mongo ISO string (or pass a ``datetime``/``None``) to naive ``TIMESTAMP``."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value)
    if text == "":
        return None
    return datetime.fromisoformat(text)


def _insert_stmt(
    oid: str,
    import_date: datetime | None,
    description: str,
    filename: str,
    name: str,
    user_oid: str,
    content: bytes | None,
):
    return insert(imports_tbl).values(
        oid=oid,
        import_date=import_date,
        description=description,
        filename=filename,
        name=name,
        user_oid=user_oid,
        content=content,
    )


class OracleImportsRepo:
    """``ImportsRepo`` over Oracle ADB (``zvms_new.imports``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def create(
        self,
        name: str,
        filename: str,
        description: str,
        user: str,
        date: str,
        content: Any,
    ) -> str:
        oid = new_oid()
        payload = bytes(content) if content is not None else None
        stmt = _insert_stmt(
            oid, _parse_date(date), description, filename, name, user, payload
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return oid


__all__ = ["OracleImportsRepo"]
