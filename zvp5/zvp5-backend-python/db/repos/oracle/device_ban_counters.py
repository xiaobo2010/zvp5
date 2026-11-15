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

from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError
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


#: ``ban_count += 1`` 的原子 upsert。Oracle 的 ``MERGE`` 是唯一能「不存在才插、
#: 存在就加」的单语句写法：先查再插的写法在并发首罚时会让两个事务都看到「无行」，
#: 一个插入成功、另一个撞主键——而调用方为了不让封禁消失把异常吞成第 1 档，
#: 计数就永久少一次（梯度实际被压低一档）。
_MERGE_INCREMENT = text(
    """
    MERGE INTO device_ban_counters target
    USING (SELECT :xuehai_id AS xuehai_id FROM dual) source
    ON (target.xuehai_id = source.xuehai_id)
    WHEN MATCHED THEN
        UPDATE SET target.ban_count = target.ban_count + 1,
                   target.last_banned_at = :at
    WHEN NOT MATCHED THEN
        INSERT (xuehai_id, ban_count, last_banned_at)
        VALUES (:xuehai_id, 1, :at)
    """
)


def _merge_increment_stmt():
    return _MERGE_INCREMENT


def _increment_stmt(xuehai_id: str, at: datetime):
    """``MERGE`` 撞唯一键后的补救：把这次计数补上（行已存在）。"""
    return (
        update(counters_tbl)
        .where(counters_tbl.c.xuehai_id == xuehai_id)
        .values(
            ban_count=counters_tbl.c.ban_count + 1,
            last_banned_at=at,
        )
    )


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
        """``ban_count += 1`` 并返回新值；首次违规从 1 开始。

        单语句 ``MERGE`` 保证并发首罚也只加一次：两个事务撞同一条「不存在」
        时，后到者在主键上失败，转成 ``UPDATE ... ban_count + 1`` 重试，绝不
        把异常吞成「第 1 档」（那等于让梯度永久少一档）。返回值一律**回读**
        库里的行，不用「读到的旧值 + 1」推算。
        """
        now = at or datetime.now()
        async with _session_for(self._session) as (session, owns):
            try:
                await session.execute(
                    _merge_increment_stmt(),
                    {"xuehai_id": xuehai_id, "at": now},
                )
            except IntegrityError:
                # 并发首罚：另一个事务刚插入同一主键。语句级失败不影响事务，
                # 直接补一次 UPDATE 即可。
                await session.execute(_increment_stmt(xuehai_id, now))
            row = (await session.execute(_get_stmt(xuehai_id))).first()
            if owns:
                await session.commit()
        if row is None:
            # 理论上不可达（MERGE 或 UPDATE 至少写成了行）；保守按首档返回。
            return 1
        return int(row._mapping["ban_count"] or 0)

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
