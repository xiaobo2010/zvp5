"""管理员主学海设备的封禁豁免契约。

规则表（``xuehai_id`` 是任一 admin 的 ``primary_xuehai_id`` 时）：

======================  ==========================  ==========
显式行来源 / 判定路径    自动规则 ``rule``            豁免
账号推导（主设备=本机）   被任一账号封禁连带           豁免
显式行 ``admin``         管理员手工封禁               仍受限
无 admin owner           ``rule`` / 账号推导          原行为（受限）
======================  ==========================  ==========

为什么 ``admin`` 手工封禁不豁免：人工决定优先于脚本；自动规则封的是一台
「看起来在冒用」的机器，管理员的平板被规则误伤时必须能自己走回来，但不能
由代码替管理员推翻他刚下的封禁。

全部离线（backends 打桩），不连 Oracle。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from db import backends
from services import device_guard

NOW = datetime(2026, 10, 29, 12, 0, 0)
XUEHAI = "20260001"
ADMIN_OID = "00000000000000000000a001"


class Env:
    def __init__(self) -> None:
        self.device_bans: dict[str, dict] = {}
        self.banned_users: list[dict] = []
        self.admin_devices: set[str] = set()
        self.admin_queries: list[str] = []


@pytest.fixture
def env(monkeypatch) -> Env:
    state = Env()

    async def get_device_ban(xuehai_id):
        return state.device_bans.get(xuehai_id)

    async def list_banned_by_xuehai(xuehai_id):
        return list(state.banned_users)

    async def is_admin_primary_device(xuehai_id):
        state.admin_queries.append(xuehai_id)
        return xuehai_id in state.admin_devices

    monkeypatch.setattr(
        backends,
        "get_device_bans_repo",
        lambda *a, **k: SimpleNamespace(get=get_device_ban),
    )
    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda *a, **k: SimpleNamespace(
            list_banned_by_xuehai=list_banned_by_xuehai,
            is_admin_primary_device=is_admin_primary_device,
        ),
    )
    return state


def _ban(*, source: str, until=None, reason: str = "违规", rule_code=None):
    return {
        "xuehai_id": XUEHAI,
        "reason": reason,
        "until": until,
        "source": source,
        "rule_code": rule_code,
        "operator_oid": ADMIN_OID if source == "admin" else None,
        "created_at": NOW,
    }


def _banned_user(*roles: str, until=None, rule_code: str | None = None):
    user = {
        "banned": True,
        "roles": list(roles),
        "ban_reason": "账号违规",
        "ban_until": until,
    }
    if rule_code:
        user["ban_rule"] = rule_code
    return user


# ---------------------------------------------------------------------------
# 账号推导路径（b 路）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_admin_device_account_ban_stays_banned(env) -> None:
    """非 admin 主设备：别人被封照样连带，豁免不能扩大到普通设备。"""
    env.banned_users = [_banned_user("student")]

    assert await device_guard.device_ban_status(XUEHAI, now=NOW) == (
        True,
        "账号违规",
        None,
    )
    assert env.admin_queries == [XUEHAI]


@pytest.mark.asyncio
async def test_admin_device_exempt_from_someone_elses_account_ban(env) -> None:
    """admin 主设备被他人账号的封禁连带 → 豁免。"""
    env.admin_devices.add(XUEHAI)
    env.banned_users = [_banned_user("student")]

    assert await device_guard.device_ban_status(XUEHAI, now=NOW) == (False, None, None)


@pytest.mark.asyncio
async def test_admin_own_account_ban_does_not_lock_admin_device(env) -> None:
    """banned 行本身就是 admin：直接看 roles，不再反查 owner。"""
    env.banned_users = [_banned_user("admin")]

    assert await device_guard.device_ban_status(XUEHAI, now=NOW) == (False, None, None)
    assert env.admin_queries == []


@pytest.mark.asyncio
async def test_expired_account_ban_on_admin_device_still_allowed(env) -> None:
    env.admin_devices.add(XUEHAI)
    env.banned_users = [
        _banned_user("student", until=NOW - timedelta(days=1))
    ]

    assert await device_guard.device_ban_status(XUEHAI, now=NOW) == (False, None, None)
    assert env.admin_queries == []


# ---------------------------------------------------------------------------
# 显式行路径（a 路）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_device_rule_ban_is_exempt(env) -> None:
    """自动规则（临时密码连错等）封到 admin 主设备 → 豁免。"""
    env.admin_devices.add(XUEHAI)
    env.device_bans[XUEHAI] = _ban(
        source="rule", rule_code=device_guard.RULE_CODE_DEVICE_TEMP_PASSWORD
    )

    assert await device_guard.device_ban_status(XUEHAI, now=NOW) == (False, None, None)


@pytest.mark.asyncio
async def test_admin_device_manual_ban_still_applies(env) -> None:
    """管理员手工封禁（source=admin）优先于豁免。"""
    env.admin_devices.add(XUEHAI)
    env.device_bans[XUEHAI] = _ban(source="admin", reason="管理员封禁")

    assert await device_guard.device_ban_status(XUEHAI, now=NOW) == (
        True,
        "管理员封禁",
        None,
    )


@pytest.mark.asyncio
async def test_rule_ban_without_admin_owner_stays_banned(env) -> None:
    """无 owner 的设备保持原行为，并记录 rule_code 供排查。"""
    env.device_bans[XUEHAI] = _ban(
        source="rule", rule_code=device_guard.RULE_CODE_DEVICE_TEMP_PASSWORD
    )

    assert await device_guard.device_ban_status(XUEHAI, now=NOW) == (
        True,
        "违规",
        None,
    )
    assert env.admin_queries == [XUEHAI]


@pytest.mark.asyncio
async def test_expired_rule_ban_skips_owner_lookup(env) -> None:
    """过期行直接放行，不再为豁免多查一次 owner。"""
    env.device_bans[XUEHAI] = _ban(
        source="rule", until=NOW - timedelta(seconds=1)
    )

    assert await device_guard.device_ban_status(XUEHAI, now=NOW) == (False, None, None)
    assert env.admin_queries == []


# ---------------------------------------------------------------------------
# 临时密码路径（_device_ban_active）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_temp_password_path_exempts_admin_device_rule_ban(env) -> None:
    env.admin_devices.add(XUEHAI)
    env.device_bans[XUEHAI] = _ban(source="rule", rule_code="device_temp_password_abuse")

    assert await device_guard._device_ban_active(XUEHAI, NOW) is False


@pytest.mark.asyncio
async def test_temp_password_path_keeps_admin_manual_ban(env) -> None:
    env.admin_devices.add(XUEHAI)
    env.device_bans[XUEHAI] = _ban(source="admin")

    assert await device_guard._device_ban_active(XUEHAI, NOW) is True
