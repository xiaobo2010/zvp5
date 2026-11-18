"""``/api/ai/agent/ack``：AI Agent 首次使用安全警示的按账号持久化。

钉住三件事：

* 仓储层用 ``MERGE`` 的 ``WHEN NOT MATCHED`` 分支写入 —— 重复确认**不覆盖**
  ``acked_at``，并发下不会撞出第二行，也不会 500；
* 表形状与 ``NOTIFICATION_READS`` 同口径：``user_oid`` 单列主键、无 OID 列、
  无外键；
* 路由契约：``GET`` 空表回 ``acked:false`` + ``acked_at:null``；``POST`` 之后
  ``GET`` 回同一时间；无 AI 权限走既有的 OpenAI 形状 403 + ``X-AI-Request-Id``。

全离线：SQL 用 Oracle 方言编译、不连库；路由的仓储 / 审计全是内存桩。
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import oracle
from sqlalchemy.exc import IntegrityError

from db import backends
from db.models import ai_agent_acks as acks_tbl
from db.repos.oracle import ai_agent_acks as acks_repo
from routers import ai_agent_router
from util.object_id import get_current_user

USER_OID = "000000000000000000000003"
OTHER_OID = "000000000000000000000004"
NOW = datetime(2026, 11, 18, 10, 0, 0)
LATER = datetime(2026, 11, 18, 11, 30, 0)
OTHER_TIME = datetime(2026, 11, 18, 9, 15, 0)

ADMIN = {"id": USER_OID, "per": ["admin"]}
STUDENT = {"id": OTHER_OID, "per": ["student"]}

DIALECT = oracle.dialect()


def _sql(stmt) -> str:
    """Compile with bound values inlined; whitespace collapsed for matching."""
    return " ".join(
        str(stmt.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True})).split()
    )


def _text(stmt) -> str:
    """Whitespace-collapsed raw SQL for ``text()`` statements."""
    return " ".join(str(stmt).split())


# ---------------------------------------------------------------------------
# Schema / SQL shape
# ---------------------------------------------------------------------------


def test_table_is_one_row_per_account() -> None:
    assert [col.name for col in acks_tbl.c] == ["user_oid", "acked_at"]
    assert [col.name for col in acks_tbl.primary_key.columns] == ["user_oid"]
    assert acks_tbl.c.user_oid.type.length == 24
    assert acks_tbl.c.acked_at.nullable is False
    assert "SYSTIMESTAMP" in str(acks_tbl.c.acked_at.server_default.arg).upper()


def test_merge_never_updates_the_existing_timestamp() -> None:
    sql = _text(acks_repo._ack_merge_stmt())

    assert sql.startswith("MERGE INTO ai_agent_acks target")
    assert "ON (target.user_oid = source.user_oid)" in sql
    assert "WHEN NOT MATCHED THEN" in sql
    assert "INSERT (user_oid, acked_at)" in sql
    # 有 WHEN MATCHED 就会把「首次确认时间」刷成「最近一次点击时间」。
    assert "WHEN MATCHED" not in sql


def test_select_reads_the_primary_key() -> None:
    sql = _sql(acks_repo._select_stmt(USER_OID))

    assert "SELECT ai_agent_acks.acked_at" in sql
    assert "FROM ai_agent_acks" in sql
    assert f"ai_agent_acks.user_oid = '{USER_OID}'" in sql


# ---------------------------------------------------------------------------
# Repo behaviour (fake session, no DB)
# ---------------------------------------------------------------------------


class _FakeRow:
    def __init__(self, mapping: dict) -> None:
        self._mapping = mapping


class _FakeResult:
    def __init__(self, mapping: dict | None = None) -> None:
        self._row = _FakeRow(mapping) if mapping is not None else None

    def first(self):
        return self._row


class _FakeSession:
    """只理解本仓储的两种语句：``MERGE`` 与按 user_oid 的 ``SELECT``。"""

    def __init__(self, *, conflict: bool = False) -> None:
        self.rows: dict[str, datetime] = {}
        self.merges = 0
        self.commits = 0
        self.rollbacks = 0
        self.conflict = conflict

    async def execute(self, stmt, params=None):
        sql = _text(stmt)
        if sql.upper().startswith("MERGE"):
            self.merges += 1
            user = params["user_oid"]
            if self.conflict:
                # 模拟并发：另一事务刚好提交了同一 PK，本条 INSERT 撞 ORA-00001。
                self.rows[user] = OTHER_TIME
                raise IntegrityError("merge ai_agent_acks", params, Exception("ORA-00001"))
            self.rows.setdefault(user, params["at"])
            return _FakeResult()
        compiled = _sql(stmt)
        for user, at in self.rows.items():
            if f"'{user}'" in compiled:
                return _FakeResult({"acked_at": at})
        return _FakeResult()

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


def test_repeated_ack_keeps_the_first_timestamp_and_one_row() -> None:
    session = _FakeSession()
    repo = acks_repo.OracleAIAgentAcksRepo(session=session)

    first = asyncio.run(repo.ack(USER_OID, NOW))
    second = asyncio.run(repo.ack(USER_OID, LATER))

    assert first == NOW
    assert second == NOW
    assert len(session.rows) == 1
    assert session.merges == 1
    # 注入的 session 由调用方提交/关闭：仓储不能替它 commit。
    assert session.commits == 0


def test_concurrent_insert_loser_reads_back_the_winner(monkeypatch) -> None:
    """并发两个 POST：输掉 PK 竞态的那个回滚后读回赢家的行，而不是 500。

    真实链路里仓储自己开 session（``owns=True``）。注入 session 时仓储不替
    调用方回滚，会把异常原样抛给上层——这里用自开 session 的形状测。
    """
    session = _FakeSession(conflict=True)

    @asynccontextmanager
    async def fake_scope():
        yield session

    monkeypatch.setattr(acks_repo, "session_scope", fake_scope)
    repo = acks_repo.OracleAIAgentAcksRepo()

    acked_at = asyncio.run(repo.ack(USER_OID, LATER))

    assert acked_at == OTHER_TIME
    assert len(session.rows) == 1
    # 撞 PK 后必须回滚再读，否则 SQLAlchemy 的下一条 SELECT 会 PendingRollbackError。
    assert session.rollbacks == 1
    assert session.commits == 1


def test_injected_session_is_not_rolled_back_on_conflict() -> None:
    """注入的 session 归调用方所有：仓储不能替它 rollback，冲突向上抛。"""
    session = _FakeSession(conflict=True)
    repo = acks_repo.OracleAIAgentAcksRepo(session=session)

    with pytest.raises(IntegrityError):
        asyncio.run(repo.ack(USER_OID, LATER))
    assert session.rollbacks == 0


def test_get_returns_none_until_acked() -> None:
    session = _FakeSession()
    repo = acks_repo.OracleAIAgentAcksRepo(session=session)

    assert asyncio.run(repo.get(USER_OID)) is None
    asyncio.run(repo.ack(USER_OID, NOW))
    assert asyncio.run(repo.get(USER_OID)) == NOW


# ---------------------------------------------------------------------------
# Router contract
# ---------------------------------------------------------------------------


class FakeAcksRepo:
    def __init__(self) -> None:
        self.rows: dict[str, datetime] = {}
        self.calls: list[tuple] = []

    async def get(self, user_oid: str):
        self.calls.append(("get", user_oid))
        return self.rows.get(user_oid)

    async def ack(self, user_oid: str, now: datetime):
        self.calls.append(("ack", user_oid))
        return self.rows.setdefault(user_oid, now)


class FakeAuditRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(self, **fields) -> str:
        self.rows.append(fields)
        return "a" * 24


@pytest.fixture
def ack_env(monkeypatch):
    repo = FakeAcksRepo()
    audit = FakeAuditRepo()
    monkeypatch.setattr(backends, "get_ai_agent_acks_repo", lambda session=None: repo)
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: audit)

    state = {"user": ADMIN}

    app = FastAPI()
    app.include_router(ai_agent_router.router, prefix="/api/ai/agent")
    app.dependency_overrides[get_current_user] = lambda: state["user"]

    return TestClient(app), repo, audit, state


def test_get_before_ack_reports_false(ack_env):
    client, repo, _audit, _state = ack_env

    response = client.get("/api/ai/agent/ack")

    assert response.status_code == 200
    assert response.json() == {"acked": False, "acked_at": None}
    assert response.headers["X-AI-Request-Id"]
    assert repo.calls == [("get", USER_OID)]


def test_post_then_get_keeps_the_first_timestamp(ack_env):
    client, _repo, _audit, _state = ack_env

    first = client.post("/api/ai/agent/ack")
    assert first.status_code == 200
    body = first.json()
    assert body["acked"] is True
    assert datetime.fromisoformat(body["acked_at"]).tzinfo is None
    assert first.headers["X-AI-Request-Id"]

    # 幂等：重复 POST 不刷新时间（仓储返回库里第一行）。
    second = client.post("/api/ai/agent/ack")
    assert second.status_code == 200
    assert second.json() == body

    read_back = client.get("/api/ai/agent/ack")
    assert read_back.status_code == 200
    assert read_back.json() == body


def test_without_ai_access_is_the_openai_403_envelope(ack_env):
    client, repo, audit, state = ack_env
    state["user"] = STUDENT

    for response in (client.get("/api/ai/agent/ack"), client.post("/api/ai/agent/ack")):
        assert response.status_code == 403
        payload = response.json()
        assert payload == {
            "error": {
                "message": "ai service is not available for your role",
                "type": "invalid_request_error",
                "code": "AI_FORBIDDEN",
            }
        }
        assert response.headers["X-AI-Request-Id"]

    # 门禁拒绝落审计（与同文件其它端点同口径），且根本没碰仓储。
    assert len(audit.rows) == 2
    assert all(row["error_code"] == "AI_FORBIDDEN" for row in audit.rows)
    assert repo.calls == []
