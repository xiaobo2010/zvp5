"""客户端来源（IP / UA）的采集与落库契约（离线，不连库）。

三件事必须同时成立才算把「这条日志从哪来」留住：

* ``client_ip_from`` 优先 ``X-Forwarded-For`` 首个非空段（生产在 nginx 后，
  socket 对端恒为回环），没有该头才回落到对端地址，两者都没有给空串。
* 写路径把两列真正落库（``_insert_stmt`` 的 values 不是摆设）。
* 读路径把两列透出（``_log_row``），否则库里存了前端也看不到。
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.requests import Request

from db.repos.oracle import app_logs as app_logs_repo
from services import log_events
from typings.log import (
    CLIENT_IP_MAX_LEN,
    USER_AGENT_MAX_LEN,
    ZVMSLog,
    client_ip_from,
    user_agent_from,
)


def _request(headers: dict[str, str] | None = None, client: Any = ("10.0.0.9", 51234)) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw, "client": client, "method": "GET", "path": "/"})


class _RecordingSession:
    def __init__(self) -> None:
        self.statements: list[Any] = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return None


# ---------------------------------------------------------------------------
# 采集
# ---------------------------------------------------------------------------


def test_client_ip_prefers_the_first_non_empty_forwarded_segment():
    request = _request({"X-Forwarded-For": " 203.0.113.7 , 10.0.0.2, 10.0.0.3"})

    assert client_ip_from(request) == "203.0.113.7"


def test_client_ip_falls_back_to_peer_host_without_the_header():
    request = _request({}, client=("198.51.100.4", 443))

    assert client_ip_from(request) == "198.51.100.4"


def test_client_ip_is_empty_when_there_is_no_client():
    request = _request({}, client=None)

    assert client_ip_from(request) == ""


def test_user_agent_is_truncated_to_the_column_width():
    request = _request({"User-Agent": "x" * (USER_AGENT_MAX_LEN + 40)})

    assert len(user_agent_from(request)) == USER_AGENT_MAX_LEN


def test_client_ip_is_truncated_to_the_column_width():
    request = _request({"X-Forwarded-For": "1" * (CLIENT_IP_MAX_LEN + 10)})

    assert len(client_ip_from(request)) == CLIENT_IP_MAX_LEN


# ---------------------------------------------------------------------------
# 写入 / 读取
# ---------------------------------------------------------------------------


def test_insert_statement_carries_client_ip_and_user_agent():
    stmt = app_logs_repo._insert_stmt(
        "0" * 24, "/api/users/auth", "0" * 24, "登录", None, 1.5, "user_login", "info",
        "20240001", "203.0.113.7", "Mozilla/5.0",
    )

    params = stmt.compile().params

    assert "203.0.113.7" in params.values()
    assert "Mozilla/5.0" in params.values()


@pytest.mark.asyncio
async def test_repo_insert_writes_the_two_columns():
    session = _RecordingSession()
    repo = app_logs_repo.OracleAppLogsRepo(session=session)

    await repo.insert(
        "/api/users/auth", "0" * 24, "登录", 1.5, "user_login", "info", "20240001",
        "203.0.113.7", "Mozilla/5.0",
    )

    assert len(session.statements) == 1
    params = session.statements[0].compile().params

    assert "203.0.113.7" in params.values()
    assert "Mozilla/5.0" in params.values()


def test_log_row_exposes_client_ip_and_user_agent():
    row = app_logs_repo._log_row(
        {
            "oid": "0" * 24,
            "url": "/api/users/auth",
            "app_user": "0" * 24,
            "data": "登录",
            "ts_at": None,
            "ts_raw": 1.5,
            "ltype": "user_login",
            "log_level": "info",
            "xuehai_id": "20240001",
            "client_ip": "203.0.113.7",
            "user_agent": "Mozilla/5.0",
        }
    )

    assert row["client_ip"] == "203.0.113.7"
    assert row["user_agent"] == "Mozilla/5.0"


def test_log_row_tolerates_rows_without_the_new_columns():
    """历史行（迁移前的数据）两列为 NULL，读路径不能因此 KeyError。"""
    row = app_logs_repo._log_row(
        {
            "oid": "0" * 24,
            "url": "/api/users/auth",
            "app_user": "0" * 24,
            "data": "登录",
            "ts_at": None,
            "ts_raw": 1.5,
            "ltype": "user_login",
            "log_level": "info",
            "xuehai_id": "20240001",
        }
    )

    assert row["client_ip"] is None
    assert row["user_agent"] is None


def test_log_columns_project_the_new_fields():
    columns = {str(column) for column in app_logs_repo._log_columns()}

    assert "app_logs.client_ip" in columns
    assert "app_logs.user_agent" in columns


# ---------------------------------------------------------------------------
# ZVMSLog 装配
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zvmslog_passes_client_fields_to_the_repo(monkeypatch):
    captured: dict[str, Any] = {}

    class _Repo:
        async def insert(self, *args):
            captured["args"] = args

    monkeypatch.setattr(
        "db.backends.get_applogs_repo", lambda session=None: _Repo()
    )

    await ZVMSLog(
        "/api/users/auth", "0" * 24, "登录", 1.5,
        type=log_events.USER_LOGIN, client_ip="203.0.113.7", user_agent="Mozilla/5.0",
    ).insert_log()

    assert captured["args"][7] == "203.0.113.7"
    assert captured["args"][8] == "Mozilla/5.0"


def test_event_constants_match_the_written_strings():
    """常量是「已经在写」的事件名的唯一来源，值必须与历史字面量逐字相同。"""
    assert log_events.USER_LOGIN == "user_login"
    assert log_events.LOGIN_FAIL == "login_fail"
    assert log_events.USER_LOGOUT == "user_logout"
    assert log_events.AI_QUOTA_REQUEST_APPROVE == "ai_quota_request_approve"
    assert log_events.AI_QUOTA_REQUEST_REJECT == "ai_quota_request_reject"
    assert log_events.QUOTA_DECISION_EVENTS == {
        "approved": "ai_quota_request_approve",
        "rejected": "ai_quota_request_reject",
    }
