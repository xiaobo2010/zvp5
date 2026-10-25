"""agent 工具的数据层（共享查询实现）。

这一层是**纯数据**：不判权限、不写审计、不拼 OpenAI 消息。权限在
``services.agent.tools`` 的注册表里（能力限制在工具定义层，而不是靠 prompt），
循环与计费在 ``services.agent.executor``，写操作的提案在
``services.agent.proposal``。

为什么把查询逻辑单独放这里而不是直接让工具调用路由函数：MCP 端点
（``routers/mcp_router``）与 agent 工具必须回答**同一份数据**。两边各写一遍
「怎么查活动、怎么投影字段」，第一周看起来等价，第一次改投影就会漂移——而
「agent 说的和页面显示的不一样」是最难排查的一类问题。所以 mcp_router 现在只保留
权限判定与响应外壳，核心逻辑全部在这里。

``AgentError`` 定义在本模块（包内最底层，无环依赖）：它是整个
``services.agent`` 的可预期错误类型，路由层把它翻译成 HTTP 状态码。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal

from db import backends
from db.repos.oracle.members import escape_oracle_regex
from util.activity_statistics import describe_durations, to_mcp_statistics
from util.calculate import calculate_user_time

_logger = logging.getLogger(__name__)

ACTIVITY_TYPES = ("on-campus", "off-campus", "social-practice", "hybrid")

# MCP 端点的历史投影：字段集与顺序都是对外契约（既有 MCP 客户端按这套键读），
# 抽到共享层时逐字保留。
MCP_ACTIVITY_KEYS = (
    "_id",
    "name",
    "description",
    "type",
    "date",
    "location",
    "duration",
    "status",
    "creator",
    "createdAt",
    "updatedAt",
)

MCP_ACTIVITY_DETAIL_KEYS = (
    "name",
    "description",
    "type",
    "date",
    "location",
    "duration",
    "status",
    "creator",
    "createdAt",
    "updatedAt",
)

# 写工具的成员状态封闭集合（与 ``activities_v2_router.PutMemberStatus`` 同值）。
MEMBER_STATUSES = ("effective", "pending", "refused")

_STATUS_LABELS = {"effective": "生效", "pending": "待审核", "refused": "已驳回"}


class AgentError(Exception):
    """``services.agent`` 的可预期错误；路由层按 ``status_code`` 回协议体。

    ``retry_after`` 非空时进 ``Retry-After`` 头（频控），``headers`` 用于额度类
    错误把「已用 / 上限」带回去，与 ``/v1/chat/completions`` 的 429 同款。
    """

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        *,
        retry_after: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retry_after = retry_after
        self.headers = dict(headers or {})


def to_json_text(value: object) -> str:
    """工具结果 → 给模型看的 JSON 文本（datetime / Decimal 转字符串）。

    上游拿到的必须是可解析的 JSON：``json.dumps`` 遇到 ``datetime`` 会直接抛，
    而这种失败发生在「工具已经查成功了」之后，看起来像工具坏了。
    """

    def _default(item: object) -> str:
        if isinstance(item, (datetime, date)):
            return item.isoformat()
        if isinstance(item, Decimal):
            return str(item)
        return str(item)

    return json.dumps(value, ensure_ascii=False, default=_default)


def _project(row: dict, keys: tuple[str, ...]) -> dict:
    return {key: row[key] for key in keys if key in row}


# ---------------------------------------------------------------------------
# 活动（MCP 五个端点的核心逻辑）
# ---------------------------------------------------------------------------


def normalize_activity_types(activity_type: object) -> list[str]:
    """``all`` / 空 / 非法 → 全类型；逗号分隔的合法子集原样保留。

    与 ``routers/mcp_router`` 的原实现逐字一致：过滤值写错时**回退到全量**而不是
    返回空页——「筛出来是空的」与「条件写错了」长得一模一样，回退更容易发现。
    """
    if not isinstance(activity_type, str) or activity_type in ("", "all"):
        return list(ACTIVITY_TYPES)
    target = [item for item in activity_type.split(",") if item in ACTIVITY_TYPES]
    return target or list(ACTIVITY_TYPES)


async def list_activities(
    *,
    page: int = 1,
    perpage: int = 10,
    search: str = "",
    activity_type: str = "all",
    sort: str = "_id",
    asc: bool = False,
) -> dict:
    """一页活动（MCP 投影）+ 分页信息。"""
    rows, count = await backends.get_activities_repo().list_raw(
        search=re.escape(search) if search else "",
        atypes=normalize_activity_types(activity_type),
        sort=sort,
        asc=asc,
        page=page,
        size=perpage,
    )
    return {
        "activities": [_project(row, MCP_ACTIVITY_KEYS) for row in rows],
        "pagination": {
            "total": count,
            "page": page,
            "perpage": perpage,
            "totalPages": (count + perpage - 1) // perpage,
        },
    }


async def search_activities(*, query: str, limit: int = 10) -> dict:
    """按关键词找活动（agent 的入口工具）：复用列表查询，投影与 MCP 同形。"""
    return await list_activities(
        page=1, perpage=limit, search=query, activity_type="all", sort="_id", asc=False
    )


async def get_activity_detail(activity_id: str) -> dict:
    """单个活动详情 + 成员数。

    活动不存在时抛 :class:`AgentError`（404）：MCP 端点需要的是 HTTP 语义，
    共享层给错误码，路由层决定怎么回。
    """
    activity = await backends.get_activities_repo().get(activity_id)
    if not activity:
        raise AgentError("AI_AGENT_NOT_FOUND", "activity not found", 404)
    data = _project(activity, MCP_ACTIVITY_DETAIL_KEYS)
    data["id"] = activity_id
    data["members_count"] = await backends.get_members_repo().count_for_activity(
        activity_id
    )
    return data


async def activity_statistics(activity_id: str) -> dict:
    """活动的时长统计（MCP 契约：``total_members`` / 均值 / 中位数 / …）。"""
    if not await backends.get_activities_repo().get(activity_id):
        raise AgentError("AI_AGENT_NOT_FOUND", "activity not found", 404)

    # 与时长重建口径一致：只有 effective 的成员计数（pending/refused 不算）。
    members = await backends.get_members_repo().list_by_activity(
        activity_id, status="effective"
    )
    if not members:
        return {"total_members": 0, "statistics": None}

    distribution = describe_durations([float(member["duration"]) for member in members])
    if distribution is None:
        return {"total_members": 0, "statistics": None}
    return to_mcp_statistics(distribution)


async def activity_types_summary() -> dict:
    """按活动类型聚合的条数与状态分布。"""
    summary: dict[str, dict] = {}
    total = 0
    for result in await backends.get_activities_repo().types_summary():
        count = result["count"]
        total += count
        summary[result["type"]] = {
            "count": count,
            "status_breakdown": result.get("status_breakdown", {}),
        }
    return {"total_activities": total, "by_type": summary}


async def activity_search_suggestions(*, query: str, limit: int = 10) -> dict:
    """活动名联想（``_id`` / ``name`` / ``type``）。"""
    rows, _total = await backends.get_activities_repo().list_raw(
        search=re.escape(query), sort=None, page=1, size=limit
    )
    return {
        "query": query,
        "suggestions": [_project(row, ("_id", "name", "type")) for row in rows],
    }


async def list_activity_members(
    *,
    activity_id: str,
    page: int = 1,
    perpage: int = 10,
    search: str = "",
    sort: str = "_id",
    asc: bool = True,
) -> dict:
    """活动的成员名单（与 ``GET /api/v2/activities/{id}/members`` 同形）。

    ``search`` 先按姓名/学号把用户缩成 oid 集合，再按 oid 过滤成员——与路由层
    完全相同的两步查询；不这么做就得在成员表上 join 用户表，而仓储没有那条语句。
    """
    if not await backends.get_activities_repo().get(activity_id):
        raise AgentError("AI_AGENT_NOT_FOUND", "activity not found", 404)

    pattern = re.escape(search) if search else ""
    searched_users = await backends.get_users_repo().list_by_name_or_id(pattern)
    member_ids = [str(item["_id"]) for item in searched_users]

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


# ---------------------------------------------------------------------------
# 用户 / 分组
# ---------------------------------------------------------------------------


async def user_volunteer_time(user_id: str) -> dict:
    """某用户的义工时长（缓存口径，与 ``GET /api/v2/users/{id}/time`` 一致）。"""
    return await calculate_user_time(user_id, allow_cache=True)


async def user_activities(*, user_id: str, page: int = 1, perpage: int = 10) -> dict:
    """某用户参与的活动 + 他自己的那行成员记录（``mine``）。"""
    search = escape_oracle_regex("")
    rows, total = await backends.get_members_repo().list_for_user_activities(
        user_id, search, "_id", False, page, perpage
    )
    activities: list[dict] = []
    for row in rows:
        activity = row["activity"]
        if activity is None:
            # 活动被删了但成员行还在：跳过而不是造一个空活动（与路由层一致）。
            continue
        mine = row["member"]
        mine.pop("activity", None)
        mine["_id"] = str(mine["_id"])
        activity["mine"] = mine
        activity["_id"] = str(activity["_id"])
        activities.append(activity)
    return {"page": page, "perpage": perpage, "total": total, "activities": activities}


async def list_groups(
    *, page: int = 1, size: int = 10, include_permission: bool = True
) -> list[dict]:
    """班级（+ 权限组）列表。

    ``include_permission=False`` 时只回班级：与 ``GET /api/groups`` 对无特权
    调用方的降级行为一致——不报 403，只是不给权限组清单。由调用方按角色决定。
    """
    repo = backends.get_groups_repo()
    permission_groups = await repo.list_permission_groups("")
    permission_ids = {str(group["_id"]) for group in permission_groups}

    rows: list[dict] = []
    class_groups, _total = await repo.list_classes("", 1, _CLASS_LIST_CAP)
    for group in class_groups:
        if str(group["_id"]) in permission_ids:
            continue
        rows.append({"_id": str(group["_id"]), "name": group["name"], "type": "class"})

    if include_permission:
        for group in permission_groups:
            rows.append(
                {
                    "_id": str(group["_id"]),
                    "name": group["name"],
                    "type": "permission",
                    "permissions": group.get("permissions", []),
                }
            )

    rows.sort(key=lambda group: group["name"])
    start = (page - 1) * size
    return rows[start : start + size]


# 与 ``routers/groups_router._CLASS_LIST_CAP`` / ``db.repos.oracle.groups._CLASS_LIST_CAP``
# 同值：路由层先按上限取一页班级再在内存里合并权限组，共享层照抄这个上限。
_CLASS_LIST_CAP = 1000


# ---------------------------------------------------------------------------
# 写操作（只在用户确认提案后执行）
# ---------------------------------------------------------------------------


async def _load_membership(activity_id: str, member_id: str) -> tuple[dict, dict]:
    """取 (活动, 成员行)，并确认成员行真的属于路径上的活动。

    归属校验与 ``activities_v2_router`` 的成员状态/时长端点同口径：不确认归属，
    任意活动 id 就能改到别人活动的成员。``member_id`` 是成员行 oid（session id），
    不是用户 oid —— 与 REST 路径参数同义。
    """
    activity = await backends.get_activities_repo().get(activity_id)
    if not activity:
        raise AgentError("AI_AGENT_NOT_FOUND", "activity not found", 404)

    member = await backends.get_members_repo().get_by_oid(member_id)
    if not member:
        raise AgentError("AI_AGENT_NOT_FOUND", "activity member not found", 404)
    if str(member["activity"]) != activity_id:
        # 不泄露真实归属：与 REST 同款「查不到」。
        raise AgentError("AI_AGENT_NOT_FOUND", "activity member not found", 404)
    return activity, member


async def _refresh_member_time(member_oid: str) -> None:
    """时长缓存重算（``allow_cache=False`` 会把结果写回库里）。

    重算失败不改结论：成员行已经更新了，缓存旧值最多让「我的时长」晚几分钟
    才反映这次改动 —— 与两个 REST 端点同样「只记 warning」。
    """
    try:
        await calculate_user_time(member_oid, allow_cache=False)
    except Exception:  # noqa: BLE001 - 缓存重算失败不影响已完成的写
        _logger.warning(
            "成员 %s 审核/改时长后重算时长失败，保留缓存旧值", member_oid, exc_info=True
        )


async def apply_member_duration(
    *, activity_id: str, member_id: str, duration: float
) -> dict:
    """设定成员的时长（小时，非负）。

    计数模式（``mode``）沿用成员行上已有的值：工具只暴露「改时长」一件事，
    改模式是另一条语义（会改变时长归入哪一类），不在这里顺手做。活动类型与
    mode 的一致性校验保留（``hybrid`` 之外必须相等），与 REST 端点同口径。
    """
    try:
        value = float(duration)
    except (TypeError, ValueError):
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT", "duration must be a number", 400
        ) from None
    if value < 0:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT", "duration must not be negative", 400
        )

    activity, member = await _load_membership(activity_id, member_id)
    activity_type = activity.get("type")
    mode = member.get("mode")
    if activity_type and activity_type != "hybrid" and activity_type != mode:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            "activity type and member mode do not match",
            400,
        )

    await backends.get_members_repo().update_fields(
        member_id, {"duration": value, "mode": mode}
    )
    await _refresh_member_time(str(member["member"]))
    return {
        "id": member_id,
        "activity_id": activity_id,
        "duration": value,
        "mode": mode,
    }


async def apply_member_status(
    *, activity_id: str, member_id: str, status: str
) -> dict:
    """审核成员的参与状态（effective / pending / refused）。"""
    if status not in MEMBER_STATUSES:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            f"status must be one of: {', '.join(MEMBER_STATUSES)}",
            400,
        )

    _activity, member = await _load_membership(activity_id, member_id)
    if status == member.get("status"):
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT", "activity member status is not changed", 400
        )

    await backends.get_members_repo().update_fields(member_id, {"status": status})
    await _refresh_member_time(str(member["member"]))
    return {
        "id": member_id,
        "activity_id": activity_id,
        "status": status,
        "label": _STATUS_LABELS.get(status, status),
    }


__all__ = [
    "ACTIVITY_TYPES",
    "AgentError",
    "MEMBER_STATUSES",
    "activity_search_suggestions",
    "activity_statistics",
    "activity_types_summary",
    "apply_member_duration",
    "apply_member_status",
    "get_activity_detail",
    "list_activities",
    "list_activity_members",
    "list_groups",
    "normalize_activity_types",
    "search_activities",
    "to_json_text",
    "user_activities",
    "user_volunteer_time",
]
