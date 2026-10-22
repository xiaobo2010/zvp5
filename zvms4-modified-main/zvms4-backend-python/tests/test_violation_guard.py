"""``services.violation_guard`` 的离线契约（去重窗口、累计阈值、角色豁免）。

为什么这些用例都在服务层：封禁判定是政策，客户端上报只提供事实。这里的每条
断言都对应一个「能被事后追问」的决定——窗口多长、等号还是大于、谁能被自动封。
全部仓储打桩，不连 Oracle。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from db import backends
from services import ban_rules, violation_guard

USER = {"id": "000000000000000000000002", "per": ["student"]}
XUEHAI = "1234567890"


class FakeViolations:
    """内存版 ``ViolationsRepo``：只实现服务层用到的三个方法。"""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.windows: list[tuple[str, datetime]] = []

    async def insert(self, user_oid, trigger, signal, *, xuehai_id=None, detail=None):
        oid = f"{len(self.rows) + 1:024x}"
        self.rows.append(
            {
                "_id": oid,
                "user_oid": user_oid,
                "trigger": trigger,
                "signal": signal,
                "xuehai_id": xuehai_id,
                "detail": detail,
                "created_at": datetime.now(),
            }
        )
        return oid

    async def exists_recent(self, user_oid, signal, since):
        self.windows.append((signal, since))
        return any(
            row["user_oid"] == user_oid
            and row["signal"] == signal
            and row["created_at"] >= since
            for row in self.rows
        )

    async def count_since(self, user_oid, trigger, since):
        return sum(
            1
            for row in self.rows
            if row["user_oid"] == user_oid
            and row["trigger"] == trigger
            and row["created_at"] >= since
        )


def _rule(*, threshold: int = 3, window_hours: int = 24, enabled: bool = True) -> dict:
    return {
        "code": "platform_misuse",
        "trigger": "platform_misuse",
        "enabled": enabled,
        "config": {"threshold": threshold, "window_hours": window_hours},
        "penalties": [1, 3, 7],
    }


@pytest.fixture
def guard(monkeypatch):
    repo = FakeViolations()
    logs: list[dict] = []
    calls = {"rules": [], "bans": [], "logs": logs}

    state = {"rule": _rule()}

    async def get_rule_by_trigger(trigger):
        calls["rules"].append(trigger)
        return state["rule"]

    async def apply_account_ban(user_oid, rule_code, reason, **kwargs):
        calls["bans"].append((user_oid, rule_code, reason))
        return {"reason": reason, "until": None, "days": None, "permanent": True}

    async def insert_log(
        url, user, data, timestamp, ltype, level, xuehai_id
    ):  # noqa: A002 - 与 ZVMSLog.insert_log 的形参名保持一致
        logs.append(
            {
                "url": url,
                "user": user,
                "data": data,
                "type": ltype,
                "level": level,
                "xuehai_id": xuehai_id,
            }
        )

    monkeypatch.setattr(
        backends, "get_violations_repo", lambda *a, **k: repo, raising=False
    )
    monkeypatch.setattr(
        backends,
        "get_applogs_repo",
        lambda *a, **k: SimpleNamespace(insert=insert_log),
        raising=False,
    )
    monkeypatch.setattr(
        ban_rules, "get_rule_by_trigger", get_rule_by_trigger, raising=False
    )
    # 只换掉封禁动作，别把真实的 device_guard 模块改坏（monkeypatch 也会回滚，
    # 但整包替换能把「有没有调」看得更清楚）。
    monkeypatch.setattr(
        violation_guard,
        "device_guard",
        SimpleNamespace(apply_account_ban=apply_account_ban),
    )
    return repo, state, calls


def _violation_logs(logs: list[dict]) -> list[dict]:
    return [entry for entry in logs if entry["type"] == "platform_misuse_violation"]


# ---------------------------------------------------------------------------
# 入库与日志
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_records_each_valid_signal_with_audit_log(guard) -> None:
    repo, _state, calls = guard
    result = await violation_guard.record(
        USER,
        [
            {"signal": "iframe", "detail": "top!=self"},
            {"signal": "bridge", "detail": None},
        ],
        XUEHAI,
    )

    assert result == {
        "recorded": 2,
        "duplicates": 0,
        "banned": False,
        "rule_code": "platform_misuse",
    }
    assert [(row["signal"], row["detail"]) for row in repo.rows] == [
        ("iframe", "top!=self"),
        ("bridge", None),
    ]
    assert all(row["xuehai_id"] == XUEHAI for row in repo.rows)
    assert all(row["trigger"] == "platform_misuse" for row in repo.rows)

    logs = _violation_logs(calls["logs"])
    assert len(logs) == 2
    assert all(entry["level"] == "warning" for entry in logs)
    assert all(entry["xuehai_id"] == XUEHAI for entry in logs)
    assert "iframe" in logs[0]["data"] and "top!=self" in logs[0]["data"]
    assert "bridge" in logs[1]["data"]


@pytest.mark.asyncio
async def test_unknown_signal_is_ignored_not_fatal(guard) -> None:
    repo, _state, _calls = guard
    result = await violation_guard.record(
        USER, [{"signal": "screenshot"}, {"signal": "iframe"}], XUEHAI
    )

    assert result["recorded"] == 1
    assert [row["signal"] for row in repo.rows] == ["iframe"]


@pytest.mark.asyncio
async def test_detail_is_truncated_to_column_width(guard) -> None:
    repo, _state, _calls = guard
    await violation_guard.record(
        USER, [{"signal": "iframe", "detail": "x" * 900}], XUEHAI
    )

    assert len(repo.rows[0]["detail"]) == violation_guard.MAX_DETAIL_CHARS


# ---------------------------------------------------------------------------
# 去重窗口
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_within_window_is_not_recorded(guard) -> None:
    repo, _state, calls = guard
    repo.rows.append(
        {
            "_id": "0" * 24,
            "user_oid": USER["id"],
            "trigger": "platform_misuse",
            "signal": "iframe",
            "xuehai_id": XUEHAI,
            "detail": None,
            "created_at": datetime.now() - timedelta(seconds=60),
        }
    )

    result = await violation_guard.record(
        USER, [{"signal": "iframe"}], XUEHAI
    )

    assert result == {
        "recorded": 0,
        "duplicates": 1,
        "banned": False,
        "rule_code": "platform_misuse",
    }
    assert len(repo.rows) == 1
    # 全是重复：计数没变，不该再读规则表。
    assert calls["rules"] == []


@pytest.mark.asyncio
async def test_devtools_has_longer_dedup_window(guard) -> None:
    """1000 秒前的 devtools 仍算重复，同一时刻的 iframe 已经出窗。"""
    repo, _state, _calls = guard
    old = datetime.now() - timedelta(seconds=1000)
    for signal in ("devtools", "iframe"):
        repo.rows.append(
            {
                "_id": "1" * 24,
                "user_oid": USER["id"],
                "trigger": "platform_misuse",
                "signal": signal,
                "xuehai_id": XUEHAI,
                "detail": None,
                "created_at": old,
            }
        )

    result = await violation_guard.record(
        USER, [{"signal": "devtools"}, {"signal": "iframe"}], XUEHAI
    )

    assert result["recorded"] == 1
    assert result["duplicates"] == 1
    windows = {signal: since for signal, since in repo.windows}
    now = datetime.now()
    assert 1790 <= (now - windows["devtools"]).total_seconds() <= 1810
    assert 290 <= (now - windows["iframe"]).total_seconds() <= 310


# ---------------------------------------------------------------------------
# 阈值与封禁
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bans_only_when_count_equals_threshold(guard) -> None:
    repo, state, calls = guard
    state["rule"] = _rule(threshold=2)
    first = await violation_guard.record(
        USER, [{"signal": "iframe", "detail": "a"}], XUEHAI
    )
    assert first["banned"] is False
    assert calls["bans"] == []

    second = await violation_guard.record(
        USER, [{"signal": "bridge", "detail": "b"}], XUEHAI
    )
    assert second["banned"] is True
    assert calls["bans"] == [
        (USER["id"], "platform_misuse", "24 小时内累计 2 次平台违规使用（阈值 2 次）")
    ]
    ban_logs = [entry for entry in calls["logs"] if entry["type"] == "ban_rule"]
    assert len(ban_logs) == 1
    assert ban_logs[0]["level"] == "warning"
    assert "阈值 2 次" in ban_logs[0]["data"]

    # 第三次上报越过阈值：用等号判断，不会反复封。
    third = await violation_guard.record(
        USER, [{"signal": "devtools", "detail": "c"}], XUEHAI
    )
    assert third["banned"] is False
    assert len(calls["bans"]) == 1
    assert len(repo.rows) == 3


@pytest.mark.asyncio
async def test_count_above_threshold_does_not_backfill_a_ban(guard) -> None:
    """管理员调低阈值后 count > threshold：不该由下一条无关上报追罚。"""
    repo, state, calls = guard
    state["rule"] = _rule(threshold=2)
    for signal in ("iframe", "bridge", "devtools"):
        repo.rows.append(
            {
                "_id": f"{len(repo.rows) + 1:024x}",
                "user_oid": USER["id"],
                "trigger": "platform_misuse",
                "signal": signal,
                "xuehai_id": XUEHAI,
                "detail": None,
                "created_at": datetime.now() - timedelta(minutes=1),
            }
        )

    result = await violation_guard.record(
        USER, [{"signal": "iframe", "detail": "new batch"}], XUEHAI
    )

    assert result["banned"] is False
    assert calls["bans"] == []


@pytest.mark.asyncio
async def test_disabled_rule_only_records(guard) -> None:
    repo, state, calls = guard
    state["rule"] = _rule(threshold=1, enabled=False)

    result = await violation_guard.record(
        USER, [{"signal": "iframe"}], XUEHAI
    )

    assert result["recorded"] == 1
    assert result["banned"] is False
    assert len(repo.rows) == 1
    assert calls["bans"] == []


@pytest.mark.asyncio
async def test_missing_rule_only_records(guard) -> None:
    repo, state, calls = guard
    state["rule"] = None

    result = await violation_guard.record(
        USER, [{"signal": "iframe"}], XUEHAI
    )

    assert result["recorded"] == 1
    assert result["banned"] is False
    assert len(repo.rows) == 1
    assert calls["bans"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "department", "teacher"])
async def test_privileged_roles_are_recorded_but_never_banned(guard, role) -> None:
    repo, state, calls = guard
    state["rule"] = _rule(threshold=1)

    result = await violation_guard.record(
        {**USER, "per": [role]}, [{"signal": "iframe"}], XUEHAI
    )

    assert result["recorded"] == 1
    assert result["banned"] is False
    assert len(repo.rows) == 1
    assert calls["bans"] == []


@pytest.mark.asyncio
async def test_ban_failure_keeps_evidence_and_does_not_raise(guard, monkeypatch) -> None:
    repo, state, calls = guard
    state["rule"] = _rule(threshold=1)

    async def boom(*args, **kwargs):
        raise RuntimeError("oracle down")

    monkeypatch.setattr(
        violation_guard,
        "device_guard",
        SimpleNamespace(apply_account_ban=boom),
    )

    result = await violation_guard.record(
        USER, [{"signal": "iframe", "detail": "kept"}], XUEHAI
    )

    assert result["recorded"] == 1
    assert result["banned"] is False
    assert len(repo.rows) == 1
    assert [entry for entry in calls["logs"] if entry["type"] == "ban_rule"] == []


@pytest.mark.asyncio
async def test_rule_with_broken_config_falls_back_to_default(guard) -> None:
    repo, state, calls = guard
    state["rule"] = {"enabled": True, "config": "not-json", "penalties": "[1]"}

    result = await violation_guard.record(
        USER, [{"signal": "iframe"}], XUEHAI
    )

    assert result["recorded"] == 1
    # 默认阈值 3：一次上报不该封。
    assert result["banned"] is False
    assert calls["bans"] == []


@pytest.mark.asyncio
async def test_rule_lookup_failure_still_keeps_evidence(guard, monkeypatch) -> None:
    repo, _state, _calls = guard

    async def boom(trigger):
        raise RuntimeError("oracle down")

    monkeypatch.setattr(ban_rules, "get_rule_by_trigger", boom, raising=False)

    result = await violation_guard.record(
        USER, [{"signal": "iframe"}], XUEHAI
    )

    assert result["recorded"] == 1
    assert result["banned"] is False
    assert len(repo.rows) == 1
