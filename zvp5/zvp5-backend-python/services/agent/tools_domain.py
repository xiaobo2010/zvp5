"""用户/义工管理专用工具（1 读 + 4 写），补上「通用 DB 写」被关掉后的缺口。

**为什么需要这一层**：``db_insert`` / ``db_update`` 让模型直接写整行，字段语义
（``ACTIVITIES.CREATOR_OID`` / ``APPROVER`` / ``STATUS``）由模型自由填写——这既
是“义工管理没有工具、只能改库”的根因，也是「可以轻松伪造 owner 等字段」的直接
通道。本模块只提供四个**窄接口**，身份字段全部由服务端从当前登录用户派生：

* ``activity_create`` —— 创建人/负责人 = 操作者，初始状态固定 ``pending``；
* ``activity_update`` —— 只允许改名称/描述（与 ``PUT /activities/{id}/info`` 同字段）；
* ``activity_review`` —— 审核人 = 操作者，``pending → effective/refused`` 走 CAS；
* ``user_set_class`` —— 只改班级归属（admin/department，与 ``/groups/{id}/users``
  同一口径），不碰角色/密码/学号。

数据层实现在 ``tool_impl``，复用活动域既有仓储与校验函数（``validate_*``、
``repo.create`` / ``repo.cas_status``、``volunteer.validate_check_permission``），
不重写活动生命周期规则；写入沿用活动域既有口径（提案 + 本人确认，不要 TOTP），
确认/重放时由 ``guard`` 现查库。

账号供给（建号/初始密码）、角色变更、凭据重置**刻意不在本模块**：那属于账号
供给与提权，需要比 agent 提案更强的人工流程（密码再认证、角色等级闸）。通用 DB
写已对这些表做表级拒绝（``policy.GENERIC_WRITE_BLOCKED_TABLES`` +
``services.db_content`` 的服务层保护），agent 没有绕过它们的通道。
"""

from __future__ import annotations

import logging
from typing import Any

from db import backends
from db.ids import is_valid_oid
from db.oracle.session import session_scope
from util.ban import ban_is_active
from util.group import get_user_permissions

from . import tool_impl
from .ops_tools import _cap, _draft, _params
from .tool_impl import AgentError
from .tools import READ, WRITE, ToolDef, _member_writer, _privileged, _register

logger = logging.getLogger(__name__)

MAX_USERS_PAGE = 100


# --------------------------------------------------------------------------- #
# 现查库的权限闸（token 里的 ``per`` 只是快照）
# --------------------------------------------------------------------------- #


async def ensure_volunteer_manager(user: dict) -> None:
    """确认「此刻仍是 admin/department 且未被封禁」。

    复用 ``util.group.get_user_permissions``（与 REST 端点同一口径）而不是复制
    角色判断；读的是**当前库里的行**（独立会话，不经过用户读穿缓存），撤权与
    封禁在提案确认时立刻生效。任何异常按拒绝处理（fail closed）。
    """
    actor = str(user.get("id") or "")
    if not actor:
        raise AgentError("AI_AGENT_FORBIDDEN", "actor is required", 403)
    try:
        async with session_scope() as session:
            row = await backends.get_users_repo(session).get_by_oid(actor)
        if row is None:
            raise AgentError("AI_AGENT_FORBIDDEN", "权限不足", 403)
        if ban_is_active(row):
            raise AgentError("USER_BANNED", "当前账号处于封禁状态", 403)
        perms = set(await get_user_permissions(row) or [])
    except AgentError:
        raise
    except Exception:  # noqa: BLE001 - 闸门故障按拒绝处理（fail closed）
        raise AgentError("AI_AGENT_FORBIDDEN", "权限不足", 403) from None
    if not ({"admin", "department"} & perms):
        raise AgentError("AI_AGENT_FORBIDDEN", "权限不足", 403)


# --------------------------------------------------------------------------- #
# 读：用户查询（admin/department；按角色过滤仅 admin）
# --------------------------------------------------------------------------- #


def _activity_name(activity: dict | None) -> str:
    return str((activity or {}).get("name") or "未知活动")


async def _impl_user_search(
    user: dict,
    query: str = "",
    role: str = "",
    page: int = 1,
    size: int = 10,
) -> dict:
    return _cap(
        {
            "kind": "users",
            **await tool_impl.search_users(
                user, query=query, role=role, page=page, size=size
            ),
        }
    )


async def _prepare_user_set_class(
    user: dict, target: str, class_id: str = ""
) -> Any:
    row = await tool_impl.resolve_user(target)
    class_name = None
    if str(class_id or "").strip() and is_valid_oid(class_id):
        class_name = (await backends.get_groups_repo().get(class_id) or {}).get("name")
    return _draft(
        f"把 {row.get('name')} 的班级 {row.get('class_id') or '（无）'} → "
        f"{class_name or class_id or '（无）'}",
        {
            "kind": "user_set_class",
            "oid": str(row.get("_id") or ""),
            "name": row.get("name"),
            "business_id": row.get("id"),
            "from_class": str(row.get("class_id") or "") or None,
            "to_class": class_id or None,
            "class_name": class_name,
            "note": "只改班级归属；角色/密码/学号不在本工具参数内",
        },
    )


async def _impl_user_set_class(
    user: dict, target: str, class_id: str = ""
) -> dict:
    result = await tool_impl.set_user_class(
        user, target=target, class_id=class_id
    )
    return _cap({"kind": "user_set_class", **result})


# --------------------------------------------------------------------------- #
# 写：活动（创建人/审核人由服务端派生）
# --------------------------------------------------------------------------- #


async def _prepare_activity_create(
    user: dict,
    name: str,
    activity_type: str,
    date_value: str,
    description: str = "",
    place: str = "",
) -> Any:
    actor = str(user.get("id") or "")
    return _draft(
        f"创建义工活动《{name}》（{activity_type}，{date_value}）",
        {
            "kind": "activity_create",
            "name": name,
            "type": activity_type,
            "date_value": date_value,
            "place": place,
            "description": description[:500],
            "description_chars": len(description),
            # 伪造面：以下字段没有工具参数，全部由服务端派生。
            "server_derived": {
                "creator": actor,
                "appointee": actor,
                "approver": "",
                "origin": "activities",
                "status": "pending",
            },
        },
    )


async def _impl_activity_create(
    user: dict,
    name: str,
    activity_type: str,
    date_value: str,
    description: str = "",
    place: str = "",
) -> dict:
    result = await tool_impl.create_activity(
        user,
        name=name,
        description=description,
        activity_type=activity_type,
        date_value=date_value,
        place=place,
    )
    return _cap({"kind": "activity_create", **result})


async def _prepare_activity_update(
    user: dict, activity_id: str, name: str, description: str
) -> Any:
    activity = await backends.get_activities_repo().get(activity_id)
    if activity is None:
        raise AgentError("AI_AGENT_NOT_FOUND", "activity not found", 404)
    return _draft(
        f"修改活动《{_activity_name(activity)}》的名称/描述",
        {
            "kind": "activity_update",
            "activity_id": activity_id,
            "from": {
                "name": activity.get("name"),
                "description": activity.get("description"),
            },
            "to": {"name": name, "description": description},
            "note": "只改名称/描述；创建人、状态、审核人不在本工具参数内",
        },
    )


async def _impl_activity_update(
    user: dict, activity_id: str, name: str, description: str
) -> dict:
    result = await tool_impl.update_activity_info(
        user, activity_id=activity_id, name=name, description=description
    )
    return _cap({"kind": "activity_update", **result})


async def _prepare_activity_review(
    user: dict, activity_id: str, status: str
) -> Any:
    activity = await backends.get_activities_repo().get(activity_id)
    if activity is None:
        raise AgentError("AI_AGENT_NOT_FOUND", "activity not found", 404)
    return _draft(
        f"审核活动《{_activity_name(activity)}》→ {status}",
        {
            "kind": "activity_review",
            "activity_id": activity_id,
            "activity_name": activity.get("name"),
            "from": activity.get("status"),
            "to": status,
            "server_derived": {"approver": str(user.get("id") or "")},
            "note": "确认时以库里的当前状态做 CAS；已被他人审核会返回 409",
        },
    )


async def _impl_activity_review(
    user: dict, activity_id: str, status: str
) -> dict:
    result = await tool_impl.review_activity(
        user, activity_id=activity_id, status=status
    )
    return _cap({"kind": "activity_review", **result})


# --------------------------------------------------------------------------- #
# 注册（1 读 + 3 写）
# --------------------------------------------------------------------------- #

_STR = {"type": "string"}
_INT = {"type": "integer"}

_ACTIVITY_TYPE = {
    **_STR,
    "enum": list(tool_impl.ACTIVITY_TYPES),
    "description": "活动类型",
}

_ROLE_ENUM = list(tool_impl.ROLE_WHITELIST)


_register(
    ToolDef(
        name="user_search",
        description=(
            "按关键词/角色分页查询用户（白名单投影：姓名、学号、班级、角色、封禁状态；"
            "不含密码/会话）。admin/department 可查，按角色过滤仅 admin。只读。"
        ),
        kind=READ,
        params=_params(
            {
                "query": {**_STR, "description": "姓名或学号关键词（空=全部）"},
                "role": {
                    **_STR,
                    "enum": _ROLE_ENUM,
                    "description": "可选：按角色过滤（仅 admin）",
                },
                "page": {**_INT, "minimum": 1, "description": "页码（从 1 起）"},
                "size": {
                    **_INT,
                    "minimum": 1,
                    "maximum": MAX_USERS_PAGE,
                    "description": f"每页条数（1-{MAX_USERS_PAGE}，默认 10）",
                },
            },
            (),
        ),
        permission=_privileged,
        impl=_impl_user_search,
        guard=ensure_volunteer_manager,
    )
)


_register(
    ToolDef(
        name="user_set_class",
        description=(
            "把用户移入某个班级（class_id 为空 = 移出当前班级）。只改班级归属，"
            "不改角色/密码/学号。写操作：先生成提案，确认后执行。"
        ),
        kind=WRITE,
        params=_params(
            {
                "target": {**_STR, "description": "目标用户 oid 或学号"},
                "class_id": {
                    **_STR,
                    "description": "班级 oid；空字符串表示移出当前班级",
                },
            },
            ("target",),
        ),
        permission=_privileged,
        impl=_impl_user_set_class,
        prepare=_prepare_user_set_class,
        guard=ensure_volunteer_manager,
    )
)


_register(
    ToolDef(
        name="activity_create",
        description=(
            "创建义工活动（初始状态 pending）。创建人/负责人/审核人字段由服务端取当前"
            "登录用户，模型不可指定。写操作：先生成提案，确认后执行。"
        ),
        kind=WRITE,
        params=_params(
            {
                "name": {**_STR, "description": "活动名称"},
                "activity_type": _ACTIVITY_TYPE,
                "date_value": {
                    **_STR,
                    "description": "活动日期时间（ISO-8601，例如 2026-01-31T12:00:00）",
                },
                "description": {**_STR, "description": "活动描述（可空）"},
                "place": {**_STR, "description": "活动地点（可空）"},
            },
            ("name", "activity_type", "date_value"),
        ),
        permission=_member_writer,
        impl=_impl_activity_create,
        prepare=_prepare_activity_create,
        guard=ensure_volunteer_manager,
    )
)


_register(
    ToolDef(
        name="activity_update",
        description=(
            "修改活动名称/描述（只允许这两个字段；创建人、状态、审核人不可经此修改）。"
            "写操作：先生成提案，确认后执行。"
        ),
        kind=WRITE,
        params=_params(
            {
                "activity_id": {**_STR, "description": "活动 oid"},
                "name": {**_STR, "description": "新的活动名称"},
                "description": {**_STR, "description": "新的活动描述"},
            },
            ("activity_id", "name", "description"),
        ),
        permission=_member_writer,
        impl=_impl_activity_update,
        prepare=_prepare_activity_update,
        guard=ensure_volunteer_manager,
    )
)


_register(
    ToolDef(
        name="activity_review",
        description=(
            "审核活动（pending → effective/refused）。审核人由服务端写为当前登录用户。"
            "写操作：先生成提案，确认后执行；并发下以库内状态做 CAS，冲突返回 409。"
        ),
        kind=WRITE,
        params=_params(
            {
                "activity_id": {**_STR, "description": "活动 oid"},
                "status": {
                    **_STR,
                    "enum": list(tool_impl.REVIEW_STATUSES),
                    "description": "审核结果（effective=通过，refused=驳回）",
                },
            },
            ("activity_id", "status"),
        ),
        permission=_member_writer,
        impl=_impl_activity_review,
        prepare=_prepare_activity_review,
        guard=ensure_volunteer_manager,
    )
)


__all__ = ["ensure_volunteer_manager"]
