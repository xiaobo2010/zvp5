import asyncio
import logging
import re
from datetime import datetime
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.exc import IntegrityError
from db import backends
from db.oracle.session import session_scope
from typings.activity_v2 import Activity, ActivityMember
from typings.log import inject_log
from util.calculate import calculate_user_time, calculate_user_time_bulk
from util.object_id import get_current_user, compulsory_temporary_token
from util.permission import volunteer, volunteer_member
from util.user import get_user_display_name
from util.validation import (
    validate_activity_approver,
    validate_activity_description,
    validate_activity_name,
    validate_activity_place,
)
from util.oid import validate_object_id

router = APIRouter()

logger = logging.getLogger(__name__)

_TIME_REFRESH_MEMBER_LIMIT = 500
_time_refresh_semaphore = asyncio.Semaphore(2)
_time_refresh_tasks: set[asyncio.Task] = set()


async def _write_user_times(members: list[str]) -> None:
    """按人重算时长并整批回填 ``user_time``（调用方已过人数闸门）。"""
    async with session_scope() as session:
        times = await calculate_user_time_bulk(
            members, allow_cache=False, session=session
        )
        time_repo = backends.get_time_repo(session)
        entries = [
            (
                member,
                times[member]["on-campus"],
                times[member]["off-campus"],
                times[member]["social-practice"],
            )
            for member in members
        ]
        upsert_many = getattr(time_repo, "upsert_many_for_users", None)
        if upsert_many is not None:
            # 整批一次数组绑定：逐人 upsert 在远程 ADB 上是 2N 次跨洋往返。
            await upsert_many(entries)
        else:
            # 只实现旧接口的替身/装配：逐条写，结果与批量一致。
            for member, on_campus, off_campus, social in entries:
                await time_repo.upsert_for_user(
                    member, on_campus, off_campus, social
                )
        await session.commit()


async def _refresh_user_time_for_activity(activity_id: str) -> None:
    async with _time_refresh_semaphore:
        member_count = 0
        try:
            members_repo = backends.get_members_repo()
            # 先按「人数」探闸门再取行：原先要把全部成员行拉回来才比 500，
            # 超大活动白付一次全表读；而按行数判断会把「1 人 3 个模式」
            # 误当成 3 名成员（DISTINCT 是这里的语义关键）。
            member_count = await members_repo.count_distinct_members(activity_id)
            if member_count == 0:
                return
            if member_count > _TIME_REFRESH_MEMBER_LIMIT:
                logger.info(
                    "活动 %s 有 %d 名成员，跳过即时重算，交由每日全量重建",
                    activity_id,
                    member_count,
                )
                return
            rows = await members_repo.list_by_activity(activity_id)
            members = list(dict.fromkeys(str(row["member"]) for row in rows))
            if not members:
                return
            await _write_user_times(members)
        except Exception:
            logger.warning(
                "活动 %s 状态变更后重算 %d 名成员时长失败，保留缓存旧值",
                activity_id,
                member_count,
                exc_info=True,
            )


async def _refresh_user_time_for_members(
    member_ids: list[str], activity_id: str | None = None
) -> None:
    """按显式成员集合重算——删除活动时用（行已经没了，不能按活动反查）。"""
    async with _time_refresh_semaphore:
        members = list(dict.fromkeys(str(member) for member in member_ids))
        try:
            if not members:
                return
            if len(members) > _TIME_REFRESH_MEMBER_LIMIT:
                logger.info(
                    "活动 %s 涉及 %d 名成员，跳过即时重算，交由每日全量重建",
                    activity_id or "未知",
                    len(members),
                )
                return
            await _write_user_times(members)
        except Exception:
            logger.warning(
                "活动 %s 变更后重算 %d 名成员时长失败，保留缓存旧值",
                activity_id or "未知",
                len(members),
                exc_info=True,
            )


def _schedule_user_time_refresh(activity_id: str) -> None:
    task = asyncio.create_task(_refresh_user_time_for_activity(activity_id))
    _time_refresh_tasks.add(task)
    task.add_done_callback(_time_refresh_tasks.discard)


def _validated_activity(target_activity) -> Activity:
    try:
        return Activity.model_validate(target_activity, strict=False)
    except ValidationError:
        raise HTTPException(status_code=409, detail="活动数据不完整，无法完成该操作")


def _is_oracle_unique_violation(exc: IntegrityError) -> bool:
    """``IntegrityError`` 是不是 ORA-00001（唯一键冲突）。

    oracledb 把 ORA 错误码挂在 ``exc.orig.code``（新版驱动）或
    ``exc.orig.args[0].code``（老版/包装过的异常）上；两种都认，别只看一处。
    非 00001 的完整性错误（FK 失效、NOT NULL……）必须原样冒泡，不能被这里
    的 409 文案吞成「重复记录」。
    """
    orig = getattr(exc, "orig", None)
    if getattr(orig, "code", None) == 1:
        return True
    args = getattr(orig, "args", ()) or ()
    return bool(args) and getattr(args[0], "code", None) == 1


def _validate_create_fields(activity: Activity) -> None:
    """建活动前把三个定长列一起验掉，任何一列超宽都回 400 而不是 ORA-12899。"""
    for validator, value in (
        (validate_activity_name, activity.name),
        (validate_activity_place, activity.place),
        (validate_activity_approver, activity.approver),
    ):
        validated, reason = validator(value)
        if not validated:
            raise HTTPException(status_code=400, detail=reason)


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

    # 名称 + 地点 + 审核人三列一次校验：宽度以 DDL 为准，超了在写库前就是 400。
    _validate_create_fields(activity)

    validated, reason = validate_activity_description(activity.description)

    if not validated:
        raise HTTPException(status_code=400, detail=reason)

    activity.creator = str(user["id"])

    extra = {
        key: value
        for key, value in {
            "place": activity.place,
            "approver": activity.approver,
        }.items()
        if value
    }

    # 建活动 + 补写 place/approver + 审计必须在**同一个事务**里：原先是三次
    # 各自提交，中间任何一步失败都会留下「活动在、地点/审核人丢、审计也没有」
    # 的半成品，而且没人能凭返回的 500 判断到底写没写。
    async with session_scope() as session:
        async with session.begin():
            activities_repo = backends.get_activities_repo(session)
            oid = await activities_repo.create(
                activity.name,
                activity.description,
                activity.type,
                activity.creator,
                activity.appointee,
                activity.origin,
                activity.date,
            )

            if extra:
                await activities_repo.update(oid, extra)

            log.with_text(
                f'用户 {await get_user_display_name(user["id"])} 创建了义工活动《{activity.name}》（ID: {oid}）',
                type="activity_create",
            )
            await log.insert_log(session)

    return JSONResponse({"id": str(oid)}, status_code=201)


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

    if not activity:
        # 先判 404：不存在的 id 不该再花一次 COUNT 往返（远程 ADB 上一次就是
        # 一次跨洋 RTT）。
        raise HTTPException(status_code=404, detail="Activity not found")

    members = await backends.get_members_repo().count_for_activity(activity_id)

    return {"activity": activity, "members_count": members}


@router.delete("/{activity_id}")
async def delete_activity_v2(
    activity_id: str,
    user=Depends(compulsory_temporary_token),
    log=Depends(inject_log),
):
    """
    :param activity_id: ID of the activity to be deleted
    :param user: Current user (temporary/5-minute credential)
    :param log: Logger object
    :return: Deletion status
    """

    validate_object_id(activity_id)

    # 锁序：先父后子。v3 批量登记成员（activities_v3_router.py:233-235）也是先锁
    # 活动再动成员行；删除若反过来先删成员再删活动，双方会以相反顺序拿锁，
    # 并发下就是 ORA-00060。整段共用一个事务：成员删了活动没删、或反过来，
    # 都会留下没人能修的数据残骸。
    affected_members: list[str] = []
    async with session_scope() as session:
        async with session.begin():
            activities_repo = backends.get_activities_repo(session)
            target_activity = await activities_repo.get_for_update(activity_id)

            if not target_activity:
                raise HTTPException(status_code=404, detail="Activity not found")

            await volunteer.validate_delete_permission(
                user, _validated_activity(target_activity)
            )

            members_repo = backends.get_members_repo(session)
            # 受影响成员必须在删行**之前**抓：行没了就再也反查不出是谁的
            # user_time 需要重算，只能等每日全量。
            rows = await members_repo.list_by_activity(activity_id)
            affected_members = list(
                dict.fromkeys(str(row["member"]) for row in rows)
            )

            await members_repo.remove_for_activity(activity_id)
            await activities_repo.delete(activity_id)

            log.with_text(
                f'用户 {await get_user_display_name(user["id"])} 删除了义工活动《{target_activity["name"]}》（ID: {activity_id}）',
                type="activity_delete",
            )
            await log.insert_log(session)

    # 提交后按「抓到的成员」重算：这些行已经不存在，按活动反查只会得到空集。
    # 人数过多就交给每日全量（与 :36 的闸门同语义），不在请求里硬跑。
    if affected_members:
        await _refresh_user_time_for_members(affected_members, activity_id)

    return {"detail": "Activity deleted successfully"}


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

    # 路径上的活动必须真的是这条记录的归属：原实现完全不看 activity_id，
    # 换个活动 ID 也能读到任意成员的记录（活动 B 的 URL 泄露活动 A 的数据）。
    if str(target_member["activity"]) != str(activity_id):
        raise HTTPException(status_code=404, detail="Activity member not found")

    # 本人可读自己的记录；读别人的记录要 admin/department（与写路径同口径）。
    if str(target_member["member"]) != str(user["id"]):
        await volunteer_member.validate_update_permission(user)

    return target_member


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
    target_activity = _validated_activity(target_activity)
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
    user=Depends(compulsory_temporary_token),
    log=Depends(inject_log),
):
    """
    :param activity_id: ID of the activity to be retrieved
    :param user: Current user (temporary/5-minute credential)
    :param session_id: ID of the session. Since it's allowed to have *multiple* records in the same activity for the same
    person if the recoding mode is different, we must use another `session_id` to specify a document.
    :return: Activity object
    """
    validate_object_id(activity_id)
    validate_object_id(session_id)

    await volunteer_member.validate_update_permission(user)

    target_member = await backends.get_members_repo().get_by_oid(session_id)

    if not target_member:
        raise HTTPException(status_code=404, detail="Activity member not found")

    # 路径活动必须是这条记录的归属：原先只按 session_id 删，活动 ID 写错（或
    # 随便填一个别的活动）照样删掉真实记录，日志里还会写错活动名。
    if str(target_member["activity"]) != str(activity_id):
        raise HTTPException(status_code=404, detail="Activity member not found")

    target_activity = await backends.get_activities_repo().get(activity_id)
    activity_name = (
        target_activity.get("name") if target_activity else "未知活动"
    ) or "未知活动"

    member_oid = str(validate_object_id(target_member["member"]))

    await backends.get_members_repo().remove(session_id)

    # 删完之后按**这个成员本人**重算：按活动重算只会算剩下的成员，被删掉的
    # 那个人缓存里还留着旧时长，一直错到每日 01:00 的全量重建。
    try:
        await calculate_user_time(member_oid, allow_cache=False)
    except Exception:
        logger.warning(
            "成员 %s 被移出活动后重算时长失败，保留缓存旧值",
            member_oid,
            exc_info=True,
        )

    log.with_text(
        f'用户 {await get_user_display_name(user["id"])} 将成员 {await get_user_display_name(member_oid)} 移出义工活动《{activity_name}》（类型: {target_member.get("mode")}，时长: {target_member.get("duration")} 小时，ID: {activity_id}）',
        type="activity_member_remove",
    )
    await log.insert_log()

    return {"detail": "Activity member deleted successfully"}


class PutActivityStatus(BaseModel):
    status: Literal["effective", "pending", "refused"]


class PutMemberStatus(BaseModel):
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

    target_activity = _validated_activity(target_activity)

    if target_activity.status != "pending" and "admin" not in (user.get("per") or []):
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

    # CAS：期望状态写进 WHERE。两人同时审核时，只有先到的那个能改到行，
    # 后到的 rowcount=0 → 409；绝不重算、不写审计（否则两条互相矛盾的审计
    # 日志会同时存在，缓存也会按输家的意图刷一遍）。
    changed = await backends.get_activities_repo().cas_status(
        activity_id, target_activity.status, update_content
    )
    if not changed:
        raise HTTPException(
            status_code=409,
            detail="活动状态已被其他操作修改，请刷新后重试",
        )

    _schedule_user_time_refresh(activity_id)

    status_labels = {"effective": "生效", "pending": "待审核", "refused": "已驳回"}
    log.with_text(
        f'用户 {await get_user_display_name(user["id"])} 审核了义工活动《{target_activity.name}》，状态改为 {status_labels.get(status.status, status.status)}',
        type="activity_update",
    )
    await log.insert_log()

    return JSONResponse({"detail": "Activity status updated successfully"})


@router.put("/{activity_id}/members/{session_id}/status")
async def modify_activity_member_status_v2(
    activity_id: str,
    session_id: str,
    status: PutMemberStatus,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    :param activity_id: ID of the activity the membership belongs to
    :param session_id: ID of the membership row (not the member's user ID)
    :param status: Status to be set
    :param user: Current user
    :param log: Logger object

    :return: Updated membership ID and its new status
    """

    activity_id = str(validate_object_id(activity_id))
    session_id = str(validate_object_id(session_id))

    target_activity = await backends.get_activities_repo().get(activity_id)
    if not target_activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    target_member = await backends.get_members_repo().get_by_oid(session_id)
    if not target_member:
        raise HTTPException(status_code=404, detail="Activity member not found")

    if str(target_member["activity"]) != activity_id:
        raise HTTPException(status_code=404, detail="Activity member not found")

    member_oid = str(validate_object_id(target_member["member"]))
    target_user = await backends.get_users_repo().get_by_oid(member_oid)
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    await volunteer_member.validate_create_permission(
        user, target_user, target_activity
    )

    if status.status == target_member["status"]:
        raise HTTPException(
            status_code=400,
            detail="Activity member status is not changed",
        )

    # 成员状态同样走 CAS：审核是「读-判-写」，并发下两个管理员都能过 400 的
    # 同状态判断，后写的静默覆盖先写的并各自重算/写审计。
    changed = await backends.get_members_repo().cas_status(
        session_id, target_member["status"], status.status
    )
    if not changed:
        raise HTTPException(
            status_code=409,
            detail="成员状态已被其他操作修改，请刷新后重试",
        )

    # ``allow_cache=False`` recomputes from effective memberships and writes the
    # row back, so the next cached read already reflects this review.
    try:
        await calculate_user_time(member_oid, allow_cache=False)
    except Exception:
        logger.warning(
            "成员 %s 状态审核后重算时长失败，保留缓存旧值",
            member_oid,
            exc_info=True,
        )

    log.with_text(
        f'用户 {await get_user_display_name(user["id"])} 将义工活动《{target_activity.get("name") or "未知活动"}》成员 {await get_user_display_name(member_oid)} 的状态审核为 {status.status}',
        type="activity_member_review",
    )
    await log.insert_log()

    return {"id": session_id, "status": status.status}


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
    # 0/负数/NaN/Infinity 都不是合法时长：它们会静默污染聚合结果（NaN 一旦
    # 进缓存，所有求和都变 NaN），所以在入口就拦成 422。与
    # ``NewActivityMember.duration``（activities_v3_router.py:174）同口径，
    # 不发明业务上限。
    duration: float = Field(gt=0, allow_inf_nan=False)
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
    activity_id = str(validate_object_id(activity_id))
    document_id = str(validate_object_id(document_id))

    target_activity = await backends.get_activities_repo().get(activity_id)

    if not target_activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    target_member = await backends.get_members_repo().get_by_oid(document_id)

    if not target_member:
        raise HTTPException(status_code=404, detail="Activity member not found")

    # 记录必须属于路径上的活动，否则任意活动 ID 都能改到别人活动的成员。
    # 与成员状态端点（:514-515）同口径：不确认归属，也不泄露真实归属。
    if str(target_member["activity"]) != activity_id:
        raise HTTPException(status_code=404, detail="Activity member not found")

    await volunteer_member.validate_update_permission(user)

    # 计数模式必须与活动类型一致，与加成员端点（:345-348）同口径。
    activity_type = target_activity.get("type")
    if activity_type and activity_type != "hybrid" and activity_type != updated.mode:
        raise HTTPException(
            status_code=400, detail="Activity type and member mode do not match"
        )

    updated_data = updated.model_dump()
    updated_data["duration"] = float(updated_data["duration"])

    try:
        await backends.get_members_repo().update_fields(document_id, updated_data)
    except IntegrityError as exc:
        # 改 mode 会撞 uq_activity_members_dedup(member_oid, activity_oid,
        # member_mode)：同一个人在同一活动下已经有一条目标模式的记录。
        # 这是可预期的用户冲突（409），不是服务端故障；其余完整性错误
        # （FK / NOT NULL）照旧冒泡成 500，别被这段文案吞掉。
        if _is_oracle_unique_violation(exc):
            raise HTTPException(
                status_code=409,
                detail="该成员在此活动下已有相同计数模式的记录，请先删除或合并",
            )
        raise

    # ``allow_cache=False`` recomputes from effective memberships and writes the
    # row back, so the next cached read already reflects the new duration.
    member_oid = str(validate_object_id(target_member["member"]))
    try:
        await calculate_user_time(member_oid, allow_cache=False)
    except Exception:
        logger.warning(
            "成员 %s 时长修改后重算失败，保留缓存旧值",
            member_oid,
            exc_info=True,
        )

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
        user, _validated_activity(target_activity)
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
