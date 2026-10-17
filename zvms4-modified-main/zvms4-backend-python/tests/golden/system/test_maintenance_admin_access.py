"""维护模式期间 admin 全域放行的常驻回归用例（离线，不碰生产、不连 Oracle）。

被固化的契约（源自 ``main.maintenance_middleware`` / ``realtime`` 准入层与
``util.object_id.is_admin_token`` 的实现，2026-10）：

1. **HTTP**：维护开启时，非豁免 ``/api/`` 路径 —— 无 token / 非 admin token /
   过期 admin token 一律 503；有效 admin token 放行到路由层。
2. **socket**：维护开启时，握手携带有效 admin token 放行（HTTP 握手 200 +
   Engine.IO open 包）；无 token / 过期 token 仍 503。
3. **已知前向约束**：socket.io 的 ``auth`` 载荷在 CONNECT 包内，**握手这一层
   读不到** —— 只把 token 放 auth 载荷的客户端仍会被握手层 503（``connect``
   处理器能读，但那时握手已经结束）。用例钉住这一点，避免被误当成回归。
4. **门控语义**：HTTP 侧是「缓存 + fresh」两级；跨进程 disable 让缓存陈旧为
   True 时，fresh 立刻纠正为放行。

隔离与陷阱：

* 状态库用 ``ZVMS_SYSTEM_DB`` 指到 ``tmp_path``，并在 ``client`` fixture 里
  自检隔离生效，绝不碰生产的 ``system_state.db``；用例前后 ``invalidate_cache()``。
* **盾牌 UA 陷阱**：``main.shield_middleware`` 要求 UA 含 ``Mozilla``，否则
  418。所有请求都带，别把 418 误判成失败。
* 探测路径走一个**不存在**的非豁免路由：放行时是 404（路由层，不查库），
  被拦时是 503，无需任何 DB 桩。
* 维护状态直接 ``system_state.set_state`` 写入（同时失效缓存），不经过
  ``/api/system/maintenance/enable``，因此不需要 Oracle / Mongo 桩。

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

import jwt
import pytest
from starlette.testclient import TestClient

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]  # tests/golden/system -> <repo root>
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import main  # noqa: E402  (sys.path 引导之后再导入)
import realtime  # noqa: E402
from db import system_state  # noqa: E402
from util.cert import jwt_encode, jwt_private_key  # noqa: E402

# 盾牌中间件要求 UA 含 Mozilla，否则 418 —— 所有请求都带。
MOZ = {"User-Agent": "Mozilla/5.0 (regression harness)"}
ADMIN_OID = "64b7f0c2e1a3b4c5d6e7f801"
STUDENT_OID = "64b7f0c2e1a3b4c5d6e7f802"

# 非豁免的非 /api/system 路径，且不命中 shield 的 SEC_PATTERN；不存在 ⇒ 放行时 404。
PROBE_URL = "/api/__probe_not_exempt__"
POLLING_URL = "/socket.io/?EIO=4&transport=polling"

ADMIN_TOKEN = jwt_encode(ADMIN_OID, [], ["admin"], type="long")
STUDENT_TOKEN = jwt_encode(STUDENT_OID, [], ["student"], type="long")

# 手工签发一枚已过期的 admin token（jwt_encode 只发未来有效期）。
EXPIRED_ADMIN_TOKEN = jwt.encode(
    {
        "iss": "zvms",
        "exp": datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5),
        "iat": datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=10),
        "sub": ADMIN_OID,
        "scope": "access_token",
        "per": ["admin"],
        "elg": [],
        "jti": "expired-admin-token-fixture",
    },
    jwt_private_key,
    algorithm="HS256",
)


def bearer(token: str) -> dict:
    return {**MOZ, "Authorization": f"Bearer {token}"}


def enable(reason: str = "回归：admin 准入") -> None:
    system_state.set_state(True, reason, None, ADMIN_OID)


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


def test_non_exempt_path_with_admin_token_passes_through(client: TestClient) -> None:
    """维护开启 + 非豁免路径 + admin token：放行到路由层（404，而非 503）。"""
    enable()
    assert system_state.is_enabled_fresh() is True

    response = client.get(PROBE_URL, headers=bearer(ADMIN_TOKEN))
    assert response.status_code == 404


def test_non_exempt_path_without_token_is_rejected(client: TestClient) -> None:
    """维护开启 + 非豁免路径 + 无 token：503，且 body 形状不变。"""
    enable()

    response = client.get(PROBE_URL, headers=MOZ)
    assert response.status_code == 503
    assert response.json() == {
        "detail": "系统维护中，请稍后再试",
        "maintenance": True,
    }


def test_non_exempt_path_with_non_admin_token_is_rejected(
    client: TestClient,
) -> None:
    """维护开启 + 非豁免路径 + 非 admin token：503。"""
    enable()

    response = client.get(PROBE_URL, headers=bearer(STUDENT_TOKEN))
    assert response.status_code == 503
    assert response.json()["maintenance"] is True


def test_non_exempt_path_with_expired_admin_token_is_rejected(
    client: TestClient,
) -> None:
    """维护开启 + 非豁免路径 + 过期 admin token：503（exp 由解码层校验）。"""
    enable()

    response = client.get(PROBE_URL, headers=bearer(EXPIRED_ADMIN_TOKEN))
    assert response.status_code == 503
    assert response.json()["maintenance"] is True


def test_per_must_be_a_list_to_grant_admin() -> None:
    """``per`` 是字符串时不得按子串匹配放行（例如 DB 里 roles 存成 "superadmin"）。"""
    from util.object_id import is_admin_token

    string_per = jwt.encode(
        {
            "iss": "zvms",
            "exp": datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5),
            "iat": datetime.datetime.now(datetime.timezone.utc),
            "sub": ADMIN_OID,
            "scope": "access_token",
            "per": "superadmin",
            "elg": [],
            "jti": "string-per-fixture",
        },
        jwt_private_key,
        algorithm="HS256",
    )
    assert is_admin_token(string_per) is False
    assert is_admin_token(ADMIN_TOKEN) is True


def test_socket_handshake_with_admin_token_is_allowed(client: TestClient) -> None:
    """维护开启 + admin token 握手：放行（200 + Engine.IO open 包）。"""
    enable()
    assert system_state.is_enabled_fresh() is True

    response = client.get(POLLING_URL, headers=bearer(ADMIN_TOKEN))
    assert response.status_code == 200
    assert response.text.startswith("0{")
    assert json.loads(response.text[1:])["sid"]


def test_socket_handshake_without_or_with_expired_token_is_rejected(
    client: TestClient,
) -> None:
    """维护开启 + 握手无 token / 过期 admin token：仍按原样 503。"""
    enable()

    no_token = client.get(POLLING_URL, headers=MOZ)
    assert no_token.status_code == 503
    assert no_token.json()["socket"] is True

    expired = client.get(
        POLLING_URL, headers=bearer(EXPIRED_ADMIN_TOKEN)
    )
    assert expired.status_code == 503


def test_auth_payload_token_is_invisible_to_handshake_layer(
    client: TestClient,
) -> None:
    """已知前向约束：token 只在 socket.io ``auth`` 载荷里时，握手层仍 503。

    ``auth`` 载荷随 CONNECT 包（``40{"token": ...}``）到达，早于它的 Engine.IO
    握手读不到；同一 token 走 query / header 才被准入层认出来。
    """
    enable()

    rejected = client.get(POLLING_URL, headers=MOZ)
    assert rejected.status_code == 503

    allowed = client.get(POLLING_URL + f"&token={ADMIN_TOKEN}", headers=MOZ)
    assert allowed.status_code == 200

    # ``connect`` 处理器那一层（CONNECT 包之后）能读到 auth 载荷。
    assert realtime.connect_token({}, {"token": ADMIN_TOKEN}) == ADMIN_TOKEN
    assert realtime.handshake_token({"headers": [], "query_string": b""}) is None


def test_cached_true_with_fresh_false_stops_rejecting(
    client: TestClient, monkeypatch, tmp_path
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
