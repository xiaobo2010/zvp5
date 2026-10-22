"""``services.risk_monitor`` 的离线契约：九条规则里新扩的七条如何裁决。

每条规则都要钉住五件事，任何一件写错都会静默出错：

* 达到阈值才动手（差一次不动）；
* ``mode="alert"`` 只写证据与告警，``mode="ban"`` 才调用账号封禁；
* 已有生效封禁不再推梯度（``>=`` 语义下否则会一路打到永久）；
* 同一 ``(trigger, user)`` 60 秒内只评估一次（调用点在每一次失败登录上）；
* 特权角色只记录不封；任何异常都不冒泡（调用点是登录/导出主路径）。

全部离线：仓储与 ``ZVMSLog`` 全打桩，``DEVICE_CHALLENGES`` 的计数也换成假
计数器（真实现是一条 SQL，单独测没有价值，重要的是「这条规则数的是挑战」）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from db import backends
from services import device_guard, risk_monitor

NOW = datetime(2026, 10, 22, 12, 0, 0)
USER = "000000000000000000000002"

#: 七条新规则的 (trigger, 默认阈值, 默认窗口小时, 默认 mode)。
RULE_TABLE = [
    ("login_failure_burst", 10, 1, "ban", "login_fail", None),
    ("ban_evasion_retry", 5, 1, "ban", "login_fail", "banned"),
    ("challenge_flood", 8, 1, "alert", None, None),
    ("temp_password_issue_burst", 20, 1, "alert", "temp_password_create", None),
    ("export_burst", 10, 1, "alert", "export", None),
    ("login_success_churn", 15, 1, "alert", "user_login", None),
    ("device_switch_burst", 5, 24, "alert", "device_mismatch", None),
]


class Env:
    def __init__(self) -> None:
        self.rule: dict | None = None
        self.counts: dict[tuple[str, str | None], int] = {}
        self.challenge_count = 0
        self.calls: list[tuple] = []
        self.windows: list[tuple[str, datetime]] = []
        self.violations: list[dict] = []
        self.logs: list[dict] = []
        self.bans: list[dict] = []
        self.user_doc: dict | None = {"roles": ["student"], "banned": False}
        self.rule_error: Exception | None = None
        self.count_error: Exception | None = None


@pytest.fixture
def env(monkeypatch) -> Env:
    state = Env()
    risk_monitor._THROTTLE.clear()

    async def get_by_trigger(trigger):
        state.calls.append(("rule", trigger))
        if state.rule_error is not None:
            raise state.rule_error
        return state.rule

    async def count_by_user_ltype(user_oid, ltype, since_ts, contains=None):
        state.calls.append(("count", ltype, contains))
        if state.count_error is not None:
            raise state.count_error
        return state.counts.get((ltype, contains), 0)

    async def count_challenges(user_oid, since):
        state.calls.append(("challenges", user_oid))
        state.windows.append(("challenges", since))
        if state.count_error is not None:
            raise state.count_error
        return state.challenge_count

    async def insert_violation(user_oid, trigger, signal, *, xuehai_id=None, detail=None, batch_id=None):
        state.violations.append(
            {
                "user_oid": user_oid,
                "trigger": trigger,
                "signal": signal,
                "detail": detail,
                "batch_id": batch_id,
            }
        )
        return "v"

    async def get_by_oid(oid):
        return state.user_doc

    async def apply_account_ban(user_oid, rule_code, reason, *, now=None):
        state.bans.append({"user_oid": user_oid, "rule_code": rule_code, "reason": reason})
        return {"until": None, "permanent": True}

    class _Log:
        def __init__(self, url="", user="", data="", timestamp=None, type="", level="info", xuehai_id=""):
            self.url = url
            self.user = user
            self.data = data
            self.timestamp = timestamp
            self.type = type
            self.level = level
            self.xuehai_id = xuehai_id

        async def insert_log(self):
            state.logs.append(
                {"type": self.type, "level": self.level, "data": self.data, "user": self.user}
            )

    monkeypatch.setattr(
        backends,
        "get_ban_rules_repo",
        lambda *a, **k: SimpleNamespace(get_by_trigger=get_by_trigger),
    )
    monkeypatch.setattr(
        backends,
        "get_applogs_repo",
        lambda *a, **k: SimpleNamespace(count_by_user_ltype=count_by_user_ltype),
    )
    monkeypatch.setattr(
        backends,
        "get_violations_repo",
        lambda *a, **k: SimpleNamespace(insert=insert_violation),
    )
    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda *a, **k: SimpleNamespace(get_by_oid=get_by_oid),
    )
    monkeypatch.setattr(risk_monitor, "_count_challenges", count_challenges)
    monkeypatch.setattr(risk_monitor, "ZVMSLog", _Log)
    monkeypatch.setattr(device_guard, "apply_account_ban", apply_account_ban)
    return state


def _rule(trigger: str, *, mode="ban", threshold=10, window_hours=1, code=None, enabled=True):
    return {
        "code": code or trigger,
        "trigger": trigger,
        "enabled": enabled,
        "config": {"threshold": threshold, "window_hours": window_hours, "mode": mode},
        "penalties": [1, 3, 7],
    }


def _log_types(state: Env) -> list[str]:
    return [row["type"] for row in state.logs]


# ---------------------------------------------------------------------------
# 七条规则：达到阈值触发、未达阈值不触发
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("trigger", "threshold", "window_hours", "mode", "ltype", "contains"),
    RULE_TABLE,
)
@pytest.mark.asyncio
async def test_reaching_threshold_triggers(
    env, trigger, threshold, window_hours, mode, ltype, contains
) -> None:
    env.rule = _rule(
        trigger, mode=mode, threshold=threshold, window_hours=window_hours
    )
    if ltype is None:
        env.challenge_count = threshold
    else:
        env.counts[(ltype, contains)] = threshold

    result = await risk_monitor.evaluate(trigger, USER, now=NOW)

    assert result == {
        "trigger": trigger,
        "count": threshold,
        "threshold": threshold,
        "mode": mode,
        "banned": mode == "ban",
    }
    # 证据一行，signal 与 trigger 同名：管理员在违规列表里能直接对上规则。
    assert len(env.violations) == 1
    assert env.violations[0]["signal"] == trigger
    assert env.violations[0]["trigger"] == trigger
    assert f"count={threshold}" in env.violations[0]["detail"]
    assert f"threshold={threshold}" in env.violations[0]["detail"]
    assert env.violations[0]["batch_id"]

    assert "risk_alert" in _log_types(env)
    if mode == "ban":
        assert _log_types(env) == ["risk_alert", "risk_ban"]
        assert env.bans == [
            {"user_oid": USER, "rule_code": trigger, "reason": env.bans[0]["reason"]}
        ]
    else:
        # alert 模式绝不碰账号。
        assert env.bans == []
        assert _log_types(env) == ["risk_alert"]


@pytest.mark.parametrize(
    ("trigger", "threshold", "window_hours", "mode", "ltype", "contains"),
    RULE_TABLE,
)
@pytest.mark.asyncio
async def test_below_threshold_does_nothing(
    env, trigger, threshold, window_hours, mode, ltype, contains
) -> None:
    env.rule = _rule(
        trigger, mode=mode, threshold=threshold, window_hours=window_hours
    )
    if ltype is None:
        env.challenge_count = threshold - 1
    else:
        env.counts[(ltype, contains)] = threshold - 1

    assert await risk_monitor.evaluate(trigger, USER, now=NOW) is None
    assert env.violations == []
    assert env.logs == []
    assert env.bans == []


@pytest.mark.parametrize(
    ("trigger", "threshold", "window_hours", "mode", "ltype", "contains"),
    RULE_TABLE,
)
@pytest.mark.asyncio
async def test_window_follows_rule_config(
    env, trigger, threshold, window_hours, mode, ltype, contains
) -> None:
    """窗口来自规则 config，不是写死的常量：管理员改 window_hours 必须生效。"""
    env.rule = _rule(trigger, mode=mode, threshold=threshold, window_hours=6)
    if ltype is None:
        env.challenge_count = 0
    else:
        env.counts[(ltype, contains)] = 0

    await risk_monitor.evaluate(trigger, USER, now=NOW)

    if ltype is None:
        assert env.windows == [("challenges", NOW - timedelta(hours=6))]
    else:
        # 计数器是假实现，用「被查询过」证明配置被读进了判定路径。
        assert ("count", ltype, contains) in env.calls


# ---------------------------------------------------------------------------
# 规则闸门
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unregistered_trigger_is_ignored(env) -> None:
    """设备闸/上报闸的 trigger 有自己的入口，本模块不重复裁决。"""
    env.rule = _rule("foreign_device_login")

    assert await risk_monitor.evaluate("foreign_device_login", USER, now=NOW) is None
    assert env.calls == []


@pytest.mark.asyncio
async def test_missing_rule_does_nothing(env) -> None:
    assert await risk_monitor.evaluate("export_burst", USER, now=NOW) is None
    assert env.violations == []


@pytest.mark.asyncio
async def test_disabled_rule_does_nothing(env) -> None:
    env.rule = _rule("export_burst", mode="alert", threshold=1, enabled=False)
    env.counts[("export", None)] = 99

    assert await risk_monitor.evaluate("export_burst", USER, now=NOW) is None
    assert env.violations == []
    assert env.bans == []


@pytest.mark.asyncio
async def test_rule_config_uses_registry_defaults_when_incomplete(env) -> None:
    """``config`` 只有 threshold 时，窗口取注册表默认值而不是 24 小时。"""
    env.rule = {
        "code": "challenge_flood",
        "trigger": "challenge_flood",
        "enabled": True,
        "config": {"threshold": 8},
        "penalties": None,
    }
    env.challenge_count = 8

    result = await risk_monitor.evaluate("challenge_flood", USER, now=NOW)

    assert result is not None
    assert env.windows == [("challenges", NOW - timedelta(hours=1))]


# ---------------------------------------------------------------------------
# 不重复处罚 / 角色豁免
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_ban_is_not_escalated(env) -> None:
    env.rule = _rule("login_failure_burst", threshold=10)
    env.counts[("login_fail", None)] = 50
    env.user_doc = {"roles": ["student"], "banned": True, "ban_until": None}

    assert await risk_monitor.evaluate("login_failure_burst", USER, now=NOW) is None
    assert env.violations == []
    assert env.bans == []


@pytest.mark.asyncio
async def test_expired_ban_is_not_active(env) -> None:
    """``banned`` 列到期不归零：判定必须走 ``ban_is_active``，否则刑满的人封不掉。"""
    env.rule = _rule("login_failure_burst", threshold=2)
    env.counts[("login_fail", None)] = 2
    env.user_doc = {
        "roles": ["student"],
        "banned": True,
        "ban_until": NOW - timedelta(days=1),
    }

    result = await risk_monitor.evaluate("login_failure_burst", USER, now=NOW)

    assert result is not None and result["banned"] is True


@pytest.mark.asyncio
async def test_privileged_role_is_alerted_but_never_banned(env) -> None:
    env.rule = _rule("login_failure_burst", threshold=10)
    env.counts[("login_fail", None)] = 30
    env.user_doc = {"roles": ["secretary"], "banned": False}

    result = await risk_monitor.evaluate("login_failure_burst", USER, now=NOW)

    assert result is not None
    assert result["banned"] is False
    assert env.bans == []
    # 证据与告警仍然留：豁免不等于没发生。
    assert len(env.violations) == 1
    assert _log_types(env) == ["risk_alert", "risk_alert"]


@pytest.mark.asyncio
async def test_unreadable_user_falls_back_to_no_exemption(env) -> None:
    env.rule = _rule("login_failure_burst", threshold=1)
    env.counts[("login_fail", None)] = 1
    env.user_doc = None

    result = await risk_monitor.evaluate("login_failure_burst", USER, now=NOW)

    assert result is not None and result["banned"] is True


# ---------------------------------------------------------------------------
# 节流
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_second_evaluation_within_window_is_throttled(env) -> None:
    env.rule = _rule("login_success_churn", mode="alert", threshold=15)
    env.counts[("user_login", None)] = 20

    first = await risk_monitor.evaluate("login_success_churn", USER, now=NOW)
    second = await risk_monitor.evaluate(
        "login_success_churn", USER, now=NOW + timedelta(seconds=59)
    )

    assert first is not None
    assert second is None
    assert len(env.violations) == 1


@pytest.mark.asyncio
async def test_throttle_expires_and_is_per_trigger(env) -> None:
    env.rule = _rule("login_success_churn", mode="alert", threshold=15)
    env.counts[("user_login", None)] = 20

    await risk_monitor.evaluate("login_success_churn", USER, now=NOW)
    assert (
        await risk_monitor.evaluate(
            "login_success_churn", USER, now=NOW + timedelta(seconds=61)
        )
        is not None
    )
    assert len(env.violations) == 2

    # 另一个 trigger 是另一条独立的节流键。
    env.rule = _rule("export_burst", mode="alert", threshold=10)
    env.counts[("export", None)] = 10
    assert await risk_monitor.evaluate("export_burst", USER, now=NOW) is not None


@pytest.mark.asyncio
async def test_throttle_table_is_pruned(env, monkeypatch) -> None:
    """表超上限时清理过期项：节流表不能自己变成内存泄漏。"""
    monkeypatch.setattr(risk_monitor, "THROTTLE_MAX_ENTRIES", 1)
    risk_monitor._THROTTLE.clear()
    risk_monitor._THROTTLE[("old", "x")] = NOW.timestamp() - 3600
    risk_monitor._THROTTLE[("older", "y")] = NOW.timestamp() - 7200

    env.rule = None
    await risk_monitor.evaluate("export_burst", USER, now=NOW)

    assert ("old", "x") not in risk_monitor._THROTTLE
    assert ("older", "y") not in risk_monitor._THROTTLE


# ---------------------------------------------------------------------------
# 失败不冒泡
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rule_read_failure_does_not_raise(env) -> None:
    env.rule_error = RuntimeError("db down")

    assert await risk_monitor.evaluate("export_burst", USER, now=NOW) is None


@pytest.mark.asyncio
async def test_count_failure_does_not_raise(env) -> None:
    env.rule = _rule("export_burst", mode="alert", threshold=1)
    env.count_error = RuntimeError("db down")

    assert await risk_monitor.evaluate("export_burst", USER, now=NOW) is None
    assert env.violations == []


@pytest.mark.asyncio
async def test_violation_write_failure_still_alerts_and_bans(env, monkeypatch) -> None:
    """证据写失败不能让「该封的人」因为一条 INSERT 失败而放走。"""
    env.rule = _rule("login_failure_burst", threshold=3)
    env.counts[("login_fail", None)] = 3

    async def boom(*args, **kwargs):
        raise RuntimeError("violations table down")

    monkeypatch.setattr(
        backends, "get_violations_repo", lambda *a, **k: SimpleNamespace(insert=boom)
    )

    result = await risk_monitor.evaluate("login_failure_burst", USER, now=NOW)

    assert result is not None and result["banned"] is True
    assert len(env.bans) == 1


@pytest.mark.asyncio
async def test_ban_failure_is_swallowed(env, monkeypatch) -> None:
    env.rule = _rule("login_failure_burst", threshold=1)
    env.counts[("login_fail", None)] = 1

    async def boom(*args, **kwargs):
        raise RuntimeError("users table down")

    monkeypatch.setattr(device_guard, "apply_account_ban", boom)

    assert await risk_monitor.evaluate("login_failure_burst", USER, now=NOW) is None


# ---------------------------------------------------------------------------
# 规范化与配置
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_mode_falls_back_to_ban(env) -> None:
    env.rule = _rule("login_failure_burst", mode="warn", threshold=1)
    env.counts[("login_fail", None)] = 1

    result = await risk_monitor.evaluate("login_failure_burst", USER, now=NOW)

    assert result is not None
    assert result["mode"] == "ban"
    assert result["banned"] is True


@pytest.mark.asyncio
async def test_ban_uses_rule_code_not_trigger(env) -> None:
    """管理员改过 code 时，梯度与流水都要按 code 记，否则处罚会回落成默认档。"""
    env.rule = _rule(
        "login_failure_burst", threshold=1, code="custom_login_rule"
    )
    env.counts[("login_fail", None)] = 1

    await risk_monitor.evaluate("login_failure_burst", USER, now=NOW)

    assert env.bans[0]["rule_code"] == "custom_login_rule"


@pytest.mark.asyncio
async def test_every_registered_trigger_is_kept_in_the_router_registry() -> None:
    """服务层与注册表必须是同一份清单：漏一条就是「规则存在但永远不评估」。"""
    from routers import ban_router

    registered = {item["trigger"] for item in ban_router.TRIGGERS}
    assert set(risk_monitor.TRIGGERS) <= registered
    for trigger in risk_monitor.TRIGGERS:
        entry = ban_router.TRIGGER_REGISTRY[trigger]
        assert entry["evaluation"] == "monitor"
