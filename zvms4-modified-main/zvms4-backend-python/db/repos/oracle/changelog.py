"""Oracle implementation of :class:`db.repos.protocols.ChangelogRepo`.

SQLAlchemy 2.0 async Core over the ``CHANGELOG`` table (``zvms_new.changelog``):
``version`` → ``VERSION``, ``date`` → ``RELEASE_DATE`` (mapped back to the Mongo
key ``date`` on read), ``items`` → a JSON ``CLOB``. Returned rows use the shared
``Row`` shape (``_id`` lowercase hex, ``date``, ``items`` as a decoded list).

Redis read-through
------------------
``list_all`` is the single hottest caller-visible read in the API: ``GET /api/``
and ``GET /api/changelog`` both go through it (``main.current_version`` reads
``entries[0]["version"]``), and on ADB it costs one round trip *per request*
(138ms cross-ocean). The whole list is cached for 5 minutes under a key that
carries the ``changelog`` epoch; ``create`` / ``update`` / ``delete`` forget it
immediately, so an edit is visible on the next request even though the TTL is
long. A separate "latest version" key is deliberately **not** implemented:
there is no call site that can be changed to use it (``main.py`` and
``changelog_router.py`` both call ``list_all``), so it would be dead weight on
top of a cache that already answers both endpoints without touching ADB.

Session ownership
-----------------
``__init__`` takes an optional ``AsyncSession``:

* ``session is None`` (default): each method opens its own session through
  :func:`db.oracle.session.session_scope`, and **writes commit once** (one write
  = one transaction); the session is closed by ``session_scope``.
* ``session`` supplied: the repo uses it as-is and **never commits or closes**
  it — the caller owns the unit of work. Caching is bypassed in that mode: a
  caller-owned session may hold uncommitted rows, and a cache hit would hand
  back the pre-transaction value.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

import random
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db import cache
from db.ids import new_oid, normalize_oid
from db.models import changelog as changelog_tbl
from db.oracle.session import session_scope

Row = dict[str, Any]

#: 全量列表的 TTL。版本号挂在列表首行，改动只发生在管理员编辑变更日志时
#: （一年几次），且写路径主动 bump epoch；TTL 只是兜底，可以给到 30 分钟。
_LIST_TTL_SECONDS = 1800.0
#: 缓存域：``cache.epoch`` 的 scope（写时 INCR，旧 key 靠 TTL 自然淘汰）。
_CACHE_SCOPE = "changelog"


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


def _changelog_columns() -> tuple[Any, ...]:
    """The full CHANGELOG projection shared by the list / by-id lookups."""
    return (
        changelog_tbl.c.oid,
        changelog_tbl.c.version,
        changelog_tbl.c.release_date,
        changelog_tbl.c["items"],
        changelog_tbl.c.updated_at,
    )


def _list_all_stmt():
    return select(*_changelog_columns()).order_by(
        changelog_tbl.c.release_date.desc(),
        changelog_tbl.c.version.desc(),
    )


def _get_by_oid_stmt(oid: str):
    return select(*_changelog_columns()).where(changelog_tbl.c.oid == oid)


def _insert_stmt(oid: str, version: str, release_date: str, items: list[str]):
    return insert(changelog_tbl).values(
        oid=oid,
        version=version,
        release_date=release_date,
        items=items,
        updated_at=datetime.now(),
    )


def _update_stmt(oid: str, version: str, release_date: str, items: list[str]):
    return (
        update(changelog_tbl)
        .where(changelog_tbl.c.oid == oid)
        .values(
            version=version,
            release_date=release_date,
            items=items,
            updated_at=datetime.now(),
        )
    )


def _delete_stmt(oid: str):
    return delete(changelog_tbl).where(changelog_tbl.c.oid == oid)


def _entry_doc(mapping: Any) -> Row:
    """Normalise an ORM row to the shared ``Row`` shape."""
    items = mapping["items"]
    return {
        "_id": mapping["oid"],
        "version": mapping["version"],
        "date": mapping["release_date"],
        "items": items if items is not None else [],
        "updated_at": mapping["updated_at"],
    }


async def _forget_list() -> None:
    """写后失效：epoch INCR，旧列表 key 从此不再被读到。

    只自增、不删 key（也不需要知道 key 的确切形状）：旧 epoch 的 key 靠 TTL
    自然淘汰，避免为此跑一趟 ``SCAN``。
    """
    await cache.bump_epoch(_CACHE_SCOPE)


class OracleChangelogRepo:
    """``ChangelogRepo`` over Oracle ADB (``zvms_new.changelog``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session
        # 调用方自带事务时不做缓存（理由见模块 docstring）。
        self._use_cache = session is None

    async def _list_all(self) -> list[Row]:
        stmt = _list_all_stmt()
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_entry_doc(row._mapping) for row in rows]

    async def list_all(self) -> list[Row]:
        if not self._use_cache:
            return await self._list_all()
        version = await cache.epoch(_CACHE_SCOPE)
        key = f"{cache.NS}:changelog:{version}:all"
        # ±10% 抖动：多进程同时冷启动时别让这一条 key 在同毫秒一起过期。
        ttl = _LIST_TTL_SECONDS * (1.0 + random.uniform(-0.1, 0.1))
        return await cache.remember(key, ttl, self._list_all, async_backfill=True)

    async def get_by_oid(self, oid: str) -> Row | None:
        stmt = _get_by_oid_stmt(normalize_oid(oid))
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return _entry_doc(row._mapping)

    async def create(self, version: str, date: str, items: list[str]) -> str:
        oid = new_oid()
        stmt = _insert_stmt(oid, version, date, list(items))
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        await _forget_list()
        return oid

    async def update(
        self, oid: str, version: str, date: str, items: list[str]
    ) -> int:
        stmt = _update_stmt(normalize_oid(oid), version, date, list(items))
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        await _forget_list()
        return int(result.rowcount)

    async def delete(self, oid: str) -> int:
        stmt = _delete_stmt(normalize_oid(oid))
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        await _forget_list()
        return int(result.rowcount)


__all__ = ["OracleChangelogRepo"]
