from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from db import backends
from typings.log import inject_log
from util.object_id import get_current_user
from util.user import get_user_display_name
from utils import validate_object_id
from typing import Literal
from pydantic import BaseModel, Field
from bson import ObjectId

router = APIRouter()


def _is_volunteer_manager(user: dict) -> bool:
    per = user.get("per") or []
    return "admin" in per or "department" in per


@router.get('')
async def get_activities_v3(
    page: int = Query(1, ge=1, description="Page number"),
    size: int = Query(10, ge=1, le=1000, description="Page size"),
    query: str = Query('', description="Search keyword"),
    type: Literal['on-campus', 'off-campus', 'social-practice', 'hybrid', ''] = Query('', description="Type of activity to filter"),
    status: Literal['effective', 'refused', 'pending', 'draft', ''] = Query(''),
    user=Depends(get_current_user)
):
    if not _is_volunteer_manager(user):
        raise HTTPException(
            status_code=403,
            detail='权限不足'
        )

    repo = backends.get_activities_repo()
    atypes = [type] if type else None
    rows, total = await repo.list_page(query, atypes, status or None, page, size)

    creator_oids = [
        row["creator"]
        for row in rows
        if row.get("creator") and ObjectId.is_valid(row["creator"])
    ]
    names = (
        await backends.get_users_repo().get_names(creator_oids)
        if creator_oids
        else {}
    )

    activities = [
        {
            "_id": row["_id"],
            "type": row["type"],
            "name": row["name"],
            "description": row["description"],
            "status": row["status"],
            "date": row["date"],
            "createdAt": row["createdAt"],
            "updatedAt": row["updatedAt"],
            "creator": row["creator"],
            "creator_name": names.get(row["creator"]) or "未知用户",
        }
        for row in rows
    ]

    return {"total": total, "activities": activities}


class CreateActivityRequest(BaseModel):
    name: str
    description: str
    type: Literal['on-campus', 'off-campus', 'social-practice', 'hybrid']


@router.post('', status_code=201)
async def create_activity_v3(
    payload: CreateActivityRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log)
):
    if not _is_volunteer_manager(user):
        raise HTTPException(
            status_code=403,
            detail='权限不足'
        )

    oid = await backends.get_activities_repo().create(
        payload.name,
        payload.description,
        payload.type,
        user["id"],
        user["id"],
        "activities",
        datetime.now(),
    )

    log.with_text(
        f'用户 {await get_user_display_name(user["id"])} 创建了义工活动《{payload.name}》（ID: {oid}）',
        type="activity_create",
    )
    await log.insert_log()

    return {
        "id": str(oid)
    }


@router.get('/{activity_id}/members')
async def get_activity_members_v3(
    activity_id: str,
    page: int = Query(1),
    size: int = Query(10),
    query: str = Query(''),
    user=Depends(get_current_user)
):
    """
    Get activity members (ULTRA FAST impl)
    """

    validate_object_id(activity_id)

    rows, total = await backends.get_members_repo().list_for_activity(
        activity_id, query, page, size
    )

    member_oids = [
        row["member"]
        for row in rows
        if row.get("member") and ObjectId.is_valid(row["member"])
    ]
    names = (
        await backends.get_users_repo().get_names(member_oids)
        if member_oids
        else {}
    )

    members = [
        {
            "_id": row["_id"],
            "activity": row["activity"],
            "member": row["member"],
            "member_name": names.get(row["member"]) or "未知用户",
            "mode": row["mode"],
            "status": row["status"],
            "duration": row["duration"],
        }
        for row in rows
    ]

    return {
        "members": members,
        "total": total,
    }


class NewActivityMember(BaseModel):
    member: str
    mode: Literal['on-campus', 'off-campus', 'social-practice']
    duration: float = Field(gt=0)


@router.post("/{activity_id}/members", status_code=204)
async def add_activity_member_v3(
    activity_id: str,
    members: list[NewActivityMember] = Body(),
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    :param activity_id: ID of the activity to be retrieved
    :param user: Current user
    :param member: ActivityMember object to be added
    :param log: Logger object
    :return: Activity object
    """

    if not _is_volunteer_manager(user):
        raise HTTPException(status_code=403, detail="权限不足")

    members_repo = backends.get_members_repo()
    activities_repo = backends.get_activities_repo()

    target_activity = await activities_repo.get(activity_id)

    if not target_activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    log_text = ''
    has_update = False

    for member in members:
        if target_activity["type"] != "hybrid" and target_activity["type"] != member.mode:
            raise HTTPException(
                status_code=400, detail="义工类型不匹配"
            )

    for member in members:

        existing: dict = await members_repo.get_by_triple(
            activity_id, member.member, member.mode
        )

        if existing:

            updated_data = {
                "duration": existing["duration"] + member.duration
            }

            await members_repo.update_fields(existing["_id"], updated_data)

            has_update = True
            log_text += f'用户 {await get_user_display_name(user["id"])} 更新了义工活动《{target_activity["name"]}》中成员 {await get_user_display_name(member.member)} 的时长记录\n'

            continue

        await members_repo.add(
            activity_id, member.member, member.mode, member.duration, "effective"
        )

        log_text += f'用户 {await get_user_display_name(user["id"])} 将成员 {await get_user_display_name(member.member)} 加入义工活动《{target_activity["name"]}》\n'

    if len(log_text):
        log.with_text(
            log_text[:-1],
            type="activity_member_update" if has_update else "activity_member_add",
        )
        await log.insert_log()

    await activities_repo.update(
        activity_id,
        {
            "status": "pending",
            "updatedAt": datetime.now(),
        },
    )
