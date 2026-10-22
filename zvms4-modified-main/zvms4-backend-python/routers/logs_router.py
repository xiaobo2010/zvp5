from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from config import MAX_QUERY_COUNT
from db import backends
from util.object_id import get_current_user
from util.user import get_user_display_names

router = APIRouter()


@router.get("")
async def read_logs(
    performer: str = Query("", description="Filter by performer/user"),
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    perpage: int = Query(10, ge=1, le=100, description="Items per page (max 100)"),
    query: str = Query(
        "",
        max_length=MAX_QUERY_COUNT,
        description="Search query (url/data, case-insensitive contains)",
    ),
    type: str = Query("", description="Comma-separated log types"),
    level: str = Query("", description="Comma-separated log levels"),
    time_start: Optional[int] = Query(None, description="Start timestamp (seconds)"),
    time_end: Optional[int] = Query(None, description="End timestamp (seconds)"),
    xuehai_id: str = Query("", description="Filter by 学海设备 ID (Xuehai-Id header)"),
    user=Depends(get_current_user),
):
    """管理端日志列表。

    分页口径：``page`` 从 1 开始（``page < 1`` → 422），``perpage`` 上限 100，
    两者在仓储层再钳一次，避免直接调用方绕过 FastAPI 校验。总数由仓储用
    ``COUNT(*) OVER()`` 与当页数据一起带回，不再每页执行一次全表 ``COUNT``。
    """
    if "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="Permission denied")

    if len(query) > MAX_QUERY_COUNT:
        raise HTTPException(status_code=400, detail="Query too long")

    types = [t.strip() for t in type.split(",") if t.strip()]
    levels = [l.strip() for l in level.split(",") if l.strip()]

    logs, count = await backends.get_applogs_repo().list_page(
        query=query,
        performer=performer,
        types=types,
        levels=levels,
        time_start=time_start,
        time_end=time_end,
        xuehai_id=xuehai_id,
        page=page,
        size=perpage,
    )

    names = await get_user_display_names(
        [log["user"] for log in logs if log.get("user")]
    )

    for log in logs:
        log["_id"] = str(log["_id"])
        if log.get("user"):
            log["user_name"] = names.get(log.get("user"), "未知用户")
        else:
            log["user_name"] = "未登录"

    return {
        "code": 200,
        "status": "ok",
        "data": logs,
        "metadata": {
            "size": count,
        },
    }