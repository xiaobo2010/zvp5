"""Oracle implementation of :class:`db.repos.protocols.BindingsRepo`.

SQLAlchemy 2.0 async Core over the ``USER_DEVICE_BINDINGS`` table (学海 ID 身份
追溯 MVP). Row shape is the Mongo-ready dict (``"_id"`` = the lowercase-hex
``OID``).

Observe-only: nothing in this module blocks a login. A binding is never deleted;
rows flipped to ``revoked`` keep their provenance, and
:meth:`OracleBindingsRepo.upsert` reactivates such a row in place rather than
inserting a duplicate ``(user_oid, xuehai_id)``. 管理端的绑定列表/改绑/解绑端点
已于 2026-11-19 删除（前端零调用），仓储侧只保留登录链路用到的读取与 upsert。

Session ownership mirrors :mod:`db.repos.oracle.users`: with no injected session
each method opens its own through :func:`db.oracle.session.session_scope`, and
writes commit once; an injected session is neither committed nor closed.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import user_device_bindings as bindings_tbl
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


def _now() -> datetime:
    """Naive ``datetime.now()`` — the schema convention for ``TIMESTAMP`` columns."""
    return datetime.now()


# ---------------------------------------------------------------------------
# statement builders
# ---------------------------------------------------------------------------

def _columns() -> tuple[Any, ...]:
    return (
        bindings_tbl.c.oid,
        bindings_tbl.c.user_oid,
        bindings_tbl.c.xuehai_id,
        bindings_tbl.c.device_id,
        bindings_tbl.c.source,
        bindings_tbl.c.status,
        bindings_tbl.c.note,
        bindings_tbl.c.created_at,
        bindings_tbl.c.updated_at,
    )


def _get_active_for_user_stmt(user_oid: str):
    return (
        select(*_columns())
        .where(
            bindings_tbl.c.user_oid == user_oid,
            bindings_tbl.c.status == "active",
        )
        .order_by(bindings_tbl.c.created_at.asc(), bindings_tbl.c.oid.asc())
    )


def _get_by_pair_stmt(user_oid: str, xuehai_id: str):
    return select(*_columns()).where(
        bindings_tbl.c.user_oid == user_oid,
        bindings_tbl.c.xuehai_id == xuehai_id,
    )


def _insert_stmt(
    oid: str, user_oid: str, xuehai_id: str, source: str, note: str | None
):
    now = _now()
    return insert(bindings_tbl).values(
        oid=oid,
        user_oid=user_oid,
        xuehai_id=xuehai_id,
        device_id=None,
        source=source,
        status="active",
        note=note,
        created_at=now,
        updated_at=now,
    )


def _reactivate_stmt(user_oid: str, xuehai_id: str):
    return (
        update(bindings_tbl)
        .where(
            bindings_tbl.c.user_oid == user_oid,
            bindings_tbl.c.xuehai_id == xuehai_id,
        )
        .values(status="active", updated_at=_now())
    )


def _binding_row(mapping: Any) -> Row:
    return {
        "_id": mapping["oid"],
        "user_oid": mapping["user_oid"],
        "xuehai_id": mapping["xuehai_id"],
        "device_id": mapping["device_id"],
        "source": mapping["source"],
        "status": mapping["status"],
        "note": mapping["note"],
        "created_at": mapping["created_at"],
        "updated_at": mapping["updated_at"],
    }


class OracleBindingsRepo:
    """``BindingsRepo`` over Oracle ADB (``USER_DEVICE_BINDINGS``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def get_active_for_user(self, user_oid: str) -> list[Row]:
        stmt = _get_active_for_user_stmt(normalize_oid(user_oid))
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_binding_row(row._mapping) for row in rows]

    async def upsert(
        self, user_oid: str, xuehai_id: str, source: str, note: str | None
    ) -> None:
        user = normalize_oid(user_oid)
        async with _session_for(self._session) as (session, owns):
            existing = (
                await session.execute(_get_by_pair_stmt(user, xuehai_id))
            ).first()
            if existing is None:
                await session.execute(
                    _insert_stmt(new_oid(), user, xuehai_id, source, note)
                )
            else:
                await session.execute(_reactivate_stmt(user, xuehai_id))
            if owns:
                await session.commit()


__all__ = ["OracleBindingsRepo"]
