"""主题偏好端点（前缀 ``/api/v1/themes``）。

契约：

* ``GET ""`` —— 登录用户读取**可选主题注册表**（:mod:`services.theme_registry`）。
  前端据此渲染主题选择器，不需要为了加主题发版后端。
* ``GET /me`` / ``PUT /me`` —— 当前账号的主题偏好，落在 ``USERS.THEME``。
  读路径对 NULL / 已下线 id 回落到默认主题（显示偏好不该让页面 500）；写路径
  严格命中注册表，未知主题 400 且**不写库**。改主题不轮换 ``session_id``：
  换个配色不是身份变更，不该把用户从所有设备踢下线。
* ``GET /stats`` —— 严格管理员（复用 ``routers.system_router._require_system_admin``，
  含即时查库复核）的主题使用统计。``items`` 按注册表顺序输出、包含 0 人的主题；
  库里存在但注册表没有的历史值只计入 ``other``，不伪造条目。

响应沿用项目 envelope：``{"status": "ok", "data": ...}``。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import backends
from routers.system_router import _require_system_admin
from services.theme_registry import (
    DEFAULT_THEME,
    THEMES,
    is_valid_theme_id,
    normalize_theme_id,
    theme_items,
)
from util.object_id import get_current_user

router = APIRouter()

_THEME_ID_MAX = 32


class ThemeUpdateRequest(BaseModel):
    """``PUT /me`` 的请求体；长度上限与 ``USERS.THEME`` 的列宽一致。"""

    theme: str = Field(min_length=1, max_length=_THEME_ID_MAX)


@router.get("")
async def list_themes(user=Depends(get_current_user)):
    """可选主题列表（注册表顺序）。"""
    return {"status": "ok", "data": {"default": DEFAULT_THEME, "items": theme_items()}}


@router.get("/me")
async def read_my_theme(user=Depends(get_current_user)):
    """当前账号的主题；库里为 NULL 或未知历史值时回默认主题。"""
    stored = await backends.get_users_repo().get_theme(user["id"])
    return {"status": "ok", "data": {"theme": normalize_theme_id(stored)}}


@router.put("/me")
async def update_my_theme(
    payload: ThemeUpdateRequest,
    user=Depends(get_current_user),
):
    """保存当前账号的主题（未知主题 400，且不触碰仓储）。"""
    theme_id = payload.theme.strip().lower()
    if not is_valid_theme_id(theme_id):
        raise HTTPException(status_code=400, detail="未知主题")

    await backends.get_users_repo().set_theme(user["id"], theme_id)
    return {"status": "ok", "data": {"theme": theme_id}}


@router.get("/stats")
async def theme_stats(user=Depends(get_current_user)):
    """管理员视角：每个注册主题的账号数与占比，未知历史值单列 ``other``。"""
    await _require_system_admin(user)

    counts = await backends.get_users_repo().count_by_theme()
    total = sum(counts.values())

    items: list[dict] = []
    known = 0
    for theme in THEMES:
        count = int(counts.get(theme.id, 0))
        known += count
        items.append(
            {
                "id": theme.id,
                "label": theme.label,
                "count": count,
                "percent": round(count * 100 / total, 1) if total else 0.0,
            }
        )

    return {
        "status": "ok",
        "data": {
            "total": total,
            "items": items,
            # 未知历史值只在这里出现（注册表没有的 id / 未来下线主题的残留）。
            "other": total - known,
        },
    }
