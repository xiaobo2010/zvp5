"""Oracle implementation of :class:`db.repos.protocols.AppLogsRepo`.

SQLAlchemy 2.0 async Core over the ``APP_LOGS`` table (``zvms_new.app_logs``).
The Oracle-safe column renames (``user``→``APP_USER``, ``type``→``LTYPE``,
``level``→``LOG_LEVEL``, ``timestamp``→``TS_AT``/``TS_RAW``) are translated back
to the Mongo document shape on read.

``TS_RAW`` keeps the original float epoch seconds (authoritative for ordering and
the time-range filter); ``TS_AT`` is its derived naive ``TIMESTAMP``.

Session ownership mirrors :mod:`db.repos.oracle.users`: with no injected session
each method opens its own through :func:`db.oracle.session.session_scope`, and
writes commit once; an injected session is neither committed nor closed.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid
from db.models import app_logs as app_logs_tbl
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


def _log_columns() -> tuple[Any, ...]:
    return (
        app_logs_tbl.c.oid,
        app_logs_tbl.c.url,
        app_logs_tbl.c.app_user,
        app_logs_tbl.c.data,
        app_logs_tbl.c.ts_at,
        app_logs_tbl.c.ts_raw,
        app_logs_tbl.c.ltype,
        app_logs_tbl.c.log_level,
        app_logs_tbl.c.xuehai_id,
    )


def _insert_stmt(
    oid: str,
    url: str,
    app_user: str,
    data: str,
    ts_at: Any,
    ts_raw: float,
    ltype: str,
    log_level: str,
    xuehai_id: str,
):
    return insert(app_logs_tbl).values(
        oid=oid,
        url=url,
        app_user=app_user,
        data=data,
        ts_at=ts_at,
        ts_raw=ts_raw,
        ltype=ltype,
        log_level=log_level,
        xuehai_id=xuehai_id,
    )


def _filter(
    query: str,
    performer: str,
    types: list[str] | None,
    levels: list[str] | None,
    time_start: int | None,
    time_end: int | None,
    xuehai_id: str = "",
) -> list[Any]:
    """Admin-browser ``$match`` (``logs_router``): escaped url/data contains etc."""
    conds: list[Any] = []
    if query:
        pattern = re.escape(query)
        conds.append(
            or_(
                func.regexp_like(app_logs_tbl.c.url, pattern),
                func.regexp_like(app_logs_tbl.c.data, pattern),
            )
        )
    if performer:
        conds.append(app_logs_tbl.c.app_user == performer)
    if types:
        conds.append(app_logs_tbl.c.ltype.in_(list(types)))
    if levels:
        conds.append(app_logs_tbl.c.log_level.in_(list(levels)))
    if time_start is not None:
        conds.append(app_logs_tbl.c.ts_raw > time_start)
    if time_end is not None:
        conds.append(app_logs_tbl.c.ts_raw < time_end)
    if xuehai_id:
        conds.append(app_logs_tbl.c.xuehai_id == xuehai_id)
    return conds


def _list_stmt(
    query: str,
    performer: str,
    types: list[str] | None,
    levels: list[str] | None,
    time_start: int | None,
    time_end: int | None,
    page: int,
    size: int,
    xuehai_id: str = "",
):
    stmt = select(*_log_columns())
    conds = _filter(query, performer, types, levels, time_start, time_end, xuehai_id)
    if conds:
        stmt = stmt.where(*conds)
    return (
        stmt.order_by(app_logs_tbl.c.ts_raw.desc())
        .offset(0 if page == -1 else (page - 1) * size)
        .limit(size)
    )


def _count_stmt(
    query: str,
    performer: str,
    types: list[str] | None,
    levels: list[str] | None,
    time_start: int | None,
    time_end: int | None,
    xuehai_id: str = "",
):
    stmt = select(func.count()).select_from(app_logs_tbl)
    conds = _filter(query, performer, types, levels, time_start, time_end, xuehai_id)
    if conds:
        stmt = stmt.where(*conds)
    return stmt


def _for_user_filter(user_oid: str, query: str) -> list[Any]:
    """Per-user ``$match`` (``users_router``): raw (unescaped) url/data contains."""
    conds: list[Any] = [app_logs_tbl.c.app_user == user_oid]
    if query:
        conds.append(
            or_(
                func.regexp_like(app_logs_tbl.c.url, query),
                func.regexp_like(app_logs_tbl.c.data, query),
            )
        )
    return conds


def _list_for_user_stmt(user_oid: str, query: str, page: int, size: int):
    stmt = select(*_log_columns()).where(*_for_user_filter(user_oid, query))
    return (
        stmt.order_by(app_logs_tbl.c.ts_raw.desc())
        .offset(0 if page == -1 else (page - 1) * size)
        .limit(size)
    )


def _count_for_user_stmt(user_oid: str, query: str):
    return (
        select(func.count())
        .select_from(app_logs_tbl)
        .where(*_for_user_filter(user_oid, query))
    )


def _log_row(mapping: Any) -> Row:
    """Mongo document shape; ``timestamp`` is the preserved raw float."""
    ts_raw = mapping["ts_raw"]
    if ts_raw is None and mapping["ts_at"] is not None:
        ts_raw = mapping["ts_at"].timestamp()
    return {
        "_id": mapping["oid"],
        "url": mapping["url"],
        "user": mapping["app_user"],
        "data": mapping["data"],
        "timestamp": ts_raw,
        "type": mapping["ltype"],
        "level": mapping["log_level"],
        "xuehai_id": mapping["xuehai_id"],
    }


class OracleAppLogsRepo:
    """``AppLogsRepo`` over Oracle ADB (``zvms_new.app_logs``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def insert(
        self,
        url: str,
        user: str,
        data: str,
        timestamp: float,
        ltype: str,
        level: str,
        xuehai_id: str,
    ) -> None:
        ts_raw = float(timestamp) if timestamp is not None else None
        ts_at = datetime.fromtimestamp(ts_raw) if ts_raw is not None else None
        stmt = _insert_stmt(
            new_oid(), url, user, data, ts_at, ts_raw, ltype, level, xuehai_id
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def list_page(
        self,
        query: str = "",
        performer: str = "",
        types: list[str] | None = None,
        levels: list[str] | None = None,
        time_start: int | None = None,
        time_end: int | None = None,
        xuehai_id: str = "",
        page: int = -1,
        size: int = 10,
    ) -> tuple[list[Row], int]:
        page_stmt = _list_stmt(
            query, performer, types, levels, time_start, time_end, page, size, xuehai_id
        )
        count_stmt = _count_stmt(
            query, performer, types, levels, time_start, time_end, xuehai_id
        )
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(count_stmt)).scalar_one()
            rows = (await session.execute(page_stmt)).all()
        return [_log_row(row._mapping) for row in rows], int(total)

    async def list_for_user(
        self, user_oid: str, query: str = "", page: int = -1, size: int = 10
    ) -> tuple[list[Row], int]:
        page_stmt = _list_for_user_stmt(user_oid, query, page, size)
        count_stmt = _count_for_user_stmt(user_oid, query)
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(count_stmt)).scalar_one()
            rows = (await session.execute(page_stmt)).all()
        return [_log_row(row._mapping) for row in rows], int(total)


__all__ = ["OracleAppLogsRepo"]
