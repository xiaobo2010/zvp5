"""管理端日志查询的访问路径：索引、单语句总数、LIKE 搜索、分页上限。

审计结论（`routers/logs_router.py` + `db/repos/oracle/app_logs.py`）：

* ``ORDER BY ts_raw DESC`` 没有 ``ts_raw`` 前缀的索引（V001 只有 ``ts_at`` 与
  ``(app_user, ts_raw)``）→ 每页全表排序。
* 每页额外一次全表 ``COUNT(*)``。
* ``query`` 走 ``REGEXP_LIKE(url|data)`` → 每行编译正则、全表扫描。

这里用离线 SQL 编译 + 假 session 钉住修复后的形状，不需要数据库。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import oracle

from db.repos.oracle import app_logs as app_logs_repo

DIALECT = oracle.dialect()
DDL = (
    Path(__file__).resolve().parents[1]
    / "db"
    / "oracle"
    / "ddl"
    / "V018__add_app_logs_indexes.sql"
)


def _sql(stmt) -> str:
    return " ".join(
        str(
            stmt.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True})
        ).split()
    )


def _row(**overrides) -> SimpleNamespace:
    mapping = {
        "oid": "a" * 24,
        "url": "/api/x",
        "app_user": "",
        "data": "{}",
        "ts_at": None,
        "ts_raw": 1.0,
        "ltype": "user_login",
        "log_level": "info",
        "xuehai_id": "",
        "user_name": None,
        "user_id": None,
        "total_count": 1,
    }
    mapping.update(overrides)
    return SimpleNamespace(_mapping=mapping)


class _Result:
    def __init__(self, rows, scalar=None):
        self._rows = rows
        self._scalar = scalar

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._scalar


class _RecordingSession:
    """按调用序号回放结果，并记录每条 SQL（用来数「这一页查了几次库」）。"""

    def __init__(self, rows, count_total=0):
        self.statements: list[str] = []
        self._rows = rows
        self._count_total = count_total

    async def execute(self, stmt):
        self.statements.append(_sql(stmt))
        if len(self.statements) == 1:
            return _Result(self._rows)
        return _Result([], self._count_total)


# ---------------------------------------------------------------------------
# 1. 索引：V018 必须给 ts_raw 排序路径一条索引
# ---------------------------------------------------------------------------


def _v018_statements() -> str:
    """V018 去掉 ``--`` 注释后的正文（注释里会出现关键字，不能拿来计数）。"""
    lines = [
        line
        for line in DDL.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    ]
    return " ".join(" ".join(lines).split())


def test_v018_indexes_the_ordering_and_the_type_level_filter():
    ddl = _v018_statements()

    assert "CREATE INDEX ix_app_logs_ts_raw ON app_logs (ts_raw)" in ddl
    assert (
        "CREATE INDEX ix_app_logs_type_level_ts ON app_logs (ltype, log_level, ts_raw)"
        in ddl
    )
    # 最多两个索引：别顺手给每个谓词都建一条。
    assert ddl.count("CREATE INDEX") == 2
    # 升序索引即可反向扫，DESC 只会和 V001 风格分叉。
    assert "DESC" not in ddl


def test_v018_is_idempotent():
    ddl = _v018_statements()
    assert ddl.count("SQLCODE NOT IN (-955, -1408)") == 2
    assert ddl.count("EXECUTE IMMEDIATE") == 2


# ---------------------------------------------------------------------------
# 2. 总数：COUNT(*) OVER()，不再每页一次全表 COUNT
# ---------------------------------------------------------------------------


def test_list_stmt_carries_window_count():
    sql = _sql(app_logs_repo._list_stmt("", "", None, None, None, None, 1, 10))
    assert "count(*) OVER () AS total_count" in sql
    assert "ORDER BY app_logs.ts_raw DESC" in sql


@pytest.mark.asyncio
async def test_list_page_uses_one_statement_for_rows_and_total():
    session = _RecordingSession([_row(total_count=42)])

    logs, total = await app_logs_repo.OracleAppLogsRepo(session).list_page(page=1, size=10)

    assert total == 42
    assert len(logs) == 1
    assert len(session.statements) == 1, "常规翻页只应有列表这一条语句"
    assert "count(*) OVER ()" in session.statements[0]


@pytest.mark.asyncio
async def test_list_page_empty_first_page_is_zero_without_a_count_query():
    session = _RecordingSession([])

    logs, total = await app_logs_repo.OracleAppLogsRepo(session).list_page(page=1, size=10)

    assert logs == []
    assert total == 0
    assert len(session.statements) == 1


@pytest.mark.asyncio
async def test_list_page_out_of_range_falls_back_to_count():
    """页码越界时空页看不出总数，才补一条 count（users.py:128 同一范式）。"""
    session = _RecordingSession([], count_total=7)

    logs, total = await app_logs_repo.OracleAppLogsRepo(session).list_page(page=9, size=10)

    assert logs == []
    assert total == 7
    assert len(session.statements) == 2
    assert "count(*)" in session.statements[1].lower()
    assert "OVER ()" not in session.statements[1]


@pytest.mark.asyncio
async def test_list_for_user_also_uses_window_count():
    session = _RecordingSession([_row(total_count=3)])

    _, total = await app_logs_repo.OracleAppLogsRepo(session).list_for_user(
        "b" * 24, page=1, size=10
    )

    assert total == 3
    assert len(session.statements) == 1
    assert "count(*) OVER ()" in session.statements[0]


# ---------------------------------------------------------------------------
# 3. 搜索：url/data 用 LIKE，正则引擎退出热路径
# ---------------------------------------------------------------------------


def test_query_search_hits_url_with_like_and_escapes_wildcards():
    sql = _sql(app_logs_repo._list_stmt("a%b_c", "", None, None, None, None, 1, 10))

    assert "lower(app_logs.url) LIKE '%a\\%b\\_c%' ESCAPE '\\'" in sql
    assert "lower(app_logs.data) LIKE '%a\\%b\\_c%' ESCAPE '\\'" in sql
    assert "regexp_like" not in sql.lower()


def test_query_search_is_case_insensitive_via_lower():
    sql = _sql(app_logs_repo._list_stmt("ADMIN", "", None, None, None, None, 1, 10))
    assert "LIKE '%admin%'" in sql


def test_per_user_search_uses_the_same_like_path():
    sql = _sql(app_logs_repo._list_for_user_stmt("b" * 24, "err", 1, 10))

    assert "lower(app_logs.url) LIKE '%err%'" in sql
    assert "lower(app_logs.data) LIKE '%err%'" in sql
    assert "regexp_like" not in sql.lower()


def test_filters_stay_indexable():
    """过滤条件仍在裸列上：ts_raw 区间、ltype/level 等值或 IN。"""
    sql = _sql(
        app_logs_repo._list_stmt(
            "", "", ["user_login"], ["warning"], 100, 200, 1, 10, "xuehai-1"
        )
    )
    assert "app_logs.ts_raw > 100" in sql
    assert "app_logs.ts_raw < 200" in sql
    assert "app_logs.ltype IN ('user_login')" in sql
    assert "app_logs.log_level IN ('warning')" in sql
    assert "app_logs.xuehai_id = 'xuehai-1'" in sql


# ---------------------------------------------------------------------------
# 4. 分页语义与上限
# ---------------------------------------------------------------------------


def test_page_size_is_capped_at_100():
    sql = _sql(app_logs_repo._list_stmt("", "", None, None, None, None, 1, 500))
    assert "FETCH FIRST 100 ROWS ONLY" in sql


def test_legacy_page_minus_one_renders_as_first_page():
    sql = _sql(app_logs_repo._list_stmt("", "", None, None, None, None, -1, 10))
    assert "OFFSET 0 ROWS" in sql
    assert "FETCH FIRST 10 ROWS ONLY" in sql


def test_page_offsets_are_one_based():
    sql = _sql(app_logs_repo._list_stmt("", "", None, None, None, None, 3, 25))
    assert "OFFSET 50 ROWS" in sql


# ---------------------------------------------------------------------------
# 5. 路由：page≥1、perpage≤100、metadata.size 语义不变
# ---------------------------------------------------------------------------


def _client(monkeypatch, repo):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from db import backends
    from routers import logs_router
    from util.object_id import get_current_user

    monkeypatch.setattr(backends, "get_applogs_repo", lambda session=None: repo)

    app = FastAPI()
    app.include_router(logs_router.router, prefix="/api/logs")
    app.dependency_overrides[get_current_user] = lambda: {"id": "x", "per": ["admin"]}
    return TestClient(app)


class _StubRepo:
    def __init__(self):
        self.calls: list[dict] = []

    async def list_page(self, **kwargs):
        self.calls.append(kwargs)
        return [
            {
                "_id": "a" * 24,
                "url": "/api/x",
                "user": "",
                "data": "{}",
                "timestamp": 1.0,
                "type": "user_login",
                "level": "info",
                "xuehai_id": "",
            }
        ], 137


def test_router_rejects_page_below_one(monkeypatch):
    repo = _StubRepo()
    response = _client(monkeypatch, repo).get("/api/logs", params={"page": 0})

    assert response.status_code == 422
    assert repo.calls == []


def test_router_rejects_perpage_above_100(monkeypatch):
    repo = _StubRepo()
    response = _client(monkeypatch, repo).get("/api/logs", params={"perpage": 101})

    assert response.status_code == 422
    assert repo.calls == []


def test_router_defaults_to_first_page_and_keeps_metadata_size(monkeypatch):
    repo = _StubRepo()
    response = _client(monkeypatch, repo).get("/api/logs")

    assert response.status_code == 200
    body = response.json()
    assert body["metadata"]["size"] == 137
    assert body["data"][0]["user_name"] == "未登录"
    assert repo.calls[0]["page"] == 1
