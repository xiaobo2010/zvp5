"""Oracle implementation of :class:`db.repos.protocols.AppLogsRepo`.

SQLAlchemy 2.0 async Core over the ``APP_LOGS`` table (``zvms_new.app_logs``).
The Oracle-safe column renames (``user``→``APP_USER``, ``type``→``LTYPE``,
``level``→``LOG_LEVEL``, ``timestamp``→``TS_AT``/``TS_RAW``) are translated back
to the Mongo document shape on read.

``TS_RAW`` keeps the original float epoch seconds (authoritative for ordering and
the time-range filter); ``TS_AT`` is its derived naive ``TIMESTAMP``.

Session ownership mirrors :mod:`db.repos.oracle.users`: with no injected session
each method opens its own through :func:`db.oracle.session.session_scope`, and
writes commit once; an injected session is neither committed nor closed.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

import re
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import delete, func, insert, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid
from db.models import app_logs as app_logs_tbl
from db.models import users as users_tbl
from db.oracle.session import session_scope

Row = dict[str, Any]


def _fit_utf8(value: str, limit: int) -> str:
    return value.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def _chunks_utf8(value: str, limit: int = 2000):
    remaining = value.encode("utf-8")
    if not remaining:
        yield ""
    while remaining:
        chunk = remaining[:limit].decode("utf-8", errors="ignore")
        yield chunk
        remaining = remaining[len(chunk.encode("utf-8")):]


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


def _log_columns() -> tuple[Any, ...]:
    """读取投影：日志本体 + ``USERS`` 解析出的显示名/学号。

    ``APP_USER`` 存的是 oid，直接回前端就是「一串哈希」。显示名在这里用
    LEFT JOIN 解析，历史行与新行一视同仁；写路径仍只落 oid（``performer``
    过滤与 DB 语义都依赖它）。``users`` 侧两列在左连接未命中时是 NULL，
    由调用方回落（``app_user`` 为空串 = 未登录请求）。
    """
    return (
        app_logs_tbl.c.oid,
        app_logs_tbl.c.url,
        app_logs_tbl.c.app_user,
        app_logs_tbl.c.data,
        app_logs_tbl.c.ts_at,
        app_logs_tbl.c.ts_raw,
        app_logs_tbl.c.ltype,
        app_logs_tbl.c.log_level,
        app_logs_tbl.c.xuehai_id,
        users_tbl.c.name.label("user_name"),
        users_tbl.c.business_id.label("user_id"),
    )


def _log_from() -> Any:
    """``app_logs LEFT JOIN users``：必须 LEFT。

    ``app_user`` 可能是空串（未登录请求）或指向已被删除的用户；内连接会把
    这些行从列表里删掉，管理员就再也看不到「匿名」和「已删号」的记录了。
    """
    return app_logs_tbl.outerjoin(users_tbl, app_logs_tbl.c.app_user == users_tbl.c.oid)


def _insert_stmt(
    oid: str,
    url: str,
    app_user: str,
    data: str,
    ts_at: Any,
    ts_raw: float,
    ltype: str,
    log_level: str,
    xuehai_id: str,
):
    return insert(app_logs_tbl).values(
        oid=oid,
        url=url,
        app_user=app_user,
        data=data,
        ts_at=ts_at,
        ts_raw=ts_raw,
        ltype=ltype,
        log_level=log_level,
        xuehai_id=xuehai_id,
    )


def _filter(
    query: str,
    performer: str,
    types: list[str] | None,
    levels: list[str] | None,
    time_start: int | None,
    time_end: int | None,
    xuehai_id: str = "",
) -> list[Any]:
    """Admin-browser ``$match`` (``logs_router``): escaped url/data contains etc."""
    conds: list[Any] = []
    if query:
        pattern = re.escape(query)
        conds.append(
            or_(
                func.regexp_like(app_logs_tbl.c.url, pattern),
                func.regexp_like(app_logs_tbl.c.data, pattern),
            )
        )
    if performer:
        conds.append(app_logs_tbl.c.app_user == performer)
    if types:
        conds.append(app_logs_tbl.c.ltype.in_(list(types)))
    if levels:
        conds.append(app_logs_tbl.c.log_level.in_(list(levels)))
    if time_start is not None:
        conds.append(app_logs_tbl.c.ts_raw > time_start)
    if time_end is not None:
        conds.append(app_logs_tbl.c.ts_raw < time_end)
    if xuehai_id:
        conds.append(app_logs_tbl.c.xuehai_id == xuehai_id)
    return conds


def _list_stmt(
    query: str,
    performer: str,
    types: list[str] | None,
    levels: list[str] | None,
    time_start: int | None,
    time_end: int | None,
    page: int,
    size: int,
    xuehai_id: str = "",
):
    stmt = select(*_log_columns()).select_from(_log_from())
    conds = _filter(query, performer, types, levels, time_start, time_end, xuehai_id)
    if conds:
        stmt = stmt.where(*conds)
    return (
        stmt.order_by(app_logs_tbl.c.ts_raw.desc())
        .offset(0 if page == -1 else (page - 1) * size)
        .limit(size)
    )


def _count_stmt(
    query: str,
    performer: str,
    types: list[str] | None,
    levels: list[str] | None,
    time_start: int | None,
    time_end: int | None,
    xuehai_id: str = "",
):
    stmt = select(func.count()).select_from(app_logs_tbl)
    conds = _filter(query, performer, types, levels, time_start, time_end, xuehai_id)
    if conds:
        stmt = stmt.where(*conds)
    return stmt


def _for_user_filter(user_oid: str, query: str) -> list[Any]:
    """Per-user ``$match`` (``users_router``): raw (unescaped) url/data contains."""
    conds: list[Any] = [app_logs_tbl.c.app_user == user_oid]
    if query:
        conds.append(
            or_(
                func.regexp_like(app_logs_tbl.c.url, query),
                func.regexp_like(app_logs_tbl.c.data, query),
            )
        )
    return conds


def _list_for_user_stmt(user_oid: str, query: str, page: int, size: int):
    stmt = (
        select(*_log_columns())
        .select_from(_log_from())
        .where(*_for_user_filter(user_oid, query))
    )
    return (
        stmt.order_by(app_logs_tbl.c.ts_raw.desc())
        .offset(0 if page == -1 else (page - 1) * size)
        .limit(size)
    )


def _count_for_user_stmt(user_oid: str, query: str):
    return (
        select(func.count())
        .select_from(app_logs_tbl)
        .where(*_for_user_filter(user_oid, query))
    )


# Log types that are never purged (design §2): security/compliance records whose
# retention is a signed-off policy decision, not an operational one. Narrowing
# this set needs the same sign-off.
PROTECTED_LTYPES = (
    "security_alert",
    "ban_change",
    "passwd_change",
    "binding_mismatch",
    "device_bind",
    "device_unbind",
    # 临时密码 / 设备封禁链路（设计 §7）：证据链一旦被保留策略删掉，
    # 「为什么封了这台机器」就永久无法追溯，故与 passwd_change 同级保护。
    "temp_password_create",
    "temp_password_login",
    "temp_password_fail",
    "device_mismatch",
    "device_ban",
    # 规则被停用时「本该封但没封」与「设备封禁被跳过」同样是追责证据。
    "ban_rule_skipped",
    "device_ban_skipped",
    "system_maintenance_enable",
    "system_maintenance_disable",
)

# Warning-level rows are protected as well, whatever their type.
PROTECTED_LEVEL = "warning"


def _expired_conds(cutoff: Any) -> list[Any]:
    """WHERE shared by the expiry count and the batched purge (design §2).

    ``TS_AT`` is the only indexed time column (``ix_app_logs_ts_at``) and is
    derived from ``TS_RAW`` on insert (``app_logs.py`` write path), so the two
    agree. Rows whose ``TS_AT`` is NULL are never touched: a comparison against
    NULL is never true.

    The ``IS NULL OR`` branches are load-bearing: Oracle treats ``''`` as NULL
    and :class:`typings.log.ZVMSLog` defaults ``type``/``level`` to ``""``, so a
    bare ``NOT (ltype IN ...)`` would evaluate to NULL and keep every ordinary
    row forever.
    """
    return [
        app_logs_tbl.c.ts_at < cutoff,
        or_(
            app_logs_tbl.c.ltype.is_(None),
            app_logs_tbl.c.ltype.not_in(PROTECTED_LTYPES),
        ),
        or_(
            app_logs_tbl.c.log_level.is_(None),
            app_logs_tbl.c.log_level != PROTECTED_LEVEL,
        ),
    ]


def _count_expired_stmt(cutoff: Any):
    """How many rows the purge predicate currently matches (no batching)."""
    return select(func.count()).select_from(app_logs_tbl).where(*_expired_conds(cutoff))


def _count_distinct_xuehai_stmt(user_oid: str, since_ts: float):
    """用户在时间窗内登录过的不同学海设备数（规则封禁的证据查询）。

    谓词刻意收窄成 ``ltype = 'user_login'``：其它事件也会带上 ``Xuehai-Id``
    头，但只有登录才代表「这台设备正在使用这个账号」。

    不用 ``xuehai_id != ''``：Oracle 把空串当 NULL，``col != NULL`` 恒为 NULL，
    整张表会被过滤成 0 行。改用 ``length(trim(col)) > 0``，NULL 与纯空白一起
    被排除，语义才是想要的「非空且非空串」。

    ``ts_raw``（BINARY_DOUBLE epoch 秒）是时间窗的权威列，``ix_app_logs_user_ts``
    让 ``app_user`` + ``ts_raw`` 两个谓词都走索引。
    """
    return (
        select(func.count(app_logs_tbl.c.xuehai_id.distinct()))
        .select_from(app_logs_tbl)
        .where(
            app_logs_tbl.c.app_user == user_oid,
            app_logs_tbl.c.ltype == "user_login",
            func.length(func.trim(app_logs_tbl.c.xuehai_id)) > 0,
            app_logs_tbl.c.ts_raw >= since_ts,
        )
    )


def _purge_batch_stmt(cutoff: Any, batch_size: int):
    """One ``DELETE`` bounded to ``batch_size`` rows.

    ``ROWNUM`` is used instead of ``FETCH FIRST n ROWS ONLY``: the latter is not
    accepted on a ``DELETE`` in Oracle.
    """
    return delete(app_logs_tbl).where(
        *_expired_conds(cutoff),
        text("ROWNUM <= :batch_size").bindparams(batch_size=batch_size),
    )


def _log_row(mapping: Any) -> Row:
    """Mongo document shape; ``timestamp`` is the preserved raw float.

    ``user_name``/``user_id`` 来自 LIST 语句的 ``users`` 左连接，只读路径才有；
    写路径与单行构造（若被复用）走 ``.get`` 回落 None。``user`` 保持 oid：
    它是 ``performer`` 过滤与审计溯源的锚点。
    """
    ts_raw = mapping["ts_raw"]
    if ts_raw is None and mapping["ts_at"] is not None:
        ts_raw = mapping["ts_at"].timestamp()
    return {
        "_id": mapping["oid"],
        "url": mapping["url"],
        "user": mapping["app_user"],
        "user_name": mapping.get("user_name"),
        "user_id": mapping.get("user_id"),
        "data": mapping["data"],
        "timestamp": ts_raw,
        "type": mapping["ltype"],
        "level": mapping["log_level"],
        "xuehai_id": mapping["xuehai_id"],
    }


class OracleAppLogsRepo:
    """``AppLogsRepo`` over Oracle ADB (``zvms_new.app_logs``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def insert(
        self,
        url: str,
        user: str,
        data: str,
        timestamp: float,
        ltype: str,
        level: str,
        xuehai_id: str,
    ) -> None:
        ts_raw = float(timestamp) if timestamp is not None else None
        ts_at = datetime.fromtimestamp(ts_raw) if ts_raw is not None else None
        async with _session_for(self._session) as (session, owns):
            for chunk in _chunks_utf8(data):
                stmt = _insert_stmt(
                    new_oid(), _fit_utf8(url, 512), _fit_utf8(user, 64), chunk,
                    ts_at, ts_raw, _fit_utf8(ltype, 64), _fit_utf8(level, 32),
                    _fit_utf8(xuehai_id, 32),
                )
                await session.execute(stmt)
            if owns:
                await session.commit()

    async def list_page(
        self,
        query: str = "",
        performer: str = "",
        types: list[str] | None = None,
        levels: list[str] | None = None,
        time_start: int | None = None,
        time_end: int | None = None,
        xuehai_id: str = "",
        page: int = -1,
        size: int = 10,
    ) -> tuple[list[Row], int]:
        page_stmt = _list_stmt(
            query, performer, types, levels, time_start, time_end, page, size, xuehai_id
        )
        count_stmt = _count_stmt(
            query, performer, types, levels, time_start, time_end, xuehai_id
        )
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(count_stmt)).scalar_one()
            rows = (await session.execute(page_stmt)).all()
        return [_log_row(row._mapping) for row in rows], int(total)

    async def list_for_user(
        self, user_oid: str, query: str = "", page: int = -1, size: int = 10
    ) -> tuple[list[Row], int]:
        page_stmt = _list_for_user_stmt(user_oid, query, page, size)
        count_stmt = _count_for_user_stmt(user_oid, query)
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(count_stmt)).scalar_one()
            rows = (await session.execute(page_stmt)).all()
        return [_log_row(row._mapping) for row in rows], int(total)

    async def count_expired(self, cutoff: datetime) -> int:
        """Rows :meth:`purge_batch` would eventually delete (read-only)."""
        stmt = _count_expired_stmt(cutoff)
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(stmt)).scalar_one()
        return int(total)

    async def count_distinct_xuehai(self, user_oid: str, since_ts: float) -> int:
        """用户在 ``since_ts`` 之后登录过的不同学海设备数（规则封禁判定用）。"""
        stmt = _count_distinct_xuehai_stmt(user_oid, float(since_ts))
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(stmt)).scalar_one()
        return int(total)

    async def purge_batch(self, cutoff: datetime, batch_size: int) -> int:
        """Delete at most ``batch_size`` rows older than ``cutoff``; return the count.

        ``0`` means nothing is left to purge. One short session and one commit
        per call — the batching loop lives in :mod:`tasks.retention`.
        """
        stmt = _purge_batch_stmt(cutoff, batch_size)
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return int(result.rowcount)


__all__ = ["OracleAppLogsRepo"]
