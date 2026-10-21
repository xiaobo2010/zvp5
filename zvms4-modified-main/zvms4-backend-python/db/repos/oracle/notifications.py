"""Oracle implementation of :class:`db.repos.protocols.NotificationsRepo`.

SQLAlchemy 2.0 async Core over ``NOTIFICATIONS`` and the singleton
``NOTIFICATION_STATE`` counter (V014). Returned rows use the shared dict shape
(``_id`` = lowercase-hex ``OID``, ``active`` = bool, ``version`` = int).

Why the global counter lives in a table
---------------------------------------
Clients need a cheap "did anything change?" check before pulling the list. The
singleton ``NOTIFICATION_STATE`` row is updated with ``version = version + 1``
in the **same transaction** as every successful notification create / edit /
delete, so:
* a committed notice always has a committed version bump;
* concurrent admin writes serialise on the single row (Oracle row lock);
* a failed write (unknown oid) bumps nothing.

List methods intentionally read the counter **before** the rows. A notice
created between the two statements may therefore appear in the response while
the returned version is older; the reverse (new version, missing notice) can
never happen, so a client that processes the full list can never miss a notice.

Session ownership
-----------------
``__init__`` takes an optional ``AsyncSession``:

* ``session is None`` (default): each method opens its own session through
  :func:`db.oracle.session.session_scope`, and writes commit once; the session is
  closed by ``session_scope``.
* ``session`` supplied: the repo uses it as-is and never commits or closes it —
  the caller owns the unit of work.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import notification_state as state_tbl
from db.models import notifications as notifications_tbl
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


def _columns() -> tuple[Any, ...]:
    return (
        notifications_tbl.c.oid,
        notifications_tbl.c.title,
        notifications_tbl.c.content,
        notifications_tbl.c.active,
        notifications_tbl.c.author_oid,
        notifications_tbl.c.author_name,
        notifications_tbl.c.version,
        notifications_tbl.c.created_at,
        notifications_tbl.c.updated_at,
    )


def _list_active_stmt():
    return (
        select(*_columns())
        .where(notifications_tbl.c.active == 1)
        .order_by(
            notifications_tbl.c.created_at.desc(),
            notifications_tbl.c.oid.desc(),
        )
    )


def _list_all_stmt():
    return select(*_columns()).order_by(
        notifications_tbl.c.created_at.desc(),
        notifications_tbl.c.oid.desc(),
    )


def _get_stmt(oid: str):
    return select(*_columns()).where(notifications_tbl.c.oid == oid)


def _get_for_update_stmt(oid: str):
    return _get_stmt(oid).with_for_update()


def _create_stmt(
    oid: str,
    title: str,
    content: str,
    author_oid: str,
    author_name: str,
    now: datetime,
):
    return insert(notifications_tbl).values(
        oid=oid,
        title=title,
        content=content,
        active=1,
        author_oid=author_oid,
        author_name=author_name,
        version=1,
        created_at=now,
        updated_at=now,
    )


def _update_stmt(oid: str, fields: dict[str, Any], now: datetime):
    """Patch only ``fields``; always bump the per-notice revision.

    ``author_oid`` / ``author_name`` / ``created_at`` are deliberately absent:
    the sender and send time are immutable facts, not editable content.
    """
    return (
        update(notifications_tbl)
        .where(notifications_tbl.c.oid == oid)
        .values(**fields, version=notifications_tbl.c.version + 1, updated_at=now)
    )


def _delete_stmt(oid: str):
    return delete(notifications_tbl).where(notifications_tbl.c.oid == oid)


def _state_select_stmt():
    return select(state_tbl.c.version, state_tbl.c.updated_at).where(
        state_tbl.c.id == 1
    )


def _bump_state_stmt(now: datetime):
    """Atomic global version increment (the single row lock serialises writers)."""
    return (
        update(state_tbl)
        .where(state_tbl.c.id == 1)
        .values(version=state_tbl.c.version + 1, updated_at=now)
    )


def _insert_state_stmt(now: datetime):
    """Repair path only: a properly migrated database already has the seed row."""
    return insert(state_tbl).values(id=1, version=1, updated_at=now)


def _row(mapping: Any) -> Row:
    return {
        "_id": mapping["oid"],
        "title": mapping["title"],
        "content": mapping["content"],
        "active": bool(mapping["active"]),
        "author_oid": mapping["author_oid"],
        "author_name": mapping["author_name"],
        "version": int(mapping["version"]),
        "created_at": mapping["created_at"],
        "updated_at": mapping["updated_at"],
    }


async def _read_state(session: AsyncSession) -> Row:
    row = (await session.execute(_state_select_stmt())).first()
    if row is None:
        return {"version": 0, "updated_at": None}
    mapping = row._mapping
    return {
        "version": int(mapping["version"]),
        "updated_at": mapping["updated_at"],
    }


async def _bump_state(session: AsyncSession, now: datetime) -> int:
    """Increment the singleton counter and return the new value.

    The UPDATE takes the row lock until the caller's transaction ends; a
    concurrent writer blocks instead of producing a lost update. The follow-up
    SELECT sees this transaction's own new value.
    """
    result = await session.execute(_bump_state_stmt(now))
    if int(result.rowcount or 0) == 0:
        # Only reachable on an unseeded database (normal migrations seed id=1).
        await session.execute(_insert_state_stmt(now))
        return 1
    row = (await session.execute(_state_select_stmt())).first()
    return int(row._mapping["version"]) if row is not None else 1


def _active_value(value: bool) -> int:
    return 1 if value else 0


class OracleNotificationsRepo:
    """``NotificationsRepo`` over Oracle ADB (``NOTIFICATIONS``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def get_state(self) -> Row:
        async with _session_for(self._session) as (session, _owns):
            return await _read_state(session)

    async def list_active(self) -> tuple[list[Row], int]:
        # State first: see the module docstring for the consistency argument.
        async with _session_for(self._session) as (session, _owns):
            state = await _read_state(session)
            rows = (await session.execute(_list_active_stmt())).all()
        return [_row(row._mapping) for row in rows], int(state["version"])

    async def list_all(self) -> tuple[list[Row], int]:
        async with _session_for(self._session) as (session, _owns):
            state = await _read_state(session)
            rows = (await session.execute(_list_all_stmt())).all()
        return [_row(row._mapping) for row in rows], int(state["version"])

    async def get(self, oid: str) -> Row | None:
        stmt = _get_stmt(normalize_oid(oid))
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return _row(row._mapping)

    async def create(
        self,
        *,
        title: str,
        content: str,
        author_oid: str,
        author_name: str,
    ) -> tuple[Row, int]:
        oid = new_oid()
        author_oid = normalize_oid(author_oid)
        now = datetime.now()
        stmt = _create_stmt(oid, title, content, author_oid, author_name, now)
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            version = await _bump_state(session, now)
            if owns:
                await session.commit()
        return (
            {
                "_id": oid,
                "title": title,
                "content": content,
                "active": True,
                "author_oid": author_oid,
                "author_name": author_name,
                "version": 1,
                "created_at": now,
                "updated_at": now,
            },
            version,
        )

    async def update(
        self,
        oid: str,
        *,
        title: str | None = None,
        content: str | None = None,
        active: bool | None = None,
    ) -> tuple[Row | None, int]:
        normalized = normalize_oid(oid)
        fields: dict[str, Any] = {}
        if title is not None:
            fields["title"] = title
        if content is not None:
            fields["content"] = content
        if active is not None:
            fields["active"] = _active_value(active)
        if not fields:
            # Nothing to write: no revision bump, just report current state.
            async with _session_for(self._session) as (session, _owns):
                state = await _read_state(session)
                row = (await session.execute(_get_stmt(normalized))).first()
            return (
                _row(row._mapping) if row is not None else None,
                int(state["version"]),
            )

        now = datetime.now()
        stmt = _update_stmt(normalized, fields, now)
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if int(result.rowcount or 0) == 0:
                state = await _read_state(session)
                return None, int(state["version"])
            version = await _bump_state(session, now)
            row = (await session.execute(_get_stmt(normalized))).first()
            if owns:
                await session.commit()
        if row is None:
            return None, version
        return _row(row._mapping), version

    async def delete(self, oid: str) -> tuple[Row | None, int]:
        normalized = normalize_oid(oid)
        async with _session_for(self._session) as (session, owns):
            row = (await session.execute(_get_for_update_stmt(normalized))).first()
            if row is None:
                state = await _read_state(session)
                return None, int(state["version"])
            deleted = _row(row._mapping)
            await session.execute(_delete_stmt(normalized))
            version = await _bump_state(session, datetime.now())
            if owns:
                await session.commit()
        return deleted, version


__all__ = ["OracleNotificationsRepo"]
