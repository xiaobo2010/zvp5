"""AI 凭据管理端点（admin）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from routers.ai.common import _credential_error, _require_admin
from services.ai import credentials
from services.ai.credentials import CredentialError
from typings.log import inject_log
from util.object_id import (
    get_current_user,
    require_password_verification,
    validate_object_id,
)

router = APIRouter()


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

