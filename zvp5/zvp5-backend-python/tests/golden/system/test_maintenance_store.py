"""``db.system_state`` 的离线单测：不连数据库、不发 HTTP。

用 ``tmp_path`` + ``ZVP5_SYSTEM_DB`` 环境变量把状态库隔离到临时目录，每个
用例前后 ``invalidate_cache()``，避免进程内 TTL 缓存跨用例串味。

从仓库根运行（``.venv/bin/python -m pytest tests/golden/system -q``）；下方
自带 ``sys.path`` 引导，因此从别处调用也能收集。
"""

from __future__ import annotations

import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]  # tests/golden/system -> <repo root>
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from db import system_state  # noqa: E402  (sys.path 引导之后再导入)

ACTOR = "64b7f0c2e1a3b4c5d6e7f801"

DEFAULT_STATE = {
    "enabled": False,
    "since": None,
    "reason": None,
    "announcement": None,
    "updated_by": None,
    "updated_at": None,
}


@pytest.fixture()
def state_db(tmp_path, monkeypatch) -> Path:
    path = tmp_path / "system_state.db"
    monkeypatch.setenv("ZVP5_SYSTEM_DB", str(path))
    system_state.invalidate_cache()
    yield path
    system_state.invalidate_cache()


def _query(path: Path, sql: str) -> list:
    with closing(sqlite3.connect(path)) as conn:
        return conn.execute(sql).fetchall()


def test_initial_state_is_disabled_and_writes_no_row(state_db: Path) -> None:
    state = system_state.get_state()
    assert state == DEFAULT_STATE
    # 读操作不得落行（否则「未开启」也会有行，无法区分默认与显式关闭）。
    assert _query(state_db, "SELECT COUNT(*) FROM maintenance_state") == [(0,)]
    assert system_state.is_enabled() is False


def test_set_state_upserts_single_row_and_appends_events(state_db: Path) -> None:
    system_state.set_state(True, "演练", "今晚 22:00 维护", ACTOR)
    assert system_state.is_enabled() is True

    first = system_state.get_state()
    assert first["enabled"] is True
    assert first["reason"] == "演练"
    assert first["announcement"] == "今晚 22:00 维护"
    assert first["updated_by"] == ACTOR
    assert first["since"] is not None

    # 重复 set 不得膨胀 state 表（单行 id=1 UPSERT），事件表每次追加。
    system_state.set_state(True, "演练 2", "改公告", ACTOR)
    assert _query(state_db, "SELECT COUNT(*) FROM maintenance_state") == [(1,)]
    assert _query(state_db, "SELECT COUNT(*) FROM maintenance_events") == [(2,)]

    second = system_state.get_state()
    assert second["reason"] == "演练 2"
    assert second["announcement"] == "改公告"
    # 已经是开启状态：since 保持首次开启的时间，不因重复 set 改写。
    assert second["since"] == first["since"]

    events = _query(
        state_db, "SELECT enabled, actor_oid, reason FROM maintenance_events ORDER BY id"
    )
    assert events == [(1, ACTOR, "演练"), (1, ACTOR, "演练 2")]


def test_since_is_set_on_enable_and_cleared_on_disable(state_db: Path) -> None:
    system_state.set_state(True, "开始维护", None, ACTOR)
    enabled = system_state.get_state()
    assert enabled["enabled"] is True
    assert enabled["since"] is not None

    system_state.set_state(False, "维护结束", None, ACTOR)
    disabled = system_state.get_state()
    assert disabled["enabled"] is False
    assert disabled["since"] is None

    system_state.set_state(True, "再次维护", None, ACTOR)
    re_enabled = system_state.get_state()
    assert re_enabled["since"] is not None
    assert re_enabled["since"] >= enabled["since"]


def test_state_persists_after_cache_is_cleared(state_db: Path) -> None:
    system_state.set_state(True, "演练", "公告", ACTOR)

    # 清空模块级缓存 = 模拟进程重启后的首次读取。
    system_state.invalidate_cache()
    assert system_state.is_enabled() is True
    state = system_state.get_state()
    assert state["enabled"] is True
    assert state["reason"] == "演练"

    # init_db 幂等：再次建表不得清掉既有数据。
    system_state.init_db()
    assert _query(state_db, "SELECT COUNT(*) FROM maintenance_state") == [(1,)]
    assert system_state.is_enabled() is True


def test_ttl_cache_returns_stale_value_until_invalidated(
    state_db: Path, monkeypatch
) -> None:
    # TTL 调大：保证「未失效」这一断言不与真实 2s 窗口赛跑。
    monkeypatch.setattr(system_state, "CACHE_TTL", 60.0)
    system_state.set_state(True, "演练", None, ACTOR)
    assert system_state.is_enabled() is True  # 预热缓存

    # 绕过 API 直接改 SQLite 文件。
    with closing(sqlite3.connect(state_db)) as conn, conn:
        conn.execute("UPDATE maintenance_state SET enabled = 0 WHERE id = 1")

    assert system_state.is_enabled() is True  # 缓存未失效 -> 仍返回旧值
    system_state.invalidate_cache()
    assert system_state.is_enabled() is False  # 失效后 -> 读到新值


def test_ttl_cache_expires_and_rereads(state_db: Path, monkeypatch) -> None:
    system_state.set_state(True, "演练", None, ACTOR)
    monkeypatch.setattr(system_state, "CACHE_TTL", 60.0)
    assert system_state.is_enabled() is True  # 预热缓存

    with closing(sqlite3.connect(state_db)) as conn, conn:
        conn.execute("UPDATE maintenance_state SET enabled = 0 WHERE id = 1")

    # TTL 归零 -> 缓存视为过期，无需手动失效即重读。
    monkeypatch.setattr(system_state, "CACHE_TTL", 0.0)
    assert system_state.is_enabled() is False
