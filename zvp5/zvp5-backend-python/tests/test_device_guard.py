"""``services/device_guard.py`` + 梯度处罚的离线契约。

钉住四件写错就会静默出事的行为：

* 设备封禁的两个来源（显式 ``BANNED_DEVICES`` / 账号联动推导）与
  ``ban_until`` 过期脏行的排除——只看 ``banned`` 列会让刑满设备永久被封；
* 梯度 ``[1,3,7,14,30,null]``：前五档递增，第六档 ``until is None``（永久）；
* 临时密码单次使用 + 第 3 次失败才同时封账号与设备；
* challenge 明文只出现一次，库里存的是 SHA-256。

全部离线（backends 全打桩），不连 Oracle。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from db import backends
from services import ban_rules, device_guard

NOW = datetime(2026, 10, 21, 12, 0, 0)
USER = "000000000000000000000002"
XUEHAI = "20260001"
OTHER_DEVICE = "20269999"
PENALTIES = [1, 3, 7, 14, 30, None]


def _rule(penalties=PENALTIES):
    return {
        "code": device_guard.RULE_CODE_FOREIGN_DEVICE,
        "name": "非主学海设备登录",
        "enabled": True,
        "builtin": True,
        "config": json.dumps({"max_attempts": 3}),
        "penalties": json.dumps(penalties),
    }


class Env:
    """一台打桩的「数据库」：三个新仓 + 用户/规则/流水/日志。"""

    def __init__(self) -> None:
        self.device_bans: dict[str, dict] = {}
        self.banned_users: list[dict] = []
        self.challenges: dict[str, dict] = {}
        self.passwords: dict[str, dict] = {}
        self.rule = _rule()
        self.rule_error: Exception | None = None
        self.other_primary: set[str] = set()
        self.admin_primary: set[str] = set()
        self.history = 0
        # 当前 USERS 行上的封禁状态：``apply_account_ban`` 会先读它避免降级。
        self.current_ban: dict | None = None
        self.bans: list[dict] = []
        self.records: list[dict] = []
        self.counters: dict[str, int] = {}
        self.device_upserts: list[dict] = []
        self.scan_limits: list[int] = []
        self.logs: list[dict] = []


@pytest.fixture
def env(monkeypatch) -> Env:
    state = Env()

    async def get_device_ban(xuehai_id):
        return state.device_bans.get(xuehai_id)

    async def upsert(xuehai_id, *, reason, until, source, rule_code, operator_oid):
        state.device_upserts.append(
            {
                "xuehai_id": xuehai_id,
                "reason": reason,
                "until": until,
                "source": source,
                "rule_code": rule_code,
                "operator_oid": operator_oid,
            }
        )
        state.device_bans[xuehai_id] = {"until": until, "reason": reason}

    async def list_banned_by_xuehai(xuehai_id):
        return list(state.banned_users)

    async def primary_owned_by_other(xuehai_id, user_oid):
        return xuehai_id in state.other_primary

    async def is_admin_primary_device(xuehai_id):
        return xuehai_id in state.admin_primary

    async def set_ban(oid, *, reason, until, source, rule_code):
        state.bans.append(
            {
                "oid": oid,
                "reason": reason,
                "until": until,
                "source": source,
                "rule_code": rule_code,
            }
        )
        state.current_ban = {
            "banned": True,
            "ban_reason": reason,
            "ban_until": until,
        }

    async def get_by_oid(oid):
        return state.current_ban

    async def insert_challenge(token_hash, user_oid, xuehai_id, expires_at):
        oid = f"{0xC1 + len(state.challenges):024x}"
        state.challenges[token_hash] = {
            "_id": oid,
            "token_hash": token_hash,
            "user_oid": user_oid,
            "xuehai_id": xuehai_id,
            "attempts": 0,
            "expires_at": expires_at,
            "consumed_at": None,
        }
        return oid

    async def get(token_hash):
        return state.challenges.get(token_hash)

    async def increment_attempts(oid):
        row = next(r for r in state.challenges.values() if r["_id"] == oid)
        row["attempts"] += 1
        return row["attempts"]

    async def consume(oid):
        next(r for r in state.challenges.values() if r["_id"] == oid)[
            "consumed_at"
        ] = NOW

    async def list_active_for_user(user_oid, limit=3):
        state.scan_limits.append(limit)
        return [row for row in state.passwords.values() if row["user_oid"] == user_oid]

    async def mark_used(oid):
        row = state.passwords[oid]
        if row.get("used_at") is not None:
            return 0
        row["used_at"] = NOW
        return 1

    async def count_for_user_rule(user_oid, rule_code):
        return state.history

    async def counter_get(xuehai_id):
        count = state.counters.get(xuehai_id)
        return None if count is None else {"xuehai_id": xuehai_id, "ban_count": count}

    async def counter_increment(xuehai_id, at=None):
        state.counters[xuehai_id] = state.counters.get(xuehai_id, 0) + 1
        return state.counters[xuehai_id]

    async def counter_get_counts(xuehai_ids):
        return {
            xuehai_id: state.counters[xuehai_id]
            for xuehai_id in xuehai_ids
            if xuehai_id in state.counters
        }

    async def counter_delete(xuehai_id):
        return 1 if state.counters.pop(xuehai_id, None) is not None else 0

    async def insert_record(user_oid, **kwargs):
        state.records.append({"user_oid": user_oid, **kwargs})
        return "rec"

    async def get_rule(code):
        if state.rule_error is not None:
            raise state.rule_error
        if state.rule is None:
            return None
        # 设备梯度规则与 foreign_device_login 共用同一份 penalties 桩。
        if code not in (state.rule["code"], device_guard.RULE_CODE_DEVICE_TEMP_PASSWORD):
            return None
        return state.rule

    async def insert_log(
        url, user, data, timestamp, ltype, level, xuehai_id, client_ip="", user_agent=""
    ):
        state.logs.append(
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
        backends,
        "get_device_bans_repo",
        lambda *a, **k: SimpleNamespace(get=get_device_ban, upsert=upsert),
    )
    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda *a, **k: SimpleNamespace(
            set_ban=set_ban,
            get_by_oid=get_by_oid,
            list_banned_by_xuehai=list_banned_by_xuehai,
            primary_owned_by_other=primary_owned_by_other,
            is_admin_primary_device=is_admin_primary_device,
        ),
    )
    monkeypatch.setattr(
        backends,
        "get_device_challenges_repo",
        lambda *a, **k: SimpleNamespace(
            insert=insert_challenge,
            get=get,
            increment_attempts=increment_attempts,
            consume=consume,
        ),
    )
    monkeypatch.setattr(
        backends,
        "get_temp_passwords_repo",
        lambda *a, **k: SimpleNamespace(
            list_active_for_user=list_active_for_user, mark_used=mark_used
        ),
    )
    monkeypatch.setattr(
        backends,
        "get_device_ban_counters_repo",
        lambda *a, **k: SimpleNamespace(
            get=counter_get,
            increment=counter_increment,
            get_counts=counter_get_counts,
            delete=counter_delete,
        ),
    )
    monkeypatch.setattr(
        backends,
        "get_ban_records_repo",
        lambda *a, **k: SimpleNamespace(
            count_for_user_rule=count_for_user_rule, insert=insert_record
        ),
    )
    monkeypatch.setattr(
        backends,
        "get_ban_rules_repo",
        lambda *a, **k: SimpleNamespace(get_by_code=get_rule, get_by_trigger=get_rule),
    )
    monkeypatch.setattr(
        backends,
        "get_applogs_repo",
        lambda *a, **k: SimpleNamespace(insert=insert_log),
    )

    async def temp_failures_by_device(user_oid, since):
        grouped: dict[str, int] = {}
        for row in state.logs:
            if row["type"] == "temp_password_fail":
                key = row["xuehai_id"]
                grouped[key] = grouped.get(key, 0) + 1
        return sorted(grouped.items(), key=lambda item: (-item[1], item[0]))

    monkeypatch.setattr(
        device_guard, "temp_failures_by_device", temp_failures_by_device
    )
    return state


# ---------------------------------------------------------------------------
# 梯度：rule_penalties / penalty_expiry
# ---------------------------------------------------------------------------


def test_rule_penalties_keeps_trailing_none() -> None:
    assert ban_rules.rule_penalties(_rule()) == PENALTIES


def test_rule_penalties_stops_at_none_in_the_middle() -> None:
    """``null`` 只在末位有意义：后面的档位永远轮不到，读到就截断。"""
    assert ban_rules.rule_penalties(_rule([1, None, 9])) == [1, None]


def test_rule_penalties_falls_back_on_empty() -> None:
    assert ban_rules.rule_penalties(_rule([])) == [1]
    assert ban_rules.rule_penalties(None) == [1]


@pytest.mark.parametrize(
    ("offense", "days", "permanent"),
    [
        (1, 1, False),
        (2, 3, False),
        (3, 7, False),
        (4, 14, False),
        (5, 30, False),
        (6, None, True),
        (99, None, True),
    ],
)
def test_penalty_expiry_walks_the_gradient(offense, days, permanent) -> None:
    until, got_days = ban_rules.penalty_expiry(PENALTIES, offense, NOW)

    assert got_days == days
    if permanent:
        assert until is None
    else:
        assert until == NOW + timedelta(days=days)


# ---------------------------------------------------------------------------
# device_ban_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_xuehai_id_is_never_banned(env) -> None:
    env.device_bans[XUEHAI] = {"until": None, "reason": "违规"}
    assert await device_guard.device_ban_status("", now=NOW) == (False, None, None)


@pytest.mark.asyncio
async def test_explicit_permanent_device_ban(env) -> None:
    env.device_bans[XUEHAI] = {"until": None, "reason": "冒用身份"}
    assert await device_guard.device_ban_status(XUEHAI, now=NOW) == (
        True,
        "冒用身份",
        None,
    )


@pytest.mark.asyncio
async def test_explicit_device_ban_with_future_until(env) -> None:
    until = NOW + timedelta(days=2)
    env.device_bans[XUEHAI] = {"until": until, "reason": "违规"}
    assert await device_guard.device_ban_status(XUEHAI, now=NOW) == (
        True,
        "违规",
        until,
    )


@pytest.mark.asyncio
async def test_expired_explicit_device_ban_is_ignored(env) -> None:
    env.device_bans[XUEHAI] = {"until": NOW - timedelta(seconds=1), "reason": "旧账"}
    assert await device_guard.device_ban_status(XUEHAI, now=NOW) == (False, None, None)


@pytest.mark.asyncio
async def test_account_derived_device_ban(env) -> None:
    """主设备=本机且该用户此刻被封 → 这台机器被拦（原因取账号的原因）。"""
    until = NOW + timedelta(days=1)
    env.banned_users = [
        {"banned": True, "ban_reason": "多设备登录", "ban_until": until}
    ]
    assert await device_guard.device_ban_status(XUEHAI, now=NOW) == (
        True,
        "多设备登录",
        until,
    )


@pytest.mark.asyncio
async def test_account_ban_with_expired_until_is_excluded(env) -> None:
    """``banned`` 列到期不归零是正常存量状态，脏行必须排除。"""
    env.banned_users = [
        {"banned": 1, "ban_reason": "旧账", "ban_until": NOW - timedelta(days=1)}
    ]
    assert await device_guard.device_ban_status(XUEHAI, now=NOW) == (False, None, None)


@pytest.mark.asyncio
async def test_account_permanent_ban_has_no_until(env) -> None:
    env.banned_users = [{"banned": True, "ban_reason": "管理员封禁", "ban_until": None}]
    assert await device_guard.device_ban_status(XUEHAI, now=NOW) == (
        True,
        "管理员封禁",
        None,
    )


# ---------------------------------------------------------------------------
# challenge_device（兑换前的设备绑定校验）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_challenge_device_returns_bound_device(env) -> None:
    token = await _challenge(env)
    assert await device_guard.challenge_device(token, now=NOW) == XUEHAI


@pytest.mark.asyncio
async def test_challenge_device_hides_consumed_or_expired(env) -> None:
    consumed = await _challenge(env, consumed_at=NOW - timedelta(minutes=1))
    assert await device_guard.challenge_device(consumed, now=NOW) is None

    expired = await _challenge(env, expires_at=NOW - timedelta(seconds=1))
    assert await device_guard.challenge_device(expired, now=NOW) is None


@pytest.mark.asyncio
async def test_challenge_device_of_unknown_token_is_none(env) -> None:
    assert await device_guard.challenge_device("never-issued", now=NOW) is None


# ---------------------------------------------------------------------------
# 挑战令牌
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_challenge_stores_only_hash(env) -> None:
    token = await device_guard.issue_challenge(USER, XUEHAI)
    expected = hashlib.sha256(token.encode("utf-8")).hexdigest()

    assert token not in env.challenges
    assert expected in env.challenges
    row = env.challenges[expected]
    assert (row["user_oid"], row["xuehai_id"]) == (USER, XUEHAI)
    assert row["attempts"] == 0
    assert row["consumed_at"] is None
    assert row["expires_at"] > datetime.now()


# ---------------------------------------------------------------------------
# verify_temp_password
# ---------------------------------------------------------------------------


def _add_password(
    env, code="12345678", oid="0000000000000000000000d1", **overrides
) -> str:
    env.passwords[oid] = {
        "_id": oid,
        "user_oid": USER,
        "code_hash": device_guard.hash_temp_password(code),
        "expires_at": NOW + timedelta(minutes=30),
        "used_at": None,
        "revoked_at": None,
        **overrides,
    }
    return oid


async def _challenge(env, expires_at=None, consumed_at=None, xuehai_id=XUEHAI) -> str:
    """签一张挑战并把到期时刻钉到 ``NOW`` 的时间轴上（不依赖真实时钟）。"""
    token = await device_guard.issue_challenge(USER, xuehai_id)
    row = env.challenges[hashlib.sha256(token.encode()).hexdigest()]
    row["expires_at"] = expires_at or NOW + timedelta(
        minutes=device_guard.DEVICE_CHALLENGE_TTL_MINUTES
    )
    row["consumed_at"] = consumed_at
    return token


@pytest.mark.asyncio
async def test_temp_password_success_is_single_use(env) -> None:
    _add_password(env)
    token = await _challenge(env)

    assert await device_guard.verify_temp_password(
        token, "12345678", now=NOW
    ) == (USER, XUEHAI)

    # 挑战已消费 → 同一对凭据第二次必然失败，且不再计数。
    with pytest.raises(HTTPException) as failure:
        await device_guard.verify_temp_password(token, "12345678", now=NOW)
    assert failure.value.status_code == 403
    assert failure.value.detail["code"] == "TEMP_PASSWORD_INVALID"


@pytest.mark.asyncio
async def test_used_temp_password_is_rejected(env) -> None:
    used = _add_password(env, used_at=NOW - timedelta(minutes=1))
    token = await _challenge(env)

    with pytest.raises(HTTPException) as failure:
        await device_guard.verify_temp_password(token, "12345678", now=NOW)
    assert failure.value.detail["remaining"] == device_guard.MAX_TEMP_ATTEMPTS - 1
    assert env.passwords[used]["used_at"] is not None


@pytest.mark.asyncio
async def test_expired_challenge_is_rejected_without_counting(env) -> None:
    _add_password(env)
    token = await _challenge(env, expires_at=NOW - timedelta(seconds=1))

    with pytest.raises(HTTPException) as failure:
        await device_guard.verify_temp_password(token, "12345678", now=NOW)

    assert failure.value.detail == {
        "code": "TEMP_PASSWORD_INVALID",
        "message": device_guard.TEMP_PASSWORD_INVALID_MESSAGE,
        "remaining": 0,
    }
    row = next(iter(env.challenges.values()))
    assert row["attempts"] == 0


@pytest.mark.asyncio
async def test_wrong_code_counts_down_remaining(env) -> None:
    _add_password(env)
    token = await _challenge(env)

    with pytest.raises(HTTPException) as failure:
        await device_guard.verify_temp_password(token, "00000000", now=NOW)

    assert failure.value.detail["code"] == "TEMP_PASSWORD_INVALID"
    assert failure.value.detail["remaining"] == device_guard.MAX_TEMP_ATTEMPTS - 1
    assert env.bans == []
    assert env.device_upserts == []
    assert [row["type"] for row in env.logs] == ["temp_password_fail"]


@pytest.mark.asyncio
async def test_third_failure_bans_device_only(env) -> None:
    _add_password(env)
    token = await _challenge(env)

    for _ in range(device_guard.MAX_TEMP_ATTEMPTS - 1):
        with pytest.raises(HTTPException) as failure:
            await device_guard.verify_temp_password(token, "00000000", now=NOW)
        assert failure.value.detail["code"] == "TEMP_PASSWORD_INVALID"

    with pytest.raises(HTTPException) as failure:
        await device_guard.verify_temp_password(token, "00000000", now=NOW)

    detail = failure.value.detail
    assert detail["code"] == "DEVICE_BAN_APPLIED"
    # 设备梯度第 1 档：1 天，不是永久。
    assert detail["until"] == int((NOW + timedelta(days=1)).timestamp())

    # 账号完全不动：临时密码没校验通过，攻击者本来就没进来。
    assert env.bans == []
    assert env.records == []

    assert env.device_upserts == [
        {
            "xuehai_id": XUEHAI,
            "reason": env.device_upserts[0]["reason"],
            "until": NOW + timedelta(days=1),
            "source": "rule",
            "rule_code": device_guard.RULE_CODE_DEVICE_TEMP_PASSWORD,
            "operator_oid": None,
        }
    ]
    assert env.counters == {XUEHAI: 1}
    assert [row["type"] for row in env.logs] == [
        "temp_password_fail",
        "temp_password_fail",
        "temp_password_fail",
        "device_ban",
    ]
    assert env.logs[-1]["level"] == "warning"
    assert "第 1 次" in env.logs[-1]["data"]
    assert device_guard.RULE_CODE_DEVICE_TEMP_PASSWORD in env.logs[-1]["data"]


@pytest.mark.asyncio
async def test_third_failure_on_permanent_step_writes_permanent_device_ban(env) -> None:
    env.rule = _rule([None])
    _add_password(env)
    token = await _challenge(env)

    for _ in range(device_guard.MAX_TEMP_ATTEMPTS):
        with pytest.raises(HTTPException):
            await device_guard.verify_temp_password(token, "00000000", now=NOW)

    assert env.bans == []
    assert env.device_upserts[0]["until"] is None
    assert env.device_upserts[0]["rule_code"] == device_guard.RULE_CODE_DEVICE_TEMP_PASSWORD


@pytest.mark.asyncio
async def test_success_after_failures_does_not_ban(env) -> None:
    _add_password(env)
    token = await _challenge(env)

    with pytest.raises(HTTPException):
        await device_guard.verify_temp_password(token, "00000000", now=NOW)

    assert await device_guard.verify_temp_password(
        token, "12345678", now=NOW
    ) == (USER, XUEHAI)
    assert env.bans == []


@pytest.mark.asyncio
async def test_attempts_beyond_limit_never_repeat_the_ban(env) -> None:
    """同一张未消费的挑战连打 6 次：只有第 3 次封一次，梯度不被重复推进。

    第 4–6 次若再走 ``apply_account_ban``，梯度会 1→3→7→14→30→永久，
    一个连错 6 次的账号就被打成永久封禁。
    """
    _add_password(env)
    token = await _challenge(env)

    for index in range(1, 7):
        with pytest.raises(HTTPException) as failure:
            await device_guard.verify_temp_password(token, "00000000", now=NOW)
        detail = failure.value.detail
        if index < device_guard.MAX_TEMP_ATTEMPTS:
            assert detail["code"] == "TEMP_PASSWORD_INVALID"
            assert detail["remaining"] == device_guard.MAX_TEMP_ATTEMPTS - index
        elif index == device_guard.MAX_TEMP_ATTEMPTS:
            assert detail["code"] == "DEVICE_BAN_APPLIED"
        else:
            # 超过上限：只拒绝本次登入，不再封禁。
            assert detail["code"] == "TEMP_PASSWORD_INVALID"
            assert detail["remaining"] == 0

    assert env.bans == []
    assert env.records == []
    assert len(env.device_upserts) == 1
    log_types = [row["type"] for row in env.logs]
    assert log_types.count("temp_password_fail") == 6
    assert log_types.count("device_ban") == 1


@pytest.mark.asyncio
async def test_device_ban_skipped_for_someone_elses_primary_device(env) -> None:
    """请求头填他人的主设备号：不写设备封禁，也不封账号。

    设备号是客户端自报的，否则 3 次错密码就能把任意人永久锁在门外；账号本来
    就没被攻破，连坐封号只会制造第二个受害者。
    """
    env.other_primary.add(OTHER_DEVICE)
    _add_password(env)
    token = await _challenge(env, xuehai_id=OTHER_DEVICE)

    for _ in range(device_guard.MAX_TEMP_ATTEMPTS):
        with pytest.raises(HTTPException):
            await device_guard.verify_temp_password(token, "00000000", now=NOW)

    assert env.bans == []
    assert env.device_upserts == []
    assert OTHER_DEVICE not in env.device_bans
    assert [row["type"] for row in env.logs][-1] == "device_ban_skipped"
    assert env.logs[-1]["level"] == "warning"
    assert "device_ban" not in [row["type"] for row in env.logs]


@pytest.mark.asyncio
async def test_disabled_rule_does_not_ban(env) -> None:
    """管理员停用 foreign_device_login → 错误照常计数，但不再封禁。"""
    env.rule["enabled"] = False
    _add_password(env)
    token = await _challenge(env)

    for _ in range(device_guard.MAX_TEMP_ATTEMPTS):
        with pytest.raises(HTTPException) as failure:
            await device_guard.verify_temp_password(token, "00000000", now=NOW)

    assert failure.value.detail["code"] == "TEMP_PASSWORD_INVALID"
    assert failure.value.detail["remaining"] == 0
    assert env.bans == []
    assert env.records == []
    assert env.device_upserts == []
    assert "ban_rule_skipped" in [row["type"] for row in env.logs]


@pytest.mark.asyncio
async def test_missing_rule_does_not_ban(env) -> None:
    env.rule = None
    _add_password(env)
    token = await _challenge(env)

    for _ in range(device_guard.MAX_TEMP_ATTEMPTS):
        with pytest.raises(HTTPException):
            await device_guard.verify_temp_password(token, "00000000", now=NOW)

    assert env.bans == []
    assert env.device_upserts == []


@pytest.mark.asyncio
async def test_rule_config_raises_the_attempt_limit(env) -> None:
    """``config["max_attempts"]`` 生效：第 4 次不封，第 5 次才封。"""
    env.rule = _rule()
    env.rule["config"] = json.dumps({"max_attempts": 5})
    _add_password(env)
    token = await _challenge(env)

    for _ in range(4):
        with pytest.raises(HTTPException) as failure:
            await device_guard.verify_temp_password(token, "00000000", now=NOW)
        assert failure.value.detail["code"] == "TEMP_PASSWORD_INVALID"
    assert env.bans == []

    with pytest.raises(HTTPException) as failure:
        await device_guard.verify_temp_password(token, "00000000", now=NOW)
    assert failure.value.detail["code"] == "DEVICE_BAN_APPLIED"
    assert len(env.device_upserts) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config",
    [
        "not-json",
        json.dumps({}),
        json.dumps({"max_attempts": 0}),
        json.dumps({"max_attempts": "x"}),
    ],
)
async def test_bad_rule_config_falls_back_to_default_limit(env, config) -> None:
    env.rule = _rule()
    env.rule["config"] = config
    _add_password(env)
    token = await _challenge(env)

    for _ in range(device_guard.MAX_TEMP_ATTEMPTS):
        with pytest.raises(HTTPException) as failure:
            await device_guard.verify_temp_password(token, "00000000", now=NOW)

    assert failure.value.detail["code"] == "DEVICE_BAN_APPLIED"
    assert len(env.device_upserts) == 1


@pytest.mark.asyncio
async def test_rule_read_failure_still_bans_with_default_limit(env) -> None:
    """读配置失败是运维问题，不能顺手把封禁关掉。"""
    env.rule_error = RuntimeError("rule table unavailable")
    _add_password(env)
    token = await _challenge(env)

    for _ in range(device_guard.MAX_TEMP_ATTEMPTS):
        with pytest.raises(HTTPException) as failure:
            await device_guard.verify_temp_password(token, "00000000", now=NOW)

    assert failure.value.detail["code"] == "DEVICE_BAN_APPLIED"
    assert len(env.device_upserts) == 1
    assert len(env.device_upserts) == 1


@pytest.mark.asyncio
async def test_every_unexpired_temp_password_is_scanned(env) -> None:
    """扫描窗口是 20 条：第 5 条的有效码也必须被接受（3 条会误判为「密码错」）。"""
    for index in range(4):
        _add_password(
            env, f"0000000{index}", oid=f"0000000000000000000000e{index}"
        )
    _add_password(env, "87654321", oid="0000000000000000000000ef")
    token = await _challenge(env)

    assert await device_guard.verify_temp_password(
        token, "87654321", now=NOW
    ) == (USER, XUEHAI)
    assert env.scan_limits == [device_guard.MAX_TEMP_PASSWORDS_SCANNED]


@pytest.mark.asyncio
async def test_scan_limit_is_twenty(env) -> None:
    _add_password(env)
    token = await _challenge(env)

    await device_guard.verify_temp_password(token, "12345678", now=NOW)

    assert device_guard.MAX_TEMP_PASSWORDS_SCANNED == 20
    assert env.scan_limits == [20]


# ---------------------------------------------------------------------------
# 跨挑战累计失败（漏封 C2）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_failures_accumulate_across_challenges(env) -> None:
    """每张挑战只有 3 次机会：换挑战重置计数必须被 24h 累计挡住。"""
    _add_password(env)

    for _ in range(device_guard.CUMULATIVE_TEMP_FAILURES - 1):
        token = await _challenge(env)
        with pytest.raises(HTTPException) as failure:
            await device_guard.verify_temp_password(token, "00000000", now=NOW)
        assert failure.value.detail["code"] == "TEMP_PASSWORD_INVALID"
    assert env.bans == []
    assert env.device_upserts == []

    token = await _challenge(env)
    with pytest.raises(HTTPException) as failure:
        await device_guard.verify_temp_password(token, "00000000", now=NOW)

    assert failure.value.detail["code"] == "DEVICE_BAN_APPLIED"
    assert len(env.device_upserts) == 1
    assert "24 小时" in env.device_upserts[0]["reason"]


@pytest.mark.asyncio
async def test_cumulative_ban_is_not_repeated(env) -> None:
    _add_password(env)

    for _ in range(device_guard.CUMULATIVE_TEMP_FAILURES):
        token = await _challenge(env)
        with pytest.raises(HTTPException):
            await device_guard.verify_temp_password(token, "00000000", now=NOW)

    assert env.bans == []
    assert len(env.device_upserts) == 1

    token = await _challenge(env)
    with pytest.raises(HTTPException) as failure:
        await device_guard.verify_temp_password(token, "00000000", now=NOW)
    assert failure.value.detail["code"] == "TEMP_PASSWORD_INVALID"
    assert env.bans == []
    assert len(env.device_upserts) == 1


@pytest.mark.asyncio
async def test_correct_password_still_wins_over_past_failures(env) -> None:
    """手里有有效码时，几次手抖不该把正确密码挡在门外。"""
    _add_password(env)

    for _ in range(device_guard.CUMULATIVE_TEMP_FAILURES - 1):
        token = await _challenge(env)
        with pytest.raises(HTTPException):
            await device_guard.verify_temp_password(token, "00000000", now=NOW)

    token = await _challenge(env)
    assert await device_guard.verify_temp_password(
        token, "12345678", now=NOW
    ) == (USER, XUEHAI)
    assert env.bans == []


@pytest.mark.asyncio
async def test_cumulative_ban_respects_disabled_rule(env) -> None:
    env.rule["enabled"] = False
    _add_password(env)

    for _ in range(device_guard.CUMULATIVE_TEMP_FAILURES):
        token = await _challenge(env)
        with pytest.raises(HTTPException) as failure:
            await device_guard.verify_temp_password(token, "00000000", now=NOW)

    assert failure.value.detail["code"] == "TEMP_PASSWORD_INVALID"
    assert env.bans == []
    assert "ban_rule_skipped" in [row["type"] for row in env.logs]


@pytest.mark.asyncio
async def test_cumulative_bans_the_device_with_most_failures(env) -> None:
    """跨阈封的是「穷举的那台机器」：不是当前请求设备，更不是账号主设备。"""
    _add_password(env)
    env.logs.extend(
        {"type": "temp_password_fail", "xuehai_id": OTHER_DEVICE}
        for _ in range(device_guard.CUMULATIVE_TEMP_FAILURES)
    )

    token = await _challenge(env)
    with pytest.raises(HTTPException) as failure:
        await device_guard.verify_temp_password(token, "00000000", now=NOW)

    assert failure.value.detail["code"] == "DEVICE_BAN_APPLIED"
    assert env.device_upserts[0]["xuehai_id"] == OTHER_DEVICE
    assert XUEHAI not in env.device_bans
    assert env.bans == []


@pytest.mark.asyncio
async def test_device_ban_gradient_walks_and_stays_permanent(env) -> None:
    """设备侧梯度：1/3/7 天逐档，末档永久；重复违规不降级。"""
    for expected_days in (1, 3, 7):
        ban = await device_guard._ban_device_for_temp_password(
            USER, XUEHAI, request_url="", now=NOW
        )
        assert ban["days"] == expected_days
        assert ban["until"] == NOW + timedelta(days=expected_days)
        assert env.device_upserts[-1]["until"] == NOW + timedelta(days=expected_days)

    env.device_bans[XUEHAI] = {"until": None, "reason": "已永久"}
    ban = await device_guard._ban_device_for_temp_password(
        USER, XUEHAI, request_url="", now=NOW
    )
    assert ban["until"] is None
    assert ban["permanent"] is True
    assert env.device_upserts[-1]["until"] is None


@pytest.mark.asyncio
async def test_device_ban_never_downgrades_longer_existing_sentence(env) -> None:
    later = NOW + timedelta(days=30)
    env.device_bans[XUEHAI] = {"until": later, "reason": "管理员封禁"}

    ban = await device_guard._ban_device_for_temp_password(
        USER, XUEHAI, request_url="", now=NOW
    )

    assert ban["days"] == 1  # 本次梯度照实回报
    assert ban["until"] == later
    assert env.device_upserts[-1]["until"] == later


# ---------------------------------------------------------------------------
# apply_account_ban 梯度
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("history", "days", "permanent"),
    [(0, 1, False), (1, 3, False), (4, 30, False), (5, None, True), (40, None, True)],
)
async def test_apply_account_ban_follows_gradient(env, history, days, permanent) -> None:
    env.history = history
    result = await device_guard.apply_account_ban(
        USER, device_guard.RULE_CODE_FOREIGN_DEVICE, "连续输错", now=NOW
    )

    assert result["days"] == days
    assert result["permanent"] is permanent
    assert result["offense"] == history + 1
    assert env.bans[0]["until"] == (None if permanent else NOW + timedelta(days=days))
    assert env.records[0]["permanent"] is permanent


@pytest.mark.asyncio
async def test_apply_account_ban_does_not_downgrade_admin_permanent_ban(env) -> None:
    """管理员落的永久封禁（banned=1 + ban_until IS NULL）不能被规则改成 1 天。"""
    env.current_ban = {"banned": True, "ban_reason": "管理员封禁"}
    env.history = 0

    result = await device_guard.apply_account_ban(
        USER, device_guard.RULE_CODE_FOREIGN_DEVICE, "连续输错", now=NOW
    )

    assert env.bans == []  # 没写 USERS
    assert result["until"] is None  # 反映库里真正生效的永久封禁
    assert result["permanent"] is True
    assert len(env.records) == 1  # 流水照落
    assert env.records[0]["permanent"] is True
    assert env.records[0]["expires_at"] is None


@pytest.mark.asyncio
async def test_apply_account_ban_keeps_longer_existing_ban(env) -> None:
    later = NOW + timedelta(days=30)
    env.current_ban = {"banned": True, "ban_until": later, "ban_reason": "管理员封禁"}

    result = await device_guard.apply_account_ban(
        USER, device_guard.RULE_CODE_FOREIGN_DEVICE, "连续输错", now=NOW
    )

    assert env.bans == []
    assert result["until"] == later
    assert result["days"] == 1  # 本次梯度仍照实回报
    assert env.records[0]["expires_at"] == later


@pytest.mark.asyncio
async def test_apply_account_ban_overwrites_shorter_existing_ban(env) -> None:
    env.current_ban = {
        "banned": True,
        "ban_until": NOW + timedelta(hours=1),
        "ban_reason": "旧封禁",
    }

    result = await device_guard.apply_account_ban(
        USER, device_guard.RULE_CODE_FOREIGN_DEVICE, "连续输错", now=NOW
    )

    assert env.bans[0]["until"] == NOW + timedelta(days=1)
    assert result["until"] == NOW + timedelta(days=1)


@pytest.mark.asyncio
async def test_apply_account_ban_overwrites_expired_ban(env) -> None:
    """刑满的 ``banned=1`` 是正常存量状态，新封禁理应覆盖它。"""
    env.current_ban = {"banned": True, "ban_until": NOW - timedelta(days=2)}

    result = await device_guard.apply_account_ban(
        USER, device_guard.RULE_CODE_FOREIGN_DEVICE, "连续输错", now=NOW
    )

    assert result["until"] == NOW + timedelta(days=1)
    assert env.bans[0]["until"] == NOW + timedelta(days=1)


# ---------------------------------------------------------------------------
# 失败次数的读取口径（直接落 APP_LOGS）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_recent_temp_failures_reads_app_logs(monkeypatch) -> None:
    """只查「该用户 + temp_password_fail + 时间窗」三类谓词，且不另建计数表。"""
    from contextlib import asynccontextmanager

    import db.oracle.session as oracle_session

    captured: dict[str, str] = {}

    class FakeSession:
        async def execute(self, stmt):
            captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            return SimpleNamespace(scalar_one=lambda: 7)

    @asynccontextmanager
    async def fake_scope():
        yield FakeSession()

    monkeypatch.setattr(oracle_session, "session_scope", fake_scope)

    assert await device_guard.count_recent_temp_failures(USER, NOW) == 7

    sql = captured["sql"].lower()
    assert "count(*)" in sql
    assert "app_logs.app_user = '000000000000000000000002'" in sql
    assert "app_logs.ltype = 'temp_password_fail'" in sql
    assert "app_logs.ts_raw >=" in sql


@pytest.mark.asyncio
async def test_count_recent_temp_failures_filters_by_device(monkeypatch) -> None:
    """累计口径按 (user, xuehai_id)：同一账号的多台设备不能合并计数。"""
    from contextlib import asynccontextmanager

    import db.oracle.session as oracle_session

    captured: dict[str, str] = {}

    class FakeSession:
        async def execute(self, stmt):
            captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            return SimpleNamespace(scalar_one=lambda: 2)

    @asynccontextmanager
    async def fake_scope():
        yield FakeSession()

    monkeypatch.setattr(oracle_session, "session_scope", fake_scope)

    assert (
        await device_guard.count_recent_temp_failures(USER, NOW, OTHER_DEVICE) == 2
    )
    assert "app_logs.xuehai_id = '20269999'" in captured["sql"].lower()


@pytest.mark.asyncio
async def test_temp_failures_by_device_groups_in_sql(monkeypatch) -> None:
    from contextlib import asynccontextmanager

    import db.oracle.session as oracle_session

    captured: dict[str, str] = {}

    class FakeSession:
        async def execute(self, stmt):
            captured["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True}))

            class Row:
                _mapping = {"xuehai_id": XUEHAI, "failures": 3}

            return SimpleNamespace(all=lambda: [Row()])

    @asynccontextmanager
    async def fake_scope():
        yield FakeSession()

    monkeypatch.setattr(oracle_session, "session_scope", fake_scope)

    assert await device_guard.temp_failures_by_device(USER, NOW) == [(XUEHAI, 3)]
    sql = captured["sql"].lower()
    assert "app_logs.xuehai_id" in sql
    assert "group by" in sql
    assert "order by" in sql


# ---------------------------------------------------------------------------
# P0-2 时区口径：aware 的 until/now 落库前折成本地 naive 墙钟
# ---------------------------------------------------------------------------


@pytest.fixture
def shanghai_tz():
    """把进程时区钉到 Asia/Shanghai（时区回归必须可复现，不能看机器设置）。"""
    import os
    import time as _time

    previous = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Shanghai"
    _time.tzset()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = previous
        _time.tzset()


def test_to_local_naive_converts_utc_offset_to_local_wall_clock(shanghai_tz) -> None:
    """``2026-10-28T14:01:17+00:00`` 在东八区是 22:01:17，不是 14:01:17。"""
    from datetime import timezone

    aware = datetime(2026, 10, 28, 14, 1, 17, tzinfo=timezone.utc)
    converted = device_guard._to_local_naive(aware)

    assert converted == datetime(2026, 10, 28, 22, 1, 17)
    assert converted.tzinfo is None


def test_to_local_naive_keeps_same_wall_clock_for_offset_input(shanghai_tz) -> None:
    from datetime import timedelta, timezone

    aware = datetime(2026, 10, 28, 22, 1, 17, tzinfo=timezone(timedelta(hours=8)))

    assert device_guard._to_local_naive(aware) == datetime(2026, 10, 28, 22, 1, 17)


def test_to_local_naive_passes_naive_and_none_through() -> None:
    naive = datetime(2026, 10, 28, 14, 1, 17)

    assert device_guard._to_local_naive(naive) is naive
    assert device_guard._to_local_naive(None) is None


@pytest.mark.asyncio
async def test_apply_account_ban_normalizes_aware_now(env) -> None:
    """调用方传 aware 的「此刻」时，落库的 until 仍是本地 naive。"""
    from datetime import timezone

    aware = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)

    result = await device_guard.apply_account_ban(
        USER, device_guard.RULE_CODE_FOREIGN_DEVICE, "连续输错", now=aware
    )

    expected = device_guard._to_local_naive(aware) + timedelta(days=1)
    assert result["until"] == expected
    assert result["until"].tzinfo is None
    assert env.bans[0]["until"] == expected


# ---------------------------------------------------------------------------
# P1-2/P1-3 封禁事务：锁目标行 + 档位/状态/流水同一次提交
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ban_unit_locks_row_and_commits_once(monkeypatch) -> None:
    """真实仓储的 ``ban_unit``：先 ``FOR UPDATE`` 锁行，退出时只 commit 一次。"""
    from contextlib import asynccontextmanager

    import db.repos.oracle.users as users_repo_mod

    statements: list[str] = []
    commits: list[int] = []

    class FakeSession:
        async def execute(self, stmt):
            statements.append(str(stmt.compile(compile_kwargs={"literal_binds": True})))
            return SimpleNamespace(first=lambda: None)

        async def commit(self):
            commits.append(1)

    @asynccontextmanager
    async def fake_scope():
        yield FakeSession()

    monkeypatch.setattr(users_repo_mod, "session_scope", fake_scope)

    repo = users_repo_mod.OracleUsersRepo()
    async with repo.ban_unit(USER) as session:
        assert session is not None

    assert statements and "FOR UPDATE" in statements[0].upper()
    # Oracle 里 FOR UPDATE 与行限制子句不能同现（ORA-02014）。
    assert "FETCH FIRST" not in statements[0].upper()
    assert len(commits) == 1


@pytest.mark.asyncio
async def test_apply_account_ban_counts_and_writes_in_one_transaction(
    env, monkeypatch
) -> None:
    """档位计数与两条写共用 ``ban_unit`` 的 session，只提交一次。"""
    from contextlib import asynccontextmanager

    session = object()
    events: list[tuple] = []
    commits: list[int] = []

    @asynccontextmanager
    async def ban_unit(oid):
        events.append(("lock", oid))
        yield session
        commits.append(1)

    async def set_ban_if_stronger(oid, *, reason, until, source, rule_code):
        events.append(("ban", until, source, rule_code))
        return until

    async def count_for_user_rule(user_oid, rule_code):
        events.append(("count", user_oid, rule_code))
        return 2

    async def insert(user_oid, **kwargs):
        events.append(("record", kwargs["expires_at"]))
        return "rec"

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda bound=None: (
            SimpleNamespace(set_ban_if_stronger=set_ban_if_stronger)
            if bound is session
            else SimpleNamespace(ban_unit=ban_unit)
        ),
    )
    monkeypatch.setattr(
        backends,
        "get_ban_records_repo",
        lambda bound=None: SimpleNamespace(
            count_for_user_rule=count_for_user_rule, insert=insert
        ),
    )

    result = await device_guard.apply_account_ban(
        USER, device_guard.RULE_CODE_FOREIGN_DEVICE, "连续输错", now=NOW
    )

    # history=2 → 第 3 次，梯度 [1,3,7,...] 取第 3 档 7 天。
    assert result["offense"] == 3
    assert result["until"] == NOW + timedelta(days=7)
    assert events[0] == ("lock", USER)
    assert ("count", USER, device_guard.RULE_CODE_FOREIGN_DEVICE) in events
    assert ("record", NOW + timedelta(days=7)) in events
    assert sum(1 for event in events if event[0] == "lock") == 1
    # 两次写（users + 流水）落在同一个事务里，退出时一次提交。
    assert commits == [1]
    # 状态写入走会话内仓储（不是另开事务的 set_ban）。
    assert [event[0] for event in events].count("ban") == 1


@pytest.mark.asyncio
async def test_apply_admin_ban_uses_one_transaction(monkeypatch) -> None:
    from contextlib import asynccontextmanager

    session = object()
    events: list[tuple] = []
    commits: list[int] = []

    @asynccontextmanager
    async def ban_unit(oid):
        events.append(("lock", oid))
        yield session
        commits.append(1)

    async def set_ban(oid, *, reason, until, source, rule_code):
        events.append(("set_ban", reason, until, source, rule_code))

    async def insert(user_oid, **kwargs):
        events.append(("record", kwargs["source"], kwargs["permanent"], kwargs["started_at"]))
        return "rec"

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda bound=None: (
            SimpleNamespace(set_ban=set_ban)
            if bound is session
            else SimpleNamespace(ban_unit=ban_unit)
        ),
    )
    monkeypatch.setattr(
        backends,
        "get_ban_records_repo",
        lambda bound=None: SimpleNamespace(insert=insert),
    )

    await device_guard.apply_admin_ban(
        USER, reason="多次刷分", operator_oid="000000000000000000000001", at=NOW
    )

    assert events[0] == ("lock", USER)
    assert ("set_ban", "多次刷分", None, "admin", None) in events
    assert ("record", "admin", True, NOW) in events
    assert commits == [1]


@pytest.mark.asyncio
async def test_apply_admin_ban_falls_back_without_ban_unit(monkeypatch) -> None:
    """测试替身/旧适配器没有 ``ban_unit``：退回两次独立写，语义不变。"""
    events: list[tuple] = []

    async def set_ban(oid, *, reason, until, source, rule_code):
        events.append(("set_ban", oid, reason, until, source, rule_code))

    async def insert(user_oid, **kwargs):
        events.append(("record", user_oid, kwargs["started_at"]))
        return "rec"

    monkeypatch.setattr(
        backends, "get_users_repo", lambda *a, **k: SimpleNamespace(set_ban=set_ban)
    )
    monkeypatch.setattr(
        backends,
        "get_ban_records_repo",
        lambda *a, **k: SimpleNamespace(insert=insert),
    )

    await device_guard.apply_admin_ban(USER, reason="多次刷分", at=NOW)

    assert events == [
        ("set_ban", USER, "多次刷分", None, "admin", None),
        ("record", USER, NOW),
    ]


# ---------------------------------------------------------------------------
# P1-6 设备梯度计数：原子 upsert（并发首罚不丢计数）
# ---------------------------------------------------------------------------


def test_counter_increment_is_a_single_merge_statement() -> None:
    from db.repos.oracle import device_ban_counters as counters_repo

    sql = " ".join(str(counters_repo._merge_increment_stmt()).split())

    assert sql.startswith("MERGE INTO device_ban_counters")
    assert "WHEN MATCHED THEN UPDATE SET target.ban_count = target.ban_count + 1" in sql
    assert "WHEN NOT MATCHED THEN INSERT" in sql


@pytest.mark.asyncio
async def test_counter_increment_recovers_from_unique_conflict() -> None:
    """并发首罚撞主键：回读 + 累加，返回递增后的值而不是吞成 1。"""
    from types import SimpleNamespace as NS

    from sqlalchemy.exc import IntegrityError

    from db.repos.oracle.device_ban_counters import OracleDeviceBanCountersRepo

    executed: list[str] = []

    class FakeSession:
        async def execute(self, stmt, params=None):
            executed.append(str(stmt))
            if len(executed) == 1:
                raise IntegrityError("MERGE", {}, Exception("ORA-00001"))
            if len(executed) == 2:
                return NS(rowcount=1)

            class Row:
                _mapping = {
                    "xuehai_id": XUEHAI,
                    "ban_count": 2,
                    "last_banned_at": NOW,
                }

            return NS(first=lambda: Row())

    session = FakeSession()
    repo = OracleDeviceBanCountersRepo(session=session)

    assert await repo.increment(XUEHAI, NOW) == 2
    assert len(executed) == 3
    assert executed[1].upper().startswith("UPDATE DEVICE_BAN_COUNTERS")
