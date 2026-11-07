"""Oracle implementation of :class:`db.repos.protocols.AiScheduledTasksRepo`.

SQLAlchemy 2.0 async Core over ``AI_SCHEDULED_TASKS`` (用户自建的周期性智能体
任务, V040). 行形状是 Mongo 风格的 dict（``"_id"`` = 小写 hex ``OID``）。

四条承重约定（调度器会依赖，改之前先读）：

* **领取是 CAS，不是锁**。:meth:`claim` 的 WHERE 带上 ``next_run_at <= :now`` 与
  ``last_run_at = :expected``（``expected`` 来自 :meth:`claim_due` 读到的快照）。
  两个 tick 并发时只有一个 UPDATE 命中；而「领取后进程崩溃」也不会把任务永久
  卡死——下一次 tick 重新读快照，``expected`` 就是崩溃时写下的那个时间戳，
  CAS 自然通过。这是不用悲观锁也能自愈的写法。
* **:meth:`mark_result` 必须推进 ``next_run_at``**。执行、跳过、失败三条分支都要
  写它；漏写的后果是一条坏任务被每个 tick 反复重试，把日志和额度一起打爆。
* **``last_run_at`` 有两个含义**：最近一次「领取」的时间（不是完成时间），以及
  claim 的 CAS 期望值。它由 :meth:`claim` 写，:meth:`mark_result` 不再改它——
  改它会让并发的第二个 tick 拿着旧快照命中。
* **不建外键**：任务要活过账号删除（同 AI_CALL_LOGS / AI_CONVERSATIONS）。

Session ownership mirrors :mod:`db.repos.oracle.temp_passwords`: with no injected
session each method opens its own through :func:`db.oracle.session.session_scope`,
and writes commit once; an injected session is neither committed nor closed.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator, Mapping

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import ai_runs as ai_runs_tbl
from db.models import ai_scheduled_tasks as tasks_tbl
from db.oracle.session import session_scope
from db.repos.pagination import page_args

Row = dict[str, Any]

REVIEW_STATUSES = ("draft", "pending", "approved", "rejected")
SCHEDULE_KINDS = ("interval", "cron")

# 单用户任务上限的硬顶（防止 ``?limit=`` 被拿来一次拉全表）。
DEFAULT_LIST_LIMIT = 200
MAX_LIST_LIMIT = 500
# 单次 tick 领取上限：库里的到期任务再多，也不该在一个 tick 里排完整张表。
DEFAULT_CLAIM_LIMIT = 50

# 可更新列白名单：路由层传进来的 dict 直接进 UPDATE 之前必须过滤，否则一个
# 拼错的键会静默变成「什么都没改」，而一个恶意的键能改到不该改的列。
UPDATE_COLUMNS = frozenset(
    {
        "name",
        "prompt_text",
        "review_status",
        "reviewed_by_oid",
        "reviewed_at",
        "review_note",
        "schedule_kind",
        "interval_minutes",
        "cron_expr",
        "enabled",
        "model",
        "next_run_at",
    }
)

# 「某任务执行历史」的投影：只取列表要展示的列，正文/消息不进这一层。
_RUN_COLUMNS = (
    ai_runs_tbl.c.oid,
    ai_runs_tbl.c.status,
    ai_runs_tbl.c.model,
    ai_runs_tbl.c.stop_reason,
    ai_runs_tbl.c.error_code,
    ai_runs_tbl.c.error_message,
    ai_runs_tbl.c.created_at,
    ai_runs_tbl.c.started_at,
    ai_runs_tbl.c.finished_at,
)


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
    tbl = tasks_tbl
    return (
        tbl.c.oid,
        tbl.c.owner_oid,
        tbl.c.name,
        tbl.c.prompt_text,
        tbl.c.review_status,
        tbl.c.reviewed_by_oid,
        tbl.c.reviewed_at,
        tbl.c.review_note,
        tbl.c.schedule_kind,
        tbl.c.interval_minutes,
        tbl.c.cron_expr,
        tbl.c.enabled,
        tbl.c.model,
        tbl.c.last_run_at,
        tbl.c.next_run_at,
        tbl.c.run_count,
        tbl.c.last_status,
        tbl.c.last_error,
        tbl.c.created_at,
        tbl.c.updated_at,
    )


def _task_row(mapping: Any) -> Row:
    return {
        "_id": mapping["oid"],
        "owner_oid": mapping["owner_oid"],
        "name": mapping["name"],
        "prompt_text": mapping["prompt_text"],
        "review_status": mapping["review_status"],
        "reviewed_by_oid": mapping["reviewed_by_oid"],
        "reviewed_at": mapping["reviewed_at"],
        "review_note": mapping["review_note"],
        "schedule_kind": mapping["schedule_kind"],
        "interval_minutes": int(mapping["interval_minutes"] or 0),
        "cron_expr": mapping["cron_expr"],
        "enabled": bool(mapping["enabled"]),
        "model": mapping["model"],
        "last_run_at": mapping["last_run_at"],
        "next_run_at": mapping["next_run_at"],
        "run_count": int(mapping["run_count"] or 0),
        "last_status": mapping["last_status"],
        "last_error": mapping["last_error"],
        "created_at": mapping["created_at"],
        "updated_at": mapping["updated_at"],
    }


def _run_row(mapping: Any) -> Row:
    return {
        "_id": mapping["oid"],
        "status": mapping["status"],
        "model": mapping["model"],
        "stop_reason": mapping["stop_reason"],
        "error_code": mapping["error_code"],
        "error_message": mapping["error_message"],
        "created_at": mapping["created_at"],
        "started_at": mapping["started_at"],
        "finished_at": mapping["finished_at"],
    }


def _due_stmt(now: datetime, limit: int):
    """到期待领取的任务，最久没跑的排前面。

    ``next_run_at IS NOT NULL`` 是必要的：新建但还没排程过的行（PATCH 之前）不该
    被当成「1970 年就该跑」。行锁不加——claim 的 CAS 已经足够，加锁只会让两个
    tick 串行等同一条。
    """
    return (
        select(*_columns())
        .where(
            tasks_tbl.c.enabled == 1,
            tasks_tbl.c.next_run_at.is_not(None),
            tasks_tbl.c.next_run_at <= now,
        )
        .order_by(tasks_tbl.c.next_run_at.asc(), tasks_tbl.c.oid.asc())
        .limit(limit)
    )


def _claim_stmt(oid: str, now: datetime, expected: datetime | None):
    """领取一条任务：``rowcount = 0`` 说明别人先领了（并发或快照过期）。

    ``expected`` 为 ``None`` 时要写 ``IS NULL``：Oracle 里 ``= NULL`` 恒为假，
    直接绑参数会让「从未跑过」的任务永远领不到。
    """
    conditions = [
        tasks_tbl.c.oid == oid,
        tasks_tbl.c.enabled == 1,
        tasks_tbl.c.next_run_at.is_not(None),
        tasks_tbl.c.next_run_at <= now,
    ]
    if expected is None:
        conditions.append(tasks_tbl.c.last_run_at.is_(None))
    else:
        conditions.append(tasks_tbl.c.last_run_at == expected)
    return update(tasks_tbl).where(*conditions).values(last_run_at=now)


def _count_active_stmt(owner_oid: str):
    """配额口径：**只算未被驳回的**。

    被驳回的任务不占额度——否则用户被拒一次就永久失去配额，只能求管理员删行。
    """
    return (
        select(func.count())
        .select_from(tasks_tbl)
        .where(
            tasks_tbl.c.owner_oid == owner_oid,
            tasks_tbl.c.review_status != "rejected",
        )
    )


def _list_runs_stmt(task_oid: str, offset: int, limit: int):
    return (
        select(*_RUN_COLUMNS)
        .where(ai_runs_tbl.c.task_oid == task_oid)
        .order_by(ai_runs_tbl.c.created_at.desc(), ai_runs_tbl.c.oid.desc())
        .offset(offset)
        .limit(limit)
    )


def _runs_total_stmt(task_oid: str):
    return (
        select(func.count())
        .select_from(ai_runs_tbl)
        .where(ai_runs_tbl.c.task_oid == task_oid)
    )


def _mark_result_stmt(
    oid: str,
    *,
    last_status: str,
    last_error: str | None,
    next_run_at: datetime,
    counted: bool,
    at: datetime,
):
    """写调度结论。``counted=True`` 才 ``run_count + 1``（跳过不加计数）。"""
    values: dict[str, Any] = {
        "last_status": last_status,
        "last_error": last_error,
        "next_run_at": next_run_at,
        "updated_at": at,
    }
    stmt = update(tasks_tbl).where(tasks_tbl.c.oid == normalize_oid(oid))
    if counted:
        return stmt.values(**values, run_count=tasks_tbl.c.run_count + 1)
    return stmt.values(**values)


# ---------------------------------------------------------------------------
# repo
# ---------------------------------------------------------------------------

class OracleAiScheduledTasksRepo:
    """``AiScheduledTasksRepo`` over Oracle ADB (``AI_SCHEDULED_TASKS``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def create(
        self,
        *,
        owner_oid: str,
        name: str,
        prompt_text: str,
        schedule_kind: str,
        interval_minutes: int,
        cron_expr: str | None,
        next_run_at: datetime | None,
        run_at: datetime | None = None,
        review_status: str = "draft",
        model: str | None = None,
    ) -> Row:
        """落一条任务并回传整行（调用方要立刻回 201 的视图）。"""
        now = run_at or _now()
        oid = new_oid()
        stmt = insert(tasks_tbl).values(
            oid=oid,
            owner_oid=normalize_oid(owner_oid),
            name=name,
            prompt_text=prompt_text,
            review_status=review_status,
            reviewed_by_oid=None,
            reviewed_at=None,
            review_note=None,
            schedule_kind=schedule_kind,
            interval_minutes=int(interval_minutes),
            cron_expr=cron_expr,
            enabled=1,
            model=model,
            last_run_at=None,
            next_run_at=next_run_at,
            run_count=0,
            last_status=None,
            last_error=None,
            created_at=now,
            updated_at=now,
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return await self._require(oid)

    async def get(self, oid: str) -> Row | None:
        normalized = normalize_oid(oid)
        async with _session_for(self._session) as (session, _owns):
            row = (
                await session.execute(
                    select(*_columns()).where(tasks_tbl.c.oid == normalized)
                )
            ).first()
        return _task_row(row._mapping) if row is not None else None

    async def _require(self, oid: str) -> Row:
        row = await self.get(oid)
        if row is None:  # 刚插入就被删：理论上不可能，检查后宁可抛也不要回 None
            raise RuntimeError(f"ai scheduled task vanished after write: {oid}")
        return row

    async def list_for_owner(
        self, owner_oid: str, *, limit: int = DEFAULT_LIST_LIMIT
    ) -> list[Row]:
        """本人的任务，新的在前（``ix_ai_scheduled_tasks_owner_created``）。"""
        stmt = (
            select(*_columns())
            .where(tasks_tbl.c.owner_oid == normalize_oid(owner_oid))
            .order_by(tasks_tbl.c.created_at.desc(), tasks_tbl.c.oid.desc())
            .limit(min(int(limit), MAX_LIST_LIMIT))
        )
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_task_row(row._mapping) for row in rows]

    async def list_all(
        self,
        *,
        owner_oid: str | None = None,
        review_status: str | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> list[Row]:
        """管理端视图：可跨用户，按创建时间倒序。

        ``review_status`` 是审核队列的过滤条件（管理员只看 ``pending``）；
        不在白名单里的值由调用方拦，这里不做枚举校验。
        """
        stmt = select(*_columns())
        if owner_oid:
            stmt = stmt.where(tasks_tbl.c.owner_oid == normalize_oid(owner_oid))
        if review_status:
            stmt = stmt.where(tasks_tbl.c.review_status == str(review_status))
        stmt = (
            stmt.order_by(tasks_tbl.c.created_at.desc(), tasks_tbl.c.oid.desc())
            .offset(max(int(offset), 0))
            .limit(min(int(limit), MAX_LIST_LIMIT))
        )
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_task_row(row._mapping) for row in rows]

    async def update(self, oid: str, values: Mapping[str, Any]) -> bool:
        """部分更新（白名单过滤），``False`` = 行不存在。

        不校验「属于谁」——那是服务层/路由层的判断；这一层只保证写的是允许的列。
        """
        payload = {
            key: value for key, value in dict(values).items() if key in UPDATE_COLUMNS
        }
        if not payload:
            return False
        payload["updated_at"] = _now()
        stmt = (
            update(tasks_tbl)
            .where(tasks_tbl.c.oid == normalize_oid(oid))
            .values(**payload)
        )
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return bool(result.rowcount)

    async def delete(self, oid: str) -> bool:
        stmt = delete(tasks_tbl).where(tasks_tbl.c.oid == normalize_oid(oid))
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return bool(result.rowcount)

    async def count_active_for_owner(self, owner_oid: str) -> int:
        async with _session_for(self._session) as (session, _owns):
            total = (
                await session.execute(_count_active_stmt(normalize_oid(owner_oid)))
            ).scalar()
        return int(total or 0)

    async def claim_due(
        self, now: datetime, *, limit: int = DEFAULT_CLAIM_LIMIT
    ) -> list[Row]:
        """领取所有到期任务：先读快照，再逐条 CAS。抢输的行不在返回值里。"""
        async with _session_for(self._session) as (session, _owns):
            snapshot = (await session.execute(_due_stmt(now, int(limit)))).all()
        claimed: list[Row] = []
        for row in snapshot:
            task = _task_row(row._mapping)
            if await self.claim(
                task["_id"], now, expected=task.get("last_run_at")
            ):
                task["last_run_at"] = now
                claimed.append(task)
        return claimed

    async def claim(
        self, oid: str, now: datetime, *, expected: datetime | None
    ) -> bool:
        """单条 CAS 领取。``expected`` 必须是读快照时的 ``last_run_at``。"""
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(
                _claim_stmt(normalize_oid(oid), now, expected)
            )
            if owns:
                await session.commit()
        return bool(result.rowcount)

    async def mark_result(
        self,
        oid: str,
        *,
        last_status: str,
        next_run_at: datetime,
        last_error: str | None = None,
        counted: bool = False,
    ) -> bool:
        """写本次调度结论并推进 ``next_run_at``。

        ``counted=True`` 表示这一次真的提交了 run，``run_count`` 才 +1：跳过分支
        加计数会让「跑过几次」这个数字失去意义。``last_run_at`` 不动（它是 claim
        的 CAS 令牌，见模块头）。
        """
        stmt = _mark_result_stmt(
            oid,
            last_status=last_status,
            last_error=last_error,
            next_run_at=next_run_at,
            counted=counted,
            at=_now(),
        )
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return bool(result.rowcount)

    async def list_runs(
        self, task_oid: str, *, page: int = 1, size: int = 20
    ) -> tuple[list[Row], int]:
        """某任务的执行历史（``ai_runs.task_oid``），新的在前。"""
        normalized = normalize_oid(task_oid)
        offset, size = page_args(page, size)
        async with _session_for(self._session) as (session, _owns):
            rows = (
                await session.execute(_list_runs_stmt(normalized, offset, size))
            ).all()
            total = (await session.execute(_runs_total_stmt(normalized))).scalar()
        return [_run_row(row._mapping) for row in rows], int(total or 0)

    async def last_conversation_oid(self, task_oid: str) -> str | None:
        """该任务最近一次 run 用的会话（复用，避免每次执行都新建一个会话）。"""
        stmt = (
            select(ai_runs_tbl.c.conversation_oid)
            .where(ai_runs_tbl.c.task_oid == normalize_oid(task_oid))
            .order_by(ai_runs_tbl.c.created_at.desc(), ai_runs_tbl.c.oid.desc())
            .limit(1)
        )
        async with _session_for(self._session) as (session, _owns):
            return (await session.execute(stmt)).scalar()

    async def attach_run_task(self, run_id: str, task_oid: str) -> bool:
        """把已入队的 run 归因到任务上（``insert_queued`` 不认识 task_oid）。

        必须在 ``kick()`` **之前**调用：泵一起来就可能开始执行，那时再补写，
        历史列表里会出现一条 task_oid 为空的窗口期记录。
        """
        stmt = (
            update(ai_runs_tbl)
            .where(ai_runs_tbl.c.oid == str(run_id))
            .values(task_oid=normalize_oid(task_oid))
        )
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return bool(result.rowcount)
