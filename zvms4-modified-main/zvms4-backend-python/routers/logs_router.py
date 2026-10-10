import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from config import MAX_QUERY_COUNT
from database import db
from util.object_id import get_current_user
from util.user import get_user_display_names

router = APIRouter()


@router.get("")
async def read_logs(
    performer: str = Query("", description="Filter by performer/user"),
    page: int = Query(-1, ge=-1, description="Page number (-1 for no pagination)"),
    perpage: int = Query(10, ge=1, le=100, description="Number of items per page"),
    query: str = Query("", max_length=MAX_QUERY_COUNT, description="Search query"),
    type: str = Query("", description="Comma-separated log types"),
    level: str = Query("", description="Comma-separated log levels"),
    time_start: Optional[int] = Query(None, description="Start timestamp (seconds)"),
    time_end: Optional[int] = Query(None, description="End timestamp (seconds)"),
    user=Depends(get_current_user),
):
    if "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="Permission denied")

    if len(query) > MAX_QUERY_COUNT:
        raise HTTPException(status_code=400, detail="Query too long")

    match: dict[str, Any] = {}
    if query != "":
        escaped = re.escape(query)
        match["$or"] = [{"url": {"$regex": escaped}}, {"data": {"$regex": escaped}}]
    if performer != "":
        match["user"] = performer
    if type != "":
        match["type"] = {"$in": [t.strip() for t in type.split(",") if t.strip()]}
    if level != "":
        match["level"] = {"$in": [l.strip() for l in level.split(",") if l.strip()]}
    timestamp_filter: dict[str, Any] = {}
    if time_start is not None:
        timestamp_filter["$gt"] = time_start
    if time_end is not None:
        timestamp_filter["$lt"] = time_end
    if timestamp_filter:
        match["timestamp"] = timestamp_filter

    count = await db.zvms.logs.count_documents(match)

    pipeline = [
        {"$match": match},
        {"$sort": {"timestamp": -1}},
        {"$skip": 0 if page == -1 else (page - 1) * perpage},
        {"$limit": perpage},
    ]

    logs = await db.zvms.logs.aggregate(pipeline).to_list(None)

    names = await get_user_display_names(
        [log["user"] for log in logs if log.get("user")]
    )

    for log in logs:
        log["_id"] = str(log["_id"])
        log["user_name"] = names.get(log.get("user"), "未知用户")

    return {
        "code": 200,
        "status": "ok",
        "data": logs,
        "metadata": {
            "size": count,
        },
    }