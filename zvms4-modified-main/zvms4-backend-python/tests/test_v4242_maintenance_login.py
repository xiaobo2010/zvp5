"""v4.2.4-2 R4（设计 §3.2）：维护期间登录端点不得向非管理员下发 token。

``/api/users/auth`` 被逐字列入 ``MAINTENANCE_EXEMPT_PATHS``（main.py:139）且
豁免判定先于 admin 判定（main.py:163-167），所以中间件永远不会拦它——否则
管理员无法登录关掉维护。拒绝因此只能落在端点内，本文件钉住它：

1. 维护开启 + 普通用户 → 503，响应 JSON 里**不存在** ``token`` 字段（也不能
   在响应正文里出现 token 值）；
2. 维护开启 + admin → 200，且带 token（管理员必须能登录关维护）；
3. 维护关闭 → 与改动前一致（普通用户照样拿到 token）；
4. 拦截留痕：``app_logs`` 收到一条 ``auth_maintenance_blocked`` / ``warning``，
   且正文里不含提交的凭据；
5. 审计写失败不得把 503 变成 500（拒绝仍然生效）。

全部离线：业务库（users / app_logs）全打桩，不连 Oracle；维护状态库由
tests/conftest.py 隔离到 tmp_path。跑法：
``.venv/bin/python -m pytest tests/test_v4242_maintenance_login.py -q``
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends, system_state
from routers import users_router

NORMAL_OID = "000000000000000000000001"
ADMIN_OID = "000000000000000000000002"
XUEHAI = "20240001"
PASSWORD = "StrongPwd1!"
TOKEN = "signed-token"
BLOCKED_DETAIL = "系统维护中，仅管理员可登录"


class StubLogs:
    """``AppLogsRepo`` 的最小桩：只记 ``insert`` 的 7 个位置参。"""

    def __init__(self, exc: Exception | None = None):
        self.rows: list[dict] = []
        self._exc = exc

    async def insert(
        self, url, user, data, timestamp, ltype, level, xuehai_id,
        client_ip="", user_agent="",
    ):
        if self._exc is not None:
            raise self._exc
        self.rows.append(
            {
                "url": url,
                "user": user,
                "data": data,
                "timestamp": timestamp,
                "type": ltype,
                "level": level,
                "xuehai_id": xuehai_id,
                "client_ip": client_ip,
                "user_agent": user_agent,
            }
        )

    def types(self) -> list[str]:
        return [row["type"] for row in self.rows]


def _enable_maintenance() -> None:
    system_state.set_state(True, "升级数据库", "系统升级中", ADMIN_OID)
    system_state.invalidate_cache()


def _disable_maintenance() -> None:
    system_state.set_state(False, None, None, ADMIN_OID)
    system_state.invalidate_cache()


@pytest.fixture
def harness(monkeypatch):
    """打桩认证后的库读取；返回 ``(TestClient, StubLogs)``。"""
    logs = StubLogs()
    users = {
        NORMAL_OID: {
            "_id": NORMAL_OID,
            "id": "20240001",
            "name": "普通用户",
            "roles": ["student"],
            "class_id": None,
            "primary_xuehai_id": XUEHAI,
        },
        ADMIN_OID: {
            "_id": ADMIN_OID,
            "id": "20240002",
            "name": "管理员",
            "roles": ["admin"],
            "class_id": None,
            "primary_xuehai_id": XUEHAI,
        },
    }

    async def get_by_oid(oid):
        return users.get(oid)

    async def find_by_business_id(business_id):
        return next((u for u in users.values() if u["id"] == str(business_id)), None)

    async def validate_by_cert(user_id, credential, mode="long"):
        # 凭据校验已通过（RSA/OTP 链路不在本用例范围内）。
        return TOKEN, False

    async def get_active_for_user(user_oid):
        # 学海设备是主设备且已绑定：设备闸不写额外审计，日志里只留 user_login。
        return [{"xuehai_id": XUEHAI}]

    async def no_device_ban(xuehai_id):
        return None

    async def no_banned_users(xuehai_id):
        return []

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda *a, **k: SimpleNamespace(
            get_by_oid=get_by_oid,
            find_by_business_id=find_by_business_id,
            list_banned_by_xuehai=no_banned_users,
        ),
    )
    monkeypatch.setattr(
        backends,
        "get_device_bans_repo",
        lambda *a, **k: SimpleNamespace(get=no_device_ban),
    )
    monkeypatch.setattr(
        backends,
        "get_bindings_repo",
        lambda *a, **k: SimpleNamespace(get_active_for_user=get_active_for_user),
    )
    monkeypatch.setattr(backends, "get_applogs_repo", lambda *a, **k: logs)
    monkeypatch.setattr(users_router, "validate_by_cert", validate_by_cert)

    app = FastAPI()
    app.include_router(users_router.router, prefix="/api/users")
    return TestClient(app), logs


def _login(http: TestClient, oid: str, purpose: str = "login"):
    return http.post(
        "/api/users/auth",
        json={
            "id": oid,
            "credential": PASSWORD,
            "mode": "long",
            "purpose": purpose,
        },
        headers={"Xuehai-Id": XUEHAI},
    )


# ---------------------------------------------------------------------------
# ① 维护开启 + 普通用户 ⇒ 503 且无 token
# ---------------------------------------------------------------------------


def test_non_admin_gets_503_without_any_token_during_maintenance(harness):
    http, logs = harness
    _enable_maintenance()

    response = _login(http, NORMAL_OID)

    assert response.status_code == 503
    body = response.json()
    assert "token" not in body
    assert TOKEN not in response.text
    assert body["maintenance"] is True
    assert body["detail"] == BLOCKED_DETAIL
    # 拦截留痕：ltype / level 与设计一致
    assert logs.types() == ["auth_maintenance_blocked"]
    assert logs.rows[0]["level"] == "warning"
    assert NORMAL_OID in logs.rows[0]["user"]
    assert "普通用户" in logs.rows[0]["data"]
    # 脱敏：正文里不得出现提交的凭据
    assert PASSWORD not in str(logs.rows)


def test_blocked_login_is_not_logged_as_a_successful_login(harness):
    http, logs = harness
    _enable_maintenance()

    _login(http, NORMAL_OID)

    assert "user_login" not in logs.types()


def test_verify_purpose_is_blocked_too_during_maintenance(harness):
    """端点整体受闸：短票（purpose=verify）同样是 token，不得漏发。"""
    http, logs = harness
    _enable_maintenance()

    response = _login(http, NORMAL_OID, purpose="verify")

    assert response.status_code == 503
    assert "token" not in response.json()
    assert TOKEN not in response.text


def test_login_entry_uses_role_from_db_not_from_request(harness):
    """闸门按库里的 roles 判定：请求体里塞 roles 不生效。"""
    http, _logs = harness
    _enable_maintenance()

    response = http.post(
        "/api/users/auth",
        json={
            "id": NORMAL_OID,
            "credential": PASSWORD,
            "mode": "long",
            "purpose": "login",
            "roles": ["admin"],
        },
    )

    assert response.status_code == 503
    assert "token" not in response.json()


def test_audit_failure_still_refuses_with_503(monkeypatch, harness):
    """审计写失败不得把拒绝变成 500（token 依然不下发）。"""
    http, _logs = harness
    _enable_maintenance()
    monkeypatch.setattr(
        backends, "get_applogs_repo", lambda *a, **k: StubLogs(RuntimeError("db down"))
    )

    response = _login(http, NORMAL_OID)

    assert response.status_code == 503
    assert "token" not in response.json()


# ---------------------------------------------------------------------------
# ② 维护开启 + admin ⇒ 照常下发 token
# ---------------------------------------------------------------------------


def test_admin_still_gets_a_token_during_maintenance(harness):
    http, logs = harness
    _enable_maintenance()

    response = _login(http, ADMIN_OID)

    assert response.status_code == 200
    body = response.json()
    assert body["token"] == TOKEN
    assert body["_id"] == ADMIN_OID
    assert body["profile"]["roles"] == ["admin"]
    # admin 登录仍按原路径写 user_login 审计，没有拦截记录
    assert logs.types() == ["user_login"]


# ---------------------------------------------------------------------------
# ③ 维护关闭 ⇒ 行为与改动前一致
# ---------------------------------------------------------------------------


def test_login_is_unchanged_when_maintenance_is_off(harness):
    http, logs = harness
    _disable_maintenance()

    response = _login(http, NORMAL_OID)

    assert response.status_code == 200
    body = response.json()
    assert body["token"] == TOKEN
    assert body["_id"] == NORMAL_OID
    assert body["profile"]["roles"] == ["student"]
    assert logs.types() == ["user_login"]
    assert "auth_maintenance_blocked" not in logs.types()


def test_no_blocked_audit_when_maintenance_is_off(harness):
    http, logs = harness
    _disable_maintenance()

    _login(http, ADMIN_OID)

    assert logs.types() == ["user_login"]
