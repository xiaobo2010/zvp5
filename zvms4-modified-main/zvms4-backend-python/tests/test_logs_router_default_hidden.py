"""管理端日志列表默认隐藏 ``login_fail``（离线，不连库）。

背景：``login_fail`` 是 ``services/risk_monitor.py`` 两条规则
（``login_failure_burst`` / ``ban_evasion_retry``）的唯一计数数据源，机制必须
保留；但它对管理员而言是「机器看、人不用看」的行，量大且噪声高。降噪的方式
是在 ``GET /api/logs`` 的**默认查询**里把它当黑名单排除，而不是停写或改计数。

这里钉住三件事：

* 不传 ``type`` → 仓储收到 ``exclude_types=["login_fail"]``；
* 显式 ``type=login_fail`` → 原样按白名单查（管理员排查账号异常的入口保留）；
* 显式指定任何其它类型时也不套黑名单（白名单永远优先），且 SQL 谓词里带
  ``IS NULL OR`` 分支——Oracle 把 ``''`` 当 NULL，裸 ``NOT IN`` 会把普通行
  一起吞掉。
"""

from __future__ import annotations

from sqlalchemy.dialects import oracle

from db.repos.oracle import app_logs as app_logs_repo

DIALECT = oracle.dialect()


def _sql(stmt) -> str:
    return " ".join(
        str(stmt.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True})).split()
    )


class _StubRepo:
    """记录路由传给仓储的过滤参数，并按登录行回一页数据。"""

    def __init__(self) -> None:
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
                "type": "login_fail",
                "level": "info",
                "xuehai_id": "",
            }
        ], 1


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


def test_default_list_hides_login_fail(monkeypatch):
    repo = _StubRepo()

    response = _client(monkeypatch, repo).get("/api/logs")

    assert response.status_code == 200
    assert repo.calls[0]["types"] == []
    assert repo.calls[0]["exclude_types"] == ["login_fail"]


def test_explicit_login_fail_type_still_reachable(monkeypatch):
    repo = _StubRepo()

    response = _client(monkeypatch, repo).get(
        "/api/logs", params={"type": "login_fail"}
    )

    assert response.status_code == 200
    assert repo.calls[0]["types"] == ["login_fail"]
    # 白名单优先：显式要看就不该再被黑名单削掉。
    assert repo.calls[0]["exclude_types"] is None
    assert response.json()["data"][0]["type"] == "login_fail"


def test_explicit_other_type_does_not_apply_the_blacklist(monkeypatch):
    repo = _StubRepo()

    _client(monkeypatch, repo).get("/api/logs", params={"type": "user_login"})

    assert repo.calls[0]["types"] == ["user_login"]
    assert repo.calls[0]["exclude_types"] is None


def test_default_sql_excludes_login_fail_and_keeps_null_types():
    # 带一个额外谓词，验证 OR 被括号圈住（否则 OR 会吃掉 AND，把过滤打穿）。
    sql = _sql(
        app_logs_repo._list_stmt(
            "", "u1", None, None, None, None, 1, 10, "", ["login_fail"]
        )
    )

    assert (
        "AND (app_logs.ltype IS NULL OR (app_logs.ltype NOT IN ('login_fail')))" in sql
    )
    assert "app_logs.app_user = 'u1'" in sql


def test_explicit_type_sql_has_no_blacklist():
    sql = _sql(
        app_logs_repo._list_stmt(
            "", "", ["login_fail"], None, None, None, 1, 10
        )
    )

    assert "app_logs.ltype IN ('login_fail')" in sql
    assert "NOT IN" not in sql
