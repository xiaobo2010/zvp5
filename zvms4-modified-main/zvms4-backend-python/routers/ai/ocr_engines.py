"""OCR 引擎注册表端点（admin；配置全站生效）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from routers.ai.common import VOLUNTEER_PROVIDER_ROLES, _is_admin, _require_admin
from services.ai import ocr_engines, ocr_engines_admin
from services.ai.routing import RouteError
from typings.log import inject_log
from util.object_id import get_current_user, validate_object_id

router = APIRouter()


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
