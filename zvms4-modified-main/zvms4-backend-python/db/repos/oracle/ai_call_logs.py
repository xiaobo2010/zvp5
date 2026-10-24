"""Oracle implementation of :class:`db.repos.protocols.AICallLogsRepo`.

SQLAlchemy 2.0 async Core over the ``AI_CALL_LOGS`` table (AI 网关调用流水,
V022). 行形状是 Mongo 风格的 dict（``"_id"`` = 小写 hex ``OID``）。

为什么这张表存在：网关的每一次调用都要留下「谁、用哪个能力、打哪个上游、
结果如何、扣了多少额度」。只记成功调用会让排查断在最需要的一环——额度被拒
（``rejected``）、上游限流（``busy``）与上游真失败（``error``）在现象上都是
「用户说用不了」，只有流水能区分。

追加写：本模块没有 update —— 审计行一旦能被改写，「当时到底发生了什么」就不再
可信。唯一的删除路径是 :meth:`OracleAICallLogsRepo.purge_batch`（保留期清理）：
它整行删除 ``created_at`` 已过期的流水，绝不改行；调用方是
:mod:`tasks.retention`。

无外键（同 ``BAN_VIOLATIONS``）：账号被删除后审计仍要能回答问题，外键会把
审计的寿命绑在账号的寿命上。

``list_page`` / ``total_count`` 共用同一组可选过滤条件（用户 / 能力 / 状态），
两者分开是为了让「先翻页再数总数」的调用方不必为了拿一页而多扫一遍全表：
分页只取 ``LIMIT`` 行，总数只在需要时才算。

Session ownership mirrors :mod:`db.repos.oracle.users`: with no injected session
each method opens its own through :func:`db.oracle.session.session_scope`, and
writes commit once; an injected session is neither committed nor closed.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import ai_call_logs as ai_call_logs_tbl
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
        ai_call_logs_tbl.c.oid,
        ai_call_logs_tbl.c.user_oid,
        ai_call_logs_tbl.c.capability,
        ai_call_logs_tbl.c.endpoint,
        ai_call_logs_tbl.c.provider,
        ai_call_logs_tbl.c.model,
        ai_call_logs_tbl.c.status,
        ai_call_logs_tbl.c.http_status,
        ai_call_logs_tbl.c.error_code,
        ai_call_logs_tbl.c.latency_ms,
        ai_call_logs_tbl.c.units,
        ai_call_logs_tbl.c.input_bytes,
        ai_call_logs_tbl.c.request_id,
        ai_call_logs_tbl.c.key_scope,
        ai_call_logs_tbl.c.created_at,
    )


def _insert_stmt(
    *,
    oid: str,
    user_oid: str,
    capability: str,
    endpoint: str,
    provider: str | None,
    model: str | None,
    status: str,
    http_status: int | None,
    error_code: str | None,
    latency_ms: int | None,
    units: int,
    input_bytes: int | None,
    request_id: str | None,
    key_scope: str | None = None,
    created_at: datetime,
):
    # ``units`` 显式写进语句而不是靠列默认值：它是这次调用**实际扣掉的额度**，
    # 与 AI_QUOTA 的增量同源；让默认值 0 顶替会把「没传」和「扣了 0」混为一谈。
    # ``key_scope`` 同样显式绑定（含 None）：NULL 在语义上是「不知道 / 存量行」，
    # 与「平台出资」的 ``'server'`` 是两件事，靠列默认值表达不了。
    return insert(ai_call_logs_tbl).values(
        oid=oid,
        user_oid=user_oid,
        capability=capability,
        endpoint=endpoint,
        provider=provider,
        model=model,
        status=status,
        http_status=http_status,
        error_code=error_code,
        latency_ms=latency_ms,
        units=units,
        input_bytes=input_bytes,
        request_id=request_id,
        key_scope=key_scope,
        created_at=created_at,
    )


def _count_stmt(user_oid: str, since: datetime):
    return (
        select(func.count())
        .select_from(ai_call_logs_tbl)
        .where(
            ai_call_logs_tbl.c.user_oid == user_oid,
            ai_call_logs_tbl.c.created_at >= since,
        )
    )


def _count_expired_stmt(cutoff: datetime):
    """保留期清理口径下的过期行数（只读，给进度/结果里的 ``remaining`` 用）。"""
    return (
        select(func.count())
        .select_from(ai_call_logs_tbl)
        .where(ai_call_logs_tbl.c.created_at < cutoff)
    )


def _purge_batch_stmt(cutoff: datetime, batch_size: int):
    """One ``DELETE`` bounded to ``batch_size`` rows older than ``cutoff``.

    ``ROWNUM`` 而不是 ``FETCH FIRST n ROWS ONLY``：后者在 Oracle 的 ``DELETE``
    上不被接受（与 ``db/repos/oracle/app_logs.py::_purge_batch_stmt`` 同一原因）。
    """
    return delete(ai_call_logs_tbl).where(
        ai_call_logs_tbl.c.created_at < cutoff,
        text("ROWNUM <= :batch_size").bindparams(batch_size=batch_size),
    )


def _filters(
    user_oid: str | None,
    capability: str | None,
    status: str | None,
    key_scope: str | None = None,
    time_start: datetime | None = None,
    time_end: datetime | None = None,
) -> list[Any]:
    """可选过滤条件；``None`` = 不过滤（而不是「匹配 NULL」）。

    时间边界是**闭区间**（``>= time_start`` / ``<= time_end``）：端点那一秒是
    调用方明确要求看的，排除它会让「按秒排查」少一行。两个边界都收的是 naive
    本地 datetime（``CREATED_AT`` 的列口径），epoch→datetime 的转换在路由层。
    """
    conds: list[Any] = []
    if user_oid is not None:
        conds.append(ai_call_logs_tbl.c.user_oid == normalize_oid(user_oid))
    if capability is not None:
        conds.append(ai_call_logs_tbl.c.capability == capability)
    if status is not None:
        conds.append(ai_call_logs_tbl.c.status == status)
    if key_scope is not None:
        conds.append(ai_call_logs_tbl.c.key_scope == key_scope)
    if time_start is not None:
        conds.append(ai_call_logs_tbl.c.created_at >= time_start)
    if time_end is not None:
        conds.append(ai_call_logs_tbl.c.created_at <= time_end)
    return conds


def _page_stmt(
    user_oid: str | None,
    capability: str | None,
    status: str | None,
    page: int,
    size: int,
    key_scope: str | None = None,
    time_start: datetime | None = None,
    time_end: datetime | None = None,
):
    """一页流水，新调用在前。

    ``created_at`` 倒序（新的在上），同一时刻再按 oid 倒序：``new_oid`` 前缀是
    时间戳，所以这个次序同时也是写入次序，分页不会因时间戳相同而漂移。
    """
    stmt = select(*_columns()).select_from(ai_call_logs_tbl)
    conds = _filters(user_oid, capability, status, key_scope, time_start, time_end)
    if conds:
        stmt = stmt.where(*conds)
    return (
        stmt.order_by(
            ai_call_logs_tbl.c.created_at.desc(), ai_call_logs_tbl.c.oid.desc()
        )
        .offset((page - 1) * size)
        .limit(size)
    )


def _total_stmt(
    user_oid: str | None,
    capability: str | None,
    status: str | None,
    key_scope: str | None = None,
    time_start: datetime | None = None,
    time_end: datetime | None = None,
):
    stmt = select(func.count()).select_from(ai_call_logs_tbl)
    conds = _filters(user_oid, capability, status, key_scope, time_start, time_end)
    if conds:
        stmt = stmt.where(*conds)
    return stmt


def _row(mapping: Any) -> Row:
    """``_id`` 用主键而不是单独一列；数值列统一转 int（Oracle 返回 Decimal）。"""
    return {
        "_id": mapping["oid"],
        "user_oid": mapping["user_oid"],
        "capability": mapping["capability"],
        "endpoint": mapping["endpoint"],
        "provider": mapping["provider"],
        "model": mapping["model"],
        "status": mapping["status"],
        "http_status": (
            int(mapping["http_status"]) if mapping["http_status"] is not None else None
        ),
        "error_code": mapping["error_code"],
        "latency_ms": (
            int(mapping["latency_ms"]) if mapping["latency_ms"] is not None else None
        ),
        "units": int(mapping["units"]),
        "input_bytes": (
            int(mapping["input_bytes"]) if mapping["input_bytes"] is not None else None
        ),
        "request_id": mapping["request_id"],
        "key_scope": mapping["key_scope"],
        "created_at": mapping["created_at"],
    }


class OracleAICallLogsRepo:
    """``AICallLogsRepo`` over Oracle ADB (``AI_CALL_LOGS``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def insert(
        self,
        *,
        user_oid: str,
        capability: str,
        endpoint: str,
        provider: str | None = None,
        model: str | None = None,
        status: str = "success",
        http_status: int | None = None,
        error_code: str | None = None,
        latency_ms: int | None = None,
        units: int = 0,
        input_bytes: int | None = None,
        request_id: str | None = None,
        key_scope: str | None = None,
    ) -> str:
        """追加一条调用流水并返回新 ``_id``（写入时刻由本层生成）。

        ``key_scope`` 记这次调用用的是哪一类 key：``personal``（管理员自己的）、
        ``shared``（通用共享）、``server``（服务端默认配置）或 ``None``（未知 /
        存量行）。它不参与鉴权，只用于事后对账——同一份报表里「平台出资」与
        「管理员自付」必须能分开统计。
        """
        oid = new_oid()
        stmt = _insert_stmt(
            oid=oid,
            user_oid=normalize_oid(user_oid),
            capability=capability,
            endpoint=endpoint,
            provider=provider,
            model=model,
            status=status,
            http_status=http_status,
            error_code=error_code,
            latency_ms=latency_ms,
            units=units,
            input_bytes=input_bytes,
            request_id=request_id,
            key_scope=key_scope,
            created_at=datetime.now(),
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return oid

    async def count_since(self, user_oid: str, since: datetime) -> int:
        """``since`` 之后该用户的调用条数（限流窗口的唯一判据）。"""
        stmt = _count_stmt(normalize_oid(user_oid), since)
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(stmt)).scalar_one()
        return int(total)

    async def list_page(
        self,
        *,
        user_oid: str | None = None,
        capability: str | None = None,
        status: str | None = None,
        key_scope: str | None = None,
        time_start: datetime | None = None,
        time_end: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> list[Row]:
        """一页流水（新调用在前）；``page`` 从 1 开始。"""
        stmt = _page_stmt(
            user_oid,
            capability,
            status,
            page,
            page_size,
            key_scope,
            time_start,
            time_end,
        )
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_row(row._mapping) for row in rows]

    async def total_count(
        self,
        *,
        user_oid: str | None = None,
        capability: str | None = None,
        status: str | None = None,
        key_scope: str | None = None,
        time_start: datetime | None = None,
        time_end: datetime | None = None,
    ) -> int:
        """与 :meth:`list_page` 同过滤条件的总数。"""
        stmt = _total_stmt(
            user_oid, capability, status, key_scope, time_start, time_end
        )
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(stmt)).scalar_one()
        return int(total)

    async def count_expired(self, cutoff: datetime) -> int:
        """``created_at < cutoff`` 的行数（只读，与 :meth:`purge_batch` 同谓词）。"""
        stmt = _count_expired_stmt(cutoff)
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(stmt)).scalar_one()
        return int(total)

    async def purge_batch(self, cutoff: datetime, batch_size: int) -> int:
        """删除 ``created_at < cutoff`` 的行，最多 ``batch_size`` 条，返回实际删除数。

        ``0`` 表示没有可删的行。每次调用一个短 session、一次 commit；批循环在
        :mod:`tasks.retention`。删除不可逆，且按批提交 —— 长清理不会一直占着
        ADB 上的一个事务。
        """
        stmt = _purge_batch_stmt(cutoff, batch_size)
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return int(result.rowcount or 0)


__all__ = ["OracleAICallLogsRepo"]
