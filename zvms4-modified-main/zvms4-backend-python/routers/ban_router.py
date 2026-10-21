"""封禁规则与封禁流水的管理端点（前缀 ``/api/v3/ban``，admin only）。

为什么规则要有管理端点：``multi_device_login`` 的阈值/时间窗/处罚梯度全部在
``BAN_RULES`` 里，改一次就影响整条登录闸门。把它们暴露成「只改tunables」的
接口（``code`` / ``name`` / ``builtin`` 不可改），既能让管理员按学校实际情况
调参，又不会在运行时把代码引用的 ``code`` 改坏。

``CONFIG`` / ``PENALTIES`` 在库里是文本（DB 只保证 ``IS JSON``），解析与校验
都在本层：读路径对坏数据降级（``{}`` / ``[]``），写路径对非法值直接 422，
绝不把坏配置写进库。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from db import backends
from util.object_id import get_current_user

router = APIRouter()


def _require_admin(user: dict) -> None:
    if "admin" not in (user.get("per") or []):
        raise HTTPException(status_code=403, detail="权限不足")


def _iso(value) -> Optional[str]:
    """``datetime`` → ``YYYY-MM-DDTHH:MM:SS``；None 原样返回（永久封禁）。"""
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return None


def _load_json(raw, fallback):
    """读取宽进：库里是 JSON 文本，坏数据降级而不是让整页 500。"""
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


def _rule_config(rule: dict) -> dict:
    config = _load_json(rule.get("config"), {})
    return config if isinstance(config, dict) else {}


def _rule_penalties(rule: dict) -> list:
    penalties = _load_json(rule.get("penalties"), [])
    return penalties if isinstance(penalties, list) else []


def _rule_view(rule: dict) -> dict:
    return {
        "code": rule["code"],
        "name": rule["name"],
        "enabled": bool(rule.get("enabled")),
        "builtin": bool(rule.get("builtin")),
        "config": _rule_config(rule),
        "penalties": _rule_penalties(rule),
        "updated_at": _iso(rule.get("updated_at")),
    }


class PutRuleConfig(BaseModel):
    # 只校验类型；范围在端点上判，才能给出中文 422（与既有 v3 端点一致）。
    threshold: Optional[int] = None
    window_hours: Optional[int] = None


class PutRuleRequest(BaseModel):
    enabled: Optional[bool] = None
    config: Optional[PutRuleConfig] = None
    penalties: Optional[list[int]] = None


def _check_range(name: str, value: int, low: int, high: int) -> None:
    if not (low <= value <= high):
        raise HTTPException(
            status_code=422, detail=f"{name} 必须在 {low}–{high} 之间"
        )


def _validated_penalties(penalties: list[int]) -> list[int]:
    """1–6 档、每档 1–3650 天、严格升序。"""
    if not 1 <= len(penalties) <= 6:
        raise HTTPException(status_code=422, detail="处罚梯度必须是 1–6 项")
    for step in penalties:
        _check_range("处罚天数", step, 1, 3650)
    for previous, current in zip(penalties, penalties[1:]):
        if current <= previous:
            raise HTTPException(
                status_code=422, detail="处罚梯度必须严格升序"
            )
    return list(penalties)


@router.get("/rules")
async def list_ban_rules(user=Depends(get_current_user)):
    _require_admin(user)
    rules = await backends.get_ban_rules_repo().list_all()
    return {
        "status": "ok",
        "code": 200,
        "data": [_rule_view(rule) for rule in rules],
    }


@router.put("/rules/{code}")
async def update_ban_rule(
    code: str,
    payload: PutRuleRequest,
    user=Depends(get_current_user),
):
    _require_admin(user)
    repo = backends.get_ban_rules_repo()
    rule = await repo.get_by_code(code)
    if rule is None:
        raise HTTPException(status_code=404, detail="规则不存在")

    fields: dict = {}
    if payload.enabled is not None:
        fields["enabled"] = payload.enabled

    if payload.config is not None:
        # 整体序列化：``config`` 是一份完整配置，缺省项沿用库里现值，
        # 避免「只传 window_hours」把 threshold 冲掉。
        merged = dict(_rule_config(rule))
        if payload.config.threshold is not None:
            _check_range("threshold", payload.config.threshold, 2, 20)
            merged["threshold"] = payload.config.threshold
        if payload.config.window_hours is not None:
            _check_range("window_hours", payload.config.window_hours, 1, 168)
            merged["window_hours"] = payload.config.window_hours
        fields["config"] = json.dumps(merged, ensure_ascii=False)

    if payload.penalties is not None:
        fields["penalties"] = json.dumps(_validated_penalties(payload.penalties))

    # 空 patch 不产生写：仍然返回当前规则，语义上是幂等的「读回」。
    if fields:
        affected = await repo.update_by_code(code, **fields)
        if affected == 0:
            # 前置读与更新之间的并发删除：对外就是 404。
            raise HTTPException(status_code=404, detail="规则不存在")

    updated = await repo.get_by_code(code)
    if updated is None:
        raise HTTPException(status_code=404, detail="规则不存在")
    return {"status": "ok", "code": 200, "data": _rule_view(updated)}


@router.get("/users")
async def list_banned_users(
    page: int = Query(1, ge=1),
    perpage: int = Query(20, ge=1, le=100),
    query: str = Query("", max_length=128),
    user=Depends(get_current_user),
):
    """当前被封禁的用户（``USERS.BANNED = 1``），不是流水。

    ``/records`` 是只增的封禁历史，一屏「封禁管理」要看的是「现在谁进不来」：
    人工解封后流水仍留着，照流水显示会把已解封的人继续挂在列表上。
    """
    _require_admin(user)
    rows, total = await backends.get_users_repo().list_banned_page(query, page, perpage)
    data = [
        {
            "oid": row["_id"],
            "name": row["name"],
            # 与 /records 同一命名约定：对外叫 user_id，值是学号。
            "user_id": row.get("id"),
            "class_name": row.get("class_name"),
            "reason": row.get("ban_reason"),
            # ban_until 为 NULL 且 banned=1 就是永久封禁。
            "permanent": row.get("ban_until") is None,
            "until": _iso(row.get("ban_until")),
            "source": row.get("ban_source"),
            "rule_code": row.get("ban_rule"),
        }
        for row in rows
    ]
    return {
        "status": "ok",
        "code": 200,
        "data": data,
        "metadata": {"size": total, "page": page, "perpage": perpage},
    }


@router.get("/records")
async def list_ban_records(
    page: int = Query(1, ge=1),
    perpage: int = Query(20, ge=1, le=100),
    query: str = Query("", max_length=128),
    user=Depends(get_current_user),
):
    _require_admin(user)
    rows, total = await backends.get_ban_records_repo().list_page(page, perpage, query)
    data = [
        {
            "_id": row["_id"],
            "user_oid": row["user_oid"],
            "user_name": row.get("user_name"),
            # 对外叫 user_id，前端沿用的是「学号 = 业务 id」的既有命名。
            "user_id": row.get("user_business_id"),
            "source": row["source"],
            "rule_code": row.get("rule_code"),
            "reason": row.get("reason"),
            "permanent": bool(row.get("permanent")),
            "started_at": _iso(row.get("started_at")),
            "expires_at": _iso(row.get("expires_at")),
            "operator_name": row.get("operator_name"),
        }
        for row in rows
    ]
    return {
        "status": "ok",
        "code": 200,
        "data": data,
        "metadata": {"size": total, "page": page, "perpage": perpage},
    }
