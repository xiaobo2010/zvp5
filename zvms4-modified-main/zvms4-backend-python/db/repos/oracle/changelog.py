"""Oracle implementation of :class:`db.repos.protocols.ChangelogRepo`.

SQLAlchemy 2.0 async Core over the ``CHANGELOG`` table (``zvms_new.changelog``):
``version`` → ``VERSION``, ``date`` → ``RELEASE_DATE`` (mapped back to the Mongo
key ``date`` on read), ``items`` → a JSON ``CLOB``. Returned rows use the shared
``Row`` shape (``_id`` lowercase hex, ``date``, ``items`` as a decoded list).

Session ownership
-----------------
``__init__`` takes an optional ``AsyncSession``:

* ``session is None`` (default): each method opens its own session through
  :func:`db.oracle.session.session_scope`, and **writes commit once** (one write
  = one transaction); the session is closed by ``session_scope``.
* ``session`` supplied: the repo uses it as-is and **never commits or closes**
  it — the caller owns the unit of work.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import changelog as changelog_tbl
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


def _changelog_columns() -> tuple[Any, ...]:
    """The full CHANGELOG projection shared by the list / by-id lookups."""
    return (
        changelog_tbl.c.oid,
        changelog_tbl.c.version,
        changelog_tbl.c.release_date,
        changelog_tbl.c["items"],
        changelog_tbl.c.updated_at,
    )


def _list_all_stmt():
    return select(*_changelog_columns()).order_by(
        changelog_tbl.c.release_date.desc(),
        changelog_tbl.c.version.desc(),
    )


def _get_by_oid_stmt(oid: str):
    return select(*_changelog_columns()).where(changelog_tbl.c.oid == oid)


def _insert_stmt(oid: str, version: str, release_date: str, items: list[str]):
    return insert(changelog_tbl).values(
        oid=oid,
        version=version,
        release_date=release_date,
        items=items,
        updated_at=datetime.now(),
    )


def _update_stmt(oid: str, version: str, release_date: str, items: list[str]):
    return (
        update(changelog_tbl)
        .where(changelog_tbl.c.oid == oid)
        .values(
            version=version,
            release_date=release_date,
            items=items,
            updated_at=datetime.now(),
        )
    )


def _delete_stmt(oid: str):
    return delete(changelog_tbl).where(changelog_tbl.c.oid == oid)


def _entry_doc(mapping: Any) -> Row:
    """Normalise an ORM row to the shared ``Row`` shape."""
    items = mapping["items"]
    return {
        "_id": mapping["oid"],
        "version": mapping["version"],
        "date": mapping["release_date"],
        "items": items if items is not None else [],
        "updated_at": mapping["updated_at"],
    }


class OracleChangelogRepo:
    """``ChangelogRepo`` over Oracle ADB (``zvms_new.changelog``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def list_all(self) -> list[Row]:
        stmt = _list_all_stmt()
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_entry_doc(row._mapping) for row in rows]

    async def get_by_oid(self, oid: str) -> Row | None:
        stmt = _get_by_oid_stmt(normalize_oid(oid))
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return _entry_doc(row._mapping)

    async def create(self, version: str, date: str, items: list[str]) -> str:
        oid = new_oid()
        stmt = _insert_stmt(oid, version, date, list(items))
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return oid

    async def update(
        self, oid: str, version: str, date: str, items: list[str]
    ) -> int:
        stmt = _update_stmt(normalize_oid(oid), version, date, list(items))
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return int(result.rowcount)

    async def delete(self, oid: str) -> int:
        stmt = _delete_stmt(normalize_oid(oid))
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return int(result.rowcount)


__all__ = ["OracleChangelogRepo"]
