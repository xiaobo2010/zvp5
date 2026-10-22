"""P0-1：本地维护状态库故障不得把每个 ``/api/`` 请求变成 500。

被固化的契约（``db/system_state.py`` + ``main.maintenance_middleware``）：

1. 读函数**永不抛异常**：``sqlite3.Error`` / ``OSError`` 一律回落 ``DEFAULT_STATE``
   （未维护，fail-open），否则本地锁库 / 只读盘 / 磁盘满会全站 500；
2. 同类故障只告警一次（状态库坏了会命中每个请求，逐请求告警 = 日志风暴）；
3. 准入复核（``is_enabled_fresh``）走 ≤1s 的短缓存，本进程 ``set_state`` 主动
   失效 —— 每请求不再直读 SQLite，而管理员开关仍在 1s 内生效；
4. ``busy_timeout`` 不长时间阻塞事件循环线程。

离线：状态库指到 ``tmp_path``（``tests/conftest.py`` 已默认隔离），故障用替身
``_connect`` 注入，不连任何真实数据库。
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

import main
from db import system_state

MOZ = {"User-Agent": "Mozilla/5.0 (p0-1 harness)"}
# 非豁免的非 /api/system 路径：放行时到路由层（404），被拦时 503。
PROBE_URL = "/api/__probe_not_exempt__"
ACTOR = "64b7f0c2e1a3b4c5d6e7f801"


@pytest.fixture(autouse=True)
def _reset_caches_and_warnings(monkeypatch):
    # 「同类只告一次」的集合是进程级的：用例间清空，否则断言次数随执行顺序漂移。
    monkeypatch.setattr(system_state, "_warned_failures", set())
    system_state.invalidate_cache()
    yield
    system_state.invalidate_cache()


def _break_state_db(monkeypatch, exc: BaseException) -> None:
    def boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(system_state, "_connect", boom)


@pytest.fixture
def broken_db(monkeypatch):
    _break_state_db(monkeypatch, sqlite3.OperationalError("database is locked"))


# ---------------------------------------------------------------------------
# 读路径：故障 ⇒ 安全默认值，且不串改共享默认值
# ---------------------------------------------------------------------------


def test_get_state_returns_defaults_when_sqlite_fails(broken_db):
    assert system_state.get_state() == system_state.DEFAULT_STATE


def test_defaults_are_not_shared_mutable_state(broken_db):
    state = system_state.get_state()
    state["enabled"] = True
    assert system_state.DEFAULT_STATE["enabled"] is False
    assert system_state.get_state()["enabled"] is False


def test_oserror_is_tolerated_too(monkeypatch):
    # 只读盘 / 磁盘满在 sqlite3 里可能表现为 OSError，而不是 sqlite3.Error。
    _break_state_db(monkeypatch, OSError("disk I/O error"))

    assert system_state.get_state() == system_state.DEFAULT_STATE
    assert system_state.is_enabled() is False
    assert system_state.is_enabled_fresh() is False


def test_admission_checks_fail_open_and_stay_total(broken_db):
    # 直读 + 缓存读两条路径都不得抛：中间件与登录闸都在事件循环上调用它们。
    for _ in range(3):
        assert system_state.is_enabled() is False
        assert system_state.is_enabled_fresh() is False


def test_middleware_does_not_500_when_the_state_db_is_broken(broken_db):
    response = TestClient(main.app).get(PROBE_URL, headers=MOZ)

    assert response.status_code == 404, response.text


def test_warning_is_logged_once_per_kind_of_failure(monkeypatch, caplog):
    _break_state_db(monkeypatch, sqlite3.OperationalError("database is locked"))

    with caplog.at_level(logging.WARNING, logger="db.system_state"):
        for _ in range(10):
            system_state.get_state()

    warnings = [r for r in caplog.records if "get_state" in r.getMessage()]
    assert len(warnings) == 1


# ---------------------------------------------------------------------------
# 准入复核：≤1s 短缓存 + 主动失效
# ---------------------------------------------------------------------------


def test_fresh_cache_ttl_is_at_most_one_second():
    assert 0 < system_state.FRESH_CACHE_TTL <= 1.0


def test_repeated_admission_checks_reuse_one_sqlite_read(monkeypatch):
    real_connect = system_state._connect
    calls = {"n": 0}

    def counting(*args, **kwargs):
        calls["n"] += 1
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(system_state, "_connect", counting)
    system_state.set_state(False, None, None, ACTOR)  # 同时失效两级缓存
    system_state.init_db()
    baseline = calls["n"]

    for _ in range(50):
        assert system_state.is_enabled_fresh() is False

    assert calls["n"] - baseline <= 1


def test_fresh_value_is_cached_but_invalidation_rereads(tmp_path):
    system_state.set_state(True, "演练", None, ACTOR)
    assert system_state.is_enabled_fresh() is True  # 预热 fresh 缓存

    # 绕过 API 直接改库：1s 窗口内仍复用缓存（这正是省掉的那次读）。
    with closing(sqlite3.connect(system_state._db_path())) as conn, conn:
        conn.execute("UPDATE maintenance_state SET enabled = 0 WHERE id = 1")

    assert system_state.is_enabled_fresh() is True
    system_state.invalidate_cache()
    assert system_state.is_enabled_fresh() is False


def test_set_state_invalidates_the_fresh_cache_immediately():
    # 管理员在本进程开关维护：必须立刻生效，不能等 1s 缓存过期。
    system_state.set_state(True, "演练", None, ACTOR)
    assert system_state.is_enabled_fresh() is True

    system_state.set_state(False, "结束", None, ACTOR)
    assert system_state.is_enabled_fresh() is False

    system_state.set_state(True, "再开", None, ACTOR)
    assert system_state.is_enabled_fresh() is True


def test_busy_timeout_cannot_stall_the_event_loop():
    # 读发生在事件循环线程上（同步 sqlite）：等待时间 1:1 变成请求延迟。
    assert system_state.BUSY_TIMEOUT_MS <= 300

    with closing(system_state._connect()) as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == (
            system_state.BUSY_TIMEOUT_MS
        )
