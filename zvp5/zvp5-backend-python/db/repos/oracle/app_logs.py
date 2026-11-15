"""Oracle implementation of :class:`db.repos.protocols.AppLogsRepo`.

SQLAlchemy 2.0 async Core over the ``APP_LOGS`` table (``zvms_new.app_logs``).
The Oracle-safe column renames (``user``→``APP_USER``, ``type``→``LTYPE``,
``level``→``LOG_LEVEL``, ``timestamp``→``TS_AT``/``TS_RAW``) are translated back
to the Mongo document shape on read.

``TS_RAW`` keeps the original float epoch seconds (authoritative for ordering, the
time-range filter **and** the retention purge); ``TS_AT`` is its derived naive
``TIMESTAMP`` and is kept as data only (no index).

Session ownership mirrors :mod:`db.repos.oracle.users`: with no injected session
each method opens its own through :func:`db.oracle.session.session_scope`, and
writes commit once; an injected session is neither committed nor closed.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import delete, func, insert, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid
from db.models import app_logs as app_logs_tbl
from db.models import users as users_tbl
from db.oracle.session import session_scope
from db.repos.oracle.search import like_pattern
from db.repos.pagination import page_args, page_bounds

Row = dict[str, Any]

#: Oracle 一条 ``IN`` 的元素上限是 1000，超了就是 ORA-01795。``type``/``level``
#: 来自逗号串、没有天然上界，所以按 500 分块（与
#: ``db/repos/oracle/files.py::_OID_CHUNK`` 同口径，留一半余量）。
_IN_CHUNK = 500


def _chunked_in(column: Any, values: list[str]) -> Any:
    """``IN`` 超过 :data:`_IN_CHUNK` 项时改写成 ``OR c IN (...) OR c IN (...)``。

    与 ``db/repos/oracle/activities.py::_chunked_in`` 同一手法：分块发生在**一条**
    语句内部，Oracle 只看到一串过滤条件，``ORDER BY``/分页/``COUNT`` 都还留在
    SQL 侧；拆成多条语句执行会把排序分页按块各做一遍，语义就变了。
    """
    chunks = [
        values[start : start + _IN_CHUNK]
        for start in range(0, len(values), _IN_CHUNK)
    ]
    if not chunks:
        # 空集合维持原语义：匹配不到任何行（而非「不过滤」）。
        return column.in_([])
    return or_(*(column.in_(chunk) for chunk in chunks))


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
        app_logs_tbl.c.client_ip,
        app_logs_tbl.c.user_agent,
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
    client_ip: str,
    user_agent: str,
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
        client_ip=client_ip,
        user_agent=user_agent,
    )


#: Hard cap on one page, mirrored by ``logs_router``'s ``perpage`` ``le=``.
PAGE_SIZE_MAX = 100


def _search_conds(query: str) -> Any:
    """关键字匹配 ``url`` 或 ``data``（大小写不敏感）。

    默认搜索必须仍然能命中 url 与 data 里的关键字，所以两支都保留；成本控制
    交给 ``LIKE``（普通字符串比较，不是每行编译正则）和调用方的可索引谓词。
    ``data`` 在 ``db/models.py`` 里是 ``VARCHAR2(2000 CHAR)``（写入按 2000 字节
    切块，见 :func:`_chunks_utf8`），不是 CLOB/LOB 定位符——这里不会退化成
    正则全表扫描，也无需 ``DBMS_LOB``。
    """
    pattern = like_pattern(query)
    return or_(
        func.lower(app_logs_tbl.c.url).like(pattern, escape="\\"),
        func.lower(app_logs_tbl.c.data).like(pattern, escape="\\"),
    )


def _filter(
    query: str,
    performer: str,
    types: list[str] | None,
    levels: list[str] | None,
    time_start: int | None,
    time_end: int | None,
    xuehai_id: str = "",
    exclude_types: list[str] | None = None,
) -> list[Any]:
    """Admin-browser ``$match`` (``logs_router``): LIKE contains url/data etc.

    ``exclude_types`` 只在**没有** ``types`` 白名单时生效（``logs_router`` 用它
    默认隐藏 ``login_fail``）；显式指定类型时管理员要看什么就给什么。

    ``types``/``levels`` 走 :func:`_chunked_in`：两者都来自逗号串，值多了会撞上
    Oracle 的 ``IN`` 元素上限（ORA-01795 → 500），分块后单条语句仍然成立。
    """
    conds: list[Any] = []
    if query:
        conds.append(_search_conds(query))
    if performer:
        conds.append(app_logs_tbl.c.app_user == performer)
    if types:
        conds.append(_chunked_in(app_logs_tbl.c.ltype, list(types)))
    elif exclude_types:
        # ``IS NULL OR`` 这一支是承重的：Oracle 把空串当 NULL（``ZVMSLog`` 的
        # ``type`` 默认 ``""``），裸 ``NOT IN`` 会把这类普通行一起过滤成 NULL。
        conds.append(
            or_(
                app_logs_tbl.c.ltype.is_(None),
                app_logs_tbl.c.ltype.not_in(list(exclude_types)),
            )
        )
    if levels:
        conds.append(_chunked_in(app_logs_tbl.c.log_level, list(levels)))
    if time_start is not None:
        conds.append(app_logs_tbl.c.ts_raw > time_start)
    if time_end is not None:
        conds.append(app_logs_tbl.c.ts_raw < time_end)
    if xuehai_id:
        conds.append(app_logs_tbl.c.xuehai_id == xuehai_id)
    return conds


def _page_columns() -> tuple[Any, ...]:
    """列表投影 + ``COUNT(*) OVER()`` 总数。

    ``APP_LOGS``↔``USERS`` 是 ``app_user = oid`` 的 1:1 左连接（``oid`` 是主键），
    所以窗口计数与「匹配行数」相等，一页一条语句就能同时拿到数据与总数。
    """
    return (*_log_columns(), func.count().over().label("total_count"))


def _total_of(rows: Any) -> int | None:
    """``COUNT(*) OVER()`` 带回的总数；空页时不可观测 → ``None``。

    与 :mod:`db.repos.oracle.users` 同一范式：空结果由调用方决定是「第一页本来
    就空」（0）还是「页码越界」（补一条 count 语句）。
    """
    if not rows:
        return None
    return int(rows[0]._mapping["total_count"])


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
    exclude_types: list[str] | None = None,
):
    """一页日志 + 总数：``COUNT(*) OVER()`` 省掉每页一次全表 COUNT。

    排序走 ``ix_app_logs_ts_raw``（V018），过滤走 ``ltype``/``app_user``/
    ``xuehai_id``/``log_level`` 上的索引或复合索引前缀。
    """
    offset, limit = page_args(page, size, max_size=PAGE_SIZE_MAX)
    stmt = select(*_page_columns()).select_from(_log_from())
    conds = _filter(
        query, performer, types, levels, time_start, time_end, xuehai_id, exclude_types
    )
    if conds:
        stmt = stmt.where(*conds)
    return (
        stmt.order_by(app_logs_tbl.c.ts_raw.desc())
        .offset(offset)
        .limit(limit)
    )


def _count_stmt(
    query: str,
    performer: str,
    types: list[str] | None,
    levels: list[str] | None,
    time_start: int | None,
    time_end: int | None,
    xuehai_id: str = "",
    exclude_types: list[str] | None = None,
):
    """页码越界（窗口计数不可观测）时的兜底总数。

    正常翻页不再执行本语句：``_list_stmt`` 已经把总数带回来了。
    """
    stmt = select(func.count()).select_from(app_logs_tbl)
    conds = _filter(
        query, performer, types, levels, time_start, time_end, xuehai_id, exclude_types
    )
    if conds:
        stmt = stmt.where(*conds)
    return stmt


def _for_user_filter(user_oid: str, query: str) -> list[Any]:
    """Per-user ``$match`` (``users_router``): ``app_user`` + LIKE url/data."""
    conds: list[Any] = [app_logs_tbl.c.app_user == user_oid]
    if query:
        conds.append(_search_conds(query))
    return conds


def _list_for_user_stmt(user_oid: str, query: str, page: int, size: int):
    offset, limit = page_args(page, size, max_size=PAGE_SIZE_MAX)
    stmt = (
        select(*_page_columns())
        .select_from(_log_from())
        .where(*_for_user_filter(user_oid, query))
    )
    return (
        stmt.order_by(app_logs_tbl.c.ts_raw.desc())
        .offset(offset)
        .limit(limit)
    )


def _count_for_user_stmt(user_oid: str, query: str):
    """页码越界时的兜底总数（同 :func:`_count_stmt`）。"""
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
    # SSH 命令全文是 root 级操作的追责证据，写的是 level="info"
    # （routers/system_ssh_router.py），不进本元组就会被保留期批量清掉。
    "system_ssh_command",
    # AI 起草的命令建议（脱敏后的前 200 字符）：它本身没被执行，但「谁让模型
    # 起草了什么」是同一份运维责任链的上游一环，保留期与 system_ssh_command 同级。
    "system_ssh_suggest",
)

# Warning-level rows are protected as well, whatever their type.
PROTECTED_LEVEL = "warning"


def _expired_conds(cutoff: Any) -> list[Any]:
    """WHERE shared by the expiry count and the batched purge (design §2).

    ``TS_RAW`` (BINARY_DOUBLE epoch seconds) is the time source of truth: the write
    path stores the epoch and derives ``TS_AT`` from it
    (``datetime.fromtimestamp``), and the admin page orders/filters on ``TS_RAW`` as
    well. Purging on the same column keeps one semantic instead of two — ``TS_AT``
    is a naive local timestamp, so it would drift with a server timezone change
    while ``TS_RAW`` would not. ``ix_app_logs_ts_raw`` serves the range scan;
    ``TS_AT`` is data only, with no index.

    ``cutoff`` is the naive local ``datetime`` the caller builds
    (``tasks.retention``); ``.timestamp()`` interprets it the same way
    ``datetime.fromtimestamp`` did when ``TS_AT`` was written, so the boundary is
    unchanged. Rows whose ``TS_RAW`` is NULL are never touched: a comparison
    against NULL is never true.

    The ``IS NULL OR`` branches are load-bearing: Oracle treats ``''`` as NULL
    and :class:`typings.log.ZVMSLog` defaults ``type``/``level`` to ``""``, so a
    bare ``NOT (ltype IN ...)`` would evaluate to NULL and keep every ordinary
    row forever.
    """
    return [
        app_logs_tbl.c.ts_raw < float(cutoff.timestamp()),
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


def _count_by_user_ltype_stmt(
    user_oid: str, ltype: str, since_ts: float, contains: str | None = None
):
    """用户 + 日志类型 + 时间窗的事件计数（``services.risk_monitor`` 的证据）。

    谓词顺序对应 ``ix_app_logs_user_ts``（``app_user`` + ``ts_raw``）与
    ``ix_app_logs_type_level_ts`` 的前缀 ``ltype``：等值列在前、范围列在后，
    两个索引都能被吃到。``contains`` 是**补充**条件（同一 ``ltype`` 下靠 ``data``
    摘要区分两条规则，例如登录失败里的 ``banned`` 标记），走 ``LOWER(data) LIKE``
    并转义通配符，让它只是字符串比较而不是正则。
    """
    conds: list[Any] = [
        app_logs_tbl.c.app_user == user_oid,
        app_logs_tbl.c.ltype == ltype,
        app_logs_tbl.c.ts_raw >= float(since_ts),
    ]
    if contains:
        conds.append(
            func.lower(app_logs_tbl.c.data).like(
                like_pattern(contains), escape="\\"
            )
        )
    return select(func.count()).select_from(app_logs_tbl).where(*conds)


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
        "client_ip": mapping.get("client_ip"),
        "user_agent": mapping.get("user_agent"),
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
        client_ip: str = "",
        user_agent: str = "",
    ) -> None:
        ts_raw = float(timestamp) if timestamp is not None else None
        ts_at = datetime.fromtimestamp(ts_raw) if ts_raw is not None else None
        async with _session_for(self._session) as (session, owns):
            for chunk in _chunks_utf8(data):
                stmt = _insert_stmt(
                    new_oid(), _fit_utf8(url, 512), _fit_utf8(user, 64), chunk,
                    ts_at, ts_raw, _fit_utf8(ltype, 64), _fit_utf8(level, 32),
                    _fit_utf8(xuehai_id, 32), _fit_utf8(client_ip, 45),
                    _fit_utf8(user_agent, 256),
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
        page: int = 1,
        size: int = 10,
        exclude_types: list[str] | None = None,
    ) -> tuple[list[Row], int]:
        """一页日志与总数 ``(rows, total)``；``page`` 从 1 起，``size`` 上限 100。

        总数由 ``COUNT(*) OVER()`` 与数据同一条语句返回，常规翻页不再额外执行
        ``COUNT(*)``。只有空页（页码越界）才补一条兜底 count——空页本身看不出
        真实总数，且第一页为空时总数必然为 0，无需查库。

        ``exclude_types`` 是黑名单，仅在 ``types`` 为空时生效（管理端默认隐藏
        ``login_fail``，显式传 ``types`` 则原样按白名单查）。
        """
        page, size = page_bounds(page, size, max_size=PAGE_SIZE_MAX)
        page_stmt = _list_stmt(
            query,
            performer,
            types,
            levels,
            time_start,
            time_end,
            page,
            size,
            xuehai_id,
            exclude_types,
        )
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(page_stmt)).all()
            total = _total_of(rows)
            if total is None:
                total = (
                    0
                    if page <= 1
                    else int(
                        (
                            await session.execute(
                                _count_stmt(
                                    query,
                                    performer,
                                    types,
                                    levels,
                                    time_start,
                                    time_end,
                                    xuehai_id,
                                    exclude_types,
                                )
                            )
                        ).scalar_one()
                    )
                )
        return [_log_row(row._mapping) for row in rows], int(total)

    async def list_for_user(
        self, user_oid: str, query: str = "", page: int = 1, size: int = 10
    ) -> tuple[list[Row], int]:
        """某用户的一页日志与总数，分页/计数口径与 :meth:`list_page` 一致。"""
        page, size = page_bounds(page, size, max_size=PAGE_SIZE_MAX)
        page_stmt = _list_for_user_stmt(user_oid, query, page, size)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(page_stmt)).all()
            total = _total_of(rows)
            if total is None:
                total = (
                    0
                    if page <= 1
                    else int(
                        (await session.execute(_count_for_user_stmt(user_oid, query)))
                        .scalar_one()
                    )
                )
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

    async def count_by_user_ltype(
        self,
        user_oid: str,
        ltype: str,
        since_ts: float,
        contains: str | None = None,
    ) -> int:
        """``since_ts`` 之后该用户该类型的事件条数（只读，判定用计数）。

        ``contains`` 在 ``data`` 上做大小写不敏感的 contains，用于把共享同一
        ``ltype`` 的两条规则分开（登录失败 vs 封禁期重试）。时间窗用 ``ts_raw``
        （BINARY_DOUBLE epoch 秒）——与写入路径同源，``ts_at`` 是它的派生列。
        """
        stmt = _count_by_user_ltype_stmt(
            user_oid, ltype, float(since_ts), contains
        )
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
