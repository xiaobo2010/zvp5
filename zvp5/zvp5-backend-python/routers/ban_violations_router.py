"""平台违规使用上报与违规记录列表（前缀 ``/api/v3/ban``）。

两个端点共享一个前缀，但权限完全不同：

* ``POST /violations`` 是**任意登录用户**的自报通道——它只提交「我观察到
  什么」，判定与处罚全在服务端（``services.violation_guard``）；因此不需要
  免维护豁免，也不允许匿名。维护模式下上报被 503 挡掉是对的：那段时间客户端
  本来就不该有正常使用轨迹。
* ``GET /violations`` 是 admin 的证据列表，与 ``/records``、``/devices`` 同口径。

``detail`` 在入口截断到 500 字符（DB 列宽）：超长文本不该让整批上报 422，
客户端也不该因为一条调试信息丢掉证据。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from db import backends
from services import violation_guard
from typings.log import inject_log, xuehai_id_from
from util.object_id import get_current_user
from util.permission.features import has_feature
from util.serialization import iso_seconds

router = APIRouter()

#: 只能由服务端写入的信号。``agent_jailbreak`` 由 agent 输入审核命中时经
#: ``services.violation_guard.record_agent_jailbreak`` 落库；让浏览器自报等于把
#: 「越狱判定」交给被审核方，所以它在路由层被剔除。服务层的白名单保留它是因为
#: 服务端入口要能写这条信号，两个口径的差别就落在这个过滤上。
#:
#: 未知信号仍然**原样透传**：服务层按白名单忽略未知值，「信号名拼错」不该让
#: 同一批里的合法信号一起丢掉（见 ``tests/test_violation_api.py``）。
_SERVER_ONLY_SIGNALS = frozenset({"agent_jailbreak"})


def _require_admin(user: dict, feature: str | None = None) -> None:
    """与 ``ban_router._require_admin`` 同口径的本地实现。

    刻意不 import 那个私有函数：两个路由文件的鉴权规则可以独立演进，跨模块
    依赖一个下划线名字只是「看起来复用」。``feature`` 的语义同那边：只给只读
    端点用，写端点（信号上报）永远只认 ``admin``。
    """
    if "admin" in (user.get("per") or []):
        return
    if feature is not None and has_feature(user, feature):
        return
    raise HTTPException(status_code=403, detail="权限不足")


class SignalReport(BaseModel):
    # 信号名不做枚举校验：非法值由服务层忽略（不 422），理由见模块 docstring。
    signal: str = Field(max_length=64)
    detail: Optional[str] = None


class ViolationReport(BaseModel):
    signals: list[SignalReport] = Field(default_factory=list, max_length=20)


@router.post("/violations")
async def report_violations(
    payload: ViolationReport,
    request: Request,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """客户端上报一批平台违规信号；返回本次的记录结果。

    返回形状按契约是扁平四字段（``recorded`` / ``duplicates`` / ``banned`` /
    ``rule_code``）：客户端只关心「有没有被记下」，不需要再解一层信封。

    只拒绝 ``_SERVER_ONLY_SIGNALS``（``agent_jailbreak``）：它只能由服务端审核
    写入，客户端提交的**静默剔除**——与「未知信号不 422」的既有风格一致，
    一次上报里的合法信号不该因为夹带一个越权信号就整批丢掉。
    """
    signals = [
        {
            "signal": item.signal,
            "detail": (
                item.detail[: violation_guard.MAX_DETAIL_CHARS]
                if item.detail is not None
                else None
            ),
        }
        for item in payload.signals
        if item.signal not in _SERVER_ONLY_SIGNALS
    ]
    result = await violation_guard.record(user, signals, xuehai_id_from(request))

    # 上报本身也是一次平台事件：后台审计要能回答「谁在什么时候报了哪类信号」。
    log.with_text(
        f"上报平台违规信号 {result['recorded']} 条"
        f"（重复 {result['duplicates']} 条，"
        f"{'已自动封禁' if result['banned'] else '未触发封禁'}）",
        type="platform_misuse_report",
        level="info",
    )
    await log.insert_log()
    return result


@router.get("/violations")
async def list_violations(
    page: int = Query(1, ge=1),
    perpage: int = Query(20, ge=1, le=100),
    query: str = Query("", max_length=128),
    user=Depends(get_current_user),
):
    """违规记录列表（证据视角，不是当前封禁状态）。"""
    _require_admin(user, "ban.read")
    rows, total = await backends.get_violations_repo().list_page(query, page, perpage)
    data = [
        {
            "_id": row["_id"],
            "user_oid": row["user_oid"],
            "user_name": row.get("user_name"),
            # 与 /records 同一命名约定：对外叫 user_id，值是学号。
            "user_id": row.get("user_business_id"),
            "trigger": row["trigger"],
            "signal": row["signal"],
            "detail": row.get("detail"),
            "xuehai_id": row.get("xuehai_id"),
            "created_at": iso_seconds(row.get("created_at")),
        }
        for row in rows
    ]
    return {
        "status": "ok",
        "code": 200,
        "data": data,
        "metadata": {"size": total, "page": page, "perpage": perpage},
    }
