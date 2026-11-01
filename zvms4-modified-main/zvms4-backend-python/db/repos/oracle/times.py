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

import re
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import case, delete, false, func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import activities as activities_tbl
from db.models import activity_members as members_tbl
from db.models import time_academic_year as ay_tbl
from db.models import time_with_origin as origin_tbl
from db.models import time_with_origin_stat as stat_tbl
from db.models import user_time as time_tbl
from db.models import users as users_tbl
from db.oracle.session import session_scope
from db.repos.oracle.users import _search_filter

Row = dict[str, Any]

# 生产 ADB 的会话带 ``pdml_enabled=YES``（``PARALLEL_DEGREE_POLICY=AUTO``）：
# 这里的 ``DELETE`` 会被自动并行执行，随后同一事务里对**同一张表**的 ``INSERT``
# 立刻撞 ``ORA-12838``（``cannot read/modify an object after modifying it in
# parallel``，空表也复现）。所以每个「删完马上插回同表」的 DELETE 都带
# ``NO_PARALLEL``：这是正确性，不是性能调优。同款修法见
# :mod:`db.repos.oracle.temp_passwords`。
_NO_PARALLEL = "/*+ NO_PARALLEL */"

# Oracle 一条 ``IN`` 最多 1000 项（ORA-01795），超过就按这个粒度切块。
_OID_CHUNK = 1000


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
    return (
        delete(time_tbl)
        .where(time_tbl.c.user_oid == user_oid)
        .prefix_with(_NO_PARALLEL, dialect="oracle")
    )


def _delete_for_users_stmt(user_oids: list[str]):
    return (
        delete(time_tbl)
        .where(time_tbl.c.user_oid.in_(user_oids))
        .prefix_with(_NO_PARALLEL, dialect="oracle")
    )


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
    return (
        delete(origin_tbl)
        .where(origin_tbl.c.user_oid == user_oid)
        .prefix_with(_NO_PARALLEL, dialect="oracle")
    )


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


def _order_time_rows(
    rows: list[Row], sort_field: str | None, descending: bool
) -> list[Row]:
    """给分块读合并后的行补上 ``_list_by_users_stmt`` 的全局排序。

    单条语句时 ORDER BY 由 Oracle 负责；超过 :data:`_OID_CHUNK` 拆成多条以后，
    每条只排自己那一块，所以在外层按同一键集合（:data:`_TIME_SORT_COLUMNS`）
    和同样的 NULL 位置（ASC 时 NULL 最后、DESC 时最前，Oracle 默认）重排。
    """
    key = sort_field if sort_field in _TIME_SORT_COLUMNS else None
    if key is None:
        # 没有可用的排序列时原语句退化为 oid 升序（``descending`` 不参与）。
        rows.sort(key=lambda row: str(row["_id"]))
        return rows
    rows.sort(
        key=lambda row: (row[key] is None, 0.0 if row[key] is None else row[key]),
        reverse=descending,
    )
    return rows


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


_SEARCH_QUERY_MAX = 64


def _like_pattern(query: str) -> str:
    """``%query%`` with LIKE wildcards escaped (lower-cased for ``LOWER(col)``)."""
    escaped = (
        query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return f"%{escaped.lower()}%"


def _class_conds(class_oid: str, query: str) -> list[Any]:
    """班级成员 + 姓名/学号的关键字**字面量**匹配。

    ``LIKE`` 而不是 ``REGEXP_LIKE``：正则引擎会在每行上编译模式，用户可控的
    关键词还能构造灾难性回溯；``LIKE`` 是普通字符串比较，``%``/``_``/``\\``
    转义后保持字面量语义。关键词上限 :data:`_SEARCH_QUERY_MAX`，超长直接
    判空（不查库也不报错），避免把异常长的入参带进任何 SQL。
    """
    conds: list[Any] = [users_tbl.c.class_oid == class_oid]
    if query:
        if len(query) > _SEARCH_QUERY_MAX:
            conds.append(false())
            return conds
        pattern = _like_pattern(query)
        conds.append(
            or_(
                func.lower(users_tbl.c.name).like(pattern, escape="\\"),
                func.lower(users_tbl.c.business_id).like(pattern, escape="\\"),
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
    return (
        delete(stat_tbl)
        .where(stat_tbl.c.stat_key == stat_key)
        .prefix_with(_NO_PARALLEL, dialect="oracle")
    )


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


_MODES = ("on-campus", "off-campus", "social-practice")


def _mode_sum(mode: str):
    """``SUM(CASE WHEN member_mode = <mode> THEN duration_hours ELSE 0.0 END)``."""
    return func.sum(case((members_tbl.c.member_mode == mode, members_tbl.c.duration_hours), else_=0.0))


def _effective_member_agg():
    """Per-member per-mode sums over approved members of effective activities.

    ``activity_members am JOIN activities a ON a.oid = am.activity_oid AND
    a.status = 'effective' WHERE am.member_status = 'effective'`` grouped by
    ``am.member_oid``. The three mode sums are always present (``COALESCE`` to
    ``0.0``); the subquery is reused by the user-time / academic-year / totals
    rebuilds so the aggregation lives in SQL, not Python.
    """
    return (
        select(
            members_tbl.c.member_oid.label("member_oid"),
            func.coalesce(_mode_sum("on-campus"), 0.0).label("on_campus_raw"),
            func.coalesce(_mode_sum("off-campus"), 0.0).label("off_campus_raw"),
            func.coalesce(_mode_sum("social-practice"), 0.0).label("social_practice"),
        )
        .select_from(
            members_tbl.join(
                activities_tbl, activities_tbl.c.oid == members_tbl.c.activity_oid
            )
        )
        .where(activities_tbl.c.status == "effective")
        .where(members_tbl.c.member_status == "effective")
        .group_by(members_tbl.c.member_oid)
    )


def _rebuild_user_time_stmt():
    """One row per user (zero-time users included), ascending by oid."""
    agg = _effective_member_agg().subquery("agg")
    return (
        select(
            users_tbl.c.oid.label("user_oid"),
            func.coalesce(agg.c.on_campus_raw, 0.0).label("on_campus_raw"),
            func.coalesce(agg.c.off_campus_raw, 0.0).label("off_campus_raw"),
            func.coalesce(agg.c.social_practice, 0.0).label("social_practice"),
        )
        .select_from(users_tbl.outerjoin(agg, agg.c.member_oid == users_tbl.c.oid))
        .order_by(users_tbl.c.oid.asc())
    )


def _academic_year_expr():
    """学年 = year(date) - (month(date) >= 8 ? 0 : 1)."""
    return func.extract("YEAR", activities_tbl.c.activity_date) - case(
        (func.extract("MONTH", activities_tbl.c.activity_date) >= 8, 0),
        else_=1,
    )


def _academic_year_agg_stmt():
    """Per ``(member_oid, 学年)`` mode sums over double-effective memberships."""
    ay_expr = _academic_year_expr()
    return (
        select(
            members_tbl.c.member_oid.label("member_oid"),
            ay_expr.label("academic_year"),
            func.coalesce(_mode_sum("on-campus"), 0.0).label("on_campus"),
            func.coalesce(_mode_sum("off-campus"), 0.0).label("off_campus"),
            func.coalesce(_mode_sum("social-practice"), 0.0).label("social_practice"),
        )
        .select_from(
            members_tbl.join(
                activities_tbl, activities_tbl.c.oid == members_tbl.c.activity_oid
            )
        )
        .where(activities_tbl.c.status == "effective")
        .where(members_tbl.c.member_status == "effective")
        .group_by(members_tbl.c.member_oid, ay_expr)
    )


def _all_users_stmt():
    return select(users_tbl.c.oid, users_tbl.c.business_id)


def _totals_by_user_stmt():
    """Per-user totals + grade (``substr(business_id,1,4)``) + class, one row each."""
    agg = _effective_member_agg().subquery("agg")
    return select(
        users_tbl.c.oid.label("user_oid"),
        func.substr(users_tbl.c.business_id, 1, 4).label("grade"),
        users_tbl.c.class_oid.label("class_oid"),
        func.coalesce(agg.c.on_campus_raw, 0.0).label("on_campus_raw"),
        func.coalesce(agg.c.off_campus_raw, 0.0).label("off_campus_raw"),
        func.coalesce(agg.c.social_practice, 0.0).label("social_practice"),
    ).select_from(users_tbl.outerjoin(agg, agg.c.member_oid == users_tbl.c.oid))


def _time_by_origin_stmt():
    """Per ``(member_oid, origin, mode)`` duration sums, double-effective only."""
    return (
        select(
            members_tbl.c.member_oid.label("user_oid"),
            activities_tbl.c.origin.label("origin"),
            members_tbl.c.member_mode.label("mode"),
            func.sum(members_tbl.c.duration_hours).label("total"),
        )
        .select_from(
            members_tbl.join(
                activities_tbl, activities_tbl.c.oid == members_tbl.c.activity_oid
            )
        )
        .where(activities_tbl.c.status == "effective")
        .where(members_tbl.c.member_status == "effective")
        .group_by(
            members_tbl.c.member_oid,
            activities_tbl.c.origin,
            members_tbl.c.member_mode,
        )
    )


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

    async def upsert_many_for_users(
        self, entries: list[tuple[str, float, float, float]]
    ) -> None:
        """批量重算用户时长缓存（``entries`` = ``(user_oid, on, off, social)``）。

        班级重算原来逐学生调 :meth:`upsert_for_user`：每人一条 DELETE、一条
        INSERT、一次自带 commit，100 人最坏 ≈ 200 次跨洋往返，且中途失败会留下
        半新半旧的缓存。这里把 DELETE 收成按 :data:`_OID_CHUNK` 分块的多用户
        语句、INSERT 收成一次数组绑定，整批单事务提交——要么全更新，要么全不
        动。重复 user_oid 取最后一次，与逐条 delete+insert 的最终结果一致。
        """
        if not entries:
            return
        latest: dict[str, tuple[float, float, float]] = {}
        for user_oid, on_campus_raw, off_campus_raw, social_practice in entries:
            latest[normalize_oid(user_oid)] = (
                on_campus_raw,
                off_campus_raw,
                social_practice,
            )
        users = list(latest)
        now = datetime.now()
        stmt = insert(time_tbl)
        async with _session_for(self._session) as (session, owns):
            for start in range(0, len(users), _OID_CHUNK):
                chunk = users[start : start + _OID_CHUNK]
                await session.execute(_delete_for_users_stmt(chunk))
                # 同一批 oid 先删后插，保持「一人一行」的唯一约束语义。
                await session.execute(
                    stmt,
                    [
                        {
                            "oid": new_oid(),
                            "user_oid": user,
                            "on_campus_raw": latest[user][0],
                            "off_campus_raw": latest[user][1],
                            "social_practice": latest[user][2],
                            "updated_at": now,
                        }
                        for user in chunk
                    ],
                )
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
        users = list(dict.fromkeys(normalize_oid(oid) for oid in user_oids))
        if len(users) <= _OID_CHUNK:
            # 常规路径保持单条语句：排序和分页完全交给 Oracle（行为零变化）。
            stmt = _list_by_users_stmt(users, sort_field, descending, page, size)
            async with _session_for(self._session) as (session, _owns):
                rows = (await session.execute(stmt)).all()
            return [_time_row(row._mapping) for row in rows]
        # 超过 1000 个 user_oid 时一条 ``IN`` 会 ORA-01795，只能拆块读；
        # ORDER BY/OFFSET/FETCH 无法跨语句生效，于是合并后在外层补排序和分页。
        merged: list[Any] = []
        async with _session_for(self._session) as (session, _owns):
            for start in range(0, len(users), _OID_CHUNK):
                chunk = users[start : start + _OID_CHUNK]
                merged.extend(
                    (
                        await session.execute(
                            _list_by_users_stmt(
                                chunk, sort_field, descending, None, None
                            )
                        )
                    ).all()
                )
        rows = _order_time_rows(
            [_time_row(row._mapping) for row in merged], sort_field, descending
        )
        if page is not None:
            start = (page - 1) * size
            rows = rows[start : start + size]
        return rows

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

    def _require_session(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError(
                "rebuild_* requires a caller-owned session (single-transaction recompute)"
            )
        return self._session

    async def rebuild_user_time(self, now: datetime | None = None) -> int:
        """Single-transaction replacement of every ``user_time`` row.

        ``DELETE FROM user_time`` then one array-bound ``INSERT`` built from the
        per-user aggregation (one row per user, zero-time users included). Never
        commits — the caller owns the unit of work.
        """
        session = self._require_session()
        await session.execute(
            delete(time_tbl).prefix_with(_NO_PARALLEL, dialect="oracle")
        )
        rows = (await session.execute(_rebuild_user_time_stmt())).all()
        ts = now or datetime.now()
        payload = [
            {
                "oid": new_oid(),
                "user_oid": str(row._mapping["user_oid"]),
                "on_campus_raw": float(row._mapping["on_campus_raw"]),
                "off_campus_raw": float(row._mapping["off_campus_raw"]),
                "social_practice": float(row._mapping["social_practice"]),
                "updated_at": ts,
            }
            for row in rows
        ]
        if payload:
            await session.execute(insert(time_tbl), payload)
        return len(payload)

    async def rebuild_academic_years(self, now: datetime | None = None) -> int:
        """Single-transaction replacement of every ``time_academic_year`` row.

        Full history: for each user with a 4-digit 学号 prefix, one row for the
        entry 学年 and one for the (capped) graduation 学年, sourced from the
        per-``(user, 学年)`` aggregation. Non-numeric prefixes are skipped and
        counted. Never commits.
        """
        session = self._require_session()
        await session.execute(
            delete(ay_tbl).prefix_with(_NO_PARALLEL, dialect="oracle")
        )
        agg_rows = (await session.execute(_academic_year_agg_stmt())).all()
        agg_map = {
            (str(row._mapping["member_oid"]), int(row._mapping["academic_year"])): row._mapping
            for row in agg_rows
        }
        users = (await session.execute(_all_users_stmt())).all()
        ts = now or datetime.now()
        cur_ay = ts.year if ts.month >= 9 else ts.year - 1
        payload: list[dict[str, Any]] = []
        skipped = 0
        for user in users:
            user_oid = str(user.oid)
            entry = (user.business_id or "")[:4]
            if re.fullmatch(r"\d{4}", entry) is None:
                skipped += 1
                continue
            entry_int = int(entry)
            for ay in {entry_int, min(entry_int + 2, cur_ay)}:
                agg = agg_map.get((user_oid, ay))
                payload.append(
                    {
                        "oid": new_oid(),
                        "user_oid": user_oid,
                        "entry_year": entry,
                        "academic_year": ay,
                        "start_of_year": datetime(ay, 8, 1),
                        "end_of_year": datetime(ay + 1, 7, 31, 23, 59, 59, 999999),
                        "on_campus": float(agg["on_campus"]) if agg else 0.0,
                        "off_campus": float(agg["off_campus"]) if agg else 0.0,
                        "social_practice": float(agg["social_practice"]) if agg else 0.0,
                    }
                )
        if payload:
            await session.execute(insert(ay_tbl), payload)
        if skipped:
            print(f"rebuild_academic_years: skipped {skipped} users with non-numeric id")
        return len(payload)

    async def aggregate_totals_by_user(self) -> list[Row]:
        """Per-user totals + ``grade``/``class_oid``, one row per user (zero incl.).

        Replaces the ``indicators`` domain's N+1 read: grade is
        ``substr(business_id,1,4)`` (no numeric filtering, matching the old
        ``compute_indicators`` behaviour) and ``class_oid`` may be ``None``.
        """
        session = self._require_session()
        rows = (await session.execute(_totals_by_user_stmt())).all()
        return [
            {
                "user_oid": str(row._mapping["user_oid"]),
                "grade": row._mapping["grade"],
                "class_oid": row._mapping["class_oid"],
                "on_campus_raw": float(row._mapping["on_campus_raw"]),
                "off_campus_raw": float(row._mapping["off_campus_raw"]),
                "social_practice": float(row._mapping["social_practice"]),
            }
            for row in rows
        ]

    async def aggregate_time_by_origin(self) -> list[Row]:
        """Per ``(user, origin, mode)`` duration sums over double-effective rows."""
        session = self._require_session()
        rows = (await session.execute(_time_by_origin_stmt())).all()
        return [
            {
                "user_oid": str(row._mapping["user_oid"]),
                "origin": row._mapping["origin"],
                "mode": row._mapping["mode"],
                "total": float(row._mapping["total"]),
            }
            for row in rows
        ]

    async def rebuild_with_origin(self, rows: list[Row], now: datetime | None = None) -> int:
        """Single-transaction replacement of every ``time_with_origin`` row.

        ``rows`` carry ``user_oid`` and the JSON ``data`` dict; oids and
        ``updated_at`` are minted here. Never commits.
        """
        session = self._require_session()
        await session.execute(
            delete(origin_tbl).prefix_with(_NO_PARALLEL, dialect="oracle")
        )
        ts = now or datetime.now()
        payload = [
            {
                "oid": new_oid(),
                "user_oid": str(row["user_oid"]),
                "data": row["data"],
                "updated_at": ts,
            }
            for row in rows
        ]
        if payload:
            await session.execute(insert(origin_tbl), payload)
        return len(payload)

    async def rebuild_with_origin_stat(
        self, rows: list[Row], now: datetime | None = None
    ) -> int:
        """Single-transaction replacement of every ``time_with_origin_stat`` row.

        ``rows`` carry ``stat_key``/``scope``/``scope_id``/``require_percentage``
        and the JSON ``data`` dict; oids and ``updated_at`` are minted here. The
        orchestrator supplies the (new) ``scope_id`` for group scope. Never
        commits.
        """
        session = self._require_session()
        await session.execute(
            delete(stat_tbl).prefix_with(_NO_PARALLEL, dialect="oracle")
        )
        ts = now or datetime.now()
        payload = [
            {
                "oid": new_oid(),
                "stat_key": row["stat_key"],
                "scope": row["scope"],
                "scope_id": str(row["scope_id"]),
                "require_percentage": int(row["require_percentage"]),
                "data": row["data"],
                "updated_at": ts,
            }
            for row in rows
        ]
        if payload:
            await session.execute(insert(stat_tbl), payload)
        return len(payload)

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
        delete_stmt = (
            delete(ay_tbl)
            .where(ay_tbl.c.user_oid == user)
            .prefix_with(_NO_PARALLEL, dialect="oracle")
        )
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


__all__ = ["OracleTimeRepo"]
