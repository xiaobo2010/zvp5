"""``POST /api/users/auth`` 的 ``purpose`` 契约离线测试。

契约（用户拍板，严格照做）：

1. ``purpose: str = "login"`` 为可选字段，省略时走登录语义（旧调用方不受影响）。
2. 服务端钳制：``purpose == "verify"`` 时**绝不签发会话令牌**——不论 ``mode``
   传什么（哪怕 ``long``），都只能拿到 ``scope == "temporary_token"`` / 5 分钟的
   临时令牌。
3. fail-safe：只有 ``purpose`` **严格等于** ``"login"`` 才走登录语义；其他任何
   值（拼错 ``"verifiy"`` / 空串 / 未知值）一律按 verify 处理。
4. verify 也接受 TOTP 动态码，成功后仍只发临时令牌，登录路径行为一字不变。
5. 成功审计分流：verify 写 ``type="user_verify"``（文案含「身份验证」+ 凭据类型
   密码/动态码），login 仍写 ``type="user_login"``。
6. 失败分流：密码不匹配写一条 ``login_fail``（摘要 ``password-mismatch``，60 秒
   内按 oid 节流），动态码错 / 过期仍一行不写。失败证据是「登录失败暴破」与
   「封禁期重试」两条规则的唯一数据源，所以密码错必须留痕且不含凭据原文。

全离线：真跑 ``util.cert.validate_by_cert`` + 真 RSA/TOTP + 真 jwt，只把 users
repo 换成内存假实现、``ZVMSLog`` 换成记录器。不连数据库、不发网络。

导入 ``routers.users_router`` 会在 import 时读取仓库根的 ``aes_key.txt`` /
``rsa_*.pem``，所以 pytest 必须从仓库根运行（验收命令即如此）。

    pytest tests/golden/auth/test_auth_purpose.py -q
"""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from db import backends
from routers import users_router
from util.cert import (
    get_current_totp,
    hash_password,
    jwt_decode,
    rsa_encrypt,
)

PREFIX = "/api/users"
# A 24-hex string: passes ``string_to_option_object_id``, so the route skips the
# business-id lookup and goes straight to credential validation.
VALID_OID = "64b7f0c2e1a3b4c5d6e7f801"
STRONG_PASSWORD = "StrongPass1!"
USER_NAME = "测试用户"

# Every construction / insert_log on the swapped ZVMSLog lands here.
_LOG_CALLS: list[dict] = []


class _RecordingLog:
    """Stand-in for ``typings.log.ZVMSLog`` that records instead of persisting."""

    def __init__(
        self,
        url: str = "",
        user: str = "",
        data: str = "",
        timestamp: float | None = None,
        type: str = "",
        level: str = "info",
        xuehai_id: str = "",
        client_ip: str = "",
        user_agent: str = "",
    ) -> None:
        self.url = url
        self.user = user
        self.data = data
        self.timestamp = timestamp
        self.type = type
        self.level = level
        self.xuehai_id = xuehai_id
        self.client_ip = client_ip
        self.user_agent = user_agent
        _LOG_CALLS.append({"kind": "construct", "type": type, "data": data})

    def with_text(self, text: str, type: str = "", level: str = "") -> "_RecordingLog":
        self.data = text
        if type:
            self.type = type
        if level:
            self.level = level
        return self

    async def insert_log(self) -> None:
        _LOG_CALLS.append({"kind": "insert_log", "type": self.type, "data": self.data})


class _NoRuleRepo:
    """规则一律「不存在」：本文件不测封禁判定，也避免真去连库。"""

    async def get_by_trigger(self, trigger: str):
        return None

    async def get_by_code(self, code: str):
        return None


class _FakeUsersRepo:
    """Users repo backed by one in-memory document."""

    def __init__(self, doc: dict) -> None:
        self._doc = doc

    async def get_by_oid(self, oid: str):
        return self._doc

    async def find_by_business_id(self, business_id: str):
        return None


def _credential(password: str) -> str:
    payload = {"password": password, "time": datetime.now().timestamp()}
    return rsa_encrypt(json.dumps(payload))


def _wrong_totp() -> str:
    code, _ = get_current_totp(VALID_OID)
    wrong = ("1" if code[0] != "1" else "2") + code[1:]
    assert wrong != code
    return f"*{wrong}*"


def _user_doc() -> dict:
    return {
        "_id": VALID_OID,
        "id": "2023001",
        "name": USER_NAME,
        "password": hash_password(STRONG_PASSWORD).decode("utf-8"),
        "roles": ["secretary"],
        "class_id": "64b7f0c2e1a3b4c5d6e7f802",
    }


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    _LOG_CALLS.clear()
    doc = _user_doc()
    users_router._login_failure_logged.clear()
    monkeypatch.setattr(users_router, "ZVMSLog", _RecordingLog)
    monkeypatch.setattr(
        backends, "get_users_repo", lambda *a, **k: _FakeUsersRepo(doc)
    )
    monkeypatch.setattr(backends, "get_ban_rules_repo", lambda *a, **k: _NoRuleRepo())
    app = FastAPI()
    app.include_router(users_router.router, prefix=PREFIX)
    return TestClient(app)


def _auth(client: TestClient, credential: str, purpose=None, mode: str = "long"):
    body = {"id": VALID_OID, "mode": mode, "credential": credential}
    if purpose is not None:
        body["purpose"] = purpose
    return client.post(f"{PREFIX}/auth", json=body)


def _scope(token: str) -> str:
    return jwt_decode(token)["scope"]


def _logged_types() -> list[str]:
    return [c["type"] for c in _LOG_CALLS if c["kind"] == "insert_log"]


# --- 服务端钳制 ---------------------------------------------------------------


def test_verify_with_long_mode_never_gets_access_token(client: TestClient) -> None:
    response = _auth(client, _credential(STRONG_PASSWORD), purpose="verify")
    assert response.status_code == 200
    token = response.json()["token"]
    assert _scope(token) == "temporary_token"
    assert _scope(token) != "access_token"


def test_login_with_long_mode_still_gets_access_token(client: TestClient) -> None:
    # 回归：登录路径行为不变。
    response = _auth(client, _credential(STRONG_PASSWORD), purpose="login")
    assert response.status_code == 200
    assert _scope(response.json()["token"]) == "access_token"


def test_omitted_purpose_defaults_to_login(client: TestClient) -> None:
    response = _auth(client, _credential(STRONG_PASSWORD))
    assert response.status_code == 200
    assert _scope(response.json()["token"]) == "access_token"
    assert _logged_types() == ["user_login"]


@pytest.mark.parametrize("purpose", ["verifiy", "", "VERIFY", "login ", "verify "])
def test_unrecognized_purpose_is_clamped_like_verify(
    client: TestClient, purpose: str
) -> None:
    response = _auth(client, _credential(STRONG_PASSWORD), purpose=purpose)
    assert response.status_code == 200
    assert _scope(response.json()["token"]) == "temporary_token"


# --- 审计分流 -----------------------------------------------------------------


def test_verify_password_writes_user_verify_log(client: TestClient) -> None:
    response = _auth(client, _credential(STRONG_PASSWORD), purpose="verify")
    assert response.status_code == 200
    assert _logged_types() == ["user_verify"]
    entry = next(c for c in _LOG_CALLS if c["kind"] == "insert_log")
    assert "身份验证" in entry["data"]
    assert "密码" in entry["data"]


def test_login_password_still_writes_user_login_log(client: TestClient) -> None:
    response = _auth(client, _credential(STRONG_PASSWORD), purpose="login")
    assert response.status_code == 200
    assert _logged_types() == ["user_login"]


# --- TOTP 动态码 --------------------------------------------------------------


def test_verify_accepts_totp_and_keeps_temporary_scope(client: TestClient) -> None:
    code, _ = get_current_totp(VALID_OID)
    response = _auth(client, _credential(f"*{code}*"), purpose="verify")
    assert response.status_code == 200
    assert _scope(response.json()["token"]) == "temporary_token"
    assert _logged_types() == ["user_verify"]
    entry = next(c for c in _LOG_CALLS if c["kind"] == "insert_log")
    assert "动态码" in entry["data"]


def test_login_totp_still_gets_otp_session_token(client: TestClient) -> None:
    # 回归：login + mode=long + 动态码仍走 otp 分支（access_token）。
    code, _ = get_current_totp(VALID_OID)
    response = _auth(client, _credential(f"*{code}*"), purpose="login")
    assert response.status_code == 200
    assert _scope(response.json()["token"]) == "access_token"


# --- 失败证据 ---------------------------------------------------------------


def test_verify_wrong_password_is_403_and_logs_one_failure(
    client: TestClient,
) -> None:
    response = _auth(client, _credential("WrongPass1!"), purpose="verify")
    assert response.status_code == 403
    assert response.json() == {"detail": "用户名或密码错误"}

    inserts = [c for c in _LOG_CALLS if c["kind"] == "insert_log"]
    assert [c["type"] for c in inserts] == ["login_fail"]
    assert "password-mismatch" in inserts[0]["data"]
    # 密码原文绝不能进日志。
    assert "WrongPass1!" not in inserts[0]["data"]


def test_verify_wrong_totp_is_403_and_writes_no_log(client: TestClient) -> None:
    response = _auth(client, _credential(_wrong_totp()), purpose="verify")
    assert response.status_code == 403
    assert response.json() == {"detail": "动态码错误或已过期"}
    assert _LOG_CALLS == []


def test_login_wrong_password_is_403_and_logs_one_failure(
    client: TestClient,
) -> None:
    response = _auth(client, _credential("WrongPass1!"), purpose="login")
    assert response.status_code == 403
    assert response.json() == {"detail": "用户名或密码错误"}

    inserts = [c for c in _LOG_CALLS if c["kind"] == "insert_log"]
    assert [c["type"] for c in inserts] == ["login_fail"]
    assert "WrongPass1!" not in inserts[0]["data"]
