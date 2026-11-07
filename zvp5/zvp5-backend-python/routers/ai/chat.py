"""OpenAI 兼容面：``/v1/models`` 与 ``/v1/chat/completions`` 的入口层。

请求体上限、内容审核、选路/频控/额度预检在这里；真正的上游调用在
:mod:`routers.ai.completion`（非流式）与 :mod:`routers.ai.streaming`（流式）。
"""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from routers.ai.common import (
    AI_FORBIDDEN_MESSAGE,
    MAX_PROVIDER_ATTEMPTS,
    _billable,
    _feature_error,
    _has_ai_access,
    _is_admin,
    _openai_error,
    _rate_limited,
    _reject,
    _reject_http,
    ai_scene,
    get_ai_client,
)
from routers.ai.completion import _blocking_completion
from routers.ai.streaming import _stream_completion
from services import violation_guard
from services.ai import (
    audit,
    ban_state,
    credentials,
    prompt_audit,
    quota,
    rate_limit,
    routing,
)
from services.ai import config as ai_config
from services.ai import safety_guard
from services.ai.routing import RouteError
from typings.log import xuehai_id_from

router = APIRouter()

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# OpenAI 兼容面
# ---------------------------------------------------------------------------


@router.get("/v1/models")
async def list_models(user=Depends(get_ai_client)):
    """只列这个用户真的能调的模型：个人 key 的模型不该出现在别人的列表里。

    ``id`` 是合格 ID ``provider/model``：不同渠道可以有同名模型，按裸名去重等于
    把排在后面的渠道从列表里抹掉（客户端永远看不到、也就永远调不到）。``owned_by``
    回 provider 名，客户端据此知道这条 ID 的前缀该写什么。
    """
    request_id = uuid4().hex
    if not _has_ai_access(user):
        # OpenAI 兼容面不能抛 detail：客户端只认 {"error": {...}}。
        return _openai_error(
            403, "AI_FORBIDDEN", AI_FORBIDDEN_MESSAGE, request_id=request_id
        )
    denied = _feature_error(user, "ai.chat", request_id=request_id)
    if denied is not None:
        return denied
    entries = await credentials.catalog(user["id"])
    owners: dict[str, str] = {}
    contexts: dict[str, int | None] = {}
    for entry, model, qualified in credentials.catalog_models(entries):
        owners.setdefault(qualified, str(entry.get("name") or ""))
        # 上下文窗口来自凭据里的模型级配置（R4）；没配就是 NULL——「不知道」
        # 不能用一个猜出来的数字顶替。
        contexts.setdefault(qualified, (entry.get("model_contexts") or {}).get(model))
    return {
        "object": "list",
        "data": [
            {
                "id": qualified,
                "object": "model",
                "owned_by": owner,
                "context": contexts.get(qualified),
            }
            for qualified, owner in owners.items()
        ],
    }


# ---------------------------------------------------------------------------
# 请求体上限（OpenAI 兼容面）
# ---------------------------------------------------------------------------

# 错误码与 OpenAI 的形状保持一致：``413 + invalid_request_error`` 是客户端认得
# 的那一套（SDK 会把它当「请求太大」而不是重试）。
BODY_TOO_LARGE_CODE = "request_too_large"
INVALID_BODY_CODE = "invalid_request_body"
TOO_MANY_MESSAGES_CODE = "too_many_messages"
MESSAGE_TOO_LONG_CODE = "message_too_long"


def _too_large_error(limit: int, request_id: str) -> JSONResponse:
    return _openai_error(
        413,
        BODY_TOO_LARGE_CODE,
        f"request body must not exceed {limit} bytes",
        request_id=request_id,
    )


async def _read_chat_payload(
    request: Request, request_id: str
) -> tuple[dict | None, JSONResponse | None]:
    """流式读 body 并解析成 dict；超限/非法时返回 OpenAI 错误体。

    ``Content-Length`` 只是**第一道**（能让 99% 的超大请求连读都不读）：它由客户端
    自己声明，分块传输（chunked）时根本没有，撒谎也不会被校验——所以边读边累计
    实际字节数，再比一次。
    """
    limit = ai_config.chat_max_body_bytes()
    declared = request.headers.get("content-length")
    if declared:
        try:
            if int(declared) > limit:
                return None, _too_large_error(limit, request_id)
        except ValueError:
            # 声明一个非数字的 Content-Length 不改变结论：按实际读取量判定。
            pass

    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            return None, _too_large_error(limit, request_id)
        chunks.append(chunk)

    raw = b"".join(chunks)
    try:
        payload = json.loads(raw) if raw else None
    except (ValueError, UnicodeDecodeError):
        return None, _openai_error(
            400,
            INVALID_BODY_CODE,
            "request body must be valid JSON",
            request_id=request_id,
        )
    if not isinstance(payload, dict):
        return None, _openai_error(
            400,
            INVALID_BODY_CODE,
            "request body must be a JSON object",
            request_id=request_id,
        )
    return payload, None


def _content_chars(content: object) -> int:
    """一条消息的正文长度；multimodal 数组只算 text 段（图片按字节由体积上限管）。"""
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, str):
                total += len(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    total += len(text)
        return total
    return 0


def _payload_limit_error(payload: dict, request_id: str) -> JSONResponse | None:
    """条数/单条长度的结构上限。``messages`` 类型不对时**不在这里**判（照旧透传）。"""
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return None

    max_messages = ai_config.chat_max_messages()
    if len(messages) > max_messages:
        return _openai_error(
            400,
            TOO_MANY_MESSAGES_CODE,
            f"messages must contain at most {max_messages} items",
            request_id=request_id,
        )

    max_chars = ai_config.chat_max_content_chars()
    for message in messages:
        if not isinstance(message, dict):
            continue
        if _content_chars(message.get("content")) > max_chars:
            return _openai_error(
                400,
                MESSAGE_TOO_LONG_CODE,
                f"each message content must be at most {max_chars} characters",
                request_id=request_id,
            )
    return None


def _last_user_text(messages: object) -> str:
    """OpenAI ``messages`` 里最后一条 user 的纯文本。

    multimodal 数组只拼 ``text`` 段（图片没有用户文本可审）；找不到 user 消息时
    返回空串，调用方据此跳过安全认证——判定一个空请求没有意义，而拒绝它会让
    「只带 system 的合规调用」被误伤。
    """
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(part["text"])
            return "\n".join(parts)
        return ""
    return ""


async def _record_violation(
    user: dict, *, request: Request, text: str, rule: str
) -> bool:
    """记违规 + 警告 + 首档封禁；返回本次是否发了警告。

    落封后立刻 ``ban_state.invalidate``：同一次请求里后续的流式 chunk 检查要能
    看到「刚封的这个人」，不能等 TTL 到期。

    证据入库失败只记日志（拒绝照旧）：取证链坏掉是运维问题，不该把一次内容拒绝
    变成 500。
    """
    try:
        outcome = await violation_guard.record_agent_jailbreak(
            user,
            xuehai_id=xuehai_id_from(request),
            rule=rule,
            detail=text,
        )
    except Exception:  # noqa: BLE001 - 证据入库失败不该把拒绝变成 500
        _logger.exception("AI 安全认证违规入库失败（本次仍然拒绝）")
        return False
    if (outcome or {}).get("banned"):
        ban_state.invalidate(str(user.get("id") or ""))
    return bool((outcome or {}).get("warned"))


async def _screen_chat_request(
    *,
    user: dict,
    payload: dict,
    user_oid: str,
    request: Request,
    request_id: str,
    purpose: str | None,
    scene_roles: str | None,
    audit_oid: str | None = None,
) -> JSONResponse | None:
    """chat 面的内容审核：放行返回 ``None``，拒绝返回 OpenAI 错误体。

    内容安全**只有一道判据**：``safety_guard.screen``——用当前会话的模型（``model``
    即本次请求声明的模型，作为 ``model_hint``）在调上游之前判一次「是否属于平台
    事务」，无关即拒绝并记违规/首档封号。规则词表不参与门禁（词表本体保留在
    :mod:`services.ai.moderation` 备查）；过去的关键词层对所有角色生效，现在同一
    口径交给 LLM 判定，不再有「正则与模型各判一半」的分裂。

    位置固定在「角色/开关门禁之后、候选选择与频控/额度之前」：被拒请求不能花掉
    上游调用，也不该占用用户或 API key 的分钟窗口。

    判定跑不起来（无凭据/超时/上游失败/坏 JSON）时只拒绝本次回答、不写证据：
    对外同一个 403 + ``AI_SAFETY_REJECTED`` + 固定文案，攻击者无法按差异调参。

    ``zvk_`` 平台 key 与 JWT 走同一套判定：``get_ai_client`` 已经把 key 换成
    与 ``get_current_user`` 同形的 user dict（``per`` = owner 的原始角色），所以
    这里不需要再查一次库。
    """
    text = _last_user_text(payload.get("messages"))
    if not text.strip():
        return None

    model_name = str(payload.get("model") or "") or None

    if not safety_guard.is_department(user):
        return None

    verdict = await safety_guard.screen(user, text, model_hint=model_name)
    if verdict.allowed:
        return None

    code = safety_guard.REJECTED_CODE
    warned = False
    if not verdict.unavailable:
        warned = await _record_violation(
            user, request=request, text=text, rule=str(verdict.category or "")
        )
    # 合规审计：被拒也要在留存行上标出来（refused / warned），否则「谁被拒过」
    # 只能靠 AI_CALL_LOGS 的错误码去对时间。
    await prompt_audit.record_outcome(
        audit_oid, refused=True, warned=True if warned else None
    )

    await _reject_http(
        user_oid=user_oid,
        model=model_name,
        request_id=request_id,
        status_code=403,
        code=code,
        purpose=purpose,
        roles_snapshot=scene_roles,
    )
    return _openai_error(
        403, code, safety_guard.REJECTED_MESSAGE, request_id=request_id
    )


@router.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    user=Depends(get_ai_client),
    purpose: str | None = Depends(ai_scene),
):
    request_id = uuid4().hex
    user_oid = user["id"]
    # 角色快照只进审计（V033）：chat / OCR 不因它改变任何判定，缺省 NULL = 未采集。
    scene_roles = audit.roles_snapshot(user.get("per"))

    # 体积/结构上限必须在读 body 之前定下来：``Body(...)`` 的形式里 FastAPI 会先把
    # 整个请求体读进内存再解析，一个 500MB 的 body 就是「一个请求打满单 worker 的
    # 内存」。这里改成自己流式读，边读边比上限。**不落审计**：这是解析前的拒绝，
    # 落审计等于给「拿垃圾 body 刷 413」开了个写库放大器。
    payload, limit_error = await _read_chat_payload(request, request_id)
    if limit_error is not None:
        return limit_error
    structure_error = _payload_limit_error(payload, request_id)
    if structure_error is not None:
        return structure_error

    # prompt 受控留存（AI_PROMPT_AUDIT）：只在这里挂一次，覆盖 chat 与
    # markdown-assistant 两种 Scene（OCR 没有 prompt，不涉及）。是否真的留、留
    # 多少、怎么脱敏都由 services.ai.prompt_audit 决定，且它是旁路——任何失败
    # 都不会改变本请求的返回。放在门禁之前是有意的：被拒的请求同样可能带着
    # 需要复核的正文（「这个人反复尝试问什么」本身就是治理信号）。
    audit_oid = await prompt_audit.capture(
        user=user,
        purpose=purpose,
        messages=payload.get("messages"),
        request_id=request_id,
    )

    if not _has_ai_access(user):
        await _reject_http(
            user_oid=user_oid,
            model=str(payload.get("model") or "") or None,
            request_id=request_id,
            status_code=403,
            code="AI_FORBIDDEN",
            purpose=purpose,
            roles_snapshot=scene_roles,
        )
        return _openai_error(
            403, "AI_FORBIDDEN", AI_FORBIDDEN_MESSAGE, request_id=request_id
        )

    # 角色白名单之后再加一道「功能开关」：department 默认开着，等价改造前行为；
    # 管理员在开关层恒为 True（policy 的短路），不会因为关掉开关而掉线。
    denied = _feature_error(user, "ai.chat", request_id=request_id)
    if denied is not None:
        return denied

    if not ai_config.is_enabled():
        await _reject_http(
            user_oid=user_oid,
            model=str(payload.get("model") or "") or None,
            request_id=request_id,
            status_code=503,
            code="AI_DISABLED",
            purpose=purpose,
            roles_snapshot=scene_roles,
        )
        return _openai_error(
            503, "AI_DISABLED", "ai gateway is disabled", request_id=request_id
        )

    # 灰区闸门：department 的「无关 + 破坏性」请求在选路/频控/额度之前挡掉。
    # 被拒时这里已经落过审计行（``_reject_http`` 写 status=rejected）。
    safety_error = await _screen_chat_request(
        user=user,
        payload=payload,
        user_oid=user_oid,
        request=request,
        request_id=request_id,
        purpose=purpose,
        scene_roles=scene_roles,
        audit_oid=audit_oid,
    )
    if safety_error is not None:
        return safety_error

    model = payload.get("model")
    entries = await credentials.catalog(user_oid)
    if not entries:
        # 一个可用 provider 都没有 = 服务端/个人都没配好，不是客户端选错模型：
        # 先报 503，否则所有 model 都会被误判成 404，运维看不出是配置问题。
        return await _reject(
            RouteError("AI_NO_PROVIDER", routing.NO_PROVIDER_MESSAGE, 503),
            user_oid=user_oid,
            model=str(model or ""),
            request_id=request_id,
            latency_ms=0,
            purpose=purpose,
            roles_snapshot=scene_roles,
        )

    # 客户端给的是合格 ID（``provider/model``）或老数据里的裸名，统一在这里解析：
    # 同名模型只能靠合格 ID 区分，裸名有多家命中时要求客户端明确指定渠道。
    requested = model
    try:
        resolved = credentials.resolve_model(entries, model)
    except credentials.ModelAmbiguous as ambiguous:
        await _reject_http(
            user_oid=user_oid,
            model=str(requested or "") or None,
            request_id=request_id,
            status_code=400,
            code="model_ambiguous",
            purpose=purpose,
            roles_snapshot=scene_roles,
        )
        return _openai_error(
            400, "model_ambiguous", ambiguous.message, request_id=request_id
        )
    if resolved is None:
        await _reject_http(
            user_oid=user_oid,
            model=str(requested or "") or None,
            request_id=request_id,
            status_code=404,
            code="model_not_found",
            purpose=purpose,
            roles_snapshot=scene_roles,
        )
        return _openai_error(
            404,
            "model_not_found",
            "the requested model is not available",
            request_id=request_id,
        )
    # 对外/审计/回显用合格 ID，发给上游的仍是裸模型名。
    model = resolved.qualified
    upstream_model = resolved.model

    # ``provider`` 不是 OpenAI 协议字段，只有 admin 能拿它做定向调试；普通用户
    # 传了也忽略（报错会让第三方 SDK 多一个必填字段，得不偿失）。
    forced = payload.get("provider") if _is_admin(user) else None

    try:
        selected = await routing.candidates(
            entries, forced=str(forced) if forced else None
        )
    except RouteError as exc:
        return await _reject(
            exc,
            user_oid=user_oid,
            model=model,
            request_id=request_id,
            latency_ms=0,
            purpose=purpose,
            roles_snapshot=scene_roles,
        )

    candidates = credentials.route_candidates(selected, resolved)[:MAX_PROVIDER_ATTEMPTS]
    if not candidates:
        # 模型在目录里但对应 provider 正在冷却。
        return await _reject(
            RouteError("AI_NO_PROVIDER", routing.NO_PROVIDER_MESSAGE, 503),
            user_oid=user_oid,
            model=model,
            request_id=request_id,
            latency_ms=0,
            purpose=purpose,
        )

    # 分钟级频控：月额度防长期滥用，这一层防「一个用户在一分钟内把单 worker 打满」。
    # 位置在配额预检之前、角色门禁之后——被门禁拒掉的请求不计数。
    # 独立 API key 另有「每把 key」的维度（默认 60 次/分钟）：先查更细的那层，
    # 这样一把失控的客户端不会提前消耗同一个人的用户级窗口。
    try:
        key_oid = user.get("api_key_oid")
        if key_oid:
            await rate_limit.check(
                f"{user_oid}:{key_oid}",
                "chat",
                limit=ai_config.api_key_rate_limit_per_minute(),
            )
        await rate_limit.check(user_oid, "chat")
    except rate_limit.RateLimitError as exc:
        # 被自家频控挡下也是「有一次调用没成」：落 rejected，http_status 记 429。
        # 上游限流才是 busy —— 两者混起来会让「我们挡的」看起来像「上游挂了」。
        await _reject_http(
            user_oid=user_oid,
            model=model,
            request_id=request_id,
            status_code=429,
            code="AI_RATE_LIMITED",
            purpose=purpose,
            roles_snapshot=scene_roles,
        )
        return _rate_limited(exc, request_id=request_id)

    # 先只读预检：明显超额的请求不该先花掉上游成本再被拒。个人 key 由管理员
    # 自己付费，不占平台额度，所以整段配额逻辑对它跳过；预检只看首个候选的
    # scope——真落到共享项时，扣减本身仍是原子的，不会超卖。
    if _billable(candidates[0]):
        allowed, used, limit = await quota.peek(
            user_oid, "af", 1, roles=user.get("per")
        )
        if not allowed:
            await _reject_http(
                user_oid=user_oid,
                model=model,
                request_id=request_id,
                status_code=429,
                code="insufficient_quota",
                purpose=purpose,
                roles_snapshot=scene_roles,
            )
            return _openai_error(
                429,
                "insufficient_quota",
                "monthly chat quota is exhausted",
                request_id=request_id,
                headers={
                    "X-AI-Quota-Used": str(used),
                    "X-AI-Quota-Limit": str(limit),
                },
            )

    # 上游只认裸模型名：``provider/model`` 是我们自己的命名空间。``payload`` 是
    # 客户端原文，这里用一份浅拷贝改写，避免把合格 ID 发出去。
    upstream_payload = {**payload, "model": upstream_model}

    if bool(payload.get("stream")):
        return await _stream_completion(
            upstream_payload,
            model,
            candidates,
            user_oid,
            request_id,
            user.get("per"),
            purpose=purpose,
            audit_oid=audit_oid,
        )

    return await _blocking_completion(
        upstream_payload,
        model,
        candidates,
        user_oid,
        request_id,
        user.get("per"),
        purpose=purpose,
        audit_oid=audit_oid,
    )
