"""Oracle implementation of :class:`db.repos.protocols.ActivitiesRepo`.

SQLAlchemy 2.0 async Core over the ``ACTIVITIES`` table (``zvms_new.activities``).
Column names follow the rename map in ``docs/ADB_SCHEMA_SPEC.md`` §3
(``_id``→``OID``, ``type``→``ATYPE``, ``date``→``ACTIVITY_DATE``,
``creator``→``CREATOR_OID``, ``appointee``→``APPOINTEE_OID``,
``createdAt``/``updatedAt``→``CREATED_AT``/``UPDATED_AT``); returned rows are
translated back to the Mongo document shape.

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

import re
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import activities as activities_tbl
from db.models import users as users_tbl
from db.oracle.session import session_scope
from db.repos.pagination import page_args

Row = dict[str, Any]

# Oracle refuses more than 1000 elements in a single ``IN`` (ORA-01795).
_OID_CHUNK = 1000

# Mongo field name → Oracle column attribute name (spec §3).
_FIELD_COLUMNS: dict[str, str] = {
    "name": "name",
    "description": "description",
    "type": "atype",
    "creator": "creator_oid",
    "status": "status",
    "date": "activity_date",
    "createdAt": "created_at",
    "updatedAt": "updated_at",
    "appointee": "appointee_oid",
    "approver": "approver",
    "place": "place",
    "origin": "origin",
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


def _activity_columns() -> tuple[Any, ...]:
    """The full ACTIVITIES projection shared by get/list_page."""
    return (
        activities_tbl.c.oid,
        activities_tbl.c.name,
        activities_tbl.c.description,
        activities_tbl.c.atype,
        activities_tbl.c.creator_oid,
        activities_tbl.c.status,
        activities_tbl.c.activity_date,
        activities_tbl.c.created_at,
        activities_tbl.c.updated_at,
        activities_tbl.c.appointee_oid,
        activities_tbl.c.approver,
        activities_tbl.c.place,
        activities_tbl.c.origin,
    )


def _chunked_in(column: Any, values: list[str]) -> Any:
    """``IN`` 超过 1000 项时改写成 ``OR c IN (...) OR c IN (...)``。

    与 ``db/repos/oracle/members.py::_chunked_in`` 同一手法：单条语句里
    Oracle 只看到一串过滤条件，``ORDER BY``/分页/``COUNT`` 都还留在 SQL 侧；
    拆成多条语句执行会把排序分页按块各做一遍，语义就变了。两处各留一份是
    为了避免 members/activities 之间的循环 import。
    """
    chunks = [
        values[start : start + _OID_CHUNK]
        for start in range(0, len(values), _OID_CHUNK)
    ]
    if not chunks:
        # 空集合维持原语义：匹配不到任何行（而非「不过滤」）。
        return column.in_([])
    return or_(*(column.in_(chunk) for chunk in chunks))


def _activity_filter(query: str, atypes: list[str] | None, status: str | None) -> list[Any]:
    """Replicate the Mongo ``$match`` shared by the page/count queries.

    * non-empty ``query`` → case-insensitive ``contains`` on ``name`` only,
      with the keyword ``re.escape``d exactly like the current pipelines.
    * non-empty ``atypes`` → ``type IN (...)`` (an OR-list).
    * truthy ``status`` → equality filter (empty/``None`` = no filter).
    """
    conds: list[Any] = []
    if query:
        conds.append(
            func.regexp_like(activities_tbl.c.name, re.escape(query), "i")
        )
    if atypes:
        conds.append(activities_tbl.c.atype.in_(list(atypes)))
    if status:
        conds.append(activities_tbl.c.status == status)
    return conds


def _page_stmt(
    query: str,
    atypes: list[str] | None,
    status: str | None,
    page: int,
    size: int,
    sort: str | None = None,
    asc: bool = False,
):
    """``list_page`` select: creator name joined, total via ``COUNT(*) OVER()``.

    The LEFT JOIN resolves ``creator_name`` (a missing creator keeps the row —
    the ``USERS`` side is the optional one); the analytic total is evaluated
    over the filtered set, before the row limit.

    ``sort`` is an already-whitelisted column name (``None`` keeps the default
    ``oid DESC``); ``asc`` only applies when ``sort`` is given.
    """
    stmt = (
        select(
            *_activity_columns(),
            users_tbl.c.name.label("creator_name"),
            func.count().over().label("total_count"),
        )
        .select_from(
            activities_tbl.outerjoin(
                users_tbl, activities_tbl.c.creator_oid == users_tbl.c.oid
            )
        )
    )
    conds = _activity_filter(query, atypes, status)
    if conds:
        stmt = stmt.where(*conds)
    offset, limit = page_args(page, size)
    if sort:
        column = getattr(activities_tbl.c, sort)
        order = column.asc() if asc else column.desc()
    else:
        order = activities_tbl.c.oid.desc()
    return stmt.order_by(order).offset(offset).limit(limit)


def _count_stmt(query: str, atypes: list[str] | None, status: str | None):
    """``list_page`` count: same filter, no pagination."""
    stmt = select(func.count()).select_from(activities_tbl)
    conds = _activity_filter(query, atypes, status)
    if conds:
        stmt = stmt.where(*conds)
    return stmt


def _get_stmt(oid: str):
    return select(*_activity_columns()).where(activities_tbl.c.oid == oid)


def _insert_stmt(
    oid: str,
    name: str,
    description: str,
    atype: str,
    creator_oid: str,
    appointee_oid: str,
    origin: str,
    activity_date: Any,
    created_at: datetime,
    updated_at: datetime,
):
    return insert(activities_tbl).values(
        oid=oid,
        name=name,
        description=description,
        atype=atype,
        creator_oid=creator_oid,
        status="pending",
        activity_date=activity_date,
        created_at=created_at,
        updated_at=updated_at,
        appointee_oid=appointee_oid,
        approver="",
        place="",
        origin=origin,
    )


def _update_stmt(oid: str, values: Row):
    return update(activities_tbl).where(activities_tbl.c.oid == oid).values(**values)


def _cas_status_stmt(oid: str, expected: str, values: Row):
    """审核状态迁移的 CAS 写：``WHERE oid = :id AND status = :expected``。

    审核接口先读再判（pending → effective/refused），并发下两个审核人会都
    通过判断，后写的覆盖先写的并各自重算/写审计。把期望状态压进 ``WHERE``，
    只有一个事务能命中行；``rowcount == 0`` 由调用方转 409。
    """
    return (
        update(activities_tbl)
        .where(
            activities_tbl.c.oid == oid,
            activities_tbl.c.status == expected,
        )
        .values(**values)
    )


def _delete_stmt(oid: str):
    return delete(activities_tbl).where(activities_tbl.c.oid == oid)


def _list_effective_stmt(date_start: Any, date_end: Any):
    stmt = select(*_activity_columns()).where(activities_tbl.c.status == "effective")
    if date_start is not None and date_end is not None:
        stmt = stmt.where(
            activities_tbl.c.activity_date >= date_start,
            activities_tbl.c.activity_date <= date_end,
        )
    return stmt


def _list_by_oids_stmt(oids: list[str], query: str, page: int, size: int):
    stmt = select(*_activity_columns()).where(
        _chunked_in(activities_tbl.c.oid, oids)
    )
    if query:
        stmt = stmt.where(func.regexp_like(activities_tbl.c.name, query, "i"))
    stmt = stmt.order_by(activities_tbl.c.oid.desc())
    if page != -1:
        offset, limit = page_args(page, size)
        stmt = stmt.offset(offset).limit(limit)
    return stmt


def _count_by_oids_stmt(oids: list[str], query: str):
    stmt = select(func.count()).select_from(activities_tbl).where(
        _chunked_in(activities_tbl.c.oid, oids)
    )
    if query:
        stmt = stmt.where(func.regexp_like(activities_tbl.c.name, query, "i"))
    return stmt


# ``list_raw`` accepts an arbitrary Mongo sort key; map the known ones to columns.
_RAW_SORT_COLUMNS = {**_FIELD_COLUMNS, "_id": "oid"}


def _list_raw_stmt(
    search: str,
    atypes: list[str] | None,
    status: str | None,
    sort: str | None,
    asc: bool,
    page: int | None,
    size: int,
):
    stmt = select(*_activity_columns())
    conds = _activity_filter(search, atypes, status)
    if conds:
        stmt = stmt.where(*conds)
    if sort:
        column = getattr(activities_tbl.c, _RAW_SORT_COLUMNS.get(sort, "oid"))
        stmt = stmt.order_by(column.asc() if asc else column.desc())
    if page is not None:
        offset, limit = page_args(page, size)
        stmt = stmt.offset(offset).limit(limit)
    return stmt


def _types_summary_stmt():
    return (
        select(
            activities_tbl.c.atype,
            activities_tbl.c.status,
            func.count().label("count"),
        )
        .group_by(activities_tbl.c.atype, activities_tbl.c.status)
    )


def _total_of(rows: Any) -> int | None:
    """The ``COUNT(*) OVER()`` value carried by a merged count+page result.

    ``None`` for an empty page: the window count is not observable then, and the
    caller decides whether the page is simply empty (``page == 1`` → ``0``) or
    out of range (a dedicated count statement is issued).
    """
    if not rows:
        return None
    return int(rows[0]._mapping["total_count"])


def _activity_doc(mapping: Any) -> Row:
    """Canonical activity row (matches ``mongo/activities._activity_doc``)."""
    return {
        "_id": mapping["oid"],
        "name": mapping["name"],
        "description": mapping["description"] or "",
        "type": mapping["atype"],
        "creator": mapping["creator_oid"],
        "status": mapping["status"],
        "date": mapping["activity_date"],
        "createdAt": mapping["created_at"],
        "updatedAt": mapping["updated_at"],
        "appointee": mapping["appointee_oid"],
        "approver": mapping["approver"] or "",
        "place": mapping["place"] or "",
        "origin": mapping["origin"],
    }


class OracleActivitiesRepo:
    """``ActivitiesRepo`` over Oracle ADB (``zvms_new.activities``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def get(self, oid: str) -> Row | None:
        stmt = _get_stmt(normalize_oid(oid))
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return _activity_doc(row._mapping)

    async def get_for_update(self, oid: str) -> Row | None:
        """Serialize member changes until the caller commits its transaction."""
        if self._session is None:
            raise RuntimeError("get_for_update requires a caller-owned transaction")
        statement = _get_stmt(normalize_oid(oid)).with_for_update()
        row = (await self._session.execute(statement)).first()
        return _activity_doc(row._mapping) if row is not None else None

    async def list_page(
        self,
        query: str,
        atypes: list[str] | None,
        status: str | None,
        page: int,
        size: int,
        sort: str | None = None,
        asc: bool = False,
    ) -> tuple[list[Row], int]:
        stmt = _page_stmt(query, atypes, status, page, size, sort, asc)
        count_stmt = _count_stmt(query, atypes, status)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
            total = _total_of(rows)
            if total is None:
                total = (
                    0
                    if page == 1
                    else int((await session.execute(count_stmt)).scalar_one())
                )
        return [
            {
                **_activity_doc(row._mapping),
                "creator_name": row._mapping["creator_name"] or "未知用户",
            }
            for row in rows
        ], total

    async def create(
        self,
        name: str,
        description: str,
        atype: str,
        creator_oid: str,
        appointee_oid: str,
        origin: str,
        activity_date: Any,
    ) -> str:
        oid = new_oid()
        now = datetime.now()
        stmt = _insert_stmt(
            oid,
            name,
            description,
            atype,
            normalize_oid(creator_oid),
            normalize_oid(appointee_oid),
            origin,
            activity_date,
            now,
            now,
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return oid

    async def update(self, oid: str, fields: Row) -> None:
        values = {
            _FIELD_COLUMNS[key]: value
            for key, value in fields.items()
            if key in _FIELD_COLUMNS
        }
        if not values:
            return
        stmt = _update_stmt(normalize_oid(oid), values)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def delete(self, oid: str) -> None:
        stmt = _delete_stmt(normalize_oid(oid))
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def cas_status(self, oid: str, expected: str, fields: Row) -> bool:
        """仅当状态仍是 ``expected`` 时应用 ``fields``；返回是否命中行。

        ``fields`` 走与 :meth:`update` 相同的白名单映射，因此调用方传
        ``{"status", "approver", "updatedAt"}`` 的语义完全一致，只是多了一个
        ``status = expected`` 的并发守卫。
        """
        values = {
            _FIELD_COLUMNS[key]: value
            for key, value in fields.items()
            if key in _FIELD_COLUMNS
        }
        if not values:
            return False
        stmt = _cas_status_stmt(normalize_oid(oid), expected, values)
        async with _session_for(self._session) as (session, owns):
            changed = (await session.execute(stmt)).rowcount > 0
            if owns:
                await session.commit()
        return changed

    async def list_effective(
        self, date_start: Any = None, date_end: Any = None
    ) -> list[Row]:
        stmt = _list_effective_stmt(date_start, date_end)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_activity_doc(row._mapping) for row in rows]

    async def list_by_oids(
        self, oids: list[str], query: str, page: int = -1, size: int = 10
    ) -> tuple[list[Row], int]:
        normalized = [normalize_oid(oid) for oid in oids]
        page_stmt = _list_by_oids_stmt(normalized, query, page, size)
        count_stmt = _count_by_oids_stmt(normalized, query)
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(count_stmt)).scalar_one()
            rows = (await session.execute(page_stmt)).all()
        return [_activity_doc(row._mapping) for row in rows], int(total)

    async def list_raw(
        self,
        search: str = "",
        atypes: list[str] | None = None,
        status: str | None = None,
        sort: str | None = None,
        asc: bool = False,
        page: int | None = None,
        size: int = 10,
    ) -> tuple[list[Row], int]:
        page_stmt = _list_raw_stmt(search, atypes, status, sort, asc, page, size)
        count_stmt = _count_stmt(search, atypes, status)
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(count_stmt)).scalar_one()
            rows = (await session.execute(page_stmt)).all()
        return [_activity_doc(row._mapping) for row in rows], int(total)

    async def types_summary(self) -> list[Row]:
        stmt = _types_summary_stmt()
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()

        summary: dict[Any, Row] = {}
        order: list[Any] = []
        for row in rows:
            mapping = row._mapping
            atype = mapping["atype"]
            status = mapping["status"]
            count = int(mapping["count"])
            if atype not in summary:
                summary[atype] = {"type": atype, "count": 0, "status_breakdown": {}}
                order.append(atype)
            summary[atype]["count"] += count
            if status is not None:
                breakdown = summary[atype]["status_breakdown"]
                breakdown[status] = breakdown.get(status, 0) + count
        return [summary[atype] for atype in order]


__all__ = ["OracleActivitiesRepo"]
