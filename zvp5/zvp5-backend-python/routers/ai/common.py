"""``/api/ai`` 的共享协议层：常量、错误体、审计落行、场景头、流式负缓存。

从 ``routers/ai_router.py`` 原样搬出。各端点域子模块只 import 这里，不再
彼此横向依赖；``routers/ai_router.py`` 继续 re-export 这些符号，保证旧的
导入点（``routers.ai_models_router`` 等）与测试不被打断。
"""

from __future__ import annotations

import json
import logging
import re
import time

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from db import state
from services import risk_monitor
from services.ai import api_keys, audit, credentials, quota, rate_limit
from services.ai import config as ai_config
from services.ai import usage as ai_usage
from services.ai.credentials import CredentialError
from services.ai.routing import RouteError
from util.object_id import _authenticate, _log_level_scope, oauth2_scheme
from util.permission.features import ensure_feature

_logger = logging.getLogger(__name__)


MAX_PROVIDER_ATTEMPTS = 2
RETRYABLE_CODES = frozenset({"AI_UPSTREAM_RATE_LIMIT", "AI_UPSTREAM_ERROR", "AI_TIMEOUT"})
REJECTED_CODES = frozenset(
    {
        "AI_NO_PROVIDER",
        "AI_UPSTREAM_AUTH",
        "AI_UPSTREAM_REJECTED",
        "AI_UPSTREAM_UNREACHABLE",
    }
)
PERSONAL_SCOPE = credentials.SCOPE_PERSONAL

# 场景来源：前端在 AI 调用封装里带一个 ``X-AI-Scene`` 头（`chat` /
# `markdown-assistant`），OCR 固定 `ocr`，第三方 SDK 不带 → NULL（未知）。
# 它是**元数据**，不参与任何权限/额度判定：拿不到就留空，不该因此拒请求。
SCENE_HEADER = "X-AI-Scene"
SCENE_OCR = "ocr"
SCENE_MAX_LEN = 24  # 与 AI_CALL_LOGS.PURPOSE 的 VARCHAR2(24) 对齐
_SCENE_STRIP = re.compile(r"[^a-z0-9_-]+")

# 流式响应头一旦发出就改不了状态码（上游 4xx/5xx 往往在首个字节前才抛），
# 只能把失败写进流里。这里是 code → 中文人话的映射：key 必须与前端
# ``src/api/ai/errors.ts`` 的码表对齐，否则客户端只会显示通用错误。
STREAM_ERROR_MESSAGES = {
    "AI_TIMEOUT": "上游模型响应超时，请稍后重试。",
    "AI_UPSTREAM_AUTH": "上游模型拒绝了凭据，请联系管理员检查 key。",
    "AI_UPSTREAM_RATE_LIMIT": "上游模型限流，请稍后重试。",
    "AI_UPSTREAM_REJECTED": "上游模型拒绝了本次请求，请检查模型与参数。",
    "AI_UPSTREAM_ERROR": "上游模型暂时不可用，请稍后重试。",
    "AI_UPSTREAM_UNREACHABLE": "上游地址不可用，请联系管理员检查供应商配置。",
}
STREAM_ERROR_FALLBACK = "AI_UPSTREAM_ERROR"
STREAM_DONE_FRAME = b"data: [DONE]\n\n"

# 执行期封禁的对外错误码：字面量与 ``util.ban`` 的 detail 一致（前端按它分流到
# 「账号封禁」页）。403 用在「响应尚未开始」的两个位置（流式首帧前、非流式收稿
# 后），已经吐过正文的流只能在流内补一帧同码错误——见 ``_stream_completion``。
USER_BANNED_CODE = "USER_BANNED"
USER_BANNED_MESSAGE = "该账号当前处于封禁状态，暂时无法使用 AI 功能。"

# 流式生成器里查封禁的间隔（chunk 数）。上游一次 yield 是一段 SSE 字节，通常
# 几十字节；32 个 chunk 约等于「用户已经看到一两句话」，而查询被 TTL 缓存兜在
# 每秒一次以内。取不到这个粒度就意味着「封了还继续吐整篇回答」。
BAN_CHECK_EVERY_CHUNKS = 32

# 流式回答留存的上限：超过它只丢尾部（正文早已发给客户端，留存只是审计）。
STREAM_CAPTURE_MAX_BYTES = 64 * 1024


def _stream_error_frame(code: str, message: str) -> bytes:
    """一帧 ``data: {"error": {...}}``：流内报错的唯一通道（形状对齐 OpenAI 面）。"""
    return (
        "data: "
        + json.dumps(
            {"error": {"code": code, "message": message}}, ensure_ascii=False
        )
        + "\n\n"
    ).encode("utf-8")


def _sse_data_frame(payload: object) -> bytes:
    """把一个完整响应体包成一帧 SSE（``data: <json>\\n\\n``）。"""
    body = json.dumps(payload, ensure_ascii=False)
    return ("data: " + body + "\n\n").encode("utf-8")


# ---------------------------------------------------------------------------
# 「上游不支持流式」的判定与进程内负缓存
# ---------------------------------------------------------------------------
#
# 有些 OpenAI 兼容面根本不实现 ``stream``（或者只在某个网关后面被吞掉），表现是
# 一个 4xx 而不是「流里报错」。这种上游**每次**都会先把流式请求打废，然后再走
# 非流式才能成功——不记住它，就等于每次对话都白花一次上游调用。
#
# 负缓存是进程内的（与 ``routing`` 的熔断同一个前提：单 worker），键是
# ``(credential_key, model)``：同一把 key 下不同模型的支持情况可能不同（网关把
# 不同模型转给不同后端）。TTL 一小时，宁可过期后重探一次，也不把「临时故障」
# 永久记成「不支持」——所以**超时/连不上不写缓存**（那时没有状态码，判不出来）。

STREAM_UNSUPPORTED_TTL_SECONDS = 3600.0

#: 只有这几个状态码才可能是「不支持流式」：405 本来就是 Method Not Allowed；
#: 400/404/422 需要错误体里出现 stream 相关字样（见 :func:`_stream_unsupported`）。
STREAM_UNSUPPORTED_STATUSES = frozenset({400, 404, 405, 422})

#: 错误体判定的关键词。上游的措辞各式各样（``stream is not supported`` /
#: ``unsupported parameter: stream`` / ``does not support streaming``），只认这三个
#: 词根，大小写不敏感。
_STREAM_UNSUPPORTED_HINT = re.compile(r"stream|unsupported|not support", re.IGNORECASE)

# 错误体只留这么长做判定：错误体可能回显 prompt，不该被长期挂在内存里。
STREAM_UNSUPPORTED_BODY_MAX = 512

_stream_unsupported_cache: dict[tuple[str, str], float] = {}


def _stream_clock() -> float:
    """负缓存的时钟；单独一个函数是为了测试能 monkeypatch 过期。"""
    return time.monotonic()


def _stream_unsupported(status: object, body: object) -> bool:
    """「上游不支持流式」的纯判定：状态码 + 错误体 → bool。

    * ``405``：直接算（Method Not Allowed 就是「这个端点不接流式」）；
    * ``400 / 404 / 422``：错误体必须命中 ``stream|unsupported|not support``
      （大小写不敏感）。光看状态码不行——400 更可能是参数写错，把它记成「不支持
      流式」会让这个模型此后一律非流式；
    * 其余（含超时没有状态码）一律 ``False``。
    """
    if not isinstance(status, int) or isinstance(status, bool):
        return False
    if status == 405:
        return True
    if status not in STREAM_UNSUPPORTED_STATUSES:
        return False
    text = body if isinstance(body, str) else ""
    return bool(_STREAM_UNSUPPORTED_HINT.search(text[:STREAM_UNSUPPORTED_BODY_MAX]))


async def _stream_unsupported_cached(key: str, model: str) -> bool:
    """这个 ``(key, model)`` 还在负缓存里吗（Redis 共享，进程内兜底）。"""
    if state.redis_configured():
        cached = await state.kv_get(f"ai:stream:{key}:{model}")
        if cached is not None:
            return bool(cached)
    return _local_stream_unsupported_cached(key, model)


def _local_stream_unsupported_cached(key: str, model: str) -> bool:
    cache_key = (key, model)
    expiry = _stream_unsupported_cache.get(cache_key)
    if expiry is None:
        return False
    if expiry <= _stream_clock():
        _stream_unsupported_cache.pop(cache_key, None)
        return False
    return True


async def _mark_stream_unsupported(key: str, model: str) -> None:
    """记下「这个 provider 的这个模型不吃流式」，TTL 见常量。"""
    if state.redis_configured():
        if await state.kv_set(
            f"ai:stream:{key}:{model}", True, STREAM_UNSUPPORTED_TTL_SECONDS
        ):
            return
    _stream_unsupported_cache[(key, model)] = (
        _stream_clock() + STREAM_UNSUPPORTED_TTL_SECONDS
    )


def reset_stream_unsupported_cache() -> None:
    """仅供测试：清空负缓存（进程内状态不能在用例之间串味）。"""
    _stream_unsupported_cache.clear()


# ---------------------------------------------------------------------------
# OpenAI 兼容体的错误与审计映射
# ---------------------------------------------------------------------------


def _error_type(status_code: int) -> str:
    if status_code == 429:
        return "rate_limit_error"
    if status_code >= 500:
        return "server_error"
    return "invalid_request_error"


def _openai_error(
    status_code: int,
    code: str,
    message: str,
    *,
    request_id: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    merged = {"X-AI-Request-Id": request_id}
    if headers:
        merged.update(headers)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": _error_type(status_code),
                "code": code,
            }
        },
        headers=merged,
    )


def _audit_status(code: str) -> str:
    """把 RouteError 的 code 压成审计表的 status 枚举。"""
    if code == "AI_TIMEOUT":
        return "timeout"
    if code == "AI_BUSY":
        return "busy"
    if code in REJECTED_CODES:
        return "rejected"
    return "error"


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _usage_fields(usage: object) -> dict[str, int | None]:
    """响应体里的 ``usage`` → token 列（口径统一在 ``services.ai.usage``）。"""
    return ai_usage.token_fields(usage)


def _scene_from(request: Request | None) -> str | None:
    """``X-AI-Scene`` 头 → ``purpose``；拿不到就是 ``None``（未知来源）。

    只在**元数据**位置使用：清洗成小写字母/数字/下划线/连字符，截到列宽。不清洗
    的话，超过 24 字符的场景名会让 INSERT 在 Oracle 上以 ORA-12899 失败——审计
    旁路的异常被吞掉，等于这次调用没有流水。
    """
    if request is None:
        return None
    raw = request.headers.get(SCENE_HEADER)
    if not raw:
        return None
    scene = _SCENE_STRIP.sub("-", raw.strip().lower()).strip("-")
    return scene[:SCENE_MAX_LEN].strip("-") or None


def ai_scene(request: Request) -> str | None:
    """FastAPI 依赖：把请求头里的场景名交给路由层（缺失 = NULL）。"""
    return _scene_from(request)


def _is_admin(user: dict) -> bool:
    return "admin" in (user.get("per") or [])


async def get_ai_client(
    token: str | None = Depends(oauth2_scheme),
    _log_scope=Depends(_log_level_scope),
    request: Request = None,
):
    """``/v1/*`` 的鉴权入口：API key 或平台 JWT，二选一。

    ``zvk_`` 前缀的 token 走 :func:`services.ai.api_keys.authenticate`（只读
    ``ai_api_keys``，判定存在/启用/到期/用户/封禁）；其余 token 原样交给
    ``_authenticate``——JWT 行为逐字节不变（同样的 401 体、同样的 request 级
    缓存与日志级别下限）。

    **只有 ``/v1/models`` 与 ``/v1/chat/completions`` 用它**：API key 的爆炸
    半径就是「以本人身份对话、花本人的额度」，管理面仍然只认 JWT。
    """
    if api_keys.is_api_key(token):
        try:
            return await api_keys.authenticate(token)
        except api_keys.ApiKeyError as exc:
            # 鉴权失败发生在依赖层，响应体走仓库约定的 ``{"detail": "<code>"}``
            # ——与 JWT 路径的 401 同形（客户端看到的是「凭据无效」，不是业务错误）。
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.code,
                headers={"WWW-Authenticate": "Bearer"},
            ) from None
    return await _authenticate(token, "long", True, request)


def _require_admin(user: dict) -> None:
    if not _is_admin(user):
        raise HTTPException(status_code=403, detail="Permission denied")


def _has_ai_access(user: dict) -> bool:
    """``per`` 是原始角色（student/secretary/department/admin），学生会=department。

    刻意不用 ``perm``：那是 V2 的权限映射，口径不同，拿它判断会把「有某个 V2
    权限的人」也放进来。
    """
    return bool(set(user.get("per") or []) & ai_config.allowed_roles())


def _require_ai_access(user: dict) -> None:
    """兼容入口：AI 面旧的门禁（现在各端点自己判——拒绝要落审计行）。

    保留是为了不打断可能存在的调用方（``/api/ai/*`` 的第三方集成习惯用异常而非
    返回值判断）；新代码请用 :func:`_has_ai_access` + :func:`_reject_http`。
    """
    if not _has_ai_access(user):
        raise HTTPException(status_code=403, detail="AI_FORBIDDEN")


AI_FORBIDDEN_MESSAGE = "ai service is not available for your role"


def _feature_error(user: dict, key: str, *, request_id: str) -> JSONResponse | None:
    """OpenAI 兼容面的功能开关门禁（``ROLE_FEATURES``）。

    拒绝体必须是 ``{"error": {...}}``：客户端是 OpenAI SDK，它不认识 ``detail``。
    ``code`` 沿用既有的 ``AI_FORBIDDEN``（前端已有对应文案），``message`` 用开关层
    的文案，与角色白名单的 ``AI_FORBIDDEN_MESSAGE`` 区分开——「这个角色不该用 AI」
    与「这个角色没被授予这项功能」是两件事。

    位置恒在既有角色判定**之后**：先判角色（报错文案不变），再判开关。
    """
    try:
        ensure_feature(user, key)
    except HTTPException as exc:
        return _openai_error(
            exc.status_code, "AI_FORBIDDEN", str(exc.detail), request_id=request_id
        )
    return None


def _credential_error(exc: CredentialError) -> HTTPException:
    """服务层错误码 → HTTP。``detail`` 用 code 而不是长句：客户端按 code 分支，
    人话写在 code 里（``AI_KEY_DUPLICATE`` 比一段英文更好 grep）。"""
    return HTTPException(status_code=exc.status_code, detail=exc.code)


def _quota_error(exc: quota.QuotaError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.code)


async def _charge(
    user_oid: str, units: int, roles: list[str] | None = None
) -> None:
    """后置扣减是 best-effort：配额回写失败不能丢掉已经生成的答案。

    ``units <= 0``（完全没产出的失败）直接返回：不扣额度，也不为 0 AF 建额度行。
    """
    if int(units) <= 0:
        return
    try:
        await quota.consume(user_oid, "af", units, roles=roles, want_state=False)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("ai quota write-back failed: %s", type(exc).__name__)


#: AI 对话突发规则的 trigger code（与 V033 的种子、``routers.ban_router`` 同名）。
BURST_TRIGGER = "ai_chat_burst"


async def _teardown_step(label: str, awaitable) -> None:
    """流式收尾的一步：失败只记日志，不能顺手把后面的审计/扣费也跳掉。

    槽已经由调用方先还了，所以这里兜住「异常」（不是取消）只是记账；
    ``CancelledError`` 仍然向上抛——那是客户端断开，吞掉反而会干扰取消传播。
    """
    try:
        await awaitable
    except Exception as exc:  # noqa: BLE001 - 收尾是旁路，失败不改响应
        _logger.warning(
            "ai stream teardown failed at %s: %s", label, type(exc).__name__
        )


async def _evaluate_burst(user_oid: str) -> None:
    """一次请求结束后评估「AI 对话请求突发」（只告警，不封人）。

    挂点只有两个：chat 请求完成处（成功 / 扣费后）与 agent run 完成处。``evaluate``
    自带 60 秒 ``(trigger, user)`` 节流与宽 except，所以这里不必再包一次 try：
    调用点在已经生成答案之后，任何统计失败都只能是「少一条告警」。
    """
    await risk_monitor.evaluate(BURST_TRIGGER, str(user_oid))


def _rate_limited(exc: rate_limit.RateLimitError, *, request_id: str) -> JSONResponse:
    """OpenAI 面的 429：``Retry-After`` 是客户端唯一能自动退避的信号。"""
    return _openai_error(
        429,
        rate_limit.RateLimitError.code,
        exc.message,
        request_id=request_id,
        headers={"Retry-After": str(exc.retry_after)},
    )


def _billable(entry: dict) -> bool:
    """个人 key 不占平台额度：钱是管理员自己出的。"""
    return entry.get("scope") != PERSONAL_SCOPE


async def _reject(
    exc: RouteError,
    *,
    user_oid: str,
    model: str,
    request_id: str,
    latency_ms: int,
    key_scope: str | None = None,
    provider: str | None = None,
    purpose: str | None = None,
    attempt_no: int | None = None,
    roles_snapshot: str | None = None,
) -> JSONResponse:
    """拒绝路径落一行审计再回错误。

    ``status`` 沿用既有枚举（由 :func:`_audit_status` 从错误码压出来），
    ``attempt_no`` 为 NULL 表示「这一行不是某次上游尝试」（门禁/预检直接挡掉，
    没打过上游）；重试之后才被拒的那种会带上当时是第几次尝试。
    """
    await audit.record(
        user_oid=user_oid,
        capability="chat",
        endpoint=audit.ENDPOINT_CHAT,
        provider=provider,
        model=model,
        status=_audit_status(exc.code),
        http_status=exc.status_code,
        error_code=exc.code,
        latency_ms=latency_ms,
        request_id=request_id,
        key_scope=key_scope,
        purpose=purpose,
        attempt_no=attempt_no,
        roles_snapshot=roles_snapshot,
    )
    return _openai_error(
        exc.status_code, exc.code, exc.message, request_id=request_id
    )


async def _reject_http(
    *,
    user_oid: str,
    model: str | None,
    request_id: str,
    status_code: int,
    code: str,
    purpose: str | None = None,
    error_code: str | None = None,
    capability: str = "chat",
    endpoint: str = audit.ENDPOINT_CHAT,
    roles_snapshot: str | None = None,
) -> None:
    """门禁类拒绝（权限 / 关闭 / 模型不存在 / 额度 / 频控）的审计落行。

    只写流水，**不**生成响应：调用方仍用原来那段 ``_openai_error`` /
    ``_rate_limited`` 返回，这样响应体逐字节不变，行为改动只有「多了一行审计」。
    ``status`` 恒为 ``rejected``：这几种情况网关自己挡掉了，一次上游调用都没发生。
    上游限流才是 ``busy``（见 ``_audit_status``），两者不能混。

    ``capability`` / ``endpoint`` 默认是 chat 面；OCR 的 403 要显式传 ``ocr``，
    否则同一条拒绝会被记到错误的入口上（按 capability 统计就失真了）。
    """
    await audit.record(
        user_oid=user_oid,
        capability=capability,
        endpoint=endpoint,
        model=model,
        status="rejected",
        http_status=status_code,
        error_code=error_code or code,
        latency_ms=0,
        request_id=request_id,
        purpose=purpose,
        roles_snapshot=roles_snapshot,
    )




#: 义工登记页可选引擎的入口是「可登记义工的管理者」——比 ``/api/ai/ocr`` 宽一档
#: （那边只有 admin）。入口不同门禁就不同，所以集合写在各自的入口旁边。
VOLUNTEER_PROVIDER_ROLES = frozenset({"admin", "department", "secretary"})
