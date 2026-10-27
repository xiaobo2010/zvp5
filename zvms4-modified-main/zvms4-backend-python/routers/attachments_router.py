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

from datetime import datetime, timedelta, timezone

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
    MAX_BYTES,
    AttachmentError,
    QuotaExceeded,
    can_read_attachment,
    check_quota,
    effective_daily_limits,
    ext_for_mime,
    make_signed_url,
    process_upload,
    read_capped,
    record_upload,
    sanitize_filename,
    verify_signed,
)
from util.attachment_ocr import LINES_KEY, process_ocr
from util.ban import ban_is_active, raise_banned
from util.cert import jwt_decode
from util.object_id import get_current_user, optional_current_user, validate_object_id

# ---------------------------------------------------------------------------
# Device gate
# ---------------------------------------------------------------------------

# 学海设备的解锁窗口：临时凭证本身只有 5 分钟寿命，直链不能活得比它更久。
XUEHAI_UNLOCK_TTL = 300

DEVICE_LOCKED_DETAIL = "学海设备需要密码解锁后访问媒体库"


def _is_xuehai_device(request: Request) -> bool:
    """``Xuehai-Id`` 能解析成正整数才算学海设备。"""
    raw = xuehai_id_from(request)
    return raw.isdecimal() and int(raw) > 0


def _decode_quietly(token: str) -> dict | None:
    """解码 JWT，任何异常都只是「无效」，绝不向调用方抛错。"""
    try:
        payload = jwt_decode(token)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _valid_temporary_token(token: str, request: Request) -> bool:
    """校验 5 分钟临时凭证：scope 正确、未过期、且与当前登录用户同一个人。

    同人校验的意义：临时凭证是「证明你本人刚输入过密码」的票据，如果允许
    拿 A 的票据给 B 的 access token 解锁，闸门就形同虚设。
    """
    payload = _decode_quietly(token)
    if payload is None:
        return False
    if payload.get("scope") != "temporary_token":
        return False
    # PyJWT 默认已校验 exp，这里再判断一次，避免上游改配置后静默失效。
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return False
    if exp <= datetime.now(timezone.utc).timestamp():
        return False

    authorization = (request.headers.get("Authorization") or "").strip()
    if not authorization:
        return True
    scheme, _, access_token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not access_token.strip():
        return False
    access_payload = _decode_quietly(access_token.strip())
    if access_payload is None:
        return False
    return str(access_payload.get("sub") or "") == str(payload.get("sub") or "")


async def require_xuehai_unlock(request: Request) -> None:
    """学海设备访问媒体库的门禁：无设备头放行，有设备头需要密码解锁。

    放行条件（依次判断）：
    * 请求不带 ``Xuehai-Id``：非学海设备，行为和以前完全一致；
    * ``X-Device-Unlock`` 是有效的 5 分钟临时凭证：用户刚校验过密码；
    * ``GET`` 且带 ``sign`` 查询参数：签名直链本身是解锁后换来的能力，
      而 ``<img>`` 之类的加载没法带自定义请求头，端点会自行验签；
    * 其余一律 403，前端据此提示输入密码。
    """
    if not _is_xuehai_device(request):
        return
    token = (request.headers.get("X-Device-Unlock") or "").strip()
    if token and _valid_temporary_token(token, request):
        return
    if request.method == "GET" and (request.query_params.get("sign") or "").strip():
        return
    raise HTTPException(status_code=403, detail=DEVICE_LOCKED_DETAIL)


router = APIRouter(dependencies=[Depends(require_xuehai_unlock)])

# Only these statuses are ever served; deleted/quarantined/rejected are 410 even
# with a valid signature (design §4 #2 / §6.2).
SERVE_STATUSES = {"stored", "ready", "verified"}

# --- 管理员审计面（列表过滤 + 单条详情 + 状态裁决）---------------------------
# 列表过滤的取值一律白名单校验：一个拼错的 ``kind`` 应当回 400，而不是静默
# 返回空页（审计界面会把它当成「这批素材不存在」）。
OCR_STATUSES = {"pending", "processing", "done", "failed"}
FILE_KINDS = {"original", "thumb", "derived"}

# 状态裁决只允许这两个终态；``deleted`` 不在其中——墓碑的字节可能已被保留期任务
# 清空，把它改回 verified 会返回一个 410 的「已验证」附件。
AUDIT_STATUSES = {"verified", "rejected"}
AUDIT_ACTION_BY_STATUS = {"verified": "audit_verified", "rejected": "audit_rejected"}

# ``FILE_EVENTS.detail`` 存的是 JSON/CLOB；审计备注必须截断，否则一条 10 万字
# 的备注会跟着每次时间线读取一起回到前端。
AUDIT_NOTE_MAX = 500

# 单条审计详情的审计时间线窗口（最近 50 条，仍按时间正序返回）。
AUDIT_EVENT_LIMIT = 50

_BYTE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "private",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'",
}


class VerifyRequest(BaseModel):
    decision: str
    note: str = ""


class StatusRequest(BaseModel):
    """管理员裁决（``PATCH /{oid}/status``）。"""

    status: str
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
        "deleted_at": _iso(row.get("deleted_at")),
    }
    if include_sha:
        data["sha256"] = row.get("sha256")
    return data


def _serialize_summary(row: dict, *, has_ocr: bool) -> dict:
    data = _serialize_meta(row, include_sha=False)
    data["owner_oid"] = row.get("owner_oid")
    data["has_ocr"] = bool(has_ocr)
    return data


def _serialize_event(row: dict) -> dict:
    return {
        "_id": str(row.get("_id") or ""),
        "actor_oid": row.get("actor_oid"),
        "action": row.get("action"),
        "from_status": row.get("from_status"),
        "to_status": row.get("to_status"),
        "detail": row.get("detail"),
        "created_at": _iso(row.get("created_at")),
    }


def _parse_created_bound(raw: str | None, *, end: bool) -> datetime | None:
    """解析列表的 ``created_from`` / ``created_to``（ISO 日期或时刻）。

    日期的语义是**含当天**：``created_to=2026-10-01`` 会转成
    ``2026-10-01T00:00 + 1 day`` 的上界（配合仓储的 ``created_at <`` 半开区间）。
    带时刻的输入原样使用，因此 ``created_to`` 在仓储里始终是开区间上界——
    这条约定写在一处，避免两个调用点各解一半。

    任何解析失败都是 400：审计界面把「过滤条件无效」静默当成「没有数据」比报错
    更危险。
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid date filter") from None
    if end and len(text) == 10:
        value += timedelta(days=1)
    return value


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
        # 文案里的数字必须与判定用的是同一组上限：用户级覆盖 / 在线设置改过的
        # 值都要如实反映，否则「配额用尽」会报一个已经过期的默认数。
        files_limit, bytes_limit = effective_daily_limits(user["id"])
        raise HTTPException(
            status_code=429,
            detail=(
                f"今日上传配额已用尽（每日上限 {files_limit} 个 / "
                f"{bytes_limit // (1024 * 1024)} MB），请明天再试"
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
    owner_oid: str | None = Query(None),
    created_from: str | None = Query(None),
    created_to: str | None = Query(None),
    ocr_status: str | None = Query(None),
    kind: str | None = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
):
    if scope == "all":
        # Never trust the client param: gate `all` server-side (design §4 #4).
        if not _is_manager(user):
            raise HTTPException(status_code=403, detail="Permission denied")
        # ``owner_oid`` 是审计过滤（不是越权入口）：只有过了 manager 闸才会被读。
        owner: str | None = validate_object_id(owner_oid) if owner_oid else None
    else:
        # scope=mine 一律锁死本人：``owner_oid`` 在这里被忽略，不做「顺手换个
        # owner」的隐式行为，否则同一个参数名会有两套语义。
        owner = user["id"]

    if ocr_status is not None and ocr_status not in OCR_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid ocr_status")
    if kind is not None and kind not in FILE_KINDS:
        raise HTTPException(status_code=400, detail="Invalid kind")

    repo = backends.get_files_repo()
    rows, total = await repo.list_page(
        owner,
        purpose,
        status,
        entity_type,
        entity_oid,
        page,
        size,
        created_from=_parse_created_bound(created_from, end=False),
        created_to=_parse_created_bound(created_to, end=True),
        ocr_status=ocr_status,
        kind=kind,
    )

    # ``has_ocr`` 整页一次聚合查询：逐行 ``get_ocr`` 会把每行的 CLOB 文本和
    # raw JSON 全拉回来，只为回答一个布尔值。
    oids = [str(row.get("_id") or "") for row in rows]
    with_ocr = await repo.file_oids_with_ocr(oids) if oids else set()

    return {
        "status": "ok",
        "code": 200,
        "data": [
            _serialize_summary(row, has_ocr=str(row.get("_id") or "") in with_ocr)
            for row in rows
        ],
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
# 3b. Administrative audit detail  /  3c. Status verdict
# ---------------------------------------------------------------------------


@router.get("/{oid}/audit")
async def get_attachment_audit(
    oid: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """单条附件的审计详情（admin | department）。

    和 ``GET /{oid}/meta`` 的区别是「审核要看到的一切」：谁传的（学号/姓名）、
    识别出了什么（文本 + ``_zvms_lines`` 坐标）、谁在什么时候动过它
    （``FILE_EVENTS`` 最近 50 条）。

    刻意不复用 ``GET /{oid}/ocr``：那个端点只回 ``lines``（前端画框用），也要
    求登录用户是 owner 或 manager；审核面需要完整文本 + 事件线，且必须是
    manager 闸，权限与字段都不同，硬复用会把两边都拧歪。
    """
    _require_manager(user)
    oid = validate_object_id(oid)
    repo = backends.get_files_repo()
    row = await repo.get_meta(oid)
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    # owner 的学号/姓名：一次 ``get_by_oid``，不为了两个字段再拼一条聚合 SQL。
    owner: dict | None = None
    owner_doc: dict | None = None
    owner_oid = str(row.get("owner_oid") or "")
    if owner_oid:
        owner_doc = await backends.get_users_repo().get_by_oid(owner_oid)
        if owner_doc:
            owner = {
                "_id": str(owner_doc.get("_id") or owner_oid),
                "student_id": owner_doc.get("id"),
                "name": owner_doc.get("name"),
            }

    ocr = await repo.get_ocr(oid)
    events = await repo.list_events(oid, limit=AUDIT_EVENT_LIMIT)
    lines = _ocr_lines(ocr)

    data = _serialize_meta(row, include_sha=_is_admin(user))
    data.update(
        {
            # 上传者：嵌套形态给「还想拿别的字段」的调用方，``owner_*`` 扁平字段
            # 与前端 ``types/attachment.d.ts`` 的 ``AttachmentAuditDetail`` 对齐。
            "owner": owner,
            "owner_name": owner["name"] if owner else None,
            "owner_id": owner["student_id"] if owner else None,
            "owner_xuehai_id": (
                owner_doc.get("primary_xuehai_id") if owner_doc else None
            ),
            "has_ocr": ocr is not None,
            # OCR：同样两种形态。``lines`` 是 ``raw_json._zvms_lines`` 的坐标，
            # 前端按它画框；``ocr.text`` 给需要文本块的调用方。
            "ocr_text": ocr.get("text") if ocr else None,
            "ocr_engine": ocr.get("engine") if ocr else None,
            "ocr_confidence": ocr.get("confidence") if ocr else None,
            "lines": lines,
            "ocr": {
                "status": row.get("ocr_status"),
                "engine": ocr.get("engine") if ocr else None,
                "engine_version": ocr.get("engine_version") if ocr else None,
                "text": ocr.get("text") if ocr else None,
                "confidence": ocr.get("confidence") if ocr else None,
                "lines": lines,
                "updated_at": _iso(ocr.get("created_at")) if ocr else None,
            },
            "events": [_serialize_event(event) for event in events],
            "events_limit": AUDIT_EVENT_LIMIT,
        }
    )
    log.with_text(f"用户 {user['id']} 查看附件审计详情 {oid}")
    await log.insert_log()
    return {"status": "ok", "code": 200, "data": data}


@router.patch("/{oid}/status")
async def update_attachment_status(
    oid: str,
    payload: StatusRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """管理员裁决：``verified`` / ``rejected``，并留一条 ``FILE_EVENTS``。

    为什么另开一个端点而不是复用 ``POST /{oid}/verify``：那个端点写的是
    ``action='verify'``，是给上传者自查证用的旧通道；审计面的裁决动作名必须是
    ``audit_verified`` / ``audit_rejected``，否则「谁在审计里判的」和「谁自查证
    的」在事件线里分不开。

    两条拒绝语义：

    * ``400`` — 状态不在白名单（客户端把 ``deleted`` 写进来也一样）；
    * ``409`` — 已经是这个状态（重复裁决不是成功，否则事件线会被同一条裁决
      刷屏）；墓碑（``deleted``）也不接受裁决，它的字节可能已被保留期任务清空。
    """
    _require_manager(user)
    if payload.status not in AUDIT_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")
    oid = validate_object_id(oid)
    repo = backends.get_files_repo()
    row = await repo.get_meta(oid)
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    previous = str(row.get("status") or "")
    if previous == payload.status:
        raise HTTPException(status_code=409, detail="Attachment already " + payload.status)
    if previous == "deleted":
        raise HTTPException(status_code=409, detail="Attachment is deleted")

    note = (payload.note or "").strip()[:AUDIT_NOTE_MAX]
    await repo.set_status(oid, payload.status)
    await repo.add_event(
        oid,
        user["id"],
        AUDIT_ACTION_BY_STATUS[payload.status],
        previous,
        payload.status,
        note,
    )
    log.with_text(f"用户 {user['id']} 裁决附件 {oid} -> {payload.status}")
    await log.insert_log()
    return {
        "status": "ok",
        "code": 200,
        "data": {
            "status": payload.status,
            "previous_status": previous,
            "note": note,
        },
    }


# ---------------------------------------------------------------------------
# 6. Signed link
# ---------------------------------------------------------------------------


@router.post("/{oid}/link")
async def create_attachment_link(
    oid: str,
    request: Request,
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

    # 学海设备的直链只活到解锁窗口结束：否则一次解锁换来的 URL 能拿着用一小时。
    if _is_xuehai_device(request):
        ttl = min(ttl, XUEHAI_UNLOCK_TTL)

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
            "lines": _ocr_lines(ocr),
            "updated_at": _iso(ocr.get("updated_at")) if ocr else None,
        },
    }


def _ocr_lines(ocr: dict | None) -> list[dict]:
    """``file_ocr.raw`` 里的保留键；老数据（改版前落的行）没有这个键。

    恒回列表而不是「没有就不出现字段」：客户端可以无条件 ``data.lines.map``，
    不必区分「后端太旧」与「这张图没行」——两者对展示是同一件事。
    """
    if not ocr:
        return []
    raw = ocr.get("raw")
    lines = raw.get(LINES_KEY) if isinstance(raw, dict) else None
    return lines if isinstance(lines, list) else []


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
