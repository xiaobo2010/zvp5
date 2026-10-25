from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from config import MAX_QUERY_COUNT
from db import backends
from util.object_id import get_current_user
from util.user import get_user_display_names

router = APIRouter()

#: 管理端默认隐藏的日志类型。``login_fail`` 是两条风控规则的计数数据源
#: （``services/risk_monitor.py``），一次暴破尝试就会写满一屏；它属于
#: 「机器看、人不用看」的行。默认列表排除，管理员排查账号异常时用
#: ``?type=login_fail`` 显式取回（见 :func:`read_logs` 的 docstring）。
HIDDEN_LOG_TYPES = ("login_fail",)


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

    默认隐藏 ``login_fail``：不传 ``type`` 时按 :data:`HIDDEN_LOG_TYPES` 做
    黑名单（该类是风控计数数据源，量大且对管理员无事可做）；显式
    ``type=login_fail`` 仍然按白名单查询，账号排查入口保留。
    """
    if "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="Permission denied")

    if len(query) > MAX_QUERY_COUNT:
        raise HTTPException(status_code=400, detail="Query too long")

    types = [t.strip() for t in type.split(",") if t.strip()]
    levels = [l.strip() for l in level.split(",") if l.strip()]
    # 只在管理员**没有**指定类型时套黑名单：显式选择（含多选）以调用方为准。
    exclude_types = list(HIDDEN_LOG_TYPES) if not types else None

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
        exclude_types=exclude_types,
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