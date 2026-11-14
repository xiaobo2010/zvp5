"""Oracle implementation of :class:`db.repos.protocols.MembersRepo`.

SQLAlchemy 2.0 async Core over the ``ACTIVITY_MEMBERS`` table
(``zvms_new.activity_members``). Column names follow the rename map in
``docs/ADB_SCHEMA_SPEC.md`` §3 (``activity``→``ACTIVITY_OID``,
``member``→``MEMBER_OID``, ``status``→``MEMBER_STATUS``, ``mode``→``MEMBER_MODE``,
``duration``→``DURATION_HOURS``); returned rows are translated back to the
Mongo document shape.

The two list queries reproduce the original ``zvms_new.activity_members``
pipelines:

* ``list_for_activity`` — the ``activities_v3`` aggregation: filter by
  ``ACTIVITY_OID``, optionally keep only members whose user name matches the
  ``query`` (``REGEXP_LIKE`` with the keyword ``re.escape``d, LEFT JOIN to
  ``USERS`` exactly like the ``$lookup`` + ``member_obj != null`` match), ordered
  by ``OID``.
* ``list_for_user`` — the ``users_v2`` per-user pipeline: filter by
  ``MEMBER_OID`` (+ optional ``MEMBER_STATUS``), JOIN to ``ACTIVITIES`` to
  mirror the non-preserving ``$unwind``, ordered by ``OID``.
* ``list_for_user_activities`` — the ``users_v2`` per-user activity page in a
  single statement: ``ACTIVITY_MEMBERS`` LEFT JOIN ``ACTIVITIES`` (a missing
  activity is reported as ``None`` while still counting towards the total),
  optional keyword predicate, ``COUNT(*) OVER ()`` for the total.

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

from sqlalchemy import delete, exists, func, insert, literal_column, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from db.ids import new_oid, normalize_oid
from db.models import activities as activities_tbl
from db.models import activity_members as members_tbl
from db.models import users as users_tbl
from db.oracle.session import session_scope
from db.repos.oracle.activities import _activity_doc

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


_ORACLE_REGEX_METACHARS = "\\^$.*+?()[]{}|"

# Oracle refuses more than 1000 elements in a single ``IN`` (ORA-01795).
_OID_CHUNK = 1000


def escape_oracle_regex(value: str) -> str:
    """Escape the characters ``REGEXP_LIKE`` reads as operators."""
    return "".join(
        "\\" + char if char in _ORACLE_REGEX_METACHARS else char for char in value
    )


def _chunked_in(column: Any, values: list[str]) -> Any:
    """同一列上的 ``IN`` 超过 1000 项时改写成 ``OR c IN (...) OR c IN (...)``。

    为什么不是「分块执行、结果在 Python 里拼」：页面/计数语句带 ``ORDER BY``
    与 ``OFFSET/LIMIT``，拆成多条语句会让排序与分页在**每块内部**各来一次，
    对外语义直接变形。OR 起来的同一条语句里 Oracle 只看到一个过滤条件，
    排序、分页、``COUNT(*) OVER()`` 全都还在 SQL 侧，与不分块时一致。
    """
    chunks = [
        values[start : start + _OID_CHUNK]
        for start in range(0, len(values), _OID_CHUNK)
    ]
    if not chunks:
        # 空集合维持原语义：匹配不到任何行（而非「不过滤」）。
        return column.in_([])
    return or_(*(column.in_(chunk) for chunk in chunks))


_JOINED_ACTIVITY_COLUMNS = (
    ("oid", activities_tbl.c.oid),
    ("name", activities_tbl.c.name),
    ("description", activities_tbl.c.description),
    ("atype", activities_tbl.c.atype),
    ("creator_oid", activities_tbl.c.creator_oid),
    ("status", activities_tbl.c.status),
    ("activity_date", activities_tbl.c.activity_date),
    ("created_at", activities_tbl.c.created_at),
    ("updated_at", activities_tbl.c.updated_at),
    ("appointee_oid", activities_tbl.c.appointee_oid),
    ("approver", activities_tbl.c.approver),
    ("place", activities_tbl.c.place),
    ("origin", activities_tbl.c.origin),
)

_JOINED_ACTIVITY_PREFIX = "a_"


def _joined_activity_columns() -> tuple[Any, ...]:
    """The ACTIVITIES projection of the members/activities join, prefixed."""
    return tuple(
        column.label(_JOINED_ACTIVITY_PREFIX + name)
        for name, column in _JOINED_ACTIVITY_COLUMNS
    )


def _joined_activity_row(mapping: Any) -> Row | None:
    """The joined activity document, or ``None`` when the activity is gone."""
    if mapping[_JOINED_ACTIVITY_PREFIX + "oid"] is None:
        return None
    return _activity_doc(
        {
            name: mapping[_JOINED_ACTIVITY_PREFIX + name]
            for name, _column in _JOINED_ACTIVITY_COLUMNS
        }
    )


def _user_activity_search_filter(pattern: str) -> Any:
    """The keyword predicate of the per-user activity page.

    Mirrors the current handler: an activity matches on ``name``/``description``
    (case-insensitive), and when **no** activity in the table matches the whole
    filter degenerates to "no filter" — the empty activity-id list is treated as
    "do not filter" downstream. The ``NOT EXISTS`` probe is uncorrelated, so
    Oracle evaluates it once.
    """
    probe = aliased(activities_tbl)
    return or_(
        or_(
            func.regexp_like(activities_tbl.c.name, pattern, "i"),
            func.regexp_like(activities_tbl.c.description, pattern, "i"),
        ),
        ~exists(
            select(1)
            .select_from(probe)
            .where(
                or_(
                    func.regexp_like(probe.c.name, pattern, "i"),
                    func.regexp_like(probe.c.description, pattern, "i"),
                )
            )
        ),
    )


def _list_for_user_activities_query(user_oid: str, pattern: str):
    """The ``FROM``/``WHERE`` shared by the page and count statements."""
    stmt = (
        select(*_member_columns())
        .select_from(
            members_tbl.outerjoin(
                activities_tbl, members_tbl.c.activity_oid == activities_tbl.c.oid
            )
        )
        .where(members_tbl.c.member_oid == user_oid)
    )
    if pattern:
        stmt = stmt.where(_user_activity_search_filter(pattern))
    return stmt


def _list_for_user_activities_stmt(
    user_oid: str,
    pattern: str,
    sort: str,
    asc: bool,
    page: int,
    size: int,
):
    """The per-user activity page: one statement, total via ``COUNT(*) OVER()``.

    Ties follow ``ACTIVITY_MEMBERS.ROWID`` — the physical order the original
    unpaginated ``WHERE member_oid = ...`` scan returned, which the handler's
    stable ``list.sort`` preserved (golden ``01f`` shows ties are **not** in
    ``_id`` order).
    """
    stmt = _list_for_user_activities_query(user_oid, pattern).add_columns(
        *_joined_activity_columns(),
        func.count().over().label("total_count"),
    )
    column = getattr(members_tbl.c, _RAW_SORT_COLUMNS.get(sort, "oid"))
    return (
        stmt.order_by(
            column.asc() if asc else column.desc(),
            literal_column("activity_members.rowid").asc(),
        )
        .offset((page - 1) * size)
        .limit(size)
    )


def _list_for_user_activities_count_stmt(user_oid: str, pattern: str):
    """The count companion of :func:`_list_for_user_activities_stmt`.

    Built from the very same query, so the predicates (including the
    no-hit-means-no-filter probe) cannot drift apart.
    """
    return select(func.count()).select_from(
        _list_for_user_activities_query(user_oid, pattern).subquery()
    )


def _list_for_activity_stmt(
    activity_oid: str, query: str, page: int, size: int
):
    """``list_for_activity`` select: members of an activity, name-filtered.

    The member name comes from the LEFT JOIN and the total from
    ``COUNT(*) OVER()`` (evaluated over the filtered set, before the row limit).
    """
    stmt = (
        select(
            *_member_columns(),
            users_tbl.c.name.label("member_name"),
            func.count().over().label("total_count"),
        )
        .select_from(
            members_tbl.outerjoin(
                users_tbl, members_tbl.c.member_oid == users_tbl.c.oid
            )
        )
        .where(members_tbl.c.activity_oid == activity_oid)
    )
    if query:
        stmt = stmt.where(
            or_(
                func.regexp_like(users_tbl.c.name, re.escape(query), "i"),
                func.regexp_like(users_tbl.c.business_id, re.escape(query), "i"),
            )
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
            or_(
                func.regexp_like(users_tbl.c.name, re.escape(query), "i"),
                func.regexp_like(users_tbl.c.business_id, re.escape(query), "i"),
            )
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


def _cas_status_stmt(oid: str, expected: str, status: str):
    """状态迁移的 CAS 写：``WHERE oid = :id AND status = :expected``。

    审核是「先读再判再写」，两个管理员同时点会都通过读后的判断，后写的
    悄悄覆盖先写的（并各自触发一次重算/审计）。把期望状态写进 ``WHERE``
    后，只有一个事务能改到行，另一个 ``rowcount == 0``，由调用方回 409。
    """
    return (
        update(members_tbl)
        .where(
            members_tbl.c.oid == oid,
            members_tbl.c.member_status == expected,
        )
        .values(member_status=status)
    )


def _set_mode_stmt(oid: str, mode: str):
    return (
        update(members_tbl)
        .where(members_tbl.c.oid == oid)
        .values(member_mode=mode)
    )


# Mongo field name → Oracle column attribute name (spec §3).
_FIELD_COLUMNS: dict[str, str] = {
    "status": "member_status",
    "mode": "member_mode",
    "duration": "duration_hours",
}


def _get_by_oid_stmt(oid: str):
    return select(*_member_columns()).where(members_tbl.c.oid == oid)


def _get_by_triple_stmt(activity_oid: str, member_oid: str, mode: str):
    return select(*_member_columns()).where(
        members_tbl.c.member_oid == member_oid,
        members_tbl.c.activity_oid == activity_oid,
        members_tbl.c.member_mode == mode,
    )


def _list_by_activity_stmt(activity_oid: str, status: str | None = None):
    stmt = select(*_member_columns()).where(
        members_tbl.c.activity_oid == activity_oid
    )
    if status is not None:
        stmt = stmt.where(members_tbl.c.member_status == status)
    return stmt


def _list_for_member_stmt(
    member_oid: str, status: str | None, activity_oids: list[str] | None
):
    stmt = select(*_member_columns()).where(members_tbl.c.member_oid == member_oid)
    if status:
        stmt = stmt.where(members_tbl.c.member_status == status)
    if activity_oids is not None:
        # 调用方（util/calculate.py）把「全部有效活动」一次传进来，超过 1000
        # 就是 ORA-01795；这里用 OR-of-INs 保住单条语句。
        stmt = stmt.where(_chunked_in(members_tbl.c.activity_oid, activity_oids))
    return stmt


def _list_by_members_stmt(member_oids: list[str]):
    return select(*_member_columns()).where(
        _chunked_in(members_tbl.c.member_oid, member_oids)
    )


def _list_by_activity_and_members_stmt(
    activity_oid: str, member_oids: list[str]
):
    return select(*_member_columns()).where(
        members_tbl.c.activity_oid == activity_oid,
        _chunked_in(members_tbl.c.member_oid, member_oids),
    )


def _count_for_activity_stmt(
    activity_oid: str, status: str | None, member_oids: list[str] | None
):
    stmt = select(func.count()).select_from(members_tbl).where(
        members_tbl.c.activity_oid == activity_oid
    )
    if status:
        stmt = stmt.where(members_tbl.c.member_status == status)
    if member_oids is not None:
        stmt = stmt.where(_chunked_in(members_tbl.c.member_oid, member_oids))
    return stmt


def _count_distinct_members_stmt(activity_oid: str):
    """一个活动里**不同的人**有几个（同一个人可有多条 mode 行）。

    即时重算的闸门必须按人为单位：按行数判断会把「1 个人 3 条记录」当成
    3 个成员，也会让 501 行但其实只有 200 人的活动被无谓地推给每日全量。
    """
    return (
        select(func.count(func.distinct(members_tbl.c.member_oid)))
        .select_from(members_tbl)
        .where(members_tbl.c.activity_oid == activity_oid)
    )


def _remove_for_activity_stmt(activity_oid: str):
    return delete(members_tbl).where(members_tbl.c.activity_oid == activity_oid)


def _update_fields_stmt(oid: str, values: Row):
    return update(members_tbl).where(members_tbl.c.oid == oid).values(**values)


def _set_duration_for_member_stmt(activity_oid: str, member_oid: str, duration: float):
    return (
        update(members_tbl)
        .where(
            members_tbl.c.activity_oid == activity_oid,
            members_tbl.c.member_oid == member_oid,
        )
        .values(duration_hours=duration)
    )


_RAW_SORT_COLUMNS = {
    **_FIELD_COLUMNS,
    "_id": "oid",
    "activity": "activity_oid",
    "member": "member_oid",
}


def _list_raw_stmt(
    activity_oid: str,
    member_oids: list[str] | None,
    sort: str | None,
    asc: bool,
    page: int | None,
    size: int,
):
    stmt = select(*_member_columns()).where(
        members_tbl.c.activity_oid == activity_oid
    )
    if member_oids is not None:
        stmt = stmt.where(_chunked_in(members_tbl.c.member_oid, member_oids))
    if sort:
        column = getattr(members_tbl.c, _RAW_SORT_COLUMNS.get(sort, "oid"))
        stmt = stmt.order_by(column.asc() if asc else column.desc())
    if page is not None:
        stmt = stmt.offset((page - 1) * size).limit(size)
    return stmt


def _total_of(rows: Any) -> int | None:
    """The ``COUNT(*) OVER()`` value carried by a merged count+page result.

    ``None`` for an empty page: the window count is not observable then, and the
    caller decides whether the page is simply empty (``page == 1`` → ``0``) or
    out of range (a dedicated count statement is issued).
    """
    if not rows:
        return None
    return int(rows[0]._mapping["total_count"])


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
        stmt = _list_for_activity_stmt(activity, query, page, size)
        count_stmt = _list_for_activity_count_stmt(activity, query)
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
                **_member_row(row._mapping),
                "member_name": row._mapping["member_name"] or "未知用户",
            }
            for row in rows
        ], total

    async def list_for_user_activities(
        self,
        user_oid: str,
        search: str,
        sort: str,
        asc: bool,
        page: int,
        size: int,
    ) -> tuple[list[Row], int]:
        member = normalize_oid(user_oid)
        stmt = _list_for_user_activities_stmt(member, search, sort, asc, page, size)
        count_stmt = _list_for_user_activities_count_stmt(member, search)
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
                "member": _member_row(row._mapping),
                "activity": _joined_activity_row(row._mapping),
            }
            for row in rows
        ], total

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

    async def add_many(
        self,
        activity_oid: str,
        entries: list[tuple[str, str, float]],
        status: str,
    ) -> None:
        """批量建成员关系（``entries`` = ``(member_oid, mode, duration)``）。

        Excel 导入原来逐行调 :meth:`add`，远程 ADB 上一行一个 RTT，1w 行 ≈ 23
        分钟且全程占着事务。这里把整批交给 oracledb 的数组绑定（一次
        ``executemany``），按 :data:`_OID_CHUNK` 行一批控制绑定变量数；与单行版
        一样只写调用方给的 session，``owns`` 时才提交，所以导入的「要么全成、
        要么全滚」由外层事务保证。
        """
        if not entries:
            return
        activity = normalize_oid(activity_oid)
        stmt = insert(members_tbl)
        async with _session_for(self._session) as (session, owns):
            for start in range(0, len(entries), _OID_CHUNK):
                batch = [
                    {
                        "oid": new_oid(),
                        "activity_oid": activity,
                        "member_oid": normalize_oid(member_oid),
                        "member_mode": mode,
                        "duration_hours": duration,
                        "member_status": status,
                    }
                    for member_oid, mode, duration in entries[
                        start : start + _OID_CHUNK
                    ]
                ]
                await session.execute(stmt, batch)
            if owns:
                await session.commit()

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

    async def cas_status(self, oid: str, expected: str, status: str) -> bool:
        """仅当当前状态仍是 ``expected`` 时改成 ``status``；返回是否改到行。

        调用方拿 ``False`` 就知道并发里有人先改了状态：不该再重算、不该再
        写审计，应当回冲突。
        """
        stmt = _cas_status_stmt(normalize_oid(oid), expected, status)
        async with _session_for(self._session) as (session, owns):
            changed = (await session.execute(stmt)).rowcount > 0
            if owns:
                await session.commit()
        return changed

    async def set_mode(self, oid: str, mode: str) -> None:
        stmt = _set_mode_stmt(normalize_oid(oid), mode)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def get_by_oid(self, oid: str) -> Row | None:
        stmt = _get_by_oid_stmt(normalize_oid(oid))
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return _member_row(row._mapping)

    async def get_by_triple(
        self, activity_oid: str, member_oid: str, mode: str
    ) -> Row | None:
        stmt = _get_by_triple_stmt(
            normalize_oid(activity_oid), normalize_oid(member_oid), mode
        )
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return _member_row(row._mapping)

    async def list_by_activity(
        self, activity_oid: str, status: str | None = None
    ) -> list[Row]:
        """Return every membership of an activity (no join, no pagination).

        ``status`` optionally keeps only memberships in that approval state;
        ``None`` returns every state (the original behaviour). Stats callers pass
        ``"effective"`` so they match the time rebuild's population.
        """
        stmt = _list_by_activity_stmt(normalize_oid(activity_oid), status)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_member_row(row._mapping) for row in rows]

    async def list_for_member(
        self,
        member_oid: str,
        status: str | None = None,
        activity_oids: list[str] | None = None,
    ) -> list[Row]:
        normalized = (
            [normalize_oid(o) for o in activity_oids]
            if activity_oids is not None
            else None
        )
        stmt = _list_for_member_stmt(normalize_oid(member_oid), status, normalized)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_member_row(row._mapping) for row in rows]

    async def list_by_members(self, member_oids: list[str]) -> list[Row]:
        unique = list(dict.fromkeys(normalize_oid(o) for o in member_oids))
        if not unique:
            return []
        # 班级人数压到 1000 以上时单条 ``IN`` 会直接 ORA-01795；这里把同一个
        # ``IN`` 拆成 OR 起来的多段，仍是**一条**语句，不按块重开、不丢顺序。
        stmt = _list_by_members_stmt(unique)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_member_row(row._mapping) for row in rows]

    async def list_by_activity_and_members(
        self, activity_oid: str, member_oids: list[str]
    ) -> list[Row]:
        """Batch form of :meth:`get_by_triple` for one activity.

        Lets a bulk import read every existing ``(member, mode)`` row in one
        statement instead of one lookup per member; the returned rows share
        ``get_by_triple``'s key space, so the caller can decide update-vs-insert
        in memory. The ``IN`` list is split into :data:`_OID_CHUNK`-sized
        ``OR``-ed ``IN`` clauses because Oracle rejects more than 1000 elements
        in one ``IN`` (ORA-01795) — the statement itself stays single, so the
        result set (and the caller's in-memory upsert pass) is unchanged.
        """
        unique = list(dict.fromkeys(normalize_oid(o) for o in member_oids))
        if not unique:
            return []
        activity = normalize_oid(activity_oid)
        stmt = _list_by_activity_and_members_stmt(activity, unique)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_member_row(row._mapping) for row in rows]

    async def count_distinct_members(self, activity_oid: str) -> int:
        """活动里不同成员的人数（同一人多条 mode 记录只算一个）。"""
        stmt = _count_distinct_members_stmt(normalize_oid(activity_oid))
        async with _session_for(self._session) as (session, _owns):
            return int((await session.execute(stmt)).scalar_one())

    async def count_for_activity(
        self,
        activity_oid: str,
        status: str | None = None,
        member_oids: list[str] | None = None,
    ) -> int:
        normalized = (
            [normalize_oid(o) for o in member_oids] if member_oids is not None else None
        )
        stmt = _count_for_activity_stmt(
            normalize_oid(activity_oid), status, normalized
        )
        async with _session_for(self._session) as (session, _owns):
            return int((await session.execute(stmt)).scalar_one())

    async def remove_for_activity(self, activity_oid: str) -> None:
        stmt = _remove_for_activity_stmt(normalize_oid(activity_oid))
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def update_fields(self, oid: str, fields: Row) -> None:
        values = {
            _FIELD_COLUMNS[key]: value
            for key, value in fields.items()
            if key in _FIELD_COLUMNS
        }
        if not values:
            return
        stmt = _update_fields_stmt(normalize_oid(oid), values)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def set_duration_for_member(
        self, activity_oid: str, member_oid: str, duration: float
    ) -> None:
        stmt = _set_duration_for_member_stmt(
            normalize_oid(activity_oid), normalize_oid(member_oid), duration
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def list_raw(
        self,
        activity_oid: str,
        member_oids: list[str] | None = None,
        sort: str | None = None,
        asc: bool = True,
        page: int | None = None,
        size: int = 10,
    ) -> tuple[list[Row], int]:
        normalized = (
            [normalize_oid(o) for o in member_oids] if member_oids is not None else None
        )
        activity = normalize_oid(activity_oid)
        page_stmt = _list_raw_stmt(activity, normalized, sort, asc, page, size)
        count_stmt = _count_for_activity_stmt(activity, None, normalized)
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(count_stmt)).scalar_one()
            rows = (await session.execute(page_stmt)).all()
        return [_member_row(row._mapping) for row in rows], int(total)


__all__ = ["OracleMembersRepo", "escape_oracle_regex"]
