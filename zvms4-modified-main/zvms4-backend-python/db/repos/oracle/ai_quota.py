"""Oracle implementation of :class:`db.repos.protocols.AIQuotaRepo`.

SQLAlchemy 2.0 async Core over the ``AI_QUOTA`` table (按 用户 + 月份 + 能力 计的
额度, V024). 行形状是 Mongo 风格的 dict（``"_id"`` = 小写 hex ``OID``）。

为什么额度要落在库里：网关是多进程/多 worker 部署的，把用量放进程内存会让
「每人每月 N 次」变成「每 worker 每月 N 次」。``(user_oid, period, kind)`` 的
唯一索引把额度行收敛成一行，于是扣减可以写成**一条**条件 UPDATE：

    UPDATE ai_quota SET used = used + :amount, updated_at = :ts
    WHERE user_oid = :u AND period = :p AND kind = :k
      AND used + :amount <= limit_count

``rowcount == 1`` 就是「额度够，已扣」，``rowcount == 0`` 就是「不够或行还
不存在」。这条语句在数据库里是原子的，并发请求由行锁串行化——本模块**没有**
「先 SELECT used 再判断再 UPDATE」的读后写，那才是会超卖的形状。

行不存在时（用户第一次用某能力）才走插入：插入与别的请求撞唯一键是正常
并发，吞掉冲突后**重试一次** UPDATE 即可，不必把 500 抛给用户。

``period`` 是 ``YYYYMM`` 的 ``CHAR(6)``：月份是计费口径而不是时间点，用字符串
比较，不需要「按哪个时区截断月初」的隐式规则。

Session ownership mirrors :mod:`db.repos.oracle.users`: with no injected session
each method opens its own through :func:`db.oracle.session.session_scope`, and
writes commit once; an injected session is neither committed nor closed.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import case, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import ai_quota as ai_quota_tbl
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

def _consume_stmt(
    user_oid: str, period: str, kind: str, amount: int, updated_at: datetime
):
    """额度扣减：条件写在 WHERE 里，所以「够不够」由数据库一次裁决。

    ``used + :amount <= limit_count`` 是唯一的闸门；把它放在 WHERE 而不是
    Python 的 if 里，是为了让并发请求在行锁上排队而不是各自读到同一个旧值。
    """
    return (
        update(ai_quota_tbl)
        .where(
            ai_quota_tbl.c.user_oid == user_oid,
            ai_quota_tbl.c.period == period,
            ai_quota_tbl.c.kind == kind,
            ai_quota_tbl.c.used + amount <= ai_quota_tbl.c.limit_count,
        )
        .values(
            used=ai_quota_tbl.c.used + amount,
            updated_at=updated_at,
        )
    )


def _select_stmt(user_oid: str, period: str, kind: str):
    return select(
        ai_quota_tbl.c.oid,
        ai_quota_tbl.c.user_oid,
        ai_quota_tbl.c.period,
        ai_quota_tbl.c.kind,
        ai_quota_tbl.c.used,
        ai_quota_tbl.c.limit_count,
        ai_quota_tbl.c.updated_at,
    ).where(
        ai_quota_tbl.c.user_oid == user_oid,
        ai_quota_tbl.c.period == period,
        ai_quota_tbl.c.kind == kind,
    )


def _insert_stmt(
    oid: str,
    user_oid: str,
    period: str,
    kind: str,
    used: int,
    limit_count: int,
    updated_at: datetime,
):
    return insert(ai_quota_tbl).values(
        oid=oid,
        user_oid=user_oid,
        period=period,
        kind=kind,
        used=used,
        limit_count=limit_count,
        updated_at=updated_at,
    )


def _update_limit_stmt(
    user_oid: str,
    period: str,
    kind: str,
    limit_count: int,
    updated_at: datetime,
):
    return (
        update(ai_quota_tbl)
        .where(
            ai_quota_tbl.c.user_oid == user_oid,
            ai_quota_tbl.c.period == period,
            ai_quota_tbl.c.kind == kind,
        )
        .values(limit_count=limit_count, updated_at=updated_at)
    )


def _reset_stmt(user_oid: str, period: str, kind: str, updated_at: datetime):
    """已用量清零：只动 ``used``，上限是管理员定的，重置不该顺手改掉。"""
    return (
        update(ai_quota_tbl)
        .where(
            ai_quota_tbl.c.user_oid == user_oid,
            ai_quota_tbl.c.period == period,
            ai_quota_tbl.c.kind == kind,
        )
        .values(used=0, updated_at=updated_at)
    )


def _refund_stmt(
    user_oid: str, period: str, kind: str, amount: int, updated_at: datetime
):
    """退还额度：一条 UPDATE 完成「扣到零为止」。

    ``CASE`` 而不是「先 SELECT 再写差值」：后者把读-改-写窗口留给并发请求，
    两个补偿同时算同一个旧值就会把别的调用的用量抹掉。``used < amount`` 时
    归零——表上有 ``used >= 0`` 的 CHECK，补偿不能自己制造一次失败。
    """
    return (
        update(ai_quota_tbl)
        .where(
            ai_quota_tbl.c.user_oid == user_oid,
            ai_quota_tbl.c.period == period,
            ai_quota_tbl.c.kind == kind,
        )
        .values(
            used=case(
                (ai_quota_tbl.c.used >= amount, ai_quota_tbl.c.used - amount),
                else_=0,
            ),
            updated_at=updated_at,
        )
    )


def _row(mapping: Any) -> Row:
    return {
        "_id": mapping["oid"],
        "user_oid": mapping["user_oid"],
        "period": mapping["period"],
        "kind": mapping["kind"],
        "used": int(mapping["used"]),
        "limit_count": int(mapping["limit_count"]),
        "updated_at": mapping["updated_at"],
    }


async def _read_row(
    session: AsyncSession, user_oid: str, period: str, kind: str
) -> Any:
    result = await session.execute(_select_stmt(user_oid, period, kind))
    return result.first()


async def _read_used_limit(
    session: AsyncSession, user_oid: str, period: str, kind: str
) -> tuple[int, int]:
    """扣减成功后的回读：返回 ``(used, limit)`` 给调用方展示剩余额度。"""
    row = await _read_row(session, user_oid, period, kind)
    if row is None:
        return 0, 0
    mapping = row._mapping
    return int(mapping["used"]), int(mapping["limit_count"])


async def _consume_in_session(
    session: AsyncSession,
    user_oid: str,
    period: str,
    kind: str,
    amount: int,
    default_limit: int,
    updated_at: datetime,
) -> tuple[bool, int, int]:
    """在已打开的 session 里完成一次扣减（调用方负责 commit）。"""
    result = await session.execute(
        _consume_stmt(user_oid, period, kind, amount, updated_at)
    )
    if result.rowcount == 1:
        used, limit = await _read_used_limit(session, user_oid, period, kind)
        return True, used, limit

    # 没扣到：要么额度不足（行在），要么行还不存在（首次使用该能力）。
    existing = await _read_row(session, user_oid, period, kind)
    if existing is not None:
        mapping = existing._mapping
        return False, int(mapping["used"]), int(mapping["limit_count"])

    # 首次使用：建行（used = 0），插入放进 savepoint——撞唯一键只回滚这一小段，
    # 外层事务仍然可用，才能接着重试扣减。
    try:
        async with session.begin_nested():
            await session.execute(
                _insert_stmt(
                    new_oid(), user_oid, period, kind, 0, default_limit, updated_at
                )
            )
    except IntegrityError:
        # 并发请求刚刚建好了同一行，语义等价；继续走下面的重试。
        pass

    result = await session.execute(
        _consume_stmt(user_oid, period, kind, amount, updated_at)
    )
    if result.rowcount == 1:
        used, limit = await _read_used_limit(session, user_oid, period, kind)
        return True, used, limit

    existing = await _read_row(session, user_oid, period, kind)
    if existing is None:
        # 行刚建好就被并发删掉（本模块没有删除接口，只可能是人工介入）：
        # 保守拒绝——宁可不给，也不超发。
        return False, 0, default_limit
    mapping = existing._mapping
    return False, int(mapping["used"]), int(mapping["limit_count"])


class OracleAIQuotaRepo:
    """``AIQuotaRepo`` over Oracle ADB (``AI_QUOTA``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def consume(
        self, user_oid: str, period: str, kind: str, amount: int, default_limit: int
    ) -> tuple[bool, int, int]:
        """原子扣减，返回 ``(allowed, used, limit)``；不超卖。

        ``default_limit`` 只在该三元组**首次**出现时生效：之后改限额走
        :meth:`set_limit`，这里绝不覆盖已有行的 ``limit_count``。
        """
        target = normalize_oid(user_oid)
        now = _now()
        async with _session_for(self._session) as (session, owns):
            allowed, used, limit = await _consume_in_session(
                session, target, period, kind, amount, default_limit, now
            )
            if owns:
                await session.commit()
        return allowed, used, limit

    async def get(self, user_oid: str, period: str, kind: str) -> Row | None:
        """取额度行；用户还没用过该能力时返回 ``None``。"""
        async with _session_for(self._session) as (session, _owns):
            row = await _read_row(session, normalize_oid(user_oid), period, kind)
        if row is None:
            return None
        return _row(row._mapping)

    async def set_limit(
        self, user_oid: str, period: str, kind: str, limit_count: int
    ) -> None:
        """设置额度上限；行不存在则建行（``used = 0``）。

        先 UPDATE 再按需 INSERT：行存在时只改 ``limit_count``，**不动** ``used``
        ——管理员调整上限不该把已用额度清零。
        """
        target = normalize_oid(user_oid)
        now = _now()
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(
                _update_limit_stmt(target, period, kind, limit_count, now)
            )
            if result.rowcount == 0:
                try:
                    async with session.begin_nested():
                        await session.execute(
                            _insert_stmt(
                                new_oid(), target, period, kind, 0, limit_count, now
                            )
                        )
                except IntegrityError:
                    # 并发建行：别人先插了，改成更新它（否则这次设置会静默丢失）。
                    await session.execute(
                        _update_limit_stmt(target, period, kind, limit_count, now)
                    )
            if owns:
                await session.commit()

    async def reset_used(self, user_oid: str, period: str, kind: str) -> int:
        """把当月已用量清零（保留 ``limit_count``），返回受影响行数。

        行不存在时不插入：重置的语义是「回到没用量」，本来就没用量，插行只会
        凭空造出一条额度记录。故单条 UPDATE 足够，``rowcount`` 由调用方判断。
        """
        target = normalize_oid(user_oid)
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(_reset_stmt(target, period, kind, _now()))
            if owns:
                await session.commit()
        return int(result.rowcount or 0)

    async def refund(
        self, user_oid: str, period: str, kind: str, amount: int
    ) -> int:
        """退还额度（``used`` 不足时归零），返回受影响行数。

        没有「建行」分支：能退的前提是先扣过，扣过就必然有行。``amount <= 0``
        直接返回 0——不写库也不报错，调用方传 0 只是「没东西可退」。
        """
        if int(amount) <= 0:
            return 0
        target = normalize_oid(user_oid)
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(
                _refund_stmt(target, period, kind, int(amount), _now())
            )
            if owns:
                await session.commit()
        return int(result.rowcount or 0)


__all__ = ["OracleAIQuotaRepo"]
