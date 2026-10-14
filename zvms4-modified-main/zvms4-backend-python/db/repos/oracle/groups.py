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

from sqlalchemy import delete, func, insert, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import groups as groups_tbl
from db.models import users as users_tbl
from db.oracle.session import session_scope

Row = dict[str, Any]

_UNKNOWN_CLASS = "未知班级"

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
    stmt = select(groups_tbl.c.oid, groups_tbl.c.name, groups_tbl.c.description)
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
        self, query: str, page: int, size: int
    ) -> tuple[list[Row], int]:
        page_stmt = _list_classes_stmt(query, page, size)
        count_stmt = _list_classes_count_stmt(query)
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(count_stmt)).scalar_one()
            rows = (await session.execute(page_stmt)).all()
        return [_class_row(row._mapping) for row in rows], int(total)

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
