"""会话与消息的业务层（``AI_CONVERSATIONS`` / ``AI_MESSAGES``，契约 §3.1）。

这一层做三件事：把仓储行组装成对外视图、把 HTTP 层的意图翻成仓储调用、把「删一
段会话」这种跨表编排写在一个地方。它**不认识执行器，也不认识队列**——轮次落库只
借 ``AIMessagesRepo.append_batch`` 的原子性，run 的生命周期全在
:mod:`services.agent.run_queue`。

两条硬规则：

* **每条路径都先过归属闸**。``AIMessagesRepo`` 按会话 oid 查（消息表没有 owner
  列），所以消息路径必须先 :func:`require_row`；少一次过滤就是跨用户读别人的对话。
* **列表不投影正文**。单会话 200 轮 ≈1.6MB CLOB，翻页时把 ``content`` / ``summary``
  拖回来等于为了排序字段读几 MB；正文只走 ``GET /conversations/{oid}/messages``。

计数列一律读会话行的冗余列（``message_count`` / ``tokens_*``），不 COUNT 消息表:
列表页每行一次 COUNT 就是 N+1，而删除回执要的那个数字也不值得扫 CLOB。
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import Any, Awaitable, Callable, Mapping, Sequence
from uuid import uuid4

import settings

from db import backends
from services.agent.tool_impl import AgentError

# 会话类型与状态：与表上的 CHECK 逐字一致（写错只会在 Oracle 上变成 ORA-02290，
# 而那时事务已经开了一半）。
MODES = ("chat", "agent")
STATUSES = ("active", "archived")

# 列宽配额：``title`` VARCHAR2(120)。超了是 ORA-12899，不如在这里截断。
TITLE_MAX = 120

# 分页上限与 HTTP 层同口径（数据层再兜一次，免得写错一层就拉全表）。
LIST_LIMIT_MAX = 100
LIST_LIMIT_DEFAULT = 50
MESSAGE_LIMIT_MAX = 100
MESSAGE_LIMIT_DEFAULT = 50

# 批删量：与 ``tasks/retention.py`` 的范式对齐（一次一条 DELETE，锁 500 行 CLOB）。
DELETE_BATCH = 500
# 批删的循环上限：只是防「删的同时有人一直插」把请求挂死，不是业务上限。
DELETE_MAX_BATCHES = 400

# 作废提案时的扫描窗口：``list_by_actor`` 没有游标，取一页够用的量。待确认提案
# 是「待办」，正常是几条；上限存在的意义是别为一次删除把整张事件表读一遍。
PROPOSAL_SCAN_LIMIT = 500
DELETED_PROPOSAL_SUMMARY = "会话已删除"

# 错误码（契约 §3.4）。路由层原样回出去，前端按 code 分支。
NOT_FOUND = "AI_CONVERSATION_NOT_FOUND"
MODE_MISMATCH = "AI_CONVERSATION_MODE_MISMATCH"
RUN_CONFLICT = "AI_RUN_CONFLICT"
BAD_CURSOR = "AI_CONVERSATION_BAD_CURSOR"

# 上下文上限的兜底：``AI_DEFAULT_CONTEXT_LIMIT`` 缺省 32768（保守值，见 §5.3）。
CONTEXT_LIMIT_DEFAULT = 32768

Row = dict[str, Any]


def _repo() -> Any:
    """每次取一个新仓储：它自己开 session，长事务不该藏在模块状态里。"""
    return backends.get_ai_conversations_repo()


def _messages() -> Any:
    return backends.get_ai_messages_repo()


def _runs() -> Any:
    return backends.get_ai_runs_repo()


def _events() -> Any:
    return backends.get_ai_agent_events_repo()


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _iso(value: object) -> str | None:
    """TIMESTAMP → 字符串；与仓库其余 AI 面同一口径（空格分隔、秒精度）。"""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return value if isinstance(value, str) else None


def _json(value: object) -> object:
    """CLOB 里的 JSON 原文 → 对象；解析不了就原样回（宁可让前端显示原文，也不要
    把一条坏行变成 500）。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _clip_title(title: object) -> str:
    return str(title or "").strip()[:TITLE_MAX]


def title_from(content: object) -> str:
    """首条用户消息 → 会话标题（§2.1）。

    压缩换行/连续空白：标题是一行文本，换行会让列表项高度不受控；截断到列宽是
    唯一安全的上限，多出来的信息在消息里，不在标题里。
    """
    return " ".join(str(content or "").split())[:TITLE_MAX]


def context_limit(model: object) -> int:
    """该模型的上下文上限（§5.3 的 ``limit``，视图里的分母）。

    默认值 + 按模型的覆盖表都从 ``settings`` 读：额度随模型变，属于部署知识，不该
    硬编码在本文件。配置坏了就回落 32768——分母是展示用的，不能因为一个笔误让
    会话页 500。
    """
    limits = getattr(settings, "AI_MODEL_CONTEXT_LIMITS", None)
    if isinstance(limits, Mapping) and isinstance(model, str):
        value = limits.get(model)
        try:
            parsed = int(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            parsed = 0
        if parsed > 0:
            return parsed
    try:
        fallback = int(getattr(settings, "AI_DEFAULT_CONTEXT_LIMIT", CONTEXT_LIMIT_DEFAULT))
    except (TypeError, ValueError):
        fallback = CONTEXT_LIMIT_DEFAULT
    return fallback if fallback > 0 else CONTEXT_LIMIT_DEFAULT


# ---------------------------------------------------------------------------
# 游标（base64 的 ``updated_at|oid``）
# ---------------------------------------------------------------------------

def encode_cursor(updated_at: datetime, oid: str) -> str:
    raw = f"{updated_at.isoformat()}|{oid}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    """游标是**不透明句柄**：解不开就是客户端传了脏数据，回 400 而不是静默从头翻
    页（静默重启分页会把第一页重复给用户，比报错更难查）。"""
    try:
        raw = base64.urlsafe_b64decode(str(cursor).encode("ascii")).decode("utf-8")
        stamp, _, oid = raw.partition("|")
        return datetime.fromisoformat(stamp), oid
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise AgentError(BAD_CURSOR, "invalid cursor", 400) from exc


# ---------------------------------------------------------------------------
# 视图
# ---------------------------------------------------------------------------

def conversation_view(row: Mapping[str, Any]) -> dict:
    """会话视图（§3.1）。列表与详情共用：两者的差别只在仓储投影，不在形状。"""
    limit = context_limit(row.get("model"))
    context = _int(row.get("context_tokens"))
    tokens = {
        "prompt": _int(row.get("tokens_prompt")),
        "completion": _int(row.get("tokens_completion")),
        "total": _int(row.get("tokens_total")),
    }
    upto = _int(row.get("summary_upto_seq"))
    return {
        "oid": str(row.get("_id") or ""),
        "mode": row.get("mode"),
        "title": str(row.get("title") or ""),
        "model": row.get("model"),
        "status": row.get("status"),
        "tokens": tokens,
        "context": {
            "tokens": context,
            "limit": limit,
            # 进度条的分母；超限时夹到 1.0——「占用 130%」会让条撑破容器，而超限
            # 这件事由压缩水位表达，不该由进度条表达。
            "ratio": min(1.0, round(context / limit, 4)) if limit else 0.0,
        },
        "compaction": {
            "upto_seq": upto,
            "summarized": upto > 0,
            "degraded_at": _iso(row.get("compaction_degraded_at")),
        },
        "message_count": _int(row.get("message_count")),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def _usage_of(row: Mapping[str, Any]) -> dict | None:
    """单条消息的该轮 usage；全为 NULL 就是「上游没报」，回 ``None``（不是 0）。

    0 是真实观测值（这一轮确实没花 token），NULL 是「不知道」；混用会让报表把
    「没报」统计成「免费」。
    """
    keys = (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
    )
    values = {key: row.get(key) for key in keys}
    if all(value is None for value in values.values()):
        return None
    return {
        key: (None if value is None else _int(value)) for key, value in values.items()
    }


def message_view(row: Mapping[str, Any]) -> dict:
    return {
        "oid": str(row.get("_id") or ""),
        "seq": _int(row.get("seq")),
        "role": row.get("role"),
        "content": str(row.get("content") or ""),
        "tool_calls": _json(row.get("tool_calls")),
        "tool_call_id": row.get("tool_call_id"),
        "tool_name": row.get("tool_name"),
        "round_no": row.get("round_no"),
        "request_id": row.get("request_id"),
        "usage": _usage_of(row),
        "created_at": _iso(row.get("created_at")),
    }


# ---------------------------------------------------------------------------
# 归属闸
# ---------------------------------------------------------------------------

async def fetch_row(owner_oid: str, oid: str) -> Row | None:
    """本人的会话行（``None`` = 不存在或不是本人的）。"""
    return await _repo().get(str(oid), str(owner_oid))


async def require_row(*, user: Mapping[str, Any], oid: str) -> Row:
    """404 一律「不存在」：不区分「没有」与「不是你的」，否则 oid 就成了探针。"""
    row = await fetch_row(str(user.get("id") or ""), oid)
    if row is None:
        raise AgentError(NOT_FOUND, "conversation not found", 404)
    return row


# ---------------------------------------------------------------------------
# 会话 CRUD
# ---------------------------------------------------------------------------

async def create(
    *,
    user: Mapping[str, Any],
    mode: str,
    title: str | None = None,
    model: str | None = None,
) -> dict:
    """建一个会话（``status='active'``，``summary_upto_seq=0``）。

    ``mode`` 在这里定死：它决定后续走同步 SSE 还是后台任务，事后改它等于把一段
    历史变成另一种语义（数据层也没有这条更新路径）。
    """
    normalized = str(mode or "").strip().lower()
    if normalized not in MODES:
        raise AgentError(
            "AI_BAD_REQUEST", "mode must be chat or agent", 400
        )
    row = await _repo().create(
        oid=uuid4().hex,
        owner_oid=str(user.get("id") or ""),
        mode=normalized,
        title=_clip_title(title) or None,
        model=(str(model).strip() or None) if model else None,
    )
    return conversation_view(row)


async def list_page(
    *,
    user: Mapping[str, Any],
    status: str | None = None,
    cursor: str | None = None,
    limit: int = LIST_LIMIT_DEFAULT,
) -> dict:
    """本人的会话列表（``updated_at`` 倒序，键集分页）。"""
    wanted = (str(status).strip().lower() or None) if status else None
    if wanted is not None and wanted not in STATUSES:
        raise AgentError("AI_BAD_REQUEST", "status must be active or archived", 400)
    size = max(1, min(int(limit or LIST_LIMIT_DEFAULT), LIST_LIMIT_MAX))
    keyset = decode_cursor(cursor) if cursor else None
    rows, next_cursor = await _repo().list_page(
        str(user.get("id") or ""),
        status=wanted,
        cursor=keyset,
        limit=size,
    )
    return {
        "conversations": [conversation_view(row) for row in rows],
        "next_cursor": encode_cursor(*next_cursor) if next_cursor else None,
    }


async def get(*, user: Mapping[str, Any], oid: str) -> dict:
    return conversation_view(await require_row(user=user, oid=oid))


async def update(
    *,
    user: Mapping[str, Any],
    oid: str,
    title: str | None = None,
    status: str | None = None,
    model: str | None = None,
    clear_model: bool = False,
) -> dict:
    """改名 / 归档 / 换模型。

    ``model=None`` 在仓储层表示「不改」，所以「清空模型（回到服务端默认）」必须
    由调用方用 ``clear_model`` 显式表达——否则 PATCH 里写 ``model: null`` 只会得到
    一次静默 no-op。
    """
    owner = str(user.get("id") or "")
    await require_row(user=user, oid=str(oid))
    values: dict[str, Any] = {}
    if title is not None:
        values["title"] = _clip_title(title)
    if status is not None:
        normalized = str(status).strip().lower()
        if normalized not in STATUSES:
            raise AgentError("AI_BAD_REQUEST", "status must be active or archived", 400)
        values["status"] = normalized
    if model is not None:
        values["model"] = str(model).strip() or None
    changed = await _repo().update(
        str(oid),
        owner,
        title=values.get("title"),
        status=values.get("status"),
        model=values.get("model"),
        clear_model=bool(clear_model) and model is None,
    )
    if not changed:
        # 归属闸刚放行、随即 0 行：只可能是并发删除。按「不存在」回，客户端重试
        # 一次就能拿到一致的结论。
        raise AgentError(NOT_FOUND, "conversation not found", 404)
    row = await fetch_row(owner, str(oid))
    if row is None:
        raise AgentError(NOT_FOUND, "conversation not found", 404)
    return conversation_view(row)


async def retitle(*, owner_oid: str, oid: str, title: str) -> None:
    """内部用：首条消息落库后补标题（§2.1），不经过视图。"""
    await _repo().update(str(oid), str(owner_oid), title=_clip_title(title))


# ---------------------------------------------------------------------------
# 删除（§3.1 的三步门禁）
# ---------------------------------------------------------------------------

async def _drain(purge: Callable[[], Awaitable[int]], *, batch: int = DELETE_BATCH) -> int:
    """循环批删到「不足一批」。循环有上限：防「一边删一边有人插」把请求挂死。"""
    total = 0
    for _ in range(DELETE_MAX_BATCHES):
        removed = await purge()
        total += int(removed or 0)
        if int(removed or 0) < batch:
            break
    return total


async def _invalidate_proposals(owner_oid: str, conversation_oid: str) -> int:
    """作废该会话所有 ``proposed`` 提案（评审 P0-3 第二步）。

    为什么必须做：``confirm`` 只校验提案的 ``actor_oid``，**不要求 run 还在**
    （``routers/ai_agent_router.py`` 的确认路径）。删掉会话却不作废提案，等于留着
    一个「用户以为已经删掉」的写操作，30 分钟内还能被执行。

    仓储层没有「按会话批量作废」的接口，所以这里按 actor 的待确认提案逐条回查它
    属于哪次 run（一次 run 只会属于一个会话），命中才 CAS 成 ``failed``。
    """
    events = _events()
    runs = _runs()
    rows = await events.list_by_actor(
        str(owner_oid), status="proposed", limit=PROPOSAL_SCAN_LIMIT
    )
    invalidated = 0
    for row in rows:
        request_id = str(row.get("request_id") or "")
        if not request_id:
            continue
        run = await runs.get(request_id, str(owner_oid))
        if run is None or str(run.get("conversation_oid")) != str(conversation_oid):
            continue
        changed = await events.update_status(
            str(row.get("_id") or row.get("oid") or ""),
            "failed",
            from_status="proposed",
            result_summary=DELETED_PROPOSAL_SUMMARY,
        )
        if changed:
            invalidated += 1
    return invalidated


async def delete(*, user: Mapping[str, Any], oid: str) -> dict:
    """硬删一段会话：门禁 → 作废提案 → 批删 ``messages → runs → conversation``。

    顺序不是风格问题：消息与 run 都必须先删（Oracle 没有级联），而提案作废必须
    发生在 run 还在的时候——run 删完就查不到它属于哪个会话了。
    """
    owner = str(user.get("id") or "")
    await require_row(user=user, oid=str(oid))

    active = await _runs().list_active(owner)
    if any(str(row.get("conversation_oid")) == str(oid) for row in active):
        raise AgentError(
            RUN_CONFLICT, "cancel the active run before deleting", 409
        )

    await _invalidate_proposals(owner, str(oid))

    messages = await _drain(lambda: _messages().purge_by_conversation(str(oid)))
    runs = await _drain(lambda: _runs().purge_by_conversation(str(oid)))
    removed = await _drain(lambda: _repo().delete_batched(str(oid), owner))
    if not removed:
        raise AgentError(NOT_FOUND, "conversation not found", 404)
    return {"deleted": True, "messages": messages, "runs": runs}


# ---------------------------------------------------------------------------
# 消息
# ---------------------------------------------------------------------------

async def list_messages(
    *,
    user: Mapping[str, Any],
    oid: str,
    after_seq: int = 0,
    limit: int = MESSAGE_LIMIT_DEFAULT,
) -> dict:
    """``seq > after_seq`` 的增量（刷新与轮询同一条路径）。

    ``has_more`` 是「这一页可能还有下一批」：取满一页就置真，让客户端再拉一次；
    判少一条也比漏一条便宜。``total`` 读冗余列（不为一个数字扫 CLOB 表）。
    """
    conversation = await require_row(user=user, oid=oid)
    size = max(1, min(int(limit or MESSAGE_LIMIT_DEFAULT), MESSAGE_LIMIT_MAX))
    rows = await _messages().list_after(
        str(oid), max(0, int(after_seq or 0)), limit=size
    )
    return {
        "messages": [message_view(row) for row in rows],
        "has_more": len(rows) >= size,
        "total": _int(conversation.get("message_count")),
    }


async def append_user(
    conversation_oid: str,
    content: str,
    *,
    request_id: str | None = None,
) -> dict:
    """用户侧的一条消息（chat 同步面的入口；`append_round` 只收 assistant/tool）。

    为什么单独一条函数而不是放开 `append_round` 的角色校验：那条校验守的是
    「一次 run「重跑 queued」不能重复写用户消息」（§4.5），把 user 放进去等于拆掉
    幂等边界。chat 没有 run，用户消息就是这一轮的起点，天然只写一次，所以另设入口。

    `seq` / `message_count` / `updated_at` 仍由 `append_batch` 在一个事务里分配与
    累加——前端拿到的 `seq` 必须与随后 assistant 那条连续。返回值里的 ``oid`` 由本
    函数自己分配（不读仓储的 ``_id`` 别名：不同实现回不同键名，而调用方要用它做
    首帧句柄）。
    """
    oid = uuid4().hex
    row = {
        "oid": oid,
        "role": "user",
        "content": str(content or ""),
        "request_id": str(request_id) if request_id else None,
    }
    inserted = await _messages().append_batch(str(conversation_oid), [row])
    result = dict(inserted[0]) if inserted else {}
    result["oid"] = oid
    result.setdefault("seq", 0)
    return result


async def append_round(
    conversation_oid: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    usage: Mapping[str, int | None] | None = None,
) -> list[dict]:
    """一轮的 assistant + 全部 tool 消息落库，**同一事务**累加会话用量。

    原子性由 ``append_batch`` 保证（锁会话行 → 连续分配 seq → 逐行插入 → 更新会话
    行）。这里只做角色校验：``user`` 消息必须由 run 创建时写（§4.5 幂等边界），
    从这条路径进来只能是 assistant/tool，否则「重跑 queued」就会重复用户消息。
    """
    for row in rows:
        if str(row.get("role")) not in ("assistant", "tool"):
            raise ValueError(f"append_round only accepts assistant/tool, got {row.get('role')!r}")
    return await _messages().append_batch(str(conversation_oid), rows, usage=usage)


__all__ = [
    "BAD_CURSOR",
    "CONTEXT_LIMIT_DEFAULT",
    "DELETED_PROPOSAL_SUMMARY",
    "LIST_LIMIT_DEFAULT",
    "LIST_LIMIT_MAX",
    "MESSAGE_LIMIT_DEFAULT",
    "MESSAGE_LIMIT_MAX",
    "MODES",
    "MODE_MISMATCH",
    "NOT_FOUND",
    "RUN_CONFLICT",
    "STATUSES",
    "TITLE_MAX",
    "append_round",
    "append_user",
    "context_limit",
    "conversation_view",
    "create",
    "decode_cursor",
    "delete",
    "encode_cursor",
    "fetch_row",
    "get",
    "list_messages",
    "list_page",
    "message_view",
    "require_row",
    "retitle",
    "title_from",
    "update",
]
