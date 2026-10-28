"""个人首页的「最近 run」：``GET /api/ai/runs?active=0&limit=N``。

这个端点只做一件容易做错的事：把**本人的**最近 run 按时间倒序给一条横幅。
容易错的地方有三个，用例按这三点钉：

* ``active=0`` 以前回空表（「本期没有历史 run 列表」），现在改为最近 run；
  老语义 ``active=1``（重连横幅、``created_at`` 升序、只看在飞）必须原样保留。
* ``limit`` 只能是 1..10：首页不翻页，放开上限只会让同一份数据被反复拉取；
  越界由协议层 422，服务层再夹一次（其它入口不会绕过）。
* 过滤条件只有 ``owner_oid``：别人的 run 不存在任何参数可以读到。

全部离线：仓储是内存桩，HTTP 面用 ``TestClient`` + 依赖覆盖。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import oracle

from db import backends
from db.repos.oracle import ai_runs as runs_repo
from routers import ai_conversations_router
from services.agent import run_queue
from util.object_id import get_current_user

PREFIX = "/api/ai"
OWNER = "0" * 24
OTHER = "1" * 24
ADMIN = {"id": OWNER, "per": ["admin"]}
CONV = "a" * 32

TS = datetime(2026, 10, 27, 3, 0, 0)


def _sql(stmt) -> str:
    """Compile with bound values inlined; whitespace collapsed for matching."""
    return " ".join(
        str(
            stmt.compile(
                dialect=oracle.dialect(), compile_kwargs={"literal_binds": True}
            )
        ).split()
    )


def _run_row(
    run_id: str,
    *,
    owner_oid: str = OWNER,
    status: str = "succeeded",
    created_at: datetime = TS,
    conversation_oid: str = CONV,
) -> dict:
    return {
        "_id": run_id,
        "conversation_oid": conversation_oid,
        "owner_oid": owner_oid,
        "client_msg_id": None,
        "status": status,
        "model": "gpt-x",
        "stop_reason": None,
        "rounds": None,
        "error_code": None,
        "error_message": None,
        "result_message_oid": None,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "cancel_requested": False,
        "heartbeat_at": None,
        "created_at": created_at,
        "started_at": None,
        "finished_at": None,
    }


class FakeRunsRepo:
    """语义按真实仓储实现：``list_active`` 升序、``list_recent`` 倒序并截断。"""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.recent_calls: list[tuple[str, int]] = []

    def seed(self, run_id: str, **kwargs) -> dict:
        row = _run_row(run_id, **kwargs)
        self.rows.append(row)
        return row

    async def list_active(self, owner_oid: str) -> list[dict]:
        rows = [
            dict(row)
            for row in self.rows
            if str(row["owner_oid"]) == str(owner_oid)
            and row["status"] in ("queued", "running")
        ]
        rows.sort(key=lambda row: (row["created_at"], row["_id"]))
        return rows

    async def list_recent(self, owner_oid: str, limit: int) -> list[dict]:
        self.recent_calls.append((str(owner_oid), int(limit)))
        rows = [
            dict(row) for row in self.rows if str(row["owner_oid"]) == str(owner_oid)
        ]
        rows.sort(key=lambda row: (row["created_at"], row["_id"]), reverse=True)
        return rows[: int(limit)]


class FakeCallLogsRepo:
    async def insert(self, **_kwargs) -> None:
        return None


@pytest.fixture
def repo(monkeypatch) -> FakeRunsRepo:
    runs = FakeRunsRepo()
    monkeypatch.setattr(backends, "get_ai_runs_repo", lambda session=None: runs)
    monkeypatch.setattr(
        backends, "get_ai_call_logs_repo", lambda session=None: FakeCallLogsRepo()
    )
    return runs


@pytest.fixture
def api(repo) -> TestClient:
    state = {"user": dict(ADMIN)}
    app = FastAPI()
    app.include_router(ai_conversations_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    client = TestClient(app)
    client.state = state  # type: ignore[attr-defined]
    return client


# ---------------------------------------------------------------------------
# SQL 形状
# ---------------------------------------------------------------------------

def test_list_recent_sql_is_owner_scoped_newest_first_and_capped() -> None:
    sql = _sql(runs_repo._list_recent_stmt(OWNER, 5))

    # 唯一的过滤条件是 owner_oid，且不带状态谓词：失败/取消的 run 也要出现在首页。
    assert f"ai_runs.owner_oid = '{OWNER}'" in sql
    assert "status IN" not in sql
    assert "ORDER BY ai_runs.created_at DESC, ai_runs.oid DESC" in sql
    assert "FETCH FIRST 5 ROWS ONLY" in sql


def test_list_active_sql_stays_oldest_first_for_the_reconnect_banner() -> None:
    """``active=1`` 的语义不能被新查询顺手改掉：横幅按时间升序恢复。"""

    sql = _sql(runs_repo._list_active_stmt(OWNER))

    assert "ai_runs.status IN ('queued', 'running')" in sql
    assert "ORDER BY ai_runs.created_at ASC" in sql
    assert "FETCH FIRST" not in sql


# ---------------------------------------------------------------------------
# 服务层：limit 夹紧
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_service_clamps_recent_limit(repo) -> None:
    repo.seed("f" * 32, created_at=TS + timedelta(minutes=1))
    repo.seed("e" * 32, created_at=TS)

    await run_queue.list_recent(OWNER, limit=99)
    await run_queue.list_recent(OWNER, limit=0)

    assert [call[1] for call in repo.recent_calls] == [
        run_queue.RECENT_LIMIT_MAX,
        1,
    ]


@pytest.mark.asyncio
async def test_recent_limit_falls_back_on_garbage() -> None:
    assert run_queue.recent_limit("not-a-number") == run_queue.RECENT_LIMIT_DEFAULT
    assert run_queue.recent_limit(None) == run_queue.RECENT_LIMIT_DEFAULT


# ---------------------------------------------------------------------------
# HTTP 面
# ---------------------------------------------------------------------------

def test_recent_runs_are_owner_scoped_and_newest_first(api, repo) -> None:
    repo.seed("1" * 32, status="succeeded", created_at=TS)
    repo.seed("2" * 32, status="failed", created_at=TS + timedelta(minutes=2))
    repo.seed("3" * 32, status="cancelled", created_at=TS + timedelta(minutes=1))
    repo.seed("9" * 32, owner_oid=OTHER, created_at=TS + timedelta(minutes=9))

    response = api.get(f"{PREFIX}/runs", params={"active": 0, "limit": 2})

    assert response.status_code == 200, response.text
    runs = response.json()["data"]["runs"]
    assert [run["oid"] for run in runs] == ["2" * 32, "3" * 32]
    # 失败/取消的 run 也在首页里：这不是「成功记录」列表。
    assert [run["status"] for run in runs] == ["failed", "cancelled"]
    # 别人的 run 连 oid 都不该出现。
    assert all(run["oid"] != "9" * 32 for run in runs)
    assert repo.recent_calls[-1] == (OWNER, 2)


def test_recent_runs_default_to_five(api, repo) -> None:
    for index in range(7):
        repo.seed(
            f"{index:032x}",
            created_at=TS + timedelta(minutes=index),
        )

    response = api.get(f"{PREFIX}/runs", params={"active": 0})

    assert response.status_code == 200, response.text
    assert len(response.json()["data"]["runs"]) == run_queue.RECENT_LIMIT_DEFAULT
    assert response.json()["data"]["runs"][0]["oid"] == f"{6:032x}"


def test_other_users_runs_are_invisible(api, repo) -> None:
    repo.seed("9" * 32, owner_oid=OTHER)

    response = api.get(f"{PREFIX}/runs", params={"active": 0})

    assert response.status_code == 200, response.text
    assert response.json()["data"]["runs"] == []


def test_active_flag_keeps_the_reconnect_banner_contract(api, repo) -> None:
    repo.seed("1" * 32, status="queued", created_at=TS)
    repo.seed("2" * 32, status="running", created_at=TS + timedelta(minutes=1))
    repo.seed("3" * 32, status="succeeded", created_at=TS + timedelta(minutes=2))

    default = api.get(f"{PREFIX}/runs")
    explicit = api.get(f"{PREFIX}/runs", params={"active": 1, "limit": 10})

    for response in (default, explicit):
        assert response.status_code == 200, response.text
        runs = response.json()["data"]["runs"]
        # 默认仍是「在飞 run，升序」；limit 在这支上不生效（老客户端继续可用）。
        assert [run["oid"] for run in runs] == ["1" * 32, "2" * 32]
        assert [run["status"] for run in runs] == ["queued", "running"]


def test_limit_outside_one_to_ten_is_rejected(api) -> None:
    assert api.get(f"{PREFIX}/runs", params={"active": 0, "limit": 0}).status_code == 422
    assert api.get(f"{PREFIX}/runs", params={"active": 0, "limit": 11}).status_code == 422
    assert api.get(f"{PREFIX}/runs", params={"active": 0, "limit": 10}).status_code == 200


def test_role_gate_still_applies(api, repo) -> None:
    repo.seed("1" * 32)
    api.state["user"] = {"id": OWNER, "per": ["student"]}  # type: ignore[attr-defined]

    response = api.get(f"{PREFIX}/runs", params={"active": 0})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "AI_FORBIDDEN"
