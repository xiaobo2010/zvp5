"""Web SSH 多目标（单网关 + 服务端目标目录）的离线用例。

网关部署在 API 服务器上，前端只能选目标 id；host/port/user/密钥路径全部来自
``ZVMS_SSH_TARGETS`` / ``settings.SSH_TARGETS``。本文件钉住四件事：

* 配置解析：JSON/list、非法 JSON、重复 id、缺省字段、legacy 退化；
* HTTP 契约：``GET /targets`` 只给 id/label/display，未知 target 400；
* ticket 绑定目标：ticket 里的 target 就是 WS 的落点，换不掉；
* 配置在发 ticket 之后失效时 WS 关 4500，而不是静默连默认目标。

不连真 sshd、不连真库：连接工厂与审计/仓储都换成替身。

跑法：``.venv/bin/python -m pytest tests/test_ssh_targets.py -q``
"""

from __future__ import annotations

import asyncio
import json
import logging
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import settings
from db import backends
from routers import system_ssh_router
from services import ssh_bridge
from util.object_id import get_current_user

OPERATOR = "000000000000000000000001"
APPROVER = "000000000000000000000002"
WS_PATH = "/api/system/ssh/ws"

FRONTEND_KEY = "/srv/keys/frontend_ed25519"
API_KEY = "/srv/keys/api_ed25519"

TWO_TARGETS = [
    {
        "id": "frontend",
        "label": "前端服务器",
        "host": "192.0.2.5",
        "port": 2222,
        "user": "deploy",
        "key_path": FRONTEND_KEY,
        "known_hosts": "/srv/known_hosts_frontend",
    },
    {"id": "api", "label": "API 服务器", "host": "192.0.2.6", "key_path": API_KEY},
]


# ---------------------------------------------------------------------------
# 替身：SSH 连接 + WS 客户端状态
# ---------------------------------------------------------------------------


class _FakeReader:
    """``process.stdout`` 替身：一直阻塞到 close，模拟「远端没有输出」。"""

    def __init__(self) -> None:
        self._closed = asyncio.Event()

    async def read(self, _n: int) -> bytes:
        await self._closed.wait()
        return b""

    def open_eof(self) -> None:
        self._closed.set()


class _FakeProcess:
    def __init__(self) -> None:
        self.stdout = _FakeReader()
        self.closed = False

    def change_terminal_size(self, _cols: int, _rows: int) -> None:
        return None

    async def wait(self):
        return type("Completed", (), {"exit_status": 0})()

    def close(self) -> None:
        self.closed = True
        self.stdout.open_eof()


class _FakeConnection:
    def __init__(self) -> None:
        self.process = _FakeProcess()
        self.closed = False
        self.create_kwargs: dict = {}

    async def create_process(self, **kwargs):
        self.create_kwargs = kwargs
        return self.process

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _clean_ssh_config(monkeypatch):
    """把目标配置清成「完全没配」：用例各自按需注入。"""
    for name in (
        "ZVMS_SSH_TARGETS",
        "ZVMS_SSH_DEFAULT_TARGET",
        "ZVMS_SSH_HOST",
        "ZVMS_SSH_PORT",
        "ZVMS_SSH_USER",
        "ZVMS_SSH_KEY_PATH",
        "ZVMS_SSH_KNOWN_HOSTS",
    ):
        monkeypatch.delenv(name, raising=False)
    for name in (
        "SSH_TARGETS",
        "SSH_DEFAULT_TARGET",
        "SSH_HOST",
        "SSH_PORT",
        "SSH_USER",
        "SSH_KEY_PATH",
        "SSH_KNOWN_HOSTS",
    ):
        monkeypatch.delattr(settings, name, raising=False)
    yield


@pytest.fixture(autouse=True)
def _fresh_process_state(monkeypatch):
    """ticket 与并发计数都是进程内状态，用例间必须清空。"""
    monkeypatch.setattr(ssh_bridge, "_tickets", {})
    monkeypatch.setattr(ssh_bridge, "_registry", ssh_bridge.SessionRegistry())
    yield


@pytest.fixture
def configured(monkeypatch):
    """注入两个目标：一个自定义端口 + 独立密钥，一个只有 host（走缺省）。"""
    monkeypatch.setenv(
        "ZVMS_SSH_TARGETS", json.dumps(TWO_TARGETS, ensure_ascii=False)
    )
    return ssh_bridge.load_targets()


@pytest.fixture
def api(monkeypatch, configured):
    """最小可用应用：admin 仓储/权限/双人守卫/审计全部打桩。"""
    audits: list[dict] = []
    approve_calls: list[tuple] = []

    class StubCommandLog:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def insert_log(self):
            return None

    monkeypatch.setattr(system_ssh_router, "ZVMSLog", StubCommandLog)

    class StubUsersRepo:
        async def get_by_oid(self, oid):
            return {"_id": OPERATOR, "id": "20240101", "roles": ["admin"]}

    monkeypatch.setattr(backends, "get_users_repo", lambda session=None: StubUsersRepo())

    async def permissions(user):
        return list(user.get("roles") or [])

    monkeypatch.setattr(system_ssh_router, "get_user_permissions", permissions)

    async def approve(operator, code, *, action):
        approve_calls.append((operator, code, action))
        return APPROVER

    monkeypatch.setattr(system_ssh_router, "require_dual_auth", approve)

    async def audit(
        operator_oid, *, action, detail="", log_type="system_op", level="warning"
    ):
        audits.append(
            {
                "operator": operator_oid,
                "action": action,
                "detail": detail,
                "type": log_type,
                "level": level,
            }
        )

    monkeypatch.setattr(system_ssh_router, "audit_system_op", audit)

    async def names(oids):
        return {oid: "运维管理员" for oid in oids if oid == OPERATOR}

    monkeypatch.setattr(system_ssh_router, "resolve_names", names)

    def install_ssh():
        connection = _FakeConnection()
        connected: list[ssh_bridge.SshTarget] = []

        async def factory(target):
            connected.append(target)
            return connection

        monkeypatch.setattr(ssh_bridge, "_connect_factory", factory)
        return connection, connected

    app = FastAPI()
    app.include_router(system_ssh_router.router, prefix="/api/system/ssh")
    app.dependency_overrides[get_current_user] = lambda: {"id": OPERATOR, "per": ["admin"]}

    return type(
        "Api",
        (),
        {
            "app": app,
            "client": TestClient(app),
            "audits": audits,
            "approve_calls": approve_calls,
            "install_ssh": staticmethod(install_ssh),
        },
    )()


# ---------------------------------------------------------------------------
# 1. 配置解析
# ---------------------------------------------------------------------------


def test_env_json_targets_parse_with_defaults(configured):
    assert [t.id for t in configured] == ["frontend", "api"]

    frontend, api = configured
    assert (frontend.label, frontend.host, frontend.port, frontend.user) == (
        "前端服务器",
        "192.0.2.5",
        2222,
        "deploy",
    )
    assert frontend.key_path == FRONTEND_KEY
    assert frontend.display == "deploy@192.0.2.5:2222"

    # 只配了 host 的项：端口/用户/密钥/known_hosts 沿用现有默认值。
    assert (api.port, api.user) == (ssh_bridge.DEFAULT_PORT, ssh_bridge.DEFAULT_USER)
    assert api.key_path == API_KEY
    assert api.known_hosts == ssh_bridge.DEFAULT_KNOWN_HOSTS
    assert api.display == "root@192.0.2.6"


def test_missing_optional_fields_fall_back_to_legacy_env(monkeypatch):
    monkeypatch.setenv("ZVMS_SSH_USER", "legacy-user")
    monkeypatch.setenv("ZVMS_SSH_PORT", "2022")
    monkeypatch.setenv("ZVMS_SSH_KEY_PATH", "/legacy/key")
    monkeypatch.setenv("ZVMS_SSH_TARGETS", json.dumps([{"id": "a", "host": "h1"}]))

    (target,) = ssh_bridge.load_targets()

    assert (target.port, target.user, target.key_path) == (2022, "legacy-user", "/legacy/key")


def test_settings_targets_accepts_list_and_json_string(monkeypatch):
    monkeypatch.setattr(
        settings, "SSH_TARGETS", [{"id": "a", "host": "h1"}], raising=False
    )
    assert [t.id for t in ssh_bridge.load_targets()] == ["a"]

    monkeypatch.setattr(
        settings, "SSH_TARGETS", json.dumps([{"id": "b", "host": "h2"}]), raising=False
    )
    assert [t.id for t in ssh_bridge.load_targets()] == ["b"]


def test_env_wins_over_settings(monkeypatch):
    monkeypatch.setattr(
        settings, "SSH_TARGETS", [{"id": "from-settings", "host": "h0"}], raising=False
    )
    monkeypatch.setenv("ZVMS_SSH_TARGETS", json.dumps([{"id": "from-env", "host": "h1"}]))

    assert [t.id for t in ssh_bridge.load_targets()] == ["from-env"]


def test_invalid_json_falls_back_to_legacy_and_warns(monkeypatch, caplog):
    monkeypatch.setenv("ZVMS_SSH_TARGETS", "{not-json")

    with caplog.at_level(logging.WARNING, logger="services.ssh_bridge"):
        targets = ssh_bridge.load_targets()

    (target,) = targets
    assert target.id == ssh_bridge.LEGACY_TARGET_ID == "self"
    assert target.label == "本机"
    assert target.host == ssh_bridge.DEFAULT_HOST
    assert target.display == f"root@{ssh_bridge.DEFAULT_HOST}"
    assert any("invalid JSON" in record.message for record in caplog.records)


def test_legacy_fallback_keeps_old_env_vars(monkeypatch):
    monkeypatch.setenv("ZVMS_SSH_HOST", "legacy.example.com")
    monkeypatch.setenv("ZVMS_SSH_PORT", "2200")

    (target,) = ssh_bridge.load_targets()

    assert target.id == "self" and target.label == "本机"
    assert target.host == "legacy.example.com"
    assert target.display == "root@legacy.example.com:2200"


@pytest.mark.parametrize("raw", ["", "[]", "{}", '"x"', "null"])
def test_empty_or_malformed_config_still_yields_one_target(monkeypatch, caplog, raw):
    monkeypatch.setenv("ZVMS_SSH_TARGETS", raw)

    with caplog.at_level(logging.WARNING, logger="services.ssh_bridge"):
        targets = ssh_bridge.load_targets()

    assert [t.id for t in targets] == ["self"]
    if raw in ('"x"', "null"):
        assert any("not a list" in record.message for record in caplog.records)


def test_invalid_ids_duplicates_and_missing_host_are_dropped(monkeypatch, caplog):
    monkeypatch.setenv(
        "ZVMS_SSH_TARGETS",
        json.dumps(
            [
                {"id": "UPPER", "host": "h0"},  # 大写不合法
                {"id": "-lead", "host": "h0"},  # 首字符不合法
                {"id": "x" * 33, "host": "h0"},  # 超长
                {"id": "", "host": "h0"},
                {"id": "no-host"},  # 缺 host
                {"id": "bad-port", "host": "h0", "port": "abc"},
                {"id": "bad-port", "host": "h0", "port": 70000},
                "not-an-object",
                {"id": "dup", "host": "first"},
                {"id": "dup", "host": "second"},
                {"id": "good", "host": "g1"},
            ]
        ),
    )

    with caplog.at_level(logging.WARNING, logger="services.ssh_bridge"):
        targets = ssh_bridge.load_targets()

    assert [t.id for t in targets] == ["dup", "good"]
    # 重复 id 保留第一条（配置顺序即优先级）。
    assert targets[0].host == "first"
    reasons = " ".join(record.message for record in caplog.records)
    for reason in ("invalid id", "missing host", "invalid port", "not an object", "duplicate id"):
        assert reason in reasons


def test_key_paths_never_reach_the_warning_log(monkeypatch, caplog):
    monkeypatch.setenv(
        "ZVMS_SSH_TARGETS",
        json.dumps(
            [
                {"id": "BAD", "host": "h", "key_path": FRONTEND_KEY},
                {"id": "dup", "host": "h1", "key_path": API_KEY},
                {"id": "dup", "host": "h2", "key_path": API_KEY},
            ]
        ),
    )

    with caplog.at_level(logging.WARNING, logger="services.ssh_bridge"):
        ssh_bridge.load_targets()

    joined = " ".join(record.getMessage() for record in caplog.records)
    assert FRONTEND_KEY not in joined
    assert API_KEY not in joined


def test_get_target_and_load_target_resolution(monkeypatch):
    monkeypatch.setenv("ZVMS_SSH_TARGETS", json.dumps(TWO_TARGETS))

    assert ssh_bridge.get_target("api").host == "192.0.2.6"
    assert ssh_bridge.get_target("nope") is None
    assert ssh_bridge.get_target("") is None
    assert ssh_bridge.get_target(None) is None

    # 默认目标是第一项；显式 id 优先。
    assert ssh_bridge.load_target().id == "frontend"
    assert ssh_bridge.load_target("api").id == "api"
    # 未知 id 在服务层回落默认（HTTP 入口用 get_target 判 400）。
    assert ssh_bridge.load_target("nope").id == "frontend"


def test_default_target_follows_env_then_first(monkeypatch):
    monkeypatch.setenv("ZVMS_SSH_TARGETS", json.dumps(TWO_TARGETS))
    monkeypatch.setenv("ZVMS_SSH_DEFAULT_TARGET", "api")
    assert ssh_bridge.load_target().id == "api"

    monkeypatch.setenv("ZVMS_SSH_DEFAULT_TARGET", "does-not-exist")
    assert ssh_bridge.load_target().id == "frontend"

    monkeypatch.delenv("ZVMS_SSH_DEFAULT_TARGET")
    monkeypatch.setattr(settings, "SSH_DEFAULT_TARGET", "api", raising=False)
    assert ssh_bridge.load_target().id == "api"


def test_session_open_forwards_target_and_keeps_positional_compat(monkeypatch, configured):
    connection = _FakeConnection()
    recorded: list[ssh_bridge.SshTarget] = []

    async def factory(target):
        recorded.append(target)
        return connection

    monkeypatch.setattr(ssh_bridge, "_connect_factory", factory)
    frontend = ssh_bridge.get_target("frontend")

    session = asyncio.run(ssh_bridge.SshSession.open(100, 40, target=frontend))
    assert recorded == [frontend]
    assert connection.create_kwargs["term_size"] == (100, 40)
    session.close()

    # 前两个位置参数仍是 cols/rows；不传 target 时用默认目标。
    session = asyncio.run(ssh_bridge.SshSession.open(120, 50))
    assert recorded[-1] == ssh_bridge.load_target()
    assert connection.create_kwargs["term_size"] == (120, 50)
    session.close()


# ---------------------------------------------------------------------------
# 2. HTTP 契约
# ---------------------------------------------------------------------------


def test_get_targets_requires_admin(api):
    api.app.dependency_overrides[get_current_user] = lambda: {
        "id": OPERATOR,
        "per": ["student"],
    }

    response = api.client.get("/api/system/ssh/targets")

    assert response.status_code == 403


def test_get_targets_lists_only_public_fields(api):
    response = api.client.get("/api/system/ssh/targets")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["code"] == 200
    assert body["data"]["default"] == "frontend"
    assert body["data"]["targets"] == [
        {"id": "frontend", "label": "前端服务器", "display": "deploy@192.0.2.5:2222"},
        {"id": "api", "label": "API 服务器", "display": "root@192.0.2.6"},
    ]

    dumped = json.dumps(body, ensure_ascii=False)
    for secret in (FRONTEND_KEY, API_KEY, "known_hosts_frontend"):
        assert secret not in dumped
    # host 之外的连接参数一个字段都不返回。
    for leaked in ("host", "port", "user", "key_path", "known_hosts"):
        assert leaked not in dumped


def test_session_rejects_unknown_target_before_touching_totp(api):
    response = api.client.post(
        "/api/system/ssh/session", json={"totp_code": "123456", "target": "nope"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "SSH_UNKNOWN_TARGET"
    # 未知目标不是「被批准的动作」：不消耗动态码、不发 ticket。
    assert api.approve_calls == []
    assert ssh_bridge._tickets == {}


def test_session_binds_ticket_to_selected_target(api):
    response = api.client.post(
        "/api/system/ssh/session", json={"totp_code": "123456", "target": "api"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["target"] == {"id": "api", "label": "API 服务器"}
    entry = ssh_bridge.consume_ticket(body["data"]["ticket"])
    assert entry is not None and entry.target_id == "api"

    # 不传 target：绑定默认目标，响应里也回显它。
    response = api.client.post("/api/system/ssh/session", json={"totp_code": "123456"})
    body = response.json()
    assert body["data"]["target"]["id"] == "frontend"
    entry = ssh_bridge.consume_ticket(body["data"]["ticket"])
    assert entry is not None and entry.target_id == "frontend"


# ---------------------------------------------------------------------------
# 3. WS 落点与审计
# ---------------------------------------------------------------------------


def _ticket(target_id: str | None = None) -> str:
    return ssh_bridge.issue_ticket(
        OPERATOR, APPROVER, target_id=target_id
    )[0]


def test_ws_connects_to_the_selected_target_and_audits_it(api):
    connection, connected = api.install_ssh()

    with api.client.websocket_connect(f"{WS_PATH}?ticket={_ticket('api')}") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

    assert [t.id for t in connected] == ["api"]
    assert connected[0].host == "192.0.2.6"
    assert connection.closed is True

    details = [entry["detail"] for entry in api.audits]
    assert any(detail.startswith("open") and "target=api" in detail for detail in details)
    assert any(detail.startswith("close") and "target=api" in detail for detail in details)
    assert all(entry["action"] == "web_ssh" for entry in api.audits)


def test_ws_uses_default_target_for_legacy_ticket_without_target_id(api):
    connection, connected = api.install_ssh()
    # 升级前/手工造的 ticket 没有 target_id：按默认目标处理，老语义不变。
    ssh_bridge._tickets["legacy-ticket"] = ssh_bridge.Ticket(
        operator_oid=OPERATOR,
        approver_oid=APPROVER,
        expires_at=time.monotonic() + 60,
    )

    with api.client.websocket_connect(f"{WS_PATH}?ticket=legacy-ticket") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

    assert [t.id for t in connected] == ["frontend"]
    assert connection.closed is True


def test_ws_closes_4500_when_ticket_target_disappeared(api):
    _, connected = api.install_ssh()

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with api.client.websocket_connect(f"{WS_PATH}?ticket={_ticket('gone')}") as ws:
            frame = ws.receive_json()
            assert frame["type"] == "error"
            # 笼统提示：不暴露「哪个配置项不存在」或任何连接参数。
            assert "gone" not in frame["detail"]
            ws.receive_json()

    assert excinfo.value.code == ssh_bridge.WS_CLOSE_FAILURE == 4500
    assert connected == []
    assert any(
        entry["detail"].startswith("reject:target") for entry in api.audits
    )


def test_ws_does_not_leak_key_paths_on_connect_failure(api, monkeypatch, caplog):
    async def boom(target):
        raise OSError(f"cannot read {target.key_path}")

    monkeypatch.setattr(ssh_bridge, "_connect_factory", boom)

    with caplog.at_level(logging.ERROR, logger="routers.system_ssh_router"):
        with pytest.raises(WebSocketDisconnect) as excinfo:
            with api.client.websocket_connect(f"{WS_PATH}?ticket={_ticket('api')}") as ws:
                frame = ws.receive_json()
                assert frame == {"type": "error", "detail": "终端连接失败"}
                ws.receive_json()

    assert excinfo.value.code == 4500
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert API_KEY not in joined
    assert "OSError" in joined
