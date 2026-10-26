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

import json
import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    HTTPException,
    Query,
    Request,
)
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from db import backends
from db.ids import is_valid_oid
from services import risk_monitor
from services.ai import api_keys, audit, credentials, quota, rate_limit, routing
from services.ai import config as ai_config
from services.ai import ocr as ai_ocr
from services.ai import ocr_engines
from services.ai import ocr_engines_admin
from services.ai import prompt_audit
from services.ai import prompt_cache
from services.ai import providers
from services.ai import usage as ai_usage
from services.ai.credentials import CredentialError
from services.ai.routing import RouteError
from typings.log import inject_log
from util.attachment_ocr import process_ocr
from util.object_id import (
    _authenticate,
    _log_level_scope,
    get_current_user,
    oauth2_scheme,
    validate_object_id,
)
from util.user import get_user_display_name, get_user_display_names

router = APIRouter()
_logger = logging.getLogger(__name__)

MAX_PROVIDER_ATTEMPTS = 2
RETRYABLE_CODES = frozenset({"AI_UPSTREAM_RATE_LIMIT", "AI_UPSTREAM_ERROR", "AI_TIMEOUT"})
REJECTED_CODES = frozenset({"AI_NO_PROVIDER", "AI_UPSTREAM_AUTH", "AI_UPSTREAM_REJECTED"})
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
}
STREAM_ERROR_FALLBACK = "AI_UPSTREAM_ERROR"
STREAM_DONE_FRAME = b"data: [DONE]\n\n"


def _stream_error_frame(code: str, message: str) -> bytes:
    """一帧 ``data: {"error": {...}}``：流内报错的唯一通道（形状对齐 OpenAI 面）。"""
    return (
        "data: "
        + json.dumps(
            {"error": {"code": code, "message": message}}, ensure_ascii=False
        )
        + "\n\n"
    ).encode("utf-8")


class OcrRequest(BaseModel):
    attachment_oid: str
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
    models: list[str] = Field(default_factory=list)
    api_key: str = Field(min_length=8, max_length=512)
    enabled: bool = True
    headers: dict[str, str] | None = None
    api_path: str | None = None
    # ISO 8601 字符串；不传 = 永不过期。解析与「必须晚于现在」的口径全在
    # ``credentials._clean_expires_at``（错误码 AI_KEY_BAD_EXPIRY）。
    expires_at: str | None = None


class KeyUpdateRequest(BaseModel):
    """部分更新：只改传了的字段，没传的保持原值。

    ``api_key`` 允许不传（改名字/换模型时不该被迫重填 key）；传了就是轮换。
    ``expires_at`` 用 ``exclude_unset`` 天然区分三种意图：不传 = 不改，传字符串
    = 设置，显式 ``null`` = 清除到期。
    """

    name: str | None = None
    base_url: str | None = None
    models: list[str] | None = None
    api_key: str | None = Field(default=None, min_length=8, max_length=512)
    enabled: bool | None = None
    headers: dict[str, str] | None = None
    api_path: str | None = None
    expires_at: str | None = None


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
        await quota.consume(user_oid, "chat", units, roles=roles)
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


@router.post("/volunteer-ocr", status_code=202)
async def request_volunteer_ocr(
    payload: VolunteerOcrRequest,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    verified = await _authenticate(payload.verification_token, "short", True)
    if (
        str(verified.get("id")) != str(user.get("id"))
        or verified.get("purpose") != "verify"
    ):
        raise HTTPException(status_code=403, detail="AI_PASSWORD_VERIFICATION_REQUIRED")
    return await request_ocr(
        OcrRequest(attachment_oid=payload.attachment_oid), background_tasks, user, log
    )


@router.post("/ocr", status_code=202)
async def request_ocr(
    payload: OcrRequest,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
    log=Depends(inject_log),
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
    oid = validate_object_id(payload.attachment_oid)
    try:
        data = await ai_ocr.enqueue(user, oid, provider=payload.provider)
    except RouteError as exc:
        if exc.status_code == 403:
            # 「非 admin 想指定引擎」也是拒绝路径：留一行流水，否则「有人在
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
    try:
        view = await credentials.update_entry(
            oid=target,
            actor=user,
            fields=payload.model_dump(exclude_unset=True),
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
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    _require_admin(user)
    target = validate_object_id(oid)
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
    """全部引擎（含 disabled）。响应里没有 ``secret_cipher``，只有 ``key_hint``。"""
    _require_admin(user)
    return {"status": "ok", "code": 200, "data": await ocr_engines_admin.list_engines()}


@router.post("/ocr-engines", status_code=201)
async def create_ocr_engine(
    payload: OcrEngineCreateRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    _require_admin(user)
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
    """当前用户本月额度快照：两种能力各一条，没有额度行就回默认上限。"""
    return {
        "status": "ok",
        "code": 200,
        "data": await quota.snapshot(user["id"], roles=user.get("per")),
    }


@router.get("/quota/{user_oid}")
async def user_quota(user_oid: str, user=Depends(get_current_user)):
    """admin 视角的同一份快照。

    ``ai_quota`` 没有指向用户的 FK，查无额度行是正常状态（本月还没用过），
    所以这里**不校验用户是否存在**，只校验 oid 形状。

    展示的「默认上限」必须与真实扣费口径一致：扣费走
    ``default_limit_for(用户的 roles, kind)``（admin 的 chat 是 1_000_000），所以
    先查目标用户角色再算，不能拿管理端的 ``per`` 也不能停在全局
    ``DEFAULT_QUOTA``（chat 200_000）——否则管理页会把一个 admin 的额度少显示
    一个数量级。查不到用户时回落全局默认，响应里的 ``source`` 会标
    ``global_default``，不编造角色。
    """
    _require_admin(user)
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
            # 冷却状态是**进程内**的：多 worker 各熔各的，所以显式标出作用域，
            # 免得运维把它当成全局事实。
            "cooldown_seconds": routing.cooldown_state(),
            "cooldown_scope": "process",
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
    """只列这个用户真的能调的模型：个人 key 的模型不该出现在别人的列表里。"""
    if not _has_ai_access(user):
        # OpenAI 兼容面不能抛 detail：客户端只认 {"error": {...}}。
        return _openai_error(
            403, "AI_FORBIDDEN", AI_FORBIDDEN_MESSAGE, request_id=uuid4().hex
        )
    entries = await credentials.catalog(user["id"])
    owners: dict[str, str] = {}
    for entry in entries:
        for model in entry.get("models") or []:
            if isinstance(model, str) and model:
                # 先出现的胜出：目录已按 personal → shared → server 排好序。
                owners.setdefault(model, str(entry.get("name") or ""))
    return {
        "object": "list",
        "data": [
            {"id": model, "object": "model", "owned_by": owner}
            for model, owner in owners.items()
        ],
    }


@router.post("/v1/chat/completions")
async def chat_completions(
    payload: dict = Body(...),
    user=Depends(get_ai_client),
    purpose: str | None = Depends(ai_scene),
):
    request_id = uuid4().hex
    user_oid = user["id"]
    # 角色快照只进审计（V033）：chat / OCR 不因它改变任何判定，缺省 NULL = 未采集。
    scene_roles = audit.roles_snapshot(user.get("per"))

    # prompt 受控留存（AI_PROMPT_AUDIT）：只在这里挂一次，覆盖 chat 与
    # markdown-assistant 两种 Scene（OCR 没有 prompt，不涉及）。是否真的留、留
    # 多少、怎么脱敏都由 services.ai.prompt_audit 决定，且它是旁路——任何失败
    # 都不会改变本请求的返回。放在门禁之前是有意的：被拒的请求同样可能带着
    # 需要复核的正文（「这个人反复尝试问什么」本身就是治理信号）。
    await prompt_audit.capture(
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

    available = await ai_config.allowed_models_for(user_oid)
    if not isinstance(model, str) or model not in available:
        await _reject_http(
            user_oid=user_oid,
            model=str(model or "") or None,
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

    # ``provider`` 不是 OpenAI 协议字段，只有 admin 能拿它做定向调试；普通用户
    # 传了也忽略（报错会让第三方 SDK 多一个必填字段，得不偿失）。
    forced = payload.get("provider") if _is_admin(user) else None

    try:
        selected = routing.candidates(
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

    candidates = [
        entry for entry in selected if model in (entry.get("models") or [])
    ][:MAX_PROVIDER_ATTEMPTS]
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
            rate_limit.check(
                f"{user_oid}:{key_oid}",
                "chat",
                limit=ai_config.api_key_rate_limit_per_minute(),
            )
        rate_limit.check(user_oid, "chat")
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
            user_oid, "chat", 1, roles=user.get("per")
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

    if bool(payload.get("stream")):
        return await _stream_completion(
            payload,
            model,
            candidates[0],
            user_oid,
            request_id,
            user.get("per"),
            purpose=purpose,
        )

    return await _blocking_completion(
        payload,
        model,
        candidates,
        user_oid,
        request_id,
        user.get("per"),
        purpose=purpose,
    )


async def _blocking_completion(
    payload: dict,
    model: str,
    candidates: list[dict],
    user_oid: str,
    request_id: str,
    roles: list[str] | None = None,
    purpose: str | None = None,
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
            routing.mark_failure(key)
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
                routing.mark_failure(key)
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

        routing.mark_success(key)
        # 上游回了 usage 就把 token 列一并落库：它们与 units 同源，报表才能分清
        # 「实扣」与「估算」（token 列全 NULL ⟺ units 是估算值）。
        tokens = _usage_fields(result.get("usage"))
        units = ai_usage.units_from_total_tokens(tokens["total_tokens"])
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


async def _stream_completion(
    payload: dict,
    model: str,
    entry: dict,
    user_oid: str,
    request_id: str,
    roles: list[str] | None = None,
    purpose: str | None = None,
):
    name = str(entry.get("name") or "")
    key = str(entry.get("key") or "")
    snapshot = audit.roles_snapshot(roles)

    try:
        api_key = await credentials.resolve_api_key(entry)
    except CredentialError as exc:
        routing.mark_failure(key)
        return await _reject(
            RouteError(exc.code, exc.message, exc.status_code),
            user_oid=user_oid,
            model=model,
            request_id=request_id,
            latency_ms=0,
            key_scope=entry.get("scope"),
            provider=name,
            purpose=purpose,
            attempt_no=1,
            roles_snapshot=snapshot,
        )

    # 并发闸门必须在返回 StreamingResponse 之前拿到：进了生成器再报 429 时响应头
    # 已经发出去了，客户端只会看到一个空流。
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
            latency_ms=0,
            key_scope=entry.get("scope"),
            provider=name,
            purpose=purpose,
            attempt_no=1,
            roles_snapshot=snapshot,
        )

    # 流式默认拿不到 usage（上游要 ``stream_options.include_usage`` 才在最后一帧
    # 报），所以先留一条估算路径；真正有没有 usage 由 StreamUsageTracker 决定。
    # 估算与实测的边界就是「token 列是否为 NULL」，见 services/ai/usage.py。
    estimated_prompt_chars = ai_usage.prompt_chars(payload)
    # 缓存注入只作用于发给上游的 body；``stream_options`` 由 providers 内部补，
    # 这里不重复补（两处都补会覆盖客户端自己的 stream_options 取值）。
    body, cache_key = prompt_cache.request_body(payload, entry)
    tracker = ai_usage.StreamUsageTracker()
    state: dict[str, Any] = {
        "chars": 0,
        "status": "success",
        "error_code": None,
        "first_token_ms": None,
    }

    async def generate():
        started = time.monotonic()
        try:
            async for chunk in providers.chat_completion_stream(body, entry, api_key):
                if state["first_token_ms"] is None and chunk:
                    # 「首块到达」而不是「首字生成」：一次 yield 就是上游吐出的一段
                    # SSE 字节，客户端正是在这一刻看到第一个字的传输开始。
                    state["first_token_ms"] = _elapsed_ms(started)
                state["chars"] += len(chunk)
                tracker.feed(chunk)
                yield chunk
        except Exception as exc:  # noqa: BLE001 - 已开始输出，只能收尾
            # 走到这里说明响应头（200 + text/event-stream）早就发出去了，状态码
            # 改不了。以前只写一条 warning 就结束生成器，客户端拿到的是
            # 「200 + 空 body」——前端只能猜成「被截断」。现在把失败作为一帧
            # 显式写进流里，已发过的正文保留，后面跟 error 帧 + [DONE] 收尾。
            raw_code = getattr(exc, "code", None)
            code = (
                raw_code
                if isinstance(raw_code, str) and raw_code in STREAM_ERROR_MESSAGES
                else STREAM_ERROR_FALLBACK
            )
            state["status"] = _audit_status(code)
            state["error_code"] = code
            routing.mark_failure(key)
            # 只记异常类名与码：异常 message 可能带上游原文，不能进日志。
            _logger.warning(
                "ai stream failed on %s: %s (%s)", name, type(exc).__name__, code
            )
            yield _stream_error_frame(code, STREAM_ERROR_MESSAGES[code])
            yield STREAM_DONE_FRAME
        else:
            routing.mark_success(key)
        finally:
            # 最后一帧可能没有空行收尾（上游在 [DONE] 后直接关连接），收尾解析一次。
            tracker.close()
            tokens = {
                "prompt_tokens": tracker.prompt_tokens,
                "completion_tokens": tracker.completion_tokens,
                "total_tokens": tracker.total_tokens,
                "cache_read_tokens": tracker.cache_read_tokens,
                "cache_write_tokens": tracker.cache_write_tokens,
            }
            if tracker.captured:
                units = ai_usage.units_from_total_tokens(tokens["total_tokens"])
            else:
                # 没有 usage：token 列留 NULL，units 按字符估算。这是「NULL ⟺
                # 估算」不变式的另一半。
                units = ai_usage.estimate_units(
                    estimated_prompt_chars, state["chars"]
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
                attempt_no=1,
                first_token_ms=state["first_token_ms"],
                roles_snapshot=snapshot,
                cache_key=cache_key,
                **tokens,
            )
            if _billable(entry):
                await _charge(user_oid, units, roles)
            # 流式请求的「完成」就在生成器收尾：此时流水已落库，计数包含本次。
            await _evaluate_burst(user_oid)
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
