from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Dict, Any
from db import backends
from util.activity_statistics import describe_durations, to_mcp_statistics
from util.object_id import get_current_user, validate_object_id
import re

router = APIRouter()

# MCP (Model Context Protocol) Server Endpoints
# These endpoints provide read-only access to activity data for AI agents and tools
# No sensitive operations or authentication bypasses are included


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
    if "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="权限不足")

    # Validate activity types
    if activity_type == "all" or activity_type == "":
        target_types = ["on-campus", "off-campus", "social-practice", "hybrid"]
    else:
        target_types = activity_type.split(",")
        valid_types = ["on-campus", "off-campus", "social-practice", "hybrid"]
        target_types = [t for t in target_types if t in valid_types]
        if not target_types:
            target_types = valid_types

    # Escape search term for safety
    search = re.escape(search) if search else ""

    activities, count = await backends.get_activities_repo().list_raw(
        search=search,
        atypes=target_types,
        sort=sort,
        asc=asc,
        page=page,
        size=perpage,
    )

    projected_keys = [
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
    ]
    activities = [
        {key: activity[key] for key in projected_keys if key in activity}
        for activity in activities
    ]

    return {
        "success": True,
        "data": {
            "activities": activities,
            "pagination": {
                "total": count,
                "page": page,
                "perpage": perpage,
                "totalPages": (count + perpage - 1) // perpage,
            },
        },
    }


@router.get("/activities/{activity_id}")
async def mcp_get_activity_details(
    activity_id: str, user=Depends(get_current_user)
) -> Dict[str, Any]:
    """
    MCP endpoint to retrieve detailed information about a specific activity.
    Read-only access for AI agents.
    """

    if "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="权限不足")

    try:
        activity_oid = validate_object_id(activity_id)
    except (HTTPException, ValueError):
        raise HTTPException(status_code=400, detail="Invalid activity ID format")

    activity = await backends.get_activities_repo().get(activity_id)

    if not activity:
        raise HTTPException(status_code=404, detail="Activity not found")

    # Get member count
    members_count = await backends.get_members_repo().count_for_activity(activity_id)

    projected_keys = [
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
    ]
    data = {key: activity[key] for key in projected_keys if key in activity}
    data["id"] = activity_id
    data["members_count"] = members_count

    return {"success": True, "data": data}


@router.get("/activities/{activity_id}/statistics")
async def mcp_get_activity_statistics(
    activity_id: str, user=Depends(get_current_user)
) -> Dict[str, Any]:
    """
    MCP endpoint to retrieve statistical data for an activity.
    Provides aggregated statistics without exposing individual member data.
    """

    if "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="权限不足")

    try:
        validate_object_id(activity_id)
    except (HTTPException, ValueError):
        raise HTTPException(status_code=400, detail="Invalid activity ID format")

    # Check if activity exists
    activity_exists = await backends.get_activities_repo().get(activity_id)

    if not activity_exists:
        raise HTTPException(status_code=404, detail="Activity not found")

    # Get the effective members' durations for statistics (same population as
    # the reconstruction: pending/refused memberships are not counted)
    members = await backends.get_members_repo().list_by_activity(
        activity_id, status="effective"
    )

    if not members:
        return {"success": True, "data": {"total_members": 0, "statistics": None}}

    durations = [float(member["duration"]) for member in members]
    distribution = describe_durations(durations)
    if distribution is None:
        return {"success": True, "data": {"total_members": 0, "statistics": None}}

    return {"success": True, "data": to_mcp_statistics(distribution)}


@router.get("/activities/types/summary")
async def mcp_get_activity_types_summary(
    user=Depends(get_current_user),
) -> Dict[str, Any]:
    """
    MCP endpoint to get a summary of activities by type.
    Provides aggregate counts without sensitive details.
    """

    if "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="权限不足")

    results = await backends.get_activities_repo().types_summary()

    summary = {}
    total_activities = 0

    for result in results:
        activity_type = result["type"]
        count = result["count"]
        total_activities += count

        summary[activity_type] = {
            "count": count,
            "status_breakdown": result.get("status_breakdown", {}),
        }

    return {
        "success": True,
        "data": {"total_activities": total_activities, "by_type": summary},
    }


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

    if "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="权限不足")

    # Escape the query for regex safety
    escaped_query = re.escape(query)

    raw_suggestions, _total = await backends.get_activities_repo().list_raw(
        search=escaped_query,
        sort=None,
        page=1,
        size=limit,
    )
    suggestions = [
        {key: row[key] for key in ("_id", "name", "type") if key in row}
        for row in raw_suggestions
    ]

    return {"success": True, "data": {"query": query, "suggestions": suggestions}}
