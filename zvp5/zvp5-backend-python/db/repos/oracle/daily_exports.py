"""Oracle implementation of :class:`db.repos.protocols.DailyExportsRepo`.

SQLAlchemy 2.0 async Core over the ``DAILY_EXPORTS`` table
(``zvms_new.daily_exports``). ``data`` is a JSON ``CLOB`` array of spreadsheet
rows; ``push_data`` performs a read-modify-write because Oracle has no
``$push``.

Session ownership mirrors :mod:`db.repos.oracle.users`: with no injected session
each method opens its own through :func:`db.oracle.session.session_scope`, and
writes commit once; an injected session is neither committed nor closed.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import daily_exports as daily_exports_tbl
from db.oracle.session import session_scope

Row = dict[str, Any]

# Mongo field name -> Oracle column name (identical here).
_FIELD_COLUMNS: dict[str, str] = {
    "created_at": "created_at",
    "status": "status",
    "data": "data",
}


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


def _columns() -> tuple[Any, ...]:
    return (
        daily_exports_tbl.c.oid,
        daily_exports_tbl.c.created_at,
        daily_exports_tbl.c.status,
        daily_exports_tbl.c["data"],
    )


def _insert_stmt(
    oid: str,
    created_at: Any,
    status: str,
    data: Any,
):
    return insert(daily_exports_tbl).values(
        oid=oid,
        created_at=created_at,
        status=status,
        data=data,
    )


def _get_stmt(oid: str):
    return select(*_columns()).where(daily_exports_tbl.c.oid == oid)


def _data_stmt(oid: str):
    return select(daily_exports_tbl.c["data"]).where(daily_exports_tbl.c.oid == oid)


def _update_stmt(oid: str, values: Row):
    return (
        update(daily_exports_tbl)
        .where(daily_exports_tbl.c.oid == oid)
        .values(**values)
    )


def _delete_all_stmt():
    return delete(daily_exports_tbl)


def _field_values(fields: Row) -> Row:
    """Map Mongo-keyed ``$set`` fields to Oracle column values."""
    values: Row = {}
    for key, value in fields.items():
        column = _FIELD_COLUMNS.get(key)
        if column is not None:
            values[column] = value
    return values


def _row(mapping: Any) -> Row:
    data = mapping["data"]
    return {
        "_id": mapping["oid"],
        "created_at": mapping["created_at"],
        "status": mapping["status"],
        "data": data if data is not None else [],
    }


class OracleDailyExportsRepo:
    """``DailyExportsRepo`` over Oracle ADB (``zvms_new.daily_exports``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def delete_all(self) -> int:
        stmt = _delete_all_stmt()
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return int(result.rowcount)

    async def insert(self, document: Row) -> str:
        oid = new_oid()
        stmt = _insert_stmt(
            oid,
            document.get("created_at"),
            document.get("status"),
            document.get("data") or [],
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return oid

    async def update_fields(self, oid: str, fields: Row) -> None:
        values = _field_values(fields)
        if not values:
            return
        stmt = _update_stmt(normalize_oid(oid), values)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def push_data(self, oid: str, item: Row) -> None:
        target = normalize_oid(oid)
        async with _session_for(self._session) as (session, owns):
            current = (
                await session.execute(_data_stmt(target))
            ).scalar_one_or_none()
            merged = list(current) if current is not None else []
            merged.append(item)
            await session.execute(_update_stmt(target, {"data": merged}))
            if owns:
                await session.commit()

    async def find_one(self, oid: str) -> Row | None:
        stmt = _get_stmt(normalize_oid(oid))
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return _row(row._mapping)


__all__ = ["OracleDailyExportsRepo"]
