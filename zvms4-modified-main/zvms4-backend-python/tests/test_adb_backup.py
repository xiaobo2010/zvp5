"""Offline tests for the manual ADB backup endpoints (trigger + progress).

要点：

* ``POST /api/system/db/backup/run`` → 管理员专属；``BackupBusy`` → 409；service 层
  的 ``BackupTriggerError`` 由**路由层**翻成 500（断言不会被 route_class 吞成 503）；
  成功写一条 ``adb_backup`` / ``warning`` 审计行；
  service 模块因此不依赖 fastapi（端点用例同时覆盖两条路径）；
* ``GET /api/system/db/backup/progress`` → 冻结结构 + ``Cache-Control: no-store``；
* 解析器纯函数：无日志 → idle；有起点无终态 → 看 ``is-active``（stub）;``SYNC_OK``
  → ok（表数/行数/耗时）；``SYNC_FAIL`` → failed；另一实例占用 → failed/running；
  且**任何**返回里都不出现假 DSN —— 防泄漏回归。

离线：日志是 ``tmp_path`` 里的假文件、``is-active`` 与子进程都被替身接管，**绝不**
读 ``/var/log/adb-sync.log``、不执行 ``sudo``/``systemctl``、不连库。

跑法：``.venv/bin/python -m pytest tests/test_adb_backup.py -q``
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from db import backends
from db.oracle.session import get_session
from routers import db_system_router
from services import adb_backup
from util.object_id import authenticated_log_user, get_current_user

ADMIN_OID = "64b7f0c2e1a3b4c5d6e7f801"
BUSY_DETAIL = "备份任务正在执行中，请稍后重试"

RUN_URL = "/api/system/db/backup/run"
PROGRESS_URL = "/api/system/db/backup/progress"

# 假 DSN：一旦出现在响应里就是泄漏。
SECRET_HOST = "adb-secret-host.invalid"
SECRET_PASSWORD = "Hunter2Secret"
SECRET_DSN = (
    f"(description=(address=(host={SECRET_HOST})(port=1521))"
    f"(service_name=prod_low))(password={SECRET_PASSWORD})"
)

CONNECTED_LINE = (
    f"2026-10-19T03:30:45 OK connected src={SECRET_DSN} dst={SECRET_DSN} schema=ADMIN"
)
TABLE_LINES = [
    '2026-10-19T03:30:48 "ADMIN"."APP_LOGS": 1777 rows loaded (3.2s)',
    '2026-10-19T03:30:52 "ADMIN"."USERS": 350 rows loaded (4.1s)',
]
STARTED_LINES = [
    "2026-10-19T03:30:45 source tables (23): ['ACTIVITIES', 'APP_LOGS', 'USERS']",
]
OK_LINES = [
    "2026-10-19T03:44:29 COMMIT ok (868.0s)",
    "2026-10-19T03:44:39 SYNC_OK tables=23 rows=28122 elapsed=878.0s",
]

FROZEN_KEYS = {
    "state",
    "started_at",
    "finished_at",
    "tables_total",
    "tables_done",
    "rows_loaded",
    "current_table",
    "elapsed_seconds",
    "message",
}


def _log(tmp_path, lines: list[str]):
    path = tmp_path / "adb-sync.log"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _snapshot(**overrides) -> dict:
    data = {
        "state": "running",
        "started_at": "2026-10-19T03:30:45",
        "finished_at": None,
        "tables_total": 23,
        "tables_done": 2,
        "rows_loaded": 2127,
        "current_table": "USERS",
        "elapsed_seconds": None,
        "message": "备份进行中：已完成 2/23 张表",
    }
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# parser: 纯函数
# ---------------------------------------------------------------------------


def test_parse_returns_only_structured_fields():
    parsed = adb_backup.parse_backup_log(
        [CONNECTED_LINE, *STARTED_LINES, *TABLE_LINES, *OK_LINES]
    )

    assert parsed["state"] == "ok"
    assert parsed["started_at"] == "2026-10-19T03:30:45"
    assert parsed["finished_at"] == "2026-10-19T03:44:39"
    assert parsed["tables_total"] == 23
    assert parsed["tables_done"] == 2
    assert parsed["rows_loaded"] == 1777 + 350
    assert parsed["current_table"] == "USERS"
    assert parsed["elapsed_seconds"] == 878.0


def test_parse_never_echoes_the_log_text_or_dsn():
    parsed = adb_backup.parse_backup_log(
        [CONNECTED_LINE, *STARTED_LINES, *TABLE_LINES, *OK_LINES]
    )
    text = json.dumps(parsed, ensure_ascii=False)

    for leak in (SECRET_DSN, SECRET_HOST, SECRET_PASSWORD, "description", "connected"):
        assert leak not in text


def test_parse_without_start_marker_is_idle():
    parsed = adb_backup.parse_backup_log(
        ["2026-10-19T03:29:00 WARN something happened before any run"]
    )

    assert parsed["state"] == "idle"
    assert parsed["started_at"] is None and parsed["tables_done"] == 0


def test_parse_uses_the_last_connected_marker_as_the_run_start():
    lines = [
        *STARTED_LINES,
        *TABLE_LINES,
        *OK_LINES,
        CONNECTED_LINE,  # 第二次 run 的起点
        '2026-10-19T05:00:00 "ADMIN"."ACTIVITIES": 9 rows loaded (0.4s)',
    ]
    parsed = adb_backup.parse_backup_log(lines)

    assert parsed["state"] == "unsettled"
    assert parsed["started_at"] == "2026-10-19T03:30:45"
    assert parsed["tables_done"] == 1  # 只数第二次 run 之后的进度行
    assert parsed["rows_loaded"] == 9
    assert parsed["tables_total"] is None  # source tables 行在起点之前


def test_parse_ignores_unknown_table_names():
    parsed = adb_backup.parse_backup_log(
        [CONNECTED_LINE, '"ADMIN"."../ETC/PASSWD": 3 rows loaded (0.1s)']
    )

    assert parsed["tables_done"] == 0
    assert parsed["rows_loaded"] == 0
    assert parsed["current_table"] is None


# ---------------------------------------------------------------------------
# snapshot: 冻结结构 + is-active 收敛
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_without_log_is_idle(tmp_path):
    data = await adb_backup.backup_snapshot(
        log_path=tmp_path / "missing.log", unit_state="activating"
    )

    assert set(data) == FROZEN_KEYS  # 无日志不探 is-active，直接 idle
    assert data["state"] == "idle"
    assert data["started_at"] is None and data["finished_at"] is None
    assert data["tables_done"] == 0 and data["rows_loaded"] == 0
    assert data["tables_total"] is None and data["elapsed_seconds"] is None


@pytest.mark.asyncio
async def test_snapshot_of_an_empty_log_is_idle(tmp_path):
    path = tmp_path / "empty.log"
    path.write_text("", encoding="utf-8")

    data = await adb_backup.backup_snapshot(log_path=path, unit_state="inactive")

    assert data["state"] == "idle"
    assert set(data) == FROZEN_KEYS


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unit_state,expected",
    [("activating", "running"), ("active", "idle"), ("inactive", "idle"), ("", "idle")],
)
async def test_snapshot_without_terminal_follows_is_active(tmp_path, unit_state, expected):
    path = _log(tmp_path, [CONNECTED_LINE, *STARTED_LINES, *TABLE_LINES])

    data = await adb_backup.backup_snapshot(log_path=path, unit_state=unit_state)

    assert data["state"] == expected
    assert data["started_at"] == "2026-10-19T03:30:45"
    assert data["finished_at"] is None
    assert data["tables_total"] == 23
    assert data["tables_done"] == 2
    assert data["current_table"] == "USERS"
    if expected == "running":
        assert "备份进行中" in data["message"]
    else:
        assert data["message"] == "暂无备份任务运行"


@pytest.mark.asyncio
async def test_snapshot_ok_does_not_need_is_active(tmp_path, monkeypatch):
    path = _log(tmp_path, [CONNECTED_LINE, *STARTED_LINES, *TABLE_LINES, *OK_LINES])

    async def explode(unit=adb_backup.SYSTEMD_UNIT_NAME):
        raise AssertionError("is-active must not be probed for a finished run")

    # 跑完的 run 不该再花一次子进程：把探针换成会炸的实现，结果必须不变。
    monkeypatch.setattr(adb_backup, "_unit_is_active", explode)

    data = await adb_backup.backup_snapshot(log_path=path)

    assert data["state"] == "ok"
    assert data["tables_total"] == 23
    assert data["tables_done"] == 2
    assert data["rows_loaded"] == 2127
    assert data["elapsed_seconds"] == 878.0
    assert data["finished_at"] == "2026-10-19T03:44:39"
    assert "备份成功" in data["message"]


@pytest.mark.asyncio
async def test_snapshot_sync_fail_is_failed(tmp_path):
    path = _log(
        tmp_path,
        [CONNECTED_LINE, *STARTED_LINES, '2026-10-19T03:33:00 SYNC_FAIL timeout'],
    )

    data = await adb_backup.backup_snapshot(log_path=path, unit_state="inactive")

    assert data["state"] == "failed"
    assert data["finished_at"] == "2026-10-19T03:33:00"
    assert "失败" in data["message"]


@pytest.mark.asyncio
async def test_snapshot_dry_run_is_not_a_terminal(tmp_path):
    path = _log(
        tmp_path,
        [CONNECTED_LINE, "2026-10-19T03:31:00 SYNC_DRY_RUN would copy 23 tables"],
    )

    running = await adb_backup.backup_snapshot(log_path=path, unit_state="activating")
    idle = await adb_backup.backup_snapshot(log_path=path, unit_state="inactive")

    assert running["state"] == "running"
    assert idle["state"] == "idle"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unit_state,expected",
    [("activating", "running"), ("inactive", "failed"), ("failed", "failed")],
)
async def test_snapshot_contended_instance(tmp_path, unit_state, expected):
    path = _log(
        tmp_path,
        [CONNECTED_LINE, "2026-10-19T03:31:00 another sync instance running, exit"],
    )

    data = await adb_backup.backup_snapshot(log_path=path, unit_state=unit_state)

    assert data["state"] == expected
    assert "另一个备份实例" in data["message"]


@pytest.mark.asyncio
async def test_snapshot_never_leaks_dsn_end_to_end(tmp_path):
    path = _log(tmp_path, [CONNECTED_LINE, *STARTED_LINES, *TABLE_LINES, *OK_LINES])

    data = await adb_backup.backup_snapshot(log_path=path, unit_state="inactive")
    text = json.dumps(data, ensure_ascii=False)

    assert set(data) == FROZEN_KEYS
    for leak in (SECRET_DSN, SECRET_HOST, SECRET_PASSWORD, "description", "connected"):
        assert leak not in text


# ---------------------------------------------------------------------------
# trigger: 白名单命令 + 失败语义
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_refuses_while_the_unit_is_activating(monkeypatch):
    calls: list[tuple] = []

    async def is_active(unit=adb_backup.SYSTEMD_UNIT_NAME):
        return "activating"

    async def run(argv, timeout):
        calls.append(argv)
        return 0, "", ""

    monkeypatch.setattr(adb_backup, "_unit_is_active", is_active)
    monkeypatch.setattr(adb_backup, "_run_command", run)

    with pytest.raises(adb_backup.BackupBusy) as excinfo:
        await adb_backup.trigger_backup()

    assert str(excinfo.value) == BUSY_DETAIL
    assert calls == []  # 在跑就一个子进程都不发


@pytest.mark.asyncio
async def test_trigger_submits_the_whitelisted_command(monkeypatch):
    calls: list[tuple] = []
    snapshot = _snapshot()

    async def is_active(unit=adb_backup.SYSTEMD_UNIT_NAME):
        return "inactive"

    async def run(argv, timeout):
        calls.append((argv, timeout))
        return 0, "", ""

    async def fake_snapshot(**kwargs):
        return snapshot

    monkeypatch.setattr(adb_backup, "_unit_is_active", is_active)
    monkeypatch.setattr(adb_backup, "_run_command", run)
    monkeypatch.setattr(adb_backup, "backup_snapshot", fake_snapshot)

    assert await adb_backup.trigger_backup() == snapshot
    argv, timeout = calls[0]
    assert argv == (
        "sudo",
        "-n",
        "/usr/bin/systemctl",
        "start",
        "--no-block",
        "adb-sync.service",
    )
    assert 0 < timeout <= 30


@pytest.mark.asyncio
async def test_trigger_non_zero_exit_raises_a_domain_error_with_a_scrubbed_summary(
    monkeypatch,
):
    async def is_active(unit=adb_backup.SYSTEMD_UNIT_NAME):
        return "inactive"

    async def run(argv, timeout):
        return 1, "", f"sudo: a password is required\nsrc={SECRET_DSN}\n"

    monkeypatch.setattr(adb_backup, "_unit_is_active", is_active)
    monkeypatch.setattr(adb_backup, "_run_command", run)

    with pytest.raises(adb_backup.BackupTriggerError) as excinfo:
        await adb_backup.trigger_backup()

    detail = str(excinfo.value)
    assert "启动备份任务失败" in detail
    assert "<redacted>" in detail  # 摘要里的 DSN 位置被抹掉
    assert SECRET_HOST not in detail and SECRET_PASSWORD not in detail
    assert not isinstance(excinfo.value, HTTPException)


@pytest.mark.asyncio
async def test_trigger_timeout_raises_a_domain_error(monkeypatch):
    async def is_active(unit=adb_backup.SYSTEMD_UNIT_NAME):
        return "inactive"

    async def run(argv, timeout):
        raise asyncio.TimeoutError

    monkeypatch.setattr(adb_backup, "_unit_is_active", is_active)
    monkeypatch.setattr(adb_backup, "_run_command", run)

    with pytest.raises(adb_backup.BackupTriggerError) as excinfo:
        await adb_backup.trigger_backup()

    assert "超时" in str(excinfo.value)
    assert not isinstance(excinfo.value, HTTPException)


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------


class _FakeLogsRepo:
    def __init__(self) -> None:
        self.inserted: list[dict] = []

    async def insert(
        self,
        url: str,
        user: str,
        data: str,
        timestamp: float,
        ltype: str,
        level: str,
        xuehai_id: str,
    ) -> None:
        self.inserted.append(
            {
                "url": url,
                "user": user,
                "data": data,
                "timestamp": timestamp,
                "type": ltype,
                "level": level,
                "xuehai_id": xuehai_id,
            }
        )


@pytest.fixture
def logs(monkeypatch):
    repo = _FakeLogsRepo()
    monkeypatch.setattr(backends, "get_applogs_repo", lambda session=None: repo)
    return repo


@pytest.fixture
def client(monkeypatch):
    state = {"_id": ADMIN_OID, "roles": ["admin"], "banned": False}

    async def get_by_oid(oid):
        return state

    async def get_names(oids):
        return {oid: "管理员" for oid in oids}

    async def session():
        yield None

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda session=None: SimpleNamespace(
            get_by_oid=get_by_oid, get_names=get_names
        ),
    )
    app = FastAPI()
    app.include_router(db_system_router.router, prefix="/api/system/db")
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[get_current_user] = lambda: {
        "id": state["_id"],
        "per": ["admin"],
    }
    app.dependency_overrides[authenticated_log_user] = lambda: {"id": state["_id"]}
    with TestClient(app) as http:
        yield http, state


def test_backup_requires_login():
    app = FastAPI()
    app.include_router(db_system_router.router, prefix="/api/system/db")

    assert TestClient(app).post(RUN_URL).status_code == 401
    assert TestClient(app).get(PROGRESS_URL).status_code == 401


@pytest.mark.parametrize(
    "roles,banned", [(["student"], False), (["department"], False), (["admin"], True)]
)
def test_backup_rechecks_privileges_before_triggering(client, logs, monkeypatch, roles, banned):
    http, state = client
    state.update(roles=roles, banned=banned)

    async def explode(*args, **kwargs):  # pragma: no cover - 不该被调用
        raise AssertionError("must not touch the backup unit before authorization")

    monkeypatch.setattr(db_system_router, "trigger_backup", explode)
    monkeypatch.setattr(db_system_router, "backup_snapshot", explode)

    assert http.post(RUN_URL).status_code == 403
    assert http.get(PROGRESS_URL).status_code == 403
    assert logs.inserted == []


def test_backup_run_returns_the_snapshot_and_audits_the_actor(client, logs, monkeypatch):
    http, _ = client
    snapshot = _snapshot()

    async def fake_trigger():
        return snapshot

    monkeypatch.setattr(db_system_router, "trigger_backup", fake_trigger)

    response = http.post(RUN_URL)

    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ok", "code": 200, "data": snapshot}

    assert len(logs.inserted) == 1
    audit = logs.inserted[0]
    assert audit["type"] == "adb_backup"
    assert audit["level"] == "warning"
    assert audit["user"] == ADMIN_OID
    assert "管理员" in audit["data"]
    assert "ADB 手动备份" in audit["data"]


def test_backup_run_is_409_when_a_backup_is_already_running(client, logs, monkeypatch):
    http, _ = client

    async def busy():
        raise adb_backup.BackupBusy()

    monkeypatch.setattr(db_system_router, "trigger_backup", busy)

    response = http.post(RUN_URL)

    assert response.status_code == 409
    assert response.json()["detail"] == BUSY_DETAIL
    assert logs.inserted == []


def test_backup_run_translates_the_domain_error_into_500(client, logs, monkeypatch):
    """service 只抛 BackupTriggerError；路由层必须显式翻成 500（不是 503）。"""
    http, _ = client

    async def failing():
        raise adb_backup.BackupTriggerError("启动备份任务失败：sudo: a password is required")

    monkeypatch.setattr(db_system_router, "trigger_backup", failing)

    response = http.post(RUN_URL)

    assert response.status_code == 500
    assert response.json()["detail"] == "启动备份任务失败：sudo: a password is required"
    assert logs.inserted == []


def test_backup_run_500_is_not_swallowed_into_503(client, logs, monkeypatch):
    http, _ = client

    async def failing():
        raise HTTPException(status_code=500, detail="启动备份任务失败：sudo: a password is required")

    monkeypatch.setattr(db_system_router, "trigger_backup", failing)

    response = http.post(RUN_URL)

    assert response.status_code == 500
    assert "启动备份任务失败" in response.json()["detail"]
    assert logs.inserted == []


def test_backup_progress_is_frozen_and_no_store(client, monkeypatch):
    http, _ = client

    async def fake_snapshot():
        return _snapshot(state="idle", message="暂无备份任务运行")

    monkeypatch.setattr(db_system_router, "backup_snapshot", fake_snapshot)

    response = http.get(PROGRESS_URL)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["status"] == "ok" and body["code"] == 200
    assert set(body["data"]) == FROZEN_KEYS
