"""OpenAI 兼容上游调用（httpx，全异步）。

单 worker + 共享事件循环，所以这里没有任何同步阻塞调用；``AsyncClient`` 模块级懒建、
复用连接池，``aclose()`` 供测试与进程退出时关闭。

两条硬规则：
* ``trust_env=False`` —— 不吃 ``http_proxy`` 之类环境变量，出口必须可预期；
* ``follow_redirects=False`` —— 重定向会把 ``Authorization`` 带去别的主机。

返回给前端的 message 里不出现上游 URL、报文、密钥：这些只进日志（截断 500 字符）。

上游地址来自调用方传入的 entry（可能是个人/共享/服务端任一层），本模块不再读
settings：凭据目录已经决定「这次该打谁」，这里只负责把请求发出去。
"""

from __future__ import annotations

import logging
from typing import AsyncIterator

import httpx

from .routing import RouteError

_logger = logging.getLogger(__name__)

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 120.0
BODY_LOG_LIMIT = 500
MAX_CONNECTIONS = 32
MAX_KEEPALIVE_CONNECTIONS = 16

_client: httpx.AsyncClient | None = None


def _client_instance() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=MAX_CONNECTIONS,
                max_keepalive_connections=MAX_KEEPALIVE_CONNECTIONS,
            ),
        )
    return _client


async def aclose() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


def _timeout(entry: dict) -> httpx.Timeout:
    raw = entry.get("timeout") or {}
    if not isinstance(raw, dict):
        raw = {}
    try:
        read = float(raw.get("read", READ_TIMEOUT))
        connect = float(raw.get("connect", CONNECT_TIMEOUT))
    except (TypeError, ValueError):
        read, connect = READ_TIMEOUT, CONNECT_TIMEOUT
    return httpx.Timeout(read, connect=connect)


def _endpoint(entry: dict) -> str:
    """``api_path`` 是相对 base_url 的完整路径（非 OpenAI 形状的服务端要用）。

    空/非字符串一律走默认路径：存量 provider 的 ``api_path`` 是 NULL，语义必须
    与加这一列之前完全一致。
    """
    base = str(entry.get("base_url") or "").rstrip("/")
    path = entry.get("api_path")
    if isinstance(path, str) and path:
        return base + path
    return base + "/chat/completions"


def _headers(entry: dict, api_key: str) -> dict[str, str]:
    """key 由调用方在发请求前才解密出来，本模块不碰库、也不缓存它。

    ``entry["headers"]`` 是额外/覆盖头（例如 ``X-Api-Key`` 代替 Bearer）：同名
    （大小写不敏感，HTTP 头即如此）覆盖默认值，新键合并进来。值可能含 token，
    所以本函数不记录任何日志，异常 message 也只说「credentials are missing」。
    """
    if not api_key:
        # 正常路径上 resolve_api_key() 已经挡掉；这里是防御，且 message 不含 key。
        raise RouteError("AI_UPSTREAM_AUTH", "upstream credentials are missing", 502)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    extra = entry.get("headers")
    if not isinstance(extra, dict):
        return headers
    for key, value in extra.items():
        if not isinstance(key, str) or not isinstance(value, str) or not key:
            continue
        if "\r" in key or "\n" in key or "\r" in value or "\n" in value:
            # 服务层已校验；这里再挡一次，避免脏数据走到 httpx 抛异常。
            continue
        lowered = key.lower()
        for existing in [k for k in headers if k.lower() == lowered]:
            del headers[existing]
        headers[key] = value
    return headers


def _upstream_error(name: str, status_code: int, body: str) -> RouteError:
    _logger.warning(
        "ai upstream %s returned %s: %s", name, status_code, body[:BODY_LOG_LIMIT]
    )
    if status_code in (401, 403):
        # 鉴权失败换 provider 也没意义：同一把 key 到哪都是错。
        return RouteError("AI_UPSTREAM_AUTH", "upstream rejected the credentials", 502)
    if status_code == 429:
        return RouteError(
            "AI_UPSTREAM_RATE_LIMIT", "upstream rate limit reached", 502
        )
    if status_code >= 500:
        return RouteError("AI_UPSTREAM_ERROR", "upstream failed", 502)
    return RouteError("AI_UPSTREAM_REJECTED", "upstream rejected the request", 502)


async def chat_completion(payload: dict, entry: dict, api_key: str) -> dict:
    client = _client_instance()
    try:
        response = await client.post(
            _endpoint(entry),
            json=payload,
            headers=_headers(entry, api_key),
            timeout=_timeout(entry),
        )
    except httpx.TimeoutException:
        raise RouteError("AI_TIMEOUT", "upstream timed out", 504) from None
    except httpx.HTTPError:
        raise RouteError("AI_UPSTREAM_ERROR", "upstream is unreachable", 502) from None

    if response.status_code < 200 or response.status_code >= 300:
        raise _upstream_error(
            str(entry.get("name") or ""), response.status_code, response.text
        )

    try:
        return response.json()
    except ValueError:
        raise RouteError(
            "AI_UPSTREAM_ERROR", "upstream returned malformed json", 502
        ) from None


async def chat_completion_stream(
    payload: dict, entry: dict, api_key: str
) -> AsyncIterator[bytes]:
    client = _client_instance()
    try:
        async with client.stream(
            "POST",
            _endpoint(entry),
            json=payload,
            headers=_headers(entry, api_key),
            timeout=_timeout(entry),
        ) as response:
            if response.status_code < 200 or response.status_code >= 300:
                # 流式响应的错误体不在 status 里，必须先读出来才能判定/记日志。
                body = await response.aread()
                raise _upstream_error(
                    str(entry.get("name") or ""),
                    response.status_code,
                    body.decode("utf-8", "replace"),
                )
            async for chunk in response.aiter_raw():
                yield chunk
    except httpx.TimeoutException:
        raise RouteError("AI_TIMEOUT", "upstream timed out", 504) from None
    except httpx.HTTPError:
        raise RouteError("AI_UPSTREAM_ERROR", "upstream is unreachable", 502) from None
