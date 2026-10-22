from types import SimpleNamespace
from datetime import datetime

import pytest
from sqlalchemy.sql import visitors
from sqlalchemy.sql.elements import BindParameter

from db.repos.oracle import app_logs as app_logs_module
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


# ---------------------------------------------------------------------------
# 保留期清理的豁免名单
# ---------------------------------------------------------------------------


def _purge_predicate_literals(cutoff: datetime) -> set[str]:
    """保留期谓词里出现的字符串字面量（即 ``ltype NOT IN (...)`` 的名单）。"""
    values: set[str] = set()
    for condition in app_logs_module._expired_conds(cutoff):
        for element in visitors.iterate(condition):
            if not isinstance(element, BindParameter):
                continue
            value = element.value
            if isinstance(value, (tuple, list, set)):
                values.update(item for item in value if isinstance(item, str))
            elif isinstance(value, str):
                values.add(value)
    return values


def test_ssh_command_logs_are_never_purged() -> None:
    """SSH 命令全文是 root 级操作的追责证据，必须落在清理谓词之外。

    它写的是 ``level="info"``（``routers/system_ssh_router.py``），唯一挡在
    保留期前面就是 ``PROTECTED_LTYPES``。
    """
    protected = _purge_predicate_literals(datetime(2026, 10, 17, 0, 0, 0))

    assert "system_ssh_command" in protected
    # 反证：普通登录日志可以被清理，说明取到的是真实谓词而不是空集合。
    assert "user_login" not in protected
