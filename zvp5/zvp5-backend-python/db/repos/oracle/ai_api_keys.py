"""Oracle implementation of :class:`db.repos.protocols.AIApiKeysRepo`.

SQLAlchemy 2.0 async Core over the ``AI_API_KEYS`` table (独立 API key, V030).
行形状是 Mongo 风格的 dict（``"_id"`` = 小写 hex ``OID``）。

三个口径说明：

* ``token_hash`` 是 ``sha256(token).hexdigest()``，**单向**：本模块没有任何
  解密路径，读出来的行也拿不到明文 key。校验是唯一索引上的一次等值查询，不是
  逐行比较。
* ``expires_at`` 用 :data:`UNSET` 哨兵区分「不改」与「显式清空（永不过期）」，
  与 ``AI_CREDENTIALS`` 同口径；直接写 ``None`` 会把「不传」误当成「清空」。
* ``touch`` 只写 ``last_used_at``：调用方按分钟采样调用它，不参与任何判定。

Session ownership mirrors :mod:`db.repos.oracle.ai_ocr_engines`: with no injected
session each method opens its own through :func:`db.oracle.session.session_scope`,
and writes commit once; an injected session is neither committed nor closed.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import ai_api_keys as ai_api_keys_tbl
from db.oracle.session import session_scope
from db.repos.protocols import UNSET, DuplicateApiKey

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

def _columns():
    return (
        ai_api_keys_tbl.c.oid,
        ai_api_keys_tbl.c.user_oid,
        ai_api_keys_tbl.c.name,
        ai_api_keys_tbl.c.token_hash,
        ai_api_keys_tbl.c.key_hint,
        ai_api_keys_tbl.c.enabled,
        ai_api_keys_tbl.c.expires_at,
        ai_api_keys_tbl.c.last_used_at,
        ai_api_keys_tbl.c.created_at,
    )


def _list_stmt(user_oid: str):
    """一个用户的全部 key，新的在前（``created_at`` 同秒时用 ``oid`` 定序）。"""
    return (
        select(*_columns())
        .where(ai_api_keys_tbl.c.user_oid == user_oid)
        .order_by(
            ai_api_keys_tbl.c.created_at.desc(),
            ai_api_keys_tbl.c.oid.desc(),
        )
    )


def _count_stmt(user_oid: str):
    return (
        select(func.count())
        .select_from(ai_api_keys_tbl)
        .where(ai_api_keys_tbl.c.user_oid == user_oid)
    )


def _select_by_oid_stmt(oid: str):
    return select(*_columns()).where(ai_api_keys_tbl.c.oid == oid)


def _select_by_hash_stmt(token_hash: str):
    """认证入口：唯一索引上的等值查询（``token_hash`` 全局唯一）。"""
    return select(*_columns()).where(ai_api_keys_tbl.c.token_hash == token_hash)


def _insert_stmt(
    *,
    oid: str,
    user_oid: str,
    name: str,
    token_hash: str,
    key_hint: str,
    enabled: bool,
    expires_at: datetime | None,
    created_at: datetime,
):
    """``created_at`` 显式绑定：列上的 SYSTIMESTAMP 默认值在仓储层拿不到回读值。"""
    return insert(ai_api_keys_tbl).values(
        oid=oid,
        user_oid=user_oid,
        name=name,
        token_hash=token_hash,
        key_hint=key_hint,
        enabled=1 if enabled else 0,
        expires_at=expires_at,
        last_used_at=None,
        created_at=created_at,
    )


def _update_values(
    *,
    name: str | None = None,
    enabled: bool | None = None,
    expires_at: Any = UNSET,
) -> dict:
    """只收「传了的字段」：``None`` 对 name/enabled 表示不改。

    ``expires_at`` 例外地看 :data:`UNSET`：``None`` 是「永不过期」这个**真实
    值**，不能和「没传」混在一起。
    """
    values: dict[str, Any] = {}
    if name is not None:
        values["name"] = name
    if enabled is not None:
        values["enabled"] = 1 if enabled else 0
    if expires_at is not UNSET:
        values["expires_at"] = expires_at
    return values


def _update_stmt(oid: str, values: dict):
    return (
        update(ai_api_keys_tbl)
        .where(ai_api_keys_tbl.c.oid == oid)
        .values(**values)
    )


def _delete_stmt(oid: str):
    return delete(ai_api_keys_tbl).where(ai_api_keys_tbl.c.oid == oid)


def _touch_stmt(oid: str, used_at: datetime):
    return (
        update(ai_api_keys_tbl)
        .where(ai_api_keys_tbl.c.oid == oid)
        .values(last_used_at=used_at)
    )


def _row(mapping: Any) -> Row:
    return {
        "_id": mapping["oid"],
        "user_oid": mapping["user_oid"],
        "name": mapping["name"],
        "token_hash": mapping["token_hash"],
        "key_hint": mapping["key_hint"],
        "enabled": bool(mapping["enabled"]),
        "expires_at": mapping["expires_at"],
        "last_used_at": mapping["last_used_at"],
        "created_at": mapping["created_at"],
    }


class OracleAIApiKeysRepo:
    """``AIApiKeysRepo`` over Oracle ADB (``AI_API_KEYS``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def list_for(self, user_oid: str) -> list[Row]:
        """一个用户的全部 key（含 disabled / 已过期），新的在前。"""
        async with _session_for(self._session) as (session, _owns):
            rows = (
                await session.execute(_list_stmt(normalize_oid(user_oid)))
            ).all()
        return [_row(row._mapping) for row in rows]

    async def count_for(self, user_oid: str) -> int:
        """该用户已有多少把 key（每用户上限由服务层判）。"""
        async with _session_for(self._session) as (session, _owns):
            result = await session.execute(_count_stmt(normalize_oid(user_oid)))
        return int(result.scalar_one() or 0)

    async def get_by_oid(self, oid: str) -> Row | None:
        async with _session_for(self._session) as (session, _owns):
            row = (
                await session.execute(_select_by_oid_stmt(normalize_oid(oid)))
            ).first()
        if row is None:
            return None
        return _row(row._mapping)

    async def get_by_hash(self, token_hash: str) -> Row | None:
        """认证查询：``WHERE token_hash = :hash``（唯一索引，不含任何全表扫描）。"""
        async with _session_for(self._session) as (session, _owns):
            row = (
                await session.execute(_select_by_hash_stmt(str(token_hash)))
            ).first()
        if row is None:
            return None
        return _row(row._mapping)

    async def insert(
        self,
        *,
        user_oid: str,
        name: str,
        token_hash: str,
        key_hint: str,
        enabled: bool = True,
        expires_at: datetime | None = None,
    ) -> str:
        """插入一把 key 并返回新 ``_id``；撞唯一键抛 :class:`DuplicateApiKey`。"""
        oid = new_oid()
        stmt = _insert_stmt(
            oid=oid,
            user_oid=normalize_oid(user_oid),
            name=name,
            token_hash=str(token_hash),
            key_hint=key_hint,
            enabled=bool(enabled),
            expires_at=expires_at,
            created_at=_now(),
        )
        async with _session_for(self._session) as (session, owns):
            try:
                # savepoint：唯一键冲突只回滚这一条 INSERT，外侧事务仍然可用。
                async with session.begin_nested():
                    await session.execute(stmt)
            except IntegrityError as exc:
                raise DuplicateApiKey(name=name) from exc
            if owns:
                await session.commit()
        return oid

    async def update(
        self,
        oid: str,
        *,
        name: str | None = None,
        enabled: bool | None = None,
        expires_at: Any = UNSET,
    ) -> bool:
        """只更新传入的字段；返回是否命中（``False`` = 行不存在）。"""
        values = _update_values(
            name=name, enabled=enabled, expires_at=expires_at
        )
        if not values:
            # 没有任何字段要改：不发空 UPDATE（Oracle 会报错），也算「命中」由
            # 调用方先 get 过——这里返回 True 只为不把 no-op 当成 404。
            return True
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(
                _update_stmt(normalize_oid(oid), values)
            )
            if owns:
                await session.commit()
        return result.rowcount > 0

    async def delete(self, oid: str) -> bool:
        """删除一把 key；``True`` 表示真的删掉了。"""
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(_delete_stmt(normalize_oid(oid)))
            if owns:
                await session.commit()
        return result.rowcount > 0

    async def touch(self, oid: str) -> None:
        """只写 ``last_used_at``（调用方已按分钟采样，不需要额外节流）。"""
        async with _session_for(self._session) as (session, owns):
            await session.execute(_touch_stmt(normalize_oid(oid), _now()))
            if owns:
                await session.commit()


__all__ = ["OracleAIApiKeysRepo", "DuplicateApiKey"]
