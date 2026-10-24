"""Oracle implementation of :class:`db.repos.protocols.AICredentialsRepo`.

SQLAlchemy 2.0 async Core over the ``AI_CREDENTIALS`` table (AI provider 凭据,
V023). 行形状是 Mongo 风格的 dict（``"_id"`` = 小写 hex ``OID``）。

两类凭据同表：``personal``（管理员自己的 key）与 ``shared``（所有人可用的通用
key）。它们的字段与读取路径完全一样，区别只有两点：

* ``owner_oid``：personal 是拥有者，shared 固定是
  :data:`db.repos.protocols.SHARED_OWNER_OID`（24 个 0 的哨兵）。**不用 NULL**：
  Oracle 唯一索引里 NULL 互不相等，``owner_oid`` 可空就等于
  ``(scope, owner_oid, name)`` 对共享项不生效。
* ``scope``：决定可见性。:meth:`OracleAICredentialsRepo.list_visible` 取「自己的
  personal + 全部 shared」，personal 排在前面（``CASE`` 而不是按字符串排序——
  按字符串 ``'shared' > 'personal'`` 会把共享项顶到上面）。

密钥只以密文形态存在：``api_key_cipher`` 是 AES-GCM 密文的 base64，明文既不落
库也不进日志；``key_hint`` 只留末四位用于展示，不是解密依据。

``create`` 撞唯一键时**不静默吞**：向上抛 :class:`DuplicateCredential`，由服务
层转成 409。否则管理员会以为新 key 存下来了，实际用的还是旧 key。

Session ownership mirrors :mod:`db.repos.oracle.users`: with no injected session
each method opens its own through :func:`db.oracle.session.session_scope`, and
writes commit once; an injected session is neither committed nor closed.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import case, delete, insert, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import ai_credentials as ai_credentials_tbl
from db.oracle.session import session_scope
from db.repos.protocols import SHARED_OWNER_OID, UNSET

Row = dict[str, Any]

_logger = logging.getLogger(__name__)

# ``expires_at`` 的「不改」哨兵：该列是本表唯一需要区分「不改」与「置空」的字段
# （清除到期是管理员的正常操作），所以它的默认值不是 None 而是 :data:`UNSET`。
_UNSET = UNSET


class DuplicateCredential(Exception):
    """``(scope, owner_oid, name)`` 已存在（Oracle -1 → IntegrityError）。

    服务层据此返回 409。带 ``name`` / ``scope`` 是因为错误消息要说清「哪个名字
    撞了」——只回一句「冲突」管理员没法判断该改名还是该改哪一行。
    """

    def __init__(self, name: str, scope: str) -> None:
        self.name = name
        self.scope = scope
        super().__init__(f"credential already exists: scope={scope!r} name={name!r}")


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

def _personal_first():
    """``ORDER BY`` 的排序键：personal 0、其余 1。

    不能用 ``ORDER BY scope DESC`` —— 那是按字符串比较，``'shared' > 'personal'``
    正好把共享项排到个人项前面。``CASE`` 显式声明「个人优先」这个业务口径。
    """
    return case({"personal": 0}, value=ai_credentials_tbl.c.scope, else_=1)


def _columns():
    return (
        ai_credentials_tbl.c.oid,
        ai_credentials_tbl.c.scope,
        ai_credentials_tbl.c.owner_oid,
        ai_credentials_tbl.c.name,
        ai_credentials_tbl.c.kind,
        ai_credentials_tbl.c.base_url,
        ai_credentials_tbl.c.models_json,
        ai_credentials_tbl.c.headers_json,
        ai_credentials_tbl.c.api_path,
        ai_credentials_tbl.c.api_key_cipher,
        ai_credentials_tbl.c.key_hint,
        ai_credentials_tbl.c.enabled,
        ai_credentials_tbl.c.created_at,
        ai_credentials_tbl.c.updated_at,
        ai_credentials_tbl.c.expires_at,
    )


def _select_stmt(oid: str):
    return select(*_columns()).where(ai_credentials_tbl.c.oid == oid)


def _list_visible_stmt(user_oid: str):
    """自己的 personal + 全部 shared（含 disabled，过滤留给服务层）。"""
    return (
        select(*_columns())
        .where(
            or_(
                ai_credentials_tbl.c.owner_oid == user_oid,
                ai_credentials_tbl.c.scope == "shared",
            )
        )
        .order_by(_personal_first(), ai_credentials_tbl.c.name.asc())
    )


def _list_all_stmt():
    return select(*_columns()).order_by(
        _personal_first(),
        ai_credentials_tbl.c.name.asc(),
        ai_credentials_tbl.c.oid.asc(),
    )


def _insert_stmt(
    *,
    oid: str,
    scope: str,
    owner_oid: str,
    name: str,
    kind: str,
    base_url: str,
    models: list[str],
    api_key_cipher: str,
    key_hint: str | None,
    enabled: bool,
    created_at: datetime,
    updated_at: datetime,
    headers: dict[str, str] | None = None,
    api_path: str | None = None,
    expires_at: datetime | None = None,
):
    # ``models`` 直接给 list：``JSONText`` 负责 ``json.dumps``，落库是 JSON 字符串，
    # 不是 Python repr（后者会被 ``IS JSON`` 约束挡下来）。``headers`` 显式 dumps，
    # 与读取侧「坏 JSON 按空跳过」的宽容解析对称。
    return insert(ai_credentials_tbl).values(
        oid=oid,
        scope=scope,
        owner_oid=owner_oid,
        name=name,
        kind=kind,
        base_url=base_url,
        models_json=models,
        headers_json=(
            json.dumps(headers, ensure_ascii=False) if headers is not None else None
        ),
        api_path=api_path,
        api_key_cipher=api_key_cipher,
        key_hint=key_hint,
        enabled=1 if enabled else 0,
        created_at=created_at,
        updated_at=updated_at,
        # 显式绑定（含 None）：NULL 是「永不过期」这个业务事实，不是「没传」。
        expires_at=expires_at,
    )


def _update_values(
    *,
    name: str | None = None,
    base_url: str | None = None,
    models: list[str] | None = None,
    api_key_cipher: str | None = None,
    key_hint: str | None = None,
    enabled: bool | None = None,
    headers: dict[str, str] | None = None,
    api_path: str | None = None,
    expires_at: datetime | None = _UNSET,
) -> dict[str, Any]:
    """只把**非 None** 的字段放进 UPDATE 的 SET。

    ``None`` 在本协议里表示「不改」，不是「置空」：``key_hint`` 这类可空列若
    允许置空，就需要一个与 None 不同的哨兵，而协议刻意没引入它——想换 key 就
    连 ``api_key_cipher`` 一起给。``headers={}`` 是「清空自定义头」的显式表达，
    与「不改」区分开（落库是 ``"{}"``，读回来仍是空 dict）。

    ``expires_at`` 是唯一的例外：清除到期（``None``）是管理员的正常操作，所以
    它的默认值是 :data:`UNSET`（不改），``None`` 明确表示「置 NULL」。
    """
    values: dict[str, Any] = {}
    if name is not None:
        values["name"] = name
    if base_url is not None:
        values["base_url"] = base_url
    if models is not None:
        values["models_json"] = models
    if api_key_cipher is not None:
        values["api_key_cipher"] = api_key_cipher
    if key_hint is not None:
        values["key_hint"] = key_hint
    if enabled is not None:
        values["enabled"] = 1 if enabled else 0
    if headers is not None:
        values["headers_json"] = json.dumps(headers, ensure_ascii=False)
    if api_path is not None:
        values["api_path"] = api_path
    if expires_at is not _UNSET:
        values["expires_at"] = expires_at
    return values


def _update_stmt(oid: str, values: dict[str, Any], updated_at: datetime):
    """单条 UPDATE：命中的字段 + ``updated_at``，一次往返。

    ``updated_at`` 无条件刷新：即使这次没有字段变化（空 ``values``），被调用的
    ``update`` 也是一次「管理员确认过这行」的事实，让它留下时间戳比悄悄返回
    ``False`` 更有用。
    """
    return (
        update(ai_credentials_tbl)
        .where(ai_credentials_tbl.c.oid == oid)
        .values(**values, updated_at=updated_at)
    )


def _delete_stmt(oid: str):
    return delete(ai_credentials_tbl).where(ai_credentials_tbl.c.oid == oid)


# ---------------------------------------------------------------------------
# row mapping
# ---------------------------------------------------------------------------

def _models_list(raw: Any) -> list[str]:
    """``MODELS_JSON`` → ``list[str]``（CLOB 可能以 LOB / str / list 形态回来）。"""
    if raw is None:
        return []
    if hasattr(raw, "read"):
        raw = raw.read()
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            # 约束保证入库的是合法 JSON；真出现坏串时按空列表处理而不是让整个
            # 凭据列表 500 —— 一个 provider 的模型目录坏了不该拖垮其它 provider。
            _logger.warning("ai_credentials.models_json 不是合法 JSON，按空列表处理")
            return []
    else:
        parsed = raw
    if not isinstance(parsed, (list, tuple)):
        return []
    return [str(item) for item in parsed]


def _row(mapping: Any) -> Row:
    return {
        "_id": mapping["oid"],
        "scope": mapping["scope"],
        "owner_oid": mapping["owner_oid"],
        "name": mapping["name"],
        "kind": mapping["kind"],
        "base_url": mapping["base_url"],
        "models": _models_list(mapping["models_json"]),
        "headers_json": mapping["headers_json"],
        "api_path": mapping["api_path"],
        "api_key_cipher": mapping["api_key_cipher"],
        "key_hint": mapping["key_hint"],
        "enabled": bool(mapping["enabled"]),
        "created_at": mapping["created_at"],
        "updated_at": mapping["updated_at"],
        "expires_at": mapping["expires_at"],
    }


class OracleAICredentialsRepo:
    """``AICredentialsRepo`` over Oracle ADB (``AI_CREDENTIALS``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def list_visible(self, user_oid: str) -> list[Row]:
        """自己的 personal + 全部 shared，personal 在前、同类按名字。"""
        stmt = _list_visible_stmt(normalize_oid(user_oid))
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_row(row._mapping) for row in rows]

    async def list_all(self) -> list[Row]:
        """全部凭据（管理员视图），personal 在前、同类按名字。"""
        stmt = _list_all_stmt()
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_row(row._mapping) for row in rows]

    async def get(self, oid: str) -> Row | None:
        """取一行；不存在返回 ``None``。"""
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(_select_stmt(normalize_oid(oid)))).first()
        if row is None:
            return None
        return _row(row._mapping)

    async def create(
        self,
        *,
        scope: str,
        owner_oid: str,
        name: str,
        kind: str,
        base_url: str,
        models: list[str],
        api_key_cipher: str,
        key_hint: str | None = None,
        enabled: bool = True,
        headers: dict[str, str] | None = None,
        api_path: str | None = None,
        expires_at: datetime | None = None,
    ) -> str:
        """插入一行并返回新 ``_id``；撞唯一键抛 :class:`DuplicateCredential`。

        ``shared`` 行的 owner 在这里被强制改成哨兵：调用方传什么都一样。把这条
        规则放在仓储而不是服务层，是因为它是**唯一索引能否生效**的前提——任何
        写入路径（含将来的脚本/运维手工调用）都不能绕开它。
        """
        oid = new_oid()
        owner = SHARED_OWNER_OID if scope == "shared" else normalize_oid(owner_oid)
        now = _now()
        stmt = _insert_stmt(
            oid=oid,
            scope=scope,
            owner_oid=owner,
            name=name,
            kind=kind,
            base_url=base_url,
            models=list(models),
            api_key_cipher=api_key_cipher,
            key_hint=key_hint,
            enabled=enabled,
            created_at=now,
            updated_at=now,
            headers=headers,
            api_path=api_path,
            expires_at=expires_at,
        )
        async with _session_for(self._session) as (session, owns):
            try:
                # savepoint：唯一键冲突只回滚这一条 INSERT，外层事务（可能是调用
                # 方传进来的那个）仍然可用，服务层还有机会写审计/返回 409。
                async with session.begin_nested():
                    await session.execute(stmt)
            except IntegrityError as exc:
                raise DuplicateCredential(name=name, scope=scope) from exc
            if owns:
                await session.commit()
        return oid

    async def update(
        self,
        oid: str,
        *,
        name: str | None = None,
        base_url: str | None = None,
        models: list[str] | None = None,
        api_key_cipher: str | None = None,
        key_hint: str | None = None,
        enabled: bool | None = None,
        headers: dict[str, str] | None = None,
        api_path: str | None = None,
        expires_at: datetime | None = _UNSET,
    ) -> bool:
        """只更新传入的字段（``None`` = 不改），``updated_at`` 每次刷新。

        ``expires_at`` 例外：``UNSET``（默认）= 不改，``None`` = 清除到期。
        """
        values = _update_values(
            name=name,
            base_url=base_url,
            models=models,
            api_key_cipher=api_key_cipher,
            key_hint=key_hint,
            enabled=enabled,
            headers=headers,
            api_path=api_path,
            expires_at=expires_at,
        )
        stmt = _update_stmt(normalize_oid(oid), values, _now())
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return result.rowcount > 0

    async def delete(self, oid: str) -> bool:
        """删除一行；``True`` 表示真的删掉了。"""
        stmt = _delete_stmt(normalize_oid(oid))
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return result.rowcount > 0


__all__ = ["OracleAICredentialsRepo", "DuplicateCredential", "SHARED_OWNER_OID"]
