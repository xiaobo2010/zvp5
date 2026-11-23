from typing import Literal, Optional

from fastapi import HTTPException

from db import backends
from db.ids import is_valid_oid
from util.object_id import validate_object_id


async def get_zvms_new_user(oid: str) -> Optional[dict]:
    """
    Fetch a user document from the live `zvms_new.users` collection.
    """
    return await backends.get_users_repo().get_by_oid(str(validate_object_id(oid)))


async def validate_read_user_permission(
    user: dict, target_user: str, applied: Literal["volunteer", "club"] = "volunteer"
):
    """
    Validate that the current user may read the target user's information.

    读取范围（从宽到窄）：

    * 本人；
    * ``admin`` / ``department``：全校；
    * 同班同学：同 ``class_id`` 内可见（班级是学校里最小的公开单位）；
    * 其余（跨班普通学生、外班 secretary）：403。

    之前的实现无条件 ``return True``，任何登录学生都能用 OID 枚举全校用户的
    义工时长与参与记录；这不是“读接口放宽”，而是把 ``USERS`` 表当成了公开
    通讯录。这里保留班级内互看，跨班一律拒绝。``secretary`` 不需要单独分支：
    其 ``class_id`` 与目标相同就已在上面的同班判断里。

    调用方：``get_user_activities_v2`` / ``get_user_time_v2`` /
    ``get_user_time_statistics_v2``，全部只读。写入端点各自有独立权限闸。
    """
    actor_oid = str(user.get("id", ""))
    if actor_oid and actor_oid == str(target_user):
        return True

    per = user.get("per") or []
    if "admin" in per or "department" in per:
        return True

    try:
        target_oid = str(validate_object_id(target_user))
    except Exception:
        raise HTTPException(status_code=403, detail="权限不足")

    target = await backends.get_users_repo().get_by_oid(target_oid)
    if target is None:
        raise HTTPException(status_code=403, detail="权限不足")

    actor_class = user.get("class_id")
    target_class = target.get("class_id")
    if actor_class and target_class and str(actor_class) == str(target_class):
        return True

    raise HTTPException(status_code=403, detail="权限不足")


def credential_change_forbidden(
    actor_oid: str, target_per: list[str], target_oid: str
) -> bool:
    """
    判断一次凭据变更是否被「等级闸」拦下。

    为什么要这条闸：双人授权（危险操作须两名不同管理员批准）的前提，是
    一个人的身份不能被另一个人冒充。若某管理员能改掉同级账号的密码、或把
    它重置成一个已知/可推导的初始密码，他就能以对方身份登录并「批准」自己
    的危险操作——发起人与审批人其实是同一个人，双人授权被彻底击穿。

    因此：**admin 级账号的凭据只有本人能动**，其他任何账号（含其他 admin）
    一律拒绝；其余等级（department / secretary / student）维持现状，不做
    额外收紧。纯判定、不查库：目标账号的权限由调用方查库后传入，便于单测。
    """
    if str(actor_oid) == str(target_oid):
        return False
    return "admin" in (target_per or [])


def role_change_forbidden(
    actor_oid: str, target_per: list[str], target_oid: str
) -> bool:
    """
    判断一次**角色变更**是否被「等级闸」拦下（与
    :func:`credential_change_forbidden` 同一套语义：admin 级账号只认本人）。

    两条拦停理由：

    1. **改别人的 admin 账号**：角色决定权限，能改别人的 admin 角色就等于能
       给自己造一个 admin（先降级对方再顶替其权限链），这会击穿 Stage 2 的双人
       授权前提（见 :func:`credential_change_forbidden` 的说明）。因此目标账号
       带 ``admin`` 且目标 ≠ 操作者 → 拒绝。
    2. **改自己的角色**：一条请求里同时包含「自我提权」（给自己加 ``admin``）
       与「自我降级/自锁」（把自己踢出 ``admin``，`_set_roles_stmt` 还会轮换
       ``session_id``，本地立即掉线）。两者都不可在无人复核的情况下自助发生，
       与 ``users_router`` 的「不能封禁自己」是同一类自锁风险，故**自我角色
       变更整体拒绝**（是「禁止自我提权」的超集）。

    纯判定、不查库：目标账号的角色由调用方查库后传入，便于单测。
    """
    if str(actor_oid) == str(target_oid):
        return True
    return "admin" in (target_per or [])


async def validate_read_group_permission(
    user: dict,
    target_group: str,
    applied: Literal["volunteer", "club"] = "volunteer",
    allow_class: bool = False,
):
    """
    Validate that the current user may read the target group's information.

    - admin/department: always allowed (unchanged).
    - ``allow_class=True`` and the target is a **class group** (any group that
      is not a permission group): any authenticated user is allowed. Callers
      must opt in explicitly — the default keeps the read restricted.
    - secretary: allowed only for their own class (unchanged).
    - otherwise: 403.

    A malformed ``target_group`` never raises ``InvalidOid``: the class-group
    rule is skipped and the request falls through to the same 403 as today.

    The secretary branch reads the caller's ``class_id`` off the auth dict when
    :func:`util.object_id._authenticate` supplied it (it always does for real
    requests: the key is present, possibly ``None``). That spares a re-read of
    the very document authentication just loaded. A hand-built ``user`` dict
    without the key keeps the old behaviour and re-reads the document, so the
    allow/deny decision is the same either way.
    """
    per = user.get("per") or []
    if "admin" in per or "department" in per:
        return True
    if allow_class and is_valid_oid(target_group):
        # Class groups are exactly the non-permission groups: the class side of
        # the groups table carries no ``type`` marker, and the repo exposes the
        # permission side through ``get_permission_group``.
        if await backends.get_groups_repo().get_permission_group(target_group) is None:
            return True
    if "secretary" in per:
        if "class_id" in user:
            class_id = user.get("class_id")
        else:
            current = await get_zvms_new_user(user["id"])
            class_id = current.get("class_id") if current is not None else None
        if class_id is not None and str(class_id) == target_group:
            return True
    raise HTTPException(status_code=403, detail="权限不足")