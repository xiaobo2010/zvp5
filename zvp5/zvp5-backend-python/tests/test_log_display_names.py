"""日志列表「显示名」契约：``app_logs LEFT JOIN users``（离线，不连库）。

管理员看到的日志里，``user`` 是 oid，光看哈希分不出人。显示名必须在**读路径**
解析，才同时修好历史数据；写路径继续只落 oid（``performer`` 过滤依赖它）。

这里钉住三件事：

* LIST 语句必须带 ``users`` 列 ``user_name`` / ``name`` 与 ``user_id`` /
  ``business_id``，并且是 **LEFT OUTER JOIN**：``app_user`` 为空串（未登录
  请求）或指向已删除用户时，行不能消失。
* ``COUNT`` 语句不 join：总数与显示名无关，多连一张表只是白付代价。
* ``_log_row`` 把两列透出，缺失时为 ``None``（前端回落「匿名」）。
"""

from __future__ import annotations

from sqlalchemy.dialects import oracle

from db.repos.oracle import app_logs as app_logs_repo

USER_OID = "000000000000000000000002"
DIALECT = oracle.dialect()


def _sql(stmt) -> str:
    return " ".join(
        str(stmt.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True})).split()
    )


def test_list_sql_left_joins_users_and_projects_display_fields():
    sql = _sql(app_logs_repo._list_stmt("", "", None, None, None, None, -1, 10))

    assert "LEFT OUTER JOIN users ON app_logs.app_user = users.oid" in sql
    assert "users.name AS user_name" in sql
    assert "users.business_id AS user_id" in sql
    # 日志本体列一个都不能少：显示名是加法，不是替换。
    for column in ("app_logs.oid", "app_logs.url", "app_logs.app_user", "app_logs.data"):
        assert column in sql


def test_list_for_user_sql_also_joins_users():
    sql = _sql(app_logs_repo._list_for_user_stmt(USER_OID, "", -1, 10))

    assert "LEFT OUTER JOIN users" in sql
    assert "users.name AS user_name" in sql
    assert f"app_logs.app_user = '{USER_OID}'" in sql


def test_count_sql_does_not_join_users():
    sql = _sql(app_logs_repo._count_stmt("", "", None, None, None, None))

    assert "users" not in sql
    assert "count(*)" in sql.lower()


def test_query_filter_stays_on_url_and_data_after_the_join():
    """``query`` 只匹配 url/data：join 不能顺手把 users.name 也纳入搜索。

    ``REGEXP_LIKE`` 已换成 ``LOWER(col) LIKE ... ESCAPE``：正则引擎不再参与，
    但命中的列范围不变（url + data，大小写不敏感）。
    """
    sql = _sql(app_logs_repo._list_stmt("张三", "", None, None, None, None, 1, 10))

    assert "lower(app_logs.url) LIKE '%张三%' ESCAPE '\\'" in sql
    assert "lower(app_logs.data) LIKE '%张三%' ESCAPE '\\'" in sql
    assert "regexp_like" not in sql.lower()
    assert "users." not in sql.split(" WHERE ")[-1].lower()


def test_anonymous_rows_survive_because_join_has_no_user_predicate():
    """空 ``app_user``（未登录）不能被 WHERE 排掉。

    左连接的连接条件写在 ``ON`` 里，``WHERE`` 里没有任何 ``users`` 谓词——
    否则 Oracle 会把外连接退化成内连接，匿名/已删号的行整体消失。
    """
    sql = _sql(
        app_logs_repo._list_stmt("", "", ["system_ssh_command"], None, None, None, -1, 10)
    )
    where_clause = sql.split(" WHERE ")[-1]
    assert "app_user" not in where_clause.lower()
    assert "users." not in where_clause.lower()


def test_log_row_exposes_display_fields_and_tolerates_missing():
    row = {
        "oid": "a" * 24,
        "url": "/api/x",
        "app_user": USER_OID,
        "data": "{}",
        "ts_at": None,
        "ts_raw": 1.0,
        "ltype": "system_ssh_command",
        "log_level": "info",
        "xuehai_id": "",
        "user_name": "李四",
        "user_id": "2026002",
    }

    doc = app_logs_repo._log_row(row)

    assert doc["user"] == USER_OID  # oid 保留：performer 过滤与溯源都靠它
    assert doc["user_name"] == "李四"
    assert doc["user_id"] == "2026002"


def test_log_row_anonymous_row_has_none_names():
    row = {
        "oid": "b" * 24,
        "url": "/api/x",
        "app_user": None,
        "data": "{}",
        "ts_at": None,
        "ts_raw": 2.0,
        "ltype": "",
        "log_level": "info",
        "xuehai_id": "",
        "user_name": None,
        "user_id": None,
    }

    doc = app_logs_repo._log_row(row)

    assert doc["user"] is None
    assert doc["user_name"] is None
    assert doc["user_id"] is None


def _logs_client(monkeypatch, rows):
    """``GET /api/logs`` 的离线客户端；users 仓储一旦被调用就直接失败。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from db import backends
    from routers import logs_router
    from util.object_id import get_current_user

    class StubLogsRepo:
        async def list_page(self, **_kwargs):
            return [dict(row) for row in rows], len(rows)

    def _no_users_repo(*_args, **_kwargs):  # pragma: no cover - 被调用即失败
        raise AssertionError("日志列表不应再查 users 仓储")

    monkeypatch.setattr(
        backends, "get_applogs_repo", lambda session=None: StubLogsRepo()
    )
    monkeypatch.setattr(backends, "get_users_repo", _no_users_repo)

    app = FastAPI()
    app.include_router(logs_router.router, prefix="/api/logs")
    app.dependency_overrides[get_current_user] = lambda: {
        "id": USER_OID,
        "per": ["admin"],
    }
    return TestClient(app)


def _log_row(**overrides):
    row = {
        "_id": "a" * 24,
        "url": "/api/x",
        "user": USER_OID,
        "user_name": "李四",
        "user_id": "2026002",
        "data": "{}",
        "timestamp": 1.0,
        "type": "user_login",
        "level": "info",
        "xuehai_id": "",
    }
    row.update(overrides)
    return row


def test_router_derives_display_name_without_a_second_users_query(monkeypatch):
    """显示名只来自 JOIN，路由不再批量查 ``users``（每请求省一次 ADB 往返）。"""
    from routers import logs_router

    assert not hasattr(logs_router, "get_user_display_names")

    response = _logs_client(
        monkeypatch,
        [
            _log_row(),
            # 已删用户：LEFT JOIN 未命中 → user_name 为 None，但 app_user 还在
            _log_row(user="0" * 23 + "9", user_name=None, user_id=None),
            # 未登录请求：app_user 为空串
            _log_row(user="", user_name=None, user_id=None),
        ],
    ).get("/api/logs")

    assert response.status_code == 200
    data = response.json()["data"]
    assert [row["user_name"] for row in data] == ["李四", "未知用户", "未登录"]
    assert data[0]["user_id"] == "2026002"
    # oid 仍是 ``user`` 字段：响应形状与取值口径不变。
    assert data[0]["user"] == USER_OID
