"""``/api/users/auth`` 三步闸 + ``/api/users/auth/device`` 的离线契约。

覆盖设计 §5 的状态机：

1. 设备闸（密码校验前）→ 403 ``DEVICE_BANNED`` + ``X-Ban-*`` 头，**不封账号**；
2. 凭据校验（打桩，不碰 RSA/bcrypt）；
3. 主设备闸 → 403 ``DEVICE_NOT_PRIMARY`` + ``challenge``，不发 token、不封禁；
4. 临时密码登入 → 1 小时 token + ``tp`` claim + ``temp_session``；
5. 临时会话的日志级别下限（contextvar）在请求内生效、请求间不残留。

全部离线（仓储打桩），不连 Oracle。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import users_router
from services import device_guard
from typings.log import ZVMSLog
from util import cert
from util.object_id import get_current_user, log_level_floor

PREFIX = "/api/users"
USER = "000000000000000000000002"
XUEHAI = "20260001"
OTHER = "20269999"


class Env:
    def __init__(self) -> None:
        self.user = {
            "_id": USER,
            "id": "2026002",
            "name": "张三",
            "roles": ["student"],
            "class_id": None,
            "session_id": "",
            "primary_xuehai_id": None,
            "banned": False,
        }
        self.device_bans: dict[str, dict] = {}
        self.banned_users: list[dict] = []
        self.users: dict[str, dict] = {USER: self.user}
        self.challenges: dict[str, dict] = {}
        self.passwords: dict[str, dict] = {}
        self.bans: list[dict] = []
        self.records: list[dict] = []
        self.device_upserts: list[dict] = []
        self.logs: list[dict] = []
        self.marks: list[str] = []
        self.consumed: list[str] = []
        self.other_primary: set[str] = set()
        self.admin_primary: set[str] = set()
        self.counters: dict[str, int] = {}


@pytest.fixture
def env(monkeypatch) -> Env:
    state = Env()
    counter = {"n": 0}

    async def get_device_ban(xuehai_id):
        return state.device_bans.get(xuehai_id)

    async def upsert(xuehai_id, *, reason, until, source, rule_code, operator_oid):
        state.device_upserts.append({"xuehai_id": xuehai_id, "until": until})
        state.device_bans[xuehai_id] = {"until": until, "reason": reason}

    async def list_banned_by_xuehai(xuehai_id):
        return list(state.banned_users)

    async def primary_owned_by_other(xuehai_id, user_oid):
        return xuehai_id in state.other_primary

    async def is_admin_primary_device(xuehai_id):
        return xuehai_id in state.admin_primary

    async def get_by_oid(oid):
        return state.users.get(oid)

    async def counter_increment(xuehai_id, at=None):
        state.counters[xuehai_id] = state.counters.get(xuehai_id, 0) + 1
        return state.counters[xuehai_id]

    async def find_by_business_id(business_id):
        return next((u for u in state.users.values() if u["id"] == business_id), None)

    async def set_ban(oid, *, reason, until, source, rule_code):
        state.bans.append({"oid": oid, "until": until, "rule_code": rule_code})

    async def get_active_for_user(user_oid):
        return []

    async def insert_challenge(token_hash, user_oid, xuehai_id, expires_at):
        counter["n"] += 1
        oid = f"00000000000000000000c{counter['n']:03d}"
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
        state.consumed.append(oid)
        next(r for r in state.challenges.values() if r["_id"] == oid)[
            "consumed_at"
        ] = datetime.now()

    async def list_active_for_user(user_oid, limit=3):
        return list(state.passwords.values())

    async def mark_used(oid):
        state.marks.append(oid)
        if state.passwords[oid]["used_at"] is not None:
            return 0
        state.passwords[oid]["used_at"] = datetime.now()
        return 1

    async def count_for_user_rule(user_oid, rule_code):
        return 0

    async def insert_record(user_oid, **kwargs):
        state.records.append({"user_oid": user_oid, **kwargs})
        return "rec"

    async def insert_log(
        url, user, data, timestamp, ltype, level, xuehai_id, client_ip="", user_agent=""
    ):
        state.logs.append(
            {"type": ltype, "level": level, "user": user, "client_ip": client_ip}
        )

    async def no_rule(code):
        # 只认「非主学海设备登录」规则；多设备规则返回 None，避免真去连库。
        if code != device_guard.RULE_CODE_FOREIGN_DEVICE:
            return None
        return {
            "code": code,
            "enabled": True,
            "config": json.dumps({"max_attempts": 3}),
            "penalties": json.dumps([1, 3, 7, 14, 30, None]),
        }

    monkeypatch.setattr(
        backends,
        "get_device_bans_repo",
        lambda *a, **k: SimpleNamespace(get=get_device_ban, upsert=upsert),
    )
    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda *a, **k: SimpleNamespace(
            get_by_oid=get_by_oid,
            find_by_business_id=find_by_business_id,
            set_ban=set_ban,
            list_banned_by_xuehai=list_banned_by_xuehai,
            primary_owned_by_other=primary_owned_by_other,
            is_admin_primary_device=is_admin_primary_device,
        ),
    )
    monkeypatch.setattr(
        backends,
        "get_bindings_repo",
        lambda *a, **k: SimpleNamespace(get_active_for_user=get_active_for_user),
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
        lambda *a, **k: SimpleNamespace(increment=counter_increment),
    )
    monkeypatch.setattr(
        backends,
        "get_ban_records_repo",
        lambda *a, **k: SimpleNamespace(
            count_for_user_rule=count_for_user_rule, insert=insert_record
        ),
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

    # 规则封禁（多设备）在本文件不测：直接返回 None，避免真去连库。
    monkeypatch.setattr(
        backends,
        "get_ban_rules_repo",
        lambda *a, **k: SimpleNamespace(get_by_code=no_rule, get_by_trigger=no_rule),
    )
    monkeypatch.setattr(
        backends,
        "get_applogs_repo",
        lambda *a, **k: SimpleNamespace(insert=insert_log),
    )
    # 凭据校验会解密 RSA 密文并查库：本文件只关心三道闸的编排。
    async def fake_validate(id, credential, type="long"):
        return "signed-token", False

    monkeypatch.setattr(users_router, "validate_by_cert", fake_validate)
    return state


@pytest.fixture
def client(env):
    app = FastAPI()
    app.include_router(users_router.router, prefix=PREFIX)
    return TestClient(app)


def _add_password(env, code="12345678") -> str:
    oid = "0000000000000000000000d1"
    env.passwords[oid] = {
        "_id": oid,
        "user_oid": USER,
        "code_hash": device_guard.hash_temp_password(code),
        "expires_at": datetime.now() + timedelta(minutes=30),
        "used_at": None,
        "revoked_at": None,
    }
    return oid


def _login(client, xuehai_id: str = ""):
    headers = {"Xuehai-Id": xuehai_id} if xuehai_id else {}
    return client.post(
        f"{PREFIX}/auth",
        json={"id": "2026002", "credential": "whatever", "purpose": "login"},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# 主设备闸
# ---------------------------------------------------------------------------


def test_unbound_account_is_not_blocked(client, env) -> None:
    response = _login(client, OTHER)

    assert response.status_code == 200
    assert response.json()["token"] == "signed-token"
    assert response.json()["binding_mismatch"] is False


def test_same_primary_device_is_allowed(client, env) -> None:
    env.user["primary_xuehai_id"] = XUEHAI

    assert _login(client, XUEHAI).status_code == 200


def test_foreign_device_gets_challenge_and_no_token(client, env) -> None:
    env.user["primary_xuehai_id"] = XUEHAI

    response = _login(client, OTHER)

    assert response.status_code == 403
    detail = response.json()["detail"]
    assert detail["code"] == "DEVICE_NOT_PRIMARY"
    assert detail["challenge"]
    assert "token" not in response.json()

    # challenge 落库，明文只在响应里出现一次
    assert detail["challenge"] not in env.challenges
    assert hashlib.sha256(detail["challenge"].encode()).hexdigest() in env.challenges

    # 不封账号、不封设备，但留一条 warning 级的 device_mismatch
    assert env.bans == []
    assert env.device_upserts == []
    mismatch = [row for row in env.logs if row["type"] == "device_mismatch"]
    assert len(mismatch) == 1
    assert mismatch[0]["level"] == "warning"
    # 被拦下的登录不能再写一条成功的 user_login
    assert [row for row in env.logs if row["type"] == "user_login"] == []


def test_missing_device_header_blocks_student(client, env) -> None:
    """老客户端不带 Xuehai-Id：学生是在非学海设备上，必须拒绝。"""
    env.user["primary_xuehai_id"] = XUEHAI

    response = _login(client)

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "DEVICE_REQUIRED"


_MISSING = object()


def _set_roles(env, roles) -> None:
    if roles is _MISSING:
        env.user.pop("roles", None)
    else:
        env.user["roles"] = roles


def test_student_without_device_is_rejected_without_challenge(client, env) -> None:
    response = _login(client)

    assert response.status_code == 403
    detail = response.json()
    assert detail["detail"] == {
        "code": "DEVICE_REQUIRED",
        "message": device_guard.DEVICE_REQUIRED_MESSAGE,
    }
    assert "token" not in detail
    assert "challenge" not in detail["detail"]

    # 不发 challenge、不封禁、不写成功登录日志，但留一条同类 warning 审计
    assert env.challenges == {}
    assert env.bans == []
    assert env.device_upserts == []
    assert [row for row in env.logs if row["type"] == "user_login"] == []
    required = [row for row in env.logs if row["type"] == "device_required"]
    assert len(required) == 1
    assert required[0]["level"] == "warning"


def test_unbound_student_without_device_is_rejected(client, env) -> None:
    env.user["primary_xuehai_id"] = None

    response = _login(client)

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "DEVICE_REQUIRED"


@pytest.mark.parametrize("roles", [[], None, _MISSING])
def test_missing_or_empty_roles_is_treated_as_student(client, env, roles) -> None:
    _set_roles(env, roles)

    response = _login(client)

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "DEVICE_REQUIRED"


@pytest.mark.parametrize("roles", [["secretary"], ["department"], ["admin"]])
def test_privileged_roles_login_without_device_header(client, env, roles) -> None:
    _set_roles(env, roles)

    response = _login(client)

    assert response.status_code == 200
    assert response.json()["token"] == "signed-token"


@pytest.mark.parametrize("roles", ["secretary", {"admin": True}, 123])
def test_bad_role_type_is_treated_as_student(client, env, roles) -> None:
    _set_roles(env, roles)

    response = _login(client)

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "DEVICE_REQUIRED"


@pytest.mark.parametrize("roles", [["secretary"], ["department"], ["admin"]])
def test_privileged_roles_bypass_primary_device_gate(client, env, roles) -> None:
    """团支书及以上换了设备直接放行，不再走临时密码。"""
    _set_roles(env, roles)
    env.user["primary_xuehai_id"] = XUEHAI

    response = _login(client, OTHER)

    assert response.status_code == 200
    assert response.json()["token"] == "signed-token"
    assert env.challenges == {}


def test_multi_role_hits_exemption(client, env) -> None:
    _set_roles(env, ["student", "secretary"])

    assert _login(client).status_code == 200


# ---------------------------------------------------------------------------
# 设备闸（在密码校验之前）
# ---------------------------------------------------------------------------


def test_banned_device_is_rejected_with_ban_headers(client, env, monkeypatch) -> None:
    env.device_bans[OTHER] = {"until": None, "reason": "冒用身份"}

    response = _login(client, OTHER)

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "DEVICE_BANNED"
    assert response.headers["X-Ban-Until"] == ""
    assert response.headers["X-Ban-Reason"]

    # 关键：设备被封不代表这个账号被封
    assert env.bans == []
    assert not [row for row in env.logs if row["type"] == "user_login"]


def test_device_gate_runs_before_credential_check(client, env, monkeypatch) -> None:
    """设备封禁必须先于密码校验：调用顺序是可观察的契约。"""
    calls: list[str] = []
    env.device_bans[OTHER] = {"until": None, "reason": "冒用身份"}

    async def fake_validate(id, credential, type="long"):
        calls.append("credential")
        return "signed-token", False

    monkeypatch.setattr(users_router, "validate_by_cert", fake_validate)

    assert _login(client, OTHER).status_code == 403
    assert calls == []


def test_account_ban_on_primary_device_blocks_device(client, env) -> None:
    """账号在主设备上被封 → 该设备也被拦（实时推导，无同步任务）。"""
    env.banned_users = [
        {
            "banned": True,
            "ban_reason": "多设备登录",
            "ban_until": datetime.now() + timedelta(days=1),
        }
    ]

    response = _login(client, XUEHAI)

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "DEVICE_BANNED"


# ---------------------------------------------------------------------------
# 临时密码登入
# ---------------------------------------------------------------------------


def _device_login(client, challenge: str, code: str, xuehai_id: str = OTHER):
    headers = {"Xuehai-Id": xuehai_id} if xuehai_id else {}
    return client.post(
        f"{PREFIX}/auth/device",
        json={"challenge": challenge, "temp_password": code},
        headers=headers,
    )


def _issued_challenge(env, client) -> str:
    """走一次异设备登录拿到 challenge（真实链路，不打桩内部函数）。"""
    env.user["primary_xuehai_id"] = XUEHAI
    response = _login(client, OTHER)
    assert response.status_code == 403
    return response.json()["detail"]["challenge"]


def test_temp_password_login_issues_one_hour_session(client, env) -> None:
    _add_password(env)
    challenge = _issued_challenge(env, client)

    response = _device_login(client, challenge, "12345678")

    assert response.status_code == 200
    body = response.json()
    assert body["temp_session"] is True
    assert body["weak_password"] is False
    # 正在异设备上登入，前端要提示「这不是你的主设备」
    assert body["binding_mismatch"] is True
    assert body["profile"]["id"] == "2026002"

    payload = cert.jwt_decode(body["token"])
    assert payload["sub"] == USER
    assert payload["scope"] == "access_token"
    assert payload["tp"] is True
    assert abs(payload["exp"] - payload["iat"] - 3600) <= 1

    assert env.marks == ["0000000000000000000000d1"]
    login_logs = [row for row in env.logs if row["type"] == "temp_password_login"]
    assert len(login_logs) == 1
    assert login_logs[0]["level"] == "warning"


def test_temp_password_is_single_use(client, env) -> None:
    _add_password(env)
    challenge = _issued_challenge(env, client)

    assert _device_login(client, challenge, "12345678").status_code == 200

    again = _device_login(client, challenge, "12345678")
    assert again.status_code == 403
    assert again.json()["detail"]["code"] == "TEMP_PASSWORD_INVALID"


def test_wrong_temp_password_reports_remaining(client, env) -> None:
    _add_password(env)
    challenge = _issued_challenge(env, client)

    response = _device_login(client, challenge, "00000000")

    assert response.status_code == 403
    assert response.json()["detail"]["remaining"] == device_guard.MAX_TEMP_ATTEMPTS - 1
    assert env.bans == []


def test_third_wrong_temp_password_bans_device(client, env) -> None:
    _add_password(env)
    challenge = _issued_challenge(env, client)

    for _ in range(device_guard.MAX_TEMP_ATTEMPTS - 1):
        assert _device_login(client, challenge, "00000000").status_code == 403

    final = _device_login(client, challenge, "00000000")

    assert final.status_code == 403
    assert final.json()["detail"]["code"] == "DEVICE_BAN_APPLIED"
    # 设备梯度规则缺失 → 默认第 1 档 1 天；账号不连坐。
    assert [row["xuehai_id"] for row in env.device_upserts] == [OTHER]
    assert env.device_upserts[0]["until"] is not None
    assert env.bans == []


def test_temp_login_refuses_banned_account(client, env) -> None:
    """连错 3 次后账号被封：即便有人还拿着有效临时密码也进不来。"""
    _add_password(env)
    challenge = _issued_challenge(env, client)
    env.user["banned"] = True
    env.user["ban_until"] = None

    response = _device_login(client, challenge, "12345678")

    assert response.status_code == 403
    assert response.json()["detail"] == "USER_BANNED"


def test_challenge_cannot_be_redeemed_from_another_device(client, env) -> None:
    """挑战是「这台设备」的单次许可：换台机器兑换 403，且不消耗 attempts。"""
    _add_password(env)
    challenge = _issued_challenge(env, client)

    response = _device_login(client, challenge, "12345678", xuehai_id=XUEHAI)

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "code": "DEVICE_CHALLENGE_MISMATCH",
        "message": device_guard.CHALLENGE_MISMATCH_MESSAGE,
    }
    assert "token" not in response.json()
    assert env.marks == []
    row = next(iter(env.challenges.values()))
    assert row["attempts"] == 0
    assert row["consumed_at"] is None

    # 挑战仍可在签发它的设备上使用
    assert _device_login(client, challenge, "12345678").status_code == 200


def test_banned_device_cannot_redeem_temp_password(client, env) -> None:
    """challenge 签发之后设备被封：兑换必须 403，不发 token、不消耗密码。"""
    _add_password(env)
    challenge = _issued_challenge(env, client)
    env.device_bans[OTHER] = {"until": None, "reason": "冒用身份"}

    response = _device_login(client, challenge, "12345678")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "DEVICE_BANNED"
    assert response.headers["X-Ban-Reason"]
    assert env.marks == []
    row = next(iter(env.challenges.values()))
    assert row["attempts"] == 0
    assert row["consumed_at"] is None


def test_banned_challenge_device_is_checked_without_header(client, env) -> None:
    """老客户端不带 Xuehai-Id：设备闸退回挑战里绑定的设备号。"""
    _add_password(env)
    challenge = _issued_challenge(env, client)
    env.device_bans[OTHER] = {"until": None, "reason": "冒用身份"}

    response = _device_login(client, challenge, "12345678", xuehai_id="")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "DEVICE_BANNED"
    assert env.marks == []


# ---------------------------------------------------------------------------
# 临时会话的日志级别下限
# ---------------------------------------------------------------------------


def _token(tp: bool) -> str:
    return cert.jwt_encode(
        USER, ["student"], ["student"], "long",
        session_id="", purpose="login", tp=tp,
    )


@pytest.fixture
def probe_client(env):
    """最小 app：读 contextvar 并写一条 info 日志。"""
    app = FastAPI()

    @app.get("/probe")
    async def probe(user=Depends(get_current_user)):
        await ZVMSLog("/probe", USER, "普通操作", type="probe", level="info").insert_log()
        return {"floor": log_level_floor.get(), "temp_session": user["temp_session"]}

    return TestClient(app)


def test_temp_session_raises_log_floor(probe_client, env) -> None:
    body = probe_client.get(
        "/probe", headers={"Authorization": f"Bearer {_token(tp=True)}"}
    ).json()

    assert body == {"floor": "warning", "temp_session": True}
    assert env.logs[-1]["level"] == "warning"


def test_normal_session_keeps_info_level(probe_client, env) -> None:
    body = probe_client.get(
        "/probe", headers={"Authorization": f"Bearer {_token(tp=False)}"}
    ).json()

    assert body == {"floor": None, "temp_session": False}
    assert env.logs[-1]["level"] == "info"
