"""MCP（Model Context Protocol）只读端点。

这些端点给 AI agent 与外部工具提供**只读**的活动数据：没有敏感操作，也没有任何
鉴权旁路——每个端点都单独判 admin，与 ``/api/v2/*`` 的写面完全隔离。

审批与响应外壳（``{"success": true, "data": ...}``）留在这里，查询逻辑全部搬到了
``services.agent.tool_impl``：agent 工具与 MCP 端点必须回答同一份数据，各写一遍
「怎么查活动、怎么投影字段」第一次改投影就会漂移。

``validate_object_id`` 的调用刻意留在本模块：非法 id 的 400 文案
（``Invalid Object ID format``）是既有契约，且只该由 HTTP 层决定；共享层给的是
``AgentError(404)``，由这里翻译成 MCP 的 ``Activity not found``。
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query

from services.agent import tool_impl
from services.agent.tool_impl import AgentError
from util.object_id import get_current_user, validate_object_id

router = APIRouter()

NOT_FOUND_DETAIL = "Activity not found"


def _require_admin(user: dict) -> None:
    if "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="权限不足")


def _activity_error(exc: AgentError) -> HTTPException:
    """共享层的错误 → MCP 的历史文案（形状与状态码都不变）。"""
    return HTTPException(status_code=exc.status_code, detail=NOT_FOUND_DETAIL)


@router.get("/activities")
async def mcp_get_activities(
    page: int = Query(1, ge=1, description="Page number"),
    perpage: int = Query(10, ge=1, le=100, description="Items per page"),
    search: str = Query("", description="Search term for activity names"),
    activity_type: str = Query("all", description="Filter by activity type"),
    sort: str = Query("_id", description="Sort field"),
    asc: bool = Query(False, description="Sort in ascending order"),
    user=Depends(get_current_user),
) -> Dict[str, Any]:
    """
    MCP endpoint to retrieve activities with pagination and filtering.
    Read-only access to activity data for AI agents.
    """
    _require_admin(user)

    data = await tool_impl.list_activities(
        page=page,
        perpage=perpage,
        search=search,
        activity_type=activity_type,
        sort=sort,
        asc=asc,
    )
    return {"success": True, "data": data}


@router.get("/activities/{activity_id}")
async def mcp_get_activity_details(
    activity_id: str, user=Depends(get_current_user)
) -> Dict[str, Any]:
    """
    MCP endpoint to retrieve detailed information about a specific activity.
    Read-only access for AI agents.
    """
    _require_admin(user)

    try:
        activity_oid = validate_object_id(activity_id)
    except (HTTPException, ValueError):
        raise HTTPException(status_code=400, detail="Invalid activity ID format")

    try:
        data = await tool_impl.get_activity_detail(activity_oid)
    except AgentError as exc:
        raise _activity_error(exc) from None

    return {"success": True, "data": data}


@router.get("/activities/{activity_id}/statistics")
async def mcp_get_activity_statistics(
    activity_id: str, user=Depends(get_current_user)
) -> Dict[str, Any]:
    """
    MCP endpoint to retrieve statistical data for an activity.
    Provides aggregated statistics without exposing individual member data.
    """
    _require_admin(user)

    try:
        validate_object_id(activity_id)
    except (HTTPException, ValueError):
        raise HTTPException(status_code=400, detail="Invalid activity ID format")

    try:
        data = await tool_impl.activity_statistics(activity_id)
    except AgentError as exc:
        raise _activity_error(exc) from None

    return {"success": True, "data": data}


@router.get("/activities/types/summary")
async def mcp_get_activity_types_summary(
    user=Depends(get_current_user),
) -> Dict[str, Any]:
    """
    MCP endpoint to get a summary of activities by type.
    Provides aggregate counts without sensitive details.
    """
    _require_admin(user)

    return {"success": True, "data": await tool_impl.activity_types_summary()}


@router.get("/activities/search/suggestions")
async def mcp_get_activity_search_suggestions(
    query: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(10, ge=1, le=20, description="Maximum suggestions"),
    user=Depends(get_current_user),
) -> Dict[str, Any]:
    """
    MCP endpoint to get activity name suggestions for search functionality.
    Helps AI agents provide better search assistance.
    """
    _require_admin(user)

    return {
        "success": True,
        "data": await tool_impl.activity_search_suggestions(query=query, limit=limit),
    }

