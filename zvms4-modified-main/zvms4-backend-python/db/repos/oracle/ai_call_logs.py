"""Oracle implementation of :class:`db.repos.protocols.AICallLogsRepo`.

SQLAlchemy 2.0 async Core over the ``AI_CALL_LOGS`` table (AI 网关调用流水,
V024). 行形状是 Mongo 风格的 dict（``"_id"`` = 小写 hex ``OID``）。

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

``list_page`` / ``total_count`` 共用同一组可选过滤条件（用户 / 能力 / 状态 /
密钥来源 / request_id / provider / model / purpose / 时间），两者分开是为了让
「先翻页再数总数」的调用方不必为了拿一页而多扫一遍全表：分页只取 ``LIMIT`` 行，
总数只在需要时才算。``stats`` 是同一组时间边界上的聚合视图（按日 + 按
provider/model/purpose 分组小计），给 ``/api/ai/logs/stats`` 用。

``PROMPT_TOKENS`` / ``COMPLETION_TOKENS`` / ``TOTAL_TOKENS`` 为 NULL 的语义是
「上游没报 usage」，此时该行的 ``UNITS`` 是估算值；不为 NULL 时两者同源。别把
NULL 读成 0——0 是一个真实的 token 数。

Session ownership mirrors :mod:`db.repos.oracle.users`: with no injected session
each method opens its own through :func:`db.oracle.session.session_scope`, and
writes commit once; an injected session is neither committed nor closed.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import case, delete, func, insert, select, text
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
        ai_call_logs_tbl.c.prompt_tokens,
        ai_call_logs_tbl.c.completion_tokens,
        ai_call_logs_tbl.c.total_tokens,
        ai_call_logs_tbl.c.attempt_no,
        ai_call_logs_tbl.c.purpose,
        ai_call_logs_tbl.c.first_token_ms,
        ai_call_logs_tbl.c.engine_kind,
        ai_call_logs_tbl.c.attachment_oid,
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
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    attempt_no: int | None = None,
    purpose: str | None = None,
    first_token_ms: int | None = None,
    engine_kind: str | None = None,
    attachment_oid: str | None = None,
    created_at: datetime,
):
    # ``units`` 显式写进语句而不是靠列默认值：它是这次调用**实际扣掉的额度**，
    # 与 AI_QUOTA 的增量同源；让默认值 0 顶替会把「没传」和「扣了 0」混为一谈。
    # ``key_scope`` 同样显式绑定（含 None）：NULL 在语义上是「不知道 / 存量行」，
    # 与「平台出资」的 ``'server'`` 是两件事，靠列默认值表达不了。
    # V031 的元数据列（token / attempt / purpose / trace）同理：NULL = 未知，
    # 不是 0，所以全部显式绑定（绑定 None 会写入 NULL，列也没有 DEFAULT）。
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
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        attempt_no=attempt_no,
        purpose=purpose,
        first_token_ms=first_token_ms,
        engine_kind=engine_kind,
        attachment_oid=attachment_oid,
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


# 这一组过滤条件的名字：路由层按它把 query 参数原样透传（少一个就会静默变成
# 「不过滤」），测试也按它构造。新增过滤条件时这里是唯一起点。
_FILTER_KEYS = frozenset(
    {
        "user_oid",
        "capability",
        "status",
        "key_scope",
        "request_id",
        "provider",
        "model",
        "purpose",
        "time_start",
        "time_end",
    }
)


def _filters(
    *,
    user_oid: str | None = None,
    capability: str | None = None,
    status: str | None = None,
    key_scope: str | None = None,
    request_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    purpose: str | None = None,
    time_start: datetime | None = None,
    time_end: datetime | None = None,
) -> list[Any]:
    """可选过滤条件；``None`` = 不过滤（而不是「匹配 NULL」）。

    时间边界是**闭区间**（``>= time_start`` / ``<= time_end``）：端点那一秒是
    调用方明确要求看的，排除它会让「按秒排查」少一行。两个边界都收的是 naive
    本地 datetime（``CREATED_AT`` 的列口径），epoch→datetime 的转换在路由层。

    ``request_id`` / ``provider`` / ``model`` / ``purpose`` 是精确匹配而不是
    LIKE：它们都是「已知值再查」的入口（从响应头、从报表、从日志里抄下来的），
    模糊匹配在这里只会把索引让给全表扫。
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
    if request_id is not None:
        conds.append(ai_call_logs_tbl.c.request_id == request_id)
    if provider is not None:
        conds.append(ai_call_logs_tbl.c.provider == provider)
    if model is not None:
        conds.append(ai_call_logs_tbl.c.model == model)
    if purpose is not None:
        conds.append(ai_call_logs_tbl.c.purpose == purpose)
    if time_start is not None:
        conds.append(ai_call_logs_tbl.c.created_at >= time_start)
    if time_end is not None:
        conds.append(ai_call_logs_tbl.c.created_at <= time_end)
    return conds


def _page_stmt(conds: list[Any], page: int, size: int):
    """一页流水，新调用在前。

    ``created_at`` 倒序（新的在上），同一时刻再按 oid 倒序：``new_oid`` 前缀是
    时间戳，所以这个次序同时也是写入次序，分页不会因时间戳相同而漂移。
    """
    stmt = select(*_columns()).select_from(ai_call_logs_tbl)
    if conds:
        stmt = stmt.where(*conds)
    return (
        stmt.order_by(
            ai_call_logs_tbl.c.created_at.desc(), ai_call_logs_tbl.c.oid.desc()
        )
        .offset((page - 1) * size)
        .limit(size)
    )


def _total_stmt(conds: list[Any]):
    stmt = select(func.count()).select_from(ai_call_logs_tbl)
    if conds:
        stmt = stmt.where(*conds)
    return stmt


# ---------------------------------------------------------------------------
# 聚合（``/api/ai/logs/stats``）：按日 + 按 provider/model/purpose 小计
# ---------------------------------------------------------------------------

# 分组小计最多回多少行。provider/model 的数量由配置决定（十几个量级），但
# purpose 是开放集合、model 也可能随供应商改版膨胀；给一个上限保证「打开统计
# 面板」不会因为某天有人塞了 500 个模型名而变成一页一万行。
STATS_GROUP_LIMIT = 50


def _day_expr():
    """按本地自然日分组：``CREATED_AT`` 是 naive TIMESTAMP，``TRUNC`` 直接落日期。"""
    return func.trunc(ai_call_logs_tbl.c.created_at)


def _errors_expr():
    """错误数 = ``status != 'success'`` 的行数（rejected/busy 也算错误）。

    口径与 ``/api/logs`` 的「失败」一致：运营关心的是「有多少次没成」，不是
    「上游挂了还是我们挡了」——后者由 ``status`` 分组再看。
    """
    return func.sum(
        case((ai_call_logs_tbl.c.status != "success", 1), else_=0)
    )


def _stats_daily_stmt(conds: list[Any]):
    day = _day_expr()
    stmt = (
        select(
            day.label("day"),
            func.count().label("calls"),
            _errors_expr().label("errors"),
            func.sum(ai_call_logs_tbl.c.prompt_tokens).label("prompt_tokens"),
            func.sum(ai_call_logs_tbl.c.completion_tokens).label("completion_tokens"),
            func.sum(ai_call_logs_tbl.c.total_tokens).label("total_tokens"),
            func.avg(ai_call_logs_tbl.c.latency_ms).label("avg_latency_ms"),
        )
        .select_from(ai_call_logs_tbl)
        .group_by(day)
        .order_by(day)
    )
    if conds:
        stmt = stmt.where(*conds)
    return stmt


def _stats_group_stmt(column, conds: list[Any]):
    """按某个维度分组的调用数 / 错误数 / 额度 / token 小计，调用多的在前。"""
    calls = func.count()
    stmt = (
        select(
            column.label("bucket"),
            calls.label("calls"),
            _errors_expr().label("errors"),
            func.sum(ai_call_logs_tbl.c.units).label("units"),
            func.sum(ai_call_logs_tbl.c.total_tokens).label("total_tokens"),
        )
        .select_from(ai_call_logs_tbl)
        .group_by(column)
        .order_by(calls.desc())
        .limit(STATS_GROUP_LIMIT)
    )
    if conds:
        stmt = stmt.where(*conds)
    return stmt


def _as_int(value: Any) -> int | None:
    """Oracle 的 ``SUM``/``COUNT`` 回 ``Decimal``；统一成 int，``None`` 保留。"""
    if value is None:
        return None
    return int(value)


def _daily_row(mapping: Any) -> Row:
    day = mapping["day"]
    return {
        "date": day.strftime("%Y-%m-%d") if hasattr(day, "strftime") else str(day),
        "calls": _as_int(mapping["calls"]) or 0,
        "errors": _as_int(mapping["errors"]) or 0,
        # 全窗口都没报过 usage 时 SUM 回 NULL：这里给 0 而不是 None，因为它是
        # 聚合值（「这几天总共观测到多少 token」），0 是诚实的答案。
        "prompt_tokens": _as_int(mapping["prompt_tokens"]) or 0,
        "completion_tokens": _as_int(mapping["completion_tokens"]) or 0,
        "total_tokens": _as_int(mapping["total_tokens"]) or 0,
        "avg_latency_ms": _as_int(mapping["avg_latency_ms"]),
    }


def _group_row(mapping: Any, key: str) -> Row:
    return {
        key: mapping["bucket"],
        "calls": _as_int(mapping["calls"]) or 0,
        "errors": _as_int(mapping["errors"]) or 0,
        "units": _as_int(mapping["units"]) or 0,
        "total_tokens": _as_int(mapping["total_tokens"]) or 0,
    }


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
        "prompt_tokens": _as_int(mapping["prompt_tokens"]),
        "completion_tokens": _as_int(mapping["completion_tokens"]),
        "total_tokens": _as_int(mapping["total_tokens"]),
        "attempt_no": _as_int(mapping["attempt_no"]),
        "purpose": mapping["purpose"],
        "first_token_ms": _as_int(mapping["first_token_ms"]),
        "engine_kind": mapping["engine_kind"],
        "attachment_oid": mapping["attachment_oid"],
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
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        attempt_no: int | None = None,
        purpose: str | None = None,
        first_token_ms: int | None = None,
        engine_kind: str | None = None,
        attachment_oid: str | None = None,
    ) -> str:
        """追加一条调用流水并返回新 ``_id``（写入时刻由本层生成）。

        ``key_scope`` 记这次调用用的是哪一类 key：``personal``（管理员自己的）、
        ``shared``（通用共享）、``server``（服务端默认配置）或 ``None``（未知 /
        存量行）。它不参与鉴权，只用于事后对账——同一份报表里「平台出资」与
        「管理员自付」必须能分开统计。

        ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens`` 只在**上游
        报了 usage** 时传：三者与 ``units`` 同源（由 usage 折算）。为 ``None``
        时这一行的 ``units`` 就是估算值，这是「tokens 为 NULL ⟺ units 为估算」
        不变式的写入侧。
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
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            attempt_no=attempt_no,
            purpose=purpose,
            first_token_ms=first_token_ms,
            engine_kind=engine_kind,
            attachment_oid=attachment_oid,
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
        request_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        purpose: str | None = None,
        time_start: datetime | None = None,
        time_end: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> list[Row]:
        """一页流水（新调用在前）；``page`` 从 1 开始。"""
        conds = _filters(
            user_oid=user_oid,
            capability=capability,
            status=status,
            key_scope=key_scope,
            request_id=request_id,
            provider=provider,
            model=model,
            purpose=purpose,
            time_start=time_start,
            time_end=time_end,
        )
        stmt = _page_stmt(conds, page, page_size)
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
        request_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        purpose: str | None = None,
        time_start: datetime | None = None,
        time_end: datetime | None = None,
    ) -> int:
        """与 :meth:`list_page` 同过滤条件的总数。"""
        conds = _filters(
            user_oid=user_oid,
            capability=capability,
            status=status,
            key_scope=key_scope,
            request_id=request_id,
            provider=provider,
            model=model,
            purpose=purpose,
            time_start=time_start,
            time_end=time_end,
        )
        stmt = _total_stmt(conds)
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(stmt)).scalar_one()
        return int(total)

    async def stats(
        self,
        *,
        since: datetime,
        until: datetime | None = None,
    ) -> dict[str, list[Row]]:
        """``since`` 起的调用聚合：按日 + 按 provider/model/purpose 小计。

        四条查询共用一个时间窗口（``since`` / ``until`` 边界口径与
        :meth:`list_page` 一致：闭区间）。窗口刻意**只按时间**切：统计面板回答
        的是「这几天平台消耗了多少」，再叠用户/模型过滤会变成另一种报表。
        想看某个维度的细分，用 ``list_page`` 的过滤条件。

        四个部分一次返回，是因为它们必须来自**同一个窗口**——分开调用会让面板
        上的总数与分项小计对不上（跨了分钟边界）。
        """
        conds = _filters(time_start=since, time_end=until)
        async with _session_for(self._session) as (session, _owns):
            daily = (await session.execute(_stats_daily_stmt(conds))).all()
            by_provider = (
                await session.execute(
                    _stats_group_stmt(ai_call_logs_tbl.c.provider, conds)
                )
            ).all()
            by_model = (
                await session.execute(
                    _stats_group_stmt(ai_call_logs_tbl.c.model, conds)
                )
            ).all()
            by_purpose = (
                await session.execute(
                    _stats_group_stmt(ai_call_logs_tbl.c.purpose, conds)
                )
            ).all()
        return {
            "daily": [_daily_row(row._mapping) for row in daily],
            "by_provider": [
                _group_row(row._mapping, "provider") for row in by_provider
            ],
            "by_model": [_group_row(row._mapping, "model") for row in by_model],
            "by_purpose": [
                _group_row(row._mapping, "purpose") for row in by_purpose
            ],
        }

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
