"""角色功能开关（V043 ``ROLE_FEATURES``）的统一判定入口。

口径与 ``services.roles.policy`` 一致：``admin`` 恒可用；多角色取并集；未知 key
恒 False（拼错不放行）。默认值见 ``policy.DEFAULTS``，因此在配置表为空时行为与
改造前一致。

三种用法：

* :func:`has_feature` —— 纯判断，适合已经拿到 ``user`` 的同步分支；
* :func:`ensure_feature` —— 不满足时抛 403，适合写在既有 guard 内部；
* :func:`require_feature` —— FastAPI 依赖工厂，顺带刷一次策略快照。
"""

from __future__ import annotations

from typing import Iterable

from fastapi import Depends, HTTPException

from services.roles import policy
from util.object_id import get_current_user


def _roles(user: dict | None) -> Iterable[str]:
    return (user or {}).get("per") or []


def has_feature(user: dict | None, key: str) -> bool:
    """当前用户是否具备某项功能（``admin`` 恒 True）。"""
    return policy.feature_enabled(_roles(user), key)


def ensure_feature(user: dict | None, key: str) -> None:
    """不满足即抛 403；文案对用户可见，不含内部实现细节。"""
    if not has_feature(user, key):
        raise HTTPException(status_code=403, detail="当前角色无权使用该功能")


def require_feature(key: str):
    """FastAPI 依赖：先刷策略快照，再判定，返回原 ``user`` 供端点继续使用。"""

    async def _dependency(user: dict = Depends(get_current_user)) -> dict:
        await policy.ensure_fresh()
        ensure_feature(user, key)
        return user

    return _dependency
