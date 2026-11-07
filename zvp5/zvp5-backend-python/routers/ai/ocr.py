"""OCR 端点：附件入队、义工登记入口、站点内图片文件识别。"""

from __future__ import annotations

import asyncio
import os
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from routers.ai.common import (
    SCENE_OCR,
    VOLUNTEER_PROVIDER_ROLES,
    _has_ai_access,
    _reject_http,
)
from services import file_ops
from services.ai import audit
from services.ai import config as ai_config
from services.ai import ocr as ai_ocr
from services.ai.routing import RouteError
from typings.log import inject_log
from util.attachment_ocr import process_ocr
from util.object_id import (
    get_current_user,
    require_password_verification,
    validate_object_id,
)
from util.permission.features import ensure_feature

router = APIRouter()


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

