"""Oracle implementation of :class:`db.repos.protocols.BanRecordsRepo`.

SQLAlchemy 2.0 async Core over the ``BAN_RECORDS`` table (封禁流水, V013). Row
shape is the Mongo-ready dict (``"_id"`` = the lowercase-hex ``OID``).

The table is the escalation ledger, not a mirror of the current ban state
(``USERS.BANNED`` owns that): one row per ban event, append-only. The number of
rows for one ``(user_oid, rule_code)`` pair is what picks the next penalty step,
which is why :meth:`OracleBanRecordsRepo.count_for_user_rule` exists and why
nothing here ever updates or deletes a row.

``list_page`` outer-joins ``USERS`` so the admin ledger can show 姓名/学号 in one
round trip; a record whose account has since disappeared still appears, with
``user_name`` / ``user_business_id`` as ``None`` (the same "未知用户" semantics as
the rest of the schema). A second LEFT JOIN on ``USERS`` (aliased ``op``)
resolves the operator's name for admin bans; rule bans have
``operator_oid IS NULL`` and yield ``operator_name = None``.

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

from sqlalchemy import func, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import ban_records as ban_records_tbl
from db.models import users as users_tbl
from db.oracle.session import session_scope

Row = dict[str, Any]

# 操作人姓名用别名 ``op``：USERS 在同一语句里已被 user_oid 的 join 占用，
# 直接复用会要求 operator_oid == user_oid，把「谁封的」查成「被封的人」。
# 模块级常量保证 select 与 join 用的是同一个 alias 对象。
_operator_tbl = users_tbl.alias("op")


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
        ban_records_tbl.c.oid,
        ban_records_tbl.c.user_oid,
        ban_records_tbl.c.source,
        ban_records_tbl.c.rule_code,
        ban_records_tbl.c.reason,
        ban_records_tbl.c.permanent,
        ban_records_tbl.c.started_at,
        ban_records_tbl.c.expires_at,
        ban_records_tbl.c.operator_oid,
        ban_records_tbl.c.created_at,
    )


def _insert_stmt(
    oid: str,
    user_oid: str,
    source: str,
    rule_code: str | None,
    reason: str | None,
    permanent: bool,
    started_at: datetime,
    expires_at: datetime | None,
    operator_oid: str | None,
):
    return insert(ban_records_tbl).values(
        oid=oid,
        user_oid=user_oid,
        source=source,
        rule_code=rule_code,
        reason=reason,
        permanent=1 if permanent else 0,
        started_at=started_at,
        expires_at=expires_at,
        operator_oid=operator_oid,
    )


def _count_for_user_rule_stmt(user_oid: str, rule_code: str):
    return (
        select(func.count())
        .select_from(ban_records_tbl)
        .where(
            ban_records_tbl.c.user_oid == user_oid,
            ban_records_tbl.c.rule_code == rule_code,
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
    # LEFT JOIN：用户行消失后流水仍要在审计列表里可见（姓名/学号退回 None）。
    # 流水按 user_oid 单值关联，不会放大行数。
    # 第二个 LEFT JOIN 只为操作人姓名（``op``）：规则封禁没有 operator_oid，
    # LEFT 保证这些行照旧出现且 operator_name 为 None。
    return (
        ban_records_tbl.outerjoin(
            users_tbl, ban_records_tbl.c.user_oid == users_tbl.c.oid
        )
        .outerjoin(_operator_tbl, ban_records_tbl.c.operator_oid == _operator_tbl.c.oid)
    )


def _page_stmt(query: str, page: int, size: int):
    """一页流水 + ``COUNT(*) OVER()`` 总数，一次往返。

    ``started_at`` 倒序（新的封禁在上），同一时刻再按 oid 倒序保证分页稳定。
    """
    stmt = select(
        *_columns(),
        users_tbl.c.name.label("user_name"),
        users_tbl.c.business_id.label("user_business_id"),
        _operator_tbl.c.name.label("operator_name"),
        func.count().over().label("total_count"),
    ).select_from(_from_clause())
    conds = _page_filter(query)
    if conds:
        stmt = stmt.where(*conds)
    return (
        stmt.order_by(ban_records_tbl.c.started_at.desc(), ban_records_tbl.c.oid.desc())
        .offset(0 if page == -1 else (page - 1) * size)
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
        "operator_name": mapping["operator_name"],
        "source": mapping["source"],
        "rule_code": mapping["rule_code"],
        "reason": mapping["reason"],
        "permanent": bool(mapping["permanent"]),
        "started_at": mapping["started_at"],
        "expires_at": mapping["expires_at"],
        "operator_oid": mapping["operator_oid"],
        "created_at": mapping["created_at"],
    }


def _total_of(rows: Any) -> int | None:
    """窗口计数；空页时不可观测（``None``），由调用方决定是否补一次 count。"""
    if not rows:
        return None
    return int(rows[0]._mapping["total_count"])


class OracleBanRecordsRepo:
    """``BanRecordsRepo`` over Oracle ADB (``BAN_RECORDS``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def insert(
        self,
        user_oid: str,
        *,
        source: str,
        rule_code: str | None = None,
        reason: str | None = None,
        permanent: bool = False,
        started_at: datetime | None = None,
        expires_at: datetime | None = None,
        operator_oid: str | None = None,
    ) -> str:
        """追加一条封禁流水并返回新 ``_id``。

        ``created_at`` 由本层生成（写入时刻），``started_at`` 允许调用方回填
        （例如补录）；两者语义不同：前者是「这行什么时候被写下」，后者是
        「封禁从什么时候生效」。
        """
        oid = new_oid()
        stmt = _insert_stmt(
            oid,
            normalize_oid(user_oid),
            source,
            rule_code,
            reason,
            permanent,
            started_at if started_at is not None else datetime.now(),
            expires_at,
            normalize_oid(operator_oid) if operator_oid is not None else None,
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return oid

    async def count_for_user_rule(self, user_oid: str, rule_code: str) -> int:
        """该用户在该规则下的历史封禁次数（升级处罚的档位索引）。"""
        stmt = _count_for_user_rule_stmt(normalize_oid(user_oid), rule_code)
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(stmt)).scalar_one()
        return int(total)

    async def list_page(
        self, page: int, size: int, query: str
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


__all__ = ["OracleBanRecordsRepo"]
