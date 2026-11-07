"""规则封禁服务层（``services/ban_rules.py`` + ``util/ban.py``）的离线契约。

钉住四件一旦写错就会静默出错的事：

* 判定口径：到期封禁的 ``banned`` 列仍是 1，只有 ``ban_is_active`` 才能放行；
* 403 契约：``detail="USER_BANNED"`` + 头里带 URL 编码的原因/到期 epoch；
* 梯度处罚：第 N 次犯按 ``penalties[min(N-1, len-1)]`` 取天数，超出梯度取末档，
  末位 ``None`` = 永久；
* 只加刑不减刑：规则算出的 ``until`` 弱于既有封禁时不得覆盖，流水照落。

求值已按证据来源拆到各入口（设备闸 / 上报闸），这里只测三者共用的积木。
全部离线（backends 全打桩），不连 Oracle。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from urllib.parse import quote, unquote

import pytest
from fastapi import HTTPException

from db import backends
from services import ban_rules
from util.ban import (
    ban_headers,
    ban_is_active,
    ban_reason_of,
    raise_banned,
)

NOW = datetime(2026, 10, 20, 12, 0, 0)
USER = "000000000000000000000002"


# ---------------------------------------------------------------------------
# util/ban.py
# ---------------------------------------------------------------------------


def test_permanent_ban_is_active() -> None:
    assert ban_is_active({"banned": True}, now=NOW) is True
    assert ban_is_active({"banned": 1}, now=NOW) is True


def test_unexpired_ban_is_active() -> None:
    doc = {"banned": True, "ban_until": NOW + timedelta(days=1)}
    assert ban_is_active(doc, now=NOW) is True


def test_expired_ban_is_not_active() -> None:
    """刑满即自动失效——这是「不能只看 banned 列」的核心原因。"""
    doc = {"banned": True, "ban_until": NOW - timedelta(seconds=1)}
    assert ban_is_active(doc, now=NOW) is False


def test_unbanned_or_missing_doc_is_not_active() -> None:
    assert ban_is_active({}, now=NOW) is False
    assert ban_is_active({"banned": False}, now=NOW) is False
    assert ban_is_active(None, now=NOW) is False


def test_ban_headers_urlencodes_chinese_reason() -> None:
    doc = {"banned": True, "ban_reason": "多次刷分", "ban_until": NOW + timedelta(days=2)}
    headers = ban_headers(doc)

    assert headers["X-Ban-Reason"] == quote("多次刷分", safe="")
    assert "%" in headers["X-Ban-Reason"]
    assert unquote(headers["X-Ban-Reason"]) == "多次刷分"
    assert headers["X-Ban-Until"] == str(int((NOW + timedelta(days=2)).timestamp()))


def test_ban_headers_permanent_has_empty_until() -> None:
    headers = ban_headers({"banned": True, "ban_reason": "管理员封禁"})
    assert headers["X-Ban-Until"] == ""
    assert unquote(headers["X-Ban-Reason"]) == "管理员封禁"


def test_ban_reason_falls_back_when_missing_or_blank() -> None:
    assert ban_reason_of({}) == "违反平台使用规定"
    assert ban_reason_of({"ban_reason": "   "}) == "违反平台使用规定"
    assert ban_reason_of({"ban_reason": " 刷分 "}) == "刷分"


def test_raise_banned_is_403_user_banned_with_headers() -> None:
    with pytest.raises(HTTPException) as failure:
        raise_banned({"banned": True, "ban_reason": "刷分"})

    assert failure.value.status_code == 403
    assert failure.value.detail == "USER_BANNED"
    assert unquote(failure.value.headers["X-Ban-Reason"]) == "刷分"


# ---------------------------------------------------------------------------
# rule_config / rule_penalties：宽进——坏数据回退默认值，永不因为配置坏而不罚
# ---------------------------------------------------------------------------


def test_rule_config_reads_json_text() -> None:
    rule = {"config": json.dumps({"threshold": 5, "window_hours": 48})}
    assert ban_rules.rule_config(rule) == {
        "threshold": 5,
        "window_hours": 48,
        "mode": ban_rules.MODE_BAN,
    }


def test_rule_config_accepts_dict_and_falls_back_on_broken_json() -> None:
    assert ban_rules.rule_config({"config": {"threshold": 4, "window_hours": 2}}) == {
        "threshold": 4,
        "window_hours": 2,
        "mode": ban_rules.MODE_BAN,
    }
    fallback = {**ban_rules.DEFAULT_CONFIG, "mode": ban_rules.MODE_BAN}
    assert ban_rules.rule_config({"config": "{not json"}) == fallback
    assert ban_rules.rule_config(None) == fallback
    assert ban_rules.rule_config({"config": json.dumps({"threshold": 0})}) == fallback


def test_rule_penalties_truncates_at_permanent_step() -> None:
    """``null`` 只在末位有意义：读到它就到此为止，调用方不必防「中间夹 null」。"""
    rule = {"penalties": json.dumps([1, 3, None, 9])}
    assert ban_rules.rule_penalties(rule) == [1, 3, None]


def test_rule_penalties_falls_back_to_one_day() -> None:
    assert ban_rules.rule_penalties({"penalties": "[]"}) == [1]
    assert ban_rules.rule_penalties({"penalties": "[0]"}) == [1]
    assert ban_rules.rule_penalties(None) == [1]


# ---------------------------------------------------------------------------
# penalty_expiry：梯度取档
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("offense", "days"),
    [(1, 1), (2, 3), (3, 7), (4, 14), (5, 30)],
)
def test_penalty_expiry_walks_the_gradient(offense: int, days: int) -> None:
    until, step = ban_rules.penalty_expiry([1, 3, 7, 14, 30, None], offense, NOW)
    assert step == days
    assert until == NOW + timedelta(days=days)


def test_penalty_expiry_beyond_gradient_uses_last_step() -> None:
    until, step = ban_rules.penalty_expiry([1, 2, 7], 99, NOW)
    assert step == 7
    assert until == NOW + timedelta(days=7)


def test_penalty_expiry_permanent_step_is_none() -> None:
    """末档 ``None`` → 永久：调用方据此写 ``permanent=True`` 且 ``expires_at=None``。"""
    assert ban_rules.penalty_expiry([1, 3, None], 3, NOW) == (None, None)
    assert ban_rules.penalty_expiry([1, 3, None], 9, NOW) == (None, None)


def test_penalty_expiry_empty_gradient_falls_back_to_one_day() -> None:
    until, step = ban_rules.penalty_expiry([], 1, NOW)
    assert step == 1
    assert until == NOW + timedelta(days=1)


# ---------------------------------------------------------------------------
# set_ban_unless_stronger：只加刑不减刑
# ---------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch):
    """打桩用户仓储，返回可调的 state。"""
    state = {"current_ban": None, "bans": []}

    async def set_ban(oid, *, reason, until, source, rule_code):
        state["bans"].append(
            {
                "oid": oid,
                "reason": reason,
                "until": until,
                "source": source,
                "rule_code": rule_code,
            }
        )

    async def get_by_oid(oid):
        return state["current_ban"]

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda *a, **k: SimpleNamespace(set_ban=set_ban, get_by_oid=get_by_oid),
    )
    return state


async def _apply(state, penalties, offense=1, rule_code="platform_misuse"):
    """按梯度算出 ``until`` 再落封禁——与各入口的调用顺序一致。"""
    until, _ = ban_rules.penalty_expiry(penalties, offense, NOW)
    return await ban_rules.set_ban_unless_stronger(
        USER, reason="违规", until=until, rule_code=rule_code
    )


@pytest.mark.asyncio
async def test_ban_is_written_when_nothing_stronger_exists(env) -> None:
    until = NOW + timedelta(days=1)
    assert await _apply(env, [1]) == until
    assert env["bans"] == [
        {
            "oid": USER,
            "reason": "违规",
            "until": until,
            "source": "rule",
            "rule_code": "platform_misuse",
        }
    ]


@pytest.mark.asyncio
async def test_permanent_admin_ban_is_not_downgraded(env, caplog) -> None:
    env["current_ban"] = {"banned": True, "ban_reason": "管理员封禁"}

    assert await _apply(env, [1]) is None

    assert env["bans"] == []  # USERS 行没被改写
    assert "保留既有封禁" in caplog.text


@pytest.mark.asyncio
async def test_longer_existing_until_is_kept(env) -> None:
    later = NOW + timedelta(days=30)
    env["current_ban"] = {"banned": True, "ban_until": later, "ban_reason": "管理员封禁"}

    assert await _apply(env, [1]) == later
    assert env["bans"] == []


@pytest.mark.asyncio
async def test_expired_existing_ban_is_overwritten(env) -> None:
    env["current_ban"] = {"banned": True, "ban_until": NOW - timedelta(days=3)}

    until = NOW + timedelta(days=1)
    assert await _apply(env, [1]) == until
    assert env["bans"][0]["until"] == until


@pytest.mark.asyncio
async def test_permanent_step_overwrites_any_existing_ban(env) -> None:
    """本次算出的 ``until`` 为 ``None``（永久）时它最强，一律覆写。"""
    env["current_ban"] = {"banned": True, "ban_until": NOW + timedelta(days=365)}

    assert await _apply(env, [1, None], offense=2) is None
    assert env["bans"][0]["until"] is None


# ---------------------------------------------------------------------------
# get_rule_by_trigger：求值入口按检查项取规则，不看管理员起的名字
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_rule_by_trigger_delegates_to_repo(monkeypatch) -> None:
    seen: list[str] = []
    rule = {"code": "my_rule", "trigger": "platform_misuse", "enabled": True}

    async def get_by_trigger(trigger):
        seen.append(trigger)
        return rule

    monkeypatch.setattr(
        backends,
        "get_ban_rules_repo",
        lambda *a, **k: SimpleNamespace(get_by_trigger=get_by_trigger),
    )

    assert await ban_rules.get_rule_by_trigger("platform_misuse") is rule
    assert seen == ["platform_misuse"]


@pytest.mark.asyncio
async def test_get_rule_by_trigger_returns_none_when_absent(monkeypatch) -> None:
    async def get_by_trigger(trigger):
        return None

    monkeypatch.setattr(
        backends,
        "get_ban_rules_repo",
        lambda *a, **k: SimpleNamespace(get_by_trigger=get_by_trigger),
    )

    assert await ban_rules.get_rule_by_trigger("platform_misuse") is None


def test_evaluate_and_enforce_login_are_gone() -> None:
    """判定已按证据来源拆到各入口；本模块再长出这两个名字就是回退。"""
    assert not hasattr(ban_rules, "evaluate_login")
    assert not hasattr(ban_rules, "enforce_login")
    assert "RULE_CODE" not in ban_rules.__all__


# ---------------------------------------------------------------------------
# 默认值的唯一来源（误封 B7/B8）
# ---------------------------------------------------------------------------


def test_registry_default_matches_service_default() -> None:
    """注册表给表单的初值必须与服务默认值同源：两套「默认」= 两套判定。"""
    assert ban_rules.REGISTRY_DEFAULT_CONFIG == ban_rules.DEFAULT_CONFIG


def test_normalize_config_fills_missing_keys_per_field() -> None:
    """逐项归一：只写了一个键时另一个键补默认值，已有值不被冲掉。"""
    assert ban_rules.normalize_config({"threshold": 5}) == {
        "threshold": 5,
        "window_hours": 24,
        "mode": ban_rules.MODE_BAN,
    }
    assert ban_rules.normalize_config({"window_hours": 6}) == {
        "threshold": 3,
        "window_hours": 6,
        "mode": ban_rules.MODE_BAN,
    }
    assert ban_rules.normalize_config(None) == {
        **ban_rules.DEFAULT_CONFIG,
        "mode": ban_rules.MODE_BAN,
    }
    assert ban_rules.normalize_config({"threshold": "x", "window_hours": 12}) == {
        "threshold": 3,
        "window_hours": 12,
        "mode": ban_rules.MODE_BAN,
    }


def test_normalize_config_accepts_an_explicit_baseline() -> None:
    base = {"threshold": 2, "window_hours": 1, "mode": ban_rules.MODE_ALERT}
    assert ban_rules.normalize_config({}, defaults=base) == base
    assert ban_rules.normalize_config(None, defaults=base) == base


def test_normalize_config_defaults_mode_to_ban() -> None:
    """缺省 mode 等价于既有行为：存量规则不需要改数据也不会突然只告警。"""
    assert ban_rules.normalize_config({"threshold": 5})["mode"] == ban_rules.MODE_BAN


def test_normalize_config_keeps_alert_and_degrades_unknown_mode() -> None:
    assert ban_rules.normalize_config({"mode": "alert"})["mode"] == ban_rules.MODE_ALERT
    assert ban_rules.normalize_config({"mode": "ALERT "})["mode"] == ban_rules.MODE_ALERT
    assert ban_rules.normalize_config({"mode": "warn"})["mode"] == ban_rules.MODE_BAN
    assert ban_rules.normalize_config({"mode": 1})["mode"] == ban_rules.MODE_BAN
