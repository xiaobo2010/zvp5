"""socket.io CONNECT 必须按 token 查一遍封禁（离线，不连库）。

漏洞形态：``connect`` 只查维护模式，封禁账号拿着尚未过期的旧 token 仍能建
WebSocket 连接（HTTP 侧已被 ``_authenticate`` 挡住，实时通道是漏的）。

这里驱动真实的 ``main.sio`` 事件处理器（手写 Engine.IO 长轮询帧，进程内
``TestClient``），只打桩「库」与「权限派生」：

* 被封禁用户的 token → namespace connect 被拒（``44`` CONNECT_ERROR），原因不回给
  客户端；
* 正常用户的 token → 照旧连上；
* token 既可以从 CONNECT 包的 ``auth.token`` 来，也可以从
  ``Authorization`` 头来，Bearer 前缀带不带都认；
* 无 token 的匿名连接维持原行为（不被这次改动误伤）；
* 查库炸了也必须是「拒绝连接」，不能把异常抛进 ASGI。

从仓库根运行（``util.object_id`` 在 import 期读 ``aes_key.txt``）::

    ./.venv/bin/python -m pytest tests/test_socket_connect_auth.py -q
"""

from __future__ import annotations

import json
import logging

import pytest
from starlette.testclient import TestClient

import main
from db import backends
from util import cert
from util import object_id as object_id_module

MOZ = {"User-Agent": "Mozilla/5.0 (regression harness)"}
POLLING_URL = "/socket.io/?EIO=4&transport=polling"
BANNED_OID = "64b7f0c2e1a3b4c5d6e7f901"
ACTIVE_OID = "64b7f0c2e1a3b4c5d6e7f902"
SESSION_ID = "v1"
GENERIC_REJECTION = "Connection rejected by server"


class _FakeUsersRepo:
    def __init__(self, rows: dict[str, dict], error: Exception | None = None) -> None:
        self.rows = rows
        self.error = error
        self.lookups: list[str] = []

    async def get_by_oid(self, oid: str):
        self.lookups.append(oid)
        if self.error is not None:
            raise self.error
        return self.rows.get(oid)


async def _permissions(_user):
    return ["student"]


def _user(oid: str, *, banned: bool = False) -> dict:
    return {
        "_id": oid,
        "id": oid,
        "name": "回归用户",
        "session_id": SESSION_ID,
        "banned": banned,
    }


def _token(oid: str) -> str:
    return cert.jwt_encode(oid, [], ["student"], "long", session_id=SESSION_ID)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(object_id_module, "get_user_permissions", _permissions)
    test_client = TestClient(main.app)
    yield test_client
    test_client.close()


def _install_repo(monkeypatch, repo: _FakeUsersRepo) -> _FakeUsersRepo:
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: repo)
    return repo


def _handshake(client: TestClient, headers: dict | None = None) -> str:
    response = client.get(POLLING_URL, headers={**MOZ, **(headers or {})})
    assert response.status_code == 200 and response.text.startswith("0{")
    return json.loads(response.text[1:])["sid"]


def _namespace_connect(client: TestClient, sid: str, *, auth=None) -> str:
    """发 socket.io CONNECT 并返回服务端的应答帧（``40`` 接受 / ``44`` 拒绝）。"""
    body = "40" + (json.dumps(auth) if auth is not None else "")
    client.post(
        f"{POLLING_URL}&sid={sid}",
        content=body,
        headers={**MOZ, "content-type": "text/plain;charset=UTF-8"},
    )
    poll = client.get(f"{POLLING_URL}&sid={sid}", headers=MOZ)
    assert poll.status_code == 200
    return poll.text


def test_banned_token_is_rejected_at_connect(client, monkeypatch, caplog) -> None:
    repo = _install_repo(monkeypatch, _FakeUsersRepo({BANNED_OID: _user(BANNED_OID, banned=True)}))
    sid = _handshake(client)

    with caplog.at_level(logging.WARNING, logger="main"):
        packet = _namespace_connect(client, sid, auth={"token": _token(BANNED_OID)})

    assert packet.startswith("44"), packet
    assert "banned" not in packet.lower() and "USER_BANNED" not in packet
    assert repo.lookups == [BANNED_OID]
    assert any("refuse socket connect" in record.message for record in caplog.records)


def test_active_token_can_connect(client, monkeypatch) -> None:
    _install_repo(monkeypatch, _FakeUsersRepo({ACTIVE_OID: _user(ACTIVE_OID)}))
    sid = _handshake(client)

    packet = _namespace_connect(client, sid, auth={"token": _token(ACTIVE_OID)})

    assert packet.startswith("40"), packet


@pytest.mark.parametrize("prefix", ["", "Bearer "])
def test_authorization_header_token_is_honoured(client, monkeypatch, prefix) -> None:
    _install_repo(monkeypatch, _FakeUsersRepo({BANNED_OID: _user(BANNED_OID, banned=True)}))
    sid = _handshake(client, {"Authorization": f"{prefix}{_token(BANNED_OID)}"})

    packet = _namespace_connect(client, sid)

    assert packet.startswith("44"), packet


@pytest.mark.parametrize("prefix", ["", "Bearer "])
def test_auth_payload_token_accepts_optional_bearer_prefix(client, monkeypatch, prefix) -> None:
    _install_repo(monkeypatch, _FakeUsersRepo({BANNED_OID: _user(BANNED_OID, banned=True)}))
    sid = _handshake(client)

    packet = _namespace_connect(
        client, sid, auth={"token": f"{prefix}{_token(BANNED_OID)}"}
    )

    assert packet.startswith("44"), packet


def test_anonymous_connection_stays_allowed(client, monkeypatch) -> None:
    repo = _install_repo(monkeypatch, _FakeUsersRepo({}))
    sid = _handshake(client)

    packet = _namespace_connect(client, sid)

    assert packet.startswith("40"), packet
    assert repo.lookups == [], "no token → no lookup (behaviour unchanged)"


def test_expired_or_forged_token_is_rejected(client, monkeypatch) -> None:
    _install_repo(monkeypatch, _FakeUsersRepo({ACTIVE_OID: _user(ACTIVE_OID)}))
    sid = _handshake(client)

    packet = _namespace_connect(client, sid, auth={"token": "not-a-jwt"})

    assert packet.startswith("44"), packet


def test_database_error_rejects_instead_of_crashing(client, monkeypatch, caplog) -> None:
    _install_repo(
        monkeypatch,
        _FakeUsersRepo({}, error=RuntimeError("oracle is down")),
    )
    sid = _handshake(client)

    with caplog.at_level(logging.WARNING, logger="main"):
        packet = _namespace_connect(client, sid, auth={"token": _token(ACTIVE_OID)})

    assert packet.startswith("44"), packet
    assert any("refuse socket connect" in record.message for record in caplog.records)
