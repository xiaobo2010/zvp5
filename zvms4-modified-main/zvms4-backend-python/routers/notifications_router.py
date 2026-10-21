"""站内通知端点（前缀 ``/api/v3/notifications``）。

契约分两层：

* ``GET ""`` —— 任何已登录用户读取当前**有效**通知列表 + 全局版本号。客户端
  只把返回的 ``version`` 与自己本地的版本比较，决定是否需要刷新；``已阅`` /
  ``不再显示`` 完全是客户端本地状态，不调用服务端，也不推进任何版本号。
* ``GET /admin``、``POST ""``、``PUT /{oid}``、``DELETE /{oid}`` —— 管理端。
  服务端每个请求都从数据库重新解析当前角色（``get_current_user``），
  ``_require_admin`` 只认**当前**的 ``admin``，不信任 JWT 里的旧快照。

版本语义：

* 全局 ``version``（``NOTIFICATION_STATE``）在每次成功的创建/编辑/删除时与
  业务行**同一事务** +1；失败（通知不存在）不推进。
* 单条通知的 ``version``（``NOTIFICATIONS.VERSION``）创建为 1，每次成功编辑
  或停用 +1，用于客户端判断「这条通知是否被改过」。
* 发布人 ``author_id`` / ``author_name`` 与 ``created_at`` 写入后不可变，
  编辑接口不会覆盖它们。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import backends
from typings.log import inject_log
from util.object_id import get_current_user
from util.user import get_user_display_name
from utils import validate_object_id

router = APIRouter()

_TITLE_MAX = 200
_CONTENT_MAX = 4000


class NotificationCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=_TITLE_MAX)
    content: str = Field(min_length=1, max_length=_CONTENT_MAX)


class NotificationUpdateRequest(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=_TITLE_MAX)
    content: Optional[str] = Field(default=None, min_length=1, max_length=_CONTENT_MAX)
    active: Optional[bool] = None


def _require_admin(user: dict) -> None:
    if "admin" not in (user.get("per") or []):
        raise HTTPException(status_code=403, detail="权限不足")


def _iso(value) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return None


def _clean_text(value: str, field: str, limit: int) -> str:
    """Trim and reject whitespace-only/oversized input.

    Oracle's ``VARCHAR2(4000 CHAR)`` still caps the **byte** length at 4000, so
    a 4000-character Chinese body would fail with ORA-12899. Check UTF-8 bytes
    here rather than letting the DB reject a request that Pydantic accepted.
    """
    text = value.strip()
    if not text:
        raise HTTPException(status_code=422, detail=f"{field}不能为空")
    if len(text) > limit or len(text.encode("utf-8")) > 4000:
        raise HTTPException(status_code=422, detail=f"{field}过长")
    return text


def _view(entry: dict) -> dict:
    """Public notification shape shared by the user and admin responses.

    ``author_id`` is the sender's user oid (24-char lowercase hex); it is stored
    as ``AUTHOR_OID`` and is the immutable identity behind ``author_name``.
    """
    return {
        "id": entry["_id"],
        "title": entry["title"],
        "content": entry["content"],
        "version": int(entry["version"]),
        "author_id": entry["author_oid"],
        "author_name": entry["author_name"],
        "created_at": _iso(entry["created_at"]),
        "updated_at": _iso(entry["updated_at"]),
        "active": bool(entry["active"]),
    }


def _envelope(rows: list[dict], version: int) -> dict:
    return {
        "status": "ok",
        "code": 200,
        "data": {
            "version": version,
            "notifications": [_view(row) for row in rows],
        },
    }


@router.get("")
async def read_notifications(user=Depends(get_current_user)):
    """Current active notices + global version for the client's local markers."""
    rows, version = await backends.get_notifications_repo().list_active()
    return _envelope(rows, version)


@router.get("/admin")
async def read_notifications_admin(user=Depends(get_current_user)):
    """Admin management list: active and inactive rows, newest first."""
    _require_admin(user)
    rows, version = await backends.get_notifications_repo().list_all()
    return _envelope(rows, version)


@router.post("", status_code=201)
async def create_notification(
    payload: NotificationCreateRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    _require_admin(user)

    title = _clean_text(payload.title, "标题", _TITLE_MAX)
    content = _clean_text(payload.content, "内容", _CONTENT_MAX)

    # The display name is resolved from the authenticated oid against the
    # current USERS row and persisted as the immutable send-time identity.
    author_name = await get_user_display_name(user["id"])
    entry, version = await backends.get_notifications_repo().create(
        title=title,
        content=content,
        author_oid=user["id"],
        author_name=author_name,
    )

    log.with_text(
        f"管理员 {author_name} 发布通知「{title}」",
        type="notification_create",
    )
    await log.insert_log()

    return {
        "status": "ok",
        "code": 201,
        "data": {"notification": _view(entry), "version": version},
    }


@router.put("/{oid}")
async def update_notification(
    oid: str,
    payload: NotificationUpdateRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    _require_admin(user)
    validate_object_id(oid)

    title = (
        _clean_text(payload.title, "标题", _TITLE_MAX)
        if payload.title is not None
        else None
    )
    content = (
        _clean_text(payload.content, "内容", _CONTENT_MAX)
        if payload.content is not None
        else None
    )
    if title is None and content is None and payload.active is None:
        raise HTTPException(status_code=422, detail="至少提供一个要修改的字段")

    entry, version = await backends.get_notifications_repo().update(
        oid,
        title=title,
        content=content,
        active=payload.active,
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="通知不存在")

    editor_name = await get_user_display_name(user["id"])
    log.with_text(
        f"管理员 {editor_name} 更新通知「{entry['title']}」",
        type="notification_update",
    )
    await log.insert_log()

    return {
        "status": "ok",
        "code": 200,
        "data": {"notification": _view(entry), "version": version},
    }


@router.delete("/{oid}", status_code=204)
async def delete_notification(
    oid: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    _require_admin(user)
    validate_object_id(oid)

    entry, _version = await backends.get_notifications_repo().delete(oid)
    if entry is None:
        raise HTTPException(status_code=404, detail="通知不存在")

    actor_name = await get_user_display_name(user["id"])
    log.with_text(
        f"管理员 {actor_name} 删除通知「{entry['title']}」",
        type="notification_delete",
    )
    await log.insert_log()

    return
