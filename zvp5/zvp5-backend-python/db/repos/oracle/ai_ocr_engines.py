"""Oracle implementation of :class:`db.repos.protocols.AIOcrEnginesRepo`.

SQLAlchemy 2.0 async Core over the ``AI_OCR_ENGINES`` table (OCR 引擎注册表,
V026). 行形状是 Mongo 风格的 dict（``"_id"`` = 小写 hex ``OID``）。

这张表是**引擎注册表**而不是凭据表：一行描述「用哪个厂商的 OCR、怎么连、优先级
多少」。同一 ``kind`` 可以有多行（不同 region、不同配额来源），``name`` 才是运维
用来指定/禁用某个引擎的键，所以唯一索引建在 ``name`` 上。

两个字段的口径：

* ``config_json``：请求形状（``language_type`` 之类），**不含凭证**。以 dict 形态
  进出：``JSONText`` 负责 ``json.dumps``，落库是 JSON 字符串而不是 Python repr
  （后者会被 ``IS JSON`` 约束挡下来）。
* ``secret_cipher``：整份凭证对象的 AES-GCM 密文 base64，可空。``None`` 表示
  这一行没有凭证（历史行），不是「配置缺失」。明文既不落库也不进日志。

排序是 ``priority ASC, name ASC``：路由取第一条可用引擎，这个顺序既是
``ix_ai_ocr_engines_enabled_priority`` 的查询形状，也保证「同一优先级时行为确定」
——不写第二排序键的话，同优先级的两行顺序由存储引擎决定，测试和线上会不一致。

Session ownership mirrors :mod:`db.repos.oracle.ai_credentials`: with no injected
session each method opens its own through :func:`db.oracle.session.session_scope`,
and writes commit once; an injected session is neither committed nor closed.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import ai_ocr_engines as ai_ocr_engines_tbl
from db.oracle.session import session_scope
from db.repos.protocols import DuplicateOcrEngine

Row = dict[str, Any]

_logger = logging.getLogger(__name__)


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
        ai_ocr_engines_tbl.c.oid,
        ai_ocr_engines_tbl.c.name,
        ai_ocr_engines_tbl.c.kind,
        ai_ocr_engines_tbl.c.config_json,
        ai_ocr_engines_tbl.c.secret_cipher,
        ai_ocr_engines_tbl.c.key_hint,
        ai_ocr_engines_tbl.c.enabled,
        ai_ocr_engines_tbl.c.priority,
        ai_ocr_engines_tbl.c.created_at,
        ai_ocr_engines_tbl.c.updated_at,
    )


def _select_stmt(oid: str):
    return select(*_columns()).where(ai_ocr_engines_tbl.c.oid == oid)


def _list_all_stmt(enabled_only: bool):
    """``priority`` 升序 + ``name`` 升序（后者是确定性排序键）。"""
    stmt = select(*_columns())
    if enabled_only:
        # 过滤放在 SQL：路由路径上取回一堆 disabled 行再在 Python 里丢掉，
        # 既浪费往返也容易漏掉某条分支。
        stmt = stmt.where(ai_ocr_engines_tbl.c.enabled == 1)
    return stmt.order_by(
        ai_ocr_engines_tbl.c.priority.asc(),
        ai_ocr_engines_tbl.c.name.asc(),
        ai_ocr_engines_tbl.c.oid.asc(),
    )


def _insert_stmt(
    *,
    oid: str,
    name: str,
    kind: str,
    config: dict | None,
    secret_cipher: str | None,
    key_hint: str | None,
    enabled: bool,
    priority: int,
    created_at: datetime,
    updated_at: datetime,
):
    # ``config`` 直接给 dict：``JSONText`` 负责 dumps，落库是 JSON 字符串。
    return insert(ai_ocr_engines_tbl).values(
        oid=oid,
        name=name,
        kind=kind,
        config_json=config,
        secret_cipher=secret_cipher,
        key_hint=key_hint,
        enabled=1 if enabled else 0,
        priority=priority,
        created_at=created_at,
        updated_at=updated_at,
    )


def _update_values(
    *,
    name: str | None = None,
    kind: str | None = None,
    config: dict | None = None,
    secret_cipher: str | None = None,
    key_hint: str | None = None,
    enabled: bool | None = None,
    priority: int | None = None,
) -> dict[str, Any]:
    """只把**非 None** 的字段放进 UPDATE 的 SET。

    ``None`` 在本协议里表示「不改」，不是「置空」。想换凭证就连
    ``secret_cipher`` 一起给；想降级成「不需要凭证」目前得删行重建——这类需求
    还没出现，为它引入哨兵只会让每个调用点都要想「这个 None 是哪种」。
    """
    values: dict[str, Any] = {}
    if name is not None:
        values["name"] = name
    if kind is not None:
        values["kind"] = kind
    if config is not None:
        values["config_json"] = config
    if secret_cipher is not None:
        values["secret_cipher"] = secret_cipher
    if key_hint is not None:
        values["key_hint"] = key_hint
    if enabled is not None:
        values["enabled"] = 1 if enabled else 0
    if priority is not None:
        values["priority"] = priority
    return values


def _update_stmt(oid: str, values: dict[str, Any], updated_at: datetime):
    """单条 UPDATE：命中的字段 + ``updated_at``，一次往返。"""
    return (
        update(ai_ocr_engines_tbl)
        .where(ai_ocr_engines_tbl.c.oid == oid)
        .values(**values, updated_at=updated_at)
    )


def _delete_stmt(oid: str):
    return delete(ai_ocr_engines_tbl).where(ai_ocr_engines_tbl.c.oid == oid)


# ---------------------------------------------------------------------------
# row mapping
# ---------------------------------------------------------------------------

def _config_dict(raw: Any) -> dict:
    """``CONFIG_JSON`` → ``dict``（CLOB 可能以 LOB / str / dict 形态回来）。"""
    if raw is None:
        return {}
    if hasattr(raw, "read"):
        raw = raw.read()
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            # 约束保证入库的是合法 JSON；真出现坏串时按空 dict 处理而不是让整个
            # 引擎列表 500 —— 一个引擎的配置坏了不该拖垮其它引擎的路由。
            _logger.warning("ai_ocr_engines.config_json 不是合法 JSON，按空配置处理")
            return {}
    else:
        parsed = raw
    return dict(parsed) if isinstance(parsed, dict) else {}


def _row(mapping: Any) -> Row:
    return {
        "_id": mapping["oid"],
        "name": mapping["name"],
        "kind": mapping["kind"],
        "config": _config_dict(mapping["config_json"]),
        "secret_cipher": mapping["secret_cipher"],
        "key_hint": mapping["key_hint"],
        "enabled": bool(mapping["enabled"]),
        "priority": int(mapping["priority"]),
        "created_at": mapping["created_at"],
        "updated_at": mapping["updated_at"],
    }


class OracleAIOcrEnginesRepo:
    """``AIOcrEnginesRepo`` over Oracle ADB (``AI_OCR_ENGINES``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def list_all(self, *, enabled_only: bool = False) -> list[Row]:
        """全部引擎（或仅启用的），``priority`` 升序。"""
        stmt = _list_all_stmt(enabled_only)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_row(row._mapping) for row in rows]

    async def get_by_oid(self, oid: str) -> Row | None:
        """取一行；不存在返回 ``None``。"""
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(_select_stmt(normalize_oid(oid)))).first()
        if row is None:
            return None
        return _row(row._mapping)

    async def insert(
        self,
        *,
        name: str,
        kind: str,
        config: dict | None = None,
        secret_cipher: str | None = None,
        key_hint: str | None = None,
        enabled: bool = True,
        priority: int = 100,
    ) -> str:
        """插入一行并返回新 ``_id``；撞 ``name`` 唯一键抛 :class:`DuplicateOcrEngine`。"""
        oid = new_oid()
        now = _now()
        stmt = _insert_stmt(
            oid=oid,
            name=name,
            kind=kind,
            config=config,
            secret_cipher=secret_cipher,
            key_hint=key_hint,
            enabled=enabled,
            priority=int(priority),
            created_at=now,
            updated_at=now,
        )
        async with _session_for(self._session) as (session, owns):
            try:
                # savepoint：唯一键冲突只回滚这一条 INSERT，外侧事务仍然可用，
                # 服务层还有机会写审计/返回 409。
                async with session.begin_nested():
                    await session.execute(stmt)
            except IntegrityError as exc:
                raise DuplicateOcrEngine(name=name) from exc
            if owns:
                await session.commit()
        return oid

    async def update(
        self,
        oid: str,
        *,
        name: str | None = None,
        kind: str | None = None,
        config: dict | None = None,
        secret_cipher: str | None = None,
        key_hint: str | None = None,
        enabled: bool | None = None,
        priority: int | None = None,
    ) -> bool:
        """只更新传入的字段（``None`` = 不改），``updated_at`` 每次刷新。"""
        values = _update_values(
            name=name,
            kind=kind,
            config=config,
            secret_cipher=secret_cipher,
            key_hint=key_hint,
            enabled=enabled,
            priority=priority,
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


__all__ = ["OracleAIOcrEnginesRepo", "DuplicateOcrEngine"]
