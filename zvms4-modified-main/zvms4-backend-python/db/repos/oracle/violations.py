"""Oracle implementation of :class:`db.repos.protocols.ViolationsRepo`.

SQLAlchemy 2.0 async Core over the ``BAN_VIOLATIONS`` table (平台违规使用上报,
V016). Row shape is the Mongo-ready dict (``"_id"`` = the lowercase-hex ``OID``).

The table is an **append-only evidence ledger**: the client reports what it saw
(``iframe`` / ``bridge`` / ``devtools``), and the service layer turns a window
count over this ledger into a ban. Nothing here updates or deletes a row —
losing the evidence after the ban would make the ban unexplainable.

``list_page`` outer-joins ``USERS`` so the admin list can show 姓名/学号 in one
round trip; a violation whose account has since disappeared still appears, with
``user_name`` / ``user_business_id`` as ``None`` (same "未知用户" semantics as
``ban_records``).

Session ownership mirrors :mod:`db.repos.oracle.users`: with no injected session
each method opens its own through :func:`db.oracle.session.session_scope`, and
writes commit once; an injected session is neither committed nor closed.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import func, insert, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import ban_violations as ban_violations_tbl
from db.models import users as users_tbl
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


def _insert_stmt(
    oid: str,
    user_oid: str,
    trigger: str,
    signal: str,
    xuehai_id: str | None,
    detail: str | None,
    created_at: datetime,
    batch_id: str | None = None,
):
    # ``created_at`` 由调用方传同一个 now，与去重窗口、窗口计数同源：取两次
    # 时间会让「刚写下的这条」落在自己计算的窗口之外。
    return insert(ban_violations_tbl).values(
        oid=oid,
        user_oid=user_oid,
        trigger_code=trigger,
        signal=signal,
        xuehai_id=xuehai_id,
        detail=detail,
        batch_id=batch_id,
        created_at=created_at,
    )


def _exists_recent_stmt(user_oid: str, signal: str, since: datetime):
    return (
        select(literal(1))
        .select_from(ban_violations_tbl)
        .where(
            ban_violations_tbl.c.user_oid == user_oid,
            ban_violations_tbl.c.signal == signal,
            ban_violations_tbl.c.created_at >= since,
        )
        .limit(1)
    )


def _count_since_stmt(user_oid: str, trigger: str, since: datetime):
    return (
        select(func.count())
        .select_from(ban_violations_tbl)
        .where(
            ban_violations_tbl.c.user_oid == user_oid,
            ban_violations_tbl.c.trigger_code == trigger,
            ban_violations_tbl.c.created_at >= since,
        )
    )


def _count_batches_since_stmt(user_oid: str, trigger: str, since: datetime):
    """窗口内违规**批次**数：一次上报里报了几个信号都只算一次。

    ``COALESCE(batch_id, oid)`` 是给 V019 之前的存量行留的语义：那时没有批次
    概念，每一行都是独立的一次违规，用主键顶替批次号即可保持历史口径。
    """
    return (
        select(
            func.count(
                func.distinct(
                    func.coalesce(ban_violations_tbl.c.batch_id, ban_violations_tbl.c.oid)
                )
            )
        )
        .select_from(ban_violations_tbl)
        .where(
            ban_violations_tbl.c.user_oid == user_oid,
            ban_violations_tbl.c.trigger_code == trigger,
            ban_violations_tbl.c.created_at >= since,
        )
    )


def _page_filter(query: str) -> list[Any]:
    """``query`` → 大小写不敏感 contains，命中用户姓名/学号（转义同 users 仓储）。"""
    if not query:
        return []
    pattern = re.escape(query)
    return [
        or_(
            func.regexp_like(users_tbl.c.name, pattern, "i"),
            func.regexp_like(users_tbl.c.business_id, pattern, "i"),
        )
    ]


def _from_clause():
    # LEFT JOIN：用户行消失后违规证据仍在列表里可见（姓名/学号退回 None）。
    # 违规按 user_oid 单值关联，不会放大行数。
    return ban_violations_tbl.outerjoin(
        users_tbl, ban_violations_tbl.c.user_oid == users_tbl.c.oid
    )


def _page_stmt(query: str, page: int, size: int):
    """一页违规 + ``COUNT(*) OVER()`` 总数，一次往返。

    ``created_at`` 倒序（新的违规在上），同一时刻再按 oid 倒序保证分页稳定。
    """
    stmt = select(
        ban_violations_tbl.c.oid,
        ban_violations_tbl.c.user_oid,
        ban_violations_tbl.c.trigger_code,
        ban_violations_tbl.c.signal,
        ban_violations_tbl.c.xuehai_id,
        ban_violations_tbl.c.detail,
        ban_violations_tbl.c.created_at,
        users_tbl.c.name.label("user_name"),
        users_tbl.c.business_id.label("user_business_id"),
        func.count().over().label("total_count"),
    ).select_from(_from_clause())
    conds = _page_filter(query)
    if conds:
        stmt = stmt.where(*conds)
    return (
        stmt.order_by(
            ban_violations_tbl.c.created_at.desc(), ban_violations_tbl.c.oid.desc()
        )
        .offset((page - 1) * size)
        .limit(size)
    )


def _count_stmt(query: str):
    stmt = select(func.count()).select_from(_from_clause())
    conds = _page_filter(query)
    if conds:
        stmt = stmt.where(*conds)
    return stmt


def _record_row(mapping: Any) -> Row:
    return {
        "_id": mapping["oid"],
        "user_oid": mapping["user_oid"],
        "user_name": mapping["user_name"],
        "user_business_id": mapping["user_business_id"],
        "trigger": mapping["trigger_code"],
        "signal": mapping["signal"],
        "xuehai_id": mapping["xuehai_id"],
        "detail": mapping["detail"],
        "created_at": mapping["created_at"],
    }


def _total_of(rows: Any) -> int | None:
    """窗口计数；空页时不可观测（``None``），由调用方决定是否补一次 count。"""
    if not rows:
        return None
    return int(rows[0]._mapping["total_count"])


class OracleViolationsRepo:
    """``ViolationsRepo`` over Oracle ADB (``BAN_VIOLATIONS``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def insert(
        self,
        user_oid: str,
        trigger: str,
        signal: str,
        *,
        xuehai_id: str | None = None,
        detail: str | None = None,
        batch_id: str | None = None,
    ) -> str:
        """追加一条违规证据并返回新 ``_id``（写入时刻由本层生成）。

        ``batch_id`` 由服务层为**一次上报**生成并复用给该批的每一行；它是计数
        单位（``count_batches_since``），不是证据内容。
        """
        oid = new_oid()
        stmt = _insert_stmt(
            oid,
            normalize_oid(user_oid),
            trigger,
            signal,
            xuehai_id,
            detail,
            datetime.now(),
            batch_id,
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return oid

    async def exists_recent(self, user_oid: str, signal: str, since: datetime) -> bool:
        """``since`` 之后是否已有同 ``signal`` 的记录（去重窗口的唯一判据）。"""
        stmt = _exists_recent_stmt(normalize_oid(user_oid), signal, since)
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        return row is not None

    async def count_since(self, user_oid: str, trigger: str, since: datetime) -> int:
        """``since`` 之后该用户在该 trigger 下的违规条数（证据行数）。"""
        stmt = _count_since_stmt(normalize_oid(user_oid), trigger, since)
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(stmt)).scalar_one()
        return int(total)

    async def count_batches_since(
        self, user_oid: str, trigger: str, since: datetime
    ) -> int:
        """``since`` 之后该用户在该 trigger 下的违规**批次数**（封禁计数）。"""
        stmt = _count_batches_since_stmt(normalize_oid(user_oid), trigger, since)
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(stmt)).scalar_one()
        return int(total)

    async def list_page(
        self, query: str, page: int, size: int
    ) -> tuple[list[Row], int]:
        page_stmt = _page_stmt(query, page, size)
        count_stmt = _count_stmt(query)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(page_stmt)).all()
            total = _total_of(rows)
            if total is None:
                # ``page == 1`` 的空页必然是 0；越界页才需要单独 count。
                total = (
                    0
                    if page == 1
                    else int((await session.execute(count_stmt)).scalar_one())
                )
        return [_record_row(row._mapping) for row in rows], total


__all__ = ["OracleViolationsRepo"]
