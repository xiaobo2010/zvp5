"""Oracle implementation of the agent event log (``AI_AGENT_EVENTS``, V033 + V034).

一次工具调用尝试一行，**包含被拒 / 驳回 / 过期的那些**：只记成功就等于看不见
「模型在试探白名单外的工具」，而这正是治理最需要看的信号。

这张表与 ``AI_CALL_LOGS`` 的分工：

* ``AI_CALL_LOGS`` 回答「谁、哪个能力、哪个上游、花多少额度」——一次 agent run
  的**每一轮**一行；
* ``AI_AGENT_EVENTS`` 回答「模型要求调什么工具、参数是什么、结果如何、谁批准
  的」——同一轮里可以有多条（模型一次可能要求多个工具调用）。

写操作走「提案」：模型调用写工具时不执行，落一行 ``status='proposed'``；用户
确认后同一行被推进成 ``executing`` → ``confirmed``（或 ``rejected`` /
``expired`` / ``failed``）。所以这张表是**唯一**允许更新的审计表（确认是一次
状态迁移，不是改写历史：``args_hash`` / ``args_payload`` / ``args_summary`` /
``preview`` 从建行起就不再变，改写它们会让「确认的东西」与「执行的东西」脱钩）。

``ARGS_HASH`` 是 ``ARGS_PAYLOAD``（完整 canonical JSON，CLOB，V034）的 sha256：
确认时要用同一份可复原的参数重算并比对。``ARGS_SUMMARY`` 只是 ≤500 的展示副本，
**永不参与校验**；V034 之前建的老行没有 ``ARGS_PAYLOAD``，确认时回落到
``ARGS_SUMMARY``（当时的 hash 就是它的哈希）。

所有状态迁移都是 CAS（``WHERE status = <预期>``）：终态（``confirmed`` /
``rejected`` / ``expired`` / ``failed`` / ``denied``）不会被并发的过期/确认覆盖。

Session ownership mirrors the other repos: with no injected session each method
opens its own through :func:`db.oracle.session.session_scope`, and writes commit
once; an injected session is neither committed nor closed.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ai_agent_events as ai_agent_events_tbl
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
        ai_agent_events_tbl.c.oid,
        ai_agent_events_tbl.c.request_id,
        ai_agent_events_tbl.c.round_no,
        ai_agent_events_tbl.c.tool_name,
        ai_agent_events_tbl.c.tool_kind,
        ai_agent_events_tbl.c.args_hash,
        ai_agent_events_tbl.c.args_summary,
        ai_agent_events_tbl.c.args_payload,
        ai_agent_events_tbl.c.preview,
        ai_agent_events_tbl.c.result_summary,
        ai_agent_events_tbl.c.status,
        ai_agent_events_tbl.c.actor_oid,
        ai_agent_events_tbl.c.approver_oid,
        ai_agent_events_tbl.c.confirmed_at,
        ai_agent_events_tbl.c.created_at,
    )


def _display_columns() -> tuple[Any, ...]:
    """轮询用的轻量投影：只取渲染需要的列。

    故意不含 ``args_hash`` / ``args_payload``——后者是完整 canonical JSON（CLOB），
    确认路径才需要；轮询每秒读一遍全量参数等于把整条时间线拖着走。
    """
    return (
        ai_agent_events_tbl.c.oid,
        ai_agent_events_tbl.c.request_id,
        ai_agent_events_tbl.c.round_no,
        ai_agent_events_tbl.c.tool_name,
        ai_agent_events_tbl.c.tool_kind,
        ai_agent_events_tbl.c.args_summary,
        ai_agent_events_tbl.c.result_summary,
        ai_agent_events_tbl.c.status,
        ai_agent_events_tbl.c.actor_oid,
        ai_agent_events_tbl.c.preview,
        ai_agent_events_tbl.c.created_at,
    )


def _count_expired_stmt(cutoff: datetime):
    return (
        select(func.count())
        .select_from(ai_agent_events_tbl)
        .where(ai_agent_events_tbl.c.created_at < cutoff)
    )


def _purge_batch_stmt(cutoff: datetime, batch_size: int):
    """``ROWNUM`` 而不是 ``FETCH FIRST``：Oracle 的 ``DELETE`` 不接受后者。"""
    return delete(ai_agent_events_tbl).where(
        ai_agent_events_tbl.c.created_at < cutoff,
        text("ROWNUM <= :batch_size").bindparams(batch_size=batch_size),
    )


def _list_for_request_stmt(request_id: str, after_round: int = 0):
    """一次 run 的事件，按写入顺序；``after_round > 0`` 时只取更新的轮次。"""
    stmt = (
        select(*_columns())
        .select_from(ai_agent_events_tbl)
        .where(ai_agent_events_tbl.c.request_id == str(request_id))
        .order_by(
            ai_agent_events_tbl.c.created_at, ai_agent_events_tbl.c.oid
        )
    )
    floor = int(after_round or 0)
    if floor > 0:
        stmt = stmt.where(ai_agent_events_tbl.c.round_no > floor)
    return stmt


def _list_display_stmt(request_id: str, after_round: int = 0):
    """同 :func:`_list_for_request_stmt` 的顺序与轮次过滤，但只取展示列。"""
    stmt = (
        select(*_display_columns())
        .select_from(ai_agent_events_tbl)
        .where(ai_agent_events_tbl.c.request_id == str(request_id))
        .order_by(
            ai_agent_events_tbl.c.created_at, ai_agent_events_tbl.c.oid
        )
    )
    floor = int(after_round or 0)
    if floor > 0:
        stmt = stmt.where(ai_agent_events_tbl.c.round_no > floor)
    return stmt


def _row(mapping: Any) -> Row:
    """``_id`` 用主键；``round_no`` 统一转 int（Oracle 回 ``Decimal``）。"""
    return {
        "_id": mapping["oid"],
        "request_id": mapping["request_id"],
        "round_no": (
            int(mapping["round_no"]) if mapping["round_no"] is not None else None
        ),
        "tool_name": mapping["tool_name"],
        "tool_kind": mapping["tool_kind"],
        "args_hash": mapping["args_hash"],
        "args_summary": mapping["args_summary"],
        "args_payload": mapping["args_payload"],
        "preview": mapping["preview"],
        "result_summary": mapping["result_summary"],
        "status": mapping["status"],
        "actor_oid": mapping["actor_oid"],
        "approver_oid": mapping["approver_oid"],
        "confirmed_at": mapping["confirmed_at"],
        "created_at": mapping["created_at"],
    }


def _display_row(mapping: Any) -> Row:
    """轻量投影 → 与 :func:`_row` 同形状（缺的两列补 ``None``）。

    保持同形状是为了让上层（事件视图 / 提案视图）不必区分两种来源：它们只读
    ``args_summary`` / ``preview``，而这批列恰好都在。
    """
    return {
        "_id": mapping["oid"],
        "request_id": mapping["request_id"],
        "round_no": (
            int(mapping["round_no"]) if mapping["round_no"] is not None else None
        ),
        "tool_name": mapping["tool_name"],
        "tool_kind": mapping["tool_kind"],
        "args_hash": None,
        "args_summary": mapping["args_summary"],
        "args_payload": None,
        "preview": mapping["preview"],
        "result_summary": mapping["result_summary"],
        "status": mapping["status"],
        "actor_oid": mapping["actor_oid"],
        "approver_oid": None,
        "confirmed_at": None,
        "created_at": mapping["created_at"],
    }


class OracleAIAgentEventsRepo:
    """``AIAgentEventsRepo`` over Oracle ADB (``AI_AGENT_EVENTS``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def insert(
        self,
        *,
        oid: str,
        request_id: str | None = None,
        round_no: int | None = None,
        tool_name: str | None = None,
        tool_kind: str | None = None,
        args_hash: str | None = None,
        args_summary: str | None = None,
        args_payload: str | None = None,
        preview: str | None = None,
        result_summary: str | None = None,
        status: str = "executed",
        actor_oid: str | None = None,
        approver_oid: str | None = None,
        confirmed_at: datetime | None = None,
    ) -> str:
        """追加一条工具调用事件；``oid`` 由调用方给（提案句柄要提前返回给前端）。"""
        stmt = insert(ai_agent_events_tbl).values(
            oid=oid,
            request_id=request_id,
            round_no=round_no,
            tool_name=tool_name,
            tool_kind=tool_kind,
            args_hash=args_hash,
            args_summary=args_summary,
            args_payload=args_payload,
            preview=preview,
            result_summary=result_summary,
            status=status,
            actor_oid=actor_oid,
            approver_oid=approver_oid,
            confirmed_at=confirmed_at,
            created_at=datetime.now(),
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return oid

    async def get(self, oid: str) -> Row | None:
        stmt = (
            select(*_columns())
            .select_from(ai_agent_events_tbl)
            .where(ai_agent_events_tbl.c.oid == str(oid))
        )
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        return _row(row._mapping) if row is not None else None

    async def update_status(
        self,
        oid: str,
        status: str,
        *,
        from_status: str = "proposed",
        result_summary: str | None = None,
        approver_oid: str | None = None,
        confirmed_at: datetime | None = None,
    ) -> bool:
        """CAS 推进一条提案的状态；只有**当前**状态等于 ``from_status`` 才生效。

        ``WHERE status = <预期>`` 是并发闸：两个管理员同时点确认时，后到的那条
        影响 0 行（调用方据此回 409），而不是把已执行的操作再执行一遍。过期同理
        ——``proposed → expired`` 抢不到已经 ``confirmed`` / ``executing`` 的行，
        终态因此不可被覆盖。``approver_oid`` / ``confirmed_at`` 只在非 ``None``
        时写入（``proposed → executing`` 只带审批人，``executing → confirmed``
        只带执行时间）。
        """
        values: dict[str, Any] = {"status": status}
        if result_summary is not None:
            values["result_summary"] = result_summary
        if approver_oid is not None:
            values["approver_oid"] = approver_oid
        if confirmed_at is not None:
            values["confirmed_at"] = confirmed_at
        stmt = (
            update(ai_agent_events_tbl)
            .where(
                ai_agent_events_tbl.c.oid == str(oid),
                ai_agent_events_tbl.c.status == from_status,
            )
            .values(**values)
        )
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return bool(result.rowcount)

    async def update_status_unconditional(
        self, oid: str, status: str, *, result_summary: str | None = None
    ) -> bool:
        """无条件改状态：**仅供数据修复**，业务路径一律走 CAS。

        保留它是因为运维脚本可能需要纠正一条写错的行；任何状态机迁移用它会覆盖
        终态，而终态被覆盖等于「已执行的写」在审计里消失。
        """
        stmt = (
            update(ai_agent_events_tbl)
            .where(ai_agent_events_tbl.c.oid == str(oid))
            .values(status=status, result_summary=result_summary)
        )
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return bool(result.rowcount)

    async def list_for_request(
        self, request_id: str, *, after_round: int = 0
    ) -> list[Row]:
        """一次 run 的全部工具事件（按写入顺序：``created_at`` 后 ``oid``）。

        ``after_round > 0`` 时只回 ``round_no > after_round`` 的行：调用方要增量
        却把整条时间线拉回来，等于每秒重读一遍历史。默认 ``0`` 与旧行为逐字相同。
        """
        stmt = _list_for_request_stmt(request_id, after_round)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_row(row._mapping) for row in rows]

    async def list_display_for_request(
        self, request_id: str, *, after_round: int = 0
    ) -> list[Row]:
        """同 :meth:`list_for_request`，但**不取** ``args_payload`` / ``args_hash``。

        轮询专用：这两个列一个是 CLOB（完整参数），一个是它的哈希，只有确认提案
        时才需要。展示层用不到，拉回来纯属给每条事件多背一份正文。
        """
        stmt = _list_display_stmt(request_id, after_round)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_display_row(row._mapping) for row in rows]

    async def list_by_actor(
        self,
        actor_oid: str,
        *,
        status: str | None = None,
        limit: int = 20,
    ) -> list[Row]:
        """某个人发起的提案/事件，``created_at`` 倒序。

        ``actor_oid`` 是**必须**的：``GET /proposals`` 只回本人的东西。把过滤留在
        SQL 里而不是调用方，少一条「忘了过滤」的路径。
        """
        stmt = (
            select(*_columns())
            .select_from(ai_agent_events_tbl)
            .where(ai_agent_events_tbl.c.actor_oid == str(actor_oid))
            .order_by(
                ai_agent_events_tbl.c.created_at.desc(),
                ai_agent_events_tbl.c.oid.desc(),
            )
        )
        if status:
            stmt = stmt.where(ai_agent_events_tbl.c.status == str(status))
        stmt = stmt.limit(max(1, int(limit)))
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_row(row._mapping) for row in rows]

    async def count_expired(self, cutoff: datetime) -> int:
        """``created_at < cutoff`` 的条数（只读，与 :meth:`purge_batch` 同谓词）。

        窗口与 ``AI_CALL_LOGS`` 对齐（``AI_CALL_LOGS_RETENTION_DAYS``）：工具事件
        与调用流水是同一次 run 的两面，一边删了另一边还在，会让「谁确认了这条
        提案」变成悬空的历史。
        """
        stmt = _count_expired_stmt(cutoff)
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(stmt)).scalar_one()
        return int(total)

    async def purge_batch(self, cutoff: datetime, batch_size: int) -> int:
        """删除 ``created_at < cutoff`` 的事件，最多 ``batch_size`` 条，返回实际数。

        不可逆、按批提交（批循环在 :mod:`tasks.retention`）。这是这张表唯一的
        删除路径：正常运行时它只追加，确认/驳回是状态迁移而不是改写内容。
        """
        stmt = _purge_batch_stmt(cutoff, batch_size)
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return int(result.rowcount or 0)


__all__ = ["OracleAIAgentEventsRepo"]
