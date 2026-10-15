"""Oracle implementation of :class:`db.repos.protocols.TimeRepo`.

SQLAlchemy 2.0 async Core over ``USER_TIME`` (Mongo ``time``),
``TIME_ACADEMIC_YEAR`` and ``TIME_WITH_ORIGIN``. Column names follow the rename
map in ``docs/ADB_SCHEMA_SPEC.md`` §3 (``user``→``USER_OID``;
``time_academic_year.entry``→``ENTRY_YEAR`` and the Mongo hyphenated
``on-campus``/``off-campus``/``social-practice``→``ON_CAMPUS``/``OFF_CAMPUS``/
``SOCIAL_PRACTICE``); returned rows are translated back to the Mongo document
shape.

``upsert_*`` mirror the live Mongo write, which is a **whole-row replace**
(``delete_many`` then ``insert_one``): the delete + insert run in one
transaction and commit **once**.

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

from sqlalchemy import delete, func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import time_academic_year as ay_tbl
from db.models import time_with_origin as origin_tbl
from db.models import time_with_origin_stat as stat_tbl
from db.models import user_time as time_tbl
from db.models import users as users_tbl
from db.oracle.session import session_scope
from db.repos.oracle.users import _search_filter

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


def _time_columns() -> tuple[Any, ...]:
    """The full USER_TIME projection."""
    return (
        time_tbl.c.oid,
        time_tbl.c.user_oid,
        time_tbl.c.on_campus_raw,
        time_tbl.c.off_campus_raw,
        time_tbl.c.social_practice,
        time_tbl.c.updated_at,
    )


def _ay_columns() -> tuple[Any, ...]:
    """The full TIME_ACADEMIC_YEAR projection."""
    return (
        ay_tbl.c.oid,
        ay_tbl.c.user_oid,
        ay_tbl.c.entry_year,
        ay_tbl.c.academic_year,
        ay_tbl.c.start_of_year,
        ay_tbl.c.end_of_year,
        ay_tbl.c.on_campus,
        ay_tbl.c.off_campus,
        ay_tbl.c.social_practice,
    )


def _origin_columns() -> tuple[Any, ...]:
    """The full TIME_WITH_ORIGIN projection."""
    return (
        origin_tbl.c.oid,
        origin_tbl.c.user_oid,
        origin_tbl.c.data,
        origin_tbl.c.updated_at,
    )


def _get_for_user_stmt(user_oid: str):
    return select(*_time_columns()).where(time_tbl.c.user_oid == user_oid)


def _delete_for_user_stmt(user_oid: str):
    return delete(time_tbl).where(time_tbl.c.user_oid == user_oid)


def _insert_for_user_stmt(
    oid: str,
    user_oid: str,
    on_campus_raw: float,
    off_campus_raw: float,
    social_practice: float,
):
    return insert(time_tbl).values(
        oid=oid,
        user_oid=user_oid,
        on_campus_raw=on_campus_raw,
        off_campus_raw=off_campus_raw,
        social_practice=social_practice,
        updated_at=datetime.now(),
    )


def _list_academic_years_stmt(user_oid: str):
    return (
        select(*_ay_columns())
        .where(ay_tbl.c.user_oid == user_oid)
        .order_by(ay_tbl.c.academic_year.asc())
    )


def _get_with_origin_stmt(user_oid: str):
    return select(*_origin_columns()).where(origin_tbl.c.user_oid == user_oid)


def _delete_with_origin_stmt(user_oid: str):
    return delete(origin_tbl).where(origin_tbl.c.user_oid == user_oid)


def _insert_with_origin_stmt(oid: str, user_oid: str, data: Any):
    return insert(origin_tbl).values(
        oid=oid,
        user_oid=user_oid,
        data=data,
        updated_at=datetime.now(),
    )


# Mongo ``time`` sort keys → Oracle columns (anything else sorts by nothing).
_TIME_SORT_COLUMNS = {
    "on_campus_raw": time_tbl.c.on_campus_raw,
    "off_campus_raw": time_tbl.c.off_campus_raw,
    "social_practice": time_tbl.c.social_practice,
}


def _list_by_users_stmt(
    user_oids: list[str],
    sort_field: str | None,
    descending: bool,
    page: int | None,
    size: int | None,
):
    stmt = select(*_time_columns()).where(time_tbl.c.user_oid.in_(user_oids))
    column = _TIME_SORT_COLUMNS.get(sort_field) if sort_field else None
    if column is not None:
        stmt = stmt.order_by(column.desc() if descending else column.asc())
    else:
        stmt = stmt.order_by(time_tbl.c.oid.asc())
    if page is not None:
        stmt = stmt.offset((page - 1) * size).limit(size)
    return stmt


def _joined_time_columns() -> tuple[Any, ...]:
    return (*_time_columns(), users_tbl.c.name, users_tbl.c.business_id)


def _user_count_stmt(conds: list[Any]):
    stmt = select(func.count()).select_from(users_tbl)
    if conds:
        stmt = stmt.where(*conds)
    return stmt


def _scalar_user_count(conds: list[Any]):
    return _user_count_stmt(conds).correlate(None).scalar_subquery()


def _list_joined_stmt(
    conds: list[Any],
    count_conds: list[Any],
    sort_field: str | None,
    descending: bool,
    page: int | None,
    size: int | None,
):
    stmt = select(
        *_joined_time_columns(),
        _scalar_user_count(count_conds).label("total_count"),
    ).select_from(time_tbl.join(users_tbl, users_tbl.c.oid == time_tbl.c.user_oid))
    if conds:
        stmt = stmt.where(*conds)
    column = _TIME_SORT_COLUMNS.get(sort_field) if sort_field else None
    if column is not None:
        stmt = stmt.order_by(column.desc() if descending else column.asc())
    else:
        stmt = stmt.order_by(time_tbl.c.oid.asc())
    if page is not None:
        stmt = stmt.offset((page - 1) * size).limit(size)
    return stmt


def _search_conds(query: str, logged_in: bool) -> list[Any]:
    return _search_filter(query, logged_in)


def _class_conds(class_oid: str, query: str) -> list[Any]:
    conds: list[Any] = [users_tbl.c.class_oid == class_oid]
    if query:
        conds.append(
            or_(
                func.regexp_like(users_tbl.c.name, query, "i"),
                func.regexp_like(users_tbl.c.business_id, query, "i"),
            )
        )
    return conds


def _list_joined_by_search_stmt(
    query: str,
    logged_in: bool,
    sort_field: str | None,
    descending: bool,
    page: int | None,
    size: int | None,
):
    return _list_joined_stmt(
        _search_conds(query, logged_in),
        _search_conds(query, logged_in),
        sort_field,
        descending,
        page,
        size,
    )


def _list_joined_by_class_stmt(
    class_oid: str,
    query: str,
    sort_field: str | None,
    descending: bool,
    page: int | None,
    size: int | None,
):
    return _list_joined_stmt(
        _class_conds(class_oid, query),
        _class_conds(class_oid, query),
        sort_field,
        descending,
        page,
        size,
    )


def _list_with_origin_stmt(user_oids: list[str]):
    return select(*_origin_columns()).where(origin_tbl.c.user_oid.in_(user_oids))


def _delete_with_origin_stat_stmt(stat_key: str):
    return delete(stat_tbl).where(stat_tbl.c.stat_key == stat_key)


def _insert_with_origin_stat_stmt(
    oid: str,
    stat_key: str,
    scope: str,
    scope_id: str,
    require_percentage: int,
    data: Any,
):
    return insert(stat_tbl).values(
        oid=oid,
        stat_key=stat_key,
        scope=scope,
        scope_id=scope_id,
        require_percentage=require_percentage,
        data=data,
        updated_at=datetime.now(),
    )


def _delete_all_stmt(table: Any):
    return delete(table)


def _time_row(mapping: Any) -> Row:
    """Translate a USER_TIME row to the Mongo ``time`` document shape."""
    return {
        "_id": mapping["oid"],
        "user": mapping["user_oid"],
        "on_campus_raw": mapping["on_campus_raw"],
        "off_campus_raw": mapping["off_campus_raw"],
        "social_practice": mapping["social_practice"],
        "updated_at": mapping["updated_at"],
    }


def _time_user_row(mapping: Any) -> Row:
    row = _time_row(mapping)
    row["name"] = mapping["name"]
    row["id"] = mapping["business_id"]
    return row


def _academic_year_row(mapping: Any) -> Row:
    """Translate TIME_ACADEMIC_YEAR to the Mongo document shape (hyphen keys)."""
    return {
        "_id": mapping["oid"],
        "user": mapping["user_oid"],
        "entry": mapping["entry_year"],
        "academic_year": mapping["academic_year"],
        "start_of_year": mapping["start_of_year"],
        "end_of_year": mapping["end_of_year"],
        "on-campus": mapping["on_campus"],
        "off-campus": mapping["off_campus"],
        "social-practice": mapping["social_practice"],
    }


def _with_origin_row(mapping: Any) -> Row:
    """Translate TIME_WITH_ORIGIN to the Mongo document shape."""
    return {
        "_id": mapping["oid"],
        "user": mapping["user_oid"],
        "data": mapping["data"],
        "updated_at": mapping["updated_at"],
    }


class OracleTimeRepo:
    """``TimeRepo`` over Oracle ADB (``zvms_new`` time tables)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def get_for_user(self, user_oid: str) -> Row | None:
        stmt = _get_for_user_stmt(normalize_oid(user_oid))
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return _time_row(row._mapping)

    async def upsert_for_user(
        self,
        user_oid: str,
        on_campus_raw: float,
        off_campus_raw: float,
        social_practice: float,
    ) -> None:
        user = normalize_oid(user_oid)
        delete_stmt = _delete_for_user_stmt(user)
        insert_stmt = _insert_for_user_stmt(
            new_oid(),
            user,
            on_campus_raw,
            off_campus_raw,
            social_practice,
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(delete_stmt)
            await session.execute(insert_stmt)
            if owns:
                await session.commit()

    async def list_academic_years(self, user_oid: str) -> list[Row]:
        stmt = _list_academic_years_stmt(normalize_oid(user_oid))
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_academic_year_row(row._mapping) for row in rows]

    async def get_with_origin(self, user_oid: str) -> Row | None:
        stmt = _get_with_origin_stmt(normalize_oid(user_oid))
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return _with_origin_row(row._mapping)

    async def upsert_with_origin(self, user_oid: str, data: Row) -> None:
        user = normalize_oid(user_oid)
        delete_stmt = _delete_with_origin_stmt(user)
        insert_stmt = _insert_with_origin_stmt(new_oid(), user, data)
        async with _session_for(self._session) as (session, owns):
            await session.execute(delete_stmt)
            await session.execute(insert_stmt)
            if owns:
                await session.commit()

    async def list_by_users(
        self,
        user_oids: list[str],
        sort_field: str | None = None,
        descending: bool = False,
        page: int | None = None,
        size: int | None = None,
    ) -> list[Row]:
        if not user_oids:
            return []
        users = [normalize_oid(oid) for oid in user_oids]
        stmt = _list_by_users_stmt(users, sort_field, descending, page, size)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_time_row(row._mapping) for row in rows]

    async def list_by_search(
        self,
        query: str,
        logged_in: bool,
        sort_field: str | None = None,
        descending: bool = False,
        page: int | None = None,
        size: int | None = None,
    ) -> tuple[list[Row], int]:
        stmt = _list_joined_by_search_stmt(
            query, logged_in, sort_field, descending, page, size
        )
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        if rows:
            return (
                [_time_user_row(row._mapping) for row in rows],
                int(rows[0]._mapping["total_count"]),
            )
        count_stmt = _user_count_stmt(_search_conds(query, logged_in))
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(count_stmt)).scalar_one()
        return [], int(total)

    async def list_by_class(
        self,
        class_oid: str,
        query: str = "",
        sort_field: str | None = None,
        descending: bool = False,
        page: int | None = None,
        size: int | None = None,
    ) -> tuple[list[Row], int]:
        target = normalize_oid(class_oid)
        stmt = _list_joined_by_class_stmt(
            target, query, sort_field, descending, page, size
        )
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        if rows:
            return (
                [_time_user_row(row._mapping) for row in rows],
                int(rows[0]._mapping["total_count"]),
            )
        count_stmt = _user_count_stmt(_class_conds(target, query))
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(count_stmt)).scalar_one()
        return [], int(total)

    async def list_with_origin(self, user_oids: list[str]) -> list[Row]:
        if not user_oids:
            return []
        users = [normalize_oid(oid) for oid in user_oids]
        stmt = _list_with_origin_stmt(users)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_with_origin_row(row._mapping) for row in rows]

    async def upsert_with_origin_stat(
        self,
        stat_key: str,
        scope: str,
        scope_id: str,
        require_percentage: int,
        data: Row,
    ) -> None:
        delete_stmt = _delete_with_origin_stat_stmt(stat_key)
        insert_stmt = _insert_with_origin_stat_stmt(
            new_oid(), stat_key, scope, scope_id, require_percentage, data
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(delete_stmt)
            await session.execute(insert_stmt)
            if owns:
                await session.commit()

    async def clear_for_all(self) -> None:
        stmt = _delete_all_stmt(time_tbl)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def upsert_academic_year(
        self,
        user_oid: str,
        entry: str,
        academic_year: int,
        start_of_year: Any,
        end_of_year: Any,
        on_campus: float,
        off_campus: float,
        social_practice: float,
    ) -> None:
        user = normalize_oid(user_oid)
        delete_stmt = delete(ay_tbl).where(ay_tbl.c.user_oid == user)
        insert_stmt = insert(ay_tbl).values(
            oid=new_oid(),
            user_oid=user,
            entry_year=entry,
            academic_year=academic_year,
            start_of_year=start_of_year,
            end_of_year=end_of_year,
            on_campus=on_campus,
            off_campus=off_campus,
            social_practice=social_practice,
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(delete_stmt)
            await session.execute(insert_stmt)
            if owns:
                await session.commit()

    async def clear_academic_years(self) -> None:
        stmt = _delete_all_stmt(ay_tbl)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def clear_with_origin(self) -> None:
        stmt = _delete_all_stmt(origin_tbl)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def clear_with_origin_stats(self) -> None:
        stmt = _delete_all_stmt(stat_tbl)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()


__all__ = ["OracleTimeRepo"]
