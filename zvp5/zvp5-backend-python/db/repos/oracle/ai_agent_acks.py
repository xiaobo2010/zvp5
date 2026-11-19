"""Oracle implementation of :class:`db.repos.protocols.AIAgentAcksRepo`.

One row per account in ``AI_AGENT_ACKS`` (V054): the acknowledgement that the
user has read the AI agent first-use safety warning. An empty table means "not
acknowledged yet", so the read side is a single indexed lookup by PK.

Idempotency
-----------
``ack`` is **first-write-wins**: the ``MERGE`` below only has a ``WHEN NOT
MATCHED`` branch, so a repeated call never moves ``acked_at``. Two concurrent
calls can both decide "no row" and race on the PK insert (Oracle may surface
that as ``ORA-00001``); the losing statement is treated as "someone else won"
and the stored row is read back. Both requests therefore return 200 with the
same timestamp — no duplicate row, no 500.

Session ownership
-----------------
``__init__`` takes an optional ``AsyncSession``:

* ``session is None`` (default): each method opens its own through
  :func:`db.oracle.session.session_scope`, and writes commit once.
* ``session`` supplied: the repo uses it as-is and never commits or closes it —
  the caller owns the unit of work.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import normalize_oid
from db.models import ai_agent_acks as acks_tbl
from db.oracle.session import session_scope


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


#: 首次确认的原子 upsert。只有 ``WHEN NOT MATCHED``：已存在的行**不更新**
#: ``acked_at`` —— 那是「首次确认时间」，不是「最近一次点击时间」。
#: 不要改成先 SELECT 再 INSERT 的业务分支：并发双击会撞主键，而这不是错误，
#: 是同一个动作被点两次。
_ACK_MERGE = text(
    """
    MERGE INTO ai_agent_acks target
    USING (SELECT :user_oid AS user_oid FROM dual) source
    ON (target.user_oid = source.user_oid)
    WHEN NOT MATCHED THEN
        INSERT (user_oid, acked_at)
        VALUES (source.user_oid, :at)
    """
)


def _ack_merge_stmt():
    return _ACK_MERGE


def _select_stmt(user_oid: str):
    return select(acks_tbl.c.acked_at).where(acks_tbl.c.user_oid == user_oid)


async def _read(session: AsyncSession, user_oid: str) -> datetime | None:
    row = (await session.execute(_select_stmt(user_oid))).first()
    if row is None:
        return None
    return row._mapping["acked_at"]


class OracleAIAgentAcksRepo:
    """``AIAgentAcksRepo`` over Oracle ADB (``AI_AGENT_ACKS``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def get(self, user_oid: str) -> datetime | None:
        user = normalize_oid(user_oid)
        async with _session_for(self._session) as (session, _owns):
            return await _read(session, user)

    async def ack(self, user_oid: str, now: datetime) -> datetime:
        user = normalize_oid(user_oid)
        async with _session_for(self._session) as (session, owns):
            existing = await _read(session, user)
            if existing is None:
                try:
                    await session.execute(
                        _ack_merge_stmt(), {"user_oid": user, "at": now}
                    )
                except IntegrityError:
                    # 并发竞态：另一事务先插了同一 PK。这里只丢一条 INSERT，
                    # 但 SQLAlchemy 在该语句失败后会把事务标记成「需要回滚」，
                    # 再执行 SELECT 会抛 PendingRollbackError。所以自己开的 session
                    # 先回滚再读回赢家行；注入的 session 归调用方所有，不能替它
                    # rollback（会顺手丢掉它的其它写），只能原样把异常语义留给上层。
                    if not owns:
                        raise
                    await session.rollback()
                existing = await _read(session, user)
            if owns:
                await session.commit()
        return existing if existing is not None else now


__all__ = ["OracleAIAgentAcksRepo"]
