"""额度端点：本人快照/用量、管理员查看与调额、重置。"""

from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from db import backends
from routers.ai.common import _quota_error, _require_admin
from services.ai import quota
from util.permission.features import ensure_feature
from typings.log import inject_log
from util.object_id import get_current_user, validate_object_id

router = APIRouter()


class QuotaSetRequest(BaseModel):
    """管理员设额：``kind`` 与 ``limit_count`` 都必填，语义是「覆盖上限」。

    字段不在这里做枚举/区间约束：``kind`` 的合法值与 ``limit_count >= 0``
    由服务层判定，这样非法输入回的是稳定的 400 + 错误码，而不是 pydantic
    的 422 报文（客户端按 code 分支，不解析 detail 数组）。
    """

    kind: str
    limit_count: int


class QuotaResetRequest(BaseModel):
    """重置只针对一种能力：``used`` 归零，``limit_count`` 保留。"""

    kind: str


# ---------------------------------------------------------------------------
# 额度（用户看自己 / admin 看别人与调额、重置）
# ---------------------------------------------------------------------------


async def _target_roles(oid: str) -> list[str] | None:
    """取目标用户的 ``roles``（只读窄投影，不搬 ``past``/``eligibility`` 两个 CLOB）。

    查不到用户回 ``None``：管理端此时展示全局默认额度，并由 ``source`` 标明
    ``global_default``。用空列表假装「这个人没有角色」也能得到同样的数值，但
    ``None`` 让「用户不存在」与「角色为空」在服务层保持可区分，便于以后细分口径。
    """
    row = await backends.get_users_repo().get_for_auth(oid)
    if not row:
        return None
    roles = row.get("roles")
    return roles if isinstance(roles, list) else None


@router.get("/quota")
async def my_quota(user=Depends(get_current_user)):
    """当前用户本月额度快照：``af`` 一条（OCR 与对话共用），没有行就回默认上限。"""
    return {
        "status": "ok",
        "code": 200,
        "data": await quota.snapshot(user["id"], roles=user.get("per")),
    }


#: 个人用量小计的数值列。顺序即响应里的键顺序，``total`` / ``by_provider`` /
#: ``by_model`` 三处共用同一组，避免某一处漏算一列。
_USAGE_KEYS = ("calls", "units", "prompt_tokens", "completion_tokens", "total_tokens")

# 月份标签：与 ``ai_quota.period`` / ``quota.period_now()`` 同一口径（``YYYYMM``）。
_PERIOD_RE = re.compile(r"^(\d{4})(\d{2})$")


def _quota_period_bounds(period: str | None) -> tuple[str, datetime, datetime]:
    """``period`` → ``(标签, 当月首日 00:00, 次月首日 00:00)``。

    缺省取本月（``quota.period_now()``）。非法格式一律 400：静默回落到本月会让
    「传错了」看起来像「这个月真的没用量」，而这两种情况在页面上都是 0。
    """
    label = period if period is not None else quota.period_now()
    match = _PERIOD_RE.match(label or "")
    if match is None:
        raise HTTPException(status_code=400, detail="period 必须是 YYYYMM 格式")
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        raise HTTPException(status_code=400, detail="period 必须是 YYYYMM 格式")
    try:
        start = datetime(year, month, 1)
        end = datetime(year + month // 12, month % 12 + 1, 1)
    except ValueError:
        # year 0000 / 9999-12 的次月：日期本身不存在，与格式错误同类处理。
        raise HTTPException(status_code=400, detail="period 必须是 YYYYMM 格式")
    return label, start, end


@router.get("/quota/usage")
async def my_quota_usage(
    period: str | None = Query(
        None, description="YYYYMM（缺省 = 本月，与 /quota 的 period 同口径）"
    ),
    user=Depends(get_current_user),
):
    """本人某个自然月的用量统计：总数 + 按 provider / model 分解。

    **只统计自己**：没有 ``user_oid`` 入参，条件里写死 ``user["id"]``。admin 想看
    别人的用量走 ``/api/ai/logs``（那条是审计面，带门禁与脱敏口径），这里是用户
    自助面——两张报表的服务对象不同，就不该共用一个参数。

    注册顺序有要求：本路由必须排在 ``/quota/{user_oid}``（admin 视角）**之前**，
    否则 ``GET /api/ai/quota/usage`` 会被动态段先匹配，``user_oid="usage"`` 再
    撞上 oid 形状校验变成 422/400。

    口径（与 ``ai_call_logs`` 的列语义一一对应）：

    * 窗口 = ``period`` 首日 00:00 起、次月首日 00:00 止的**半开区间**——闭区间
      会让跨月那一刻的调用被相邻两个月各算一次；
    * ``calls`` = ``COUNT(*)``：成功、失败、被拒都算一次调用；
    * ``units`` / 三组 token 是 ``COALESCE(SUM(...), 0)``：窗口内没上报过 usage
      时给 0（「这段时间一共观测到 0 个 token」），与逐行 NULL 的「没报」不同；
    * ``provider`` / ``model`` 的 NULL（OCR 或未标注）保留成独立分组，不丢行也
      不并进空串。

    ``period`` 缺省取 ``quota.period_now()``（本地时区的 ``YYYYMM``）；格式非法
    回 400，而不是静默回落到本月——回落的「一片 0」会被读成「这个月没用量」。
    """
    label, start, end = _quota_period_bounds(period)
    rows = await backends.get_ai_call_logs_repo().usage_breakdown(
        user_oid=str(user["id"]), since=start, until=end
    )

    total = dict.fromkeys(_USAGE_KEYS, 0)
    providers: dict[str | None, dict[str, int]] = {}
    by_model: list[dict] = []
    for row in rows:
        counts = {key: int(row.get(key) or 0) for key in _USAGE_KEYS}
        for key, value in counts.items():
            total[key] += value
        bucket = providers.setdefault(row["provider"], dict.fromkeys(_USAGE_KEYS, 0))
        for key, value in counts.items():
            bucket[key] += value
        by_model.append(
            {"provider": row["provider"], "model": row["model"], **counts}
        )

    # provider 维度在 Python 里汇总（SQL 只有一条 GROUP BY provider, model）。
    # 排序与 SQL 同口径：调用多的在前，同次数按名字定序；NULL 排最前（``or ""``）。
    by_provider = [
        {"provider": provider, **counts}
        for provider, counts in sorted(
            providers.items(), key=lambda item: (-item[1]["calls"], item[0] or "")
        )
    ]

    return {
        "status": "ok",
        "code": 200,
        "data": {
            "period": label,
            "total": total,
            "by_provider": by_provider,
            "by_model": by_model,
        },
    }


@router.get("/quota/{user_oid}")
async def user_quota(user_oid: str, user=Depends(get_current_user)):
    """admin 视角的同一份快照。

    ``ai_quota`` 没有指向用户的 FK，查无额度行是正常状态（本月还没用过），
    所以这里**不校验用户是否存在**，只校验 oid 形状。

    展示的「默认上限」必须与真实扣费口径一致：扣费走
    ``default_limit_for(用户的 roles, kind)``（admin 的 af 是 1_000_000），所以
    先查目标用户角色再算，不能拿管理端的 ``per`` 也不能停在全局
    ``DEFAULT_QUOTA``（af 200_000）——否则管理页会把一个 admin 的额度少显示
    一个数量级。查不到用户时回落全局默认，响应里的 ``source`` 会标
    ``global_default``，不编造角色。
    """
    _require_admin(user)
    ensure_feature(user, "quota.manage")
    target = validate_object_id(user_oid)
    roles = await _target_roles(target)
    return {
        "status": "ok",
        "code": 200,
        "data": await quota.admin_snapshot(target, roles=roles),
    }


@router.put("/quota/{user_oid}")
async def set_user_quota(
    user_oid: str,
    payload: QuotaSetRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """管理员设额：只改上限，已用量保持不变。"""
    _require_admin(user)
    ensure_feature(user, "quota.manage")
    target = validate_object_id(user_oid)
    try:
        data = await quota.set_user_limit(target, payload.kind, payload.limit_count)
    except quota.QuotaError as exc:
        raise _quota_error(exc) from None

    log.with_text(
        f"AI 额度设置 {target}/{data['kind']} = {data['limit']}",
        type="ai_quota_set",
    )
    await log.insert_log()
    return {"status": "ok", "code": 200, "data": data}


@router.post("/quota/{user_oid}/reset")
async def reset_user_quota(
    user_oid: str,
    payload: QuotaResetRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """管理员重置某能力当月已用量：``used`` 归零，上限保留。"""
    _require_admin(user)
    ensure_feature(user, "quota.manage")
    target = validate_object_id(user_oid)
    try:
        data = await quota.reset_used(target, payload.kind)
    except quota.QuotaError as exc:
        raise _quota_error(exc) from None

    log.with_text(
        f"AI 额度重置 {target}/{data['kind']}（上限 {data['limit']}）",
        type="ai_quota_reset",
    )
    await log.insert_log()
    return {"status": "ok", "code": 200, "data": data}
