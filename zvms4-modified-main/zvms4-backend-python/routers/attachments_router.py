"""Files / attachments HTTP layer (design ``docs/FILES_API_DESIGN.md`` §4).

Prefix ``/api/attachments`` (mounted in ``main.py``). Every response body is
inlined ``{"status", "code", "data"}`` — the shared response helper in
``util/response.py`` is deliberately not used (it is broken, design C13).

Permission matrix (design §4):
* read  = owner | admin | department | secretary-in-same-class | valid signature
* write = admin | department (or owner for delete/link)
* list ``scope=all`` is gated server-side, never on the client param.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
)
from pydantic import BaseModel
from starlette.datastructures import UploadFile

from db import backends
from services.ai import ocr as ai_ocr
from services.ai.routing import RouteError
from typings.log import ZVMSLog, inject_log, xuehai_id_from
from util.attachment import (
    ALLOWED_PURPOSES,
    DAILY_MAX_BYTES,
    DAILY_MAX_FILES,
    MAX_BYTES,
    AttachmentError,
    QuotaExceeded,
    can_read_attachment,
    check_quota,
    ext_for_mime,
    make_signed_url,
    process_upload,
    read_capped,
    record_upload,
    sanitize_filename,
    verify_signed,
)
from util.attachment_ocr import process_ocr
from util.ban import ban_is_active, raise_banned
from util.object_id import get_current_user, optional_current_user, validate_object_id

# ---------------------------------------------------------------------------
# Device gate
# ---------------------------------------------------------------------------


async def reject_xuehai_device(request: Request) -> None:
    """Policy gate: Xuehai devices may not touch the media library at all.

    A request whose ``Xuehai-Id`` header parses to a positive integer is
    treated as coming from a Xuehai device (the frontend only injects this
    header when it self-identifies as one). Missing / empty / non-positive or
    non-integer values pass through unchanged.
    """
    raw = xuehai_id_from(request)
    if raw.isdecimal() and int(raw) > 0:
        raise HTTPException(status_code=403, detail="学海设备不支持访问媒体库")


router = APIRouter(dependencies=[Depends(reject_xuehai_device)])

# Only these statuses are ever served; deleted/quarantined/rejected are 410 even
# with a valid signature (design §4 #2 / §6.2).
SERVE_STATUSES = {"stored", "ready", "verified"}

_BYTE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'",
}


class VerifyRequest(BaseModel):
    decision: str
    note: str = ""


class BindRequest(BaseModel):
    entity_type: str
    entity_oid: str


# ---------------------------------------------------------------------------
# Permission helpers
# ---------------------------------------------------------------------------


def _per(user: dict) -> list[str]:
    return user.get("per") or []


def _is_manager(user: dict) -> bool:
    per = _per(user)
    return "admin" in per or "department" in per


def _is_admin(user: dict) -> bool:
    return "admin" in _per(user)


def _require_manager(user: dict) -> None:
    if not _is_manager(user):
        raise HTTPException(status_code=403, detail="Permission denied")


def _require_owner_or_manager(user: dict, row: dict) -> None:
    if str(row.get("owner_oid") or "") == user["id"] or _is_manager(user):
        return
    raise HTTPException(status_code=403, detail="Permission denied")


async def _can_read(user: dict, row: dict, class_id: str | None) -> bool:
    """薄包装：实现在 ``util.attachment.can_read_attachment``。

    抽出去是因为 AI 网关的 OCR 入口需要同一条判定；调用点保持不变。
    """
    return await can_read_attachment(user, row, class_id)


# ---------------------------------------------------------------------------
# Serialization / headers
# ---------------------------------------------------------------------------


def _iso(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _serialize_meta(row: dict, *, include_sha: bool) -> dict:
    data = {
        "_id": str(row.get("_id") or ""),
        "kind": row.get("kind"),
        "mime": row.get("mime"),
        "size_bytes": int(row.get("size_bytes") or 0),
        "width": row.get("width"),
        "height": row.get("height"),
        "purpose": row.get("purpose"),
        "original_name": row.get("original_name"),
        "entity_type": row.get("entity_type"),
        "entity_oid": row.get("entity_oid"),
        "status": row.get("status"),
        "ocr_status": row.get("ocr_status"),
        "created_at": _iso(row.get("created_at")),
    }
    if include_sha:
        data["sha256"] = row.get("sha256")
    return data


def _serialize_summary(row: dict) -> dict:
    data = _serialize_meta(row, include_sha=False)
    data["owner_oid"] = row.get("owner_oid")
    return data


def _byte_headers(oid: str, row: dict) -> dict:
    mime = row.get("mime") or "application/octet-stream"
    headers = dict(_BYTE_HEADERS)
    headers["Content-Disposition"] = f'inline; filename="{oid}.{ext_for_mime(mime)}"'
    return headers


def _require_servable(row: dict) -> None:
    if str(row.get("status") or "") not in SERVE_STATUSES:
        raise HTTPException(status_code=410, detail="Attachment unavailable")


async def _log_byte_access(request: Request, user: dict | None, oid: str) -> None:
    """Audit a byte read. Records the PATH only: the ``sig`` query param is a
    reusable credential and must never reach the log (design §8.5 / P1-3)."""
    try:
        log = ZVMSLog(
            url=request.url.path,
            user=(user or {}).get("id", ""),
            timestamp=datetime.timestamp(datetime.now()),
            type="attachment",
            xuehai_id=xuehai_id_from(request),
        )
        log.with_text(f"读取附件字节 {oid}")
        await log.insert_log()
    except Exception:
        pass


async def _authorize_byte_request(
    request: Request,
    oid: str,
    user: dict | None,
    exp: str | None,
    purpose: str | None,
    sig: str | None,
) -> dict:
    """Resolve the row for a byte request and enforce read access + serve gate.

    Bearer path: fetch meta, then check read permission. Signature path: verify
    the signature *before* touching the DB (design §6.2 verification order), so
    an invalid signature is a uniform 403 regardless of ``oid`` existence.
    """
    repo = backends.get_files_repo()
    if user is not None:
        row = await repo.get_meta(oid)
        if row is None:
            raise HTTPException(status_code=404, detail="Attachment not found")
        if not await _can_read(user, row, user.get("class_id")):
            raise HTTPException(status_code=403, detail="Permission denied")
    else:
        if not (exp and purpose and sig):
            raise HTTPException(
                status_code=401,
                detail="Authentication required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not verify_signed(oid, exp, purpose, sig):
            raise HTTPException(status_code=403, detail="Invalid signature")
        row = await repo.get_meta(oid)
        if row is None:
            raise HTTPException(status_code=404, detail="Attachment not found")
    return row


# ---------------------------------------------------------------------------
# 1. Upload
# ---------------------------------------------------------------------------


@router.post("")
async def upload_attachment(
    request: Request,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    # Re-check `banned` at the DB layer: long-lived tokens survive a ban
    # (design §8.7 / P2-7). 只看列会误伤到期封禁，判定统一走 ban_is_active。
    me = await backends.get_users_repo().get_by_oid(user["id"])
    if ban_is_active(me):
        raise_banned(me)

    # Uploading is a student-union-or-above action. Both frontend entry points
    # (the activity-creation form and the attachment dialog) are manager-only,
    # so enforce the same rule here instead of relying on the UI alone.
    _require_manager(user)

    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > MAX_BYTES:
                raise HTTPException(status_code=413, detail="Payload too large")
        except ValueError:
            pass

    try:
        form = await request.form(max_files=5, max_fields=10)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid multipart payload")

    upload = form.get("file")
    # `fastapi.UploadFile` is a *subclass* of the Starlette one, but a form read
    # by hand through `request.form()` always yields the Starlette base class:
    # FastAPI only rewraps on the declarative `File(...)` injection path. Testing
    # against the base class accepts both, so a real file part gets through
    # (`isinstance(upload, fastapi.UploadFile)` is always False here).
    if not isinstance(upload, UploadFile):
        raise HTTPException(status_code=400, detail="Missing file part")

    purpose = form.get("purpose") or "volunteer_order"
    if purpose not in ALLOWED_PURPOSES:
        raise HTTPException(status_code=400, detail="Invalid purpose")

    entity_type = form.get("entity_type")
    entity_oid = form.get("entity_oid")
    if (entity_type or entity_oid) and not _is_manager(user):
        raise HTTPException(
            status_code=400, detail="Entity binding is not permitted"
        )

    original_name = sanitize_filename(form.get("original_name") or upload.filename)

    try:
        check_quota(user["id"])
        raw = await read_capped(upload, MAX_BYTES)
        processed = await process_upload(raw)
    except QuotaExceeded:
        raise HTTPException(
            status_code=429,
            detail=(
                f"今日上传配额已用尽（每日上限 {DAILY_MAX_FILES} 个 / "
                f"{DAILY_MAX_BYTES // (1024 * 1024)} MB），请明天再试"
            ),
        )
    except AttachmentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    repo = backends.get_files_repo()

    # Same-owner dedup only (design §8.6); never cross-owner.
    try:
        existing = await repo.find_by_sha256(user["id"], processed.sha256, "original")
    except Exception:
        existing = None
    if existing is not None:
        log.with_text(f"用户 {user['id']} 命中去重附件 {existing.get('_id')}")
        await log.insert_log()
        return {
            "status": "ok",
            "code": 200,
            "data": _serialize_meta(existing, include_sha=True),
        }

    oid = await repo.create(
        user["id"],
        "original",
        None,
        processed.mime,
        processed.size,
        processed.width,
        processed.height,
        processed.sha256,
        processed.content,
        original_name,
        purpose,
    )
    thumb_oid = await repo.create(
        user["id"],
        "thumb",
        oid,
        processed.thumb_mime,
        processed.thumb_size,
        processed.thumb_width,
        processed.thumb_height,
        processed.thumb_sha256,
        processed.thumb,
        original_name,
        purpose,
    )
    if entity_type and entity_oid:
        await repo.set_entity(oid, str(entity_type), str(entity_oid))
    await repo.add_event(oid, user["id"], "upload", None, "stored", "")
    record_upload(user["id"], processed.size)

    log.with_text(f"用户 {user['id']} 上传附件 {oid}")
    await log.insert_log()

    row = await repo.get_meta(oid)
    data = _serialize_meta(row or {"_id": oid}, include_sha=True)
    data["thumb_oid"] = thumb_oid
    return {"status": "ok", "code": 201, "data": data}


# ---------------------------------------------------------------------------
# 4. List
# ---------------------------------------------------------------------------


@router.get("")
async def list_attachments(
    user=Depends(get_current_user),
    log=Depends(inject_log),
    scope: str = Query("mine"),
    purpose: str | None = Query(None),
    status: str | None = Query(None),
    entity_type: str | None = Query(None),
    entity_oid: str | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    if scope == "all":
        # Never trust the client param: gate `all` server-side (design §4 #4).
        if not _is_manager(user):
            raise HTTPException(status_code=403, detail="Permission denied")
        owner: str | None = None
    else:
        owner = user["id"]

    rows, total = await backends.get_files_repo().list_page(
        owner, purpose, status, entity_type, entity_oid, page, size
    )
    return {
        "status": "ok",
        "code": 200,
        "data": [_serialize_summary(row) for row in rows],
        "metadata": {"size": total},
    }


# ---------------------------------------------------------------------------
# 2. Bytes  /  5. Thumbnail
# ---------------------------------------------------------------------------


@router.get("/{oid}")
async def get_attachment_bytes(
    oid: str,
    request: Request,
    exp: str | None = Query(None),
    purpose: str | None = Query(None),
    sig: str | None = Query(None),
    user=Depends(optional_current_user),
):
    oid = validate_object_id(oid)
    row = await _authorize_byte_request(request, oid, user, exp, purpose, sig)
    _require_servable(row)

    content = await backends.get_files_repo().get_content(oid)
    if content is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    await _log_byte_access(request, user, oid)
    return Response(
        content=content,
        media_type=row.get("mime") or "application/octet-stream",
        headers=_byte_headers(oid, row),
    )


@router.get("/{oid}/thumb")
async def get_attachment_thumb(
    oid: str,
    request: Request,
    exp: str | None = Query(None),
    purpose: str | None = Query(None),
    sig: str | None = Query(None),
    user=Depends(optional_current_user),
):
    oid = validate_object_id(oid)
    parent = await _authorize_byte_request(request, oid, user, exp, purpose, sig)
    _require_servable(parent)

    repo = backends.get_files_repo()
    thumb = await repo.thumb_for(oid)
    if thumb is None:
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    _require_servable(thumb)

    thumb_oid = str(thumb.get("_id") or "")
    content = await repo.get_content(thumb_oid)
    if content is None:
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    await _log_byte_access(request, user, oid)
    return Response(
        content=content,
        media_type=thumb.get("mime") or "image/jpeg",
        headers=_byte_headers(thumb_oid, thumb),
    )


# ---------------------------------------------------------------------------
# 3. Metadata
# ---------------------------------------------------------------------------


@router.get("/{oid}/meta")
async def get_attachment_meta(
    oid: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    oid = validate_object_id(oid)
    row = await backends.get_files_repo().get_meta(oid)
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    if not await _can_read(user, row, user.get("class_id")):
        raise HTTPException(status_code=403, detail="Permission denied")
    # sha256 is only for the owner (design §8.6 / P1-5).
    include_sha = str(row.get("owner_oid") or "") == user["id"]
    return {
        "status": "ok",
        "code": 200,
        "data": _serialize_meta(row, include_sha=include_sha),
    }


# ---------------------------------------------------------------------------
# 6. Signed link
# ---------------------------------------------------------------------------


@router.post("/{oid}/link")
async def create_attachment_link(
    oid: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
    ttl: int = Query(3600),
):
    oid = validate_object_id(oid)
    repo = backends.get_files_repo()
    row = await repo.get_meta(oid)
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    _require_owner_or_manager(user, row)

    url, expires_at = make_signed_url(oid, ttl)
    await repo.add_event(oid, user["id"], "link", row.get("status"), row.get("status"), "")
    log.with_text(f"用户 {user['id']} 签发附件外链 {oid}")
    await log.insert_log()
    return {
        "status": "ok",
        "code": 200,
        "data": {"url": url, "expires_at": expires_at},
    }


# ---------------------------------------------------------------------------
# 7. OCR enqueue  /  8. OCR result
# ---------------------------------------------------------------------------


@router.post("/{oid}/ocr")
async def request_attachment_ocr(
    oid: str,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """触发附件 OCR：与 ``/api/ai/ocr`` 共用同一套引擎池。

    早期这里是一条 manager 专属的排障通道——自选第一个 enabled 引擎，且不扣用户
    额度也不扣站点额度，等于给附件 API 留了个免费 OCR 后门。现在两条入口同样计价：
    频控 → 用户额度 peek/consume → 按 priority 选「站点额度还够」的引擎 → 入队。

    引擎在入队时就定下来，后台任务第一次尝试必须复用同一个（``engine_kind``），
    否则识别会花掉 A 引擎的站点额度却打到 B 引擎上；A 失败之后才按同一套
    ``priority`` 顺序回退，回退的每一次尝试另扣一份对应的站点额度。
    """
    _require_manager(user)
    oid = validate_object_id(oid)

    try:
        data = await ai_ocr.enqueue(user, oid)
    except RouteError as exc:
        # 与 ``/api/ai/*`` 对齐：detail 用 code，客户端按 code 分支。
        raise HTTPException(status_code=exc.status_code, detail=exc.code) from None

    # ``engine_kind`` 只服务于后台任务，不对外暴露。
    engine_kind = data.pop("engine_kind", None)
    if data.get("status") == "pending":
        # 第 4 个参数只用于逐次尝试的审计归属（谁触发的这次识别）。
        background_tasks.add_task(
            process_ocr, oid, engine_kind, data.get("engine"), user["id"]
        )

    log.with_text(
        f"用户 {user['id']} 触发附件 OCR {oid}"
        f"（引擎 {data.get('engine') or '-'}，状态 {data.get('status')}）",
        type="attachment_ocr_enqueue",
    )
    await log.insert_log()

    if data.get("status") == "processing":
        # 幂等分支：这次没有新受理任何东西（也没扣额度），200 表示「已经是这个状态」。
        return {"status": "ok", "code": 200, "data": data}

    return {"status": "ok", "code": 202, "data": data}


@router.get("/{oid}/ocr")
async def get_attachment_ocr(
    oid: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    oid = validate_object_id(oid)
    repo = backends.get_files_repo()
    row = await repo.get_meta(oid)
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")
    _require_owner_or_manager(user, row)

    ocr = await repo.get_ocr(oid)
    return {
        "status": "ok",
        "code": 200,
        "data": {
            "status": row.get("ocr_status"),
            "engine": ocr.get("engine") if ocr else None,
            "engine_version": ocr.get("engine_version") if ocr else None,
            "text": ocr.get("text") if ocr else None,
            "confidence": ocr.get("confidence") if ocr else None,
            "updated_at": _iso(ocr.get("updated_at")) if ocr else None,
        },
    }


# ---------------------------------------------------------------------------
# 9. Verify  /  10. Bind
# ---------------------------------------------------------------------------


@router.post("/{oid}/verify")
async def verify_attachment(
    oid: str,
    payload: VerifyRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    _require_manager(user)
    if payload.decision not in ("verified", "rejected"):
        raise HTTPException(status_code=400, detail="Invalid decision")
    oid = validate_object_id(oid)
    repo = backends.get_files_repo()
    row = await repo.get_meta(oid)
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    previous = row.get("status")
    await repo.set_status(oid, payload.decision)
    await repo.add_event(
        oid, user["id"], "verify", previous, payload.decision, payload.note or ""
    )
    log.with_text(f"用户 {user['id']} 查证附件 {oid} -> {payload.decision}")
    await log.insert_log()
    return {"status": "ok", "code": 200, "data": {"status": payload.decision}}


@router.post("/{oid}/bind")
async def bind_attachment(
    oid: str,
    payload: BindRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    _require_manager(user)
    if not payload.entity_type or not payload.entity_oid:
        raise HTTPException(status_code=400, detail="entity_type and entity_oid required")
    oid = validate_object_id(oid)
    repo = backends.get_files_repo()
    row = await repo.get_meta(oid)
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    await repo.set_entity(oid, payload.entity_type, payload.entity_oid)
    await repo.add_event(
        oid, user["id"], "bind", row.get("status"), row.get("status"), payload.entity_oid
    )
    log.with_text(f"用户 {user['id']} 绑定附件 {oid}")
    await log.insert_log()
    return {
        "status": "ok",
        "code": 200,
        "data": {
            "entity_type": payload.entity_type,
            "entity_oid": payload.entity_oid,
        },
    }


# ---------------------------------------------------------------------------
# 11. Soft delete
# ---------------------------------------------------------------------------


@router.delete("/{oid}")
async def delete_attachment(
    oid: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    oid = validate_object_id(oid)
    repo = backends.get_files_repo()
    row = await repo.get_meta(oid)
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    owner = str(row.get("owner_oid") or "") == user["id"]
    if not owner and not _is_manager(user):
        raise HTTPException(status_code=403, detail="Permission denied")
    # An owner or a manager (admin | department) may delete only unverified
    # material (design §4 #11); verified/rejected stay undeletable for both,
    # admins only keep their override.
    if not _is_admin(user) and str(row.get("status") or "") in (
        "verified",
        "rejected",
    ):
        raise HTTPException(status_code=403, detail="Attachment is verified")

    previous = row.get("status")
    await repo.soft_delete(oid)
    await repo.add_event(oid, user["id"], "delete", previous, "deleted", "")
    log.with_text(f"用户 {user['id']} 删除附件 {oid}")
    await log.insert_log()
    return {"status": "ok", "code": 200, "data": {"status": "deleted"}}
