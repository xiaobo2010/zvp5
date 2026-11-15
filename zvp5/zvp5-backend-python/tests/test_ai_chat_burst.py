"""``ai_chat_burst`` 风控规则：窗口计数、告警/封禁分支、节流与注册表。

这条规则的证据来自 ``AI_CALL_LOGS``（不是 ``APP_LOGS``）：窗口计数必须只数
``capability='chat'`` 的行，被拒/失败也要数——一直敲门本身就是信号。裁决复用
``risk_monitor`` 的既有闸门，所以这里重点钉它与别处的**不同**之处：

* 计数走 ``count_chat_by_user_since``（不是 ``count_by_user_ltype``）；
* 默认 ``mode="alert"``：只写 ``risk_alert`` + 证据，不调封禁；
* ``department`` 在豁免名单里：即使管理员把规则改成 ``ban`` 也不会自动封人；
* 60 秒 ``(trigger, user)`` 节流照旧生效；
* ``routers.ban_router`` 的注册表里必须有这一项（管理端下拉 + 表单初值）。

全部离线：仓储、用户、日志、封禁全部打桩。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from db import backends
from routers import ban_router
from services import device_guard, risk_monitor

NOW = datetime(2026, 10, 25, 12, 0, 0)
USER = "000000000000000000000002"
CHAT_WINDOW_HOURS = 1
THRESHOLD = 80


class Env:
    def __init__(self) -> None:
        self.rule: dict | None = None
        self.chat_count = 0
        self.chat_calls: list[tuple[str, datetime]] = []
        self.other_counts: list[tuple] = []
        self.violations: list[dict] = []
        self.logs: list[dict] = []
        self.bans: list[dict] = []
        self.user_doc: dict | None = {"roles": ["student"], "banned": False}


def _rule(*, mode: str = "alert", threshold: int = THRESHOLD, enabled: bool = True) -> dict:
    return {
        "code": "ai_chat_burst",
        "trigger": "ai_chat_burst",
        "enabled": enabled,
        "config": {
            "threshold": threshold,
            "window_hours": CHAT_WINDOW_HOURS,
            "mode": mode,
        },
        "penalties": [1, 3, 7],
    }


@pytest.fixture()
def env(monkeypatch) -> Env:
    state = Env()
    risk_monitor._THROTTLE.clear()
    state.rule = _rule()

    async def get_by_trigger(trigger):
        return state.rule

    async def count_chat_by_user_since(user_oid, since):
        state.chat_calls.append((user_oid, since))
        return state.chat_count

    async def count_by_user_ltype(user_oid, ltype, since_ts, contains=None):
        # 走错分支时留下证据：这条规则不该碰 APP_LOGS。
        state.other_counts.append((user_oid, ltype, since_ts, contains))
        return 0

    async def insert_violation(
        user_oid, trigger, signal, *, xuehai_id=None, detail=None, batch_id=None
    ):
        state.violations.append({"user_oid": user_oid, "trigger": trigger, "detail": detail})
        return "v"

    async def get_by_oid(oid):
        return state.user_doc

    async def apply_account_ban(user_oid, rule_code, reason, *, now=None):
        state.bans.append({"user_oid": user_oid, "rule_code": rule_code, "reason": reason})
        return {"until": None, "permanent": True}

    class _Log:
        def __init__(self, url="", user="", data="", timestamp=None, type="", level="info", xuehai_id=""):
            self.type = type
            self.level = level
            self.data = data
            self.user = user

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
        "get_ai_call_logs_repo",
        lambda *a, **k: SimpleNamespace(
            count_chat_by_user_since=count_chat_by_user_since
        ),
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
        backends, "get_users_repo", lambda *a, **k: SimpleNamespace(get_by_oid=get_by_oid)
    )
    monkeypatch.setattr(risk_monitor, "ZVMSLog", _Log)
    monkeypatch.setattr(device_guard, "apply_account_ban", apply_account_ban)
    return state


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------


def test_trigger_is_registered_in_both_registries():
    assert risk_monitor.TRIGGERS["ai_chat_burst"]["source"] == "ai_logs"
    # AI_CALL_LOGS 没有 ltype/摘要：两个过滤字段恒为 None（形状统一，不做无用过滤）。
    assert risk_monitor.TRIGGERS["ai_chat_burst"]["ltype"] is None
    assert risk_monitor.TRIGGERS["ai_chat_burst"]["contains"] is None

    entry = ban_router.TRIGGER_REGISTRY["ai_chat_burst"]
    assert entry["name"] == "AI 对话请求突发"
    assert entry["evaluation"] == "monitor"
    assert entry["config"] == {
        "threshold": 80,
        "window_hours": 1,
        "mode": "alert",
    }


def test_every_registered_trigger_has_a_ban_router_entry():
    registered = {item["trigger"] for item in ban_router.TRIGGERS}
    assert set(risk_monitor.TRIGGERS) <= registered


# ---------------------------------------------------------------------------
# 计数窗口
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_reads_the_chat_rows_of_the_window(env):
    env.chat_count = 3

    await risk_monitor.evaluate("ai_chat_burst", USER, now=NOW)

    (user_oid, since), = env.chat_calls
    assert user_oid == USER
    assert (NOW - since).total_seconds() == CHAT_WINDOW_HOURS * 3600
    # 走错源就会在这里留下 1 条记录（APP_LOGS 分支不该被调用）。
    assert env.other_counts == []


@pytest.mark.asyncio
async def test_below_the_threshold_does_nothing(env):
    env.chat_count = THRESHOLD - 1

    assert await risk_monitor.evaluate("ai_chat_burst", USER, now=NOW) is None
    assert env.violations == [] and env.logs == [] and env.bans == []


@pytest.mark.asyncio
async def test_reaching_the_threshold_alerts_and_records_evidence(env):
    env.chat_count = THRESHOLD

    result = await risk_monitor.evaluate("ai_chat_burst", USER, now=NOW)

    assert result == {
        "trigger": "ai_chat_burst",
        "count": THRESHOLD,
        "threshold": THRESHOLD,
        "mode": "alert",
        "banned": False,
    }
    (violation,) = env.violations
    assert violation["trigger"] == "ai_chat_burst"
    assert f"count={THRESHOLD}" in violation["detail"]
    (log,) = env.logs
    assert log["type"] == "risk_alert"
    assert "AI 对话请求突发" in log["data"]
    # alert 模式绝不封人。
    assert env.bans == []


@pytest.mark.asyncio
async def test_department_is_exempt_even_when_the_rule_is_flipped_to_ban(env):
    """学生会是这条规则的目标用户，但仍在自动封禁豁免名单里（现状语义）。"""
    env.rule = _rule(mode="ban")
    env.chat_count = THRESHOLD
    env.user_doc = {"roles": ["department"], "banned": False}

    result = await risk_monitor.evaluate("ai_chat_burst", USER, now=NOW)

    assert result == {
        "trigger": "ai_chat_burst",
        "count": THRESHOLD,
        "threshold": THRESHOLD,
        "mode": "ban",
        "banned": False,
    }
    assert env.bans == []
    # 证据与豁免说明都要留下：只告警不封禁也要能解释为什么。
    assert [log["type"] for log in env.logs] == ["risk_alert", "risk_alert"]
    assert "特权角色" in env.logs[1]["data"]


@pytest.mark.asyncio
async def test_a_plain_student_is_banned_when_the_mode_is_ban(env):
    """切到 ban 之后，非豁免角色走既有梯度——规则接入没有改变裁决语义。"""
    env.rule = _rule(mode="ban")
    env.chat_count = THRESHOLD
    env.user_doc = {"roles": ["student"], "banned": False}

    result = await risk_monitor.evaluate("ai_chat_burst", USER, now=NOW)

    assert result["banned"] is True
    (ban,) = env.bans
    assert ban["rule_code"] == "ai_chat_burst"


# ---------------------------------------------------------------------------
# 节流：60 秒内同一 (trigger, user) 只评估一次
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_throttle_swallows_the_second_evaluation(env):
    env.chat_count = THRESHOLD

    first = await risk_monitor.evaluate("ai_chat_burst", USER, now=NOW)
    second = await risk_monitor.evaluate(
        "ai_chat_burst", USER, now=NOW.replace(second=30)
    )

    assert first is not None
    assert second is None
    assert len(env.violations) == 1


@pytest.mark.asyncio
async def test_throttle_expires_after_sixty_seconds(env):
    env.chat_count = THRESHOLD

    assert await risk_monitor.evaluate("ai_chat_burst", USER, now=NOW) is not None
    assert (
        await risk_monitor.evaluate(
            "ai_chat_burst", USER, now=NOW.replace(minute=1, second=1)
        )
        is not None
    )
    assert len(env.violations) == 2


@pytest.mark.asyncio
async def test_a_disabled_rule_does_nothing(env):
    env.rule = _rule(enabled=False)
    env.chat_count = THRESHOLD

    assert await risk_monitor.evaluate("ai_chat_burst", USER, now=NOW) is None
    assert env.violations == [] and env.logs == []
