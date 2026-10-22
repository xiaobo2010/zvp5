"""Oracle implementation of :class:`db.repos.protocols.BanRulesRepo`.

SQLAlchemy 2.0 async Core over the ``BAN_RULES`` table (规则封禁配置, V013).
Row shape is the Mongo-ready dict (``"_id"`` = the lowercase-hex ``OID``).

``CONFIG`` / ``PENALTIES`` are bounded JSON **text**: the DB only guarantees
``IS JSON``, and turning them into dicts is the service layer's job (it owns the
per-rule schema, e.g. ``{"threshold":3,"window_hours":24}`` / ``[1,2,7]``).

``CODE`` is the stable identity quoted by :meth:`update_by_code`;
:meth:`update_by_code` patches only the tunables (``name`` / ``enabled`` /
``config`` / ``penalties``) and always bumps ``UPDATED_AT`` so an admin can see
when a rule last changed. ``TRIGGER`` is the *evaluation* identity: the login and
report paths look a rule up by trigger (:meth:`get_by_trigger`), so exactly one
rule may own a trigger (unique index) — otherwise the penalty step would depend
on which row happened to come back first.

Session ownership mirrors :mod:`db.repos.oracle.users`: with no injected session
each method opens its own through :func:`db.oracle.session.session_scope`, and
writes commit once; an injected session is neither committed nor closed.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid
from db.models import ban_rules as ban_rules_tbl
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


def _columns() -> tuple[Any, ...]:
    return (
        ban_rules_tbl.c.oid,
        ban_rules_tbl.c.code,
        ban_rules_tbl.c.name,
        ban_rules_tbl.c.trigger_code,
        ban_rules_tbl.c.enabled,
        ban_rules_tbl.c.config,
        ban_rules_tbl.c.penalties,
        ban_rules_tbl.c.builtin,
        ban_rules_tbl.c.created_at,
        ban_rules_tbl.c.updated_at,
    )


def _list_all_stmt():
    # ``code`` 升序：规则集合小且必须稳定，管理员界面不需要再排序。
    return select(*_columns()).order_by(ban_rules_tbl.c.code.asc())


def _get_by_code_stmt(code: str):
    return select(*_columns()).where(ban_rules_tbl.c.code == code)


def _get_by_trigger_stmt(trigger: str):
    # 唯一索引保证最多一行：求值入口拿到的规则必须唯一，否则封禁天数不可复现。
    return select(*_columns()).where(ban_rules_tbl.c.trigger_code == trigger)


def _insert_stmt(
    oid: str,
    code: str,
    name: str,
    trigger: str,
    enabled: bool,
    config: str,
    penalties: str,
    builtin: bool,
    now: datetime,
):
    return insert(ban_rules_tbl).values(
        oid=oid,
        code=code,
        name=name,
        trigger_code=trigger,
        enabled=1 if enabled else 0,
        config=config,
        penalties=penalties,
        builtin=1 if builtin else 0,
        created_at=now,
        updated_at=now,
    )


def _delete_by_code_stmt(code: str):
    return delete(ban_rules_tbl).where(ban_rules_tbl.c.code == code)


def _update_by_code_stmt(
    code: str,
    fields: dict[str, Any],
    now: datetime,
):
    """按 code 局部更新；``fields`` 由调用方过滤掉「未提供」的列。

    ``updated_at`` 总是刷新——它回答的是「这条规则最后一次被改动是什么时候」，
    不因改了哪一列而变。
    """
    return (
        update(ban_rules_tbl)
        .where(ban_rules_tbl.c.code == code)
        .values(**fields, updated_at=now)
    )


def _rule_row(mapping: Any) -> Row:
    return {
        "_id": mapping["oid"],
        "code": mapping["code"],
        "name": mapping["name"],
        "trigger": mapping["trigger_code"],
        "enabled": bool(mapping["enabled"]),
        "config": mapping["config"],
        "penalties": mapping["penalties"],
        "builtin": bool(mapping["builtin"]),
        "created_at": mapping["created_at"],
        "updated_at": mapping["updated_at"],
    }


class OracleBanRulesRepo:
    """``BanRulesRepo`` over Oracle ADB (``BAN_RULES``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def list_all(self) -> list[Row]:
        stmt = _list_all_stmt()
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_rule_row(row._mapping) for row in rows]

    async def get_by_code(self, code: str) -> Row | None:
        stmt = _get_by_code_stmt(code)
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return _rule_row(row._mapping)

    async def get_by_trigger(self, trigger: str) -> Row | None:
        """求值入口按**检查项**取规则（唯一索引保证最多一条）。"""
        stmt = _get_by_trigger_stmt(trigger)
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return _rule_row(row._mapping)

    async def insert(
        self,
        code: str,
        name: str,
        trigger: str,
        *,
        enabled: bool,
        config: str,
        penalties: str,
        builtin: bool = False,
    ) -> str:
        """新建一条规则并返回新 ``_id``。

        ``created_at`` 与 ``updated_at`` 同源（同一个 ``now``）：刚建的行两列
        相等，管理员看到的「最后修改时间」不会比创建时间早。
        ``builtin`` 默认 ``False``：内置规则只由 DDL 种子插入，运行时不造。
        """
        oid = new_oid()
        now = datetime.now()
        stmt = _insert_stmt(
            oid, code, name, trigger, enabled, config, penalties, builtin, now
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return oid

    async def delete_by_code(self, code: str) -> int:
        """删除一条规则并返回受影响行数（``0`` = 没有这条规则）。"""
        stmt = _delete_by_code_stmt(code)
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return int(result.rowcount)

    async def update_by_code(
        self,
        code: str,
        *,
        name: str | None = None,
        enabled: bool | None = None,
        config: str | None = None,
        penalties: str | None = None,
    ) -> int:
        """局部更新并返回受影响行数（``0`` = 没有这条规则）。

        ``None`` 表示「不动这一列」而不是「写成 NULL」：这几列都是 NOT NULL，
        而 ``enabled=False`` 必须能表达，所以不能拿假值当哨兵。
        ``trigger`` 不在这里：检查项决定求值入口，改它等于换一条规则。
        """
        fields: dict[str, Any] = {}
        if name is not None:
            fields["name"] = name
        if enabled is not None:
            fields["enabled"] = 1 if enabled else 0
        if config is not None:
            fields["config"] = config
        if penalties is not None:
            fields["penalties"] = penalties
        stmt = _update_by_code_stmt(code, fields, datetime.now())
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return int(result.rowcount)


__all__ = ["OracleBanRulesRepo"]
