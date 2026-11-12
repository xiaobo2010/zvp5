"""维护模式期间 socket.io 准入 / 断连行为的常驻回归用例（离线，不碰生产）。

被固化的三条契约（源自 ``realtime.MaintenanceSocketGuard`` /
``routers.system_router`` 的实现，2026-10 定位）：

1. **维护开启时**：新的 socket.io 握手被拒 —— HTTP 握手返回 503，body 含
   ``maintenance: true`` 与 ``socket: true``；websocket upgrade 在 accept
   之前被 close。
2. **enable 那一刻**：已建立的连接被服务端主动断开（客户端下一跳长轮询收到
   socket.io ``41`` DISCONNECT 帧，且 ``manager.is_connected(sid, "/")`` 变
   ``False``）。
3. **disable 之后**：握手恢复正常（200 + 能完成 namespace connect）。

隔离与陷阱：

* 状态库用 ``ZVMS_SYSTEM_DB`` 指到 ``tmp_path``，并在 ``client`` fixture 里
  **自检隔离生效**（``_db_path()`` 必须落在临时目录），绝不碰生产的
  ``system_state.db``；用例前后 ``invalidate_cache()``，避免进程内 TTL 缓存
  跨用例串味。
* **盾牌 UA 陷阱**：``main.shield_middleware`` 要求 UA 含 ``Mozilla``，否则
  418。任何 HTTP 冒烟都必须带 Mozilla UA，否则会把 418 误判成失败 —— 用例
  ``test_non_mozilla_ua_is_rejected_by_shield_before_guard`` 专门钉住这一点。
* **TTL 缓存陷阱**：准入层走 ``is_enabled_fresh()`` 绕过 ~2s 缓存。用例
  ``test_guard_bypasses_ttl_cache_when_state_flips_out_of_band`` 证明「缓存仍是
  旧值时握手已被拒」。

离线机制：用 ``TestClient`` 在进程内直接打 ASGI，socket.io 走手写 Engine.IO
长轮询帧，因此「已建立的连接」就是一个 sid，不用起端口、不连真 ADB / Mongo。
只为「非被测」的外围打桩（当前用户 dependency、system_admin 的查库复核、显示
名查询、日志注入）；被测路径全是真实的：状态层、维护中间件、socket 准入中间
件、connect 处理器与 ``disconnect_all_clients``。

离线妥协：``import main`` 会触发 ``util.object_id`` 在 import 期读 cwd 下的
``aes_key.txt``，所以 pytest 必须从仓库根运行（验收命令如此）；从别处运行会在
collection 阶段失败，而不是在断言里。

    cd <repo root> && .venv/bin/python -m pytest \\
        tests/golden/system/test_maintenance_socket_guard.py -q
"""

from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest
from starlette.testclient import TestClient, WebSocketDisconnect

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]  # tests/golden/system -> <repo root>
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import main  # noqa: E402  (sys.path 引导之后再导入)
import socket_events  # noqa: E402
from db import backends, system_state  # noqa: E402
from routers import system_router  # noqa: E402
from typings.log import inject_log  # noqa: E402
from util.object_id import get_current_user  # noqa: E402

# 盾牌中间件要求 UA 含 Mozilla，否则 418 —— 所有请求都带，别把 418 误判成失败。
MOZ = {"User-Agent": "Mozilla/5.0 (regression harness)"}
ADMIN_OID = "64b7f0c2e1a3b4c5d6e7f801"

SOCKET_PATH = "/socket.io"
POLLING_URL = "/socket.io/?EIO=4&transport=polling"
WEBSOCKET_URL = "/socket.io/?EIO=4&transport=websocket"
ENABLE_URL = "/api/system/maintenance/enable"
DISABLE_URL = "/api/system/maintenance/disable"


class _FakeLog:
    """替身日志：不落库，只吞掉 with_text / insert_log 调用。"""

    def with_text(self, *args, **kwargs):
        return self

    async def insert_log(self) -> None:
        return None


class _FakeUsersRepo:
    """system_admin 的查库复核：返回未封禁的管理员。"""

    async def get_by_oid(self, oid):
        return {"_id": oid, "banned": False}


async def _fake_permissions(user):
    return ["admin"]


async def _fake_display_name(oid):
    return "回归管理员"


def _admin_user() -> dict:
    return {"id": ADMIN_OID, "per": ["admin"]}


async def _accept_any_token(token, scope, exception=True, request=None) -> dict:
    return _admin_user()


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    """隔离状态库 + 打桩外围 + 返回进程内 TestClient（不进 lifespan）。"""
    state_db = tmp_path / "system_state.db"
    monkeypatch.setenv("ZVMS_SYSTEM_DB", str(state_db))
    system_state.invalidate_cache()

    # 自检隔离生效：环境变量确实把状态库指到临时目录，绝不碰生产库。
    isolated = Path(system_state._db_path()).resolve()
    assert isolated == state_db.resolve(), "ZVMS_SYSTEM_DB 未生效，用例可能污染生产状态库"
    assert isolated != Path(system_state.STATE_DB_PATH).resolve()

    monkeypatch.setitem(main.app.dependency_overrides, get_current_user, _admin_user)
    monkeypatch.setitem(main.app.dependency_overrides, inject_log, _FakeLog)
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: _FakeUsersRepo())
    monkeypatch.setattr(socket_events, "_authenticate", _accept_any_token)
    monkeypatch.setattr(system_router, "get_user_permissions", _fake_permissions)
    monkeypatch.setattr(system_router, "get_user_display_name", _fake_display_name)

    # 不进入 context manager ⇒ 不跑 lifespan ⇒ 不连 ADB / Mongo。
    test_client = TestClient(main.app)
    yield test_client
    test_client.close()
    system_state.invalidate_cache()


def handshake(client: TestClient, headers: dict | None = None):
    """Engine.IO 握手（无 sid = 新连接）。"""
    return client.get(POLLING_URL, headers=MOZ if headers is None else headers)


def namespace_connect(client: TestClient, sid: str):
    """发 socket.io CONNECT(40) 并取回 CONNECT ack。

    匿名连接已被 ``connect`` 拒绝，所以这里显式带 ``auth.token``；本文件的
    ``_authenticate`` 打桩为「任何 token 即管理员」，被测的是维护模式行为。
    """
    post = client.post(
        f"{POLLING_URL}&sid={sid}",
        content="40" + json.dumps({"token": "maintenance-harness"}),
        headers={**MOZ, "content-type": "text/plain;charset=UTF-8"},
    )
    poll = client.get(f"{POLLING_URL}&sid={sid}", headers=MOZ)
    return post, poll


def socket_sid_from_ack(ack_body: str) -> str:
    """从 CONNECT ack（``40{"sid":...}``）里取 socket.io 的 sid。

    注意：``manager.is_connected`` / ``rooms`` 用的是 socket.io sid，与握手
    返回的 Engine.IO sid 不是同一个值（前者用于 manager 注册表，后者用于带
    ``sid`` 的长轮询 URL）。
    """
    return json.loads(ack_body[2:])["sid"]


def open_connection(client: TestClient) -> str:
    """完成一次握手并返回 engine.io sid（此时尚未 namespace connect）。"""
    response = handshake(client)
    assert response.status_code == 200 and response.text.startswith("0{")
    return json.loads(response.text[1:])["sid"]


def enable(client: TestClient, reason: str = "回归：断 socket"):
    return client.post(
        ENABLE_URL,
        json={"reason": reason, "announcement": "回归演练"},
        headers=MOZ,
    )


def disable(client: TestClient, reason: str = "回归结束"):
    return client.post(DISABLE_URL, json={"reason": reason}, headers=MOZ)


def test_state_db_is_isolated_to_tmp_path(client: TestClient, tmp_path) -> None:
    """隔离自检：状态库落在临时目录，初始为空库（未读到任何生产状态）。"""
    assert Path(system_state._db_path()).resolve().parent == tmp_path.resolve()
    assert system_state.get_state()["enabled"] is False
    # 惰性建表后读一次即已建表；但「读」不落行（默认关闭与显式关闭可区分）。
    with closing(sqlite3.connect(tmp_path / "system_state.db")) as conn:
        rows = conn.execute("SELECT COUNT(*) FROM maintenance_state").fetchone()
    assert rows == (0,)


def test_handshake_rejected_while_maintenance_enabled(client: TestClient) -> None:
    """维护开启时：新的 HTTP 握手被拒（503 + maintenance/socket 标记）。"""
    response = enable(client)
    assert response.status_code == 200 and response.json()["data"]["enabled"] is True
    assert system_state.is_enabled_fresh() is True

    rejected = handshake(client)
    assert rejected.status_code == 503
    body = rejected.json()
    assert body["maintenance"] is True
    assert body["socket"] is True
    # 拿不到 Engine.IO open 包 ⇒ 客户端不会拿到 sid。
    assert not rejected.text.startswith("0{")


def test_websocket_upgrade_rejected_while_maintenance_enabled(
    client: TestClient,
) -> None:
    """维护开启时：websocket upgrade 在 accept 之前被 close。"""
    enable(client)
    # accept 前 close：TestClient 在进入 context manager 时抛 WebSocketDisconnect。
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(WEBSOCKET_URL, headers=MOZ):
            pass


def test_enable_disconnects_established_client(client: TestClient) -> None:
    """enable 那一刻：已建立的连接被服务端断开（41 DISCONNECT + 注册表摘除）。"""
    sid = open_connection(client)
    _post, poll = namespace_connect(client, sid)
    assert poll.status_code == 200 and poll.text.startswith("40")
    socket_sid = socket_sid_from_ack(poll.text)
    assert main.sio.manager.is_connected(socket_sid, "/") is True

    response = enable(client)
    assert response.status_code == 200 and response.json()["data"]["enabled"] is True
    # 状态落库之后的同一请求里就已断开，无需客户端配合。
    assert main.sio.manager.is_connected(socket_sid, "/") is False

    # DISCONNECT(41) 已入队，长轮询客户端下一跳即可取到。
    tail = client.get(f"{POLLING_URL}&sid={sid}", headers=MOZ)
    assert tail.status_code == 200 and tail.text.startswith("41")


def test_handshake_recovers_after_disable(client: TestClient) -> None:
    """disable 之后：握手恢复正常，并能完成完整 namespace connect。"""
    enable(client)
    assert handshake(client).status_code == 503

    response = disable(client)
    assert response.status_code == 200 and response.json()["data"]["enabled"] is False

    sid = open_connection(client)
    _post, poll = namespace_connect(client, sid)
    assert poll.status_code == 200 and poll.text.startswith("40")
    assert main.sio.manager.is_connected(socket_sid_from_ack(poll.text), "/") is True


def test_guard_bypasses_ttl_cache_when_state_flips_out_of_band(
    client: TestClient, monkeypatch, tmp_path
) -> None:
    """准入层绕 TTL 缓存：缓存仍为旧值时，握手已被 503 拒掉。"""
    monkeypatch.setattr(system_state, "CACHE_TTL", 60.0)

    system_state.set_state(False, None, None, ADMIN_OID)  # 落一行，enabled=0
    system_state.invalidate_cache()
    assert system_state.is_enabled() is False  # 预热缓存：False

    # 绕过 API 直接改 SQLite —— 模拟「缓存还没过期，但库里已经开启」。
    with closing(sqlite3.connect(tmp_path / "system_state.db")) as conn, conn:
        conn.execute("UPDATE maintenance_state SET enabled = 1 WHERE id = 1")

    assert system_state.get_state()["enabled"] is True
    assert system_state.is_enabled() is False  # 带缓存读仍是陈旧的 False
    assert system_state.is_enabled_fresh() is True  # 绕缓存读到新值

    rejected = handshake(client)  # 准入层用 fresh ⇒ 不吃缓存
    assert rejected.status_code == 503
    assert rejected.json()["maintenance"] is True


def test_non_mozilla_ua_is_rejected_by_shield_before_guard(
    client: TestClient,
) -> None:
    """盾牌 UA 陷阱：无 Mozilla UA 的握手被外层盾牌 418，而非 socket 守卫 503。"""
    enable(client)

    no_ua = handshake(client, headers={})  # httpx 默认 UA 不含 Mozilla
    assert no_ua.status_code == 418

    with_ua = handshake(client)
    assert with_ua.status_code == 503
