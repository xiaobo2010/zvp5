"""维护模式期间 admin 全域放行的常驻回归用例（离线，不碰生产、不连 Oracle）。

被固化的契约（源自 ``main.maintenance_middleware`` / ``realtime`` 准入层与
``util.object_id.is_admin_token`` 的实现，2026-10）：

1. **HTTP**：维护开启时，非豁免 ``/api/`` 路径 —— 无 token / 非 admin /
   过期 / banned / session 不匹配一律 503；有效 admin token 放行到路由层。
2. **socket**：维护开启时，握手携带有效 admin token 放行（HTTP 握手 200 +
   Engine.IO open 包）；其余情形仍 503。
3. **新认证模型**：admin 判定走 ``_authenticate``（``long`` scope），``per``
   来自**当前 roles**而非 JWT 快照，且校验 ``sid == session_id`` 与未封禁。
4. **已知前向约束**：socket.io 的 ``auth`` 载荷在 CONNECT 包内，**握手这一层
   读不到** —— 只把 token 放 auth 载荷的客户端仍会被握手层 503（``connect``
   处理器能读，但那时握手已经结束）。用例钉住这一点，避免被误当成回归。
5. **门控语义**：HTTP 侧是「缓存 + fresh」两级；跨进程 disable 让缓存陈旧为
   True 时，fresh 立刻纠正为放行。

隔离与陷阱：

* 状态库用 ``ZVMS_SYSTEM_DB`` 指到 ``tmp_path``，并在 ``client`` fixture 里
  自检隔离生效，绝不碰生产的 ``system_state.db``；用例前后 ``invalidate_cache()``。
* **用户库用假 repo**（``monkeypatch.setattr(backends, "get_users_repo", ...)``，
  与 ``tests/test_auth_security.py`` 同法），不连 Oracle / Mongo。
* **盾牌 UA 陷阱**：``main.shield_middleware`` 要求 UA 含 ``Mozilla``，否则
  418。所有请求都带，别把 418 误判成失败。
* 探测路径走一个**不存在**的非豁免路由：放行时是 404（路由层），被拦时是
  503，无需真实业务数据。
* 维护状态直接 ``system_state.set_state`` 写入（同时失效缓存），不经过
  ``/api/system/maintenance/enable``。

    cd <repo root> && .venv/bin/python -m pytest \\
        tests/golden/system/test_maintenance_admin_access.py -q
"""

from __future__ import annotations

import datetime
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from starlette.testclient import TestClient

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]  # tests/golden/system -> <repo root>
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import main  # noqa: E402  (sys.path 引导之后再导入)
import realtime  # noqa: E402
from db import backends, system_state  # noqa: E402
from util.cert import jwt_encode, jwt_private_key  # noqa: E402

# 盾牌中间件要求 UA 含 Mozilla，否则 418 —— 所有请求都带。
MOZ = {"User-Agent": "Mozilla/5.0 (regression harness)"}
ADMIN_OID = "64b7f0c2e1a3b4c5d6e7f801"
STUDENT_OID = "64b7f0c2e1a3b4c5d6e7f802"
SESSION_ID = "v1"

# 非豁免的非 /api/system 路径，且不命中 shield 的 SEC_PATTERN；不存在 ⇒ 放行时 404。
PROBE_URL = "/api/__probe_not_exempt__"
POLLING_URL = "/socket.io/?EIO=4&transport=polling"


@pytest.fixture()
def account(monkeypatch) -> dict:
    """假用户库：admin（未封禁、session 匹配）+ 学生；admin 返回值可当场改动。"""
    admin = {
        "_id": ADMIN_OID,
        "roles": ["admin"],
        "password": None,
        "session_id": SESSION_ID,
    }
    users = {
        ADMIN_OID: admin,
        STUDENT_OID: {
            "_id": STUDENT_OID,
            "roles": ["student"],
            "password": None,
            "session_id": SESSION_ID,
        },
    }

    async def get_by_oid(oid):
        return users.get(oid)

    monkeypatch.setattr(
        backends, "get_users_repo", lambda: SimpleNamespace(get_by_oid=get_by_oid)
    )
    return admin


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    """隔离状态库 + 返回进程内 TestClient（不进 lifespan，不连 ADB / Mongo）。"""
    state_db = tmp_path / "system_state.db"
    monkeypatch.setenv("ZVMS_SYSTEM_DB", str(state_db))
    system_state.invalidate_cache()

    # 自检隔离生效：环境变量确实把状态库指到临时目录，绝不碰生产库。
    isolated = Path(system_state._db_path()).resolve()
    assert isolated == state_db.resolve(), "ZVMS_SYSTEM_DB 未生效，用例可能污染生产状态库"
    assert isolated != Path(system_state.STATE_DB_PATH).resolve()

    test_client = TestClient(main.app)
    yield test_client
    test_client.close()
    system_state.invalidate_cache()


def admin_token(session_id: str = SESSION_ID, permissions=None) -> str:
    return jwt_encode(
        ADMIN_OID,
        [],
        ["admin"] if permissions is None else permissions,
        "long",
        session_id=session_id,
    )


def expired_admin_token() -> str:
    """手工签发一枚已过期的 admin token（jwt_encode 只发未来有效期）。"""
    now = datetime.datetime.now(datetime.timezone.utc)
    return jwt.encode(
        {
            "iss": "zvms",
            "exp": now - datetime.timedelta(minutes=5),
            "iat": now - datetime.timedelta(minutes=10),
            "sub": ADMIN_OID,
            "scope": "access_token",
            "per": ["admin"],
            "elg": [],
            "jti": "expired-admin-token-fixture",
            "sid": SESSION_ID,
            "purpose": "verify",
        },
        jwt_private_key,
        algorithm="HS256",
    )


def bearer(token: str) -> dict:
    return {**MOZ, "Authorization": f"Bearer {token}"}


def enable(reason: str = "回归：admin 准入") -> None:
    system_state.set_state(True, reason, None, ADMIN_OID)


def test_http_non_exempt_path_with_admin_token_passes_through(
    client: TestClient, account
) -> None:
    """维护开启 + 非豁免路径 + admin token：放行到路由层（404，而非 503）。"""
    enable()
    assert system_state.is_enabled_fresh() is True

    response = client.get(PROBE_URL, headers=bearer(admin_token()))
    assert response.status_code == 404


def test_socket_handshake_with_admin_token_is_allowed(
    client: TestClient, account
) -> None:
    """维护开启 + admin token 握手：放行（200 + Engine.IO open 包）。"""
    enable()

    response = client.get(POLLING_URL, headers=bearer(admin_token()))
    assert response.status_code == 200
    assert response.text.startswith("0{")
    assert json.loads(response.text[1:])["sid"]


def test_without_token_is_rejected(client: TestClient, account) -> None:
    """无 token：503，且 body 形状（HTTP / socket 各自）不变。"""
    enable()

    http_rejected = client.get(PROBE_URL, headers=MOZ)
    assert http_rejected.status_code == 503
    assert http_rejected.json() == {
        "detail": "系统维护中，请稍后再试",
        "maintenance": True,
    }

    socket_rejected = client.get(POLLING_URL, headers=MOZ)
    assert socket_rejected.status_code == 503
    assert socket_rejected.json()["socket"] is True


def test_non_admin_is_rejected(client: TestClient, account) -> None:
    """非 admin（用户存在但 roles 无 admin）：HTTP 与 socket 均 503。"""
    enable()
    token = jwt_encode(STUDENT_OID, [], ["admin"], "long", session_id=SESSION_ID)

    response = client.get(PROBE_URL, headers=bearer(token))
    assert response.status_code == 503
    assert response.json()["maintenance"] is True
    assert client.get(POLLING_URL, headers=bearer(token)).status_code == 503


def test_unknown_user_is_rejected(client: TestClient, account) -> None:
    """用户不存在（查库返回 None）：503。"""
    enable()
    ghost_oid = "64b7f0c2e1a3b4c5d6e7f899"
    token = jwt_encode(ghost_oid, [], ["admin"], "long", session_id=SESSION_ID)

    assert client.get(PROBE_URL, headers=bearer(token)).status_code == 503


def test_expired_admin_token_is_rejected(client: TestClient, account) -> None:
    """过期 admin token：503（exp 由解码层校验）。"""
    enable()

    response = client.get(PROBE_URL, headers=bearer(expired_admin_token()))
    assert response.status_code == 503
    assert response.json()["maintenance"] is True


def test_banned_admin_is_rejected(client: TestClient, account) -> None:
    """封禁的 admin：503（新认证模型新增的 banned 校验）。"""
    enable()
    token = admin_token()
    account["banned"] = True

    response = client.get(PROBE_URL, headers=bearer(token))
    assert response.status_code == 503
    assert response.json()["maintenance"] is True
    assert client.get(POLLING_URL, headers=bearer(token)).status_code == 503


def test_session_mismatch_is_rejected(client: TestClient, account) -> None:
    """token 的 sid 与库里 session_id 不匹配（凭据已轮换）：503。"""
    enable()
    token = admin_token(session_id="v0")  # 库里是 SESSION_ID="v1"
    assert account["session_id"] == SESSION_ID

    response = client.get(PROBE_URL, headers=bearer(token))
    assert response.status_code == 503
    assert response.json()["maintenance"] is True
    assert client.get(POLLING_URL, headers=bearer(token)).status_code == 503


def test_admin_comes_from_current_roles_not_jwt_snapshot(
    client: TestClient, account
) -> None:
    """JWT ``per`` 快照不参与判定：快照写着 admin 但库里的 roles 不是 ⇒ 503。"""
    enable()
    account["roles"] = ["student"]
    assert client.get(PROBE_URL, headers=bearer(admin_token())).status_code == 503

    # 反之：快照不含 admin，但库里角色是 admin ⇒ 放行。
    account["roles"] = ["admin"]
    snapshot = admin_token(permissions=["student"])
    assert client.get(PROBE_URL, headers=bearer(snapshot)).status_code == 404


def test_auth_payload_token_is_invisible_to_handshake_layer(
    client: TestClient, account
) -> None:
    """已知前向约束：token 只在 socket.io ``auth`` 载荷里时，握手层仍 503。

    ``auth`` 载荷随 CONNECT 包（``40{"token": ...}``）到达，早于它的 Engine.IO
    握手读不到；同一 token 走 query / header 才被准入层认出来。
    """
    enable()
    token = admin_token()

    rejected = client.get(POLLING_URL, headers=MOZ)
    assert rejected.status_code == 503

    allowed = client.get(POLLING_URL + f"&token={token}", headers=MOZ)
    assert allowed.status_code == 200

    # ``connect`` 处理器那一层（CONNECT 包之后）能读到 auth 载荷。
    assert realtime.connect_token({}, {"token": token}) == token
    assert realtime.handshake_token({"headers": [], "query_string": b""}) is None


def test_cached_true_with_fresh_false_stops_rejecting(
    client: TestClient, account, monkeypatch, tmp_path
) -> None:
    """门控语义：缓存陈旧为 True 而库里已关闭时，fresh 复核立刻纠正为放行。"""
    monkeypatch.setattr(system_state, "CACHE_TTL", 60.0)

    enable()
    assert system_state.is_enabled() is True  # 预热缓存：True

    # 绕过 API 直接改 SQLite —— 模拟跨进程 disable（本进程缓存还是 True）。
    with closing(sqlite3.connect(tmp_path / "system_state.db")) as conn, conn:
        conn.execute("UPDATE maintenance_state SET enabled = 0 WHERE id = 1")

    assert system_state.is_enabled() is True  # 带缓存读仍是陈旧的 True
    assert system_state.is_enabled_fresh() is False

    response = client.get(PROBE_URL, headers=MOZ)  # 缓存为 True ⇒ 走到 fresh 复核
    assert response.status_code == 404
