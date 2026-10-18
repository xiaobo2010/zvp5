"""崩溃自愈：自动来源的陈旧维护态（设计 §6.4）。

场景：清理编排把全站切进维护态（``updated_by='auto:retention'``）之后进程崩了
—— ``finally`` 没机会执行，SQLite 里留下 ``enabled=1``。重启后进程内探针必然
为 ``False``，这一行必须被判为**未维护**并惰性清除，否则全站永久 503。

钉子（验收要求）：
1. ``enabled=1`` + ``updated_by='auto:retention'`` + 探针 ``False`` ⇒
   ``is_enabled()`` 为 ``False``，行被清除（``enabled=0``），``maintenance_events``
   里多一条自愈审计 —— 且**只写一次**（并发/多请求下也只有一条）。
2. 反例：人工维护（``updated_by`` 是管理员 oid）**永不**自愈。
3. 反例：清理仍在跑（探针 ``True``）**不**自愈。
4. 探针未注册 / 探针抛异常 ⇒ 无法判定 ⇒ **保守地不自愈**。

全部离线：状态库用 ``ZVMS_SYSTEM_DB`` 隔离到 ``tmp_path``，不连业务库、不发
HTTP。跑法：``.venv/bin/python -m pytest tests/test_maintenance_auto_heal.py -q``
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from pathlib import Path

import pytest

from db import system_state

ADMIN_OID = "64b7f0c2e1a3b4c5d6e7f801"
AUTO_ACTOR = "auto:retention"
HEAL_ACTOR = system_state.AUTO_HEAL_ACTOR
REASON = "数据库清理"
ANNOUNCEMENT = "系统正在清理数据库历史数据，期间暂时无法访问，清理完成后自动恢复。"


@pytest.fixture()
def state_db(tmp_path, monkeypatch) -> Path:
    """隔离状态库 + 清缓存；结束后恢复原本注册的探针，避免跨用例串味。"""
    path = tmp_path / "system_state.db"
    monkeypatch.setenv("ZVMS_SYSTEM_DB", str(path))
    original_probe = system_state._auto_source_probe
    system_state.invalidate_cache()
    yield path
    system_state.set_auto_source_probe(original_probe)
    system_state.invalidate_cache()


@pytest.fixture()
def probe(monkeypatch):
    """可切换的自动来源探针：``probe["live"]`` 决定返回值。"""
    holder = {"live": False, "calls": 0}

    def _probe() -> bool:
        holder["calls"] += 1
        return holder["live"]

    monkeypatch.setattr(system_state, "_auto_source_probe", _probe, raising=False)
    return holder


def _rows(path: Path, sql: str) -> list:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(sql).fetchall()


def _events(path: Path) -> list:
    """``maintenance_events``：``(enabled, actor_oid, reason)``。"""
    return _rows(path, "SELECT enabled, actor_oid, reason FROM maintenance_events ORDER BY id")


def _state_row(path: Path) -> tuple:
    rows = _rows(path, "SELECT enabled, updated_by FROM maintenance_state WHERE id = 1")
    return rows[0] if rows else (None, None)


def _enable_auto() -> None:
    """模拟「清理编排刚把全站切进维护态，然后进程崩了」。"""
    system_state.set_state(True, REASON, ANNOUNCEMENT, AUTO_ACTOR)
    system_state.invalidate_cache()


# ---------------------------------------------------------------------------
# ① 陈旧自动维护态 ⇒ 自愈
# ---------------------------------------------------------------------------


def test_stale_auto_state_is_reported_as_not_enabled_and_cleared(state_db, probe):
    probe["live"] = False
    _enable_auto()
    assert system_state.get_state()["enabled"] is True  # 崩溃前落下的状态
    assert _events(state_db) == [(1, AUTO_ACTOR, REASON)]

    assert system_state.is_enabled() is False

    # 行被清除：enabled=0（updated_by 变成自愈标记，仍带 auto: 前缀）。
    enabled, updated_by = _state_row(state_db)
    assert enabled == 0
    assert updated_by == HEAL_ACTOR
    assert system_state.is_auto_actor(updated_by) is True
    assert system_state.get_state()["enabled"] is False

    # 自愈审计：enabled=1 那条 + enabled=0 的自愈条。
    events = _events(state_db)
    assert [event[0] for event in events] == [1, 0]
    assert events[1][1] == HEAL_ACTOR
    assert events[1][2] == system_state.STALE_AUTO_REASON


def test_heal_writes_exactly_once_and_never_again(state_db, probe):
    """"不要每请求写一次库"：重复读不得反复写审计。"""
    probe["live"] = False
    _enable_auto()

    for _ in range(5):
        assert system_state.is_enabled() is False
    assert system_state.is_enabled_fresh() is False

    assert len(_events(state_db)) == 2  # 开启 + 一次自愈
    assert _rows(state_db, "SELECT COUNT(*) FROM maintenance_state") == [(1,)]


def test_is_enabled_fresh_also_heals(state_db, probe):
    """准入路径走的是 fresh 版本，也必须自愈（否则仍会放行 503 判断）。"""
    probe["live"] = False
    _enable_auto()

    assert system_state.is_enabled_fresh() is False
    assert _state_row(state_db)[0] == 0
    assert len(_events(state_db)) == 2


def test_concurrent_readers_produce_a_single_heal_write(state_db, probe):
    """线程安全：并发请求同时发现陈旧态，也只写一次库、一条审计。"""
    probe["live"] = False
    _enable_auto()

    results: list[bool] = []
    barrier = threading.Barrier(8)

    def _read() -> None:
        barrier.wait()
        results.append(system_state.is_enabled())

    threads = [threading.Thread(target=_read) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == [False] * 8
    assert len([e for e in _events(state_db) if e[1] == HEAL_ACTOR]) == 1
    assert _rows(state_db, "SELECT COUNT(*) FROM maintenance_state") == [(1,)]


def test_healed_state_survives_a_simulated_restart(state_db, probe):
    """自愈结果落盘：清缓存（=新进程首次读）后依然是「未维护」。"""
    probe["live"] = False
    _enable_auto()

    assert system_state.is_enabled() is False
    system_state.invalidate_cache()  # 模拟进程重启
    assert system_state.is_enabled() is False
    assert system_state.get_state()["enabled"] is False


def test_stale_cached_true_is_lost_after_the_ttl(state_db, probe, monkeypatch):
    """自愈与 2s TTL 的交互：缓存里压着的 True 最多存活一个 TTL 窗口。

    自愈走 ``set_state`` → ``invalidate_cache``，所以锁内自愈后本进程立刻读到
    False；唯一窗口是「自愈之前就已经被缓存住的 True」——与既有缓存语义一致
    （跨进程 enable 同样是 ≤TTL 的陈旧放行）。
    """
    monkeypatch.setattr(system_state, "CACHE_TTL", 60.0)
    probe["live"] = True
    _enable_auto()
    assert system_state.is_enabled() is True  # 预热缓存（此刻探针为 True）

    probe["live"] = False  # 进程崩了（本进程仍是同一个进程，探针翻脸）
    assert system_state.is_enabled() is True  # 缓存未过期 —— 已知窗口

    system_state.invalidate_cache()
    assert system_state.is_enabled() is False
    assert _state_row(state_db)[0] == 0


# ---------------------------------------------------------------------------
# ② 人工维护永不自动解除
# ---------------------------------------------------------------------------


def test_manual_maintenance_is_never_healed(state_db, probe):
    probe["live"] = False  # 探针说「没有清理在跑」
    system_state.set_state(True, "升级数据库", None, ADMIN_OID)
    system_state.invalidate_cache()

    assert system_state.is_enabled() is True
    assert system_state.is_enabled_fresh() is True

    assert _state_row(state_db) == (1, ADMIN_OID)
    assert _events(state_db) == [(1, ADMIN_OID, "升级数据库")]


def test_manual_maintenance_is_untouched_even_when_the_probe_raises(state_db):
    def _boom() -> bool:
        raise RuntimeError("probe down")

    system_state.set_auto_source_probe(_boom)
    system_state.set_state(True, "手动演练", None, ADMIN_OID)
    system_state.invalidate_cache()

    assert system_state.is_enabled() is True
    assert _state_row(state_db) == (1, ADMIN_OID)


# ---------------------------------------------------------------------------
# ③ 清理进行中（探针 True）不自愈
# ---------------------------------------------------------------------------


def test_live_auto_source_is_not_healed(state_db, probe):
    probe["live"] = True
    _enable_auto()

    assert system_state.is_enabled() is True
    assert system_state.is_enabled_fresh() is True
    assert _state_row(state_db) == (1, AUTO_ACTOR)
    assert len(_events(state_db)) == 1  # 没有自愈审计


def test_running_cleanup_then_restart_is_healed_once_it_is_gone(state_db, probe):
    """同一进程内：清理在跑（True）不自愈；探针翻 False（进程没了）后自愈。"""
    probe["live"] = True
    _enable_auto()
    assert system_state.is_enabled() is True

    probe["live"] = False
    system_state.invalidate_cache()
    assert system_state.is_enabled() is False
    assert _state_row(state_db)[0] == 0


# ---------------------------------------------------------------------------
# ④ 无法判定 ⇒ 保守不自愈
# ---------------------------------------------------------------------------


def test_unregistered_probe_never_heals(state_db):
    system_state.set_auto_source_probe(None)
    _enable_auto()

    assert system_state.is_enabled() is True
    assert _state_row(state_db) == (1, AUTO_ACTOR)
    assert len(_events(state_db)) == 1


def test_failing_probe_is_treated_as_unknown(state_db, probe):
    """探针坏了不能把准入路径带崩，也不能误清维护态。"""

    def _boom() -> bool:
        raise RuntimeError("probe down")

    system_state.set_auto_source_probe(_boom)
    _enable_auto()

    assert system_state.is_enabled() is True
    assert system_state.is_enabled_fresh() is True
    assert _state_row(state_db) == (1, AUTO_ACTOR)
    assert len(_events(state_db)) == 1


def test_probe_is_only_consulted_for_enabled_auto_rows(state_db, probe):
    """成本约束：非维护态 / 人工维护态都不该调用探针。"""
    probe["live"] = False
    assert system_state.is_enabled() is False
    assert probe["calls"] == 0

    system_state.set_state(True, "手动", None, ADMIN_OID)
    system_state.invalidate_cache()
    assert system_state.is_enabled() is True
    assert probe["calls"] == 0

    _enable_auto()
    assert system_state.is_enabled() is False  # 自动来源 + 探针 False ⇒ 自愈
    assert probe["calls"] >= 1
