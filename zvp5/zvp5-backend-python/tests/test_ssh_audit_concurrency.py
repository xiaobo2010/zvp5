"""Web SSH 命令审计的并发上限。

一次粘贴 N 行会派生 N 个审计写任务。这里钉住：

* ``ZVP5_SSH_AUDIT_CONCURRENCY``（默认 8）可配，非法值回落默认；
* 并发写入数不超过信号量上限；
* 超出上限的命令排队而非丢弃（全部落库，只是顺序化了）。
"""

from __future__ import annotations

import asyncio

import pytest

from routers import system_ssh_router

OPERATOR = "000000000000000000000001"


def test_audit_concurrency_is_configurable_and_defensive(monkeypatch):
    monkeypatch.delenv("ZVP5_SSH_AUDIT_CONCURRENCY", raising=False)
    assert system_ssh_router.audit_concurrency() == 8
    assert system_ssh_router.AUDIT_CONCURRENCY_DEFAULT == 8

    monkeypatch.setenv("ZVP5_SSH_AUDIT_CONCURRENCY", "3")
    assert system_ssh_router.audit_concurrency() == 3

    for invalid in ("0", "-2", "abc", ""):
        monkeypatch.setenv("ZVP5_SSH_AUDIT_CONCURRENCY", invalid)
        assert (
            system_ssh_router.audit_concurrency()
            == system_ssh_router.AUDIT_CONCURRENCY_DEFAULT
        )


def test_command_audits_are_queued_behind_a_bounded_semaphore(monkeypatch):
    monkeypatch.setattr(system_ssh_router, "_audit_semaphore", asyncio.Semaphore(3))
    state = {"current": 0, "peak": 0, "total": 0}

    class StubLog:
        def __init__(self, **kwargs) -> None:
            self.kwargs = kwargs

        async def insert_log(self) -> None:
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
            await asyncio.sleep(0.02)
            state["current"] -= 1
            state["total"] += 1

    monkeypatch.setattr(system_ssh_router, "ZVMSLog", StubLog)

    async def run():
        pending: set[asyncio.Task] = set()
        for index in range(20):
            system_ssh_router._spawn_command_audit(
                pending, OPERATOR, "运维", f"echo {index}"
            )
        await asyncio.gather(*pending)
        await asyncio.sleep(0)

    asyncio.run(run())

    assert state["peak"] == 3
    assert state["total"] == 20
    assert state["current"] == 0
