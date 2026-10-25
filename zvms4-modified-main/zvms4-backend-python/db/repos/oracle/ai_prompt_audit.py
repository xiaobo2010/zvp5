"""Oracle implementation of the prompt audit log (``AI_PROMPT_AUDIT``, V033).

这张表与 ``AI_CALL_LOGS`` 的唯一区别是**它存正文**：同一份对话在流水里只有
元数据（谁、哪个场景、多少 token），在这里才有「问了什么」。写入方是
:mod:`services.ai.prompt_audit`（按角色受控、脱敏、截断、best-effort），
本模块只做读写。

两条读侧口径是刻意分开的：

* :meth:`OracleAIPromptAuditRepo.list_page` 回**元数据**（含 ``prompt_chars``
  = ``LENGTH(PROMPT_TEXT)``），**不选正文**：翻一页就把几十段学生对话读进
  内存，既慢又扩大了正文的暴露面。
* :meth:`OracleAIPromptAuditRepo.get_by_oid` 回完整正文，只给「单条审阅」
  路径用；调用方（``GET /api/ai/prompts/{oid}``）必须在同一请求里写一条
  ``APP_LOGS`` 访问审计——读正文本身就是一个要被记录的动作。

删除只有 :meth:`purge_before` 一条路（保留期清理，整行删除，绝不改行）：正文
一旦能被改写，「当时学生说了什么」就不再可信。

Session ownership mirrors the other repos: with no injected session each method
opens its own through :func:`db.oracle.session.session_scope`, and writes commit
once; an injected session is neither committed nor closed.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import new_oid, normalize_oid
from db.models import ai_prompt_audit as ai_prompt_audit_tbl
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


def _meta_columns() -> tuple[Any, ...]:
    """列表口径：**不含** ``prompt_text``，字符数在库里算。"""
    return (
        ai_prompt_audit_tbl.c.oid,
        ai_prompt_audit_tbl.c.user_oid,
        ai_prompt_audit_tbl.c.purpose,
        ai_prompt_audit_tbl.c.roles_snapshot,
        ai_prompt_audit_tbl.c.created_at,
        func.length(ai_prompt_audit_tbl.c.prompt_text).label("prompt_chars"),
    )


def _detail_columns() -> tuple[Any, ...]:
    return (
        ai_prompt_audit_tbl.c.oid,
        ai_prompt_audit_tbl.c.request_id,
        ai_prompt_audit_tbl.c.user_oid,
        ai_prompt_audit_tbl.c.purpose,
        ai_prompt_audit_tbl.c.roles_snapshot,
        ai_prompt_audit_tbl.c.prompt_text,
        ai_prompt_audit_tbl.c.created_at,
    )


def _filters(
    *,
    user_oid: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[Any]:
    """可选过滤；``None`` = 不过滤。时间边界是闭区间（与 ``AI_CALL_LOGS`` 一致）。"""
    conds: list[Any] = []
    if user_oid is not None:
        conds.append(ai_prompt_audit_tbl.c.user_oid == normalize_oid(user_oid))
    if since is not None:
        conds.append(ai_prompt_audit_tbl.c.created_at >= since)
    if until is not None:
        conds.append(ai_prompt_audit_tbl.c.created_at <= until)
    return conds


def _page_stmt(conds: list[Any], page: int, size: int):
    """一页元数据，新记录在前（同一时刻再按 oid：``new_oid`` 前缀是时间戳）。"""
    stmt = select(*_meta_columns()).select_from(ai_prompt_audit_tbl)
    if conds:
        stmt = stmt.where(*conds)
    return (
        stmt.order_by(
            ai_prompt_audit_tbl.c.created_at.desc(), ai_prompt_audit_tbl.c.oid.desc()
        )
        .offset((page - 1) * size)
        .limit(size)
    )


def _count_stmt(conds: list[Any]):
    stmt = select(func.count()).select_from(ai_prompt_audit_tbl)
    if conds:
        stmt = stmt.where(*conds)
    return stmt


def _count_expired_stmt(cutoff: datetime):
    return (
        select(func.count())
        .select_from(ai_prompt_audit_tbl)
        .where(ai_prompt_audit_tbl.c.created_at < cutoff)
    )


def _purge_before_stmt(cutoff: datetime, batch_size: int):
    """一条有界的 ``DELETE``；``ROWNUM`` 而不是 ``FETCH FIRST``（Oracle 的 DELETE
    不接受后者，与 ``db/repos/oracle/ai_call_logs.py::_purge_batch_stmt`` 同因）。
    """
    return delete(ai_prompt_audit_tbl).where(
        ai_prompt_audit_tbl.c.created_at < cutoff,
        text("ROWNUM <= :batch_size").bindparams(batch_size=batch_size),
    )


def _meta_row(mapping: Any) -> Row:
    return {
        "_id": mapping["oid"],
        "user_oid": mapping["user_oid"],
        "purpose": mapping["purpose"],
        "roles_snapshot": mapping["roles_snapshot"],
        "created_at": mapping["created_at"],
        # Oracle 的 LENGTH(CLOB) 回 NUMBER；空正文（理论上不会写）按 0 算。
        "prompt_chars": int(mapping["prompt_chars"] or 0),
    }


def _detail_row(mapping: Any) -> Row:
    return {
        "_id": mapping["oid"],
        "request_id": mapping["request_id"],
        "user_oid": mapping["user_oid"],
        "purpose": mapping["purpose"],
        "roles_snapshot": mapping["roles_snapshot"],
        "prompt_text": mapping["prompt_text"],
        "created_at": mapping["created_at"],
    }


class OracleAIPromptAuditRepo:
    """``AIPromptAuditRepo`` over Oracle ADB (``AI_PROMPT_AUDIT``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def insert(
        self,
        *,
        request_id: str | None = None,
        user_oid: str | None = None,
        roles_snapshot: str | None = None,
        purpose: str | None = None,
        prompt_text: str | None = None,
    ) -> str:
        """追加一条正文留存并返回新 ``_id``（写入时刻由本层生成）。

        ``prompt_text`` 已经过调用方的脱敏与截断：本层不再改内容——兜底改写会
        让「表里存的就是调用方认为该存的」这条假设失效，而审阅端点是按表里的
        原文给管理员看的。
        """
        oid = new_oid()
        stmt = insert(ai_prompt_audit_tbl).values(
            oid=oid,
            request_id=request_id,
            user_oid=normalize_oid(user_oid) if user_oid else None,
            roles_snapshot=roles_snapshot,
            purpose=purpose,
            prompt_text=prompt_text,
            created_at=datetime.now(),
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return oid

    async def list_page(
        self,
        *,
        user_oid: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> list[Row]:
        """一页元数据（新记录在前），**不含正文**；``page`` 从 1 开始。"""
        conds = _filters(user_oid=user_oid, since=since, until=until)
        stmt = _page_stmt(conds, page, page_size)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_meta_row(row._mapping) for row in rows]

    async def count(
        self,
        *,
        user_oid: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int:
        """与 :meth:`list_page` 同过滤条件的总数（分页的 ``metadata.size``）。"""
        conds = _filters(user_oid=user_oid, since=since, until=until)
        stmt = _count_stmt(conds)
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(stmt)).scalar_one()
        return int(total)

    async def get_by_oid(self, oid: str) -> Row | None:
        """按主键取一条**含正文**的记录；不存在回 ``None``。"""
        stmt = (
            select(*_detail_columns())
            .select_from(ai_prompt_audit_tbl)
            .where(ai_prompt_audit_tbl.c.oid == str(oid))
        )
        async with _session_for(self._session) as (session, _owns):
            row = (await session.execute(stmt)).first()
        return _detail_row(row._mapping) if row is not None else None

    async def count_expired(self, cutoff: datetime) -> int:
        """``created_at < cutoff`` 的行数（只读，与 :meth:`purge_before` 同谓词）。"""
        stmt = _count_expired_stmt(cutoff)
        async with _session_for(self._session) as (session, _owns):
            total = (await session.execute(stmt)).scalar_one()
        return int(total)

    async def purge_before(self, cutoff: datetime, batch_size: int) -> int:
        """删除 ``created_at < cutoff`` 的行，最多 ``batch_size`` 条，返回实际删除数。

        不可逆，按批提交（每批一个短 session、一次 commit，批循环在
        :mod:`tasks.retention`），这样长清理不会一直占着 ADB 上的一个事务。
        """
        stmt = _purge_before_stmt(cutoff, batch_size)
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(stmt)
            if owns:
                await session.commit()
        return int(result.rowcount or 0)


__all__ = ["OracleAIPromptAuditRepo"]
