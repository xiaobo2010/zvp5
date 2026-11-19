"""失败路径必须走 logging（级别/脱敏可控），不再往 stdout 打 ``print``。

涉及六处：Web SSH 的输出泵 / 连接失败 / 命令审计写失败，双人授权的取名失败 /
授权审计写失败 / 普通操作审计写失败。这些路径以前用 ``print``，既没有级别，
也没法在部署侧按 logger 过滤。

同时钉住脱敏：命令原文、票据、异常里的私钥路径都**不得**出现在日志或
stdout 里 —— 只记「阶段 + 异常类型」。

从仓库根运行::

    ./.venv/bin/python -m pytest tests/test_failure_paths_logging.py -q
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace


from routers import system_ssh_router
from util import dual_auth


class _FailingLog:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def with_text(self, *_args, **_kwargs):
        return self

    async def insert_log(self) -> None:
        raise RuntimeError("insert failed: INSERT INTO app_logs ...")


class _FakeUsersRepo:
    async def get_names(self, _oids):
        raise RuntimeError("users repo unavailable")


class _FakeUsersRepoOk:
    async def get_names(self, oids):
        return {oid: "名字" for oid in oids}


def _messages(caplog, level: int) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == level]


# ---------------------------------------------------------------------------
# util.dual_auth
# ---------------------------------------------------------------------------


def test_resolve_names_failure_is_a_warning_not_a_print(monkeypatch, caplog, capsys) -> None:
    monkeypatch.setattr(dual_auth.backends, "get_users_repo", lambda *a, **k: _FakeUsersRepo())

    with caplog.at_level(logging.WARNING, logger="util.dual_auth"):
        names = asyncio.run(dual_auth.resolve_names(["64b7f0c2e1a3b4c5d6e7aa01"]))

    assert names == {}
    assert any("resolve names" in message for message in _messages(caplog, logging.WARNING))
    assert capsys.readouterr().out == ""


def test_dual_auth_audit_write_failure_is_a_warning(monkeypatch, caplog, capsys) -> None:
    monkeypatch.setattr(dual_auth.backends, "get_users_repo", lambda *a, **k: _FakeUsersRepoOk())
    monkeypatch.setattr(dual_auth, "ZVMSLog", _FailingLog)

    with caplog.at_level(logging.WARNING, logger="util.dual_auth"):
        asyncio.run(
            dual_auth._write_audit(
                action="web_ssh",
                operator_oid="64b7f0c2e1a3b4c5d6e7aa01",
                approver_oid="64b7f0c2e1a3b4c5d6e7aa02",
            )
        )

    messages = _messages(caplog, logging.WARNING)
    assert any("dual-auth audit" in message for message in messages)
    assert capsys.readouterr().out == ""


def test_system_op_audit_write_failure_is_a_warning(monkeypatch, caplog, capsys) -> None:
    monkeypatch.setattr(dual_auth.backends, "get_users_repo", lambda *a, **k: _FakeUsersRepoOk())
    monkeypatch.setattr(dual_auth, "ZVMSLog", _FailingLog)

    with caplog.at_level(logging.WARNING, logger="util.dual_auth"):
        asyncio.run(
            dual_auth.audit_system_op(
                "64b7f0c2e1a3b4c5d6e7aa01", action="db_ddl", detail="DROP TABLE demo"
            )
        )

    messages = _messages(caplog, logging.WARNING)
    assert any("system op audit" in message for message in messages)
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# routers.system_ssh_router
# ---------------------------------------------------------------------------


def test_command_audit_failure_is_logged_without_the_command(
    monkeypatch, caplog, capsys
) -> None:
    monkeypatch.setattr(system_ssh_router, "ZVMSLog", _FailingLog)

    with caplog.at_level(logging.ERROR, logger="routers.system_ssh_router"):
        asyncio.run(
            system_ssh_router._write_command_audit(
                "64b7f0c2e1a3b4c5d6e7aa01", "运维", "mysql -uroot -pS3cr3t"
            )
        )

    messages = _messages(caplog, logging.ERROR)
    assert any("command audit" in message for message in messages)
    joined = " ".join(messages)
    assert "S3cr3t" not in joined
    assert capsys.readouterr().out == ""


class _FakeSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.closed: tuple[int, str] | None = None

    async def accept(self) -> None:
        return None

    async def send_json(self, payload: dict) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)


class _BoomSession:
    async def read_output(self):
        raise OSError("channel closed: /root/.ssh/id_ed25519")


def test_output_pump_failure_logs_the_type_only(monkeypatch, caplog, capsys) -> None:
    socket = _FakeSocket()

    with caplog.at_level(logging.ERROR, logger="routers.system_ssh_router"):
        asyncio.run(system_ssh_router._pump_output(socket, _BoomSession()))

    messages = _messages(caplog, logging.ERROR)
    assert any("OSError" in message for message in messages)
    assert "id_ed25519" not in " ".join(messages)
    assert socket.closed == (
        system_ssh_router.ssh_bridge.WS_CLOSE_FAILURE,
        "终端连接已断开",
    )
    assert capsys.readouterr().out == ""


def test_connect_failure_is_logged_and_returns_a_generic_error(
    monkeypatch, caplog, capsys
) -> None:
    async def _boom(*_args, **_kwargs):
        raise OSError("cannot read /root/.ssh/id_rsa")

    class _Registry:
        def acquire(self, _oid):
            return True

        def release(self, _oid):
            return None

    async def _audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        system_ssh_router.ssh_bridge,
        "consume_ticket",
        lambda _ticket: SimpleNamespace(
            operator_oid="64b7f0c2e1a3b4c5d6e7aa01",
            approver_oid="64b7f0c2e1a3b4c5d6e7aa02",
        ),
    )
    monkeypatch.setattr(system_ssh_router.ssh_bridge, "registry", lambda: _Registry())
    monkeypatch.setattr(
        system_ssh_router.ssh_bridge.SshSession, "open", staticmethod(_boom)
    )
    monkeypatch.setattr(system_ssh_router, "audit_system_op", _audit)
    socket = _FakeSocket()

    with caplog.at_level(logging.ERROR, logger="routers.system_ssh_router"):
        asyncio.run(system_ssh_router.open_terminal(socket, ticket="one-shot-ticket"))

    messages = _messages(caplog, logging.ERROR)
    assert any("connect failed" in message and "OSError" in message for message in messages)
    joined = " ".join(messages)
    assert "id_rsa" not in joined
    assert "one-shot-ticket" not in joined
    assert {"type": "error", "detail": "终端连接失败"} in socket.sent
    assert socket.closed == (
        system_ssh_router.ssh_bridge.WS_CLOSE_FAILURE,
        "终端连接失败",
    )
    assert capsys.readouterr().out == ""
