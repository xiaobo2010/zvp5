"""流式上游调用：首帧探测、负缓存、SSE 包装与收尾审计。"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi.responses import JSONResponse, Response, StreamingResponse

from routers.ai.common import (
    BAN_CHECK_EVERY_CHUNKS,
    RETRYABLE_CODES,
    STREAM_CAPTURE_MAX_BYTES,
    STREAM_DONE_FRAME,
    STREAM_ERROR_FALLBACK,
    STREAM_ERROR_MESSAGES,
    USER_BANNED_CODE,
    USER_BANNED_MESSAGE,
    _billable,
    _charge,
    _elapsed_ms,
    _evaluate_burst,
    _mark_stream_unsupported,
    _openai_error,
    _reject,
    _reject_http,
    _audit_status,
    _sse_data_frame,
    _stream_error_frame,
    _stream_unsupported,
    _stream_unsupported_cached,
    _teardown_step,
    _usage_fields,
)
from routers.ai.completion import (
    _answer_text,
    _blocking_completion,
    _stream_answer_text,
)
from services.ai import (
    audit,
    ban_state,
    credentials,
    model_rates,
    prompt_audit,
    prompt_cache,
    providers,
    routing,
)
from services.ai import usage as ai_usage
from services.ai.credentials import CredentialError
from services.ai.routing import RouteError

_logger = logging.getLogger(__name__)


def _stream_failure_code(exc: BaseException) -> str:
    """异常 → 前端码表里有的错误码（不认识的失真成通用码）。

    只记码不记 message：上游 message 可能带正文，不能进日志/审计。
    """
    raw = getattr(exc, "code", None)
    if isinstance(raw, str) and raw in STREAM_ERROR_MESSAGES:
        return raw
    return STREAM_ERROR_FALLBACK


def _looks_like_sse(chunk: bytes) -> bool:
    """首块字节像不像 SSE（只在响应没带 ``Content-Type`` 时才用）。

    SSE 的块一定以字段名开头（``data:`` / ``event:`` / ``id:`` / ``retry:``，或
    注释行 ``:``）；而「200 + 普通 JSON」的首字节是 ``{``。空块按 SSE 处理：
    那只是上游还没吐内容，不该被猜成 JSON 去 ``json.loads``。
    """
    head = bytes(chunk or b"")[:16].decode("utf-8", "replace").lstrip().lower()
    if not head:
        return True
    return head.startswith(("data:", ":", "event:", "id:", "retry:"))


def _sse_stream_response(frames: list[bytes], request_id: str) -> StreamingResponse:
    """若干帧 + ``[DONE]`` 拼成一条 SSE 响应（响应头与正常流一致）。"""

    async def _body():
        for frame in frames:
            yield frame
        yield STREAM_DONE_FRAME

    return StreamingResponse(
        _body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-AI-Request-Id": request_id,
        },
    )


def _client_stream_response(response: JSONResponse, request_id: str) -> Response:
    """非流式的结果回给**要流式**的客户端：整包成一帧 SSE。

    客户端发了 ``stream: true``，解析器只认 SSE 帧。不包装的话它只会看到
    「200 + application/json」，前端把它当空流/截断处理——这与「回退非流式」的
    初衷正好相反。非 200 不包装：那时响应头还没发，应该给一个干净的错误状态码。
    """
    if response.status_code != 200:
        return response
    try:
        payload = json.loads(response.body)
    except (TypeError, ValueError):
        return response
    return _sse_stream_response([_sse_data_frame(payload)], request_id)


async def _stream_completion(
    payload: dict,
    model: str,
    candidates: list[dict],
    user_oid: str,
    request_id: str,
    roles: list[str] | None = None,
    purpose: str | None = None,
    audit_oid: str | None = None,
):
    """流式对话：**首字节前**还能改主意，首字节之后只能在流里报错。

    与非流式的关系（这一版修掉的两个坑）：

    * 以前只吃 ``candidates[0]``：第一个候选废了整条请求就废。现在逐候选尝试，
      首帧前失败交给 :func:`_blocking_completion` 按**既有**重试语义处理（同一
      个候选先回退非流式，可重试就换下一个候选）——不另写一套重试；
    * 以前上游 4xx 在 ``client.stream()`` 里抛出时响应头已经发出，只能写一帧
      error，前端看到的是「200 + 空回答」。现在先探首帧再决定响应形状：首帧没到
      = 还能回退非流式 / 给干净的 4xx-5xx；首帧到了才发 200 SSE。

    首帧**之后**的失败保持原状：写 error 帧收尾、不换候选、不回退。半句 A 半句 B
    比直接报错更难排查，也会把一次调用算成两行流水。
    """
    started = time.monotonic()
    snapshot = audit.roles_snapshot(roles)

    # 上游请求发出前最后看一眼封禁：响应头还没发，这里还能给一个干净的 403。
    # 入口闸只在请求开始时判过一次，而排队/换 provider 的这段时间足够落下一次封禁。
    if await ban_state.is_banned(user_oid):
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

    # 估算口径的 prompt 侧：客户端原文（与发给上游的 messages 同源）。完成侧不看
    # 字节数，只看解析出的正文（见 generate 里的 tracker.content_chars）。
    estimated_prompt_chars = ai_usage.prompt_chars(payload)
    # 回退非流式用的 body：``stream`` / ``stream_options`` 必须去掉，否则同一个
    # 上游会照着流式语义准备响应。
    blocking_payload = {
        field: value
        for field, value in payload.items()
        if field not in ("stream", "stream_options")
    }

    for index, entry in enumerate(candidates, start=1):
        name = str(entry.get("name") or "")
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
                provider=name,
                purpose=purpose,
                attempt_no=index,
                roles_snapshot=snapshot,
            )

        # 并发闸门必须在返回 StreamingResponse 之前拿到：进了生成器再报 429 时响应
        # 头已经发出去了，客户端只会看到一个空流。探首帧也在这把闸门里——探测同样
        # 占着一条上游连接，绕过去等于给并发上限开了个口子。
        guard = routing.slot()
        try:
            await guard.__aenter__()
        except RouteError as exc:
            # 同非流式：闸门满不是上游故障，不计入熔断。
            return await _reject(
                exc,
                user_oid=user_oid,
                model=model,
                request_id=request_id,
                latency_ms=_elapsed_ms(started),
                key_scope=entry.get("scope"),
                provider=name,
                purpose=purpose,
                attempt_no=index,
                roles_snapshot=snapshot,
            )

        # 手动进上下文管理器就没有兜底：每个出口都得自己还槽，漏一个就永久吃掉
        # 一格并发（漏满 8 次后全站 429，直到重启）。``released`` 让归还幂等——
        # 重入会多还一格，等于把并发上限悄悄放大。
        released = False

        async def _release_slot() -> None:
            nonlocal released
            if released:
                return
            released = True
            await guard.__aexit__(None, None, None)

        # 缓存注入只作用于发给上游的 body；``stream_options`` 由 providers 内部补，
        # 这里不重复补（两处都补会覆盖客户端自己的 stream_options 取值）。
        body, cache_key = prompt_cache.request_body(payload, entry)

        try:
            stream_unsupported = await _stream_unsupported_cached(key, model)
        except BaseException:
            # 负缓存查询也是 await 点：客户端在这里断开同样会跳过后面的还槽。
            await _release_slot()
            raise

        if stream_unsupported:
            # 负缓存命中：一小时内的第二次请求。这个上游明确不吃流式，别再拿一次
            # 必废的流式调用去试——直接以非流式起步（结果照样包成 SSE 帧）。
            await _release_slot()
            return _client_stream_response(
                await _blocking_completion(
                    blocking_payload,
                    model,
                    candidates[index - 1 :],
                    user_oid,
                    request_id,
                    roles,
                    purpose=purpose,
                    audit_oid=audit_oid,
                ),
                request_id,
            )

        # 探首帧：这一步拿到的异常都是「首字节还没出去」，所以还能改主意。
        # ``meta`` 是 providers 的回填窗口：状态码 / Content-Type / 错误体——判定
        # 「不支持流式」和「200 但不是 SSE」需要它们，字节流本身表达不了。
        meta: dict[str, Any] = {}
        stream_iter = providers.chat_completion_stream(
            body, entry, api_key, meta=meta
        )
        first: bytes | None = None
        try:
            first = await stream_iter.__anext__()
        except StopAsyncIteration:
            # 200 但一个字节都没有：按空流透传（与改造前一致），不猜原因。
            first = b""
            stream_iter = None
        except BaseException as exc:  # noqa: BLE001 - 取消也是 BaseException，也得还槽
            # 先还槽再判断：``CancelledError`` 不是 ``Exception``，以前从这里直接
            # 逃出、槽跟着丢。``__aexit__`` 里只有 ``semaphore.release()``，没有可
            # 挂起的 await，所以这一句本身不会被取消打断。
            await _release_slot()
            if not isinstance(exc, Exception):
                raise
            code = _stream_failure_code(exc)
            unsupported = _stream_unsupported(
                meta.get("status_code"), meta.get("error_body")
            )
            if unsupported:
                # 上游根本不支持流式：记住它（含 model），后续直接非流式起步。
                # 超时/连不上走不到这里（没有状态码），所以不会被误记。
                await _mark_stream_unsupported(key, model)
            if code != "AI_BUSY" and not unsupported:
                # 「不支持流式」不是上游故障（它非流式好好儿的），不计入熔断：
                # 否则三个模型各探一次就把它整家熔断 60 秒，非流式也跟着遭殃。
                await routing.mark_failure(key)
            if (
                unsupported
                or code in RETRYABLE_CODES
                or code == STREAM_ERROR_FALLBACK
            ):
                await audit.record(
                    user_oid=user_oid,
                    capability="chat",
                    endpoint=audit.ENDPOINT_CHAT,
                    provider=name,
                    model=model,
                    status=_audit_status(code),
                    http_status=(
                        exc.status_code if isinstance(exc, RouteError) else None
                    ),
                    error_code=code,
                    latency_ms=_elapsed_ms(started),
                    request_id=request_id,
                    key_scope=entry.get("scope"),
                    purpose=purpose,
                    attempt_no=index,
                    roles_snapshot=snapshot,
                )
                # 复用非流式的重试语义：同一个候选先改走非流式一次，仍失败且错误码
                # 可重试就换下一个候选（循环、审计、扣费全在 _blocking_completion
                # 里，这里不复制一份）。
                return _client_stream_response(
                    await _blocking_completion(
                        blocking_payload,
                        model,
                        candidates[index - 1 :],
                        user_oid,
                        request_id,
                        roles,
                        purpose=purpose,
                        audit_oid=audit_oid,
                    ),
                    request_id,
                )
            return await _reject(
                (
                    exc
                    if isinstance(exc, RouteError)
                    else RouteError(
                        STREAM_ERROR_FALLBACK,
                        STREAM_ERROR_MESSAGES[STREAM_ERROR_FALLBACK],
                        502,
                    )
                ),
                user_oid=user_oid,
                model=model,
                request_id=request_id,
                latency_ms=_elapsed_ms(started),
                key_scope=entry.get("scope"),
                provider=name,
                purpose=purpose,
                attempt_no=index,
                roles_snapshot=snapshot,
            )

        # 首帧到手。Content-Type 说不是 SSE（或没说）时按形状判断：200 + JSON 是
        # 「上游把 stream 开关当没看见」，不是错误。
        content_type = str(meta.get("content_type") or "").lower()
        if "text/event-stream" in content_type:
            json_body = False
        elif content_type:
            json_body = True
        else:
            json_body = not _looks_like_sse(first)

        # 流式默认拿不到 usage（上游要 ``stream_options.include_usage`` 才在最后一帧
        # 报），所以先留一条估算路径；真正有没有 usage 由 StreamUsageTracker 决定。
        # 估算与实测的边界就是「token 列是否为 NULL」，见 services/ai/usage.py。
        tracker = ai_usage.StreamUsageTracker()
        state: dict[str, Any] = {
            # 完成侧字符数：只数解析出的正文，不数 SSE 原始字节（见 usage.py）。
            "chars": 0,
            "status": "success",
            "error_code": None,
            # 「首块到达」而不是「首字生成」：客户端正是在这一刻开始看到内容。
            "first_token_ms": _elapsed_ms(started) if first else None,
            # 回答正文只为一件事被留下来：``AI_PROMPT_AUDIT.ANSWER_TEXT``（合规审计）。
            # 有上限：流式理论上可以吐很久，留存区分不出「长回答」与「上游失控」。
            "answer": bytearray(),
        }

        async def _upstream_chunks():
            """上游字节流；首帧已经在探测阶段拿在手上，这里先补发它。"""
            if first:
                yield first
            if stream_iter is None:
                return
            async for piece in stream_iter:
                yield piece

        async def generate():
            chunks = 0
            json_result: dict[str, Any] | None = None
            try:
                if json_body:
                    # 200 但不是 SSE：把整个 body 当普通 JSON 读出来，包成一帧回给
                    # 客户端（它要的是流，前端只认 SSE 帧）。
                    raw = bytearray(first or b"")
                    if stream_iter is not None:
                        async for piece in stream_iter:
                            raw.extend(piece)
                    parsed = json.loads(bytes(raw).decode("utf-8", "replace"))
                    if not isinstance(parsed, dict):
                        raise RouteError(
                            STREAM_ERROR_FALLBACK,
                            STREAM_ERROR_MESSAGES[STREAM_ERROR_FALLBACK],
                            502,
                        )
                    json_result = parsed
                    state["chars"] = len(_answer_text(parsed) or "")
                    yield _sse_data_frame(parsed)
                    # 客户端按 SSE 收流：帧之后必须补一个 [DONE]，否则它的解析器
                    # 会一直等下一帧，界面上就是「回答完但没结束」。
                    yield STREAM_DONE_FRAME
                else:
                    async for chunk in _upstream_chunks():
                        # 计费估算只看正文增量：tracker 内部已经按帧解析过一遍，
                        # 这里只是把「已解析出的正文字符数」抄进 state。
                        tracker.feed(chunk)
                        state["chars"] = tracker.content_chars
                        if state["first_token_ms"] is None and chunk:
                            # 探测阶段的首块是空的（上游握手后先吐了个空块）：
                            # 真正的「首块到达」在这里。
                            state["first_token_ms"] = _elapsed_ms(started)
                        if audit_oid and len(state["answer"]) < STREAM_CAPTURE_MAX_BYTES:
                            state["answer"].extend(
                                chunk[: STREAM_CAPTURE_MAX_BYTES - len(state["answer"])]
                            )
                        yield chunk
                        chunks += 1
                        if (
                            chunks % BAN_CHECK_EVERY_CHUNKS == 0
                            and await ban_state.is_banned(user_oid)
                        ):
                            # 已经吐出去的正文只能留在客户端，但**不能继续生成**：
                            # 多吐的每一句都是被封账号拿到的内容。error 帧 + [DONE]
                            # 让客户端知道是中止，而不是以为模型说完了。
                            state["status"] = _audit_status(USER_BANNED_CODE)
                            state["error_code"] = USER_BANNED_CODE
                            _logger.warning(
                                "ai stream aborted mid-flight: user %s became banned",
                                user_oid,
                            )
                            # 上游本身是成功的（内容与连接都正常），不能记成上游故障。
                            await routing.mark_success(key)
                            yield _stream_error_frame(
                                USER_BANNED_CODE, USER_BANNED_MESSAGE
                            )
                            yield STREAM_DONE_FRAME
                            return
            except Exception as exc:  # noqa: BLE001 - 已开始输出，只能收尾
                # 走到这里说明响应头（200 + text/event-stream）早就发出去了，状态码
                # 改不了。以前只写一条 warning 就结束生成器，客户端拿到的是
                # 「200 + 空 body」——前端只能猜成「被截断」。现在把失败作为一帧
                # 显式写进流里，已发过的正文保留，后面跟 error 帧 + [DONE] 收尾。
                code = _stream_failure_code(exc)
                state["status"] = _audit_status(code)
                state["error_code"] = code
                await routing.mark_failure(key)
                # 只记异常类名与码：异常 message 可能带上游原文，不能进日志。
                _logger.warning(
                    "ai stream failed on %s: %s (%s)", name, type(exc).__name__, code
                )
                yield _stream_error_frame(code, STREAM_ERROR_MESSAGES[code])
                yield STREAM_DONE_FRAME
            else:
                await routing.mark_success(key)
            finally:
                # 槽必须在任何可被取消的 await **之前**还：客户端中途断开时 anyio
                # 会把 CancelledError 注入这个 finally，之后第一个 await 就再抛一次，
                # 排在它后面的收尾全部跳过——以前正是这样漏槽 + 丢审计/扣费的。
                await _release_slot()
                # 客户端断开/中途返回时也要把上游连接关掉（httpx 的 stream 上下文
                # 由生成器的 aclose 驱动）。
                if stream_iter is not None:
                    await _teardown_step("upstream-close", stream_iter.aclose())
                # 最后一帧可能没有空行收尾（上游在 [DONE] 后直接关连接），收尾解析一次。
                tracker.close()
                if json_body:
                    # 非 SSE 的那一跳：usage 直接来自响应体的 ``usage``。
                    tokens = _usage_fields(
                        json_result.get("usage") if json_result else None
                    )
                else:
                    tokens = {
                        "prompt_tokens": tracker.prompt_tokens,
                        "completion_tokens": tracker.completion_tokens,
                        "total_tokens": tracker.total_tokens,
                        "cache_read_tokens": tracker.cache_read_tokens,
                        "cache_write_tokens": tracker.cache_write_tokens,
                    }
                rate = await model_rates.resolve_rate(model)
                if ai_usage.has_tokens(tokens):
                    units = ai_usage.af_units(tokens["total_tokens"], rate)
                elif state["status"] != "success" and state["chars"] == 0:
                    # 完全没产出（没 usage 且正文 0 字符）的失败不扣额度：上游没
                    # 交付可计费内容。审计行照写（units=0，token 列仍为 NULL）。
                    units = 0
                else:
                    # 没有 usage：token 列留 NULL，units 按字符估算后走**同一个** AF
                    # 公式。这是「NULL ⟺ 估算」不变式的另一半。中途失败但已产出
                    # 正文的仍按估算收费：上游成本已经发生。
                    units = ai_usage.estimate_af(
                        estimated_prompt_chars, state["chars"], rate
                    )
                # 每一步单独兜住：一步失败（或断连后只剩同步机会）不能把后面的
                # 审计/扣费一起带走；槽已经在上面还过了，这里失败不会造成泄漏。
                await _teardown_step(
                    "audit",
                    audit.record(
                        user_oid=user_oid,
                        capability="chat",
                        endpoint=audit.ENDPOINT_CHAT,
                        provider=name,
                        model=model,
                        status=state["status"],
                        http_status=200 if state["status"] == "success" else None,
                        error_code=state["error_code"],
                        latency_ms=_elapsed_ms(started),
                        units=units,
                        request_id=request_id,
                        key_scope=entry.get("scope"),
                        purpose=purpose,
                        attempt_no=index,
                        first_token_ms=state["first_token_ms"],
                        roles_snapshot=snapshot,
                        cache_key=cache_key,
                        **tokens,
                    ),
                )
                if _billable(entry):
                    await _teardown_step(
                        "charge", _charge(user_oid, units, roles)
                    )
                # 流式请求的「完成」就在生成器收尾：此时流水已落库，计数包含本次。
                await _teardown_step("burst", _evaluate_burst(user_oid))
                await _teardown_step(
                    "prompt-outcome",
                    prompt_audit.record_outcome(
                        audit_oid,
                        answer_text=(
                            _answer_text(json_result)
                            if json_body
                            else _stream_answer_text(bytes(state["answer"]))
                        ),
                    ),
                )

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-AI-Request-Id": request_id,
            },
        )

    # 走到这里说明候选全都在解凭据/并发闸门阶段就没了（正常不会：上面每条路径
    # 都会 return）。保留一个兜底，别让函数隐式返回 None。
    return _openai_error(
        503, "AI_NO_PROVIDER", routing.NO_PROVIDER_MESSAGE, request_id=request_id
    )
