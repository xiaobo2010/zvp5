"""会话内的对话（同步 SSE）：``POST /api/ai/conversations/{oid}/chat``（契约 §3.3）。

它与 ``/api/ai/v1/chat/completions`` 是**两个面**，故意不合并：

* ``/v1`` 是无状态 OpenAI 兼容面（服务外部 ``zvk_`` 客户端），请求体自带全量
  ``messages``，服务端不读也不写任何会话；
* 本端点的入参只有 ``{content, model?}``，历史与压缩摘要**从库装配**，回答会落成
  ``ai_messages`` 并累加会话用量。两者共用同一套 provider/路由/额度/审计，但协议
  与副作用完全不同。

四条不能省的顺序：

1. **门禁与预检全部在返回 ``StreamingResponse`` 之前**。响应头一旦发出，状态码就
   改不了：404/409/429 必须还是可解析的 JSON（前端按 ``error.code`` 分支），只有
   「上游在流里失败」才走流内 error 帧。
2. **用户消息先落库，assistant 后落库**。前者是这一轮的起点（客户端要拿
   ``message_oid`` 去对齐自己的乐观消息），后者只有在上游正常结束才写。
3. **中断不写 assistant**（§3.3）。半句话写进库会让下次会话回放时多一条「模型
   说过但没人见过」的答案；失败只留审计。
4. **一条 assistant + 该轮 usage + 会话累加是一个事务**（``append_round``）。分两次
   写就会出现「消息在、token 不在」的行，水位与列表都跟着失真。

流式**不做 provider 切换**：字节已经吐给客户端了，重放等于把两份答案拼在一起
（与 ``routers/ai_router`` 的流式取舍一致）。所以候选只取第一个可用项。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Mapping
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from routers.ai_router import (
    AI_FORBIDDEN_MESSAGE,
    STREAM_DONE_FRAME,
    STREAM_ERROR_FALLBACK,
    STREAM_ERROR_MESSAGES,
    _audit_status,
    _billable,
    _charge,
    _elapsed_ms,
    _evaluate_burst,
    _has_ai_access,
    _openai_error,
    _rate_limited,
    _stream_error_frame,
    ai_scene,
)
from services import violation_guard
from services.agent import context as agent_context
from services.agent import prompt
from services.agent.tool_impl import AgentError
from services.ai import audit, credentials, prompt_audit, prompt_cache, providers
from services.ai import config as ai_config
from services.ai import conversations, quota, rate_limit, routing
from services.ai import moderation
from services.ai import safety_guard
from services.ai import usage as ai_usage
from services.ai.credentials import CredentialError
from services.ai.routing import RouteError
from typings.log import xuehai_id_from
from util import agent_unlock
from util.object_id import get_current_user

_logger = logging.getLogger(__name__)

router = APIRouter()

# 审计里的端点名：与挂载前缀一致，但**不能**复用 ``audit.ENDPOINT_CHAT``——那是
# ``/v1/chat/completions``。混在一起时「哪条链路在烧钱」这个问题就答不了。
ENDPOINT_CHAT_CONVERSATION = "/api/ai/conversations/chat"

# 前端 ``X-AI-Scene`` 缺省时的场景名（``src/api/ai/conversations.ts`` 走的就是
# ``chat``）。它只进 ``AI_CALL_LOGS.PURPOSE``，不参与任何判定。
DEFAULT_PURPOSE = "chat"

# 契约 §2.2：``ai_messages.round_no`` 与 ``ai_call_logs(request_id, round_no)`` 对齐。
# chat 一轮就是一问一答，永远写 1（不写 NULL：NULL 的语义是「不是某轮」）。
CHAT_ROUND_NO = 1

# 历史回放的上限：与 ``services/agent/run_queue`` 同口径。chat 的上下文靠压缩
# 摘要收口，这里只是兜住「一次把上万条 CLOB 拉进内存」的病态情形。
HISTORY_MAX = 400

# 中断/落库失败时写进审计的错误码。前端码表里没有它们，但这两条**不可能**出现在
# 流内（客户端已经断开 / [DONE] 已经发完），只用于后台诊断。
ABORTED_CODE = "AI_STREAM_ABORTED"
PERSIST_FAILED_CODE = "AI_MESSAGE_PERSIST_FAILED"

BODY_MAX_CHARS = 20000

# SSE 帧边界与 ``services/ai/usage`` 同一组（一帧可能被 TCP 切开，也可能多帧挤在
# 一段里）；单帧缓冲区上限同理，超了就不再猜边界。
_FRAME_SEPARATORS = (b"\r\n\r\n", b"\n\n")
_MAX_BUFFER_BYTES = 64 * 1024


class ChatRequest(BaseModel):
    """``model`` 可省：缺省沿用会话最近一次用的模型（再缺省取目录里第一个）。"""

    content: str = Field(min_length=1, max_length=BODY_MAX_CHARS)
    model: str | None = Field(default=None, max_length=120)


# ---------------------------------------------------------------------------
# 审计（一行 AI_CALL_LOGS）
# ---------------------------------------------------------------------------

async def _audit(
    *,
    user_oid: str,
    request_id: str,
    purpose: str | None,
    status: str,
    http_status: int | None,
    error_code: str | None = None,
    model: str | None = None,
    provider: str | None = None,
    key_scope: str | None = None,
    roles_snapshot: str | None = None,
    units: int = 0,
    latency_ms: int | None = None,
    first_token_ms: int | None = None,
    cache_key: str | None = None,
    round_no: int | None = None,
    **tokens: int | None,
) -> None:
    """写一行审计。``audit.record`` 自己吞异常：审计失败不该改变响应结论。"""
    await audit.record(
        user_oid=user_oid,
        capability="chat",
        endpoint=ENDPOINT_CHAT_CONVERSATION,
        provider=provider,
        model=model,
        status=status,
        http_status=http_status,
        error_code=error_code,
        latency_ms=latency_ms,
        units=units,
        request_id=request_id,
        key_scope=key_scope,
        purpose=purpose,
        first_token_ms=first_token_ms,
        roles_snapshot=roles_snapshot,
        cache_key=cache_key,
        round_no=round_no,
        **tokens,
    )


def _error(
    status_code: int,
    code: str,
    message: str,
    *,
    request_id: str,
    headers: dict[str, str] | None = None,
):
    """OpenAI 形状的错误体（前端 ``aiResponseError`` 读 ``error.code``）。"""
    return _openai_error(
        status_code, code, message, request_id=request_id, headers=headers
    )


# ---------------------------------------------------------------------------
# 模型 / 候选（只取一个：流式不切换 provider）
# ---------------------------------------------------------------------------

async def _select_route(
    owner_oid: str, payload_model: object, conversation_model: object
) -> tuple[str, dict]:
    """``(model, entry)``：显式模型 → 会话模型 → 目录里第一个可用。

    与 ``services/agent/executor._select_model`` 同一条优先级：前端不该为了「随便
    用」先发一次 ``/v1/models``；显式指定的模型必须在该用户**可见目录**里，否则
    就是靠猜名字用别人的模型（个人 key 只对本人可见）。
    """
    entries = await credentials.catalog(owner_oid)
    if not entries:
        raise AgentError("AI_NO_PROVIDER", routing.NO_PROVIDER_MESSAGE, 503)

    available = await ai_config.allowed_models_for(owner_oid)
    if not available:
        raise AgentError("AI_NO_PROVIDER", routing.NO_PROVIDER_MESSAGE, 503)

    wanted: str | None = None
    for candidate in (payload_model, conversation_model):
        text = str(candidate).strip() if isinstance(candidate, str) else ""
        if text:
            wanted = text
            break

    if wanted is None:
        chosen = next(iter(available))
    elif wanted in available:
        chosen = wanted
    else:
        raise AgentError("model_not_found", "the requested model is not available", 404)

    try:
        selected = routing.candidates(entries)
    except RouteError as exc:
        raise AgentError(exc.code, exc.message, exc.status_code) from None

    for entry in selected:
        if chosen in (entry.get("models") or []):
            return chosen, entry
    # 模型在目录里但对应 provider 全在冷却。
    raise AgentError("AI_NO_PROVIDER", routing.NO_PROVIDER_MESSAGE, 503)


# ---------------------------------------------------------------------------
# 历史回放
# ---------------------------------------------------------------------------

def _replay(row: Mapping[str, Any]) -> dict:
    """``message_view`` 行 → OpenAI 回放形状（只留正文与工具身份）。

    为什么不把视图整个塞给上游：视图带 ``oid`` / ``usage`` / ``created_at`` 这些
    内部字段，上游不认识它们（严格校验的实现直接 400），而它们对模型也没有意义。
    """
    role = str(row.get("role") or "")
    content = str(row.get("content") or "")
    if role == "assistant":
        message: dict[str, Any] = {"role": "assistant", "content": content}
        calls = row.get("tool_calls")
        if isinstance(calls, list) and calls:
            message["tool_calls"] = calls
        return message
    if role == "tool":
        return {
            "role": "tool",
            "tool_call_id": str(row.get("tool_call_id") or ""),
            "content": content,
        }
    return {"role": "user", "content": content}


async def _history(conversation: Mapping[str, Any]) -> list[dict]:
    """``seq > summary_upto_seq`` 的历史，按仓库的单页上限翻到 400 条为止（§5.1）。

    摘要之前的内容**不进上下文**：它已经在第二条 system 里，再带原文等于把压缩
    白花的钱又花一次。

    归属闸由调用方（``chat_in_conversation`` 的 ``fetch_row``）做过，所以这里不再走
    ``list_messages``：那条路径每页都多一次 ``require_row``（每翻 4 页 = 4 次多余的
    归属查询），而且把「翻到上限」写成了 4 次固定往返。

    查询次数（仓库对 ``list_after`` 的行数封顶是 ``MESSAGE_LIMIT_MAX``，不能靠「一次
    要 400 行」绕过去）：

    * 常见情况（历史 ≤ 100 条，绝大多数会话）—— **1 次**查询；
    * 400 条满额—— 4 次，是仓库单页上限决定的物理下限；
    * 改造前同一场景是 8 次（4 页 × (归属 + 取行)）。
    """
    conversation_oid = str(conversation.get("_id") or "")
    after = max(0, _int(conversation.get("summary_upto_seq")))
    rows: list[dict] = []
    while len(rows) < HISTORY_MAX:
        page = await conversations._messages().list_after(
            conversation_oid, after, limit=conversations.MESSAGE_LIMIT_MAX
        )
        if not page:
            break
        rows.extend(page)
        last = max(after, _int(page[-1].get("seq")))
        if len(page) < conversations.MESSAGE_LIMIT_MAX or last <= after:
            # 取不满一页 = 已经没有下一页；``last <= after`` 是防御：seq 不动时
            # 再翻一次只会拿到同一页。
            break
        after = last
    return [
        _replay(conversations.message_view(row)) for row in rows[:HISTORY_MAX]
    ]


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# 正文旁路：从 SSE 字节里攒回答
# ---------------------------------------------------------------------------

def _find_separator(buffer: bytes) -> tuple[int, int] | None:
    found: tuple[int, int] | None = None
    for separator in _FRAME_SEPARATORS:
        index = buffer.find(separator)
        if index < 0:
            continue
        if found is None or index < found[0]:
            found = (index, index + len(separator))
    return found


class _StreamText:
    """按帧解析 ``delta.content`` 与 ``finish_reason``。

    与 ``usage.StreamUsageTracker`` 是同一套边界处理的两半：那边只要 usage，这边
    只要正文——合并不了，因为「要落库的是正文」这件事对 usage 模块不该有存在感。
    这里**不改**要转发给客户端的字节，只旁路观察。
    """

    def __init__(self) -> None:
        self._buffer = b""
        self._parts: list[str] = []
        self.finish_reason: str | None = None

    @property
    def text(self) -> str:
        return "".join(self._parts)

    def feed(self, chunk: bytes) -> None:
        if not chunk:
            return
        self._buffer += bytes(chunk)
        self._drain(final=False)

    def close(self) -> None:
        self._drain(final=True)

    def _drain(self, *, final: bool) -> None:
        while True:
            cut = _find_separator(self._buffer)
            if cut is None:
                break
            frame, self._buffer = self._buffer[: cut[0]], self._buffer[cut[1] :]
            self._consume(frame)
        if self._buffer and (final or len(self._buffer) > _MAX_BUFFER_BYTES):
            if final:
                self._consume(self._buffer)
            self._buffer = b""

    def _consume(self, frame: bytes) -> None:
        for line in frame.decode("utf-8", "replace").replace("\r\n", "\n").split("\n"):
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if not data or data == "[DONE]":
                continue
            try:
                payload = json.loads(data)
            except ValueError:
                continue
            if not isinstance(payload, dict):
                continue
            choices = payload.get("choices")
            if not isinstance(choices, list) or not choices:
                continue
            first = choices[0]
            if not isinstance(first, dict):
                continue
            finish = first.get("finish_reason")
            if isinstance(finish, str) and finish:
                self.finish_reason = finish
            delta = first.get("delta")
            if isinstance(delta, dict):
                content = delta.get("content")
                if isinstance(content, str) and content:
                    self._parts.append(content)


def _head_frame(conversation_oid: str, message_oid: str) -> bytes:
    """首帧：两个句柄，先于任何上游字节发出。

    客户端在拿到首帧的瞬间就能把「乐观插入的用户气泡」换成服务端的 ``seq`` 载体，
    不必等第一个字——否则弱网下用户会看到自己的消息先消失再出现。
    """
    return (
        "data: "
        + json.dumps(
            {"conversation_oid": conversation_oid, "message_oid": message_oid},
            ensure_ascii=False,
        )
        + "\n\n"
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------

@router.post("/conversations/{oid}/chat")
async def chat_in_conversation(
    oid: str,
    payload: ChatRequest,
    request: Request,
    user=Depends(get_current_user),
    scene: str | None = Depends(ai_scene),
):
    """在会话里发一条消息，SSE 返回回答，结束后落库。"""
    request_id = uuid4().hex
    purpose = scene or DEFAULT_PURPOSE
    owner_oid = str(user.get("id") or "")
    roles = user.get("per")
    snapshot = audit.roles_snapshot(roles)

    if not _has_ai_access(user):
        await _audit(
            user_oid=owner_oid,
            request_id=request_id,
            purpose=purpose,
            status="rejected",
            http_status=403,
            error_code="AI_FORBIDDEN",
            roles_snapshot=snapshot,
        )
        return _error(403, "AI_FORBIDDEN", AI_FORBIDDEN_MESSAGE, request_id=request_id)

    if not ai_config.is_enabled():
        await _audit(
            user_oid=owner_oid,
            request_id=request_id,
            purpose=purpose,
            status="rejected",
            http_status=503,
            error_code="AI_DISABLED",
            roles_snapshot=snapshot,
        )
        return _error(503, "AI_DISABLED", "ai gateway is disabled", request_id=request_id)

    # 归属闸一次就够：``fetch_row`` 只认本人的行，非本人与不存在回同一个 404
    # （否则 oid 就成了探针）。这类 404/409 是正常控制流，不落审计。
    conversation = await conversations.fetch_row(owner_oid, str(oid))
    if conversation is None:
        return _error(
            404,
            conversations.NOT_FOUND,
            "conversation not found",
            request_id=request_id,
        )
    if str(conversation.get("mode") or "") != "chat":
        # 往 agent 会话发同步 chat 会把后台任务语义换成同步流：两种产物不能互换。
        return _error(
            409,
            conversations.MODE_MISMATCH,
            "chat requires a chat conversation",
            request_id=request_id,
        )

    content = str(payload.content or "").strip()
    if not content:
        return _error(
            400, "AI_BAD_REQUEST", "content must not be empty", request_id=request_id
        )

    # 正文留存必须在审核拒绝的 early-return 之前：被拦下的越狱原文本身就是
    # 事后复核的证据。``capture`` 仍按角色白名单过滤，白名单外的角色不留。
    await prompt_audit.capture(
        user=dict(user),
        purpose=purpose,
        messages=[{"role": "user", "content": content}],
        request_id=request_id,
    )

    # 审核必须在 ``StreamingResponse`` 之前：响应头一旦发出，422 就只能藏在流里，
    # 前端按 ``error.code`` 的分支会全部失效。chat 不做密码门——它没有写工具。
    if agent_unlock.is_unrestricted(owner_oid):
        await _audit(
            user_oid=owner_oid,
            request_id=request_id,
            purpose=purpose,
            status="warning",
            http_status=200,
            error_code="AI_AGENT_MODERATION_SKIPPED",
            roles_snapshot=snapshot,
        )
    else:
        verdict = moderation.check(content)
        if verdict.hit:
            _logger.warning(
                "conversation chat 审核拦截 user=%s rule=%s", owner_oid, verdict.rule
            )
            try:
                await violation_guard.record_agent_jailbreak(
                    user,
                    xuehai_id=xuehai_id_from(request),
                    rule=str(verdict.rule or ""),
                    detail=content,
                )
            except Exception:  # noqa: BLE001 - 证据入库失败不该把拒绝变成 500
                _logger.exception("agent 越狱违规入库失败（本次仍然拒绝）")
            await _audit(
                user_oid=owner_oid,
                request_id=request_id,
                purpose=purpose,
                status="rejected",
                http_status=422,
                error_code="AI_AGENT_MODERATION_REJECTED",
                roles_snapshot=snapshot,
            )
            return _error(
                422,
                "AI_AGENT_MODERATION_REJECTED",
                moderation.REJECTED_MESSAGE,
                request_id=request_id,
            )

    # 灰区闸门：关键词规则之后、路由/扣费之前。只对 department 生效（admin 在
    # ``safety_guard.is_department`` 里已豁免），非 department 不触达网络。
    # 拒绝时不选路由、不扣额度、不落用户消息之外任何东西。
    screened = await safety_guard.screen(
        user,
        content,
        model_hint=payload.model or conversation.get("model"),
    )
    if not screened.allowed:
        try:
            outcome = await violation_guard.record_agent_jailbreak(
                user,
                xuehai_id=xuehai_id_from(request),
                rule=str(screened.category or ""),
                detail=content,
            )
        except Exception:  # noqa: BLE001 - 证据入库失败不该把拒绝变成 500
            _logger.exception("AI 安全认证违规入库失败（本次仍然拒绝）")
            outcome = {}
        code = "AI_SAFETY_BANNED" if outcome.get("banned") else "AI_SAFETY_WARNED"
        await _audit(
            user_oid=owner_oid,
            request_id=request_id,
            purpose=purpose,
            status="rejected",
            http_status=422,
            error_code=code,
            model=str(payload.model or conversation.get("model") or "") or None,
            roles_snapshot=snapshot,
        )
        return _error(
            422,
            code,
            safety_guard.REJECTED_MESSAGE,
            request_id=request_id,
        )

    try:
        model, entry = await _select_route(
            owner_oid, payload.model, conversation.get("model")
        )
    except AgentError as exc:
        await _audit(
            user_oid=owner_oid,
            request_id=request_id,
            purpose=purpose,
            status=_audit_status(exc.code),
            http_status=exc.status_code,
            error_code=exc.code,
            model=str(payload.model or conversation.get("model") or "") or None,
            roles_snapshot=snapshot,
        )
        return _error(exc.status_code, exc.code, exc.message, request_id=request_id)

    try:
        api_key = await credentials.resolve_api_key(entry)
    except CredentialError as exc:
        # 库里的密文坏了（换过主密钥 / 被改）：换 provider 也救不了一条解不开的
        # key，直接给结论，并把这条候选记进熔断。
        routing.mark_failure(str(entry.get("key") or ""))
        await _audit(
            user_oid=owner_oid,
            request_id=request_id,
            purpose=purpose,
            status="rejected",
            http_status=exc.status_code,
            error_code=exc.code,
            model=model,
            provider=str(entry.get("name") or "") or None,
            key_scope=entry.get("scope"),
            roles_snapshot=snapshot,
        )
        return _error(exc.status_code, exc.code, exc.message, request_id=request_id)

    # 分钟级频控与月额度预检：与 ``/v1/chat/completions`` 完全同口径、同先后
    # （先频控再配额），否则「同一用户在两个面上限流不一致」会变成可绕过的洞。
    try:
        key_oid = user.get("api_key_oid")
        if key_oid:
            rate_limit.check(
                f"{owner_oid}:{key_oid}",
                "chat",
                limit=ai_config.api_key_rate_limit_per_minute(),
            )
        rate_limit.check(owner_oid, "chat")
    except rate_limit.RateLimitError as exc:
        await _audit(
            user_oid=owner_oid,
            request_id=request_id,
            purpose=purpose,
            status="rejected",
            http_status=429,
            error_code="AI_RATE_LIMITED",
            model=model,
            provider=str(entry.get("name") or "") or None,
            key_scope=entry.get("scope"),
            roles_snapshot=snapshot,
        )
        return _rate_limited(exc, request_id=request_id)

    if _billable(entry):
        allowed, used, limit = await quota.peek(owner_oid, "chat", 1, roles=roles)
        if not allowed:
            await _audit(
                user_oid=owner_oid,
                request_id=request_id,
                purpose=purpose,
                status="rejected",
                http_status=429,
                error_code="insufficient_quota",
                model=model,
                provider=str(entry.get("name") or "") or None,
                key_scope=entry.get("scope"),
                roles_snapshot=snapshot,
            )
            return _error(
                429,
                "insufficient_quota",
                "monthly chat quota is exhausted",
                request_id=request_id,
                headers={
                    "X-AI-Quota-Used": str(used),
                    "X-AI-Quota-Limit": str(limit),
                },
            )

    # 并发闸门必须在返回 StreamingResponse 之前拿到：进了生成器再报 429 时响应头
    # 已经发出去了，客户端只会看到一个空流（与 ``_stream_completion`` 同一理由）。
    guard = routing.slot()
    try:
        await guard.__aenter__()
    except RouteError as exc:
        # 闸门满不是上游故障，不计入熔断。
        await _audit(
            user_oid=owner_oid,
            request_id=request_id,
            purpose=purpose,
            status=_audit_status(exc.code),
            http_status=exc.status_code,
            error_code=exc.code,
            model=model,
            provider=str(entry.get("name") or "") or None,
            key_scope=entry.get("scope"),
            roles_snapshot=snapshot,
        )
        return _error(exc.status_code, exc.code, exc.message, request_id=request_id)

    try:
        history = await _history(conversation)
        messages = agent_context.build_messages(
            prompt.SYSTEM_PROMPT,
            conversation.get("summary"),
            history,
            [{"role": "user", "content": content}],
        )
        inserted = await conversations.append_user(
            str(oid), content, request_id=request_id
        )
        # ``append_user`` 自己分配 oid（不依赖仓储的 ``_id`` 别名），所以首帧句柄
        # 与库里那一行必然一致。
        message_oid = str(inserted.get("oid") or "")
        await _backfill_title(conversation, owner_oid, str(oid), content)
    except Exception:  # noqa: BLE001 - 落库/组装失败仍是可解析的 JSON，不能漏闸门
        await guard.__aexit__(None, None, None)
        _logger.exception("ai chat conversation setup failed")
        await _audit(
            user_oid=owner_oid,
            request_id=request_id,
            purpose=purpose,
            status="error",
            http_status=500,
            error_code="AI_AGENT_INTERNAL_ERROR",
            model=model,
            provider=str(entry.get("name") or "") or None,
            key_scope=entry.get("scope"),
            roles_snapshot=snapshot,
        )
        return _error(
            500,
            "AI_AGENT_INTERNAL_ERROR",
            "chat request failed unexpectedly",
            request_id=request_id,
        )

    upstream_payload = {"model": model, "messages": messages, "stream": True}
    # 缓存注入只改「发给上游的 body」：``upstream_payload`` 还要用来估算字符数。
    body, cache_key = prompt_cache.request_body(upstream_payload, entry)

    turn: dict[str, Any] = {
        "conversation_oid": str(oid),
        "owner_oid": owner_oid,
        "message_oid": message_oid,
        "request_id": request_id,
        "model": model,
        "entry": entry,
        "api_key": api_key,
        "purpose": purpose,
        "roles": roles,
        "roles_snapshot": snapshot,
        "body": body,
        "cache_key": cache_key,
        "prompt_chars": ai_usage.prompt_chars(upstream_payload),
        "prompt_tokens_estimate": agent_context.estimate_tokens(messages),
    }

    return StreamingResponse(
        _stream_turn(turn, guard),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-AI-Request-Id": request_id,
        },
    )


async def _backfill_title(
    conversation: Mapping[str, Any], owner_oid: str, oid: str, content: str
) -> None:
    """首条用户消息补标题（§2.1）。只在「还没有标题」时写一次。

    标题是展示信息：写失败只记一条 warning，不能因此废掉这次对话。
    """
    if str(conversation.get("title") or "").strip():
        return
    try:
        await conversations.retitle(
            owner_oid=owner_oid, oid=oid, title=conversations.title_from(content)
        )
    except Exception:  # noqa: BLE001
        _logger.warning("ai conversation title backfill failed")


# ---------------------------------------------------------------------------
# 流式生成
# ---------------------------------------------------------------------------

async def _stream_turn(turn: Mapping[str, Any], guard) -> Any:
    """首帧 → 上游字节原样转发 → 收尾落库/审计/扣费。"""
    name = str(turn["entry"].get("name") or "")
    key = str(turn["entry"].get("key") or "")
    tracker = ai_usage.StreamUsageTracker()
    sink = _StreamText()
    state: dict[str, Any] = {
        "chars": 0,
        "status": "success",
        "error_code": None,
        "first_token_ms": None,
    }
    started = time.monotonic()
    completed = False

    try:
        yield _head_frame(str(turn["conversation_oid"]), str(turn["message_oid"]))
        async for chunk in providers.chat_completion_stream(
            turn["body"], turn["entry"], turn["api_key"]
        ):
            if chunk:
                if state["first_token_ms"] is None:
                    state["first_token_ms"] = _elapsed_ms(started)
                state["chars"] += len(chunk)
                tracker.feed(chunk)
                sink.feed(chunk)
                yield chunk
    except asyncio.CancelledError:
        # 客户端断开 / 进程收尾：半句话不落库（§3.3），但不许把它记成 success——
        # 「这次调用没有完成」本身就是后台要看到的事实。
        state["status"] = "error"
        state["error_code"] = ABORTED_CODE
        raise
    except Exception as exc:  # noqa: BLE001 - 已开始输出，只能收尾
        # 响应头早就发出去了：失败只能写成流内 error 帧 + [DONE]（与
        # ``_stream_completion`` 同形，前端 ``extractStreamError`` 认这个形状）。
        raw_code = getattr(exc, "code", None)
        code = (
            raw_code
            if isinstance(raw_code, str) and raw_code in STREAM_ERROR_MESSAGES
            else STREAM_ERROR_FALLBACK
        )
        state["status"] = _audit_status(code)
        state["error_code"] = code
        routing.mark_failure(key)
        # 只记异常类名与码：message 可能带上游原文，不能进日志。
        _logger.warning(
            "ai conversation stream failed on %s: %s (%s)",
            name,
            type(exc).__name__,
            code,
        )
        yield _stream_error_frame(code, STREAM_ERROR_MESSAGES[code])
        yield STREAM_DONE_FRAME
    else:
        routing.mark_success(key)
        completed = True
    finally:
        try:
            await _finalize(turn, state, tracker, sink, completed=completed, started=started)
        except (asyncio.CancelledError, GeneratorExit):
            # 客户端断开：协程被取消，收尾可能连一次 await 都跑不完。正文不落库
            # 这件事已经由 ``completed=False`` 决定，其余只是少一条流水。
            raise
        except Exception:  # noqa: BLE001 - 收尾失败不该让客户端看到半个流
            _logger.exception("ai conversation finalize failed")
        finally:
            await guard.__aexit__(None, None, None)


async def _finalize(
    turn: Mapping[str, Any],
    state: Mapping[str, Any],
    tracker: ai_usage.StreamUsageTracker,
    sink: _StreamText,
    *,
    completed: bool,
    started: float,
) -> None:
    """落库 → 审计 → 扣减。三步都**不能**改变已经发完的流，只留痕迹。"""
    # 最后一帧可能没有空行收尾（上游在 [DONE] 后直接关连接），补解析一次。
    tracker.close()
    sink.close()

    captured = tracker.captured
    tokens = {
        "prompt_tokens": tracker.prompt_tokens,
        "completion_tokens": tracker.completion_tokens,
        "total_tokens": tracker.total_tokens,
        "cache_read_tokens": tracker.cache_read_tokens,
        "cache_write_tokens": tracker.cache_write_tokens,
    }
    if captured:
        units = ai_usage.units_from_total_tokens(tokens["total_tokens"])
    else:
        # 没有 usage：token 列留 NULL、units 按字符估算（「NULL ⟺ 估算」不变式）。
        units = ai_usage.estimate_units(int(turn["prompt_chars"]), int(state["chars"]))

    status = str(state["status"])
    error_code = state["error_code"]

    if completed:
        try:
            await _persist(turn, tokens, sink.text, sink.finish_reason)
        except Exception:  # noqa: BLE001 - 答案已经发给用户了，落库失败只能记账
            _logger.exception("ai conversation assistant persist failed")
            status = "error"
            error_code = PERSIST_FAILED_CODE

    await _audit(
        user_oid=str(turn["owner_oid"]),
        request_id=str(turn["request_id"]),
        purpose=turn["purpose"],
        status=status,
        # 流式只有「上游失败」会带 5xx/4xx，那时响应头已经是 200 了。
        http_status=200 if status == "success" else None,
        error_code=error_code,
        model=str(turn["model"]),
        provider=str(turn["entry"].get("name") or "") or None,
        key_scope=turn["entry"].get("scope"),
        roles_snapshot=turn["roles_snapshot"],
        units=units,
        latency_ms=_elapsed_ms(started),
        first_token_ms=state["first_token_ms"],
        cache_key=turn["cache_key"],
        round_no=CHAT_ROUND_NO,
        **tokens,
    )

    # 后置扣减是 best-effort：额度回写失败不能丢掉已经生成的答案。
    if _billable(turn["entry"]):
        await _charge(str(turn["owner_oid"]), units, turn["roles"])
    # 流式请求的「完成」就在生成器收尾：此时流水已落库，计数包含本次。
    await _evaluate_burst(str(turn["owner_oid"]))


async def _persist(
    turn: Mapping[str, Any],
    tokens: Mapping[str, int | None],
    text: str,
    finish_reason: str | None,
) -> None:
    """assistant 消息 + 该轮 usage + 会话累加，**一个事务**（``append_round``）。

    ``context_tokens`` 是覆盖语义的上下文水位：有实测就用实测 prompt token，没有
    就用组装时的估算（§5.2）。token 列留 NULL 只影响「这一轮花了多少」的展示，
    不该让水位停在上一轮——否则压缩永远不触发。
    """
    estimate = _int(turn.get("prompt_tokens_estimate"))
    measured = tokens.get("prompt_tokens")
    context_tokens = measured if measured is not None else estimate

    row = {
        "oid": uuid4().hex,
        "role": "assistant",
        "content": str(text or ""),
        "finish_reason": str(finish_reason or "stop"),
        "round_no": CHAT_ROUND_NO,
        "request_id": str(turn["request_id"]),
        # 未实测时保持 NULL：``message_view.usage`` 的 NULL 语义是「上游没报」，
        # 写 0 会把「不知道」伪装成一个观测值。
        "prompt_tokens": tokens.get("prompt_tokens"),
        "completion_tokens": tokens.get("completion_tokens"),
        "total_tokens": tokens.get("total_tokens"),
        "cache_read_tokens": tokens.get("cache_read_tokens"),
        "cache_write_tokens": tokens.get("cache_write_tokens"),
    }

    usage_block = {
        "prompt_tokens": tokens.get("prompt_tokens") or 0,
        "completion_tokens": tokens.get("completion_tokens") or 0,
        "total_tokens": tokens.get("total_tokens") or 0,
        "context_tokens": context_tokens,
    }
    await conversations.append_round(
        str(turn["conversation_oid"]), [row], usage=usage_block
    )


__all__ = ["router"]
