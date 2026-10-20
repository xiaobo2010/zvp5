"""规则封禁服务层（``services/ban_rules.py`` + ``util/ban.py``）的离线契约。

钉住四件一旦写错就会静默出错的事：

* 判定口径：到期封禁的 ``banned`` 列仍是 1，只有 ``ban_is_active`` 才能放行；
* 403 契约：``detail="USER_BANNED"`` + 头里带 URL 编码的原因/到期 epoch；
* 梯度处罚：第 N 次犯按 ``penalties[min(N-1, len-1)]`` 取天数，超出梯度取末档；
* fail-open：除命中后主动抛出的 403，任何异常都不得阻断登录。

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
XUEHAI = "20260001"


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
# services/ban_rules.evaluate_login
# ---------------------------------------------------------------------------


class StubLogs:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(self, url, user, data, timestamp, ltype, level, xuehai_id):
        self.rows.append(
            {
                "url": url,
                "user": user,
                "data": data,
                "timestamp": timestamp,
                "type": ltype,
                "level": level,
                "xuehai_id": xuehai_id,
            }
        )


def _rule(
    *,
    enabled: bool = True,
    threshold: int = 3,
    window_hours: int = 24,
    penalties="[1,2,7]",
):
    return {
        "code": "multi_device_login",
        "name": "多设备登录",
        "enabled": enabled,
        "builtin": True,
        "config": json.dumps({"threshold": threshold, "window_hours": window_hours}),
        "penalties": penalties,
    }


@pytest.fixture
def env(monkeypatch):
    """打桩规则/日志/流水/用户四个仓储，返回可调的 state。"""
    state = {
        "rule": _rule(),
        "devices": 3,
        "history": 0,
        "since": None,
        "bans": [],
        "records": [],
        "logs": StubLogs(),
        "rule_error": None,
    }

    async def get_by_code(code):
        if state["rule_error"] is not None:
            raise state["rule_error"]
        return state["rule"] if code == "multi_device_login" else None

    async def count_distinct_xuehai(user_oid, since_ts):
        state["since"] = since_ts
        return state["devices"]

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

    async def count_for_user_rule(user_oid, rule_code):
        return state["history"]

    async def insert(user_oid, **kwargs):
        state["records"].append({"user_oid": user_oid, **kwargs})
        return "rec"

    monkeypatch.setattr(
        backends,
        "get_ban_rules_repo",
        lambda *a, **k: SimpleNamespace(get_by_code=get_by_code),
    )
    monkeypatch.setattr(
        backends,
        "get_applogs_repo",
        lambda *a, **k: SimpleNamespace(
            count_distinct_xuehai=count_distinct_xuehai,
            insert=state["logs"].insert,
        ),
    )
    monkeypatch.setattr(
        backends,
        "get_ban_records_repo",
        lambda *a, **k: SimpleNamespace(
            count_for_user_rule=count_for_user_rule, insert=insert
        ),
    )
    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda *a, **k: SimpleNamespace(set_ban=set_ban),
    )
    return state


async def _evaluate(state, roles=("student",), xuehai_id=XUEHAI, now=NOW):
    return await ban_rules.evaluate_login(
        USER, list(roles), "张三", xuehai_id, "http://test/api/users/auth", now=now
    )


@pytest.mark.asyncio
async def test_below_threshold_does_not_ban(env) -> None:
    env["devices"] = 2
    assert await _evaluate(env) is None
    assert env["bans"] == []
    assert env["records"] == []


@pytest.mark.asyncio
async def test_window_lower_bound_is_window_hours_before_now(env) -> None:
    await _evaluate(env)
    assert env["since"] == NOW.timestamp() - 24 * 3600


@pytest.mark.asyncio
async def test_first_offense_bans_one_day(env) -> None:
    env["history"] = 0
    result = await _evaluate(env)

    assert result is not None
    assert result["days"] == 1
    assert result["devices"] == 3
    assert result["until"] == NOW + timedelta(days=1)
    assert result["reason"] == "在 24 小时内使用 3 台学海设备登录（阈值 3 台）"

    ban = env["bans"][0]
    assert ban == {
        "oid": USER,
        "reason": result["reason"],
        "until": NOW + timedelta(days=1),
        "source": "rule",
        "rule_code": "multi_device_login",
    }

    record = env["records"][0]
    assert record["user_oid"] == USER
    assert record["source"] == "rule"
    assert record["rule_code"] == "multi_device_login"
    assert record["permanent"] is False
    assert record["started_at"] == NOW
    assert record["expires_at"] == NOW + timedelta(days=1)
    assert record["operator_oid"] is None


@pytest.mark.asyncio
async def test_second_offense_bans_two_days(env) -> None:
    env["history"] = 1
    result = await _evaluate(env)
    assert result["days"] == 2
    assert env["bans"][0]["until"] == NOW + timedelta(days=2)


@pytest.mark.asyncio
async def test_offense_beyond_gradient_uses_last_step(env) -> None:
    env["history"] = 9  # 第 10 次
    result = await _evaluate(env)
    assert result["days"] == 7


@pytest.mark.asyncio
async def test_empty_penalties_falls_back_to_one_day(env) -> None:
    env["rule"] = _rule(penalties="[]")
    result = await _evaluate(env)
    assert result["days"] == 1


@pytest.mark.asyncio
async def test_broken_config_falls_back_to_defaults(env) -> None:
    env["rule"] = {
        **_rule(),
        "config": "{not json",
    }
    result = await _evaluate(env)
    assert result["devices"] == 3
    assert result["reason"].startswith("在 24 小时内")


@pytest.mark.asyncio
async def test_admin_is_never_auto_banned(env) -> None:
    assert await _evaluate(env, roles=("admin",)) is None
    assert env["since"] is None
    assert env["bans"] == []


@pytest.mark.asyncio
async def test_missing_xuehai_id_skips_rule(env) -> None:
    assert await _evaluate(env, xuehai_id="") is None
    assert env["since"] is None


@pytest.mark.asyncio
async def test_disabled_rule_skips(env) -> None:
    env["rule"] = _rule(enabled=False)
    assert await _evaluate(env) is None
    assert env["bans"] == []


@pytest.mark.asyncio
async def test_hit_writes_audit_log(env) -> None:
    await _evaluate(env)

    assert len(env["logs"].rows) == 1
    row = env["logs"].rows[0]
    assert row["type"] == "ban_rule"
    assert row["level"] == "warning"
    assert row["user"] == USER
    assert row["xuehai_id"] == XUEHAI
    assert row["data"].startswith("自动封禁 张三：")
    assert "到期" in row["data"]


# ---------------------------------------------------------------------------
# services/ban_rules.enforce_login
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enforce_login_raises_403_with_reason_header(env) -> None:
    with pytest.raises(HTTPException) as failure:
        await ban_rules.enforce_login(
            USER, ["student"], "张三", XUEHAI, "http://test/api/users/auth", now=NOW
        )

    assert failure.value.status_code == 403
    assert failure.value.detail == "USER_BANNED"
    assert unquote(failure.value.headers["X-Ban-Reason"]).startswith("在 24 小时内")
    assert failure.value.headers["X-Ban-Until"] == str(
        int((NOW + timedelta(days=1)).timestamp())
    )


@pytest.mark.asyncio
async def test_enforce_login_is_fail_open_on_errors(env) -> None:
    """规则表读不到 / 坏数据等异常只能放行，绝不阻断登录。"""
    env["rule_error"] = RuntimeError("db down")
    assert (
        await ban_rules.enforce_login(
            USER, ["student"], "张三", XUEHAI, "http://test/api/users/auth", now=NOW
        )
        is None
    )


@pytest.mark.asyncio
async def test_enforce_login_passes_when_below_threshold(env) -> None:
    env["devices"] = 1
    assert (
        await ban_rules.enforce_login(
            USER, ["student"], "张三", XUEHAI, "http://test/api/users/auth", now=NOW
        )
        is None
    )
