"""Stage 2 Web SSH 终端的离线用例。

不连真 sshd、不连真库：SSH 连接经 ``ssh_bridge._connect_factory`` 换成替身，
双人守卫与审计在路由命名空间打桩。覆盖契约 §2.2-§2.4：ticket 生命周期、
WS 协议帧、并发/尺寸硬约束、审计不含终端内容。

跑法：``.venv/bin/python -m pytest tests/test_v4242_ssh.py -q``
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from db import backends
from routers import system_ssh_router
from services import ssh_bridge
from util.dual_auth import DualAuthError
from util.object_id import get_current_user

OPERATOR = "000000000000000000000001"
APPROVER = "000000000000000000000002"
WS_PATH = "/api/system/ssh/ws"


class FakeReader:
    """``process.stdout`` 替身：先吐预置分片，之后阻塞到 close 才返回 EOF。"""

    def __init__(self, chunks=(), eof: bool = False) -> None:
        self._chunks = list(chunks)
        self._eof = eof
        self._closed = asyncio.Event()
        self.read_sizes: list[int] = []

    async def read(self, n: int) -> bytes:
        self.read_sizes.append(n)
        if self._chunks:
            return self._chunks.pop(0)
        if self._eof:
            return b""
        await self._closed.wait()
        return b""

    def open_eof(self) -> None:
        self._closed.set()


class FakeWriter:
    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.drains = 0

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        self.drains += 1


class FakeProcess:
    def __init__(self, chunks=(), eof: bool = False) -> None:
        self.stdout = FakeReader(chunks, eof=eof)
        self.stdin = FakeWriter()
        self.sizes: list[tuple[int, int]] = [(ssh_bridge.DEFAULT_COLS, ssh_bridge.DEFAULT_ROWS)]
        self.closed = False

    def change_terminal_size(self, cols: int, rows: int) -> None:
        self.sizes.append((cols, rows))

    async def wait(self):
        return type("Completed", (), {"exit_status": 0})()

    def close(self) -> None:
        self.closed = True
        self.stdout.open_eof()


class FakeConnection:
    def __init__(self, chunks=(), eof: bool = False) -> None:
        self.process = FakeProcess(chunks, eof=eof)
        self.closed = False
        self.create_kwargs: dict = {}

    async def create_process(self, **kwargs):
        self.create_kwargs = kwargs
        return self.process

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def _fresh_process_state(monkeypatch):
    """ticket 与并发计数都是进程内状态，用例间必须清空。"""
    monkeypatch.setattr(ssh_bridge, "_tickets", {})
    monkeypatch.setattr(ssh_bridge, "_registry", ssh_bridge.SessionRegistry())
    yield


@pytest.fixture
def env(monkeypatch):
    audits: list[dict] = []
    commands: list[dict] = []

    class StubCommandLog:
        """``ZVMSLog`` 替身：命令审计在后台任务里写，落进列表供断言。"""

        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def insert_log(self):
            commands.append(self.kwargs)

    monkeypatch.setattr(system_ssh_router, "ZVMSLog", StubCommandLog)

    class StubUsersRepo:
        async def get_by_oid(self, oid):
            return {"_id": OPERATOR, "id": "20240101", "roles": ["admin"]}

        async def get_names(self, oids):
            return {oid: "运维管理员" for oid in oids if oid == OPERATOR}

    monkeypatch.setattr(backends, "get_users_repo", lambda session=None: StubUsersRepo())

    async def permissions(user):
        return list(user.get("roles") or [])

    monkeypatch.setattr(system_ssh_router, "get_user_permissions", permissions)

    async def approve(operator, code, *, action):
        approve.calls.append((operator, code, action))
        return APPROVER

    approve.calls = []
    monkeypatch.setattr(system_ssh_router, "require_dual_auth", approve)

    async def audit(operator_oid, *, action, detail="", log_type="system_op", level="warning"):
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

    def install_ssh(chunks=(), eof: bool = False):
        connection = FakeConnection(chunks, eof=eof)

        async def factory(target):
            connection.target = target
            return connection

        monkeypatch.setattr(ssh_bridge, "_connect_factory", factory)
        return connection

    app = FastAPI()
    app.include_router(system_ssh_router.router, prefix="/api/system/ssh")
    app.dependency_overrides[get_current_user] = lambda: {"id": OPERATOR, "per": ["admin"]}

    return SimpleNamespace(
        app=app,
        client=TestClient(app),
        audits=audits,
        commands=commands,
        approve=approve,
        install_ssh=install_ssh,
    )


def _ticket(ttl_seconds=None) -> str:
    return ssh_bridge.issue_ticket(OPERATOR, APPROVER, ttl_seconds=ttl_seconds)[0]


# ---------------------------------------------------------------------------
# 1. POST /session
# ---------------------------------------------------------------------------


def test_session_requires_login():
    """未带凭据：认证依赖直接 401（端点存在，不是 404）。"""
    app = FastAPI()
    app.include_router(system_ssh_router.router, prefix="/api/system/ssh")

    response = TestClient(app).post(
        "/api/system/ssh/session", json={"totp_code": "123456"}
    )

    assert response.status_code == 401


def test_session_rejects_non_admin(env):
    env.app.dependency_overrides[get_current_user] = lambda: {
        "id": OPERATOR,
        "per": ["student"],
    }

    response = env.client.post("/api/system/ssh/session", json={"totp_code": "123456"})

    assert response.status_code == 403
    assert response.json()["detail"] == "权限不足"
    assert env.approve.calls == []


def test_session_propagates_dual_auth_rejection(env, monkeypatch):
    async def reject(operator, code, *, action):
        raise DualAuthError(403, "动态码无效或已过期")

    monkeypatch.setattr(system_ssh_router, "require_dual_auth", reject)

    response = env.client.post("/api/system/ssh/session", json={"totp_code": "000000"})

    assert response.status_code == 403
    assert response.json()["detail"] == "动态码无效或已过期"


def test_session_issues_single_use_ticket(env):
    response = env.client.post("/api/system/ssh/session", json={"totp_code": "123456"})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["code"] == 200
    assert body["data"]["expires_in"] == 120
    ticket = body["data"]["ticket"]
    assert isinstance(ticket, str) and len(ticket) >= 32

    # 发放 ticket 用的动作标识固定为 web_ssh（审计可按此回溯）。
    assert [call[2] for call in env.approve.calls] == ["web_ssh"]

    entry = ssh_bridge.consume_ticket(ticket)
    assert entry is not None and entry.operator_oid == OPERATOR
    assert entry.approver_oid == APPROVER
    # 没传 target：ticket 绑定默认目标（WS 侧只认 ticket 里的落点）。
    assert entry.target_id == ssh_bridge.load_target().id
    # 单次消费：第二次必然拿不到。
    assert ssh_bridge.consume_ticket(ticket) is None


# ---------------------------------------------------------------------------
# 2. ticket 生命周期与 WS 关闭码
# ---------------------------------------------------------------------------


def test_expired_ticket_closes_ws_with_4401(env):
    env.install_ssh()
    expired = _ticket(ttl_seconds=-1)

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with env.client.websocket_connect(f"{WS_PATH}?ticket={expired}") as ws:
            ws.receive_json()

    assert excinfo.value.code == ssh_bridge.WS_CLOSE_UNAUTHORIZED == 4401


def test_reused_ticket_closes_ws_with_4401(env):
    env.install_ssh()
    ticket = _ticket()

    with env.client.websocket_connect(f"{WS_PATH}?ticket={ticket}"):
        pass

    with pytest.raises(WebSocketDisconnect) as excinfo:
        with env.client.websocket_connect(f"{WS_PATH}?ticket={ticket}") as ws:
            ws.receive_json()

    assert excinfo.value.code == 4401


def test_missing_ticket_closes_ws_with_4401(env):
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with env.client.websocket_connect(WS_PATH) as ws:
            ws.receive_json()

    assert excinfo.value.code == 4401


# ---------------------------------------------------------------------------
# 3. WS 协议
# ---------------------------------------------------------------------------


def test_input_is_forwarded_and_resize_applies_and_ping_gets_pong(env):
    connection = env.install_ssh()

    with env.client.websocket_connect(f"{WS_PATH}?ticket={_ticket()}") as ws:
        ws.send_json({"type": "input", "data": "ls\n"})
        ws.send_json({"type": "resize", "cols": 120, "rows": 32})
        ws.send_json({"type": "ping"})
        # 帧按序处理 ⇒ 收到 pong 就说明前两帧已经生效。
        assert ws.receive_json() == {"type": "pong"}

        process = connection.process
        assert process.stdin.written == [b"ls\n"]
        assert process.stdin.drains == 1
        assert process.sizes[-1] == (120, 32)

    assert connection.closed is True
    assert connection.process.closed is True


def test_initial_process_uses_pty(env):
    connection = env.install_ssh()

    with env.client.websocket_connect(f"{WS_PATH}?ticket={_ticket()}") as ws:
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

    kwargs = connection.create_kwargs
    assert kwargs["term_type"] == "xterm-256color"
    assert kwargs["term_size"] == (ssh_bridge.DEFAULT_COLS, ssh_bridge.DEFAULT_ROWS)
    # 目标只来自服务端配置：没传 target 时就是默认目标，客户端无法指定。
    assert connection.target == ssh_bridge.load_target()
    assert connection.target.id == ssh_bridge.load_target().id


def test_output_is_split_into_bounded_chunks(env):
    connection = env.install_ssh(chunks=[b"hello", b" world"])

    with env.client.websocket_connect(f"{WS_PATH}?ticket={_ticket()}") as ws:
        assert ws.receive_json() == {"type": "output", "data": "hello"}
        assert ws.receive_json() == {"type": "output", "data": " world"}

    assert connection.process.stdout.read_sizes
    assert all(size == ssh_bridge.MAX_OUTPUT_CHUNK_BYTES for size in connection.process.stdout.read_sizes)


def test_oversized_input_is_rejected_not_forwarded(env):
    connection = env.install_ssh()

    with env.client.websocket_connect(f"{WS_PATH}?ticket={_ticket()}") as ws:
        ws.send_json({"type": "input", "data": "a" * (ssh_bridge.MAX_INPUT_BYTES + 1)})
        ws.send_json({"type": "ping"})
        error = ws.receive_json()
        assert error["type"] == "error" and "8KB" in error["detail"]
        assert ws.receive_json() == {"type": "pong"}

    assert connection.process.stdin.written == []


def test_shell_exit_sends_exit_frame_and_closes(env):
    env.install_ssh(chunks=[b"bye\r\n"], eof=True)

    with env.client.websocket_connect(f"{WS_PATH}?ticket={_ticket()}") as ws:
        assert ws.receive_json() == {"type": "output", "data": "bye\r\n"}
        assert ws.receive_json() == {"type": "exit", "code": 0}
        with pytest.raises(WebSocketDisconnect) as excinfo:
            ws.receive_json()

    assert excinfo.value.code == 1000


def test_unknown_message_type_returns_error_frame(env):
    env.install_ssh()

    with env.client.websocket_connect(f"{WS_PATH}?ticket={_ticket()}") as ws:
        ws.send_json({"type": "shutdown"})
        ws.send_json({"type": "ping"})
        assert ws.receive_json()["type"] == "error"
        assert ws.receive_json() == {"type": "pong"}


def test_third_session_for_same_operator_is_denied(env):
    env.install_ssh()
    tickets = [_ticket() for _ in range(3)]

    with env.client.websocket_connect(f"{WS_PATH}?ticket={tickets[0]}"):
        with env.client.websocket_connect(f"{WS_PATH}?ticket={tickets[1]}"):
            with env.client.websocket_connect(
                f"{WS_PATH}?ticket={tickets[2]}"
            ) as ws:
                assert ws.receive_json() == {
                    "type": "error",
                    "detail": "并发会话已达上限",
                }
                with pytest.raises(WebSocketDisconnect) as excinfo:
                    ws.receive_json()

    assert excinfo.value.code == ssh_bridge.WS_CLOSE_LIMIT == 4429
    # 被拒的会话不占配额：前两条退出后计数归零。
    assert ssh_bridge.registry().snapshot() == (0, ssh_bridge.MAX_SESSIONS_TOTAL)


# ---------------------------------------------------------------------------
# 4. 审计
# ---------------------------------------------------------------------------


def test_connect_and_close_are_audited_without_terminal_content(env):
    """open/close 审计只写会话事件；命令原文走单独的 ``system_ssh_command``。"""
    env.install_ssh(chunks=[b"TOPSECRET-OUTPUT\n"])

    with env.client.websocket_connect(f"{WS_PATH}?ticket={_ticket()}") as ws:
        assert ws.receive_json() == {"type": "output", "data": "TOPSECRET-OUTPUT\n"}
        ws.send_json({"type": "input", "data": "secret-input\n"})
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

    details = [entry["detail"] for entry in env.audits]
    assert any(detail.startswith("open") for detail in details)
    assert any(detail.startswith("close") for detail in details)
    assert all(entry["type"] == "system_ssh" for entry in env.audits)
    assert all(entry["action"] == "web_ssh" for entry in env.audits)
    assert all(entry["operator"] == OPERATOR for entry in env.audits)

    joined = " ".join(details)
    assert "TOPSECRET" not in joined
    assert "secret-input" not in joined


def test_executed_commands_are_audited_as_their_own_log_type(env):
    """命令原文进 ``system_ssh_command``：这是审计要求，不是泄露。"""
    env.install_ssh()

    with env.client.websocket_connect(f"{WS_PATH}?ticket={_ticket()}") as ws:
        ws.send_json({"type": "input", "data": "echo hi\r"})
        ws.send_json({"type": "input", "data": "cd /tmp\npwd\r"})
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

    payloads = [json.loads(entry["data"]) for entry in env.commands]
    assert [payload["command"] for payload in payloads] == [
        "echo hi",
        "cd /tmp",
        "pwd",
    ]
    for entry in env.commands:
        assert entry["url"] == "system:web_ssh"
        assert entry["type"] == "system_ssh_command"
        assert entry["level"] == "info"
        # ``user`` 保持 oid：日志列表靠 users 连接出显示名。
        assert entry["user"] == OPERATOR
    assert payloads[0] == {
        "command": "echo hi",
        "operator": OPERATOR,
        "operator_name": "运维管理员",
    }


def test_unfinished_line_is_not_audited_as_a_command(env):
    """没按回车的半行远端没有执行，不该留成命令。"""
    env.install_ssh()

    with env.client.websocket_connect(f"{WS_PATH}?ticket={_ticket()}") as ws:
        ws.send_json({"type": "input", "data": "echo half"})
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

    assert env.commands == []


def test_command_audit_failure_never_breaks_the_terminal(env, monkeypatch):
    """审计是旁路：写库炸了终端照样能用。"""
    env.install_ssh()

    class BrokenLog:
        def __init__(self, **kwargs) -> None:
            pass

        async def insert_log(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(system_ssh_router, "ZVMSLog", BrokenLog)

    with env.client.websocket_connect(f"{WS_PATH}?ticket={_ticket()}") as ws:
        ws.send_json({"type": "input", "data": "ls\r"})
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

