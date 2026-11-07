"""调用流水与 prompt 审阅端点（admin）。"""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from db import backends
from db.ids import is_valid_oid
from routers.ai.common import _require_admin
from typings.log import inject_log
from util.object_id import get_current_user, validate_object_id
from util.read_throttle import read_budget
from util.user import get_user_display_name, get_user_display_names

router = APIRouter()

# ---------------------------------------------------------------------------
# 调用流水（admin）
# ---------------------------------------------------------------------------

# 过滤值的合法集合与库里的 CHECK 一一对应（db/models.py 的 ck_ai_call_logs_*）。
# 非法值回 400 AI_LOGS_BAD_FILTER 而不是静默忽略：静默忽略会让「筛出来是空的」
# 与「筛条件写错了」长得一样。
LOG_CAPABILITIES = frozenset({"ocr", "chat"})
LOG_STATUSES = frozenset({"success", "error", "timeout", "rejected", "busy"})
LOG_KEY_SCOPES = frozenset({"personal", "shared", "server"})


def _bad_filter() -> HTTPException:
    return HTTPException(status_code=400, detail="AI_LOGS_BAD_FILTER")


def _epoch_to_local(value: int | None) -> datetime | None:
    """epoch 秒 → naive 本地 datetime。

    ``AI_CALL_LOGS.CREATED_AT`` 是 naive ``TIMESTAMP``（仓储写入用
    ``datetime.now()``），所以查询边界也用本地时间：``fromtimestamp`` 出来的
    正是同一口径。带 tz 的 datetime 直接比较会被 SQLAlchemy 当成另一个时区，
    「含端点」的边界就会偏移几个小时。
    """
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(value)
    except (OverflowError, OSError, ValueError):
        raise _bad_filter() from None


@router.get("/logs")
async def list_call_logs(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    perpage: int = Query(20, ge=1, le=100, description="Items per page (max 100)"),
    user_oid: str | None = Query(None, description="Filter by user oid"),
    capability: str | None = Query(None, description="ocr | chat"),
    status: str | None = Query(None, description="success|error|timeout|rejected|busy"),
    key_scope: str | None = Query(None, description="personal | shared | server"),
    request_id: str | None = Query(None, description="Exact request id (trace one call)"),
    provider: str | None = Query(None, description="Exact provider name"),
    model: str | None = Query(None, description="Exact model name"),
    purpose: str | None = Query(None, description="Calling scene (chat | ocr | ...)"),
    time_start: int | None = Query(None, description="created_at >= (epoch seconds)"),
    time_end: int | None = Query(None, description="created_at <= (epoch seconds)"),
    user=Depends(get_current_user),
    _budget=Depends(read_budget),
):
    """AI 调用流水（新调用在前），仅 admin。

    分页口径与 ``/api/logs`` 一致：``page`` 1-based，``perpage`` 1..100，
    总数由仓储单独 ``COUNT``（分页只取 ``LIMIT`` 行，不必为拿一页扫全表）。

    时间边界是**闭区间**：``created_at >= time_start`` 且 ``created_at <= time_end``
    ——排查「某一秒发生了什么」时，端点那一秒不该被排除在外。

    ``request_id`` / ``provider`` / ``model`` / ``purpose`` 是精确匹配：它们都是
    「先有一个已知值（响应头里的编号、报表里的服务商名）再回头查」的入口。
    """
    _require_admin(user)

    if capability and capability not in LOG_CAPABILITIES:
        raise _bad_filter()
    if status and status not in LOG_STATUSES:
        raise _bad_filter()
    if key_scope and key_scope not in LOG_KEY_SCOPES:
        raise _bad_filter()

    target = validate_object_id(user_oid) if user_oid else None
    start = _epoch_to_local(time_start)
    end = _epoch_to_local(time_end)

    repo = backends.get_ai_call_logs_repo()
    filters = {
        "user_oid": target,
        "capability": capability or None,
        "status": status or None,
        "key_scope": key_scope or None,
        "request_id": request_id or None,
        "provider": provider or None,
        "model": model or None,
        "purpose": purpose or None,
        "time_start": start,
        "time_end": end,
    }
    logs = await repo.list_page(**filters, page=page, page_size=perpage)
    total = await repo.total_count(**filters)

    names = await get_user_display_names(
        [row["user_oid"] for row in logs if row.get("user_oid")]
    )
    for row in logs:
        # 主键给两份字符串：``_id`` 是仓储的行形状（Mongo 口径），``oid`` 是
        # 对外契约/前端行 key 用的名字。两者同值，调用方不必猜该读哪个。
        oid = str(row["_id"])
        row["_id"] = oid
        row["oid"] = oid
        # 与 ``/api/logs`` 同款兜底：账号被删/查不到时回「未知用户」而不是空串。
        row["user_name"] = names.get(row.get("user_oid"), "未知用户")

    return {
        "status": "ok",
        "code": 200,
        "data": logs,
        "metadata": {"size": total},
    }


# 统计窗口的上下界：1..90 天。上限按流水的保留期（90 天）取——查更长的窗口只
# 会扫到已经被清理掉的部分，看起来像「数据丢了」，其实是保留策略。
STATS_MAX_DAYS = 90


@router.get("/logs/stats")
async def call_log_stats(
    days: int = Query(7, ge=1, le=STATS_MAX_DAYS, description="Window size in days"),
    user=Depends(get_current_user),
):
    """调用聚合概览（按日 + 按 provider/model/purpose/用户/状态 + 缓存），仅 admin。

    ``days`` 是**包含今天在内**往前数的自然日窗口（默认 7，上限 90）。窗口起点
    取「今天零点减 (days-1) 天」，而不是「现在减 days×24h」：按日分组的报表如果
    从半天中间开始，第一天的数字会是半天，看起来像服务掉了一半量。

    ``by_user``（top 50，含 ``name`` / ``avg_latency_ms``）回答「谁在花钱」，
    ``by_status`` 把 ``errors`` 拆成 success/error/rejected/busy——两者都与既有的
    四个分项来自同一个窗口，合计口径不变。
    """
    _require_admin(user)

    now = datetime.now()
    start = (now - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    data = await backends.get_ai_call_logs_repo().stats(since=start)

    return {
        "status": "ok",
        "code": 200,
        "data": {
            "days": days,
            "since": start.isoformat(sep=" ", timespec="seconds"),
            **data,
        },
    }


# ---------------------------------------------------------------------------
# prompt 审阅（admin）：正文只在这里出现，读一条就要留一条访问审计
# ---------------------------------------------------------------------------

# 审阅列表的默认/最大窗口（天）。默认 7 而不是保留期 30：这个端点是为「查最近
# 发生了什么」设计的，翻一个月前的正文应该显式写 days。
PROMPT_LIST_DAYS = 7
PROMPT_MAX_DAYS = 90


@router.get("/prompts")
async def list_prompt_audit(
    page: int = Query(1, ge=1, description="Page number (1-based)"),
    perpage: int = Query(20, ge=1, le=100, description="Items per page (max 100)"),
    user_oid: str | None = Query(None, description="Filter by user oid"),
    purpose: str | None = Query(
        None, description="Calling scene (chat | agent | markdown-assistant | ocr | ...)"
    ),
    days: int = Query(
        PROMPT_LIST_DAYS, ge=1, le=PROMPT_MAX_DAYS, description="Window size in days"
    ),
    time_start: int | None = Query(None, description="created_at >= (epoch seconds)"),
    time_end: int | None = Query(None, description="created_at <= (epoch seconds)"),
    keyword: str | None = Query(
        None, description="Substring match on the prompt body (literal, case-sensitive)"
    ),
    user=Depends(get_current_user),
    _budget=Depends(read_budget),
):
    """受控留存的 prompt **元数据**列表（新记录在前），仅 admin。

    **不返回正文**（``prompt_text``）：翻一页就把几十段学生对话读进内存，既慢
    又无谓扩大正文暴露面。要看内容走 ``GET /api/ai/prompts/{oid}``，那条路径会
    写访问审计。``prompt_chars`` 是库里的 ``LENGTH(PROMPT_TEXT)``，够判断「这条
    值不值得点开」。

    ``days`` 是滚动窗口（现在往前 N 天，1–90），默认 7；用户/场景/正文关键字与
    ``/logs`` 同款过滤。``user_oid`` 非法形状回 400（与 ``/logs`` 同口径）。

    ``time_start`` / ``time_end`` 与 ``/api/ai/logs`` 逐字同口径：epoch 秒、
    闭区间、按本地时间比较。**给定时以显式范围为准，``days`` 不再参与**——即使
    只给了 ``time_end`` 也不叠加「最近 7 天」，否则「查某天之前」会被默认窗口
    悄悄截断，结果看起来像「没有匹配」。

    ``keyword`` 是正文的**字面子串**匹配（``DBMS_LOB.INSTR``，大小写敏感，不是
    ``LIKE`` 通配）：过滤在库里完成，正文不会进入列表响应。空串等于不过滤。
    """
    _require_admin(user)

    target = validate_object_id(user_oid) if user_oid else None
    start = _epoch_to_local(time_start)
    end = _epoch_to_local(time_end)
    # days 只是兼容捷径：显式给了任一时间边界就整段以显式为准（见 docstring）。
    if start is None and end is None:
        start = datetime.now() - timedelta(days=days)
    pattern = (keyword or "").strip() or None

    repo = backends.get_ai_prompt_audit_repo()
    rows = await repo.list_page(
        user_oid=target,
        purpose=purpose or None,
        keyword=pattern,
        since=start,
        until=end,
        page=page,
        page_size=perpage,
    )
    total = await repo.count(
        user_oid=target,
        purpose=purpose or None,
        keyword=pattern,
        since=start,
        until=end,
    )

    names = await get_user_display_names(
        [row["user_oid"] for row in rows if row.get("user_oid")]
    )
    for row in rows:
        oid = str(row["_id"])
        row["_id"] = oid
        row["oid"] = oid
        row["user_name"] = names.get(row.get("user_oid"), "未知用户")
        # purpose 由仓储的元数据投影带出（列表按场景回显/筛选要用）；
        # 这里只在缺失时补 None，保证响应字段集合稳定。
        row.setdefault("purpose", None)

    return {
        "status": "ok",
        "code": 200,
        "data": rows,
        "metadata": {"size": total},
    }


@router.get("/prompts/{oid}")
async def get_prompt_audit(
    oid: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """单条 prompt 正文（admin）；**每次读取写一条 APP_LOGS 访问审计**。

    审计行 ``type='ai_prompt_access'`` / ``level='info'``：``info`` 是承重的——
    它随 APP_LOGS 的保留期自动清理（``login_fail`` 先例），不会把「谁看过学生
    的对话」永久堆在库里；而 ``warning`` 会被清理保护集留住，那是给安全事件用的。

    审计只记目标 oid 与目标 user_oid，**不记正文**：正文已经在被审计的那张表里，
    再抄一份到 APP_LOGS 等于把暴露面翻倍。

    oid 形状非法或记录不存在都回 404（不是 400）：对调用方来说两者的处置相同，
    且 404 不会泄露「这个 oid 曾经存在过」。
    """
    _require_admin(user)

    if not is_valid_oid(oid):
        raise HTTPException(status_code=404, detail="AI_PROMPT_NOT_FOUND")

    row = await backends.get_ai_prompt_audit_repo().get_by_oid(oid)
    if row is None:
        raise HTTPException(status_code=404, detail="AI_PROMPT_NOT_FOUND")

    target = row.get("user_oid")
    log.with_text(
        f"AI prompt 审阅 oid={oid} user_oid={target or ''}",
        type="ai_prompt_access",
    )
    await log.insert_log()

    row["oid"] = str(row["_id"])
    row["user_name"] = await get_user_display_name(target) if target else "未知用户"
    return {"status": "ok", "code": 200, "data": row}
