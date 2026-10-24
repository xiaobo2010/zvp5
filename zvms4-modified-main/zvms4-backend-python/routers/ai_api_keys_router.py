"""独立 API key 的 HTTP 层（``/api/ai/api-keys``）。

这一面只做协议翻译：谁能动谁的 key 由 ``services.ai.api_keys`` 判定，明文 token
只在创建响应里出现一次。

挂载点：``main.py`` → ``app.include_router(..., prefix="/api/ai/api-keys")``。
与 ``/api/ai/*`` 其余端点共用仓库约定 ``{"status","code","data"}`` + ``detail``
错误码（客户端按 code 分支）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from services.ai import api_keys
from typings.log import inject_log
from util.object_id import get_current_user, validate_object_id

router = APIRouter()


class ApiKeyCreateRequest(BaseModel):
    """``expires_at`` 是 ISO 8601 字符串；不传 = 永不过期。

    校验在服务层（错误码要稳定成 ``AI_API_KEY_BAD_EXPIRY``），不放这里——否则
    非法输入回的是 pydantic 的 422 数组，客户端还得解析 detail。
    """

    name: str
    expires_at: str | None = None


class ApiKeyUpdateRequest(BaseModel):
    """部分更新：只改传了的字段。

    ``expires_at`` 用 ``exclude_unset`` 区分三种意图：不传 = 不改，传字符串 =
    设置，显式 ``null`` = 清除到期（永不过期）。
    """

    name: str | None = None
    enabled: bool | None = None
    expires_at: str | None = None


def _is_admin(user: dict) -> bool:
    return "admin" in (user.get("per") or [])


def _api_key_error(exc: api_keys.ApiKeyError) -> HTTPException:
    """服务层错误码 → HTTP；``detail`` 用 code（客户端按 code 分支）。"""
    return HTTPException(status_code=exc.status_code, detail=exc.code)


@router.get("")
async def list_api_keys(
    user_oid: str | None = Query(None, description="admin only: another user's keys"),
    user=Depends(get_current_user),
):
    """列出 key 视图（**永不回 token / token_hash**）。

    默认只列自己的；``?user_oid=`` 是 admin 的只读视角（排障「这个人到底有多少
    把 key」），普通用户带了别人的 oid 一律 403。
    """
    if user_oid is None:
        target = user["id"]
    else:
        target = validate_object_id(user_oid)
        if target != user["id"] and not _is_admin(user):
            raise HTTPException(status_code=403, detail="AI_FORBIDDEN")

    return {
        "status": "ok",
        "code": 200,
        "data": await api_keys.list_for(target),
    }


@router.post("", status_code=201)
async def create_api_key(
    payload: ApiKeyCreateRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """签发一把 key：``data.token`` 是**唯一一次**能看到明文的机会。"""
    try:
        view = await api_keys.create(user["id"], payload.name, payload.expires_at)
    except api_keys.ApiKeyError as exc:
        raise _api_key_error(exc) from None

    log.with_text(
        f"AI API key 新增 {view['name']}（{view['key_hint']}）",
        type="ai_api_key_create",
    )
    await log.insert_log()
    return {"status": "ok", "code": 201, "data": view}


@router.patch("/{oid}")
async def update_api_key(
    oid: str,
    payload: ApiKeyUpdateRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """改名字 / 启停 / 到期时间；owner 或 admin。"""
    target = validate_object_id(oid)
    try:
        view = await api_keys.update(
            target, user, payload.model_dump(exclude_unset=True)
        )
    except api_keys.ApiKeyError as exc:
        raise _api_key_error(exc) from None

    log.with_text(
        f"AI API key 更新 {view['name']}（{view['key_hint']}）",
        type="ai_api_key_update",
    )
    await log.insert_log()
    return {"status": "ok", "code": 200, "data": view}


@router.delete("/{oid}", status_code=204)
async def delete_api_key(
    oid: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """吊销一把 key：立即从进程内校验缓存里消失（别的 worker 最晚一个 TTL 后）。"""
    target = validate_object_id(oid)
    try:
        await api_keys.delete(target, user)
    except api_keys.ApiKeyError as exc:
        raise _api_key_error(exc) from None

    log.with_text(f"AI API key 吊销 {target}", type="ai_api_key_delete")
    await log.insert_log()
    return None
