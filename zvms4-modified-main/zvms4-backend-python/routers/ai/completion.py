"""非流式上游调用与审计/扣费（``/v1/chat/completions`` 的实现层）。"""

from __future__ import annotations

import json
import logging
import time

from fastapi.responses import JSONResponse

from routers.ai.common import (
    RETRYABLE_CODES,
    USER_BANNED_CODE,
    USER_BANNED_MESSAGE,
    _audit_status,
    _billable,
    _charge,
    _elapsed_ms,
    _evaluate_burst,
    _openai_error,
    _reject,
    _reject_http,
    _usage_fields,
)
from services.ai import (
    audit,
    ban_state,
    credentials,
    model_rates,
    prompt_audit,
    prompt_cache,
    providers,
)
from services.ai import routing
from services.ai import usage as ai_usage
from services.ai.credentials import CredentialError
from services.ai.routing import RouteError

_logger = logging.getLogger(__name__)


def _answer_text(result: object) -> str | None:
    """上游响应里的第一条 assistant 正文（只为 ``AI_PROMPT_AUDIT.ANSWER_TEXT``）。"""
    if not isinstance(result, dict):
        return None
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return content if isinstance(content, str) and content else None


def _stream_answer_text(raw: bytes) -> str | None:
    """SSE 帧里的 content 增量拼成完整回答（只为留存，不参与响应）。

    解析失败一律跳过该帧：留存是旁路，不能因为上游吐了一帧坏 JSON 就把一次已经
    成功返回的响应变成 500。
    """
    parts: list[str] = []
    for line in raw.decode("utf-8", "replace").splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            frame = json.loads(payload)
        except ValueError:
            continue
        choices = frame.get("choices") if isinstance(frame, dict) else None
        if not isinstance(choices, list) or not choices:
            continue
        first = choices[0] if isinstance(choices[0], dict) else {}
        delta = first.get("delta") if isinstance(first.get("delta"), dict) else {}
        content = delta.get("content")
        if isinstance(content, str) and content:
            parts.append(content)
    return "".join(parts) or None


async def _blocking_completion(
    payload: dict,
    model: str,
    candidates: list[dict],
    user_oid: str,
    request_id: str,
    roles: list[str] | None = None,
    purpose: str | None = None,
    audit_oid: str | None = None,
) -> JSONResponse:
    started = time.monotonic()
    last_error: RouteError | None = None
    snapshot = audit.roles_snapshot(roles)

    for index, entry in enumerate(candidates, start=1):
        key = str(entry.get("key") or "")
        try:
            api_key = await credentials.resolve_api_key(entry)
        except CredentialError as exc:
            # 解不开是库里的数据坏了（换过主密钥 / 密文被改），换 provider 也救不了
            # 这一条，直接给结论。
            await routing.mark_failure(key)
            return await _reject(
                RouteError(exc.code, exc.message, exc.status_code),
                user_oid=user_oid,
                model=model,
                request_id=request_id,
                latency_ms=_elapsed_ms(started),
                key_scope=entry.get("scope"),
                provider=entry.get("name"),
                purpose=purpose,
                attempt_no=index,
                roles_snapshot=snapshot,
            )

        # 缓存注入只改「发给上游的 body」：``payload`` 还要用来算估算字符数、记
        # 审计，必须保持客户端原文。cache_key 是本进程算出来的审计值（NULL = 没注入）。
        body, cache_key = prompt_cache.request_body(payload, entry)
        try:
            async with routing.slot():
                result = await providers.chat_completion(body, entry, api_key)
        except RouteError as exc:
            # AI_BUSY 是我们自己的并发闸门，不是上游的错：算进熔断会把健康供应商
            # 也熔掉。
            if exc.code != "AI_BUSY":
                await routing.mark_failure(key)
            if exc.code not in RETRYABLE_CODES:
                return await _reject(
                    exc,
                    user_oid=user_oid,
                    model=model,
                    request_id=request_id,
                    latency_ms=_elapsed_ms(started),
                    key_scope=entry.get("scope"),
                    provider=entry.get("name"),
                    purpose=purpose,
                    attempt_no=index,
                    roles_snapshot=snapshot,
                )
            last_error = exc
            await audit.record(
                user_oid=user_oid,
                capability="chat",
                endpoint=audit.ENDPOINT_CHAT,
                provider=entry.get("name"),
                model=model,
                status=_audit_status(exc.code),
                http_status=exc.status_code,
                error_code=exc.code,
                latency_ms=_elapsed_ms(started),
                request_id=request_id,
                key_scope=entry.get("scope"),
                purpose=purpose,
                attempt_no=index,
                roles_snapshot=snapshot,
            )
            continue

        # 上游已经答完，但这个人可能在这几秒里被封了：内容一律丢弃，不回给被封
        # 账号（否则「封禁」只挡下一次请求，这一次的答案照样到手）。响应头还没发，
        # 这里能给一个干净的 403。上游本身是成功的，不能记成上游故障。
        if await ban_state.is_banned(user_oid):
            await routing.mark_success(key)
            _logger.warning(
                "chat completion discarded: user %s became banned", user_oid
            )
            await _reject_http(
                user_oid=user_oid,
                model=model,
                request_id=request_id,
                status_code=403,
                code=USER_BANNED_CODE,
                purpose=purpose,
                roles_snapshot=snapshot,
            )
            return _openai_error(
                403, USER_BANNED_CODE, USER_BANNED_MESSAGE, request_id=request_id
            )

        await routing.mark_success(key)
        # 上游回了 usage 就把 token 列一并落库：它们与 units 同源，报表才能分清
        # 「实扣」与「估算」（token 列全 NULL ⟺ units 是估算值）。AF = 千 token ×
        # 模型倍率；没拿到 usage 就按字符估算走同一公式，不再出现量纲分裂。
        tokens = _usage_fields(result.get("usage"))
        rate = await model_rates.resolve_rate(model)
        total_tokens = tokens["total_tokens"]
        if total_tokens is None:
            total_tokens = ai_usage.estimate_tokens_from_chars(
                ai_usage.prompt_chars(payload), len(_answer_text(result) or "")
            )
        units = ai_usage.af_units(total_tokens, rate)
        if _billable(entry):
            await _charge(user_oid, units, roles)
        await audit.record(
            user_oid=user_oid,
            capability="chat",
            endpoint=audit.ENDPOINT_CHAT,
            provider=entry.get("name"),
            model=model,
            status="success",
            http_status=200,
            latency_ms=_elapsed_ms(started),
            units=units,
            request_id=request_id,
            key_scope=entry.get("scope"),
            purpose=purpose,
            attempt_no=index,
            roles_snapshot=snapshot,
            cache_key=cache_key,
            **tokens,
        )
        # 请求完成处评估突发规则（流水已落库，计数才包含这一次）。
        await _evaluate_burst(user_oid)
        await prompt_audit.record_outcome(
            audit_oid, answer_text=_answer_text(result)
        )
        return JSONResponse(
            status_code=200,
            content=result,
            headers={"X-AI-Request-Id": request_id},
        )

    failure = last_error or RouteError(
        "AI_NO_PROVIDER", routing.NO_PROVIDER_MESSAGE, 503
    )
    return _openai_error(
        failure.status_code, failure.code, failure.message, request_id=request_id
    )
