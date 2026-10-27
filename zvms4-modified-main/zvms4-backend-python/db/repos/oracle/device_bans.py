"""Oracle implementation of :class:`db.repos.protocols.DeviceBansRepo`.

SQLAlchemy 2.0 async Core over the ``BANNED_DEVICES`` table (设备级封禁, V014).
主键就是学海设备号，所以这里的行形状没有 ``_id``——设备号本身既是身份也是
标识（``{"xuehai_id": ..., "reason": ..., "until": ..., ...}``）。

为什么设备封禁是一张独立表而不是 ``users`` 上的列：设备号是**外部标识**，同一
台设备可能先后被多个账号使用，也可能在账号被删后仍要拦住。账号封禁到设备的
联动**不写**这张表——服务层按 ``users.primary_xuehai_id`` 实时推导，这样账号
解封/换设备后设备自动恢复，不会留下只能人工清理的僵尸行。

Session ownership mirrors :mod:`db.repos.oracle.users`: with no injected session
each method opens its own through :func:`db.oracle.session.session_scope`, and
writes commit once; an injected session is neither committed nor closed.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import delete, func, insert, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import banned_devices as banned_devices_tbl
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
        banned_devices_tbl.c.xuehai_id,
        banned_devices_tbl.c.reason,
        banned_devices_tbl.c.until,
        banned_devices_tbl.c.source,
        banned_devices_tbl.c.rule_code,
        banned_devices_tbl.c.operator_oid,
        banned_devices_tbl.c.created_at,
    )


def _get_stmt(xuehai_id: str):
    return select(*_columns()).where(banned_devices_tbl.c.xuehai_id == xuehai_id)


def _insert_stmt(
    xuehai_id: str,
    reason: str | None,
    until: datetime | None,
    source: str,
    rule_code: str | None,
    operator_oid: str | None,
    created_at: datetime,
):
    return insert(banned_devices_tbl).values(
        xuehai_id=xuehai_id,
        reason=reason,
        until=until,
        source=source,
        rule_code=rule_code,
        operator_oid=operator_oid,
        created_at=created_at,
    )


def _update_stmt(
    xuehai_id: str,
    reason: str | None,
    until: datetime | None,
    source: str,
    rule_code: str | None,
    operator_oid: str | None,
    created_at: datetime,
):
    """覆盖一行已有的设备封禁。

    ``created_at`` 一并刷新，语义是「当前这条判决是什么时候落下的」：重复封禁
    不留旧时间戳，否则管理员看到的时间会停在第一次违规那天。
    """
    return (
        update(banned_devices_tbl)
        .where(banned_devices_tbl.c.xuehai_id == xuehai_id)
        .values(
            reason=reason,
            until=until,
            source=source,
            rule_code=rule_code,
            operator_oid=operator_oid,
            created_at=created_at,
        )
    )


def _delete_stmt(xuehai_id: str):
    return delete(banned_devices_tbl).where(
        banned_devices_tbl.c.xuehai_id == xuehai_id
    )


def _local_now(now: datetime | None) -> datetime:
    """「此刻」的落库口径：本地 naive（与 ``util.ban`` 的判定侧一致）。"""
    if now is None:
        return datetime.now()
    if now.tzinfo is None or now.tzinfo.utcoffset(now) is None:
        return now
    return now.astimezone().replace(tzinfo=None)


def _active_filter(include_expired: bool, now: datetime | None) -> list[Any]:
    """「此刻仍生效」的设备封禁谓词。

    ``until IS NULL`` 是永久；有时刻的比较本地墙钟。默认只列生效中的行：
    到期封禁没有后台任务删行（``device_ban_status`` 用 ``ban_is_active``
    判定，行本身留着），只钉主键就会把刑满的机器一直挂在「当前封禁」上。
    """
    if include_expired:
        return []
    return [
        or_(
            banned_devices_tbl.c.until.is_(None),
            banned_devices_tbl.c.until > _local_now(now),
        )
    ]


def _page_stmt(
    page: int,
    size: int,
    query: str = "",
    include_expired: bool = False,
    now: datetime | None = None,
):
    """一页 + ``COUNT(*) OVER()`` 总数，一次往返。

    ``created_at`` 倒序（新封禁在上），同一时刻再按设备号升序保证分页稳定——
    没有 oid 列可以兜底排序，设备号本身是主键，足够唯一。
    """
    stmt = select(*_columns(), func.count().over().label("total_count"))
    conds = _active_filter(include_expired, now)
    if query:
        conds.append(
            banned_devices_tbl.c.xuehai_id.like(_like_pattern(query), escape="\\")
        )
    if conds:
        stmt = stmt.where(*conds)
    return (
        stmt.order_by(
            banned_devices_tbl.c.created_at.desc(),
            banned_devices_tbl.c.xuehai_id.asc(),
        )
        .offset((page - 1) * size)
        .limit(size)
    )


def _like_pattern(query: str) -> str:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _count_stmt(
    query: str = "",
    include_expired: bool = False,
    now: datetime | None = None,
):
    stmt = select(func.count()).select_from(banned_devices_tbl)
    conds = _active_filter(include_expired, now)
    if query:
        conds.append(
            banned_devices_tbl.c.xuehai_id.like(_like_pattern(query), escape="\\")
        )
    if conds:
        stmt = stmt.where(*conds)
    return stmt


def _ban_row(mapping: Any) -> Row:
    return {
        "xuehai_id": mapping["xuehai_id"],
        "reason": mapping["reason"],
        "until": mapping["until"],
        "source": mapping["source"],
        "rule_code": mapping["rule_code"],
        "operator_oid": mapping["operator_oid"],
        "created_at": mapping["created_at"],
    }


def _total_of(rows: Any) -> int | None:
    """窗口计数；空页时不可观测（``None``），由调用方决定是否补一次 count。"""
    if not rows:
        return None
    return int(rows[0]._mapping["total_count"])


class OracleDeviceBansRepo:
    """``DeviceBansRepo`` over Oracle ADB (``BANNED_DEVICES``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def get(self, xuehai_id: str) -> Row | None:
        """该设备当前的封禁行；未封禁返回 ``None``。"""
        stmt = _get_stmt(xuehai_id)
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        return _ban_row(row._mapping)

    async def upsert(
        self,
        xuehai_id: str,
        *,
        reason: str | None,
        until: datetime | None,
        source: str,
        rule_code: str | None,
        operator_oid: str | None,
    ) -> None:
        """封禁一台设备：已存在则覆盖，不存在则插入。

        「先查后写」而不是一条 MERGE：``until``/``source`` 全列都要被覆盖，判断
        分支只依赖主键，两条语句的差距可以忽略；真正的并发兜底是主键
        （``IntegrityError`` 会让调用方重试一次，比自造 MERGE 文本更容易读）。
        """
        now = _now()
        async with _session_for(self._session) as (session, owns):
            existing = (
                await session.execute(_get_stmt(xuehai_id))
            ).first()
            if existing is None:
                await session.execute(
                    _insert_stmt(
                        xuehai_id, reason, until, source, rule_code,
                        operator_oid, now,
                    )
                )
            else:
                await session.execute(
                    _update_stmt(
                        xuehai_id, reason, until, source, rule_code,
                        operator_oid, now,
                    )
                )
            if owns:
                await session.commit()

    async def delete(self, xuehai_id: str) -> int:
        """解除设备封禁，返回受影响行数（``0`` = 本来就没封）。"""
        stmt = _delete_stmt(xuehai_id)
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return int(result.rowcount)

    async def list_page(
        self,
        page: int,
        size: int,
        query: str = "",
        include_expired: bool = False,
        now: datetime | None = None,
    ) -> tuple[list[Row], int]:
        """返回 ``(rows, total)``，新封禁在前；``page`` 从 1 开始。

        ``query`` 非空时按设备号包含匹配（管理员按学号找一台机器）。
        默认只列**此刻仍生效**的封禁；``include_expired=True`` 用于排查
        「行还在但设备已能登录」的存量数据。
        """
        page_stmt = _page_stmt(page, size, query, include_expired, now)
        count_stmt = _count_stmt(query, include_expired, now)

        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(page_stmt)).all()
            total = _total_of(rows)
            if total is None:
                # ``page == 1`` 的空页必然是 0；越界页才需要单独 count。
                total = (
                    0
                    if page == 1
                    else int((await session.execute(count_stmt)).scalar_one())
                )
        return [_ban_row(row._mapping) for row in rows], total


__all__ = ["OracleDeviceBansRepo"]
