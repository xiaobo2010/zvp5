"""Oracle implementation of the conversation + message repos (V035).

``AI_CONVERSATIONS``（一段会话）与 ``AI_MESSAGES``（会话内消息）放在同一个模块：
两者的写路径是**同一个事务**——一轮的 assistant + 全部 tool 消息落库，同时累加
会话的 token 与计数（``docs/AI_CONVERSATION_DESIGN.md`` §4.5）。拆成两个文件也
可以，但那样「同事务」就只剩口头约定。

三个口径：

* **列表不 select ``summary``**（CLOB）。单会话 200 轮 ≈1.6MB，翻页时把它拉回来
  等于每次读几 MB。``_columns()``（详情/上下文组装）与 ``_list_columns()``（列表）
  分开，测试钉住这个差别。
* **``seq`` 只有一个来源**：锁会话行后的 ``MAX(seq)+1``。客户端算号在重试时会撞
  ``uq_ai_messages_conv_seq``，而修复「重号」比修复「少一条消息」贵得多。
* 计数字段用 SQL 自增（``tokens_total = tokens_total + :n``），不读后写：同一会话
  可能被 chat 与 agent 两条路径同时累加，读后写会丢一次。``context_tokens`` 例外，
  它是**覆盖**（最近一次请求的水位，不是第二个总量）。

Session ownership mirrors the other repos: with no injected session each method
opens its own through :func:`db.oracle.session.session_scope`, and writes commit
once; an injected session is neither committed nor closed.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator, Mapping, Sequence

from sqlalchemy import and_, delete, func, insert, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import ai_conversations as ai_conversations_tbl
from db.models import ai_messages as ai_messages_tbl
from db.oracle.session import session_scope

Row = dict[str, Any]

# 列表页上限：与 HTTP 层的 limit ≤ 100 对齐（数据层再兜一次，免得写错一层就拉着
# 整张表）。默认 50 是「一屏 + 一点预取」。
LIST_LIMIT_MAX = 100
LIST_LIMIT_DEFAULT = 50

# 批删的默认批量：单条 DELETE 锁 500 行 CLOB 是可接受的 undo 量级。
DELETE_BATCH = 500


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


def _clamp_limit(limit: int | None) -> int:
    value = LIST_LIMIT_DEFAULT if limit is None else int(limit)
    return max(1, min(value, LIST_LIMIT_MAX))


# ---------------------------------------------------------------------------
# statement builders — conversations
# ---------------------------------------------------------------------------

def _columns() -> tuple[Any, ...]:
    """详情投影：带 ``summary``（上下文组装要它）。"""
    tbl = ai_conversations_tbl
    return (
        tbl.c.oid,
        tbl.c.owner_oid,
        tbl.c.mode,
        tbl.c.title,
        tbl.c.model,
        tbl.c.thinking,
        tbl.c.scene,
        tbl.c.status,
        tbl.c.summary,
        tbl.c.summary_upto_seq,
        tbl.c.tokens_prompt,
        tbl.c.tokens_completion,
        tbl.c.tokens_total,
        tbl.c.context_tokens,
        tbl.c.message_count,
        tbl.c.compaction_degraded_at,
        tbl.c.created_at,
        tbl.c.updated_at,
    )


def _list_columns() -> tuple[Any, ...]:
    """列表投影：**没有** ``summary`` —— CLOB 不进列表页。"""
    return tuple(col for col in _columns() if col.key != "summary")


def _get_stmt(oid: str, owner_oid: str):
    tbl = ai_conversations_tbl
    return (
        select(*_columns())
        .select_from(tbl)
        .where(tbl.c.oid == oid, tbl.c.owner_oid == owner_oid)
    )


def _list_page_stmt(
    owner_oid: str,
    *,
    status: str | None,
    cursor: tuple[datetime, str] | None,
    limit: int,
):
    """键集分页：``(updated_at, oid)`` 严格递减，不用 OFFSET。

    OFFSET 分页在「一边翻页一边有新消息」时会把行从第 2 页挤到第 3 页（正确性），
    也会随页数变慢（性能）。``oid`` 是同一毫秒内的定序补充键：``updated_at`` 精度
    到秒，同一秒建的两个会话只按时间排会翻页丢行。

    ``scene IS NULL`` 是固定的：列表是普通会话的列表，场景会话（终端 / 文件编辑）
    只经 `/api/ai/scenes/{scene}/runs` 触达，混进来会让 ZAiChat 的左栏凭空多出几条
    不是用户建的会话。
    """
    tbl = ai_conversations_tbl
    stmt = (
        select(*_list_columns())
        .select_from(tbl)
        .where(tbl.c.owner_oid == owner_oid, tbl.c.scene.is_(None))
    )
    if status:
        stmt = stmt.where(tbl.c.status == status)
    if cursor is not None:
        updated_at, oid = cursor
        stmt = stmt.where(
            or_(
                tbl.c.updated_at < updated_at,
                and_(tbl.c.updated_at == updated_at, tbl.c.oid < oid),
            )
        )
    return stmt.order_by(tbl.c.updated_at.desc(), tbl.c.oid.desc()).fetch(
        _clamp_limit(limit)
    )


def _insert_stmt(
    *,
    oid: str,
    owner_oid: str,
    mode: str,
    title: str | None,
    model: str | None,
    thinking: str | None,
    scene: str | None,
    created_at: datetime,
):
    tbl = ai_conversations_tbl
    return insert(tbl).values(
        oid=oid,
        owner_oid=owner_oid,
        mode=mode,
        title=title,
        model=model,
        thinking=thinking,
        scene=scene,
        status="active",
        summary=None,
        summary_upto_seq=0,
        tokens_prompt=0,
        tokens_completion=0,
        tokens_total=0,
        context_tokens=0,
        message_count=0,
        compaction_degraded_at=None,
        created_at=created_at,
        updated_at=created_at,
    )


def _update_stmt(oid: str, owner_oid: str, values: dict[str, Any]):
    tbl = ai_conversations_tbl
    return (
        update(tbl)
        .where(tbl.c.oid == oid, tbl.c.owner_oid == owner_oid)
        .values(**values)
    )


def _update_values(
    *,
    title: str | None,
    status: str | None,
    thinking: str | None,
    model: str | None,
    at: datetime,
) -> dict[str, Any]:
    """只收「传了的字段」；``None`` = 不改（``thinking`` / ``model`` 的清空走各自的
    ``clear_*`` 开关，见 :meth:`OracleAIConversationsRepo.update`）。

    ``model`` 的准入（只在空会话上放行）是服务层的判断：仓储只负责把值写下去，
    不重复一遍业务规则——两处各判一次，迟早有一处先漂。
    """
    values: dict[str, Any] = {}
    if title is not None:
        values["title"] = title
    if status is not None:
        values["status"] = status
    if thinking is not None:
        values["thinking"] = thinking
    if model is not None:
        values["model"] = model
    if values:
        # 改名/归档/调思考强度/空会话改模型都算「这条会话动过」，列表按 updated_at 排序。
        values["updated_at"] = at
    return values


def _touch_stmt(oid: str, at: datetime):
    return (
        update(ai_conversations_tbl)
        .where(ai_conversations_tbl.c.oid == oid)
        .values(updated_at=at)
    )


def _latest_scene_stmt(owner_oid: str, scene: str):
    """某用户某个场景的最近一条会话（复用即「不新建」）。"""
    tbl = ai_conversations_tbl
    return (
        select(*_list_columns())
        .select_from(tbl)
        .where(tbl.c.owner_oid == owner_oid, tbl.c.scene == scene)
        .order_by(tbl.c.updated_at.desc(), tbl.c.oid.desc())
        .fetch(1)
    )


def _stale_scene_rows_stmt(cutoff: datetime, limit: int):
    """场景会话的保留期清理候选（最旧的先删，批量出去）。"""
    tbl = ai_conversations_tbl
    return (
        select(*_list_columns())
        .select_from(tbl)
        .where(tbl.c.scene.is_not(None), tbl.c.updated_at < cutoff)
        .order_by(tbl.c.updated_at.asc())
        .fetch(int(limit))
    )


def _count_stale_scene_rows_stmt(cutoff: datetime):
    tbl = ai_conversations_tbl
    return (
        select(func.count())
        .select_from(tbl)
        .where(tbl.c.scene.is_not(None), tbl.c.updated_at < cutoff)
    )


def _add_tokens_stmt(
    oid: str,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    context_tokens: int | None,
):
    """自增写在 SET 里：并发累加由行锁串行化，不会丢一次。"""
    tbl = ai_conversations_tbl
    values: dict[str, Any] = {
        "tokens_prompt": tbl.c.tokens_prompt + int(prompt_tokens),
        "tokens_completion": tbl.c.tokens_completion + int(completion_tokens),
        "tokens_total": tbl.c.tokens_total + int(total_tokens),
    }
    if context_tokens is not None:
        values["context_tokens"] = int(context_tokens)
    return update(tbl).where(tbl.c.oid == oid).values(**values)


def _set_summary_stmt(oid: str, summary: str | None, upto_seq: int):
    return (
        update(ai_conversations_tbl)
        .where(ai_conversations_tbl.c.oid == oid)
        .values(summary=summary, summary_upto_seq=int(upto_seq))
    )


def _set_context_tokens_stmt(oid: str, tokens: int | None):
    return (
        update(ai_conversations_tbl)
        .where(ai_conversations_tbl.c.oid == oid)
        .values(context_tokens=None if tokens is None else int(tokens))
    )


def _mark_degraded_stmt(oid: str, at: datetime):
    return (
        update(ai_conversations_tbl)
        .where(ai_conversations_tbl.c.oid == oid)
        .values(compaction_degraded_at=at)
    )


def _count_messages_stmt(oid: str):
    """读冗余列，不 COUNT 消息表（删除回执为这一个数字不值得扫 CLOB）。"""
    return (
        select(ai_conversations_tbl.c.message_count)
        .select_from(ai_conversations_tbl)
        .where(ai_conversations_tbl.c.oid == oid)
    )


def _delete_batched_stmt(oid: str, owner_oid: str, batch_size: int):
    """``ROWNUM`` 而不是 ``FETCH FIRST``：Oracle 的 ``DELETE`` 不接受后者。"""
    tbl = ai_conversations_tbl
    return delete(tbl).where(
        tbl.c.oid == oid,
        tbl.c.owner_oid == owner_oid,
        text("ROWNUM <= :batch_size").bindparams(batch_size=int(batch_size)),
    )


def _base_conversation_row(mapping: Any) -> Row:
    """``_columns()`` 与 ``_list_columns()`` **共有**的列的映射。

    只有两边都投影的列才允许出现在这里：往这里加一列就等于要求列表也带上它。
    """
    return {
        "_id": mapping["oid"],
        "owner_oid": mapping["owner_oid"],
        "mode": mapping["mode"],
        "title": mapping["title"],
        "model": mapping["model"],
        "thinking": mapping["thinking"],
        "scene": mapping["scene"],
        "status": mapping["status"],
        "summary_upto_seq": int(mapping["summary_upto_seq"] or 0),
        "tokens_prompt": int(mapping["tokens_prompt"] or 0),
        "tokens_completion": int(mapping["tokens_completion"] or 0),
        "tokens_total": int(mapping["tokens_total"] or 0),
        "context_tokens": int(mapping["context_tokens"] or 0),
        "message_count": int(mapping["message_count"] or 0),
        "compaction_degraded_at": mapping["compaction_degraded_at"],
        "created_at": mapping["created_at"],
        "updated_at": mapping["updated_at"],
    }


def _conversation_row(mapping: Any) -> Row:
    """详情行映射：``_columns()`` 全列投影，含 ``summary``（上下文组装要读它）。

    只能喂详情投影的行。列表投影没有 ``summary`` 列，喂进来会抛
    ``NoSuchColumnError``——列表走 :func:`_list_row`。
    """
    row = _base_conversation_row(mapping)
    row["summary"] = mapping["summary"]
    return row


def _list_row(mapping: Any) -> Row:
    """列表行映射：``_list_columns()`` 投影，**不含** ``summary``。

    为什么列表不取 ``summary``：它是 CLOB，单会话 200 轮正文≈1.6MB，翻页时把它
    一起 select 回来等于每次翻页读几 MB（见模块 docstring）。所以这里直接给占位
    ``None``——与 ``create()`` 返回的新会话同口径——而不是去 ``mapping["summary"]``
    讨一个列表投影里不存在的列（那正是历史上列表页 100% 500 的原因）。
    """
    row = _base_conversation_row(mapping)
    row["summary"] = None
    return row


# ---------------------------------------------------------------------------
# statement builders — messages
# ---------------------------------------------------------------------------

def _message_columns() -> tuple[Any, ...]:
    tbl = ai_messages_tbl
    return (
        tbl.c.oid,
        tbl.c.conversation_oid,
        tbl.c.seq,
        tbl.c.role,
        tbl.c.content,
        tbl.c.reasoning,
        tbl.c.tool_calls,
        tbl.c.tool_call_id,
        tbl.c.tool_name,
        tbl.c.round_no,
        tbl.c.finish_reason,
        tbl.c.request_id,
        tbl.c.prompt_tokens,
        tbl.c.completion_tokens,
        tbl.c.total_tokens,
        tbl.c.cache_read_tokens,
        tbl.c.cache_write_tokens,
        tbl.c.created_at,
    )


def _lock_conversation_stmt(conversation_oid: str):
    """seq 分配的前提：先锁会话行，再取 ``MAX(seq)``。

    没有这把锁，两个并发写者会算出同一个 ``seq``，第二个撞
    ``uq_ai_messages_conv_seq``（正确但白跑一趟）；有它，二者串行。锁的是**会话
    行**而不是消息表：Oracle 没有间隙锁，锁消息表挡不住「读 MAX 时还没有下一行」。
    """
    return (
        select(ai_conversations_tbl.c.oid)
        .select_from(ai_conversations_tbl)
        .where(ai_conversations_tbl.c.oid == conversation_oid)
        .with_for_update()
    )


def _max_seq_stmt(conversation_oid: str):
    return select(func.max(ai_messages_tbl.c.seq)).where(
        ai_messages_tbl.c.conversation_oid == conversation_oid
    )


# 这三个键由仓储层在锁内决定：调用方传了也只会被丢掉（而不是悄悄覆盖事实）。
_OWNED_MESSAGE_KEYS = ("seq", "created_at", "conversation_oid")


def _message_fields(item: Mapping[str, Any]) -> dict[str, Any]:
    """只保留调用方**该**给的字段（内容 / 工具身份 / 该轮 usage）。"""
    return {key: value for key, value in item.items() if key not in _OWNED_MESSAGE_KEYS}


def _insert_message_stmt(*, seq: int, created_at: datetime, **fields: Any):    return insert(ai_messages_tbl).values(
        oid=fields["oid"],
        conversation_oid=fields["conversation_oid"],
        seq=seq,
        role=fields["role"],
        content=fields.get("content"),
        # 思考过程：只对 assistant 行有值；tool/user 行留 NULL。
        reasoning=fields.get("reasoning"),
        tool_calls=fields.get("tool_calls"),
        tool_call_id=fields.get("tool_call_id"),
        tool_name=fields.get("tool_name"),
        round_no=fields.get("round_no"),
        finish_reason=fields.get("finish_reason"),
        request_id=fields.get("request_id"),
        prompt_tokens=fields.get("prompt_tokens"),
        completion_tokens=fields.get("completion_tokens"),
        total_tokens=fields.get("total_tokens"),
        cache_read_tokens=fields.get("cache_read_tokens"),
        cache_write_tokens=fields.get("cache_write_tokens"),
        created_at=created_at,
    )


def _bump_conversation_stmt(
    conversation_oid: str,
    *,
    added: int,
    usage: Mapping[str, int | None] | None,
    at: datetime,
):
    """轮收尾的会话侧累加：与消息插入同一个事务（评审 P1-4 幂等边界）。"""
    tbl = ai_conversations_tbl
    values: dict[str, Any] = {
        "message_count": tbl.c.message_count + int(added),
        "updated_at": at,
    }
    if usage is not None:
        values["tokens_prompt"] = tbl.c.tokens_prompt + int(usage.get("prompt_tokens") or 0)
        values["tokens_completion"] = (
            tbl.c.tokens_completion + int(usage.get("completion_tokens") or 0)
        )
        values["tokens_total"] = tbl.c.tokens_total + int(usage.get("total_tokens") or 0)
        context = usage.get("context_tokens")
        if context is not None:
            # 水位是覆盖语义：它是最近一次请求的 prompt token，不是第二个总量。
            values["context_tokens"] = int(context)
    return update(tbl).where(tbl.c.oid == conversation_oid).values(**values)


def _list_after_stmt(conversation_oid: str, after_seq: int, limit: int):
    tbl = ai_messages_tbl
    return (
        select(*_message_columns())
        .select_from(tbl)
        .where(tbl.c.conversation_oid == conversation_oid, tbl.c.seq > int(after_seq))
        .order_by(tbl.c.seq.asc())
        .fetch(_clamp_limit(limit))
    )


def _count_messages_of_stmt(conversation_oid: str):
    return (
        select(func.count())
        .select_from(ai_messages_tbl)
        .where(ai_messages_tbl.c.conversation_oid == conversation_oid)
    )


def _purge_messages_stmt(conversation_oid: str, batch_size: int):
    """批删：``ROWNUM`` 分段，不用 ``IN (...)`` 大列表（Oracle 1000 项上限）。"""
    return delete(ai_messages_tbl).where(
        ai_messages_tbl.c.conversation_oid == conversation_oid,
        text("ROWNUM <= :batch_size").bindparams(batch_size=int(batch_size)),
    )


def _message_row(mapping: Any) -> Row:
    def _int(name: str) -> int | None:
        value = mapping[name]
        return None if value is None else int(value)

    return {
        "_id": mapping["oid"],
        "conversation_oid": mapping["conversation_oid"],
        "seq": int(mapping["seq"]),
        "role": mapping["role"],
        "content": mapping["content"],
        "reasoning": mapping["reasoning"],
        "tool_calls": mapping["tool_calls"],
        "tool_call_id": mapping["tool_call_id"],
        "tool_name": mapping["tool_name"],
        "round_no": _int("round_no"),
        "finish_reason": mapping["finish_reason"],
        "request_id": mapping["request_id"],
        "prompt_tokens": _int("prompt_tokens"),
        "completion_tokens": _int("completion_tokens"),
        "total_tokens": _int("total_tokens"),
        "cache_read_tokens": _int("cache_read_tokens"),
        "cache_write_tokens": _int("cache_write_tokens"),
        "created_at": mapping["created_at"],
    }


async def _allocate_seq(session: AsyncSession, conversation_oid: str) -> int:
    """锁会话行后取下一个 ``seq``（1 起）。"""
    await session.execute(_lock_conversation_stmt(conversation_oid))
    current = (await session.execute(_max_seq_stmt(conversation_oid))).scalar()
    return int(current or 0) + 1


class OracleAIConversationsRepo:
    """``AIConversationsRepo`` over Oracle ADB (``AI_CONVERSATIONS``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def create(
        self,
        *,
        oid: str,
        owner_oid: str,
        mode: str,
        title: str | None = None,
        model: str | None = None,
        thinking: str | None = None,
        scene: str | None = None,
    ) -> Row:
        now = _now()
        stmt = _insert_stmt(
            oid=str(oid),
            owner_oid=str(owner_oid),
            mode=str(mode),
            title=title,
            model=model,
            thinking=thinking,
            scene=scene,
            created_at=now,
        )
        async with _session_for(self._session) as (session, owns):
            await session.execute(stmt)
            if owns:
                await session.commit()
        return {
            "_id": str(oid),
            "owner_oid": str(owner_oid),
            "mode": str(mode),
            "title": title,
            "model": model,
            "thinking": thinking,
            "scene": scene,
            "status": "active",
            "summary": None,
            "summary_upto_seq": 0,
            "tokens_prompt": 0,
            "tokens_completion": 0,
            "tokens_total": 0,
            "context_tokens": 0,
            "message_count": 0,
            "compaction_degraded_at": None,
            "created_at": now,
            "updated_at": now,
        }

    async def get(self, oid: str, owner_oid: str) -> Row | None:
        async with _session_for(self._session) as (session, _owns):
            row = (
                await session.execute(_get_stmt(str(oid), str(owner_oid)))
            ).first()
        return _conversation_row(row._mapping) if row is not None else None

    async def list_page(
        self,
        owner_oid: str,
        *,
        status: str | None = None,
        cursor: tuple[datetime, str] | None = None,
        limit: int = LIST_LIMIT_DEFAULT,
    ) -> tuple[list[Row], tuple[datetime, str] | None]:
        stmt = _list_page_stmt(
            str(owner_oid), status=status, cursor=cursor, limit=limit
        )
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        items = [_list_row(row._mapping) for row in rows]
        # 下一页游标只在「取满一页」时给：不满说明到底了，也给游标只是让客户端多
        # 发一次空请求。
        if len(items) < _clamp_limit(limit):
            return items, None
        last = items[-1]
        return items, (last["updated_at"], last["_id"])

    async def update(
        self,
        oid: str,
        owner_oid: str,
        *,
        title: str | None = None,
        status: str | None = None,
        thinking: str | None = None,
        clear_thinking: bool = False,
        model: str | None = None,
        clear_model: bool = False,
    ) -> bool:
        """改名 / 归档 / 调思考强度 / 空会话改模型；``False`` = 会话不存在或不属于此人。

        ``clear_thinking`` / ``clear_model`` 是「显式清空（回到上游默认）」的专用开
        关：``None`` 在别处表示「不改」，两种意图必须能分开。

        ``model`` 的准入条件（``message_count == 0``）由
        :func:`services.ai.conversations.update` 判定——仓储不重复业务规则。
        """
        values = _update_values(
            title=title, status=status, thinking=thinking, model=model, at=_now()
        )
        if clear_thinking:
            values["thinking"] = None
            values.setdefault("updated_at", _now())
        if clear_model:
            values["model"] = None
            values.setdefault("updated_at", _now())
        if not values:
            return True
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(
                _update_stmt(str(oid), str(owner_oid), values)
            )
            if owns:
                await session.commit()
        return bool(result.rowcount)

    async def touch(self, oid: str, *, at: datetime | None = None) -> None:
        async with _session_for(self._session) as (session, owns):
            await session.execute(_touch_stmt(str(oid), at or _now()))
            if owns:
                await session.commit()

    async def add_tokens(
        self,
        oid: str,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        context_tokens: int | None = None,
    ) -> None:
        async with _session_for(self._session) as (session, owns):
            await session.execute(
                _add_tokens_stmt(
                    str(oid),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    context_tokens=context_tokens,
                )
            )
            if owns:
                await session.commit()

    async def set_summary(
        self, oid: str, summary: str | None, upto_seq: int
    ) -> None:
        async with _session_for(self._session) as (session, owns):
            await session.execute(_set_summary_stmt(str(oid), summary, upto_seq))
            if owns:
                await session.commit()

    async def set_context_tokens(self, oid: str, tokens: int | None) -> None:
        async with _session_for(self._session) as (session, owns):
            await session.execute(_set_context_tokens_stmt(str(oid), tokens))
            if owns:
                await session.commit()

    async def mark_compaction_degraded(
        self, oid: str, *, at: datetime | None = None
    ) -> None:
        """记「最近一次压缩降级（硬截断）」的时间；不清零，它是事实不是开关。"""
        async with _session_for(self._session) as (session, owns):
            await session.execute(_mark_degraded_stmt(str(oid), at or _now()))
            if owns:
                await session.commit()

    async def count_messages(self, oid: str) -> int:
        async with _session_for(self._session) as (session, _owns):
            value = (await session.execute(_count_messages_stmt(str(oid)))).scalar()
        return int(value or 0)

    async def latest_by_scene(self, owner_oid: str, scene: str) -> Row | None:
        """某用户某个场景的最近一条会话；没有就 ``None``（调用方新建）。"""
        async with _session_for(self._session) as (session, _owns):
            row = (
                await session.execute(
                    _latest_scene_stmt(str(owner_oid), str(scene))
                )
            ).first()
        return _list_row(row._mapping) if row is not None else None

    async def list_stale_scenes(
        self, cutoff: datetime, *, limit: int = DELETE_BATCH
    ) -> list[Row]:
        """``updated_at < cutoff`` 的场景会话（最旧的先给，供级联删除）。"""
        async with _session_for(self._session) as (session, _owns):
            rows = (
                await session.execute(_stale_scene_rows_stmt(cutoff, limit))
            ).all()
        return [_list_row(row._mapping) for row in rows]

    async def count_stale_scenes(self, cutoff: datetime) -> int:
        async with _session_for(self._session) as (session, _owns):
            value = (
                await session.execute(_count_stale_scene_rows_stmt(cutoff))
            ).scalar()
        return int(value or 0)

    async def delete_batched(
        self, oid: str, owner_oid: str, *, batch_size: int = DELETE_BATCH
    ) -> int:
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(
                _delete_batched_stmt(str(oid), str(owner_oid), batch_size)
            )
            if owns:
                await session.commit()
        return int(result.rowcount or 0)


class OracleAIMessagesRepo:
    """``AIMessagesRepo`` over Oracle ADB (``AI_MESSAGES``)."""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def append_batch(
        self,
        conversation_oid: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        usage: Mapping[str, int | None] | None = None,
    ) -> list[Row]:
        if not rows:
            # 空批次不发任何语句：一轮里没有任何可落库的消息，就不该顺手刷新
            # updated_at（那会让空轮次看起来像"有新内容"）。
            return []
        now = _now()
        inserted: list[Row] = []
        async with _session_for(self._session) as (session, owns):
            base = await _allocate_seq(session, str(conversation_oid))
            for offset, item in enumerate(rows):
                seq = base + offset
                await session.execute(
                    _insert_message_stmt(
                        seq=seq,
                        created_at=now,
                        conversation_oid=str(conversation_oid),
                        **_message_fields(item),
                    )
                )
                inserted.append(
                    {
                        "_id": item["oid"],
                        "conversation_oid": str(conversation_oid),
                        "seq": seq,
                        "role": item["role"],
                        "content": item.get("content"),
                        "reasoning": item.get("reasoning"),
                        "tool_calls": item.get("tool_calls"),
                        "tool_call_id": item.get("tool_call_id"),
                        "tool_name": item.get("tool_name"),
                        "round_no": item.get("round_no"),
                        "finish_reason": item.get("finish_reason"),
                        "request_id": item.get("request_id"),
                        "prompt_tokens": item.get("prompt_tokens"),
                        "completion_tokens": item.get("completion_tokens"),
                        "total_tokens": item.get("total_tokens"),
                        "cache_read_tokens": item.get("cache_read_tokens"),
                        "cache_write_tokens": item.get("cache_write_tokens"),
                        "created_at": now,
                    }
                )
            await session.execute(
                _bump_conversation_stmt(
                    str(conversation_oid),
                    added=len(rows),
                    usage=usage,
                    at=now,
                )
            )
            if owns:
                await session.commit()
        return inserted

    async def next_seq(self, conversation_oid: str) -> int:
        async with _session_for(self._session) as (session, _owns):
            return await _allocate_seq(session, str(conversation_oid))

    async def list_after(
        self, conversation_oid: str, after_seq: int = 0, *, limit: int = LIST_LIMIT_DEFAULT
    ) -> list[Row]:
        stmt = _list_after_stmt(str(conversation_oid), after_seq, limit)
        async with _session_for(self._session) as (session, _owns):
            rows = (await session.execute(stmt)).all()
        return [_message_row(row._mapping) for row in rows]

    async def count(self, conversation_oid: str) -> int:
        async with _session_for(self._session) as (session, _owns):
            value = (
                await session.execute(_count_messages_of_stmt(str(conversation_oid)))
            ).scalar()
        return int(value or 0)

    async def purge_by_conversation(
        self, conversation_oid: str, *, batch_size: int = DELETE_BATCH
    ) -> int:
        async with _session_for(self._session) as (session, owns):
            result = await session.execute(
                _purge_messages_stmt(str(conversation_oid), batch_size)
            )
            if owns:
                await session.commit()
        return int(result.rowcount or 0)


__all__ = ["OracleAIConversationsRepo", "OracleAIMessagesRepo"]
