"""Oracle implementation of :class:`db.repos.protocols.TempPasswordsRepo`.

SQLAlchemy 2.0 async Core over the ``TEMP_LOGIN_PASSWORDS`` table (一次性临时
密码, V014). 行形状是 Mongo 风格的 dict（``"_id"`` = 小写 hex ``OID``）。

设计要点（服务层会依赖，改之前先读）：

* **只回传哈希**。明文密码在创建响应里出现一次，之后库里只有 bcrypt 哈希；
  所以这一层没有「取回密码」的方法——也不该有。
* **单次使用**由 :meth:`mark_used` 的守卫（``used_at IS NULL``）保证：两个并发
  请求读到同一个码时，只有一个 UPDATE 能命中，另一个 ``rowcount = 0``。
* **过期**判定把 ``now`` 作为绑定参数传入（与 ``bindings.py`` 一致用 naive
  ``datetime.now()``），而不是写 ``SYSTIMESTAMP``：Oracle 的 ``SYSTIMESTAMP``
  带时区偏移，和 ``TIMESTAMP`` 列的 naive 值比较会走隐式转换，测试里也没法钉住
  时间点。
* 三个时间戳（used/revoked/expired）保留各自的事实，不合并成状态列：排查
  「这个码为什么不能用」需要知道**是**哪一种。

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
from db.models import temp_login_passwords as temp_passwords_tbl
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
        temp_passwords_tbl.c.oid,
        temp_passwords_tbl.c.target_user_oid,
        temp_passwords_tbl.c.created_by_oid,
        temp_passwords_tbl.c.code_hash,
        temp_passwords_tbl.c.creator_xuehai_id,
        temp_passwords_tbl.c.expires_at,
        temp_passwords_tbl.c.used_at,
        temp_passwords_tbl.c.revoked_at,
        temp_passwords_tbl.c.created_at,
    )


def _insert_stmt(
    oid: str,
    target_user_oid: str,
    created_by_oid: str,
    code_hash: str,
    creator_xuehai_id: str | None,
    expires_at: datetime,
    created_at: datetime,
):
    return insert(temp_passwords_tbl).values(
        oid=oid,
        target_user_oid=target_user_oid,
        created_by_oid=created_by_oid,
        code_hash=code_hash,
        creator_xuehai_id=creator_xuehai_id,
        expires_at=expires_at,
        used_at=None,
        revoked_at=None,
        created_at=created_at,
    )


def _list_active_stmt(target_user_oid: str, now: datetime, limit: int):
    """未用 / 未撤销 / 未过期的码，新的在前。

    三个条件缺一不可：只看 ``used_at`` 会把过期码列出来，只看 ``expires_at``
    会把已用过的码列出来——管理员据此判断「还能不能再用」时会直接被误导。
    """
    return (
        select(*_columns())
        .where(
            temp_passwords_tbl.c.target_user_oid == target_user_oid,
            temp_passwords_tbl.c.used_at.is_(None),
            temp_passwords_tbl.c.revoked_at.is_(None),
            temp_passwords_tbl.c.expires_at > now,
        )
        .order_by(
            temp_passwords_tbl.c.created_at.desc(),
            temp_passwords_tbl.c.oid.desc(),
        )
        .limit(limit)
    )


def _mark_used_stmt(oid: str, used_at: datetime):
    """仅当 ``used_at IS NULL`` 时置位——单次使用的原子裁决。

    ``rowcount = 0`` 说明这个码已经被别人用掉了（并发或重放），调用方必须当
    失败处理；这里绝不「先查后写」。
    """
    return (
        update(temp_passwords_tbl)
        .where(
            temp_passwords_tbl.c.oid == oid,
            temp_passwords_tbl.c.used_at.is_(None),
        )
        .values(used_at=used_at)
    )


def _revoke_expired_stmt(cutoff: datetime, revoked_at: datetime):
    """把已过期且还没被标记过的码置 ``revoked_at``（清理用，返回行数）。

    只碰 ``used_at IS NULL AND revoked_at IS NULL`` 的行：已用过的码保持原样，
    它的 ``used_at`` 本身就是审计证据，不能被撤销时间覆盖掉。
    """
    return (
        update(temp_passwords_tbl)
        .where(
            temp_passwords_tbl.c.used_at.is_(None),
            temp_passwords_tbl.c.revoked_at.is_(None),
            temp_passwords_tbl.c.expires_at < cutoff,
        )
        .values(revoked_at=revoked_at)
    )


def _revoke_open_for_user_stmt(target_user_oid: str, now: datetime):
    """把该用户**仍然可用**的码全部作废（新码签发时的取代动作）。

    语义是「被取代而失效」：写 ``used_at`` 而不是 ``revoked_at``，因为
    ``revoked_at`` 留给「过期清理/管理员撤销」这条独立事实，而这里的事实是
    「它被同一用户的新码替代，从此不该再被接受」。置位后 ``list_active_for_user``
    与 ``_record_usable`` 都会排除它，等价于不可用。

    只碰未用、未撤销、未过期的行：已经用掉/被撤销的码不改写，它们的原时间戳
    是审计证据。

    ``NO_PARALLEL`` 不是性能调优，是正确性：生产 ADB 的会话带
    ``pdml_enabled=YES``（``PARALLEL_DEGREE_POLICY=AUTO``），这条 UPDATE 会被
    自动并行执行，随后同一事务里的 INSERT 立刻撞 ``ORA-12838``（签发临时密码
    500 的真凶，空表也复现）。串行化这一条换取「作废+插入」仍在同一事务里，
    比拆事务更安全——拆开会让两个并发签发都留下有效码。
    """
    return (
        update(temp_passwords_tbl)
        .where(
            temp_passwords_tbl.c.target_user_oid == target_user_oid,
            temp_passwords_tbl.c.used_at.is_(None),
            temp_passwords_tbl.c.revoked_at.is_(None),
            temp_passwords_tbl.c.expires_at > now,
        )
        .values(used_at=now)
        .prefix_with("/*+ NO_PARALLEL */", dialect="oracle")
    )


def _password_row(mapping: Any) -> Row:
    return {
        "_id": mapping["oid"],
        "target_user_oid": mapping["target_user_oid"],
        "created_by_oid": mapping["created_by_oid"],
        "code_hash": mapping["code_hash"],
        "creator_xuehai_id": mapping["creator_xuehai_id"],
        "expires_at": mapping["expires_at"],
        "used_at": mapping["used_at"],
        "revoked_at": mapping["revoked_at"],
        "created_at": mapping["created_at"],
    }


class OracleTempPasswordsRepo:
    """``TempPasswordsRepo`` over Oracle ADB (``TEMP_LOGIN_PASSWORDS``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def insert(
        self,
        target_user_oid: str,
        created_by_oid: str,
        code_hash: str,
        creator_xuehai_id: str | None,
        expires_at: datetime,
    ) -> str:
        """落一条临时密码（已哈希），返回新 ``_id``。"""
        oid = new_oid()
        stmt = _insert_stmt(
            oid,
            normalize_oid(target_user_oid),
            normalize_oid(created_by_oid),
            code_hash,
            creator_xuehai_id,
            expires_at,
            _now(),
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return oid

    async def issue_for_user(
        self,
        target_user_oid: str,
        created_by_oid: str,
        code_hash: str,
        creator_xuehai_id: str | None,
        expires_at: datetime,
    ) -> str:
        """Revoke the target's open codes and insert the new one atomically.

        The two statements must share one transaction: a failure between them
        would leave the user with no valid code, while two concurrent issuers
        could both revoke nothing and leave two live codes.
        """
        oid = new_oid()
        now = _now()
        target = normalize_oid(target_user_oid)
        async with _session_for(self._session) as (session, owns):
            await session.execute(_revoke_open_for_user_stmt(target, now))
            await session.execute(
                _insert_stmt(
                    oid,
                    target,
                    normalize_oid(created_by_oid),
                    code_hash,
                    creator_xuehai_id,
                    expires_at,
                    now,
                )
            )
            if owns:
                await session.commit()
        return oid

    async def list_active_for_user(
        self, target_user_oid: str, limit: int = 10
    ) -> list[Row]:
        """该用户此刻仍然可用的临时密码，新的在前（最多 ``limit`` 条）。"""
        stmt = _list_active_stmt(
            normalize_oid(target_user_oid), _now(), int(limit)
        )
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_password_row(row._mapping) for row in rows]

    async def mark_used(self, oid: str) -> int:
        """把码标记为已使用，返回受影响行数（``0`` = 已被用掉/不存在）。"""
        stmt = _mark_used_stmt(normalize_oid(oid), _now())
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return int(result.rowcount)

    async def revoke_expired(self, cutoff: datetime) -> int:
        """把 ``expires_at < cutoff`` 且未用未撤销的码置撤销，返回行数。"""
        now = _now()
        stmt = _revoke_expired_stmt(cutoff, now)
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return int(result.rowcount)

    async def revoke_open_for_user(
        self, target_user_oid: str, now: datetime | None = None
    ) -> int:
        """作废该用户所有仍可用的临时密码，返回受影响行数。

        签发新码前调用：同一时刻用户手里只该有一个有效码，否则「旧码明明正确
        却被判错」会把设备刷到永久封禁。
        """
        at = now or _now()
        stmt = _revoke_open_for_user_stmt(normalize_oid(target_user_oid), at)
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return int(result.rowcount)


__all__ = ["OracleTempPasswordsRepo"]
