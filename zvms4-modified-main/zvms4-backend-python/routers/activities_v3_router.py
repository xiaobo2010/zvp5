from datetime import datetime
import math
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from db import backends
from db.ids import normalize_oid
from db.oracle.session import session_scope
from typings.log import inject_log
from util.object_id import get_current_user
from util.user import get_user_display_name
from utils import validate_object_id
from typing import Literal
from pydantic import BaseModel, Field, field_validator
from util.validation import validate_activity_description

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
    repo = backends.get_activities_repo()
    atypes = [type] if type else None
    rows, total = await repo.list_page(query, atypes, status or None, page, size)

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
            "creator_name": row["creator_name"],
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

    validated, reason = validate_activity_description(payload.description)
    if not validated:
        raise HTTPException(status_code=400, detail=reason)

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
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
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

    members = [
        {
            "_id": row["_id"],
            "activity": row["activity"],
            "member": row["member"],
            "member_name": row["member_name"],
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

    @field_validator("duration")
    @classmethod
    def validate_finite_duration(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("duration must be finite")
        return value


@router.post("/{activity_id}/members", status_code=204)
async def add_activity_member_v3(
    activity_id: str,
    members: list[NewActivityMember] = Body(min_length=1, max_length=1000),
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

    activity_id = str(validate_object_id(activity_id))
    if not members:
        raise HTTPException(status_code=400, detail="成员列表不能为空")
    additions: dict[tuple[str, str], float] = {}
    for member in members:
        key = (str(validate_object_id(member.member)), member.mode)
        additions[key] = additions.get(key, 0.0) + member.duration
        if not math.isfinite(additions[key]):
            raise HTTPException(status_code=400, detail="时长超出范围")

    async with session_scope() as session:
        async with session.begin():
            activities_repo = backends.get_activities_repo(session)
            target_activity = await activities_repo.get_for_update(activity_id)
            if not target_activity:
                raise HTTPException(status_code=404, detail="Activity not found")
            users_repo = backends.get_users_repo(session)
            for oid, mode in additions:
                if target_activity["type"] not in ("hybrid", mode):
                    raise HTTPException(status_code=400, detail="义工类型不匹配")
                if await users_repo.get_by_oid(oid) is None:
                    raise HTTPException(status_code=400, detail="成员不存在")
            members_repo = backends.get_members_repo(session)
            for (oid, mode), duration in additions.items():
                existing = await members_repo.get_by_triple(activity_id, oid, mode)
                if existing:
                    total = existing["duration"] + duration
                    if not math.isfinite(total):
                        raise HTTPException(status_code=400, detail="时长超出范围")
                    await members_repo.update_fields(existing["_id"], {"duration": total})
                else:
                    await members_repo.add(activity_id, oid, mode, duration, "effective")
            await activities_repo.update(activity_id, {"status": "pending", "updatedAt": datetime.now()})
            log.with_text(
                f"用户 {user['id']} 为活动 {activity_id} 增加时长：" +
                "; ".join(f"{oid} {mode} +{duration}" for (oid, mode), duration in additions.items()),
                type="activity_member_update",
            )
            await log.insert_log(session)
