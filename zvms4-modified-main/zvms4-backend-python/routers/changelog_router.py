from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db import backends
from services.changelog_notifications import (
    deactivate_changelog_notification,
    sync_changelog_notification,
)
from typings.log import inject_log
from util.object_id import get_current_user, require_password_verification
from util.user import get_user_display_name
from utils import validate_object_id

router = APIRouter()


class ChangelogEntryRequest(BaseModel):
    version: str
    date: str
    items: list[str]
    verification_token: str | None = None


class ChangelogDeleteRequest(BaseModel):
    """删除的复核令牌；``DELETE`` 也走 body，与附件/用户端点一致。"""

    verification_token: str | None = None


def _require_admin(user: dict) -> None:
    if "admin" not in (user.get("per") or []):
        raise HTTPException(status_code=403, detail="权限不足")


def _serialize(entry: dict) -> dict:
    return {
        "id": entry["_id"],
        "version": entry["version"],
        "date": entry["date"],
        "items": entry.get("items") or [],
    }


@router.get("")
async def read_changelog():
    entries = await backends.get_changelog_repo().list_all()
    return {"status": "ok", "data": [_serialize(entry) for entry in entries]}


@router.post("", status_code=201)
async def create_changelog(
    payload: ChangelogEntryRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    _require_admin(user)
    await require_password_verification(payload.verification_token, user)

    repo = backends.get_changelog_repo()
    new_id = await repo.create(payload.version, payload.date, list(payload.items))
    entry = await repo.get_by_oid(new_id)
    if entry is None:
        entry = {
            "_id": new_id,
            "version": payload.version,
            "date": payload.date,
            "items": list(payload.items),
        }

    summary = "; ".join(payload.items)[:800]
    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 新增版本记录 {payload.version}：{summary}",
        type="changelog_create",
    )
    await log.insert_log()

    # 版本公告是旁路：服务内部吞异常，失败只落 warning，不影响本次创建的结果。
    await sync_changelog_notification(_serialize(entry))

    return _serialize(entry)


@router.put("/{oid}", status_code=204)
async def update_changelog(
    oid: str,
    payload: ChangelogEntryRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    _require_admin(user)
    await require_password_verification(payload.verification_token, user)
    validate_object_id(oid)
    repo = backends.get_changelog_repo()
    await repo.update(oid, payload.version, payload.date, list(payload.items))

    summary = "; ".join(payload.items)[:800]
    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 更新版本记录 {payload.version}：{summary}",
        type="changelog_update",
    )
    await log.insert_log()

    # 改写只同步标题/正文（并让公告 version +1 触发客户端重新未读）；行不存在时
    # 静默跳过——PUT 本身不校验 rowcount，响应形状保持不变。
    entry = await repo.get_by_oid(oid)
    if entry is not None:
        await sync_changelog_notification(_serialize(entry))

    return


@router.delete("/{oid}", status_code=204)
async def delete_changelog(
    oid: str,
    payload: ChangelogDeleteRequest | None = None,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    _require_admin(user)
    await require_password_verification(
        payload.verification_token if payload is not None else None, user
    )
    validate_object_id(oid)
    repo = backends.get_changelog_repo()
    entry = await repo.get_by_oid(oid)
    await repo.delete(oid)

    # 公告不硬删：发布过是事实，置为 inactive 即可（客户端已读状态按 oid 记）。
    await deactivate_changelog_notification(oid)

    version = entry["version"] if entry is not None else oid
    summary = "; ".join(entry.get("items") or [])[:800] if entry is not None else ""
    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 删除版本记录 {version}：{summary}",
        type="changelog_delete",
    )
    await log.insert_log()

    return
