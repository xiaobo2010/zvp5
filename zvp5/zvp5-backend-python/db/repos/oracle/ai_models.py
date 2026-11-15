"""Oracle implementation of :class:`db.repos.protocols.AIModelsRepo`.

SQLAlchemy 2.0 async Core over the ``AI_MODELS`` table (V050)。行形状是普通 dict
（``model`` 是主键，没有 ``_id``）。

这张表是**稀疏覆盖层**：模型是否存在由 ``AI_CREDENTIALS.MODELS_JSON`` 决定，这里
只存管理员额外声明的展示名、上下文窗口、Agent Fuel 倍率与启停。因此：

* :meth:`OracleAIModelsRepo.list_all` / :meth:`get` 不回落到渠道目录——缺行就是
  ``None``，「没配过」与「配成默认值」对调用方是两件事。
* :meth:`OracleAIModelsRepo.upsert` 只覆盖调用方**明确给了**的字段：``fields`` 里
  没出现的键保持原值（新行落默认值），所以「只改展示名」不会把倍率重置成 1。
* :meth:`OracleAIModelsRepo.delete` 只删覆盖行，不动凭据里的模型列表：删掉配置
  等于回到默认倍率，而不是让模型从目录里消失。

Session ownership mirrors :mod:`db.repos.oracle.users`: with no injected session
each method opens its own through :func:`db.oracle.session.session_scope` and
writes commit once; an injected session is neither committed nor closed.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator, Mapping

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ai_models as ai_models_tbl
from db.oracle.session import session_scope

Row = dict[str, Any]

#: ``upsert`` 允许调用方改的列。白名单而不是 ``values.update(fields)``：拼错的键
#: 会让 INSERT 直接炸在列名上，白名单则把它变成「这层不认的字段被忽略」——
#: 更好的做法是让路由层只传这几个键，这里的集合是第二道闸。
MUTABLE_FIELDS = frozenset(
    {"display_name", "provider", "context_length", "rate", "enabled", "notes"}
)

#: 新行的默认口径：与 DDL 的 DEFAULT 一致（rate=1、enabled=1，其余 NULL）。
_DEFAULTS: dict[str, Any] = {
    "display_name": None,
    "provider": None,
    "context_length": None,
    "rate": 1,
    "enabled": 1,
    "notes": None,
}


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


def _columns():
    return (
        ai_models_tbl.c.model,
        ai_models_tbl.c.display_name,
        ai_models_tbl.c.provider,
        ai_models_tbl.c.context_length,
        ai_models_tbl.c.rate,
        ai_models_tbl.c.enabled,
        ai_models_tbl.c.notes,
        ai_models_tbl.c.updated_at,
        ai_models_tbl.c.updated_by,
    )


def _select_stmt(model: str):
    return select(*_columns()).where(ai_models_tbl.c.model == model)


def _list_stmt():
    return select(*_columns()).order_by(ai_models_tbl.c.model.asc())


def _row(mapping: Any) -> Row:
    """NUMBER/CHAR 的读取口径统一在这里：倍率给 float，开关给 bool。

    ``rate`` 是 ``NUMBER(10,4)``，Oracle 驱动一般回 ``Decimal``；调用方（倍率换算、
    JSON 响应）只处理 float。``enabled`` 是 ``NUMBER(1)``，0/1 在这里翻成布尔。
    """
    rate = mapping["rate"]
    return {
        "model": mapping["model"],
        "display_name": mapping["display_name"],
        "provider": mapping["provider"],
        "context_length": (
            int(mapping["context_length"])
            if mapping["context_length"] is not None
            else None
        ),
        "rate": float(rate) if rate is not None else 1.0,
        "enabled": bool(mapping["enabled"]),
        "notes": mapping["notes"],
        "updated_at": mapping["updated_at"],
        "updated_by": mapping["updated_by"],
    }


class OracleAIModelsRepo:
    """``AI_MODELS`` over Oracle ADB（模型目录覆盖层）。"""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def list_all(self) -> list[Row]:
        """全部覆盖行，按模型名升序（管理表格直接吃这个顺序）。"""
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(_list_stmt())).all()
        return [_row(row._mapping) for row in rows]

    async def get(self, model: str) -> Row | None:
        """取单个模型的覆盖行；没有配置过返回 ``None``（= 用默认口径）。"""
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(_select_stmt(model))).first()
        if row is None:
            return None
        return _row(row._mapping)

    async def upsert(
        self,
        model: str,
        *,
        fields: Mapping[str, Any],
        actor: str | None = None,
    ) -> Row:
        """按模型名插入或部分更新，返回落库后的行。

        读后写在同一事务里：先按主键取一份现状，再把它与 ``fields`` 合并成完整
        的行（新行用默认值起步）。覆盖层只有管理员一个人写，读后写的竞态窗口可以
        接受；真要并发写同一个模型，后到的那次只是把先到的值合并覆盖——不会产生
        半行或重复行（主键就是模型名）。
        """
        now = _now()
        clean = {key: value for key, value in fields.items() if key in MUTABLE_FIELDS}
        async with _session_for(self._session) as (session, owns):
            existing = (await session.execute(_select_stmt(model))).first()
            if existing is None:
                values = dict(_DEFAULTS)
                values.update(clean)
                await session.execute(
                    insert(ai_models_tbl).values(
                        model=model,
                        updated_at=now,
                        updated_by=actor,
                        **values,
                    )
                )
            else:
                mapping = existing._mapping
                values = {
                    "display_name": mapping["display_name"],
                    "provider": mapping["provider"],
                    "context_length": mapping["context_length"],
                    "rate": mapping["rate"],
                    "enabled": mapping["enabled"],
                    "notes": mapping["notes"],
                }
                values.update(clean)
                await session.execute(
                    update(ai_models_tbl)
                    .where(ai_models_tbl.c.model == model)
                    .values(**values, updated_at=now, updated_by=actor)
                )
            if owns:
                await session.commit()
        # 重新读一次：INSERT 的默认值 / UPDATE 的列默认口径都经由库落地。
        return await self.get(model)

    async def delete(self, model: str) -> bool:
        """删除覆盖行；``True`` 表示真的删掉了。凭据里的模型列表不受影响。"""
        stmt = delete(ai_models_tbl).where(ai_models_tbl.c.model == model)
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return result.rowcount > 0


__all__ = ["OracleAIModelsRepo", "MUTABLE_FIELDS"]
