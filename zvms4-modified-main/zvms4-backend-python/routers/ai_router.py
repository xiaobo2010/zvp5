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
import math
import time
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from services.ai import audit, credentials, quota, rate_limit, routing
from services.ai import config as ai_config
from services.ai import ocr as ai_ocr
from services.ai import ocr_engines_admin
from services.ai import providers
from services.ai.credentials import CredentialError
from services.ai.routing import RouteError
from typings.log import inject_log
from util.attachment_ocr import process_ocr
from util.object_id import get_current_user, validate_object_id

router = APIRouter()
_logger = logging.getLogger(__name__)

MAX_PROVIDER_ATTEMPTS = 2
RETRYABLE_CODES = frozenset({"AI_UPSTREAM_RATE_LIMIT", "AI_UPSTREAM_ERROR", "AI_TIMEOUT"})
REJECTED_CODES = frozenset({"AI_NO_PROVIDER", "AI_UPSTREAM_AUTH", "AI_UPSTREAM_REJECTED"})
PERSONAL_SCOPE = credentials.SCOPE_PERSONAL


class OcrRequest(BaseModel):
    attachment_oid: str
    # 预留给以后按请求指定引擎；实际引擎由 ai_ocr_engines 注册表按 priority 选，
    # 字段收下但不影响路由（也不允许绕过站点额度）。
    provider: str | None = None


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


class KeyUpdateRequest(BaseModel):
    """部分更新：只改传了的字段，没传的保持原值。

    ``api_key`` 允许不传（改名字/换模型时不该被迫重填 key）；传了就是轮换。
    """

    name: str | None = None
    base_url: str | None = None
    models: list[str] | None = None
    api_key: str | None = Field(default=None, min_length=8, max_length=512)
    enabled: bool | None = None
    headers: dict[str, str] | None = None
    api_path: str | None = None


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


def _units_from_usage(usage: object) -> int:
    total = 0
    if isinstance(usage, dict):
        try:
            total = int(usage.get("total_tokens") or 0)
        except (TypeError, ValueError):
            total = 0
    return max(1, math.ceil(total / 1000))


def _is_admin(user: dict) -> bool:
    return "admin" in (user.get("per") or [])


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
) -> JSONResponse:
    await audit.record(
        user_oid=user_oid,
        capability="chat",
        endpoint=audit.ENDPOINT_CHAT,
        model=model,
        status=_audit_status(exc.code),
        http_status=exc.status_code,
        error_code=exc.code,
        latency_ms=latency_ms,
        request_id=request_id,
        key_scope=key_scope,
    )
    return _openai_error(
        exc.status_code, exc.code, exc.message, request_id=request_id
    )


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------


@router.post("/ocr", status_code=202)
async def request_ocr(
    payload: OcrRequest,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    _require_ai_access(user)
    oid = validate_object_id(payload.attachment_oid)
    try:
        data = await ai_ocr.enqueue(user, oid)
    except RouteError as exc:
        # 仓库约定：``/api/ai/*`` 的普通端点用 ``detail`` + 状态码。
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None
    # ``engine_kind`` 只服务于后台任务（入队与处理必须是同一个引擎），不对外暴露。
    engine_kind = data.pop("engine_kind", None)
    if data.get("status") == "pending":
        background_tasks.add_task(process_ocr, oid, engine_kind, data.get("engine"))

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
    """
    _require_admin(user)
    target = validate_object_id(user_oid)
    return {"status": "ok", "code": 200, "data": await quota.admin_snapshot(target)}


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


@router.get("/health")
async def ai_health(user=Depends(get_current_user)):
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

    return {
        "status": "ok",
        "code": 200,
        "data": {
            "enabled": ai_config.is_enabled(),
            "max_concurrency": ai_config.max_concurrency(),
            "providers": ai_config.public_provider_view(),
            "routes": ai_config.routes(),
            "cooldown_seconds": routing.cooldown_state(),
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
async def list_models(user=Depends(get_current_user)):
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
    user=Depends(get_current_user),
):
    request_id = uuid4().hex
    user_oid = user["id"]

    if not _has_ai_access(user):
        return _openai_error(
            403, "AI_FORBIDDEN", AI_FORBIDDEN_MESSAGE, request_id=request_id
        )

    if not ai_config.is_enabled():
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
        )

    available = await ai_config.allowed_models_for(user_oid)
    if not isinstance(model, str) or model not in available:
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
            exc, user_oid=user_oid, model=model, request_id=request_id, latency_ms=0
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
        )

    # 分钟级频控：月额度防长期滥用，这一层防「一个用户在一分钟内把单 worker 打满」。
    # 位置在配额预检之前、角色门禁之后——被门禁拒掉的请求不计数。
    try:
        rate_limit.check(user_oid, "chat")
    except rate_limit.RateLimitError as exc:
        return _rate_limited(exc, request_id=request_id)

    # 先只读预检：明显超额的请求不该先花掉上游成本再被拒。个人 key 由管理员
    # 自己付费，不占平台额度，所以整段配额逻辑对它跳过；预检只看首个候选的
    # scope——真落到共享项时，扣减本身仍是原子的，不会超卖。
    if _billable(candidates[0]):
        allowed, used, limit = await quota.peek(
            user_oid, "chat", 1, roles=user.get("per")
        )
        if not allowed:
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
        )

    return await _blocking_completion(
        payload,
        model,
        candidates,
        user_oid,
        request_id,
        user.get("per"),
    )


async def _blocking_completion(
    payload: dict,
    model: str,
    candidates: list[dict],
    user_oid: str,
    request_id: str,
    roles: list[str] | None = None,
) -> JSONResponse:
    started = time.monotonic()
    last_error: RouteError | None = None

    for entry in candidates:
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
            )

        try:
            async with routing.slot():
                result = await providers.chat_completion(payload, entry, api_key)
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
            )
            continue

        routing.mark_success(key)
        units = _units_from_usage(result.get("usage"))
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


async def _stream_completion(
    payload: dict,
    model: str,
    entry: dict,
    user_oid: str,
    request_id: str,
    roles: list[str] | None = None,
):
    name = str(entry.get("name") or "")
    key = str(entry.get("key") or "")

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
        )

    # 流式没有 usage 可读，按字符估 token：4 字符 ≈ 1 token。
    prompt_chars = len(json.dumps(payload.get("messages") or [], ensure_ascii=False))
    state = {"chars": 0, "status": "success", "error_code": None}

    async def generate():
        started = time.monotonic()
        try:
            async for chunk in providers.chat_completion_stream(
                payload, entry, api_key
            ):
                state["chars"] += len(chunk)
                yield chunk
        except Exception as exc:  # noqa: BLE001 - 已开始输出，只能收尾
            code = getattr(exc, "code", "AI_STREAM_ERROR")
            state["status"] = _audit_status(code)
            state["error_code"] = code
            routing.mark_failure(key)
            _logger.warning("ai stream failed on %s: %s", name, type(exc).__name__)
        else:
            routing.mark_success(key)
        finally:
            units = max(1, math.ceil(prompt_chars / 4 + state["chars"] / 4))
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
            )
            if _billable(entry):
                await _charge(user_oid, units, roles)
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
