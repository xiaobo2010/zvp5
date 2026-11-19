"""shield 中间件的 418 路径不再等待审计写库。

外部爬虫 / 探针能把这个分支变成 DB 写放大：原先 ``await insert_log()`` 之后
才回 418。这里钉住：

* 418 立即返回（审计再慢也不拖响应）；
* 审计失败只记 ``logger.warning``，不影响响应，也不往 stdout 打 print；
* 触发条件（``SEC_PATTERN`` / UA 不含 Mozilla / 方法白名单）不变。
"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi.testclient import TestClient

import main

MOZ = {"User-Agent": "Mozilla/5.0 (lane2 harness)"}
CURL = {"User-Agent": "curl/8.5.0"}
PROBE_URL = "/api/__probe_lane2_not_exempt__"


class _FailingLog:
    def __init__(self, **_kwargs) -> None:
        pass

    async def insert_log(self) -> None:
        raise RuntimeError("insert failed: INSERT INTO app_logs ...")


def test_audit_failure_is_a_debuggable_warning_not_a_print(monkeypatch, caplog, capsys):
    with caplog.at_level(logging.WARNING, logger="main"):
        asyncio.run(main._write_security_audit(_FailingLog()))

    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("security alert audit failed" in message for message in messages)
    assert capsys.readouterr().out == ""


def test_418_is_returned_without_waiting_for_the_audit_write(monkeypatch):
    class _SlowLog:
        def __init__(self, **_kwargs) -> None:
            pass

        async def insert_log(self) -> None:
            await asyncio.sleep(5)

    monkeypatch.setattr(main, "ZVMSLog", _SlowLog)

    started = time.monotonic()
    client = TestClient(main.app)
    try:
        response = client.get(PROBE_URL, headers=CURL)
    finally:
        client.close()
    elapsed = time.monotonic() - started

    assert response.status_code == 418
    assert elapsed < 1.0


def test_audit_failure_does_not_change_the_418(monkeypatch):
    monkeypatch.setattr(main, "ZVMSLog", _FailingLog)

    client = TestClient(main.app)
    try:
        blocked = client.get(PROBE_URL, headers=CURL)
        # 触发条件不变：SEC_PATTERN 命中 + Mozilla UA，仍然是 418。
        pattern = client.get("/.env", headers=MOZ)
        allowed = client.get(PROBE_URL, headers=MOZ)
    finally:
        client.close()

    assert blocked.status_code == 418
    assert blocked.json() == main.SHIELD_BLOCKED_MESSAGE
    assert pattern.status_code == 418
    assert allowed.status_code == 404


def test_audit_url_drops_the_query_string(monkeypatch):
    """418 请求里的 query（可能带 token / 凭据）不得写进 APP_LOGS。"""
    captured: list[dict] = []

    class _RecordingLog:
        def __init__(self, **kwargs) -> None:
            captured.append(kwargs)

        async def insert_log(self) -> None:
            return None

    monkeypatch.setattr(main, "ZVMSLog", _RecordingLog)

    client = TestClient(main.app)
    try:
        response = client.get(f"{PROBE_URL}?token=super-secret-token", headers=CURL)
    finally:
        client.close()

    assert response.status_code == 418
    assert captured and captured[0]["url"] == PROBE_URL
    assert "super-secret-token" not in str(captured)
