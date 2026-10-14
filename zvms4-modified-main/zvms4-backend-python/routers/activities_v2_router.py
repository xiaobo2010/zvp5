import re
from collections import defaultdict
from datetime import datetime
from typing import Literal, cast
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from db import backends
from typings.activity_v2 import Activity, ActivityMember
from typings.log import inject_log
from util.object_id import get_current_user, compulsory_temporary_token
from util.permission import volunteer, volunteer_member
from util.user import get_user_display_name
from util.validation import validate_activity_description, validate_activity_name
from utils import validate_object_id

router = APIRouter()


@router.post("")
async def create_activity_v2(
    activity: Activity, user=Depends(get_current_user), log=Depends(inject_log)
):
    """
    :param activity: Activity object to be created
    :param user: Current user
    :param log: Logger object

    :return: Created activity ID

    The new version of the activity creation endpoint, using the latest data model.
    """
    await volunteer.validate_create_permission(user, activity.type == "hybrid")

    activity.status = "pending"

    validated, reason = validate_activity_name(activity.name)

    if not validated:
        raise HTTPException(status_code=400, detail=reason)

    validated, reason = validate_activity_description(activity.description)

    if not validated:
        raise HTTPException(status_code=400, detail=reason)

    activity.creator = str(user["id"])

    activities_repo = backends.get_activities_repo()

    oid = await activities_repo.create(
        activity.name,
        activity.description,
        activity.type,
        activity.creator,
        activity.appointee,
        activity.origin,
        activity.date,
    )

    extra = {
        key: value
        for key, value in {
            "place": activity.place,
            "approver": activity.approver,
        }.items()
        if value
    }
    if extra:
        await activities_repo.update(oid, extra)

    log.with_text(
        f'用户 {await get_user_display_name(user["id"])} 创建了义工活动《{activity.name}》（ID: {oid}）',
        type="activity_create",
    )
    await log.insert_log()

    return JSONResponse({"id": str(oid)}, status_code=201)


@router.get("")
async def get_activities_v2(
    page: int = Query(1, ge=1, description="Page number for pagination"),
    perpage: int = Query(10, ge=1, le=100, description="Number of items per page"),
    search: str = Query("", description="Search string for filtering activities"),
    sort: str = Query("_id", description="Sort field"),
    asc: bool = Query(False, description="Sort order (ascending or descending)"),
    regex: bool = Query(False, description="Whether to use regex for searching"),
    activity_type: str = Query("all", description="Type of activity to filter by"),
    user=Depends(get_current_user),
):
    """
    :param user: Current user
    :param page: Page number for pagination
    :param perpage: Number of items per page
    :param search: Search string for filtering activities
    :param sort: Sort field
    :param asc: Sort order (ascending or descending)
    :param regex: Whether to use regex for searching
    :param activity_type: Type of activity to filter by

    :return: List of activities
    """

    if activity_type is None or activity_type == "all" or activity_type == "":
        target_types = ["on-campus", "off-campus", "social-practice", "hybrid"]
    else:
        target_types = activity_type.split(",")
    if len(target_types) == 0:
        target_types = ["on-campus", "off-campus", "social-practice", "hybrid"]

    if not regex:
        search = re.escape(search) if search else ""

    activities, count = await backends.get_activities_repo().list_raw(
        search=search,
        atypes=target_types,
        sort=sort,
        asc=asc,
        page=page,
        size=perpage,
    )

    return {
        "activities": activities,
        "total": count,
        "page": page,
        "perpage": perpage,
    }


@router.get("/{activity_id}")
async def get_activity_v2(activity_id: str, user=Depends(get_current_user)):
    """
    :param activity_id: ID of the activity to be retrieved
    :param user: Current user
    :return: Activity object
    """

    validate_object_id(activity_id)
    activity = await backends.get_activities_repo().get(activity_id)
    members = await backends.get_members_repo().count_for_activity(activity_id)

    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    return {"activity": activity, "members_count": members}


@router.delete("/{activity_id}")
async def delete_activity_v2(
    activity_id: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    :param activity_id: ID of the activity to be deleted
    :param user: Current user
    :param log: Logger object
    :return: Deletion status
    """

    target_activity = await backends.get_activities_repo().get(activity_id)

    if not target_activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    await volunteer.validate_update_permission(
        user, Activity.model_validate(target_activity, strict=False)
    )

    await backends.get_members_repo().remove_for_activity(activity_id)

    await backends.get_activities_repo().delete(activity_id)

    log.with_text(
        f'用户 {await get_user_display_name(user["id"])} 删除了义工活动《{target_activity["name"]}》（ID: {activity_id}）',
        type="activity_delete",
    )
    await log.insert_log()

    return {"detail": "Activity deleted successfully"}


@router.get("/{activity_id}/members")
async def get_activity_members_v2(
    activity_id: str,
    page: int = Query(1, ge=1, description="Page number for pagination"),
    perpage: int = Query(10, ge=1, le=100, description="Number of items per page"),
    search: str = Query("", description="Search string for filtering members"),
    sort: str = Query("_id", description="Sort field"),
    asc: bool = Query(True, description="Sort order (ascending or descending)"),
    regex: bool = Query(False, description="Whether to use regex for searching"),
    user=Depends(get_current_user),
):
    """
    :param activity_id: ID of the activity to be retrieved
    :param user: Current user
    :param page: Page number for pagination
    :param perpage: Number of items per page
    :param search: Search string for filtering members
    :param sort: Sort field
    :param asc: Sort order (ascending or descending)
    :param regex: Whether to use regex for searching

    :return: List of activity members
    """
    activity = await backends.get_activities_repo().get(activity_id)

    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    if not regex:
        search = re.escape(search) if search else ""

    searched_users = await backends.get_users_repo().list_by_name_or_id(search)
    member_ids = [str(user["_id"]) for user in searched_users]

    members, count = await backends.get_members_repo().list_raw(
        str(activity_id),
        member_oids=member_ids,
        sort=sort,
        asc=asc,
        page=page,
        size=perpage,
    )

    return {
        "members": members,
        "total": count,
        "page": page,
        "perpage": perpage,
    }


@router.get("/{activity_id}/members/{session_id}")
async def get_activity_member_v2(
    activity_id: str,
    session_id: str,
    user=Depends(get_current_user),
):
    """
    :param activity_id: ID of the activity to be retrieved
    :param user: Current user
    :param session_id: ID of the session to be retrieved
    :return: ActivityMember object
    """

    target_member = await backends.get_members_repo().get_by_oid(session_id)

    if not target_member:
        raise HTTPException(status_code=404, detail="Activity member not found")

    return target_member


@router.post("/{activity_id}/members")
async def add_activity_member_v2(
    activity_id: str,
    member: ActivityMember,
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

    target_activity = await backends.get_activities_repo().get(activity_id)
    target_user = await backends.get_users_repo().get_by_oid(
        validate_object_id(member.member)
    )
    target_activity = Activity.model_validate(target_activity, strict=False)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")
    await volunteer_member.validate_create_permission(
        user, target_user, target_activity
    )

    if target_activity.type != "hybrid" and target_activity.type != member.mode:
        raise HTTPException(
            status_code=400, detail="Activity type and member mode do not match"
        )

    if await backends.get_members_repo().get_by_triple(
        activity_id, member.member, member.mode
    ):
        raise HTTPException(
            status_code=400, detail="User already in activity with same counting mode"
        )

    new_id = await backends.get_members_repo().add(
        activity_id,
        member.member,
        member.mode,
        member.duration,
        member.status,
    )
    
    log.with_text(
        f'用户 {await get_user_display_name(user["id"])} 将成员 {target_user["name"]} 加入义工活动《{target_activity.name}》',
        type="activity_member_add",
    )
    await log.insert_log()

    return {
        "id": new_id,
        "count": await backends.get_members_repo().count_for_activity(activity_id),
    }


@router.delete("/{activity_id}/members/{session_id}")
async def delete_activity_member_v2(
    activity_id: str,
    session_id: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    :param activity_id: ID of the activity to be retrieved
    :param user: Current user
    :param session_id: ID of the session. Since it's allowed to have *multiple* records in the same activity for the same
    person if the recoding mode is different, we must use another `session_id` to specify a document.
    :return: Activity object
    """
    await volunteer_member.validate_update_permission(user)

    target_member = await backends.get_members_repo().get_by_oid(session_id)

    if not target_member:
        raise HTTPException(status_code=404, detail="Activity member not found")

    target_activity = await backends.get_activities_repo().get(activity_id)
    activity_name = (
        target_activity.get("name") if target_activity else "未知活动"
    ) or "未知活动"

    await backends.get_members_repo().remove(session_id)

    log.with_text(
        f'用户 {await get_user_display_name(user["id"])} 将成员 {await get_user_display_name(target_member["member"])} 移出义工活动《{activity_name}》（类型: {target_member.get("mode")}，时长: {target_member.get("duration")} 小时，ID: {activity_id}）',
        type="activity_member_remove",
    )
    await log.insert_log()

    return {"detail": "Activity member deleted successfully"}


class PutActivityStatus(BaseModel):
    status: Literal["effective", "pending", "refused"]


@router.put("/{activity_id}/status")
async def modify_activity_status_v2(
    activity_id: str,
    status: PutActivityStatus,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    :param activity_id: ID of the activity to be retrieved
    :param user: Current user
    :param status: Status to be set
    :param log: Logger object

    :return: None
    """

    target_activity = await backends.get_activities_repo().get(activity_id)

    if not target_activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    target_activity = Activity.model_validate(target_activity, strict=False)

    if target_activity.status != "pending" and "admin" not in user.get("perm"):
        raise HTTPException(
            status_code=400,
            detail="Activity status can only be modified from pending to effective or refused",
        )

    if status.status == target_activity.status:
        raise HTTPException(
            status_code=400,
            detail="Activity status is not changed",
        )

    await volunteer.validate_check_permission(user, target_activity, strict=True)

    update_content = {
        "status": status.status,
        "approver": str(user["id"]),
        "updatedAt": datetime.now(),
    }

    if target_activity.status != "pending":
        del update_content["approver"] # The activity is not being approved

    await backends.get_activities_repo().update(activity_id, update_content)

    status_labels = {"effective": "生效", "pending": "待审核", "refused": "已驳回"}
    log.with_text(
        f'用户 {await get_user_display_name(user["id"])} 审核了义工活动《{target_activity.name}》，状态改为 {status_labels.get(status.status, status.status)}',
        type="activity_update",
    )
    await log.insert_log()

    return JSONResponse({"detail": "Activity status updated successfully"})


class AmalgamationForm(BaseModel):
    """
    Form for amalgamating multiple activities into one.

    This form is used to specify the activities to be amalgamated, the name and description of the new activity,

    :param activities: List of activity IDs to be amalgamated
    :param name: Name of the new activity
    :param description: Description of the new activity. (optional) If not provided, it will be set to the concatenation of the names of the amalgamated activities.
    :param duplicated: How to handle duplicated members. Options are 'max' (keep the maximum count) or 'merge' (sum the counts).
    :param proceedPending: Whether to proceed with pending activities. If False, only effective activities will be amalgamated; if true, the final activity will be set to pending.
    """

    activities: list[str]
    name: str
    description: str = ""
    origin: Literal[
        "labor",
        "organization",
        "tasks",
        "occasions",
        "import",
        "activities",
        "practice",
        "club",
        "prize",
        "other",
    ]
    duplicated: Literal["max", "sum"] = "max"
    proceedPending: bool = False


@router.post("/amalgamation")
async def amalgamate_activities_v2(
    form: AmalgamationForm,
    user=Depends(compulsory_temporary_token),
    log=Depends(inject_log),
):
    raise HTTPException(status_code=410, detail="Permanently removed")

class UpdateUserRecord(BaseModel):
    duration: float
    mode: Literal["on-campus", "off-campus", "social-practice"]


@router.put("/{activity_id}/members/{document_id}/record")
async def update_user_duration_v2(
    activity_id: str,
    document_id: str,
    updated: UpdateUserRecord,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    Update the duration of a user's record in an activity.

    :param activity_id: ID of the activity
    :param document_id: ID of the activity member document
    :param updated: UpdateUserRecord containing the new duration and mode
    :param user: Current user
    :return: Updated activity member document
    """
    target_activity = await backends.get_activities_repo().get(activity_id)

    if not target_activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    target_member = await backends.get_members_repo().get_by_oid(document_id)

    if not target_member:
        raise HTTPException(status_code=404, detail="Activity member not found")

    await volunteer_member.validate_update_permission(user)

    updated_data = updated.model_dump()
    updated_data["duration"] = float(updated_data["duration"])

    await backends.get_members_repo().update_fields(document_id, updated_data)

    log.with_text(
        f'用户 {await get_user_display_name(user["id"])} 修改了义工活动《{target_activity["name"]}》中成员 {await get_user_display_name(target_member["member"])} 的记录（时长 {updated.duration}，模式 {updated.mode}）',
        type="activity_member_update",
    )
    await log.insert_log()

    return JSONResponse({"detail": "Activity member updated successfully"})


class UpdateActivityInfo(BaseModel):
    name: str
    description: str


@router.put("/{activity_id}/info")
async def update_activity_info(
    payload: UpdateActivityInfo,
    activity_id: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    Update the name of an activity.

    :param payload: UpdateActivityInfo containing the new name and description
    :param activity_id: ID of the activity to be updated
    :param user: Current user
    :return: Updated activity document
    """
    target_activity = await backends.get_activities_repo().get(activity_id)

    if not target_activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    await volunteer.validate_update_permission(
        user, Activity.model_validate(target_activity, strict=False)
    )

    validated, reason = validate_activity_name(payload.name)
    if not validated:
        raise HTTPException(status_code=400, detail=reason)

    validated, reason = validate_activity_description(payload.description)
    if not validated:
        raise HTTPException(status_code=400, detail=reason)

    await backends.get_activities_repo().update(
        activity_id,
        {"name": payload.name, "description": payload.description, "updatedAt": datetime.now()},
    )

    log.with_text(
        f'用户 {await get_user_display_name(user["id"])} 修改了义工活动《{target_activity["name"]}》的信息（名称改为 {payload.name}）',
        type="activity_update",
    )
    await log.insert_log()

    return JSONResponse({"detail": "Activity name updated successfully"})
