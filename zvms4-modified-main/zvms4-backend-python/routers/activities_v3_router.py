from datetime import datetime
import math
from fastapi import APIRouter, Depends, HTTPException, Body, Query
from db import backends
from db.ids import normalize_oid
from db.oracle.session import session_scope
from typings.log import inject_log
from util.object_id import get_current_user
from util.user import get_user_display_name, user_profile
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

    # 成员行内嵌用户摘要（形状同 v1 read_user），一页行只批量取一次用户，
    # 避免前端每张成员卡再回打 GET /api/users/{id}。
    async with session_scope() as session:
        rows, total = await backends.get_members_repo(session).list_for_activity(
            activity_id, query, page, size
        )
        users = await backends.get_users_repo(session).get_many_by_oids(
            [row["member"] for row in rows]
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
            "user": (
                user_profile(users[row["member"]])
                if row["member"] in users
                else None
            ),
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
    members: list[NewActivityMember] = Body(min_length=1, max_length=100),
    user=Depends(get_current_user),
    log=Depends(inject_log),
    overwrite: bool = Query(
        False,
        description="已有 (member, mode) 记录时，用本次时长覆盖而非累加",
    ),
    reject_existing: bool = Query(
        False,
        description="已有 (member, mode) 记录时拒绝整批写入",
    ),
):
    """
    :param activity_id: ID of the activity to be retrieved
    :param user: Current user
    :param member: ActivityMember object to be added
    :param log: Logger object
    :param overwrite: True 时同一 (member, mode) 的时长按「设置」语义写入
    :return: Activity object
    """

    if not _is_volunteer_manager(user):
        raise HTTPException(status_code=403, detail="权限不足")

    activity_id = str(validate_object_id(activity_id))
    if not members:
        raise HTTPException(status_code=400, detail="成员列表不能为空")
    # 直调（单测/内部调用）时形参默认值仍是 Query 对象本身，按「未开启」归一；
    # HTTP 请求经 FastAPI 校验后已是真正的 bool，原样通过。
    if not isinstance(overwrite, bool):
        overwrite = False
    if not isinstance(reject_existing, bool):
        reject_existing = False

    additions: dict[tuple[str, str], float] = {}
    for member in members:
        key = (str(validate_object_id(member.member)), member.mode)
        # overwrite：同键只在内存留最后一行的值（设置语义）；默认仍按累加，行为不变。
        if overwrite:
            additions[key] = member.duration
        else:
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
            known_users = await users_repo.get_many_by_oids(
                [oid for oid, _mode in additions]
            )
            for oid, mode in additions:
                if target_activity["type"] not in ("hybrid", mode):
                    raise HTTPException(status_code=400, detail="义工类型不匹配")
                if oid not in known_users:
                    raise HTTPException(status_code=400, detail="成员不存在")
            members_repo = backends.get_members_repo(session)
            # 既有三元组一次查回，循环里内存判定 upsert：导入 N 个成员不再 N 次查库。
            existing_by_key = {
                (str(row["member"]), row["mode"]): row
                for row in await members_repo.list_by_activity_and_members(
                    activity_id, [oid for oid, _mode in additions]
                )
            }
            if reject_existing:
                conflicts = [key for key in additions if key in existing_by_key]
                if conflicts:
                    raise HTTPException(
                        status_code=409,
                        detail=f"已有 {len(conflicts)} 条活动记录，确认后可按本次时长覆盖",
                    )
            for (oid, mode), duration in additions.items():
                existing = existing_by_key.get((oid, mode))
                if existing and overwrite:
                    # 设置语义：直接写本次值，不读旧值也不累加。
                    await members_repo.update_fields(existing["_id"], {"duration": duration})
                elif existing:
                    total = existing["duration"] + duration
                    if not math.isfinite(total):
                        raise HTTPException(status_code=400, detail="时长超出范围")
                    await members_repo.update_fields(existing["_id"], {"duration": total})
                else:
                    await members_repo.add(activity_id, oid, mode, duration, "effective")
            await activities_repo.update(activity_id, {"status": "pending", "updatedAt": datetime.now()})
            # 审计日志是给人读的：oid 写进「详情」等于让管理员拿哈希再查一轮。
            # 操作者与成员名一次批量取回（同一事务内），活动名用行锁读到的文档。
            names = await users_repo.get_names(
                [str(user["id"]), *[oid for oid, _mode in additions]]
            )
            performer = names.get(str(user["id"])) or "未知用户"
            activity_name = target_activity["name"] or "未知活动"
            detail = "；".join(
                (
                    f"{names.get(oid) or '未知用户'} {mode} {duration}"
                    if overwrite
                    else f"{names.get(oid) or '未知用户'} {mode} +{duration}"
                )
                for (oid, mode), duration in additions.items()
            )
            log.with_text(
                f"用户 {performer} 为义工活动《{activity_name}》"
                f"{'设置' if overwrite else '增加'}时长：{detail}",
                type="activity_member_update",
            )
            await log.insert_log(session)
