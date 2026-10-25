"""Oracle implementation of the agent run repo (``AI_RUNS``, V035).

一次智能体执行一行。``oid`` 同时是 ``ai_call_logs.request_id`` 与
``ai_agent_events.request_id``：调用流水、工具留痕、消息三条链路靠它串起来
（``docs/AI_CONVERSATION_DESIGN.md`` §2.3 / §4）。

四件承重的事：

* **入队是「用户消息 + run」的同一事务**。run 进入 ``running`` 之前唯一的持久化
  就是那条用户消息（§4.5），所以崩溃的 running 不会重跑扣费、queued 重跑零重复。
* **活跃唯一性有两道闸**。应用层的锁 + 检查是第一道；``uq_ai_runs_active_conv`` /
  ``uq_ai_runs_active_owner`` 两个函数唯一索引是第二道。``insert_queued`` 把
  ORA-00001 翻译成 :class:`ActiveRunConflict`，并且**先回滚**——冲突的提交不允许
  留下一条孤儿用户消息。
* **``client_msg_id`` 幂等**：撞 ``uq_ai_runs_client_msg`` 时返回已存在的 run
  （``created=False``），不新增消息、不重复计费。
* **状态迁移全部 CAS**（``WHERE status = <预期>``）。``finish`` 返回 ``False``
  意味着这一轮已经作废（watchdog 判了僵尸），调用方必须丢弃本轮而不是把结果
  写在终态行上——那会让「已扣费、已执行工具」的事实与 run 记录脱钩。

Session ownership mirrors the other repos: with no injected session each method
opens its own through :func:`db.oracle.session.session_scope`, and writes commit
once; an injected session is neither committed nor closed.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ai_messages as ai_messages_tbl
from db.models import ai_runs as ai_runs_tbl
from db.oracle.session import session_scope
from db.repos.oracle.ai_conversations import (
    _allocate_seq,
    _bump_conversation_stmt,
    _insert_message_stmt,
)
from db.repos.protocols import ActiveRunConflict

Row = dict[str, Any]

# 批删默认量：与消息/会话的批删对齐。
DELETE_BATCH = 500

# 终态集合：只有这里的值可以被 finish 写入（其余是 CAS 的预期状态）。
TERMINAL_STATUSES = ("succeeded", "failed", "cancelled", "interrupted")
ACTIVE_STATUSES = ("queued", "running")


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
    tbl = ai_runs_tbl
    return (
        tbl.c.oid,
        tbl.c.conversation_oid,
        tbl.c.owner_oid,
        tbl.c.client_msg_id,
        tbl.c.status,
        tbl.c.model,
        tbl.c.stop_reason,
        tbl.c.rounds,
        tbl.c.error_code,
        tbl.c.error_message,
        tbl.c.result_message_oid,
        tbl.c.prompt_tokens,
        tbl.c.completion_tokens,
        tbl.c.total_tokens,
        tbl.c.cancel_requested,
        tbl.c.heartbeat_at,
        tbl.c.created_at,
        tbl.c.started_at,
        tbl.c.finished_at,
    )


def _insert_run_stmt(
    *,
    run_id: str,
    conversation_oid: str,
    owner_oid: str,
    client_msg_id: str | None,
    model: str | None,
    created_at: datetime,
):
    """``status='queued'`` 是入队唯一合法的起始态（``running`` 由领取者 CAS 得到）。"""
    return insert(ai_runs_tbl).values(
        oid=str(run_id),
        conversation_oid=str(conversation_oid),
        owner_oid=str(owner_oid),
        client_msg_id=client_msg_id,
        status="queued",
        model=model,
        stop_reason=None,
        rounds=None,
        error_code=None,
        error_message=None,
        result_message_oid=None,
        prompt_tokens=None,
        completion_tokens=None,
        total_tokens=None,
        cancel_requested=0,
        heartbeat_at=None,
        created_at=created_at,
        started_at=None,
        finished_at=None,
    )


def _by_client_msg_stmt(conversation_oid: str, client_msg_id: str):
    """幂等查询：唯一索引 ``uq_ai_runs_client_msg`` 上的等值命中。"""
    tbl = ai_runs_tbl
    return (
        select(*_columns())
        .select_from(tbl)
        .where(
            tbl.c.conversation_oid == conversation_oid,
            tbl.c.client_msg_id == client_msg_id,
        )
    )


def _user_message_oid_stmt(conversation_oid: str, run_id: str):
    """去重命中时，回执要给出**那条已存在**的用户消息 oid（而不是新造一个）。"""
    tbl = ai_messages_tbl
    return (
        select(tbl.c.oid)
        .select_from(tbl)
        .where(
            tbl.c.conversation_oid == conversation_oid,
            tbl.c.request_id == run_id,
            tbl.c.role == "user",
        )
        .order_by(tbl.c.seq.asc())
        .fetch(1)
    )


def _claim_candidate_stmt():
    """队列的前门：最老的 ``queued`` 且未被取消的一条。

    这里**刻意不加** ``FOR UPDATE``：Oracle 不允许 ``FETCH FIRST n ROWS ONLY`` 与
    ``FOR UPDATE`` 同时出现，会直接抛 ORA-02014（cannot select FOR UPDATE from view
    with DISTINCT, GROUP BY, etc.），而 ``FOR UPDATE`` 也不能与 ``ROWNUM`` 过滤写在
    同层。抢占的原子性由紧接着的 ``_claim_cas_stmt``（``WHERE oid = :id AND status =
    'queued'``）承担：两个泵同时读到同一行时只有一个 CAS 会命中，另一个 rowcount=0
    并交给下一轮 pump。单 worker 部署下不存在竞争，多 worker 下最坏是各多一次空转。
    """
    tbl = ai_runs_tbl
    return (
        select(tbl.c.oid)
        .select_from(tbl)
        .where(tbl.c.status == "queued", tbl.c.cancel_requested == 0)
        .order_by(tbl.c.created_at.asc(), tbl.c.oid.asc())
        .fetch(1)
    )


def _claim_cas_stmt(run_id: str, at: datetime):
    """``queued → running`` 的 CAS：行锁已在手，这一步同时写开始时间与心跳。"""
    return (
        update(ai_runs_tbl)
        .where(ai_runs_tbl.c.oid == run_id, ai_runs_tbl.c.status == "queued")
        .values(status="running", started_at=at, heartbeat_at=at)
    )


def _get_stmt(run_id: str):
    return select(*_columns()).select_from(ai_runs_tbl).where(
        ai_runs_tbl.c.oid == run_id
    )


def _get_owned_stmt(run_id: str, owner_oid: str):
    return (
        select(*_columns())
        .select_from(ai_runs_tbl)
        .where(ai_runs_tbl.c.oid == run_id, ai_runs_tbl.c.owner_oid == owner_oid)
    )


def _heartbeat_stmt(run_id: str, at: datetime):
    """只写 ``heartbeat_at``，且只对 running 行：终态行上的心跳是噪声。"""
    return (
        update(ai_runs_tbl)
        .where(ai_runs_tbl.c.oid == run_id, ai_runs_tbl.c.status == "running")
        .values(heartbeat_at=at)
    )


def _finish_values(
    *,
    status: str,
    stop_reason: str | None,
    rounds: int | None,
    error_code: str | None,
    error_message: str | None,
    result_message_oid: str | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    total_tokens: int | None,
    finished_at: datetime,
) -> dict[str, Any]:
    values: dict[str, Any] = {"status": status, "finished_at": finished_at}
    for name, value in (
        ("stop_reason", stop_reason),
        ("rounds", rounds),
        ("error_code", error_code),
        ("error_message", error_message),
        ("result_message_oid", result_message_oid),
        ("prompt_tokens", prompt_tokens),
        ("completion_tokens", completion_tokens),
        ("total_tokens", total_tokens),
    ):
        if value is not None:
            values[name] = value
    return values


def _finish_stmt(run_id: str, values: dict[str, Any]):
    """CAS：只有 ``running`` 才能进终态。终态被覆盖等于审计丢失。"""
    return (
        update(ai_runs_tbl)
        .where(ai_runs_tbl.c.oid == run_id, ai_runs_tbl.c.status == "running")
        .values(**values)
    )


def _request_cancel_stmt(run_id: str, owner_oid: str):
    return (
        update(ai_runs_tbl)
        .where(
            ai_runs_tbl.c.oid == run_id,
            ai_runs_tbl.c.owner_oid == owner_oid,
            ai_runs_tbl.c.status.in_(ACTIVE_STATUSES),
        )
        .values(cancel_requested=1)
    )


def _cancel_if_queued_stmt(run_id: str, at: datetime):
    """``queued → cancelled``：还没开跑的不必等 worker 在轮边界看到标志。"""
    return (
        update(ai_runs_tbl)
        .where(ai_runs_tbl.c.oid == run_id, ai_runs_tbl.c.status == "queued")
        .values(status="cancelled", finished_at=at)
    )


def _list_active_stmt(owner_oid: str):
    return (
        select(*_columns())
        .select_from(ai_runs_tbl)
        .where(
            ai_runs_tbl.c.owner_oid == owner_oid,
            ai_runs_tbl.c.status.in_(ACTIVE_STATUSES),
        )
        .order_by(ai_runs_tbl.c.created_at.asc(), ai_runs_tbl.c.oid.asc())
    )


def _interrupt_values(
    *, error_code: str, message: str, at: datetime
) -> dict[str, Any]:
    return {
        "status": "interrupted",
        "error_code": error_code,
        "error_message": message,
        "finished_at": at,
    }


def _interrupt_running_stmt(values: dict[str, Any]):
    return (
        update(ai_runs_tbl)
        .where(ai_runs_tbl.c.status == "running")
        .values(**values)
    )


def _interrupt_stale_stmt(cutoff: datetime, values: dict[str, Any]):
    """watchdog 的谓词：仍在 running **且** 心跳早于阈值。

    阈值必须大于单次上游调用的最坏耗时（默认 360s > run timeout 300s），否则
    正常的长轮次会被误杀。
    """
    return (
        update(ai_runs_tbl)
        .where(
            ai_runs_tbl.c.status == "running",
            ai_runs_tbl.c.heartbeat_at < cutoff,
        )
        .values(**values)
    )


def _purge_stmt(conversation_oid: str, batch_size: int):
    """``ROWNUM`` 分段批删：不用 ``IN (...)`` 大列表（1000 项上限 + 锁太多行）。"""
    return delete(ai_runs_tbl).where(
        ai_runs_tbl.c.conversation_oid == conversation_oid,
        text("ROWNUM <= :batch_size").bindparams(batch_size=int(batch_size)),
    )


def _run_row(mapping: Any) -> Row:
    def _int(name: str) -> int | None:
        value = mapping[name]
        return None if value is None else int(value)

    return {
        "_id": mapping["oid"],
        "conversation_oid": mapping["conversation_oid"],
        "owner_oid": mapping["owner_oid"],
        "client_msg_id": mapping["client_msg_id"],
        "status": mapping["status"],
        "model": mapping["model"],
        "stop_reason": mapping["stop_reason"],
        "rounds": _int("rounds"),
        "error_code": mapping["error_code"],
        "error_message": mapping["error_message"],
        "result_message_oid": mapping["result_message_oid"],
        "prompt_tokens": _int("prompt_tokens"),
        "completion_tokens": _int("completion_tokens"),
        "total_tokens": _int("total_tokens"),
        "cancel_requested": bool(mapping["cancel_requested"]),
        "heartbeat_at": mapping["heartbeat_at"],
        "created_at": mapping["created_at"],
        "started_at": mapping["started_at"],
        "finished_at": mapping["finished_at"],
    }


class OracleAIRunsRepo:
    """``AIRunsRepo`` over Oracle ADB (``AI_RUNS``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def insert_queued(
        self,
        *,
        run_id: str,
        conversation_oid: str,
        owner_oid: str,
        message_oid: str,
        content: str,
        client_msg_id: str | None = None,
        model: str | None = None,
    ) -> tuple[Row, bool]:
        """用户消息 + ``queued`` run 的同一事务；``(run_row, created)``。

        调用方负责在调用**之前**校验会话存在、属于本人且 ``mode='agent'``（那是
        业务判断，不是数据完整性）；这里只保证 seq 分配与两条插入原子。
        """
        now = _now()
        async with _session_for(self._session) as (session, owns):
            try:
                # savepoint：两次唯一索引冲突都只回滚这一段，外侧事务仍可用，
                # 于是「冲突 → 查已有 run」可以在同一会话里完成。
                async with session.begin_nested():
                    seq = await _allocate_seq(session, str(conversation_oid))
                    await session.execute(
                        _insert_message_stmt(
                            seq=seq,
                            created_at=now,
                            oid=str(message_oid),
                            conversation_oid=str(conversation_oid),
                            role="user",
                            content=content,
                            request_id=str(run_id),
                        )
                    )
                    await session.execute(
                        _insert_run_stmt(
                            run_id=run_id,
                            conversation_oid=conversation_oid,
                            owner_oid=owner_oid,
                            client_msg_id=client_msg_id,
                            model=model,
                            created_at=now,
                        )
                    )
                    await session.execute(
                        _bump_conversation_stmt(
                            str(conversation_oid), added=1, usage=None, at=now
                        )
                    )
            except IntegrityError as exc:
                if client_msg_id:
                    existing = (
                        await session.execute(
                            _by_client_msg_stmt(
                                str(conversation_oid), str(client_msg_id)
                            )
                        )
                    ).first()
                    if existing is not None:
                        row = _run_row(existing._mapping)
                        user_message = (
                            await session.execute(
                                _user_message_oid_stmt(
                                    str(conversation_oid), row["_id"]
                                )
                            )
                        ).scalar()
                        row["message_oid"] = user_message
                        return row, False
                # 不是幂等键冲突 → 活跃唯一索引（同会话/同用户已有 queued|running）。
                raise ActiveRunConflict(
                    f"active run exists: conversation={conversation_oid} "
                    f"owner={owner_oid}"
                ) from exc
            if owns:
                await session.commit()
        return {
            "_id": str(run_id),
            "conversation_oid": str(conversation_oid),
            "owner_oid": str(owner_oid),
            "client_msg_id": client_msg_id,
            "status": "queued",
            "model": model,
            "stop_reason": None,
            "rounds": None,
            "error_code": None,
            "error_message": None,
            "result_message_oid": None,
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
            "cancel_requested": False,
            "heartbeat_at": None,
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "message_oid": str(message_oid),
        }, True

    async def claim_next(self) -> Row | None:
        now = _now()
        async with _session_for(self._session) as (session, owns):
            candidate = (await session.execute(_claim_candidate_stmt())).first()
            if candidate is None:
                return None
            run_id = candidate._mapping["oid"]
            claimed = await session.execute(_claim_cas_stmt(str(run_id), now))
            if not claimed.rowcount:
                # 行锁在手还可能为 0 只可能是同事务里有别的写入抢先迁移了状态；
                # 交给下一轮 pump，不在这里猜测。
                return None
            row = (await session.execute(_get_stmt(str(run_id)))).first()
            if owns:
                await session.commit()
        return _run_row(row._mapping)

    async def get(self, run_id: str, owner_oid: str) -> Row | None:
        async with _session_for(self._session) as (session, _owns):
            row = (
                await session.execute(_get_owned_stmt(str(run_id), str(owner_oid)))
            ).first()
        return _run_row(row._mapping) if row is not None else None

    async def heartbeat(self, run_id: str, *, at: datetime | None = None) -> bool:
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(_heartbeat_stmt(str(run_id), at or _now()))
            if owns:
                await session.commit()
        return bool(result.rowcount)

    async def finish(
        self,
        run_id: str,
        *,
        status: str,
        stop_reason: str | None = None,
        rounds: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        result_message_oid: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        finished_at: datetime | None = None,
    ) -> bool:
        if status not in TERMINAL_STATUSES:
            # 终态以外的东西不该走 finish：``queued → running`` 由领取者 CAS，
            # 让它成为可写值等于把状态机的入口交给调用方。
            raise ValueError(f"finish() only accepts terminal statuses, got {status!r}")
        values = _finish_values(
            status=status,
            stop_reason=stop_reason,
            rounds=rounds,
            error_code=error_code,
            error_message=error_message,
            result_message_oid=result_message_oid,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            finished_at=finished_at or _now(),
        )
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(_finish_stmt(str(run_id), values))
            if owns:
                await session.commit()
        return bool(result.rowcount)

    async def request_cancel(self, run_id: str, owner_oid: str) -> bool:
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(
                _request_cancel_stmt(str(run_id), str(owner_oid))
            )
            if owns:
                await session.commit()
        return bool(result.rowcount)

    async def mark_cancelled_if_queued(self, run_id: str) -> bool:
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(
                _cancel_if_queued_stmt(str(run_id), _now())
            )
            if owns:
                await session.commit()
        return bool(result.rowcount)

    async def list_active(self, owner_oid: str) -> list[Row]:
        async with _session_for(self._session) as (session, _owns):
            rows = (
                await session.execute(_list_active_stmt(str(owner_oid)))
            ).all()
        return [_run_row(row._mapping) for row in rows]

    async def interrupt_running(
        self,
        *,
        error_code: str = "AI_RUN_INTERRUPTED",
        message: str = "执行进程失去心跳",
    ) -> int:
        values = _interrupt_values(
            error_code=error_code, message=message, at=_now()
        )
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(_interrupt_running_stmt(values))
            if owns:
                await session.commit()
        return int(result.rowcount or 0)

    async def interrupt_stale(
        self,
        cutoff: datetime,
        *,
        error_code: str = "AI_RUN_INTERRUPTED",
        message: str = "执行进程失去心跳",
    ) -> int:
        values = _interrupt_values(
            error_code=error_code, message=message, at=_now()
        )
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(_interrupt_stale_stmt(cutoff, values))
            if owns:
                await session.commit()
        return int(result.rowcount or 0)

    async def purge_by_conversation(
        self, conversation_oid: str, *, batch_size: int = DELETE_BATCH
    ) -> int:
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(
                _purge_stmt(str(conversation_oid), batch_size)
            )
            if owns:
                await session.commit()
        return int(result.rowcount or 0)


__all__ = [
    "OracleAIRunsRepo",
    "ActiveRunConflict",
    "ACTIVE_STATUSES",
    "TERMINAL_STATUSES",
]
