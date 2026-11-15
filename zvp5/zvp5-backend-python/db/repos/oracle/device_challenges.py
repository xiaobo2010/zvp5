"""Oracle implementation of :class:`db.repos.protocols.DeviceChallengesRepo`.

SQLAlchemy 2.0 async Core over the ``DEVICE_CHALLENGES`` table (异设备登录的一次性
挑战, V014). 行形状是 Mongo 风格的 dict（``"_id"`` = 小写 hex ``OID``）。

为什么这张表存在：用户在**非主学海设备**上登录时，后端不让它直接进去，而是发
一个挑战（明文 token 只回给客户端一次），让对方用「本班团支书发的临时密码」换一
次登录。挑战本身就是一次登录许可，所以：

* 库里只存 ``token_hash``（SHA-256 hex），明文泄露等于许可泄露；
* ``attempts`` 必须落在库里——多 worker 下放进程内存会让「第 3 次封禁」永远
  数不满；
* ``consumed_at`` 让同一挑战不能被用第二次（单次使用）。

并发安全**不靠行锁**：服务层每次都是自己的 ``session_scope()``，锁在返回时就
随 session 关掉了（假锁比没锁更危险，它让人以为读改写是原子的）。真正兜底的是
两条语句级事实：``UPDATE attempts = attempts + 1`` 在库里是原子的，返回值就是
「我是第几次」；``used_at IS NULL`` 守卫让同一个码只可能被消费一次。因此两个并
发请求会拿到不同的计数，只有一个能赢 ``mark_used``。

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
from db.models import device_challenges as device_challenges_tbl
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
        device_challenges_tbl.c.oid,
        device_challenges_tbl.c.token_hash,
        device_challenges_tbl.c.user_oid,
        device_challenges_tbl.c.xuehai_id,
        device_challenges_tbl.c.attempts,
        device_challenges_tbl.c.expires_at,
        device_challenges_tbl.c.consumed_at,
        device_challenges_tbl.c.created_at,
    )


def _insert_stmt(
    oid: str,
    token_hash: str,
    user_oid: str,
    xuehai_id: str,
    expires_at: datetime,
    created_at: datetime,
):
    # ``attempts`` 显式写 0 而不是靠列默认值：计数从 0 开始是业务约定
    # （第一次输错 → 1），把它写在语句里，读代码的人不用回查 DDL。
    return insert(device_challenges_tbl).values(
        oid=oid,
        token_hash=token_hash,
        user_oid=user_oid,
        xuehai_id=xuehai_id,
        attempts=0,
        expires_at=expires_at,
        consumed_at=None,
        created_at=created_at,
    )


def _get_stmt(token_hash: str):
    """按 ``token_hash`` 取行（唯一索引保证至多一行）。

    刻意**不**加 ``FOR UPDATE``：本方法自己开 session，返回时锁已经释放，加
    了也只是骗人的假锁。并发裁决交给 ``increment_attempts`` 的语句级原子自增
    （见模块 docstring）。
    """
    return select(*_columns()).where(
        device_challenges_tbl.c.token_hash == token_hash
    )


def _increment_stmt(oid: str):
    return (
        update(device_challenges_tbl)
        .where(device_challenges_tbl.c.oid == oid)
        .values(attempts=device_challenges_tbl.c.attempts + 1)
    )


def _get_attempts_stmt(oid: str):
    return select(device_challenges_tbl.c.attempts).where(
        device_challenges_tbl.c.oid == oid
    )


def _consume_stmt(oid: str, consumed_at: datetime):
    return (
        update(device_challenges_tbl)
        .where(device_challenges_tbl.c.oid == oid)
        .values(consumed_at=consumed_at)
    )


def _challenge_row(mapping: Any) -> Row:
    return {
        "_id": mapping["oid"],
        "token_hash": mapping["token_hash"],
        "user_oid": mapping["user_oid"],
        "xuehai_id": mapping["xuehai_id"],
        "attempts": int(mapping["attempts"]),
        "expires_at": mapping["expires_at"],
        "consumed_at": mapping["consumed_at"],
        "created_at": mapping["created_at"],
    }


class OracleDeviceChallengesRepo:
    """``DeviceChallengesRepo`` over Oracle ADB (``DEVICE_CHALLENGES``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def insert(
        self,
        token_hash: str,
        user_oid: str,
        xuehai_id: str,
        expires_at: datetime,
    ) -> str:
        """落一条挑战（存哈希），返回新 ``_id``。

        ``token_hash`` 的唯一约束是并发兜底：同一明文 token 只会有一行，重复
        插入抛 ``IntegrityError``，由调用方重新生成（概率极低，不在此重试）。
        """
        oid = new_oid()
        stmt = _insert_stmt(
            oid,
            token_hash,
            normalize_oid(user_oid),
            xuehai_id,
            expires_at,
            _now(),
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return oid

    async def get(self, token_hash: str) -> Row | None:
        """取挑战；不存在返回 ``None``。

        **不加行锁**：调用方各自持有自己的 session，锁不出本方法。并发安全由
        ``UPDATE attempts = attempts + 1`` 的语句级原子性（每次自增拿到唯一计数）
        与 ``mark_used`` / ``consume`` 的 ``used_at IS NULL`` / ``consumed_at``
        守卫保证——重放最多让计数继续往前跑，不会让同一张挑战被用两次。
        """
        stmt = _get_stmt(token_hash)
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return _challenge_row(row._mapping)

    async def increment_attempts(self, oid: str) -> int:
        """失败计数 +1，返回**新**计数。

        ``UPDATE attempts = attempts + 1`` 后回读而不是 ``RETURNING``：两条语句
        省掉了 ``RETURNING INTO`` 对驱动出参的依赖。原子性由自增语句本身保证
        （并发请求各自拿到不同的新值），不依赖调用方持锁。
        """
        target = normalize_oid(oid)
        async with _session_for(self._session) as (session, owns):
            await session.execute(_increment_stmt(target))
            attempts = (
                await session.execute(_get_attempts_stmt(target))
            ).scalar_one_or_none()
            if owns:
                await session.commit()
        return int(attempts) if attempts is not None else 0

    async def consume(self, oid: str) -> None:
        """置 ``consumed_at``（单次使用），无返回。"""
        stmt = _consume_stmt(normalize_oid(oid), _now())
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()


__all__ = ["OracleDeviceChallengesRepo"]
