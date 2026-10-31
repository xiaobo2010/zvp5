"""AI 网关 HTTP 层（``/api/ai``）。

两套协议故意并存，不统一错误体：

* ``/api/ai/ocr``、``/api/ai/health``、``/api/ai/keys`` 走仓库约定
  ``{"status","code","data"}`` + ``HTTPException``；
* ``/api/ai/v1/*`` 是 OpenAI 兼容面，错误体必须是 ``{"error":{"message","type","code"}}``，
  因为客户端是 OpenAI SDK，它不认识 zvp 的 ``detail``，读到别的形状只会抛解析错误。

流式请求**不做 provider 切换**：字节已经吐给客户端了，重放等于把两份答案拼在一起。

本层只做协议翻译与权限判定；「这次该用哪把 key、要不要计费」由
``services.ai.credentials`` 的目录决定。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from db import backends
from db import state
from db.ids import is_valid_oid
from services import file_ops, risk_monitor, violation_guard
from services.ai import api_keys, audit, ban_state, credentials, quota, rate_limit, routing
from services.ai import config as ai_config
from services.ai import model_rates
from services.ai import ocr as ai_ocr
from services.ai import ocr_engines
from services.ai import ocr_engines_admin
from services.ai import prompt_audit
from services.ai import prompt_cache
from services.ai import providers
from services.ai import safety_guard
from services.ai import usage as ai_usage
from services.ai.credentials import CredentialError
from services.ai.routing import RouteError
from typings.log import inject_log, xuehai_id_from
from util.attachment_ocr import process_ocr
from util.object_id import (
    _authenticate,
    _log_level_scope,
    get_current_user,
    oauth2_scheme,
    require_password_verification,
    validate_object_id,
)
from util.permission.features import ensure_feature
from util.read_throttle import read_budget
from util.user import get_user_display_name, get_user_display_names

router = APIRouter()
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


class OcrRequest(BaseModel):
    attachment_oid: str
    provider: str | None = None


class FileOcrRequest(BaseModel):
    """文件路径 OCR 的入参（预览里的图片没有附件 oid）。

    ``path`` 由 ``services.file_ops`` 解析（绝对路径 + 根目录 jail + 凭据名拦截），
    这里不再重复路径规则：两处各写一套只会漂移。
    """

    path: str = Field(min_length=1, max_length=4096)
    provider: str | None = None


class VolunteerOcrRequest(OcrRequest):
    verification_token: str


class KeyCreateRequest(BaseModel):
    """新建凭据。``api_key`` 的上下界与 ``credentials`` 的校验保持一致。

    ``headers`` / ``api_path`` 是接非 OpenAI 形状 token plan 用的（``X-Api-Key``、
    ``/v1/messages`` 之类）。两者都**不在路由层校验**：头名的 RFC 7230 形状、CR/LF
    注入、path 必须是相对路径这些口径全在 ``credentials`` 里，重复一遍只会漂移。
    """

    scope: str
    name: str
    base_url: str
    # 两种形态并存（与 ``AI_CREDENTIALS.MODELS_JSON`` 一致）：裸模型名，或
    # ``{"id": "gpt-4o-mini", "context": 128000}``（按模型填上下文窗口）。形状
    # 校验在 ``credentials._clean_models``：非法对象回 400 而不是 Pydantic 的
    # 422，前端才能拿到中文文案。
    models: list[str | dict] = Field(default_factory=list)
    api_key: str = Field(min_length=8, max_length=512)
    enabled: bool = True
    headers: dict[str, str] | None = None
    api_path: str | None = None
    # ISO 8601 字符串；不传 = 永不过期。解析与「必须晚于现在」的口径全在
    # ``credentials._clean_expires_at``（错误码 AI_KEY_BAD_EXPIRY）。
    expires_at: str | None = None
    verification_token: str | None = None


class KeyUpdateRequest(BaseModel):
    """部分更新：只改传了的字段，没传的保持原值。

    ``api_key`` 允许不传（改名字/换模型时不该被迫重填 key）；传了就是轮换。
    ``expires_at`` 用 ``exclude_unset`` 天然区分三种意图：不传 = 不改，传字符串
    = 设置，显式 ``null`` = 清除到期。
    """

    name: str | None = None
    base_url: str | None = None
    # 同 ``KeyCreateRequest.models``：字符串或 ``{id, context}`` 对象都收，
    # 形状校验留给 ``credentials._clean_models``（400 + 中文文案）。
    models: list[str | dict] | None = None
    api_key: str | None = Field(default=None, min_length=8, max_length=512)
    enabled: bool | None = None
    headers: dict[str, str] | None = None
    api_path: str | None = None
    expires_at: str | None = None
    verification_token: str | None = None


class KeyDeleteRequest(BaseModel):
    """删除凭据的复核令牌；``DELETE`` 也走 body，与附件/用户端点一致。"""

    verification_token: str | None = None


class OcrEngineCreateRequest(BaseModel):
    """新建 OCR 引擎。必填凭证字段按 ``kind`` 定，校验在服务层（错误码要稳定）。

    ``secret`` 是**明文凭证 dict**（腾讯 ``secret_id/secret_key/region``、阿里
    ``access_key_id/access_key_secret[/endpoint]``、本地引擎必须留空）；它只在这
    一次请求里以明文存在，落库前会被 :mod:`services.ai.crypto` 加密，响应里连密文
    都不回。
    """

    name: str
    kind: str
    config: dict | None = None
    secret: dict | None = None
    enabled: bool = True
    priority: int = 100


class OcrEngineUpdateRequest(BaseModel):
    """部分更新：``None``/未传 = 不改。

    ``kind`` 刻意不可改（换引擎类型等于换凭证与请求形状，删行重建更清楚）。
    """

    name: str | None = None
    config: dict | None = None
    secret: dict | None = None
    enabled: bool | None = None
    priority: int | None = None


class QuotaSetRequest(BaseModel):
    """管理员设额：``kind`` 与 ``limit_count`` 都必填，语义是「覆盖上限」。

    字段不在这里做枚举/区间约束：``kind`` 的合法值与 ``limit_count >= 0``
    由服务层判定，这样非法输入回的是稳定的 400 + 错误码，而不是 pydantic
    的 422 报文（客户端按 code 分支，不解析 detail 数组）。
    """

    kind: str
    limit_count: int


class QuotaResetRequest(BaseModel):
    """重置只针对一种能力：``used`` 归零，``limit_count`` 保留。"""

    kind: str


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
    """后置扣减是 best-effort：配额回写失败不能丢掉已经生成的答案。"""
    try:
        await quota.consume(user_oid, "af", units, roles=roles)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("ai quota write-back failed: %s", type(exc).__name__)


#: AI 对话突发规则的 trigger code（与 V033 的种子、``routers.ban_router`` 同名）。
BURST_TRIGGER = "ai_chat_burst"


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


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------


#: 义工登记页可选引擎的入口是「可登记义工的管理者」——比 ``/api/ai/ocr`` 宽一档
#: （那边只有 admin）。入口不同门禁就不同，所以集合写在各自的入口旁边。
VOLUNTEER_PROVIDER_ROLES = frozenset({"admin", "department", "secretary"})


@router.post("/volunteer-ocr", status_code=202)
async def request_volunteer_ocr(
    payload: VolunteerOcrRequest,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    await require_password_verification(
        payload.verification_token, user, detail="AI_PASSWORD_VERIFICATION_REQUIRED"
    )
    return await _enqueue_ocr(
        OcrRequest(
            attachment_oid=payload.attachment_oid, provider=payload.provider
        ),
        background_tasks,
        user,
        log,
        provider_roles=VOLUNTEER_PROVIDER_ROLES,
    )


@router.post("/ocr", status_code=202)
async def request_ocr(
    payload: OcrRequest,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    return await _enqueue_ocr(payload, background_tasks, user, log)


async def _enqueue_ocr(
    payload: OcrRequest,
    background_tasks: BackgroundTasks,
    user: dict,
    log,
    *,
    provider_roles: frozenset[str] = ai_ocr.ADMIN_PROVIDER_ROLES,
):
    if not _has_ai_access(user):
        # 门禁拒绝也要留一行流水：否则「谁在没有权限的情况下反复试探 OCR」在
        # 后台完全看不见（与 chat 面同一口径）。
        await _reject_http(
            user_oid=user["id"],
            model=None,
            request_id=uuid4().hex,
            status_code=403,
            code="AI_FORBIDDEN",
            purpose=SCENE_OCR,
            capability="ocr",
            endpoint=audit.ENDPOINT_OCR,
            roles_snapshot=audit.roles_snapshot(user.get("per")),
        )
        raise HTTPException(status_code=403, detail="AI_FORBIDDEN")
    # 角色白名单之后再过功能开关：``/ocr`` 与 ``/volunteer-ocr`` 共用这条入队路径，
    # 两个入口各自原有的人口径（含密码再认证）都保留不变。
    ensure_feature(user, "ai.ocr")
    oid = validate_object_id(payload.attachment_oid)
    try:
        data = await ai_ocr.enqueue(
            user, oid, provider=payload.provider, provider_roles=provider_roles
        )
    except RouteError as exc:
        if exc.status_code == 403:
            # 「没资格的人想指定引擎」也是拒绝路径：留一行流水，否则「有人在
            # 反复试 provider 参数」这种探测在后台看不见。
            await _reject_http(
                user_oid=user["id"],
                model=None,
                request_id=uuid4().hex,
                status_code=403,
                code=exc.code,
                purpose=SCENE_OCR,
                capability="ocr",
                endpoint=audit.ENDPOINT_OCR,
                roles_snapshot=audit.roles_snapshot(user.get("per")),
            )
        # 仓库约定：``/api/ai/*`` 的普通端点用 ``detail`` + 状态码。
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None
    # ``engine_kind`` 只服务于后台任务（入队与处理必须是同一个引擎），不对外暴露。
    engine_kind = data.pop("engine_kind", None)
    if data.get("status") == "pending":
        # 第 4 个参数只用于逐次尝试的审计归属（谁触发的这次识别）。
        background_tasks.add_task(
            process_ocr, oid, engine_kind, data.get("engine"), user["id"]
        )

    log.with_text(
        f"AI OCR 入队 {oid}（引擎 {data.get('engine') or '-'}，"
        f"状态 {data.get('status')}）",
        type="ai_ocr_enqueue",
    )
    await log.insert_log()

    if data.get("status") == "processing":
        # 幂等分支：这次请求没有新受理任何东西（已在处理中，也没扣配额），用 200
        # 表示「已经是这个状态」；202 只留给真正新入队的那一次。
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "code": 200, "data": data},
        )

    return {"status": "ok", "code": 202, "data": data}


#: 文件 OCR 认的扩展名 → MIME。与 ``services.ai.ocr.ALLOWED_MIME`` 一一对应：
#: 扩展名是「用户看得见的类型」，MIME 是引擎要的口径，两者都在这里收敛。
OCR_FILE_MIME_TYPES: dict[str, str] = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}


def _read_ocr_image(path: str) -> tuple[bytes, str]:
    """路径 → ``(字节, mime)``：解析、类型、体积三道闸，全部复用既有口径。

    同步 IO：调用方用 ``asyncio.to_thread`` 包一层（与系统文件端点同一条纪律）。
    ``FileOpError`` 原样抛出，由端点的错误翻译统一成 ``detail=<错误码>``——jail
    越界在这里就是 403 ``FILES_ROOT_FORBIDDEN``，不会走到读字节。
    """
    resolved = file_ops.resolve_path(path)
    extension = os.path.splitext(resolved)[1].lower().lstrip(".")
    mime = OCR_FILE_MIME_TYPES.get(extension)
    if mime is None:
        raise HTTPException(status_code=400, detail="AI_UNSUPPORTED_MEDIA")

    handle, size, _name = file_ops.open_download(resolved)
    try:
        if size > ai_config.max_ocr_image_bytes():
            raise HTTPException(status_code=400, detail="AI_OCR_IMAGE_TOO_LARGE")
        return handle.read(), mime
    finally:
        handle.close()


@router.post("/ocr/file")
async def request_file_ocr(
    payload: FileOcrRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """对站点内的图片文件做一次同步 OCR，``data`` 是 ``{"text": ...}``。

    只读路径 + admin：这个入口服务于「文件预览里点一下识别」，没有附件 oid、也不
    产生附件配额。计费走与附件入口完全相同的一条路径（频控 + 用户额度 + 站点额度
    + 每次尝试的审计），不是第二条免费通道。
    """
    if "admin" not in (user.get("per") or []):
        await _reject_http(
            user_oid=user["id"],
            model=None,
            request_id=uuid4().hex,
            status_code=403,
            code="AI_FORBIDDEN",
            purpose=SCENE_OCR,
            capability="ocr",
            endpoint=audit.ENDPOINT_OCR,
            roles_snapshot=audit.roles_snapshot(user.get("per")),
        )
        raise HTTPException(status_code=403, detail="AI_FORBIDDEN")
    ensure_feature(user, "ai.ocr")

    try:
        image, mime = await asyncio.to_thread(_read_ocr_image, payload.path)
    except file_ops.FileOpError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.code or "AI_OCR_FILE_UNREADABLE",
        ) from None

    try:
        result = await ai_ocr.recognize_bytes(
            user, image, mime=mime, provider=payload.provider
        )
    except RouteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None

    log.with_text(
        f"AI OCR 文件识别 {payload.path}（引擎 {result.get('engine') or '-'}，"
        f"{len(image)} 字节）",
        type="ai_ocr_enqueue",
    )
    await log.insert_log()

    return {
        "status": "ok",
        "code": 200,
        "data": {"text": result.get("text") or ""},
    }


# ---------------------------------------------------------------------------
# 凭据管理（admin）
# ---------------------------------------------------------------------------


@router.get("/keys")
async def list_keys(user=Depends(get_current_user)):
    """全部凭据，个人在前。响应里没有 api_key，也没有密文。"""
    _require_admin(user)
    return {
        "status": "ok",
        "code": 200,
        "data": await credentials.list_entries(user),
    }


@router.post("/keys", status_code=201)
async def create_key(
    payload: KeyCreateRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    _require_admin(user)
    await require_password_verification(
        payload.verification_token, user, detail="AI_PASSWORD_VERIFICATION_REQUIRED"
    )
    try:
        view = await credentials.create_entry(
            user_oid=user["id"],
            scope=payload.scope,
            name=payload.name,
            base_url=payload.base_url,
            models=payload.models,
            api_key=payload.api_key,
            enabled=payload.enabled,
            headers=payload.headers,
            api_path=payload.api_path,
            expires_at=payload.expires_at,
        )
    except CredentialError as exc:
        raise _credential_error(exc) from None

    log.with_text(
        f"AI 凭据新增 {view['scope']}/{view['name']}", type="ai_key_create"
    )
    await log.insert_log()
    return {"status": "ok", "code": 201, "data": view}


@router.patch("/keys/{oid}")
async def update_key(
    oid: str,
    payload: KeyUpdateRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    _require_admin(user)
    target = validate_object_id(oid)
    await require_password_verification(
        payload.verification_token, user, detail="AI_PASSWORD_VERIFICATION_REQUIRED"
    )
    try:
        view = await credentials.update_entry(
            oid=target,
            actor=user,
            fields=payload.model_dump(
                exclude_unset=True, exclude={"verification_token"}
            ),
        )
    except CredentialError as exc:
        raise _credential_error(exc) from None

    log.with_text(
        f"AI 凭据更新 {view['scope']}/{view['name']}", type="ai_key_update"
    )
    await log.insert_log()
    return {"status": "ok", "code": 200, "data": view}


@router.delete("/keys/{oid}", status_code=204)
async def delete_key(
    oid: str,
    payload: KeyDeleteRequest | None = None,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    _require_admin(user)
    target = validate_object_id(oid)
    await require_password_verification(
        payload.verification_token if payload is not None else None,
        user,
        detail="AI_PASSWORD_VERIFICATION_REQUIRED",
    )
    try:
        await credentials.delete_entry(oid=target, actor=user)
    except CredentialError as exc:
        raise _credential_error(exc) from None

    log.with_text(f"AI 凭据删除 {target}", type="ai_key_delete")
    await log.insert_log()
    return None


# ---------------------------------------------------------------------------
# 调用流水（admin）
# ---------------------------------------------------------------------------

# 过滤值的合法集合与库里的 CHECK 一一对应（db/models.py 的 ck_ai_call_logs_*）。
# 非法值回 400 AI_LOGS_BAD_FILTER 而不是静默忽略：静默忽略会让「筛出来是空的」
# 与「筛条件写错了」长得一样。
LOG_CAPABILITIES = frozenset({"ocr", "chat"})
LOG_STATUSES = frozenset({"success", "error", "timeout", "rejected", "busy"})
LOG_KEY_SCOPES = frozenset({"personal", "shared", "server"})


def _bad_filter() -> HTTPException:
    return HTTPException(status_code=400, detail="AI_LOGS_BAD_FILTER")


def _epoch_to_local(value: int | None) -> datetime | None:
    """epoch 秒 → naive 本地 datetime。

    ``AI_CALL_LOGS.CREATED_AT`` 是 naive ``TIMESTAMP``（仓储写入用
    ``datetime.now()``），所以查询边界也用本地时间：``fromtimestamp`` 出来的
    正是同一口径。带 tz 的 datetime 直接比较会被 SQLAlchemy 当成另一个时区，
    「含端点」的边界就会偏移几个小时。
    """
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value)
    except (OverflowError, OSError, ValueError):
        raise _bad_filter() from None


@router.get("/logs")
async def list_call_logs(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    perpage: int = Query(20, ge=1, le=100, description="Items per page (max 100)"),
    user_oid: str | None = Query(None, description="Filter by user oid"),
    capability: str | None = Query(None, description="ocr | chat"),
    status: str | None = Query(None, description="success|error|timeout|rejected|busy"),
    key_scope: str | None = Query(None, description="personal | shared | server"),
    request_id: str | None = Query(None, description="Exact request id (trace one call)"),
    provider: str | None = Query(None, description="Exact provider name"),
    model: str | None = Query(None, description="Exact model name"),
    purpose: str | None = Query(None, description="Calling scene (chat | ocr | ...)"),
    time_start: int | None = Query(None, description="created_at >= (epoch seconds)"),
    time_end: int | None = Query(None, description="created_at <= (epoch seconds)"),
    user=Depends(get_current_user),
    _budget=Depends(read_budget),
):
    """AI 调用流水（新调用在前），仅 admin。

    分页口径与 ``/api/logs`` 一致：``page`` 1-based，``perpage`` 1..100，
    总数由仓储单独 ``COUNT``（分页只取 ``LIMIT`` 行，不必为拿一页扫全表）。

    时间边界是**闭区间**：``created_at >= time_start`` 且 ``created_at <= time_end``
    ——排查「某一秒发生了什么」时，端点那一秒不该被排除在外。

    ``request_id`` / ``provider`` / ``model`` / ``purpose`` 是精确匹配：它们都是
    「先有一个已知值（响应头里的编号、报表里的服务商名）再回头查」的入口。
    """
    _require_admin(user)

    if capability and capability not in LOG_CAPABILITIES:
        raise _bad_filter()
    if status and status not in LOG_STATUSES:
        raise _bad_filter()
    if key_scope and key_scope not in LOG_KEY_SCOPES:
        raise _bad_filter()

    target = validate_object_id(user_oid) if user_oid else None
    start = _epoch_to_local(time_start)
    end = _epoch_to_local(time_end)

    repo = backends.get_ai_call_logs_repo()
    filters = {
        "user_oid": target,
        "capability": capability or None,
        "status": status or None,
        "key_scope": key_scope or None,
        "request_id": request_id or None,
        "provider": provider or None,
        "model": model or None,
        "purpose": purpose or None,
        "time_start": start,
        "time_end": end,
    }
    logs = await repo.list_page(**filters, page=page, page_size=perpage)
    total = await repo.total_count(**filters)

    names = await get_user_display_names(
        [row["user_oid"] for row in logs if row.get("user_oid")]
    )
    for row in logs:
        # 主键给两份字符串：``_id`` 是仓储的行形状（Mongo 口径），``oid`` 是
        # 对外契约/前端行 key 用的名字。两者同值，调用方不必猜该读哪个。
        oid = str(row["_id"])
        row["_id"] = oid
        row["oid"] = oid
        # 与 ``/api/logs`` 同款兜底：账号被删/查不到时回「未知用户」而不是空串。
        row["user_name"] = names.get(row.get("user_oid"), "未知用户")

    return {
        "status": "ok",
        "code": 200,
        "data": logs,
        "metadata": {"size": total},
    }


# 统计窗口的上下界：1..90 天。上限按流水的保留期（90 天）取——查更长的窗口只
# 会扫到已经被清理掉的部分，看起来像「数据丢了」，其实是保留策略。
STATS_MAX_DAYS = 90


@router.get("/logs/stats")
async def call_log_stats(
    days: int = Query(7, ge=1, le=STATS_MAX_DAYS, description="Window size in days"),
    user=Depends(get_current_user),
):
    """调用聚合概览（按日 + 按 provider/model/purpose/用户/状态 + 缓存），仅 admin。

    ``days`` 是**包含今天在内**往前数的自然日窗口（默认 7，上限 90）。窗口起点
    取「今天零点减 (days-1) 天」，而不是「现在减 days×24h」：按日分组的报表如果
    从半天中间开始，第一天的数字会是半天，看起来像服务掉了一半量。

    ``by_user``（top 50，含 ``name`` / ``avg_latency_ms``）回答「谁在花钱」，
    ``by_status`` 把 ``errors`` 拆成 success/error/rejected/busy——两者都与既有的
    四个分项来自同一个窗口，合计口径不变。
    """
    _require_admin(user)

    now = datetime.now()
    start = (now - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    data = await backends.get_ai_call_logs_repo().stats(since=start)

    return {
        "status": "ok",
        "code": 200,
        "data": {
            "days": days,
            "since": start.isoformat(sep=" ", timespec="seconds"),
            **data,
        },
    }


# ---------------------------------------------------------------------------
# prompt 审阅（admin）：正文只在这里出现，读一条就要留一条访问审计
# ---------------------------------------------------------------------------

# 审阅列表的默认/最大窗口（天）。默认 7 而不是保留期 30：这个端点是为「查最近
# 发生了什么」设计的，翻一个月前的正文应该显式写 days。
PROMPT_LIST_DAYS = 7
PROMPT_MAX_DAYS = 90


@router.get("/prompts")
async def list_prompt_audit(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    perpage: int = Query(20, ge=1, le=100, description="Items per page (max 100)"),
    user_oid: str | None = Query(None, description="Filter by user oid"),
    purpose: str | None = Query(
        None, description="Calling scene (chat | agent | markdown-assistant | ocr | ...)"
    ),
    days: int = Query(
        PROMPT_LIST_DAYS, ge=1, le=PROMPT_MAX_DAYS, description="Window size in days"
    ),
    time_start: int | None = Query(None, description="created_at >= (epoch seconds)"),
    time_end: int | None = Query(None, description="created_at <= (epoch seconds)"),
    keyword: str | None = Query(
        None, description="Substring match on the prompt body (literal, case-sensitive)"
    ),
    user=Depends(get_current_user),
    _budget=Depends(read_budget),
):
    """受控留存的 prompt **元数据**列表（新记录在前），仅 admin。

    **不返回正文**（``prompt_text``）：翻一页就把几十段学生对话读进内存，既慢
    又无谓扩大正文暴露面。要看内容走 ``GET /api/ai/prompts/{oid}``，那条路径会
    写访问审计。``prompt_chars`` 是库里的 ``LENGTH(PROMPT_TEXT)``，够判断「这条
    值不值得点开」。

    ``days`` 是滚动窗口（现在往前 N 天，1–90），默认 7；用户/场景/正文关键字与
    ``/logs`` 同款过滤。``user_oid`` 非法形状回 400（与 ``/logs`` 同口径）。

    ``time_start`` / ``time_end`` 与 ``/api/ai/logs`` 逐字同口径：epoch 秒、
    闭区间、按本地时间比较。**给定时以显式范围为准，``days`` 不再参与**——即使
    只给了 ``time_end`` 也不叠加「最近 7 天」，否则「查某天之前」会被默认窗口
    悄悄截断，结果看起来像「没有匹配」。

    ``keyword`` 是正文的**字面子串**匹配（``DBMS_LOB.INSTR``，大小写敏感，不是
    ``LIKE`` 通配）：过滤在库里完成，正文不会进入列表响应。空串等于不过滤。
    """
    _require_admin(user)

    target = validate_object_id(user_oid) if user_oid else None
    start = _epoch_to_local(time_start)
    end = _epoch_to_local(time_end)
    # days 只是兼容捷径：显式给了任一时间边界就整段以显式为准（见 docstring）。
    if start is None and end is None:
        start = datetime.now() - timedelta(days=days)
    pattern = (keyword or "").strip() or None

    repo = backends.get_ai_prompt_audit_repo()
    rows = await repo.list_page(
        user_oid=target,
        purpose=purpose or None,
        keyword=pattern,
        since=start,
        until=end,
        page=page,
        page_size=perpage,
    )
    total = await repo.count(
        user_oid=target,
        purpose=purpose or None,
        keyword=pattern,
        since=start,
        until=end,
    )

    names = await get_user_display_names(
        [row["user_oid"] for row in rows if row.get("user_oid")]
    )
    for row in rows:
        oid = str(row["_id"])
        row["_id"] = oid
        row["oid"] = oid
        row["user_name"] = names.get(row.get("user_oid"), "未知用户")
        # purpose 由仓储的元数据投影带出（列表按场景回显/筛选要用）；
        # 这里只在缺失时补 None，保证响应字段集合稳定。
        row.setdefault("purpose", None)

    return {
        "status": "ok",
        "code": 200,
        "data": rows,
        "metadata": {"size": total},
    }


@router.get("/prompts/{oid}")
async def get_prompt_audit(
    oid: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """单条 prompt 正文（admin）；**每次读取写一条 APP_LOGS 访问审计**。

    审计行 ``type='ai_prompt_access'`` / ``level='info'``：``info`` 是承重的——
    它随 APP_LOGS 的保留期自动清理（``login_fail`` 先例），不会把「谁看过学生
    的对话」永久堆在库里；而 ``warning`` 会被清理保护集留住，那是给安全事件用的。

    审计只记目标 oid 与目标 user_oid，**不记正文**：正文已经在被审计的那张表里，
    再抄一份到 APP_LOGS 等于把暴露面翻倍。

    oid 形状非法或记录不存在都回 404（不是 400）：对调用方来说两者的处置相同，
    且 404 不会泄露「这个 oid 曾经存在过」。
    """
    _require_admin(user)

    if not is_valid_oid(oid):
        raise HTTPException(status_code=404, detail="AI_PROMPT_NOT_FOUND")

    row = await backends.get_ai_prompt_audit_repo().get_by_oid(oid)
    if row is None:
        raise HTTPException(status_code=404, detail="AI_PROMPT_NOT_FOUND")

    target = row.get("user_oid")
    log.with_text(
        f"AI prompt 审阅 oid={oid} user_oid={target or ''}",
        type="ai_prompt_access",
    )
    await log.insert_log()

    row["oid"] = str(row["_id"])
    row["user_name"] = await get_user_display_name(target) if target else "未知用户"
    return {"status": "ok", "code": 200, "data": row}


# ---------------------------------------------------------------------------
# OCR 引擎注册表（admin；配置全站生效——OCR 没有个人凭证的概念）
# ---------------------------------------------------------------------------


@router.get("/ocr-engines")
async def list_ocr_engines(user=Depends(get_current_user)):
    """引擎清单。admin 拿完整视图（含 config / key_hint）；可登记义工的管理者
    只拿渲染下拉所需的公开字段——``config`` 是运维参数（endpoint / model），
    不该外泄给非运维角色。

    响应里没有 ``secret_cipher``，只有 ``key_hint``。
    """
    if _is_admin(user):
        return {
            "status": "ok",
            "code": 200,
            "data": await ocr_engines_admin.list_engines(),
        }
    if not (VOLUNTEER_PROVIDER_ROLES & set(user.get("per") or [])):
        raise HTTPException(status_code=403, detail="Permission denied")
    return {
        "status": "ok",
        "code": 200,
        "data": await ocr_engines_admin.list_engine_summaries(),
    }


@router.post("/ocr-engines", status_code=201)
async def create_ocr_engine(
    payload: OcrEngineCreateRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    _require_admin(user)
    # 上游地址的私网校验必须在落库前：识别请求由**服务端**发、带管理员凭证，
    # 允许指向 127.0.0.1/169.254.169.254 等于把网关变成内网跳板。
    if await ocr_engines.blocked_endpoint(payload.config, payload.secret):
        raise HTTPException(status_code=400, detail="AI_OCR_ENDPOINT_BLOCKED")
    try:
        view = await ocr_engines_admin.create_engine(**payload.model_dump())
    except RouteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None

    log.with_text(
        f"OCR 引擎新增 {view['kind']}/{view['name']}", type="ai_ocr_engine_create"
    )
    await log.insert_log()
    return {"status": "ok", "code": 201, "data": view}


@router.patch("/ocr-engines/{oid}")
async def update_ocr_engine(
    oid: str,
    payload: OcrEngineUpdateRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    _require_admin(user)
    target = validate_object_id(oid)
    # 只校验**这次提交**的 config/secret：没传的字段不会被改动，存量行在写入时
    # 已经过了同一道闸（否则「改个优先级」都要重新做一次 DNS）。
    if await ocr_engines.blocked_endpoint(payload.config, payload.secret):
        raise HTTPException(status_code=400, detail="AI_OCR_ENDPOINT_BLOCKED")
    try:
        view = await ocr_engines_admin.update_engine(
            target, payload.model_dump(exclude_unset=True)
        )
    except RouteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None

    log.with_text(
        f"OCR 引擎更新 {view['kind']}/{view['name']}", type="ai_ocr_engine_update"
    )
    await log.insert_log()
    return {"status": "ok", "code": 200, "data": view}


@router.delete("/ocr-engines/{oid}", status_code=204)
async def delete_ocr_engine(
    oid: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    _require_admin(user)
    target = validate_object_id(oid)
    try:
        await ocr_engines_admin.delete_engine(target)
    except RouteError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None

    log.with_text(f"OCR 引擎删除 {target}", type="ai_ocr_engine_delete")
    await log.insert_log()
    return None


# ---------------------------------------------------------------------------
# 额度（用户看自己 / admin 看别人与调额、重置）
# ---------------------------------------------------------------------------


async def _target_roles(oid: str) -> list[str] | None:
    """取目标用户的 ``roles``（只读窄投影，不搬 ``past``/``eligibility`` 两个 CLOB）。

    查不到用户回 ``None``：管理端此时展示全局默认额度，并由 ``source`` 标明
    ``global_default``。用空列表假装「这个人没有角色」也能得到同样的数值，但
    ``None`` 让「用户不存在」与「角色为空」在服务层保持可区分，便于以后细分口径。
    """
    row = await backends.get_users_repo().get_for_auth(oid)
    if not row:
        return None
    roles = row.get("roles")
    return roles if isinstance(roles, list) else None


@router.get("/quota")
async def my_quota(user=Depends(get_current_user)):
    """当前用户本月额度快照：``af`` 一条（OCR 与对话共用），没有行就回默认上限。"""
    return {
        "status": "ok",
        "code": 200,
        "data": await quota.snapshot(user["id"], roles=user.get("per")),
    }


#: 个人用量小计的数值列。顺序即响应里的键顺序，``total`` / ``by_provider`` /
#: ``by_model`` 三处共用同一组，避免某一处漏算一列。
_USAGE_KEYS = ("calls", "units", "prompt_tokens", "completion_tokens", "total_tokens")

# 月份标签：与 ``ai_quota.period`` / ``quota.period_now()`` 同一口径（``YYYYMM``）。
_PERIOD_RE = re.compile(r"^(\d{4})(\d{2})$")


def _quota_period_bounds(period: str | None) -> tuple[str, datetime, datetime]:
    """``period`` → ``(标签, 当月首日 00:00, 次月首日 00:00)``。

    缺省取本月（``quota.period_now()``）。非法格式一律 400：静默回落到本月会让
    「传错了」看起来像「这个月真的没用量」，而这两种情况在页面上都是 0。
    """
    label = period if period is not None else quota.period_now()
    match = _PERIOD_RE.match(label or "")
    if match is None:
        raise HTTPException(status_code=400, detail="period 必须是 YYYYMM 格式")
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        raise HTTPException(status_code=400, detail="period 必须是 YYYYMM 格式")
    try:
        start = datetime(year, month, 1)
        end = datetime(year + month // 12, month % 12 + 1, 1)
    except ValueError:
        # year 0000 / 9999-12 的次月：日期本身不存在，与格式错误同类处理。
        raise HTTPException(status_code=400, detail="period 必须是 YYYYMM 格式")
    return label, start, end


@router.get("/quota/usage")
async def my_quota_usage(
    period: str | None = Query(
        None, description="YYYYMM（缺省 = 本月，与 /quota 的 period 同口径）"
    ),
    user=Depends(get_current_user),
):
    """本人某个自然月的用量统计：总数 + 按 provider / model 分解。

    **只统计自己**：没有 ``user_oid`` 入参，条件里写死 ``user["id"]``。admin 想看
    别人的用量走 ``/api/ai/logs``（那条是审计面，带门禁与脱敏口径），这里是用户
    自助面——两张报表的服务对象不同，就不该共用一个参数。

    注册顺序有要求：本路由必须排在 ``/quota/{user_oid}``（admin 视角）**之前**，
    否则 ``GET /api/ai/quota/usage`` 会被动态段先匹配，``user_oid="usage"`` 再
    撞上 oid 形状校验变成 422/400。

    口径（与 ``ai_call_logs`` 的列语义一一对应）：

    * 窗口 = ``period`` 首日 00:00 起、次月首日 00:00 止的**半开区间**——闭区间
      会让跨月那一刻的调用被相邻两个月各算一次；
    * ``calls`` = ``COUNT(*)``：成功、失败、被拒都算一次调用；
    * ``units`` / 三组 token 是 ``COALESCE(SUM(...), 0)``：窗口内没上报过 usage
      时给 0（「这段时间一共观测到 0 个 token」），与逐行 NULL 的「没报」不同；
    * ``provider`` / ``model`` 的 NULL（OCR 或未标注）保留成独立分组，不丢行也
      不并进空串。

    ``period`` 缺省取 ``quota.period_now()``（本地时区的 ``YYYYMM``）；格式非法
    回 400，而不是静默回落到本月——回落的「一片 0」会被读成「这个月没用量」。
    """
    label, start, end = _quota_period_bounds(period)
    rows = await backends.get_ai_call_logs_repo().usage_breakdown(
        user_oid=str(user["id"]), since=start, until=end
    )

    total = dict.fromkeys(_USAGE_KEYS, 0)
    providers: dict[str | None, dict[str, int]] = {}
    by_model: list[dict] = []
    for row in rows:
        counts = {key: int(row.get(key) or 0) for key in _USAGE_KEYS}
        for key, value in counts.items():
            total[key] += value
        bucket = providers.setdefault(row["provider"], dict.fromkeys(_USAGE_KEYS, 0))
        for key, value in counts.items():
            bucket[key] += value
        by_model.append(
            {"provider": row["provider"], "model": row["model"], **counts}
        )

    # provider 维度在 Python 里汇总（SQL 只有一条 GROUP BY provider, model）。
    # 排序与 SQL 同口径：调用多的在前，同次数按名字定序；NULL 排最前（``or ""``）。
    by_provider = [
        {"provider": provider, **counts}
        for provider, counts in sorted(
            providers.items(), key=lambda item: (-item[1]["calls"], item[0] or "")
        )
    ]

    return {
        "status": "ok",
        "code": 200,
        "data": {
            "period": label,
            "total": total,
            "by_provider": by_provider,
            "by_model": by_model,
        },
    }


@router.get("/quota/{user_oid}")
async def user_quota(user_oid: str, user=Depends(get_current_user)):
    """admin 视角的同一份快照。

    ``ai_quota`` 没有指向用户的 FK，查无额度行是正常状态（本月还没用过），
    所以这里**不校验用户是否存在**，只校验 oid 形状。

    展示的「默认上限」必须与真实扣费口径一致：扣费走
    ``default_limit_for(用户的 roles, kind)``（admin 的 af 是 1_000_000），所以
    先查目标用户角色再算，不能拿管理端的 ``per`` 也不能停在全局
    ``DEFAULT_QUOTA``（af 200_000）——否则管理页会把一个 admin 的额度少显示
    一个数量级。查不到用户时回落全局默认，响应里的 ``source`` 会标
    ``global_default``，不编造角色。
    """
    _require_admin(user)
    ensure_feature(user, "quota.manage")
    target = validate_object_id(user_oid)
    roles = await _target_roles(target)
    return {
        "status": "ok",
        "code": 200,
        "data": await quota.admin_snapshot(target, roles=roles),
    }


@router.put("/quota/{user_oid}")
async def set_user_quota(
    user_oid: str,
    payload: QuotaSetRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """管理员设额：只改上限，已用量保持不变。"""
    _require_admin(user)
    ensure_feature(user, "quota.manage")
    target = validate_object_id(user_oid)
    try:
        data = await quota.set_user_limit(target, payload.kind, payload.limit_count)
    except quota.QuotaError as exc:
        raise _quota_error(exc) from None

    log.with_text(
        f"AI 额度设置 {target}/{data['kind']} = {data['limit']}",
        type="ai_quota_set",
    )
    await log.insert_log()
    return {"status": "ok", "code": 200, "data": data}


@router.post("/quota/{user_oid}/reset")
async def reset_user_quota(
    user_oid: str,
    payload: QuotaResetRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """管理员重置某能力当月已用量：``used`` 归零，上限保留。"""
    _require_admin(user)
    ensure_feature(user, "quota.manage")
    target = validate_object_id(user_oid)
    try:
        data = await quota.reset_used(target, payload.kind)
    except quota.QuotaError as exc:
        raise _quota_error(exc) from None

    log.with_text(
        f"AI 额度重置 {target}/{data['kind']}（上限 {data['limit']}）",
        type="ai_quota_reset",
    )
    await log.insert_log()
    return {"status": "ok", "code": 200, "data": data}


# ---------------------------------------------------------------------------
# Health（admin 视图：不含密钥、不含上游原始报文）
# ---------------------------------------------------------------------------

# 本地引擎（rapidocr）的预热状态。``None`` = 还没预热过。
# 只存布尔值：不存路径、不存模型名、不存任何加载细节，避免 health 视图变成
# 「服务器文件系统/引擎内部结构」的泄露面。
_local_engine_ready: bool | None = None


async def _warmup_local_engines() -> bool:
    """后台预热本地引擎；健康检查不能因此失败，异常一律降级为 ``False``。"""
    global _local_engine_ready
    try:
        _local_engine_ready = bool(await ocr_engines.warmup_local_engines())
    except Exception:  # noqa: BLE001 - warmup 契约上不抛，这里只是最后一道保险
        _logger.exception("ai local engine warmup failed")
        _local_engine_ready = False
    return _local_engine_ready


@router.get("/health")
async def ai_health(
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
):
    _require_admin(user)

    entries = await credentials.catalog(user["id"])
    counts = {
        credentials.SCOPE_PERSONAL: 0,
        credentials.SCOPE_SHARED: 0,
        credentials.SCOPE_SERVER: 0,
    }
    shared_entries: list[dict] = []
    models: set[str] = set()
    for entry in entries:
        scope = str(entry.get("scope") or "")
        counts[scope] = counts.get(scope, 0) + 1
        models.update(m for m in entry.get("models") or [] if isinstance(m, str))
        if scope == credentials.SCOPE_SHARED:
            # 共享项是平台出资的，运维要能一眼看到「大家此刻能打到哪」。
            shared_entries.append(
                {
                    "name": entry.get("name"),
                    "base_url": entry.get("base_url"),
                    "models": list(entry.get("models") or []),
                    "enabled": bool(entry.get("enabled", True)),
                }
            )

    # 预热本地引擎（rapidocr）：首次加载 onnxruntime 与模型要秒级时间，放在
    # 后台任务里跑，/health 立即返回；warmup 本身幂等，重复请求只是复用实例。
    # 预热的失败不影响本响应（失败只体现在下面 local_engine_ready 的取值上）。
    background_tasks.add_task(_warmup_local_engines)

    workers = ai_config.worker_count()
    return {
        "status": "ok",
        "code": 200,
        "data": {
            "enabled": ai_config.is_enabled(),
            # 只有一个布尔：预热是否已就绪。不暴露任何路径 / 模型名 / 密钥材料。
            "local_engine_ready": bool(_local_engine_ready),
            "max_concurrency": ai_config.max_concurrency(),
            # 部署的进程数 + 每进程的实际频控阈值：两者一起才能算出「全局真实
            # 上限」（阈值 × N）。单 worker 时阈值就是配置值。
            "worker_count": workers,
            "rate_limit_per_minute": {
                kind: rate_limit.effective_limit(kind)
                for kind in ai_config.RATE_LIMIT_KINDS
            },
            "providers": ai_config.public_provider_view(),
            # 「当前生效的注入策略」（不是库里能配什么）：运维要能一眼看出这台
            # 实例到底会不会往请求体里加字段。只有 host，不含密钥、路径或前缀。
            "prompt_cache": {
                "mode": ai_config.prompt_cache_mode(),
                "key_hosts": sorted(ai_config.prompt_cache_key_hosts()),
                "breakpoint_hosts": sorted(ai_config.prompt_cache_breakpoint_hosts()),
            },
            # 冷却状态的可见范围：配了 Redis 就是全局共享的，否则只有本进程。
            "cooldown_seconds": await routing.cooldown_state(),
            "cooldown_scope": routing.cooldown_scope(),
            "models": sorted(models),
            "quota_defaults": dict(ai_config.DEFAULT_QUOTA),
            "credentials": {
                "personal": counts[credentials.SCOPE_PERSONAL],
                "shared": counts[credentials.SCOPE_SHARED],
                "server": counts[credentials.SCOPE_SERVER],
                "shared_entries": shared_entries,
            },
        },
    }


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

        # 缓存注入只作用于发给上游的 body；``stream_options`` 由 providers 内部补，
        # 这里不重复补（两处都补会覆盖客户端自己的 stream_options 取值）。
        body, cache_key = prompt_cache.request_body(payload, entry)

        if await _stream_unsupported_cached(key, model):
            # 负缓存命中：一小时内的第二次请求。这个上游明确不吃流式，别再拿一次
            # 必废的流式调用去试——直接以非流式起步（结果照样包成 SSE 帧）。
            await guard.__aexit__(None, None, None)
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
        except Exception as exc:  # noqa: BLE001 - 首帧前的任何失败都还能改状态码
            await guard.__aexit__(None, None, None)
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
                # 客户端断开/中途返回时也要把上游连接关掉（httpx 的 stream 上下文
                # 由生成器的 aclose 驱动）。
                if stream_iter is not None:
                    try:
                        await stream_iter.aclose()
                    except Exception:  # noqa: BLE001 - 收尾失败不能盖住审计
                        pass
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
                else:
                    # 没有 usage：token 列留 NULL，units 按字符估算后走**同一个** AF
                    # 公式。这是「NULL ⟺ 估算」不变式的另一半。
                    units = ai_usage.estimate_af(
                        estimated_prompt_chars, state["chars"], rate
                    )
                await audit.record(
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
                )
                if _billable(entry):
                    await _charge(user_oid, units, roles)
                # 流式请求的「完成」就在生成器收尾：此时流水已落库，计数包含本次。
                await _evaluate_burst(user_oid)
                await prompt_audit.record_outcome(
                    audit_oid,
                    answer_text=(
                        _answer_text(json_result)
                        if json_body
                        else _stream_answer_text(bytes(state["answer"]))
                    ),
                )
                await guard.__aexit__(None, None, None)

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
