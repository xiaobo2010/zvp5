from types import SimpleNamespace
from datetime import datetime

import pytest

from db.repos.oracle.app_logs import OracleAppLogsRepo
from typings import log


def test_default_timestamp_is_evaluated_per_log(monkeypatch):
    times = iter([datetime(2026, 10, 17, 1), datetime(2026, 10, 17, 2)])
    monkeypatch.setattr(log, "datetime", SimpleNamespace(now=lambda: next(times)))
    assert log.ZVMSLog().timestamp != log.ZVMSLog().timestamp


@pytest.mark.asyncio
async def test_unicode_audit_data_is_split_without_loss_and_headers_fit_columns():
    rows = []

    async def execute(statement):
        rows.append(statement.compile().params)

    session = SimpleNamespace(execute=execute)
    text = "用户批量修改记录🙂" * 500
    await OracleAppLogsRepo(session).insert("x" * 600, "actor", text, 1.0, "change", "info", "9" * 100)
    assert "".join(row["data"] for row in rows) == text
    assert all(len(row["data"].encode()) <= 2000 for row in rows)
    assert all(len(row["url"].encode()) <= 512 and len(row["xuehai_id"]) <= 32 for row in rows)
