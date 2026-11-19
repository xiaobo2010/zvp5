"""维护放行清单与路由表的契约：登录端点永远必须在白名单里。

背景：users 路由前缀从 ``/api/users`` 迁到 ``/api/v1/users`` 时，
``MAINTENANCE_EXEMPT_PATHS`` 没跟着改——维护模式一开，登录端点在中间件层
被 503，端点内「维护期间仅管理员可登录」的审计分支成了死代码（管理员没
token 也进不来，更别说关维护）。

本文件不写死任何前缀：挂载前缀从 ``main.ROUTERS`` 现算，登录/登出路径从
``users_router`` 的路由表现算。下次再改挂载前缀或路径，这里会立刻红灯。
"""

from __future__ import annotations

import pytest
from fastapi.routing import APIRoute

import main
from app_setup import MAINTENANCE_EXEMPT_PATHS
from routers import users_router

#: 维护期间必须放行的 users 端点（相对挂载前缀的路径）。
_REQUIRED_AUTH_REL_PATHS = ("/auth", "/auth/logout")


def _users_mount_prefix() -> str:
    """从 main.ROUTERS 现算 users 路由的挂载前缀，不写死字符串。"""
    for router, prefix, _tags in main.ROUTERS:
        if router is users_router.router:
            return prefix
    pytest.fail("main.ROUTERS 里找不到 users_router 的挂载项")


def _users_router_paths() -> set[str]:
    return {
        route.path
        for route in users_router.router.routes
        if isinstance(route, APIRoute)
    }


def test_users_router_declares_the_auth_endpoints() -> None:
    """契约前提：这两个端点确实存在于路由表（防止端点本身被改名/删除）。"""
    paths = _users_router_paths()
    for rel_path in _REQUIRED_AUTH_REL_PATHS:
        assert rel_path in paths


def test_auth_endpoints_are_exempt_from_maintenance() -> None:
    """挂载前缀 + 登录/登出路径的完整路径必须逐字在维护放行清单里。"""
    prefix = _users_mount_prefix()
    for rel_path in _REQUIRED_AUTH_REL_PATHS:
        full_path = f"{prefix}{rel_path}"
        assert full_path in MAINTENANCE_EXEMPT_PATHS, (
            f"{full_path} 不在 MAINTENANCE_EXEMPT_PATHS：维护模式会把登录端点"
            "503 在中间件层，管理员无法登录关维护"
        )
