"""Oracle implementation of the device ban counter (``DEVICE_BAN_COUNTERS``).

设备侧处罚梯度的唯一状态：``BAN_COUNT`` 决定下一次自动设备封禁用第几档
（:func:`services.ban_rules.penalty_expiry`）。与 ``BANNED_DEVICES`` 分开是
因为两件事的生命周期不同：解封只删封禁行，历史违规次数（梯度）不该跟着消失；
管理员要重置换设备才显式调 ``reset-gradient``。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import device_ban_counters as counters_tbl
from db.oracle.session import session_scope

Row = dict[str, Any]


@asynccontextmanager
async def _session_for(
    session: AsyncSession | None,
) -> AsyncIterator[tuple[AsyncSession, bool]]:
    if session is not None:
        yield session, False
        return
    async with session_scope() as owned:
        yield owned, True


def _get_stmt(xuehai_id: str):
    return select(
        counters_tbl.c.xuehai_id,
        counters_tbl.c.ban_count,
        counters_tbl.c.last_banned_at,
    ).where(counters_tbl.c.xuehai_id == xuehai_id)


def _row(mapping: Any) -> Row:
    return {
        "xuehai_id": mapping["xuehai_id"],
        "ban_count": int(mapping["ban_count"] or 0),
        "last_banned_at": mapping["last_banned_at"],
    }


class OracleDeviceBanCountersRepo:
    """``DeviceBanCountersRepo`` over Oracle ADB."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def get(self, xuehai_id: str) -> Row | None:
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(_get_stmt(xuehai_id))).first()
        return None if row is None else _row(row._mapping)

    async def increment(self, xuehai_id: str, at: datetime | None = None) -> int:
        """``ban_count += 1`` 并返回新值；首次违规从 1 开始。"""
        now = at or datetime.now()
        async with _session_for(self._session) as (session, owns):
            existing = (await session.execute(_get_stmt(xuehai_id))).first()
            if existing is None:
                await session.execute(
                    insert(counters_tbl).values(
                        xuehai_id=xuehai_id,
                        ban_count=1,
                        last_banned_at=now,
                    )
                )
                count = 1
            else:
                await session.execute(
                    update(counters_tbl)
                    .where(counters_tbl.c.xuehai_id == xuehai_id)
                    .values(
                        ban_count=counters_tbl.c.ban_count + 1,
                        last_banned_at=now,
                    )
                )
                count = int(existing._mapping["ban_count"] or 0) + 1
            if owns:
                await session.commit()
        return count

    async def get_counts(self, xuehai_ids: list[str]) -> dict[str, int]:
        """批量取计数（列表页避免 N+1）；没有行的设备不出现在返回值里。"""
        unique = list(dict.fromkeys(xuehai_ids))
        if not unique:
            return {}
        stmt = select(counters_tbl.c.xuehai_id, counters_tbl.c.ban_count).where(
            counters_tbl.c.xuehai_id.in_(unique)
        )
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return {
            row._mapping["xuehai_id"]: int(row._mapping["ban_count"] or 0)
            for row in rows
        }

    async def delete(self, xuehai_id: str) -> int:
        """重置梯度；返回受影响行数（``0`` = 本来就没有记录）。"""
        stmt = delete(counters_tbl).where(counters_tbl.c.xuehai_id == xuehai_id)
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return int(result.rowcount or 0)


__all__ = ["OracleDeviceBanCountersRepo"]
