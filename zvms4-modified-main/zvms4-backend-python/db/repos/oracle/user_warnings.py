"""Oracle implementation of the ``UserWarningsRepo`` (警告账本, V041).

SQLAlchemy 2.0 async Core over the ``USER_WARNINGS`` table. 行形状是 Mongo 风格的
dict（``"_id"`` = 小写 hex ``OID``）。

设计要点（服务层会依赖，改之前先读）：

* **唯一键就是控制流**：``(user_oid, rule_code, period)`` 唯一。插入撞键（同月
  已警告过）不是错误，而是「该升级为封禁」的信号，:meth:`try_record` 把它翻译
  成 ``False``——调用方据此决定警告还是按梯度封号。
* **月度归零没有 cron**：``period`` 是 ``YYYYMM`` 文本，下个月的第一条 INSERT
  就是新行。没有「清理任务没跑」这种失效模式。
* **savepoint**：插入包在 ``begin_nested()`` 里，撞唯一键只回滚这一小段，调用方
  若在同一个事务里还有别的写（例如违规证据）不会被连带废掉。

Session ownership mirrors :mod:`db.repos.oracle.users`: with no injected session
each method opens its own through :func:`db.oracle.session.session_scope`, and
writes commit once; an injected session is neither committed nor closed.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import user_warnings as warnings_tbl
from db.oracle.session import session_scope

Row = dict[str, Any]

# DB 列宽即契约：超长理由/详情在服务端截断，不让一次警告因为长字符串整行失败。
MAX_REASON_CHARS = 512
MAX_DETAIL_CHARS = 500


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
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:width]


def _columns() -> tuple[Any, ...]:
    return (
        warnings_tbl.c.oid,
        warnings_tbl.c.user_oid,
        warnings_tbl.c.rule_code,
        warnings_tbl.c.period,
        warnings_tbl.c.reason,
        warnings_tbl.c.detail,
        warnings_tbl.c.created_at,
    )


def _row(mapping: Any) -> Row:
    return {
        "_id": mapping["oid"],
        "user_oid": mapping["user_oid"],
        "rule_code": mapping["rule_code"],
        "period": mapping["period"],
        "reason": mapping["reason"],
        "detail": mapping["detail"],
        "created_at": mapping["created_at"],
    }


class OracleUserWarningsRepo:
    """警告账本 （``USER_WARNINGS``）。"""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def try_record(
        self,
        user_oid: str,
        rule_code: str,
        period: str,
        reason: str | None = None,
        detail: str | None = None,
    ) -> bool:
        """写入本月第一条警告；本月已有 → ``False``（唯一冲突即「已警告过」）。

        冲突是**预期路径**：服务层据此把「首次违规」升级成「同月再犯 → 封禁」。
        """
        stmt = insert(warnings_tbl).values(
            oid=new_oid(),
            user_oid=normalize_oid(user_oid),
            rule_code=str(rule_code)[:64],
            period=str(period)[:6],
            reason=_clip(reason, MAX_REASON_CHARS),
            detail=_clip(detail, MAX_DETAIL_CHARS),
            created_at=_now(),
        )
        async with _session_for(self._session) as (session, owns):
            try:
                async with session.begin_nested():
                    await session.execute(stmt)
            except IntegrityError:
                # 同月已有警告（ORA-00001）：savepoint 已回滚，外层事务仍可用。
                if owns:
                    await session.rollback()
                return False
            if owns:
                await session.commit()
        return True

    async def get(
        self, user_oid: str, rule_code: str, period: str
    ) -> Row | None:
        """该用户/规则/月份的那一行警告（没有则 ``None``）。"""
        stmt = select(*_columns()).where(
            warnings_tbl.c.user_oid == normalize_oid(user_oid),
            warnings_tbl.c.rule_code == str(rule_code),
            warnings_tbl.c.period == str(period),
        )
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        return _row(row._mapping) if row is not None else None

    async def count_for_user_rule(self, user_oid: str, rule_code: str) -> int:
        """该用户在这条规则上被警告过的历史条数（管理端「看过往」用）。"""
        stmt = select(func.count()).select_from(warnings_tbl).where(
            warnings_tbl.c.user_oid == normalize_oid(user_oid),
            warnings_tbl.c.rule_code == str(rule_code),
        )
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(stmt)).scalar_one()
        return int(total or 0)


__all__ = ["OracleUserWarningsRepo"]
