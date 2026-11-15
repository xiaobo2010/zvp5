"""Oracle implementation of :class:`db.repos.protocols.GroupsRepo`.

SQLAlchemy 2.0 async Core over the ``GROUPS`` table (``zvms_new.groups``), which
holds **class groups only** (``OID``/``NAME``; the ``type:"class"`` marker is
synthesised by callers, not stored). ``members_page`` joins ``GROUPS`` to
``USERS`` to reproduce the ``groups_v3`` members aggregation.

Session ownership mirrors :mod:`db.repos.oracle.users`: with no injected
session each method opens its own, and writes commit once; an injected session
is neither committed nor closed (the caller owns the unit of work).
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy import delete, exists, func, insert, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import activities as activities_tbl
from db.models import activity_members as members_tbl
from db.models import groups as groups_tbl
from db.models import users as users_tbl
from db.oracle.session import session_scope
from db.repos.oracle.activities import _activity_doc
from db.repos.oracle.stats import _delete_group_indicators_stmt
from db.repos.oracle.users import _detach_class_stmt

Row = dict[str, Any]

_UNKNOWN_CLASS = "未知班级"

# ``list_classes`` 的硬上限：列表接口只做「合并后排序切片」，班级数量本就很小，
# 但没有上限就等于允许一条 SQL 拉全表。给个明确天花板，超出的部分由分页兜。
_CLASS_LIST_CAP = 1000

# Permission-group membership: Mongo ``{"permissions": {"$in": [...]}}`` matches
# when the JSON array contains a listed value. ``$[*]?(@ == $p)`` tests every
# element against the value passed through PASSING, so it stays a bound parameter.
_PERMISSION_EXISTS = (
    "JSON_EXISTS(groups.permissions, '$[*]?(@ == $p)' PASSING :perm_value AS \"p\")"
)


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


def _get_stmt(oid: str):
    return select(
        groups_tbl.c.oid, groups_tbl.c.name, groups_tbl.c.description
    ).where(groups_tbl.c.oid == oid)


def _list_all_stmt():
    return select(
        groups_tbl.c.oid, groups_tbl.c.name, groups_tbl.c.description
    ).order_by(groups_tbl.c.oid.asc())


def _list_non_permission_stmt():
    return (
        select(groups_tbl.c.oid, groups_tbl.c.name, groups_tbl.c.description)
        .where(groups_tbl.c.type.is_(None))
        .order_by(groups_tbl.c.oid.asc())
    )


def _list_classes_stmt(query: str, page: int, size: int):
    """班级列表一页 + ``COUNT(*) OVER()`` 总数（一次往返）。

    与 :func:`db.repos.oracle.users._user_page_stmt` 同一范式：总数随页一起
    返回，省掉「先 COUNT 再拉全量」的两步取数。``oid`` 升序本身就是唯一键，
    分页天然确定。
    """
    stmt = select(
        groups_tbl.c.oid,
        groups_tbl.c.name,
        groups_tbl.c.description,
        func.count().over().label("total_count"),
    )
    if query:
        stmt = stmt.where(
            func.regexp_like(groups_tbl.c.name, re.escape(query), "i")
        )
    return (
        stmt.order_by(groups_tbl.c.oid.asc())
        .offset((page - 1) * size)
        .limit(size)
    )


def _list_classes_count_stmt(query: str):
    """越界页补总数用：空页上窗口计数不可观测（同 ``list_page`` 的兜底）。"""
    stmt = select(func.count()).select_from(groups_tbl)
    if query:
        stmt = stmt.where(
            func.regexp_like(groups_tbl.c.name, re.escape(query), "i")
        )
    return stmt


def _insert_group_stmt(oid: str, name: str):
    return insert(groups_tbl).values(oid=oid, name=name)


def _rename_stmt(oid: str, name: str):
    return update(groups_tbl).where(groups_tbl.c.oid == oid).values(name=name)


def _set_description_stmt(oid: str, value: str):
    return (
        update(groups_tbl)
        .where(groups_tbl.c.oid == oid)
        .values(description=value)
    )


def _delete_stmt(oid: str):
    return delete(groups_tbl).where(groups_tbl.c.oid == oid)


def _lock_group_stmt(oid: str):
    """父行 ``FOR UPDATE``：级联删除的第一步，命中 0 行即「班级不存在」。"""
    return _get_stmt(oid).with_for_update()


def _members_page_stmt(group_oid: str, query: str, page: int, size: int):
    """``members_page`` select: members of a class with resolved class name.

    Mirrors the ``groups_v3`` pipeline: ``class_id`` filter, optional case-
    insensitive ``contains`` on ``name``/``id``, join to ``groups`` for
    ``class_name`` (``未知班级`` when the class is gone), ordered by ``id``.
    """
    stmt = (
        select(
            users_tbl.c.oid,
            users_tbl.c.business_id,
            users_tbl.c.name,
            users_tbl.c.roles,
            users_tbl.c.class_oid,
            groups_tbl.c.name.label("class_name"),
            users_tbl.c.password_hash,
            users_tbl.c.banned,
        )
        .select_from(
            users_tbl.outerjoin(
                groups_tbl, users_tbl.c.class_oid == groups_tbl.c.oid
            )
        )
        .where(users_tbl.c.class_oid == group_oid)
    )
    if query:
        pattern = re.escape(query)
        stmt = stmt.where(
            or_(
                func.regexp_like(users_tbl.c.name, pattern, "i"),
                func.regexp_like(users_tbl.c.business_id, pattern, "i"),
            )
        )
    return (
        stmt.order_by(users_tbl.c.business_id.asc(), users_tbl.c.oid.asc())
        .offset((page - 1) * size)
        .limit(size)
    )


def _members_count_stmt(group_oid: str, query: str):
    stmt = (
        select(func.count())
        .select_from(users_tbl)
        .where(users_tbl.c.class_oid == group_oid)
    )
    if query:
        pattern = re.escape(query)
        stmt = stmt.where(
            or_(
                func.regexp_like(users_tbl.c.name, pattern, "i"),
                func.regexp_like(users_tbl.c.business_id, pattern, "i"),
            )
        )
    return stmt


_ACTIVITY_COLUMNS = (
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

_ACTIVITY_SORT_COLUMNS = {
    "name": activities_tbl.c.name,
    "description": activities_tbl.c.description,
    "type": activities_tbl.c.atype,
    "creator": activities_tbl.c.creator_oid,
    "status": activities_tbl.c.status,
    "date": activities_tbl.c.activity_date,
    "createdAt": activities_tbl.c.created_at,
    "updatedAt": activities_tbl.c.updated_at,
    "appointee": activities_tbl.c.appointee_oid,
    "approver": activities_tbl.c.approver,
    "place": activities_tbl.c.place,
    "origin": activities_tbl.c.origin,
    "_id": activities_tbl.c.oid,
}


def _group_activities_stmt(
    group_oid: str,
    search: str,
    sort: str,
    asc: bool,
    page: int,
    size: int,
):
    member_exists = (
        select(1)
        .select_from(
            members_tbl.join(
                users_tbl, members_tbl.c.member_oid == users_tbl.c.oid
            )
        )
        .where(
            members_tbl.c.activity_oid == activities_tbl.c.oid,
            users_tbl.c.class_oid == group_oid,
        )
    )
    stmt = select(
        *_ACTIVITY_COLUMNS,
        func.count().over().label("total_count"),
    ).where(exists(member_exists))
    if search:
        stmt = stmt.where(
            or_(
                func.regexp_like(activities_tbl.c.name, search, "i"),
                func.regexp_like(activities_tbl.c.description, search, "i"),
            )
        )
    column = _ACTIVITY_SORT_COLUMNS.get(sort) if sort else None
    if column is not None:
        stmt = stmt.order_by(column.asc() if asc else column.desc())
    else:
        stmt = stmt.order_by(activities_tbl.c.oid.desc())
    return stmt.offset((page - 1) * size).limit(size)


def _merged_total(rows: Any) -> int:
    if not rows:
        return 0
    return int(rows[0]._mapping["total_count"])


def _permission_columns() -> tuple[Any, ...]:
    return (
        groups_tbl.c.oid,
        groups_tbl.c.name,
        groups_tbl.c["permissions"],
        groups_tbl.c.description,
    )


def _permission_filter(query: str, permissions: list[str] | None) -> list[Any]:
    """Permission-group filter: ``type == "permission"`` + name/permissions filters."""
    conds: list[Any] = [groups_tbl.c.type == "permission"]
    if query:
        conds.append(func.regexp_like(groups_tbl.c.name, re.escape(query), "i"))
    if permissions:
        conds.append(
            or_(
                *[
                    text(_PERMISSION_EXISTS).bindparams(perm_value=value)
                    for value in permissions
                ]
            )
        )
    return conds


def _list_permission_stmt(query: str, permissions: list[str] | None):
    stmt = select(*_permission_columns())
    conds = _permission_filter(query, permissions)
    if conds:
        stmt = stmt.where(*conds)
    return stmt.order_by(groups_tbl.c.oid.asc())


def _get_permission_stmt(oid: str):
    return select(*_permission_columns()).where(
        groups_tbl.c.oid == oid, groups_tbl.c.type == "permission"
    )


def _class_row(mapping: Any) -> Row:
    description = mapping["description"]
    return {
        "_id": mapping["oid"],
        "name": mapping["name"],
        "description": description if description is not None else "",
    }


def _permission_row(mapping: Any) -> Row:
    permissions = mapping["permissions"]
    description = mapping["description"]
    return {
        "_id": mapping["oid"],
        "name": mapping["name"],
        "type": "permission",
        "permissions": permissions if permissions is not None else [],
        "description": description if description is not None else "",
    }


def _member_row(mapping: Any) -> Row:
    """Members row shape (matches the groups_v3 members aggregation)."""
    class_oid = mapping["class_oid"]
    class_name = mapping["class_name"]
    roles = mapping["roles"]
    return {
        "_id": mapping["oid"],
        "id": mapping["business_id"],
        "name": mapping["name"],
        "roles": roles if roles is not None else [],
        "class_id": class_oid if class_oid is not None else "",
        "class_name": class_name if class_name is not None else _UNKNOWN_CLASS,
        "password": mapping["password_hash"],
        "banned": bool(mapping["banned"]),
    }


class OracleGroupsRepo:
    """``GroupsRepo`` over Oracle ADB (``zvms_new.groups``, class side)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def get(self, oid: str) -> Row | None:
        stmt = _get_stmt(normalize_oid(oid))
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return _class_row(row._mapping)

    async def list_classes(
        self, query: str = "", page: int = 1, size: int = _CLASS_LIST_CAP
    ) -> tuple[list[Row], int]:
        """``(rows, total)``：一页班级 + 总数，**一次往返**。

        分页下推到 SQL（``OFFSET/FETCH``），总数由 ``COUNT(*) OVER()`` 随页
        带回；只有空页（越界）才补一条 ``COUNT``。``size`` 被夹在
        ``1.._CLASS_LIST_CAP``，``page < 1`` 归一到 1：负 offset 在 Oracle 上
        直接报错，也不是任何调用方想要的语义。默认参数让老的两参数/三参数
        调用继续可用。
        """
        page = max(int(page), 1)
        size = min(max(int(size), 1), _CLASS_LIST_CAP)
        page_stmt = _list_classes_stmt(query, page, size)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(page_stmt)).all()
            total = (
                int(rows[0]._mapping["total_count"])
                if rows
                else (
                    0
                    if page == 1
                    else int(
                        (
                            await session.execute(
                                _list_classes_count_stmt(query)
                            )
                        ).scalar_one()
                    )
                )
            )
        return [_class_row(row._mapping) for row in rows], total

    async def create(self, name: str) -> str:
        oid = new_oid()
        stmt = _insert_group_stmt(oid, name)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return oid

    async def rename(self, oid: str, name: str) -> None:
        stmt = _rename_stmt(normalize_oid(oid), name)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def set_description(self, oid: str, value: str) -> None:
        stmt = _set_description_stmt(normalize_oid(oid), value)
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

    async def delete_cascade(self, oid: str) -> Row | None:
        """单事务删班：锁父行 → 清指标 → 解绑成员 → 删班。

        顺序是锁序的一部分，不能重排：``SELECT ... FOR UPDATE`` 先把父行拿住，
        之后才写子表，并发删同一个班只会排队，不会与「加人」构成反向锁序
        （``ORA-00060``）。``groups`` 被 ``group_indicators`` 以无 ``ON DELETE``
        的外键引用（``db/oracle/ddl/V009__statistics_rework.sql``），不先清指标
        行就删班必 ``ORA-02292``；``users.class_oid`` 同理，先解绑再删。

        整段一个事务、一次 ``commit``：中途失败时指标行与成员绑定一起回滚，
        不会留下「班还在、人已解绑」的半删除。命中 0 行返回 ``None``（404 的
        判定因此落在锁内，而不是锁外的一次读）。
        """
        target = normalize_oid(oid)
        async with _session_for(self._session) as (session, owns):
            locked = (await session.execute(_lock_group_stmt(target))).first()
            if locked is None:
                return None
            await session.execute(_delete_group_indicators_stmt(target))
            await session.execute(_detach_class_stmt(target))
            await session.execute(_delete_stmt(target))
            if owns:
                await session.commit()
        return _class_row(locked._mapping)

    async def members_page(
        self, group_oid: str, query: str, page: int, size: int
    ) -> tuple[list[Row], int]:
        goid = normalize_oid(group_oid)
        page_stmt = _members_page_stmt(goid, query, page, size)
        count_stmt = _members_count_stmt(goid, query)
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(count_stmt)).scalar_one()
            rows = (await session.execute(page_stmt)).all()
        return [_member_row(row._mapping) for row in rows], int(total)

    async def list_all(self) -> list[Row]:
        stmt = _list_all_stmt()
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_class_row(row._mapping) for row in rows]

    async def activities_page(
        self,
        group_oid: str,
        search: str,
        sort: str,
        asc: bool,
        page: int,
        size: int,
    ) -> tuple[list[Row], int]:
        stmt = _group_activities_stmt(
            normalize_oid(group_oid), search, sort, asc, page, size
        )
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_activity_doc(row._mapping) for row in rows], _merged_total(rows)

    async def list_non_permission(self) -> list[Row]:
        stmt = _list_non_permission_stmt()
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_class_row(row._mapping) for row in rows]

    async def list_permission_groups(
        self, query: str = "", permissions: list[str] | None = None
    ) -> list[Row]:
        stmt = _list_permission_stmt(query, permissions)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_permission_row(row._mapping) for row in rows]

    async def get_permission_group(self, oid: str) -> Row | None:
        stmt = _get_permission_stmt(normalize_oid(oid))
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return _permission_row(row._mapping)


__all__ = ["OracleGroupsRepo"]
