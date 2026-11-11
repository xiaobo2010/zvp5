"""socket.io 只认 ``auth`` 载荷（与 ``Authorization`` 头），不再认 query。

query 传 token 会被反代 access log 整行记下（``?token=...``），等于把会话凭据
写进一份长期留存的明文日志。这里钉住三条：

* 握手层（Engine.IO）只从 header 取 token：维护模式的 admin 放行也走这条；
* CONNECT 层（``main.connect``）优先 ``auth`` 载荷，其次 ``HTTP_AUTHORIZATION``；
* query 里的任何 token 键都不再被识别——用真 ASGI 打一遍，确认它既不放行维护
  模式，也不会触发一次用户查询。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import main
import realtime
from db import backends
from util import cert

MOZ = {"User-Agent": "Mozilla/5.0 (socket token harness)"}
POLLING_URL = "/socket.io/?EIO=4&transport=polling"
OID = "64b7f0c2e1a3b4c5d6e7fa01"
SID = "session-v1"
TOKEN = cert.jwt_encode(OID, [], ["student"], "long", session_id=SID)


class _RecordingUsersRepo:
    def __init__(self) -> None:
        self.lookups: list[str] = []

    async def get_by_oid(self, oid: str):
        self.lookups.append(oid)
        return {"_id": oid, "session_id": SID, "banned": 0, "roles": ["student"]}

    async def get_for_auth(self, oid: str):
        return await self.get_by_oid(oid)


async def _permissions(_user):
    return ["student"]


@pytest.fixture()
def repo(monkeypatch) -> _RecordingUsersRepo:
    from util import object_id as object_id_module

    recording = _RecordingUsersRepo()
    monkeypatch.setattr(object_id_module, "get_user_permissions", _permissions)
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: recording)
    return recording


@pytest.mark.parametrize("key", ["token", "auth", "access_token"])
def test_query_string_is_never_a_token_source(key: str) -> None:
    query = f"{key}={TOKEN}".encode()

    assert realtime.handshake_token({"headers": [], "query_string": query}) is None
    assert realtime.connect_token({"QUERY_STRING": query.decode()}, None) is None


def test_handshake_still_honours_the_authorization_header() -> None:
    scope = {
        "headers": [(b"authorization", f"Bearer {TOKEN}".encode())],
        "query_string": b"",
    }

    assert realtime.handshake_token(scope) == TOKEN


@pytest.mark.parametrize("key", ["token", "access_token", "Authorization"])
def test_connect_payload_is_the_supported_channel(key: str) -> None:
    assert realtime.connect_token({}, {key: TOKEN}) == TOKEN


def test_connect_falls_back_to_the_authorization_header() -> None:
    assert realtime.connect_token({"HTTP_AUTHORIZATION": f"Bearer {TOKEN}"}) == TOKEN


def test_query_token_does_not_authenticate_a_real_connection(repo) -> None:
    client = TestClient(main.app)
    try:
        handshake = client.get(f"{POLLING_URL}&token={TOKEN}", headers=MOZ)
        assert handshake.status_code == 200
        sid = json.loads(handshake.text[1:])["sid"]

        client.post(
            f"{POLLING_URL}&sid={sid}",
            content="40",
            headers={**MOZ, "content-type": "text/plain;charset=UTF-8"},
        )
        packet = client.get(f"{POLLING_URL}&sid={sid}&token={TOKEN}", headers=MOZ)
    finally:
        client.close()

    # query 里的 token 不被识别 ⇒ 连接仍是匿名 ⇒ 现在被拒绝。
    assert packet.text.startswith("44"), packet.text
    assert repo.lookups == []
