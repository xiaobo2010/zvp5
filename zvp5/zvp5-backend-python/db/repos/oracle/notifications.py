"""Oracle implementation of :class:`db.repos.protocols.NotificationsRepo`.

SQLAlchemy 2.0 async Core over ``NOTIFICATIONS`` and the singleton
``NOTIFICATION_STATE`` counter (V015), plus the per-user read state in
``NOTIFICATION_READS`` (V053). Returned rows use the shared dict shape
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

Redis read-through
------------------
``list_active`` is the per-request read (every client refresh): two ADB round
trips (state + rows) 138ms each. The ``(rows, version)`` pair is cached for 120s
under a key that carries a Redis epoch; every committed create / update / delete
/ rollback bumps that epoch, so an activation change is visible immediately and
the TTL only covers "someone else's process wrote and we did not see it". The
version returned to clients is the one captured with the rows, i.e. the
consistency argument above still holds.

The value is a full page of notices (up to :data:`_ACTIVE_LIMIT` rows of up to
4000-char bodies), so it can pass the global 256KB entry ceiling of
:func:`db.cache.remember`. That ceiling is a silent "do not cache" switch: the
row set would then be re-read from ADB on **every** request. This caller
therefore raises it to :data:`_ACTIVE_CACHE_MAX_BYTES`.

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

import hashlib
import random
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from db import cache
from db.ids import new_oid, normalize_oid
from db.models import notification_reads as reads_tbl
from db.models import notification_state as state_tbl
from db.models import notification_versions as versions_tbl
from db.models import notifications as notifications_tbl
from db.oracle.session import session_scope

Row = dict[str, Any]

# 用户侧公告列表的行数上限：只给客户端看最新一批，避免历史无限膨胀。
_ACTIVE_LIMIT = 100

#: 用户侧公告列表的缓存 TTL。公告是「管理员偶尔发一条」的读多写极少对象，
#: 120s 足够把首页/移动端的重复拉取挡掉；真正的失效靠写路径 bump epoch，
#: TTL 只兜「别的进程写、本进程没参与」之外的漏网情况。
_ACTIVE_TTL_SECONDS = 120.0
#: 列表缓存的单值上限。``db.cache`` 默认 256KB（超过就静默不缓存），而这里一页
#: 是 100 条 × 最多 4000 字正文，接近/超过 400KB 时默认上限会让缓存永久失效、
#: 退化成每请求两次 ADB 往返。放宽到 2MB 仍然是有界的（行数与正文长度都有上限）。
_ACTIVE_CACHE_MAX_BYTES = 2 * 1024 * 1024
#: 缓存域。NOTIFICATION_STATE 里的版本号在数据库里，读它本身就要一次 ADB 往返，
#: 所以 key 用 Redis epoch 当版本（写路径 INCR，跨进程天然共享）。
_CACHE_SCOPE = "notifications"


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
        notifications_tbl.c.source_ref,
        notifications_tbl.c.created_at,
        notifications_tbl.c.updated_at,
    )


def _list_active_stmt():
    # 用户侧公告列表：排除 CHANGELOG 同步生成的版本公告（``source_ref`` = CHANGELOG
    # oid，作者是 SYSTEM 哨兵）。每发一版都会多一条，数量随发版线性增长，会把真
    # 正的管理员公告淹掉。管理端 ``list_all`` 不筛，仍可停用/回滚这些条目。
    # 再加一道行数上限：历史公告只增不减，响应体（1G 生产机 + 远程 ADB 上）会随
    # 时间线性膨胀。客户端只读最新一批，取 ``created_at`` 最新的 N 条即可。
    return (
        select(*_columns())
        .where(notifications_tbl.c.active == 1)
        .where(notifications_tbl.c.source_ref.is_(None))
        .order_by(
            notifications_tbl.c.created_at.desc(),
            notifications_tbl.c.oid.desc(),
        )
        .limit(_ACTIVE_LIMIT)
    )


def _list_all_stmt():
    return select(*_columns()).order_by(
        notifications_tbl.c.created_at.desc(),
        notifications_tbl.c.oid.desc(),
    )


def _get_stmt(oid: str):
    return select(*_columns()).where(notifications_tbl.c.oid == oid)


def _get_by_source_ref_stmt(source_ref: str):
    return select(*_columns()).where(notifications_tbl.c.source_ref == source_ref)


def _get_for_update_stmt(oid: str):
    return _get_stmt(oid).with_for_update()


def _create_stmt(
    oid: str,
    title: str,
    content: str,
    author_oid: str,
    author_name: str,
    now: datetime,
    source_ref: str | None = None,
):
    return insert(notifications_tbl).values(
        oid=oid,
        title=title,
        content=content,
        active=1,
        author_oid=author_oid,
        author_name=author_name,
        version=1,
        source_ref=source_ref,
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


def _list_read_stmt(user_oid: str):
    return select(reads_tbl.c.notification_oid, reads_tbl.c.version).where(
        reads_tbl.c.user_oid == user_oid
    )


#: 标记已读的原子 upsert。Oracle 侧单语句 ``MERGE`` 是「不存在才插、存在就覆盖」
#: 的写法，重复点击/重试都只留一行；先 SELECT 再 INSERT/UPDATE 会在并发双击时
#: 撞主键（ORA-00001），而这不是错误，是同一个动作。
#:
#: 冲突键是完整 PK ``(user_oid, notification_oid)``：一条通知对一个用户只有一行。
#: ``version`` 由调用方从 ``NOTIFICATIONS`` 读出的**当前**版本传入，客户端不传。
_MARK_READ = text(
    """
    MERGE INTO notification_reads target
    USING (SELECT :user_oid AS user_oid, :notification_oid AS notification_oid
           FROM dual) source
    ON (target.user_oid = source.user_oid
        AND target.notification_oid = source.notification_oid)
    WHEN MATCHED THEN
        UPDATE SET target.version = :version, target.read_at = :at
    WHEN NOT MATCHED THEN
        INSERT (user_oid, notification_oid, version, read_at)
        VALUES (source.user_oid, source.notification_oid, :version, :at)
    """
)


def _mark_read_stmt():
    return _MARK_READ


def _delete_reads_stmt(notification_oid: str):
    """通知硬删除时同事务清理已读行；没有它就会留下永远不会再被读到的孤儿。"""
    return delete(reads_tbl).where(reads_tbl.c.notification_oid == notification_oid)


def _restore_stmt(
    oid: str,
    title: str,
    content: str,
    active: bool,
    author_oid: str,
    author_name: str,
    version: int,
    now: datetime,
):
    """Re-insert a deleted notice under its original oid for a rollback."""
    return insert(notifications_tbl).values(
        oid=oid,
        title=title,
        content=content,
        active=_active_value(active),
        author_oid=author_oid,
        author_name=author_name,
        version=version,
        created_at=now,
        updated_at=now,
    )


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
        # V038：手写通知为 NULL。用 ``.get`` 是为了兼容只投影旧列的调用方/测试桩。
        "source_ref": mapping.get("source_ref"),
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


def notification_content_hash(title: str, content: str, active: bool) -> str:
    """Return the 64-char lowercase sha256 of a notice's public content.

    ``active`` is folded in because deactivating a notice is a content change a
    client must notice even when the text is byte-identical.
    """
    flag = "1" if active else "0"
    return hashlib.sha256(f"{title}\n{content}\n{flag}".encode("utf-8")).hexdigest()


def _version_columns() -> tuple[Any, ...]:
    return (
        versions_tbl.c.oid,
        versions_tbl.c.notification_oid,
        versions_tbl.c.version,
        versions_tbl.c.title,
        versions_tbl.c.content,
        versions_tbl.c.active,
        versions_tbl.c.op,
        versions_tbl.c.source_version,
        versions_tbl.c.content_hash,
        versions_tbl.c.author_oid,
        versions_tbl.c.author_name,
        versions_tbl.c.created_at,
    )


def _list_versions_stmt(notification_oid: str):
    return (
        select(*_version_columns())
        .where(versions_tbl.c.notification_oid == notification_oid)
        .order_by(versions_tbl.c.version.desc())
    )


def _get_version_stmt(notification_oid: str, version: int):
    return select(*_version_columns()).where(
        versions_tbl.c.notification_oid == notification_oid,
        versions_tbl.c.version == version,
    )


def _max_version_stmt(notification_oid: str):
    return select(func.max(versions_tbl.c.version)).where(
        versions_tbl.c.notification_oid == notification_oid
    )


def _snapshot_values(
    *,
    notification_oid: str,
    version: int,
    title: str,
    content: str,
    active: bool,
    op: str,
    author_oid: str | None,
    author_name: str,
    created_at: datetime,
    source_version: int | None = None,
) -> Row:
    return {
        "_id": new_oid(),
        "notification_oid": notification_oid,
        "version": version,
        "title": title,
        "content": content,
        "active": bool(active),
        "op": op,
        "source_version": source_version,
        "content_hash": notification_content_hash(title, content, active),
        "author_oid": author_oid,
        "author_name": author_name,
        "created_at": created_at,
    }


def _snapshot_insert_stmt(values: Row):
    return insert(versions_tbl).values(
        oid=values["_id"],
        notification_oid=values["notification_oid"],
        version=values["version"],
        title=values["title"],
        content=values["content"],
        active=_active_value(values["active"]),
        op=values["op"],
        source_version=values["source_version"],
        content_hash=values["content_hash"],
        author_oid=values["author_oid"],
        author_name=values["author_name"],
        created_at=values["created_at"],
    )


def _version_row(mapping: Any) -> Row:
    source_version = mapping["source_version"]
    return {
        "_id": mapping["oid"],
        "notification_oid": mapping["notification_oid"],
        "version": int(mapping["version"]),
        "title": mapping["title"],
        "content": mapping["content"],
        "active": bool(mapping["active"]),
        "op": mapping["op"],
        "source_version": None if source_version is None else int(source_version),
        "content_hash": mapping["content_hash"],
        "author_oid": mapping["author_oid"],
        "author_name": mapping["author_name"],
        "created_at": mapping["created_at"],
    }


async def _write_snapshot(
    session: AsyncSession,
    *,
    notification_oid: str,
    version: int,
    title: str,
    content: str,
    active: bool,
    op: str,
    author_oid: str | None,
    author_name: str,
    created_at: datetime,
    source_version: int | None = None,
) -> Row:
    values = _snapshot_values(
        notification_oid=notification_oid,
        version=version,
        title=title,
        content=content,
        active=active,
        op=op,
        author_oid=author_oid,
        author_name=author_name,
        created_at=created_at,
        source_version=source_version,
    )
    await session.execute(_snapshot_insert_stmt(values))
    return values


async def _next_snapshot_version(
    session: AsyncSession, notification_oid: str, current_version: int
) -> int:
    """Next free revision for a snapshot that does not follow a normal edit.

    Deletes and re-inserts take a new revision while leaving
    ``NOTIFICATIONS.VERSION`` alone (delete) or starting from the history
    (rollback), so they must also clear any revision a legacy row already used.
    """
    row = (await session.execute(_max_version_stmt(notification_oid))).first()
    highest = int(row[0]) if row is not None and row[0] is not None else 0
    return max(highest, current_version) + 1


async def _forget_lists() -> None:
    """写后失效：epoch INCR，旧 key 不再被读（TTL 自然淘汰，无需 SCAN）。"""
    await cache.bump_epoch(_CACHE_SCOPE)


class OracleNotificationsRepo:
    """``NotificationsRepo`` over Oracle ADB (``NOTIFICATIONS``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session
        # 调用方自带事务时直通：它可能要读自己未提交的写。
        self._use_cache = session is None

    async def get_state(self) -> Row:
        async with _session_for(self._session) as (session, _owns):
            return await _read_state(session)

    async def _list_active_uncached(self) -> tuple[list[Row], int]:
        # State first: see the module docstring for the consistency argument.
        async with _session_for(self._session) as (session, _owns):
            state = await _read_state(session)
            rows = (await session.execute(_list_active_stmt())).all()
        return [_row(row._mapping) for row in rows], int(state["version"])

    async def list_active(self) -> tuple[list[Row], int]:
        """用户侧公告列表（2 条 SQL → 常态 1 次 Redis GET）。

        缓存的是 ``(rows, version)`` 整对：版本号与列表必须来自同一次快照，
        分开缓存会出现「新版本 + 旧列表」这种客户端最讨厌的组合。
        """
        if not self._use_cache:
            return await self._list_active_uncached()
        version = await cache.epoch(_CACHE_SCOPE)
        key = f"{cache.NS}:notif:{version}:active"
        ttl = _ACTIVE_TTL_SECONDS * (1.0 + random.uniform(-0.1, 0.1))
        cached = await cache.remember(
            key,
            ttl,
            self._list_active_uncached,
            max_bytes=_ACTIVE_CACHE_MAX_BYTES,
            async_backfill=True,
        )
        rows, db_version = cached
        return rows, int(db_version)

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

    async def get_by_source_ref(self, source_ref: str) -> Row | None:
        """按来源对象 oid 查自动生成的公告（V038，唯一约束保证至多一条）。"""
        stmt = _get_by_source_ref_stmt(source_ref)
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
        source_ref: str | None = None,
    ) -> tuple[Row, int]:
        oid = new_oid()
        author_oid = normalize_oid(author_oid)
        now = datetime.now()
        stmt = _create_stmt(
            oid, title, content, author_oid, author_name, now, source_ref
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            await _write_snapshot(
                session,
                notification_oid=oid,
                version=1,
                title=title,
                content=content,
                active=True,
                op="create",
                author_oid=author_oid,
                author_name=author_name,
                created_at=now,
            )
            version = await _bump_state(session, now)
            if owns:
                await session.commit()
        await _forget_lists()
        return (
            {
                "_id": oid,
                "title": title,
                "content": content,
                "active": True,
                "author_oid": author_oid,
                "author_name": author_name,
                "version": 1,
                "source_ref": source_ref,
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
        author_oid: str | None = None,
        author_name: str | None = None,
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

        actor_oid = normalize_oid(author_oid) if author_oid is not None else None
        now = datetime.now()
        stmt = _update_stmt(normalized, fields, now)
        async with _session_for(self._session) as (session, owns):
            # Lock first: two concurrent edits then produce revisions 2 and 3
            # instead of both computing 2 and colliding on the unique snapshot.
            locked = (await session.execute(_get_for_update_stmt(normalized))).first()
            if locked is None:
                state = await _read_state(session)
                return None, int(state["version"])
            result = await session.execute(stmt)
            if int(result.rowcount or 0) == 0:
                state = await _read_state(session)
                return None, int(state["version"])
            row = (await session.execute(_get_stmt(normalized))).first()
            if row is None:
                state = await _read_state(session)
                return None, int(state["version"])
            entry = _row(row._mapping)
            await _write_snapshot(
                session,
                notification_oid=normalized,
                version=entry["version"],
                title=entry["title"],
                content=entry["content"],
                active=entry["active"],
                op="update",
                author_oid=actor_oid or entry["author_oid"],
                author_name=author_name or entry["author_name"],
                created_at=now,
            )
            version = await _bump_state(session, now)
            if owns:
                await session.commit()
        await _forget_lists()
        return entry, version

    async def delete(
        self,
        oid: str,
        *,
        author_oid: str | None = None,
        author_name: str | None = None,
    ) -> tuple[Row | None, int]:
        normalized = normalize_oid(oid)
        actor_oid = normalize_oid(author_oid) if author_oid is not None else None
        now = datetime.now()
        async with _session_for(self._session) as (session, owns):
            row = (await session.execute(_get_for_update_stmt(normalized))).first()
            if row is None:
                state = await _read_state(session)
                return None, int(state["version"])
            deleted = _row(row._mapping)
            await _write_snapshot(
                session,
                notification_oid=normalized,
                version=await _next_snapshot_version(
                    session, normalized, deleted["version"]
                ),
                title=deleted["title"],
                content=deleted["content"],
                active=deleted["active"],
                op="delete",
                author_oid=actor_oid or deleted["author_oid"],
                author_name=author_name or deleted["author_name"],
                created_at=now,
            )
            await session.execute(_delete_stmt(normalized))
            # 同事务清掉已读行：表上没有 FK，就没有级联删除兜底；通知没了，这些
            # 行永远不会再被读到。回滚复活同一 oid 时不恢复它们 —— 复活后的
            # version 已高于任何历史值，语义上本来就该重新是未读。
            await session.execute(_delete_reads_stmt(normalized))
            version = await _bump_state(session, now)
            if owns:
                await session.commit()
        await _forget_lists()
        return deleted, version

    async def list_versions(self, oid: str) -> tuple[list[Row], int]:
        normalized = normalize_oid(oid)
        async with _session_for(self._session) as (session, _owns):
            state = await _read_state(session)
            rows = (await session.execute(_list_versions_stmt(normalized))).all()
        return [_version_row(row._mapping) for row in rows], int(state["version"])

    async def rollback(
        self,
        oid: str,
        *,
        target_version: int,
        author_oid: str,
        author_name: str,
    ) -> tuple[Row | None, int, Row | None]:
        normalized = normalize_oid(oid)
        actor_oid = normalize_oid(author_oid)
        now = datetime.now()
        async with _session_for(self._session) as (session, owns):
            current = (await session.execute(_get_for_update_stmt(normalized))).first()
            target = (
                await session.execute(_get_version_stmt(normalized, target_version))
            ).first()
            if target is None:
                state = await _read_state(session)
                return None, int(state["version"]), None
            snap = _version_row(target._mapping)
            if current is None:
                # Deleted: revive the original oid with the target content, so
                # client-side markers (read/suppressed keyed by id) stay valid.
                new_version = await _next_snapshot_version(session, normalized, 0)
                restored_author_oid = snap["author_oid"] or actor_oid
                await session.execute(
                    _restore_stmt(
                        normalized,
                        snap["title"],
                        snap["content"],
                        snap["active"],
                        restored_author_oid,
                        snap["author_name"] or author_name,
                        new_version,
                        now,
                    )
                )
                entry = {
                    "_id": normalized,
                    "title": snap["title"],
                    "content": snap["content"],
                    "active": snap["active"],
                    "author_oid": restored_author_oid,
                    "author_name": snap["author_name"] or author_name,
                    "version": new_version,
                    "created_at": now,
                    "updated_at": now,
                }
                note = entry
            else:
                await session.execute(
                    _update_stmt(
                        normalized,
                        {
                            "title": snap["title"],
                            "content": snap["content"],
                            "active": _active_value(snap["active"]),
                        },
                        now,
                    )
                )
                row = (await session.execute(_get_stmt(normalized))).first()
                if row is None:
                    state = await _read_state(session)
                    return None, int(state["version"]), None
                note = _row(row._mapping)
            snapshot = await _write_snapshot(
                session,
                notification_oid=normalized,
                version=note["version"],
                title=note["title"],
                content=note["content"],
                active=note["active"],
                op="rollback",
                author_oid=actor_oid,
                author_name=author_name,
                created_at=now,
                source_version=target_version,
            )
            version = await _bump_state(session, now)
            if owns:
                await session.commit()
        await _forget_lists()
        return note, version, snapshot

    # ------------------------------------------------------------------
    # 每用户已读状态（V053）
    #
    # 这两个方法刻意**不碰** ``list_active`` 的读缓存：那份缓存的是「全量通知
    # + 全局版本」，对所有人相同；已读状态是 per-user 的，掺进去既无法共享也
    # 会把缓存条数放大成「用户数 × 通知数」。写已读也不 bump epoch：它不改变
    # 任何用户看到的列表或版本号，失效缓存只会白白打穿一次全体用户的列表。
    # ------------------------------------------------------------------

    async def list_read(self, user_oid: str) -> dict[str, int]:
        """当前用户的已读版本映射 ``{notification_oid: version}``。

        返回该用户的**全部** read 行（不按通知是否还存在过滤）：客户端拿它和
        列表里的 ``version`` 比较即可，多出来的键自然被忽略。
        """
        stmt = _list_read_stmt(normalize_oid(user_oid))
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return {str(row[0]): int(row[1]) for row in rows}

    async def mark_read(
        self, user_oid: str, notification_oid: str, now: datetime
    ) -> tuple[str, int] | None:
        """把一条通知标记为该用户已读到**当前**版本；返回 ``(oid, version)``。

        ``version`` 读自 ``NOTIFICATIONS`` 的当前值，客户端不传：否则一个改过
        内容的通知可以被「补写一个更大的版本号」骗成已读。
        通知不存在返回 ``None`` —— 与 :meth:`update` / :meth:`delete` 的
        「拿不到行就交回调用方判 404」同一口径，路由层翻译成 404「通知不存在」。

        幂等：同一 (user, notification) 重复标记走同一条 ``MERGE``，只更新
        ``version`` / ``read_at``，不会长出新行。
        """
        user = normalize_oid(user_oid)
        oid = normalize_oid(notification_oid)
        async with _session_for(self._session) as (session, owns):
            row = (await session.execute(_get_stmt(oid))).first()
            if row is None:
                return None
            version = int(row._mapping["version"])
            await session.execute(
                _mark_read_stmt(),
                {
                    "user_oid": user,
                    "notification_oid": oid,
                    "version": version,
                    "at": now,
                },
            )
            if owns:
                await session.commit()
        return oid, version


__all__ = ["OracleNotificationsRepo", "notification_content_hash"]
