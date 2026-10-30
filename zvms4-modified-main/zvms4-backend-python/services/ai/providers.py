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
from typing import AsyncIterator, Mapping

import httpx

from . import config as ai_config
from . import credentials, prompt_cache
from .routing import RouteError

_logger = logging.getLogger(__name__)

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 120.0
BODY_LOG_LIMIT = 500
# 交给路由层判定「不支持流式」的错误体上限。错误体可能回显请求里的 prompt，
# 所以只留够判定的一小段（短于它的 body 原样保留）。
STREAM_META_BODY_LIMIT = 512
MAX_CONNECTIONS = 32
MAX_KEEPALIVE_CONNECTIONS = 16

#: 上游模型列表路径。与 ``_endpoint`` 的 ``/chat/completions`` 同源：只拼
#: ``base_url``，**不**吃 ``api_path``——后者是聊天的完整路径，拿来当列表路径会
#: 得到 ``/v1/messages/models`` 这种不存在的地址。
MODEL_LIST_PATH = "/models"

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


def _probe_timeout() -> httpx.Timeout:
    """``/models`` 探测的超时，与保存路径共用 ``AI_MODEL_PROBE_TIMEOUT_SECONDS``。

    ``<= 0`` 是运维的「关掉探测」开关（离线环境/单测）：显式拉取同样遵守它，而不是
    偷偷用默认值把请求发出去——这条路径会解密 key，开关必须真的有效。
    """
    value = credentials.probe_timeout()
    if value <= 0:
        raise RouteError(
            "AI_UPSTREAM_ERROR", "model probe is disabled by configuration", 502
        )
    return httpx.Timeout(value, connect=min(CONNECT_TIMEOUT, value))


def context_window(row: object) -> int | None:
    """``/models`` 的一个条目 → 上下文窗口（token）；认不出就是 ``None``（不猜）。

    字段名各家不同，按 :data:`services.ai.credentials.CONTEXT_KEYS` 的顺序取第一个
    能解析成正整数的。正整数口径与 ``MODELS_JSON`` 的解析共用一份实现。
    """
    if not isinstance(row, Mapping):
        return None
    for key in credentials.CONTEXT_KEYS:
        value = credentials.positive_int(row.get(key))
        if value is not None:
            return value
    return None


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


def _host(entry: dict) -> str:
    """``base_url`` 的 host（小写）；解析失败返回空串。

    实现委托给 ``services.ai.prompt_cache.host_of``：缓存注入的门控与这里的
    ``stream_options`` 门控判断的是同一件事，两处各写一份迟早会漂。
    """
    return prompt_cache.host_of(entry)


def wants_stream_usage(entry: dict) -> bool:
    """这个上游能不能带 ``stream_options.include_usage``。

    **宁缺勿坏**：这个参数不在最早的 OpenAI 兼容面里，严格校验请求体的上游会
    直接 400，那等于「为了多拿一列 token 把流式对话打挂」。所以只在 host 命中
    白名单（``services.ai.config.stream_usage_hosts``）时才带；拿不准的一律不带，
    代价只是该行的 token 列留 NULL、额度走估算。

    不入库、不改 entry，纯函数，便于离线断言门控。
    """
    host = _host(entry)
    if not host:
        return False
    allowed = ai_config.stream_usage_hosts()
    return "*" in allowed or host in allowed


def stream_request_body(payload: dict, entry: dict) -> dict:
    """发给上游的流式请求体：门控通过时补 ``stream_options.include_usage``。

    只补 ``stream_options.include_usage``，不动调用方给的其它字段（包括客户端
    自己传的 ``stream_options`` 里我们不认识的键——原样保留）。返回新 dict，
    不修改入参：调用方还要用原始 ``messages`` 估算字符数。
    """
    if not isinstance(payload, dict):
        return payload
    if not wants_stream_usage(entry):
        return payload
    body = dict(payload)
    existing = body.get("stream_options")
    options = dict(existing) if isinstance(existing, dict) else {}
    options["include_usage"] = True
    body["stream_options"] = options
    return body


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
        # 上游默认会按 httpx 的 ``Accept-Encoding`` 压成 gzip/br；SSE 要逐帧透传，
        # 压缩体在客户端那边就是一堆二进制垃圾。要求 identity，别让它压。
        "Accept-Encoding": "identity",
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


def _status_error(status_code: int) -> RouteError:
    """HTTP 状态码 → 带语义的 ``RouteError``（聊天与 ``/models`` 共用一份映射）。"""
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


def _upstream_error(name: str, status_code: int, body: str) -> RouteError:
    _logger.warning(
        "ai upstream %s returned %s: %s", name, status_code, body[:BODY_LOG_LIMIT]
    )
    return _status_error(status_code)


async def guard_endpoint(entry: dict) -> None:
    """发请求前复核 ``base_url`` 当前解析到哪，挡住「录入时公网、请求时内网」。

    录入侧的校验（``credentials._clean_base_url``）只看得到**写入那一刻**的 DNS；
    之后把域名解析改成内网地址（DNS rebinding）就能让网关去打
    ``169.254.169.254`` / ``127.0.0.1``。这里在每次上游调用前用同一套私网黑名单
    再看一眼，判定带 60 秒缓存。

    两条**不做**复核的快路径，都是为了不给正常请求加延迟：

    * ``server`` 作用域 —— ``settings.AI_PROVIDERS`` 只由运维改，且可能是内网自建
      网关（vLLM / LiteLLM），按「运维自己的配置」对待；
    * host 命中 :func:`services.ai.config.upstream_host_allowlist`（官方域名）。

    只记 host，不记完整 URL/上游报文：路径与 query 都可能带凭据。
    """
    if not isinstance(entry, dict):
        return
    if entry.get("scope") == credentials.SCOPE_SERVER:
        return
    host = _host(entry)
    if not host:
        return
    allowed = ai_config.upstream_host_allowlist()
    if "*" in allowed or host in allowed:
        return
    if await credentials.host_is_private(host):
        _logger.warning("ai upstream %s blocked: resolves to a private address", host)
        raise RouteError(
            "AI_UPSTREAM_UNREACHABLE", "upstream endpoint is not reachable", 502
        )


async def list_models(entry: dict, api_key: str | None = None) -> list[dict]:
    """问一次上游 ``/models``，回 ``[{"id": str, "context": int | None}]``。

    为什么和聊天走同一个模块级 client：连接池、``trust_env=False``、
    ``follow_redirects=False`` 这三条硬规则只该有一份实现；一次探测多建一个
    ``AsyncClient`` 等于把「凭据会不会跟着重定向发去别的主机」重新赌一遍。

    ``base_url`` 与聊天共用同一个录入校验，但录入之后 DNS 仍可能被改成内网地址
    （DNS rebinding），所以发请求前**必须**走 :func:`guard_endpoint`——server
    作用域与官方 host 白名单两条快路径由它自己判，这里不另立规则。

    失败抛 :class:`RouteError`（本模块既有的上游异常类型）：调用方可以把它当
    「没探到」吞掉（保存路径），也可以翻成 502（管理端显式拉取）。日志只记 host，
    不记 URL/报文/密钥——``base_url`` 的路径段可能是带凭据的网关前缀。
    """
    key = api_key if api_key is not None else str(entry.get("api_key") or "")
    base = str(entry.get("base_url") or "").strip().rstrip("/")
    if not base or not key:
        raise RouteError("AI_UPSTREAM_ERROR", "upstream endpoint is not configured", 502)

    await guard_endpoint(entry)
    client = _client_instance()
    try:
        response = await client.get(
            base + MODEL_LIST_PATH,
            headers=_headers(entry, key),
            timeout=_probe_timeout(),
        )
    except httpx.TimeoutException:
        _logger.warning("ai models probe %s timed out", _host(entry) or "-")
        raise RouteError("AI_TIMEOUT", "upstream timed out", 504) from None
    except httpx.InvalidURL:
        # ``InvalidURL`` 不是 ``HTTPError`` 的子类；漏掉它会让坏 base_url 变成 500。
        # 异常文本里带着完整 URL，所以只记 host。
        _logger.warning("ai models probe %s has an invalid url", _host(entry) or "-")
        raise RouteError(
            "AI_UPSTREAM_ERROR", "upstream endpoint is invalid", 502
        ) from None
    except httpx.HTTPError:
        _logger.warning("ai models probe %s is unreachable", _host(entry) or "-")
        raise RouteError("AI_UPSTREAM_ERROR", "upstream is unreachable", 502) from None

    if response.status_code < 200 or response.status_code >= 300:
        # 不走 ``_upstream_error``：那条路径会把响应体写进日志，而 ``/models`` 的
        # 报错体可能回显整段请求头。这里只留状态码与 host。
        _logger.warning(
            "ai models probe %s returned %s",
            _host(entry) or "-",
            response.status_code,
        )
        raise _status_error(response.status_code)

    try:
        payload = response.json()
    except ValueError:
        raise RouteError(
            "AI_UPSTREAM_ERROR", "upstream returned malformed json", 502
        ) from None

    rows = payload.get("data") if isinstance(payload, Mapping) else payload
    if not isinstance(rows, list):
        return []
    models: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        model = str(row.get("id") or "").strip()
        if not model or model in seen:
            continue
        seen.add(model)
        models.append({"id": model, "context": context_window(row)})
    return models


async def chat_completion(payload: dict, entry: dict, api_key: str) -> dict:
    await guard_endpoint(entry)
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


def _header(response: object, name: str) -> str:
    """响应头读成字符串；桩对象没有 ``headers`` 时给空串（不猜）。"""
    headers = getattr(response, "headers", None)
    if headers is None:
        return ""
    try:
        return str(headers.get(name) or "")
    except Exception:  # noqa: BLE001 - 头部形状异常不该让一次成功调用变 500
        return ""


async def chat_completion_stream(
    payload: dict,
    entry: dict,
    api_key: str,
    *,
    meta: dict | None = None,
) -> AsyncIterator[bytes]:
    """把上游 SSE 字节逐段透传。

    ``meta`` 是**调用方观察窗口**（可选）：本函数往里面填 ``status_code`` /
    ``content_type``，非 2xx 时再填 ``error_body``（截断后的响应体）。路由层靠它
    判定「上游不支持流式」（需要状态码 + 错误体）和「200 但不是 SSE」（需要
    Content-Type）——这两件事字节流本身表达不了。默认 ``None``：老调用点一行不改，
    也不多读一个字节。
    """
    await guard_endpoint(entry)
    client = _client_instance()
    try:
        async with client.stream(
            "POST",
            _endpoint(entry),
            json=stream_request_body(payload, entry),
            headers=_headers(entry, api_key),
            timeout=_timeout(entry),
        ) as response:
            if meta is not None:
                meta["status_code"] = response.status_code
                meta["content_type"] = _header(response, "content-type")
            if response.status_code < 200 or response.status_code >= 300:
                # 流式响应的错误体不在 status 里，必须先读出来才能判定/记日志。
                body = await response.aread()
                text = body.decode("utf-8", "replace")
                if meta is not None:
                    # 只留判定「不支持流式」用得上的那一小段：错误体可能回显请求
                    # 里的 prompt，不该跟着进审计或日志。
                    meta["error_body"] = text[:STREAM_META_BODY_LIMIT]
                raise _upstream_error(
                    str(entry.get("name") or ""),
                    response.status_code,
                    text,
                )
            # 双保险：就算上游无视 ``Accept-Encoding: identity`` 仍然压了，
            # ``aiter_bytes()`` 会按 Content-Encoding 解压后再给我们；``aiter_raw()``
            # 原样吐压缩字节，转发出去浏览器按 UTF-8 解析只有乱码/空白。
            async for chunk in response.aiter_bytes():
                yield chunk
    except httpx.TimeoutException:
        raise RouteError("AI_TIMEOUT", "upstream timed out", 504) from None
    except httpx.HTTPError:
        raise RouteError("AI_UPSTREAM_ERROR", "upstream is unreachable", 502) from None
