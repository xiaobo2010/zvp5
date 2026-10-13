"""Oracle implementation of :class:`db.repos.protocols.UsersRepo`.

SQLAlchemy 2.0 async Core over the ``USERS`` table (``zvms_new.users``). Column
names follow the rename map in ``docs/ADB_SCHEMA_SPEC.md`` §3 (``_id``→``OID``,
``id``→``BUSINESS_ID``, ``class_id``→``CLASS_OID``, ``password``→``PASSWORD_HASH``);
returned rows are translated back to the Mongo document shape.

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

import json
import re
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy import func, insert, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import groups as groups_tbl
from db.models import users as users_tbl
from db.oracle.session import session_scope

Row = dict[str, Any]

# Role membership: Mongo ``{"roles": role}`` matches when the JSON array
# contains ``role``. ``$[*]?(@ == $r)`` tests every element against the value
# passed through PASSING, so the role stays a bound parameter.
_ROLE_EXISTS = (
    "JSON_EXISTS(users.roles, '$[*]?(@ == $r)' PASSING :role_value AS \"r\")"
)

# The Mongo documents use a class-less sentinel: the aggregation sets
# ``class_name`` to this literal when the group lookup misses.
_UNKNOWN_CLASS = "未知班级"


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


def _user_filter(
    query: str, role: str, group_oid: str | None = None
) -> list[Any]:
    """Replicate the Mongo match stages shared by list/members/count queries.

    * ``role`` → array membership (JSON_EXISTS).
    * non-empty ``query`` → case-insensitive ``contains`` on ``name``/``id``,
      with the keyword ``re.escape``d exactly like the current pipelines.
    * ``group_oid`` → ``class_id`` equality.
    """
    conds: list[Any] = []
    if role:
        conds.append(text(_ROLE_EXISTS).bindparams(role_value=role))
    if query:
        pattern = re.escape(query)
        conds.append(
            or_(
                func.regexp_like(users_tbl.c.name, pattern, "i"),
                func.regexp_like(users_tbl.c.business_id, pattern, "i"),
            )
        )
    if group_oid is not None:
        conds.append(users_tbl.c.class_oid == group_oid)
    return conds


def _user_page_stmt(query: str, role: str, page: int, size: int):
    """``list_page`` select: users LEFT JOIN groups, ordered + paginated."""
    stmt = (
        select(
            users_tbl.c.oid,
            users_tbl.c.business_id,
            users_tbl.c.name,
            users_tbl.c.roles,
            users_tbl.c.class_oid,
            groups_tbl.c.name.label("class_name"),
        )
        .select_from(
            users_tbl.outerjoin(
                groups_tbl, users_tbl.c.class_oid == groups_tbl.c.oid
            )
        )
    )
    conds = _user_filter(query, role)
    if conds:
        stmt = stmt.where(*conds)
    return (
        stmt.order_by(users_tbl.c.business_id.asc(), users_tbl.c.oid.asc())
        .offset((page - 1) * size)
        .limit(size)
    )


def _user_count_stmt(query: str, role: str):
    """``list_page`` count: same filter, no join/pagination needed."""
    stmt = select(func.count()).select_from(users_tbl)
    conds = _user_filter(query, role)
    if conds:
        stmt = stmt.where(*conds)
    return stmt


def _user_columns() -> tuple[Any, ...]:
    """The full USERS projection shared by the by-id / by-学号 lookups."""
    return (
        users_tbl.c.oid,
        users_tbl.c.business_id,
        users_tbl.c.name,
        users_tbl.c.roles,
        users_tbl.c.class_oid,
        users_tbl.c.password_hash,
        users_tbl.c.session_id,
        users_tbl.c.sex,
        users_tbl.c.eligibility,
        users_tbl.c.past,
        users_tbl.c.banned,
    )


def _get_by_oid_stmt(oid: str):
    return select(*_user_columns()).where(users_tbl.c.oid == oid)


def _find_by_business_id_stmt(business_id: str):
    return select(*_user_columns()).where(
        users_tbl.c.business_id == business_id
    )


def _insert_user_stmt(
    oid: str,
    business_id: str,
    name: str,
    roles: list[str],
    password_hash: str | None,
    class_oid: str | None,
):
    return insert(users_tbl).values(
        oid=oid,
        business_id=business_id,
        name=name,
        roles=roles,
        password_hash=password_hash,
        class_oid=class_oid,
    )


def _set_password_stmt(oid: str, password_hash: str | None):
    return (
        update(users_tbl)
        .where(users_tbl.c.oid == oid)
        .values(password_hash=password_hash)
    )


def _set_roles_stmt(oid: str, roles: list[str]):
    return update(users_tbl).where(users_tbl.c.oid == oid).values(roles=roles)


def _set_class_stmt(user_oid: str, class_oid: str | None):
    return (
        update(users_tbl)
        .where(users_tbl.c.oid == user_oid)
        .values(class_oid=class_oid)
    )


def _get_past_stmt(oid: str):
    return select(users_tbl.c.past).where(users_tbl.c.oid == oid)


def _set_past_stmt(oid: str, past: list[str]):
    return update(users_tbl).where(users_tbl.c.oid == oid).values(past=past)


def _detach_class_stmt(class_oid: str):
    return (
        update(users_tbl)
        .where(users_tbl.c.class_oid == class_oid)
        .values(class_oid=None)
    )


def _get_names_stmt(oids: list[str]):
    return select(users_tbl.c.oid, users_tbl.c.name).where(
        users_tbl.c.oid.in_(oids)
    )


# Mongo ``{"past": {"$elemMatch": {"$regex": q, "$options": "i"}}}``: a
# case-insensitive regex over the ``past`` JSON array. Oracle 19c's SQL/JSON
# path does not support the ``like_regex ... flag "i"`` clause (21c+), so the
# array is serialised and matched with ``REGEXP_LIKE`` instead — same
# unescaped-regex, case-insensitive semantics.
_PAST_MATCH = "regexp_like(json_serialize(users.past), :past_value, 'i')"


def _all_users_stmt():
    return select(*_user_columns()).order_by(users_tbl.c.oid.asc())


def _list_by_id_prefix_stmt(prefix: str):
    return (
        select(*_user_columns())
        .where(func.regexp_like(users_tbl.c.business_id, "^" + prefix))
        .order_by(users_tbl.c.oid.asc())
    )


def _list_by_class_stmt(class_oid: str):
    return (
        select(*_user_columns())
        .where(users_tbl.c.class_oid == class_oid)
        .order_by(users_tbl.c.oid.asc())
    )


def _search_filter(query: str, logged_in: bool):
    if not logged_in:
        return [users_tbl.c.business_id == query]
    return [
        or_(
            func.regexp_like(users_tbl.c.name, query, "i"),
            func.regexp_like(users_tbl.c.business_id, query, "i"),
            text(_PAST_MATCH).bindparams(past_value=query),
        )
    ]


def _search_stmt(query: str, logged_in: bool):
    stmt = select(
        users_tbl.c.oid,
        users_tbl.c.name,
        users_tbl.c.business_id,
        users_tbl.c.class_oid,
    )
    conds = _search_filter(query, logged_in)
    if conds:
        stmt = stmt.where(*conds)
    return stmt.order_by(users_tbl.c.oid.asc())


def _search_count_stmt(query: str, logged_in: bool):
    stmt = select(func.count()).select_from(users_tbl)
    conds = _search_filter(query, logged_in)
    if conds:
        stmt = stmt.where(*conds)
    return stmt


def _list_by_name_or_id_stmt(pattern: str):
    stmt = select(*_user_columns())
    if pattern:
        stmt = stmt.where(
            or_(
                func.regexp_like(users_tbl.c.name, pattern, "i"),
                func.regexp_like(users_tbl.c.business_id, pattern, "i"),
            )
        )
    return stmt.order_by(users_tbl.c.oid.asc())


def _group_by_id_prefix_stmt():
    prefix = func.substr(users_tbl.c.business_id, 1, 4)
    return (
        select(prefix.label("prefix"), func.json_arrayagg(users_tbl.c.oid).label("original_ids"))
        .group_by(prefix)
    )


def _page_row(mapping: Any) -> Row:
    """Aggregation-shaped row for ``list_page`` (matches users_v2 pipeline)."""
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
    }


def _user_doc(mapping: Any) -> Row:
    """Full document for ``get_by_oid`` / ``find_by_business_id``.

    ``password`` is always present (even when ``None``) because callers index it
    directly (``user["password"]``). Optional columns are only added when
    non-NULL so ``"eligibility" in user`` keeps its Mongo meaning.
    """
    doc: Row = {
        "_id": mapping["oid"],
        "id": mapping["business_id"],
        "name": mapping["name"],
        "password": mapping["password_hash"],
        "roles": mapping["roles"] if mapping["roles"] is not None else [],
    }
    if mapping["class_oid"] is not None:
        doc["class_id"] = mapping["class_oid"]
    if mapping["session_id"] is not None:
        doc["session_id"] = mapping["session_id"]
    if mapping["sex"] is not None:
        doc["sex"] = mapping["sex"]
    if mapping["eligibility"] is not None:
        doc["eligibility"] = mapping["eligibility"]
    if mapping["past"] is not None:
        doc["past"] = mapping["past"]
    if mapping["banned"]:
        doc["banned"] = True
    return doc


class OracleUsersRepo:
    """``UsersRepo`` over Oracle ADB (``zvms_new.users``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def get_by_oid(self, oid: str) -> Row | None:
        stmt = _get_by_oid_stmt(normalize_oid(oid))
        async with _session_for(self._session) as (session, _owns):
            result = await session.execute(stmt)
            row = result.first()
        if row is None:
            return None
        return _user_doc(row._mapping)

    async def find_by_business_id(self, business_id: str) -> Row | None:
        stmt = _find_by_business_id_stmt(business_id)
        async with _session_for(self._session) as (session, _owns):
            result = await session.execute(stmt)
            row = result.first()
        if row is None:
            return None
        return _user_doc(row._mapping)

    async def list_page(
        self, query: str, role: str, page: int, size: int
    ) -> tuple[list[Row], int]:
        page_stmt = _user_page_stmt(query, role, page, size)
        count_stmt = _user_count_stmt(query, role)
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(count_stmt)).scalar_one()
            rows = (await session.execute(page_stmt)).all()
        return [_page_row(row._mapping) for row in rows], int(total)

    async def create(
        self,
        business_id: str,
        name: str,
        roles: list[str],
        password_hash: str | None,
        class_oid: str | None,
    ) -> str:
        oid = new_oid()
        class_value = normalize_oid(class_oid) if class_oid is not None else None
        stmt = _insert_user_stmt(
            oid, business_id, name, list(roles), password_hash, class_value
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return oid

    async def set_password(self, oid: str, password_hash: str | None) -> None:
        stmt = _set_password_stmt(normalize_oid(oid), password_hash)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def set_roles(self, oid: str, roles: list[str]) -> None:
        stmt = _set_roles_stmt(normalize_oid(oid), list(roles))
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def set_class(self, user_oid: str, class_oid: str | None) -> None:
        class_value = normalize_oid(class_oid) if class_oid is not None else None
        stmt = _set_class_stmt(normalize_oid(user_oid), class_value)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()

    async def detach_class(self, class_oid: str) -> int:
        stmt = _detach_class_stmt(normalize_oid(class_oid))
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return int(result.rowcount)

    async def add_past(self, oid: str, value: str) -> None:
        target = normalize_oid(oid)
        async with _session_for(self._session) as (session, owns):
            current = (
                await session.execute(_get_past_stmt(target))
            ).scalar_one_or_none()
            merged = list(current) if current is not None else []
            merged.append(value)
            await session.execute(_set_past_stmt(target, merged))
            if owns:
                await session.commit()

    async def remove_past(self, oid: str, value: str) -> None:
        target = normalize_oid(oid)
        async with _session_for(self._session) as (session, owns):
            current = (
                await session.execute(_get_past_stmt(target))
            ).scalar_one_or_none()
            if current is None:
                return
            remaining = [item for item in current if item != value]
            if remaining == list(current):
                return
            await session.execute(_set_past_stmt(target, remaining))
            if owns:
                await session.commit()

    async def get_names(self, oids: list[str]) -> dict[str, str]:
        unique = list(dict.fromkeys(normalize_oid(oid) for oid in oids))
        if not unique:
            return {}
        stmt = _get_names_stmt(unique)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return {row._mapping["oid"]: row._mapping["name"] for row in rows}

    async def list_all(self) -> list[Row]:
        stmt = _all_users_stmt()
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_user_doc(row._mapping) for row in rows]

    async def list_by_id_prefix(self, prefix: str) -> list[Row]:
        stmt = _list_by_id_prefix_stmt(prefix)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_user_doc(row._mapping) for row in rows]

    async def list_by_class(self, class_oid: str) -> list[Row]:
        stmt = _list_by_class_stmt(normalize_oid(class_oid))
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_user_doc(row._mapping) for row in rows]

    async def search(self, query: str, logged_in: bool) -> tuple[list[Row], int]:
        page_stmt = _search_stmt(query, logged_in)
        count_stmt = _search_count_stmt(query, logged_in)
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(count_stmt)).scalar_one()
            rows = (await session.execute(page_stmt)).all()
        out: list[Row] = []
        for row in rows:
            m = row._mapping
            out.append(
                {
                    "_id": m["oid"],
                    "name": m["name"],
                    "id": m["business_id"],
                    "class_id": m["class_oid"] if m["class_oid"] is not None else None,
                }
            )
        return out, int(total)

    async def list_by_name_or_id(self, pattern: str) -> list[Row]:
        stmt = _list_by_name_or_id_stmt(pattern)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_user_doc(row._mapping) for row in rows]

    async def group_oids_by_id_prefix(self) -> list[Row]:
        stmt = _group_by_id_prefix_stmt()
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        out: list[Row] = []
        for row in rows:
            m = row._mapping
            raw = m["original_ids"]
            if isinstance(raw, str):
                ids = json.loads(raw)
            else:
                ids = list(raw) if raw is not None else []
            out.append({"_id": m["prefix"], "originalIds": [str(x) for x in ids]})
        return out


__all__ = ["OracleUsersRepo"]
