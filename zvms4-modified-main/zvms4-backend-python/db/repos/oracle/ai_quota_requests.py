"""Oracle implementation of :class:`db.repos.protocols.AIQuotaRequestsRepo`.

SQLAlchemy 2.0 async Core over ``AI_QUOTA_REQUESTS`` (配额重置申请, V044). 行形状是
Mongo 风格的 dict（``"_id"`` = 小写 hex ``OID``）。

设计要点（服务层会依赖，改之前先读）：

* **状态迁移是条件 UPDATE**：``decide`` 的 ``WHERE status = 'pending'`` 让「两个
  审批人同时点通过」在数据库里串行化——只有一个能改到，另一个 ``rowcount = 0``
  回 ``None``。本模块刻意**不**做「先 SELECT 判 pending 再 UPDATE」，那正是会让
  同一张申请被批两次（额度被重置两次）的形状。
* **没有 pending 唯一索引**：``(user_oid, period, kind)`` 的查重放在服务层
  （``find_pending``）。写成函数唯一索引要把终态行排除在外，收益不抵复杂度；
  并发双提交的窗口由审批端兜住（见 V044 文件头）。
* **``requested_limit`` 的 NULL ≠ 0**：NULL = 「只要求重置已用量」，0 = 「上限
  设成 0」。仓储层原样往返，不做任何补零。

Session ownership mirrors :mod:`db.repos.oracle.users`: with no injected session
each method opens its own through :func:`db.oracle.session.session_scope`, and
writes commit once; an injected session is neither committed nor closed.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator, Sequence

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import ai_quota_requests as requests_tbl
from db.oracle.session import session_scope

Row = dict[str, Any]

# DB 列宽即契约：超长的理由/备注在写入前截断，不让一次申请因为长字符串整行失败。
MAX_REASON_CHARS = 500
MAX_NOTE_CHARS = 500


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


def _clip(value: str | None, width: int) -> str | None:
    """空白/空串统一成 ``None``：'' 与 NULL 在 Oracle 里本来就分不开。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:width]


def _columns() -> tuple[Any, ...]:
    return (
        requests_tbl.c.oid,
        requests_tbl.c.user_oid,
        requests_tbl.c.period,
        requests_tbl.c.kind,
        requests_tbl.c.status,
        requests_tbl.c.reason,
        requests_tbl.c.requested_limit,
        requests_tbl.c.decided_by,
        requests_tbl.c.decided_at,
        requests_tbl.c.decision_note,
        requests_tbl.c.created_at,
        requests_tbl.c.updated_at,
    )


def _row(mapping: Any) -> Row:
    limit = mapping["requested_limit"]
    return {
        "_id": mapping["oid"],
        "user_oid": mapping["user_oid"],
        "period": mapping["period"],
        "kind": mapping["kind"],
        "status": mapping["status"],
        "reason": mapping["reason"],
        "requested_limit": int(limit) if limit is not None else None,
        "decided_by": mapping["decided_by"],
        "decided_at": mapping["decided_at"],
        "decision_note": mapping["decision_note"],
        "created_at": mapping["created_at"],
        "updated_at": mapping["updated_at"],
    }


class OracleAIQuotaRequestsRepo:
    """配额重置申请（``AI_QUOTA_REQUESTS``）。"""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def create(
        self,
        user_oid: str,
        period: str,
        kind: str,
        *,
        reason: str | None = None,
        requested_limit: int | None = None,
    ) -> Row:
        """写一条 pending 申请；不查重（查重是服务层的职责，见 :meth:`find_pending`）。"""
        oid = new_oid()
        now = _now()
        stmt = insert(requests_tbl).values(
            oid=oid,
            user_oid=normalize_oid(user_oid),
            period=str(period)[:6],
            kind=str(kind)[:16],
            status="pending",
            reason=_clip(reason, MAX_REASON_CHARS),
            requested_limit=None if requested_limit is None else int(requested_limit),
            created_at=now,
            updated_at=now,
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return {
            "_id": oid,
            "user_oid": normalize_oid(user_oid),
            "period": str(period)[:6],
            "kind": str(kind)[:16],
            "status": "pending",
            "reason": _clip(reason, MAX_REASON_CHARS),
            "requested_limit": None
            if requested_limit is None
            else int(requested_limit),
            "decided_by": None,
            "decided_at": None,
            "decision_note": None,
            "created_at": now,
            "updated_at": now,
        }

    async def get(self, oid: str) -> Row | None:
        stmt = select(*_columns()).where(requests_tbl.c.oid == normalize_oid(oid))
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        return _row(row._mapping) if row is not None else None

    async def find_pending(
        self, user_oid: str, period: str, kind: str
    ) -> Row | None:
        """同一用户同一 period 同一 kind 的 pending 行（有多行也只回最新一条）。"""
        stmt = (
            select(*_columns())
            .where(
                requests_tbl.c.user_oid == normalize_oid(user_oid),
                requests_tbl.c.period == str(period)[:6],
                requests_tbl.c.kind == str(kind)[:16],
                requests_tbl.c.status == "pending",
            )
            .order_by(requests_tbl.c.created_at.desc())
        )
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        return _row(row._mapping) if row is not None else None

    async def list_page(
        self, status: str | None, *, page: int = 1, size: int = 20
    ) -> tuple[list[Row], int]:
        """按 ``created_at`` 倒序一页 + 总数（两条语句，不做窗口函数回读）。"""
        where = [] if status is None else [requests_tbl.c.status == str(status)]
        page = max(1, int(page))
        size = max(1, int(size))
        offset = (page - 1) * size
        page_stmt = (
            select(*_columns())
            .where(*where)
            # 同一秒提交的两条：补 oid 才能让分页稳定（否则翻页可能重复/漏行）。
            .order_by(requests_tbl.c.created_at.desc(), requests_tbl.c.oid.desc())
            .offset(offset)
            .fetch(size)
        )
        count_stmt = select(func.count()).select_from(requests_tbl).where(*where)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(page_stmt)).all()
            total = int((await session.execute(count_stmt)).scalar_one() or 0)
        return [_row(row._mapping) for row in rows], total

    async def list_for_user(self, user_oid: str, limit: int = 10) -> list[Row]:
        stmt = (
            select(*_columns())
            .where(requests_tbl.c.user_oid == normalize_oid(user_oid))
            .order_by(requests_tbl.c.created_at.desc(), requests_tbl.c.oid.desc())
            .fetch(max(1, int(limit)))
        )
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_row(row._mapping) for row in rows]

    async def decide(
        self,
        oid: str,
        status: str,
        *,
        decided_by: str,
        note: str | None = None,
    ) -> Row | None:
        """``pending → status`` 的条件更新；返回更新后的行或 ``None``。

        ``rowcount == 0`` 有两种可能（行不存在 / 已经不是 pending），都回 ``None``
        ——调用方已经 ``get`` 过一次，能自己区分 404 与 409。
        """
        now = _now()
        stmt = (
            update(requests_tbl)
            .where(
                requests_tbl.c.oid == normalize_oid(oid),
                requests_tbl.c.status == "pending",
            )
            .values(
                status=str(status)[:16],
                decided_by=normalize_oid(decided_by),
                decided_at=now,
                decision_note=_clip(note, MAX_NOTE_CHARS),
                updated_at=now,
            )
        )
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
            if int(result.rowcount or 0) == 0:
                return None
            row = (
                await session.execute(
                    select(*_columns()).where(
                        requests_tbl.c.oid == normalize_oid(oid)
                    )
                )
            ).first()
        return _row(row._mapping) if row is not None else None

    async def pending_user_oids(
        self, user_oids: Sequence[str], period: str
    ) -> set[str]:
        """这批用户里当月还有 pending 申请的 oid 集合。

        空输入直接回空集：Oracle 的 ``IN ()`` 是语法错误，不是「没有匹配」。
        """
        targets = [normalize_oid(oid) for oid in user_oids]
        if not targets:
            return set()
        stmt = (
            select(requests_tbl.c.user_oid)
            .where(
                requests_tbl.c.user_oid.in_(targets),
                requests_tbl.c.period == str(period)[:6],
                requests_tbl.c.status == "pending",
            )
            .distinct()
        )
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return {str(row._mapping["user_oid"]) for row in rows}


__all__ = ["OracleAIQuotaRequestsRepo"]
