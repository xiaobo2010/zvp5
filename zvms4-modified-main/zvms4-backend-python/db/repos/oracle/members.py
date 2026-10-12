"""Oracle implementation of :class:`db.repos.protocols.MembersRepo`.

SQLAlchemy 2.0 async Core over the ``ACTIVITY_MEMBERS`` table
(``zvms_new.activity_members``). Column names follow the rename map in
``docs/ADB_SCHEMA_SPEC.md`` §3 (``activity``→``ACTIVITY_OID``,
``member``→``MEMBER_OID``, ``status``→``MEMBER_STATUS``, ``mode``→``MEMBER_MODE``,
``duration``→``DURATION_HOURS``); returned rows are translated back to the
Mongo document shape.

The two list queries reproduce the Mongo pipelines of
:mod:`db.repos.mongo.members`:

* ``list_for_activity`` — the ``activities_v3`` aggregation: filter by
  ``ACTIVITY_OID``, optionally keep only members whose user name matches the
  ``query`` (``REGEXP_LIKE`` with the keyword ``re.escape``d, LEFT JOIN to
  ``USERS`` exactly like the ``$lookup`` + ``member_obj != null`` match), ordered
  by ``OID``.
* ``list_for_user`` — the ``users_v2`` per-user pipeline: filter by
  ``MEMBER_OID`` (+ optional ``MEMBER_STATUS``), JOIN to ``ACTIVITIES`` to
  mirror the non-preserving ``$unwind``, ordered by ``OID``.

Session ownership mirrors :mod:`db.repos.oracle.users`: with no injected session
each method opens its own through :func:`db.oracle.session.session_scope`, and
**writes commit once** (one write = one transaction); an injected session is
neither committed nor closed (the caller owns the unit of work). Nothing here
opens a connection at import time.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import activities as activities_tbl
from db.models import activity_members as members_tbl
from db.models import users as users_tbl
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


def _member_columns() -> tuple[Any, ...]:
    """The ACTIVITY_MEMBERS projection returned by both list queries."""
    return (
        members_tbl.c.oid,
        members_tbl.c.activity_oid,
        members_tbl.c.member_oid,
        members_tbl.c.member_status,
        members_tbl.c.member_mode,
        members_tbl.c.duration_hours,
    )


def _list_for_activity_stmt(
    activity_oid: str, query: str, page: int, size: int
):
    """``list_for_activity`` select: members of an activity, name-filtered."""
    stmt = select(*_member_columns()).select_from(
        members_tbl.outerjoin(
            users_tbl, members_tbl.c.member_oid == users_tbl.c.oid
        )
    ).where(members_tbl.c.activity_oid == activity_oid)
    if query:
        stmt = stmt.where(
            func.regexp_like(users_tbl.c.name, re.escape(query), "i")
        )
    return (
        stmt.order_by(members_tbl.c.oid.asc())
        .offset((page - 1) * size)
        .limit(size)
    )


def _list_for_activity_count_stmt(activity_oid: str, query: str):
    """``list_for_activity`` count: same activity/name filter, no pagination."""
    stmt = select(func.count()).select_from(
        members_tbl.outerjoin(
            users_tbl, members_tbl.c.member_oid == users_tbl.c.oid
        )
    ).where(members_tbl.c.activity_oid == activity_oid)
    if query:
        stmt = stmt.where(
            func.regexp_like(users_tbl.c.name, re.escape(query), "i")
        )
    return stmt


def _list_for_user_stmt(user_oid: str, status: str | None, page: int, size: int):
    """``list_for_user`` select: a user's memberships, joined to activities."""
    stmt = select(*_member_columns()).select_from(
        members_tbl.join(
            activities_tbl, members_tbl.c.activity_oid == activities_tbl.c.oid
        )
    ).where(members_tbl.c.member_oid == user_oid)
    if status:
        stmt = stmt.where(members_tbl.c.member_status == status)
    return (
        stmt.order_by(members_tbl.c.oid.asc())
        .offset((page - 1) * size)
        .limit(size)
    )


def _list_for_user_count_stmt(user_oid: str, status: str | None):
    """``list_for_user`` count: member (+ status) filter, like ``users_v2``."""
    stmt = select(func.count()).select_from(members_tbl).where(
        members_tbl.c.member_oid == user_oid
    )
    if status:
        stmt = stmt.where(members_tbl.c.member_status == status)
    return stmt


def _insert_member_stmt(
    oid: str,
    activity_oid: str,
    member_oid: str,
    member_mode: str,
    duration: float,
    member_status: str,
):
    return insert(members_tbl).values(
        oid=oid,
        activity_oid=activity_oid,
        member_oid=member_oid,
        member_mode=member_mode,
        duration_hours=duration,
        member_status=member_status,
    )


def _delete_member_stmt(oid: str):
    return delete(members_tbl).where(members_tbl.c.oid == oid)


def _set_status_stmt(oid: str, status: str):
    return (
        update(members_tbl)
        .where(members_tbl.c.oid == oid)
        .values(member_status=status)
    )


def _set_mode_stmt(oid: str, mode: str):
    return (
        update(members_tbl)
        .where(members_tbl.c.oid == oid)
        .values(member_mode=mode)
    )


def _member_row(mapping: Any) -> Row:
    """Translate an ACTIVITY_MEMBERS row to the Mongo document shape."""
    return {
        "_id": mapping["oid"],
        "activity": mapping["activity_oid"],
        "member": mapping["member_oid"],
        "status": mapping["member_status"],
        "mode": mapping["member_mode"],
        "duration": mapping["duration_hours"],
    }


class OracleMembersRepo:
    """``MembersRepo`` over Oracle ADB (``zvms_new.activity_members``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def list_for_activity(
        self, activity_oid: str, query: str, page: int, size: int
    ) -> tuple[list[Row], int]:
        activity = normalize_oid(activity_oid)
        page_stmt = _list_for_activity_stmt(activity, query, page, size)
        count_stmt = _list_for_activity_count_stmt(activity, query)
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(count_stmt)).scalar_one()
            rows = (await session.execute(page_stmt)).all()
        return [_member_row(row._mapping) for row in rows], int(total)

    async def list_for_user(
        self, user_oid: str, status: str | None, page: int, size: int
    ) -> tuple[list[Row], int]:
        member = normalize_oid(user_oid)
        page_stmt = _list_for_user_stmt(member, status, page, size)
        count_stmt = _list_for_user_count_stmt(member, status)
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(count_stmt)).scalar_one()
            rows = (await session.execute(page_stmt)).all()
        return [_member_row(row._mapping) for row in rows], int(total)

    async def add(
        self,
        activity_oid: str,
        member_oid: str,
        mode: str,
        duration: float,
        status: str,
    ) -> str:
        oid = new_oid()
        stmt = _insert_member_stmt(
            oid,
            normalize_oid(activity_oid),
            normalize_oid(member_oid),
            mode,
            duration,
            status,
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return oid

    async def remove(self, oid: str) -> None:
        stmt = _delete_member_stmt(normalize_oid(oid))
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def set_status(self, oid: str, status: str) -> None:
        stmt = _set_status_stmt(normalize_oid(oid), status)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def set_mode(self, oid: str, mode: str) -> None:
        stmt = _set_mode_stmt(normalize_oid(oid), mode)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()


__all__ = ["OracleMembersRepo"]
