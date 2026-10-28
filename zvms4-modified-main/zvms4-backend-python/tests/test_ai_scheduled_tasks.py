"""AI 定时任务 + prompt 审核的离线契约。

三块内容，各自证明一件不同的事：

* **DDL 与模型一致**。``db.ddl_check`` 只比对 V001（基线镜像），V040 是存量实例
  的补丁——它漂了不会有任何自动化报警，只能在这里钉住。顺带断言 V040 里确实
  有 ``ALTER TABLE ai_runs ADD (task_oid ...)``：新列漏了的话，``ai_runs`` 的
  插入会在 Oracle 上直接失败，而单测用的是打桩仓储，根本发现不了。
* **仓储的 SQL 形状**。``claim`` 必须是带 ``next_run_at <=`` 与 ``last_run_at =``
  的 CAS；``mark_result`` 必须推进 ``next_run_at``（漏了会让坏任务被每个 tick
  重试）。这两条一旦写错是静默事故——不会抛异常，只会重复执行/永不执行。
* **HTTP 与调度行为**。配额矩阵、审核门、422 校验、tick 的四个跳过分支，全部
  用打桩仓储跑（不连 Oracle）。

``claim_due`` 的并发语义用「同一份快照被两次 claim」证明（真实仓储的 CAS 由上面
的 SQL 断言钉住，这里钉住调用方确实按快照里的 ``last_run_at`` 去争）。

不连库：所有 SQL 只编译不执行。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import oracle

from db import ddl_check
from db.repos.oracle import ai_scheduled_tasks as repo_module
from db.repos.oracle.ai_scheduled_tasks import OracleAiScheduledTasksRepo
from routers import ai_schedule_router
from services.ai import scheduled_runner as runner
from util.object_id import get_current_user

PREFIX = "/api/ai/scheduled-tasks"

ADMIN = "000000000000000000000001"
DEPARTMENT = "000000000000000000000002"
STUDENT = "000000000000000000000003"
OTHER = "000000000000000000000004"

DIALECT = oracle.dialect()

V040_PATH = (
    Path(__file__).resolve().parents[1]
    / "db"
    / "oracle"
    / "ddl"
    / "V040__ai_scheduled_tasks.sql"
)

NOW = datetime(2026, 10, 27, 3, 0, 0)


def _sql(stmt) -> str:
    return " ".join(
        str(
            stmt.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True})
        ).split()
    )


def _where(stmt) -> str:
    sql = _sql(stmt)
    return sql.split(" WHERE ", 1)[1] if " WHERE " in sql else ""


# ---------------------------------------------------------------------------
# DDL / 模型
# ---------------------------------------------------------------------------

def test_v040_matches_models():
    text = V040_PATH.read_text(encoding="utf-8")
    parsed = ddl_check.parse_schema(ddl_check.extract_execute_immediate(text))
    expected = ddl_check.parse_models()

    assert "ai_scheduled_tasks" in parsed["tables"]
    # V047 起 models 的 created_at 带 DEFAULT SYSTIMESTAMP；V040 是历史事实。
    modeled = expected["tables"]["ai_scheduled_tasks"]
    legacy = {
        **modeled,
        "columns": {
            **modeled["columns"],
            "created_at": ("timestamp", True, None),
        },
    }
    problems = ddl_check._diff_table(
        "ai_scheduled_tasks",
        legacy,
        parsed["tables"]["ai_scheduled_tasks"],
    )
    assert problems == []

    assert ("ai_scheduled_tasks", False, ("owner_oid", "created_at DESC")) in parsed["indexes"]
    assert ("ai_scheduled_tasks", False, ("enabled", "next_run_at")) in parsed["indexes"]


def test_v040_adds_task_oid_to_ai_runs():
    text = V040_PATH.read_text(encoding="utf-8")
    assert "ALTER TABLE ai_runs ADD (task_oid VARCHAR2(24 CHAR))" in text
    assert "ix_ai_runs_task_created" in text
    assert "task_oid" in ddl_check.parse_v001()["tables"]["ai_runs"]["columns"]


def test_models_review_and_schedule_checks():
    """两个枚举必须是 CHECK，不是自由文本：拼错的状态会让任务悄悄永不执行。"""
    from db import models

    cks = {
        str(constraint.sqltext)
        for constraint in models.ai_scheduled_tasks.constraints
        if constraint.__class__.__name__ == "CheckConstraint"
    }
    joined = " ".join(cks).lower()
    assert "review_status in ('draft','pending','approved','rejected')" in joined
    assert "schedule_kind in ('interval','cron')" in joined
    assert "interval_minutes >= 15" in joined


# ---------------------------------------------------------------------------
# 仓储 SQL 形状
# ---------------------------------------------------------------------------

def test_claim_is_cas_on_schedule_and_last_run():
    first = _where(repo_module._claim_stmt("0" * 24, NOW, expected=None))
    assert "next_run_at <=" in first
    assert "enabled = 1" in first
    assert "last_run_at IS NULL" in first

    later = _where(repo_module._claim_stmt("0" * 24, NOW, expected=NOW))
    assert "last_run_at = TO_DATE('2026-10-27 03:00:00'" in later
    assert "next_run_at <=" in later


def test_due_query_only_takes_enabled_scheduled_rows():
    sql = _sql(repo_module._due_stmt(NOW, 10))
    assert "enabled = 1" in sql
    assert "next_run_at IS NOT NULL" in sql
    assert "next_run_at <=" in sql
    assert "ORDER BY ai_scheduled_tasks.next_run_at ASC" in sql


def test_mark_result_advances_next_run_and_counts_runs():
    """跳过分支也要推进 next_run_at，且只有真提交了 run 才加计数。"""
    counted = repo_module._mark_result_stmt(
        "0" * 24,
        last_status="queued",
        last_error=None,
        next_run_at=NOW + timedelta(hours=1),
        counted=True,
        at=NOW,
    )
    sql = _sql(counted)
    assert "next_run_at=" in sql
    assert "run_count=(ai_scheduled_tasks.run_count + 1)" in sql

    skipped = repo_module._mark_result_stmt(
        "0" * 24,
        last_status="skipped_no_key",
        last_error="no key",
        next_run_at=NOW + timedelta(hours=1),
        counted=False,
        at=NOW,
    )
    skipped_sql = _sql(skipped)
    assert "next_run_at=" in skipped_sql
    assert "run_count" not in skipped_sql
    assert "last_status='skipped_no_key'" in skipped_sql


def test_count_active_excludes_rejected():
    """驳回不占配额，否则用户被拒一次就永久失去创建能力。"""
    sql = _sql(repo_module._count_active_stmt(ADMIN))
    assert "review_status != 'rejected'" in sql


class _Row:
    def __init__(self, mapping: dict):
        self._mapping = mapping


class _Result:
    def __init__(self, rows=()):
        self._rows = list(rows)

    def all(self):
        return self._rows


class _SnapshotSession:
    """只回快照的假 session：用来验证 claim_due 的控制流（不执行 SQL）。"""

    def __init__(self, rows):
        self._rows = rows

    async def execute(self, _stmt):
        return _Result(self._rows)


class _LosingRepo(OracleAiScheduledTasksRepo):
    """第二条 CAS 抢输：claim_due 必须把它从返回值里剔除。"""

    def __init__(self, rows):
        super().__init__(session=_SnapshotSession(rows))
        self.attempts: list[tuple[str, object]] = []

    async def claim(self, oid: str, now: datetime, *, expected) -> bool:
        self.attempts.append((oid, expected))
        return oid != "1" * 24


@pytest.mark.asyncio
async def test_claim_due_drops_the_loser():
    rows = [
        _Row(
            {
                "oid": "0" * 24,
                "owner_oid": ADMIN,
                "name": "a",
                "prompt_text": "x",
                "review_status": "approved",
                "reviewed_by_oid": ADMIN,
                "reviewed_at": NOW,
                "review_note": None,
                "schedule_kind": "interval",
                "interval_minutes": 60,
                "cron_expr": None,
                "enabled": 1,
                "model": None,
                "last_run_at": None,
                "next_run_at": NOW,
                "run_count": 0,
                "last_status": None,
                "last_error": None,
                "created_at": NOW,
                "updated_at": NOW,
            }
        ),
        _Row(
            {
                "oid": "1" * 24,
                "owner_oid": ADMIN,
                "name": "b",
                "prompt_text": "y",
                "review_status": "approved",
                "reviewed_by_oid": ADMIN,
                "reviewed_at": NOW,
                "review_note": None,
                "schedule_kind": "interval",
                "interval_minutes": 60,
                "cron_expr": None,
                "enabled": 1,
                "model": None,
                "last_run_at": None,
                "next_run_at": NOW,
                "run_count": 0,
                "last_status": None,
                "last_error": None,
                "created_at": NOW,
                "updated_at": NOW,
            }
        ),
    ]
    repo = _LosingRepo(rows)
    claimed = await repo.claim_due(NOW)
    assert [row["_id"] for row in claimed] == ["0" * 24]
    # 两条都尝试过，且期望值都来自快照（从未跑过 = None）
    assert repo.attempts == [("0" * 24, None), ("1" * 24, None)]


# ---------------------------------------------------------------------------
# 内存仓储（行为替身，与 ctx 契约逐条对应）
# ---------------------------------------------------------------------------

def _task_row(oid="0" * 24, owner=ADMIN, **over) -> dict:
    row = {
        "_id": oid,
        "owner_oid": owner,
        "name": "每日值日总结",
        "prompt_text": "总结今天的值日情况",
        "review_status": "approved",
        "reviewed_by_oid": ADMIN,
        "reviewed_at": NOW,
        "review_note": None,
        "schedule_kind": "interval",
        "interval_minutes": 60,
        "cron_expr": None,
        "enabled": True,
        "model": None,
        "last_run_at": None,
        "next_run_at": NOW,
        "run_count": 0,
        "last_status": None,
        "last_error": None,
        "created_at": NOW,
        "updated_at": NOW,
    }
    row.update(over)
    return row


class FakeRepo:
    """与 :class:`OracleAiScheduledTasksRepo` 同形的内存实现。

    ``claim`` 的两条守卫（``next_run_at <= now`` 与 ``last_run_at == expected``）与
    真实 SQL 一一对应——并发用例靠它们成立。
    """

    def __init__(self, rows=None):
        self.rows = {row["_id"]: row for row in (rows or [])}
        self.runs: dict[str, list[dict]] = {}
        self.marked: list[dict] = []
        self.created: list[dict] = []
        self.run_tasks: dict[str, str] = {}
        self._seq = 0

    # -- CRUD -------------------------------------------------------------
    async def create(self, *, owner_oid, name, prompt_text, schedule_kind,
                     interval_minutes, cron_expr, next_run_at, run_at=None,
                     review_status="draft", model=None):
        self._seq += 1
        oid = f"{self._seq + 9:024x}"
        stamp = run_at or NOW
        row = _task_row(
            oid=oid,
            owner=owner_oid,
            name=name,
            prompt_text=prompt_text,
            schedule_kind=schedule_kind,
            interval_minutes=interval_minutes,
            cron_expr=cron_expr,
            review_status=review_status,
            model=model,
            next_run_at=next_run_at,
            reviewed_by_oid=None,
            reviewed_at=None,
            created_at=stamp,
            updated_at=stamp,
        )
        self.rows[oid] = row
        self.created.append(dict(row))
        return dict(row)

    async def get(self, oid):
        row = self.rows.get(oid)
        return dict(row) if row else None

    async def list_for_owner(self, owner_oid, *, limit=200):
        return [dict(row) for row in self.rows.values() if row["owner_oid"] == owner_oid]

    async def list_all(self, *, owner_oid=None, review_status=None, limit=200, offset=0):
        rows = [dict(row) for row in self.rows.values()]
        if owner_oid:
            rows = [row for row in rows if row["owner_oid"] == owner_oid]
        if review_status:
            rows = [row for row in rows if row["review_status"] == review_status]
        return rows[offset : offset + limit]

    async def update(self, oid, values):
        row = self.rows.get(oid)
        if row is None:
            return False
        row.update({k: v for k, v in values.items() if k in repo_module.UPDATE_COLUMNS})
        return True

    async def delete(self, oid):
        return self.rows.pop(oid, None) is not None

    async def count_active_for_owner(self, owner_oid):
        return len(
            [
                row
                for row in self.rows.values()
                if row["owner_oid"] == owner_oid and row["review_status"] != "rejected"
            ]
        )

    # -- 调度 -------------------------------------------------------------
    async def claim_due(self, now, *, limit=50):
        snapshot = [
            dict(row)
            for row in self.rows.values()
            if row["enabled"]
            and row["next_run_at"] is not None
            and row["next_run_at"] <= now
        ]
        snapshot.sort(key=lambda item: item["next_run_at"])
        claimed = []
        for task in snapshot[:limit]:
            await asyncio.sleep(0)  # 让两个并发 tick 真正交错
            if await self.claim(task["_id"], now, expected=task["last_run_at"]):
                claimed.append(task)
        return claimed

    async def claim(self, oid, now, *, expected):
        row = self.rows.get(oid)
        if row is None or not row["enabled"]:
            return False
        if row["next_run_at"] is None or row["next_run_at"] > now:
            return False
        if row["last_run_at"] != expected:
            return False
        row["last_run_at"] = now
        return True

    async def mark_result(self, oid, *, last_status, next_run_at,
                          last_error=None, counted=False):
        row = self.rows.get(oid)
        if row is None:
            return False
        row["last_status"] = last_status
        row["last_error"] = last_error
        row["next_run_at"] = next_run_at
        if counted:
            row["run_count"] += 1
        self.marked.append({"oid": oid, "status": last_status, "next": next_run_at})
        return True

    async def list_runs(self, task_oid, *, page=1, size=20):
        rows = sorted(
            self.runs.get(task_oid, []),
            key=lambda item: item["created_at"],
            reverse=True,
        )
        offset = (page - 1) * size
        return rows[offset : offset + size], len(rows)

    async def last_conversation_oid(self, task_oid):
        runs = self.runs.get(task_oid) or []
        return runs[-1]["conversation_oid"] if runs else None

    async def attach_run_task(self, run_id, task_oid):
        self.run_tasks[run_id] = task_oid
        return True


class FakeRuns:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.active: list[dict] = []
        self.inserted: list[dict] = []

    async def list_active(self, owner_oid):
        return [dict(row) for row in self.active if row["owner_oid"] == owner_oid]

    async def insert_queued(self, *, run_id, conversation_oid, owner_oid,
                            message_oid, content, client_msg_id=None, model=None):
        self.inserted.append(
            {
                "run_id": run_id,
                "conversation_oid": conversation_oid,
                "owner_oid": owner_oid,
                "content": content,
                "client_msg_id": client_msg_id,
                "model": model,
            }
        )
        row = {"_id": run_id, "message_oid": message_oid}
        self.rows[run_id] = row
        return row, True


class FakeUsers:
    def __init__(self, users):
        self.users = users

    async def get_by_oid(self, oid):
        return self.users.get(oid)


@pytest.fixture
def repo():
    return FakeRepo()


@pytest.fixture
def users():
    return FakeUsers(
        {
            ADMIN: {"_id": ADMIN, "id": ADMIN, "roles": ["admin"]},
            DEPARTMENT: {"_id": DEPARTMENT, "id": DEPARTMENT, "roles": ["department"]},
            STUDENT: {"_id": STUDENT, "id": STUDENT, "roles": ["student"]},
            OTHER: {"_id": OTHER, "id": OTHER, "roles": ["student"]},
        }
    )


@pytest.fixture
def runs():
    return FakeRuns()


@pytest.fixture
def api(monkeypatch, repo):
    """HTTP 面：注入当前用户 + 打桩仓储（不连 Oracle）。"""
    state = {"user": {"id": ADMIN, "per": ["admin"]}}

    monkeypatch.setattr(runner, "_repo", lambda: repo)

    app = FastAPI()
    app.include_router(ai_schedule_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: state["user"]

    client = TestClient(app)
    client.state = state  # type: ignore[attr-defined]
    return client


def _as(client, oid: str, roles: list[str]) -> None:
    client.state["user"] = {"id": oid, "per": roles}  # type: ignore[attr-defined]


def _create(client, **over):
    body = {
        "name": "每日值日总结",
        "prompt_text": "总结今天的值日情况",
        "schedule_kind": "interval",
        "interval_minutes": 60,
    }
    body.update(over)
    return client.post(PREFIX, json=body)


# ---------------------------------------------------------------------------
# 配额
# ---------------------------------------------------------------------------

def test_admin_can_create_many(api):
    _as(api, ADMIN, ["admin"])
    for index in range(3):
        response = _create(api, name=f"任务 {index}")
        assert response.status_code == 201, response.text
        assert response.json()["data"]["review_status"] == "pending"
        assert response.json()["data"]["owner_oid"] == ADMIN


def test_department_limited_to_one(api):
    _as(api, DEPARTMENT, ["department"])
    assert _create(api).status_code == 201
    second = _create(api, name="第二条")
    assert second.status_code == 409
    assert second.json()["detail"] == "AI_SCHEDULE_QUOTA_EXCEEDED"


def test_rejected_task_does_not_consume_quota(api, repo):
    _as(api, DEPARTMENT, ["department"])
    created = _create(api).json()["data"]
    repo.rows[created["oid"]]["review_status"] = "rejected"
    assert _create(api, name="再来一条").status_code == 201


def test_student_cannot_create(api):
    _as(api, STUDENT, ["student"])
    response = _create(api)
    assert response.status_code == 403
    assert response.json()["detail"] == "AI_SCHEDULE_FORBIDDEN"


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def test_interval_minimum_is_fifteen(api):
    _as(api, ADMIN, ["admin"])
    response = _create(api, interval_minutes=14)
    assert response.status_code == 422
    assert response.json()["detail"] == "AI_SCHEDULE_BAD_INTERVAL"


def test_bad_cron_is_422(api):
    _as(api, ADMIN, ["admin"])
    response = _create(
        api, schedule_kind="cron", cron_expr="every-morning", interval_minutes=60
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "AI_SCHEDULE_BAD_CRON"


def test_valid_cron_sets_next_run(api):
    _as(api, ADMIN, ["admin"])
    response = _create(
        api, schedule_kind="cron", cron_expr="0 3 * * *", interval_minutes=60
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    assert data["schedule_kind"] == "cron"
    assert data["next_run_at"] is not None
    assert data["next_run_at"].endswith("03:00:00")


def test_bad_schedule_kind_is_422(api):
    _as(api, ADMIN, ["admin"])
    response = _create(api, schedule_kind="hourly")
    assert response.status_code == 422
    assert response.json()["detail"] == "AI_SCHEDULE_BAD_KIND"


# ---------------------------------------------------------------------------
# 审核门 / PATCH
# ---------------------------------------------------------------------------

def _approve(client, oid: str):
    return client.post(f"{PREFIX}/{oid}/review", json={"decision": "approved"})


def test_review_is_admin_only(api):
    _as(api, ADMIN, ["admin"])
    oid = _create(api).json()["data"]["oid"]
    _as(api, DEPARTMENT, ["department"])
    assert _approve(api, oid).status_code == 403
    _as(api, ADMIN, ["admin"])
    ok = _approve(api, oid)
    assert ok.status_code == 200, ok.text
    assert ok.json()["data"]["review_status"] == "approved"
    assert ok.json()["data"]["reviewed_by_oid"] == ADMIN


def test_list_scoped_to_owner(api):
    _as(api, ADMIN, ["admin"])
    oid = _create(api).json()["data"]["oid"]
    # 用 department（不是 student）当「另一个用户」：`ai.schedule` 开关落地后 student
    # 默认全关，list 会在角色之后先吃 403，那样就测不到 owner 隔离了。
    _as(api, OTHER, ["department"])
    assert api.get(PREFIX).json()["data"] == []
    forbidden = api.get(PREFIX, params={"owner_oid": ADMIN})
    assert forbidden.status_code == 403
    _as(api, ADMIN, ["admin"])
    assert [row["oid"] for row in api.get(PREFIX, params={"owner_oid": ADMIN}).json()["data"]] == [oid]


def test_admin_can_list_across_owners_and_filter_status(api):
    """管理员审核队列：跨用户 + 按 review_status 过滤（前端「待审核」页要用）。"""
    _as(api, DEPARTMENT, ["department"])
    pending_oid = _create(api).json()["data"]["oid"]
    _as(api, ADMIN, ["admin"])
    approved_oid = _create(api, name="管理员任务").json()["data"]["oid"]
    assert _approve(api, approved_oid).status_code == 200

    every = api.get(PREFIX, params={"all_tasks": 1})
    assert every.status_code == 200, every.text
    assert {row["oid"] for row in every.json()["data"]} == {pending_oid, approved_oid}

    pending = api.get(PREFIX, params={"all_tasks": 1, "review_status": "pending"})
    assert pending.status_code == 200, pending.text
    assert [row["oid"] for row in pending.json()["data"]] == [pending_oid]


def test_all_tasks_view_is_admin_only(api):
    _as(api, DEPARTMENT, ["department"])
    assert api.get(PREFIX, params={"all_tasks": 1}).status_code == 403
    assert api.get(PREFIX, params={"review_status": "pending"}).status_code == 403


def test_list_all_rejects_unknown_status(api):
    _as(api, ADMIN, ["admin"])
    response = api.get(PREFIX, params={"all_tasks": 1, "review_status": "bogus"})
    assert response.status_code == 422
    assert response.json()["detail"] == "AI_SCHEDULE_BAD_STATUS"


def test_patch_prompt_resets_review(api):
    _as(api, ADMIN, ["admin"])
    oid = _create(api).json()["data"]["oid"]
    _approve(api, oid)

    response = api.patch(f"{PREFIX}/{oid}", json={"prompt_text": "换一段文案"})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["review_status"] == "draft"
    assert data["reviewed_at"] is None
    assert data["reviewed_by_oid"] is None
    assert data["prompt_text"] == "换一段文案"


def test_patch_interval_recomputes_next_run(api, repo):
    _as(api, ADMIN, ["admin"])
    oid = _create(api).json()["data"]["oid"]
    before = repo.rows[oid]["next_run_at"]
    response = api.patch(f"{PREFIX}/{oid}", json={"interval_minutes": 120})
    assert response.status_code == 200, response.text
    after = repo.rows[oid]["next_run_at"]
    assert after > before
    assert after - datetime.now() <= timedelta(minutes=121)


def test_patch_interval_below_minimum_is_422(api):
    _as(api, ADMIN, ["admin"])
    oid = _create(api).json()["data"]["oid"]
    assert api.patch(f"{PREFIX}/{oid}", json={"interval_minutes": 5}).status_code == 422


def test_patch_foreign_task_is_403(api):
    _as(api, ADMIN, ["admin"])
    oid = _create(api).json()["data"]["oid"]
    _as(api, OTHER, ["student"])
    assert api.patch(f"{PREFIX}/{oid}", json={"name": "抢"}).status_code == 403
    assert api.delete(f"{PREFIX}/{oid}").status_code == 403


def test_delete_task(api, repo):
    _as(api, ADMIN, ["admin"])
    oid = _create(api).json()["data"]["oid"]
    assert api.delete(f"{PREFIX}/{oid}").status_code == 204
    assert oid not in repo.rows


def test_run_now_requires_approval(api):
    _as(api, ADMIN, ["admin"])
    oid = _create(api).json()["data"]["oid"]

    pending = api.post(f"{PREFIX}/{oid}/run-now")
    assert pending.status_code == 409
    assert pending.json()["detail"] == "AI_SCHEDULE_NOT_APPROVED"

    api.post(f"{PREFIX}/{oid}/review", json={"decision": "rejected"})
    rejected = api.post(f"{PREFIX}/{oid}/run-now")
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "AI_SCHEDULE_NOT_APPROVED"

    api.patch(f"{PREFIX}/{oid}", json={"prompt_text": "改一下"})
    draft = api.post(f"{PREFIX}/{oid}/run-now")
    assert draft.status_code == 409
    assert draft.json()["detail"] == "AI_SCHEDULE_NOT_APPROVED"


def test_run_now_approved_submits(api, monkeypatch, repo):
    _as(api, ADMIN, ["admin"])
    oid = _create(api).json()["data"]["oid"]
    _approve(api, oid)

    async def fake_run_now(task, *, now=None):
        return "f" * 32

    monkeypatch.setattr(ai_schedule_router.runner, "run_now", fake_run_now)
    response = api.post(f"{PREFIX}/{oid}/run-now")
    assert response.status_code == 202, response.text
    assert response.json()["data"] == {"run_id": "f" * 32, "status": "queued"}
    assert repo.rows[oid]["run_count"] == 1


def test_runs_history_pagination(api, repo):
    _as(api, ADMIN, ["admin"])
    oid = _create(api).json()["data"]["oid"]
    repo.runs[oid] = [
        {
            "run_id": f"{index:032x}",
            "_id": f"{index:032x}",
            "status": "succeeded",
            "model": "m",
            "stop_reason": "completed",
            "error_code": None,
            "error_message": None,
            "created_at": NOW + timedelta(minutes=index),
            "started_at": NOW + timedelta(minutes=index),
            "finished_at": NOW + timedelta(minutes=index, seconds=30),
        }
        for index in range(3)
    ]
    response = api.get(f"{PREFIX}/{oid}/runs", params={"page": 1, "size": 2})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["metadata"] == {"size": 3, "page": 1, "perpage": 2}
    assert len(body["data"]) == 2
    assert body["data"][0]["created_at"].startswith("2026-10-27T03:02")


# ---------------------------------------------------------------------------
# 调度（tick）
# ---------------------------------------------------------------------------

class _Verdict:
    hit = False
    rule = None


@pytest.fixture
def env(monkeypatch, repo, runs, users):
    """tick 的依赖面：仓储 / run 队列 / 用户 / key / 额度 / 会话 / 泵。"""
    catalog: list[dict] = [{"name": "acme", "models": ["m1"]}]
    quota_state = {"allowed": True, "used": 1, "limit": 100}
    kicks: list[int] = []
    conversations: list[str] = []

    async def fake_catalog(owner_oid):
        return list(catalog)

    async def fake_peek(user_oid, kind, amount=1, *, roles=None):
        return quota_state["allowed"], quota_state["used"], quota_state["limit"]

    async def fake_create(*, user, mode, title=None, model=None):
        oid = f"c{len(conversations):031x}"
        conversations.append(oid)
        return {"oid": oid, "mode": mode, "title": title}

    async def fake_kick():
        kicks.append(1)

    monkeypatch.setattr(runner, "_repo", lambda: repo)
    monkeypatch.setattr(runner, "_runs", lambda: runs)
    monkeypatch.setattr(runner, "_users", lambda: users)
    monkeypatch.setattr(runner.credentials, "catalog", fake_catalog)
    monkeypatch.setattr(runner.quota, "peek", fake_peek)
    monkeypatch.setattr(runner.conversations, "create", fake_create)
    monkeypatch.setattr(runner.run_queue, "kick", fake_kick)
    monkeypatch.setattr(runner.moderation, "check", lambda text: _Verdict())

    return SimpleNamespace(
        repo=repo,
        runs=runs,
        catalog=catalog,
        quota=quota_state,
        kicks=kicks,
        conversations=conversations,
    )


def _seed(repo, **over):
    row = _task_row(**over)
    repo.rows[row["_id"]] = row
    return row


@pytest.mark.asyncio
async def test_tick_submits_approved_task(env):
    task = _seed(env.repo)

    summary = await runner.tick(NOW)

    assert summary == {"claimed": 1, "queued": 1, "skipped": 0, "failed": 0, "errors": 0}
    assert env.repo.rows[task["_id"]]["last_status"] == runner.STATUS_QUEUED
    assert env.repo.rows[task["_id"]]["run_count"] == 1
    assert env.repo.rows[task["_id"]]["next_run_at"] == NOW + timedelta(minutes=60)
    assert len(env.runs.inserted) == 1
    assert env.runs.inserted[0]["content"] == task["prompt_text"]
    assert env.runs.inserted[0]["client_msg_id"] == f"sched:{task['_id']}:202610270300"
    assert env.repo.run_tasks == {env.runs.inserted[0]["run_id"]: task["_id"]}
    assert env.kicks == [1]
    assert env.conversations  # 第一次执行要建一个 agent 会话


@pytest.mark.asyncio
async def test_tick_reuses_previous_conversation(env):
    task = _seed(env.repo)
    env.repo.runs[task["_id"]] = [
        {"run_id": "a" * 32, "conversation_oid": "d" * 32, "created_at": NOW}
    ]

    await runner.tick(NOW)

    assert env.runs.inserted[0]["conversation_oid"] == "d" * 32
    assert env.conversations == []


@pytest.mark.asyncio
async def test_tick_skips_unreviewed(env):
    task = _seed(env.repo, review_status="draft")

    summary = await runner.tick(NOW)

    row = env.repo.rows[task["_id"]]
    assert summary["skipped"] == 1
    assert row["last_status"] == runner.STATUS_SKIPPED_REVIEW
    assert row["next_run_at"] == NOW + timedelta(minutes=60)
    assert env.runs.inserted == []


@pytest.mark.asyncio
async def test_tick_skips_when_owner_busy(env):
    task = _seed(env.repo)
    env.runs.active.append({"owner_oid": ADMIN, "status": "running"})

    await runner.tick(NOW)

    row = env.repo.rows[task["_id"]]
    assert row["last_status"] == runner.STATUS_SKIPPED_BUSY
    assert row["next_run_at"] == NOW + timedelta(minutes=60)
    assert env.runs.inserted == []


@pytest.mark.asyncio
async def test_tick_skips_banned_owner(env, users):
    """封禁账号不该在后台继续花额度/用 key。"""
    task = _seed(env.repo)
    users.users[ADMIN]["banned"] = True

    await runner.tick(NOW)

    row = env.repo.rows[task["_id"]]
    assert row["last_status"] == runner.STATUS_SKIPPED_OWNER
    assert row["last_error"] == "task owner is banned"
    assert env.runs.inserted == []


@pytest.mark.asyncio
async def test_tick_skips_without_key(env):
    task = _seed(env.repo)
    env.catalog.clear()

    await runner.tick(NOW)

    row = env.repo.rows[task["_id"]]
    assert row["last_status"] == runner.STATUS_SKIPPED_NO_KEY
    assert row["next_run_at"] == NOW + timedelta(minutes=60)


@pytest.mark.asyncio
async def test_tick_skips_when_quota_exhausted(env):
    task = _seed(env.repo)
    env.quota.update({"allowed": False, "used": 100, "limit": 100})

    await runner.tick(NOW)

    row = env.repo.rows[task["_id"]]
    assert row["last_status"] == runner.STATUS_SKIPPED_QUOTA
    assert row["last_error"] and "quota" in row["last_error"]
    assert row["next_run_at"] == NOW + timedelta(minutes=60)


@pytest.mark.asyncio
async def test_tick_survives_a_broken_task(env, monkeypatch):
    """一条任务抛异常不能影响同批其它任务，也不能把 next_run_at 留在过去。"""
    broken = _seed(env.repo, _id="a" * 24)
    healthy = _seed(env.repo, _id="b" * 24, created_at=NOW)

    async def boom(task, *, now):
        if task["_id"] == broken["_id"]:
            raise RuntimeError("上游炸了")
        return "e" * 32

    monkeypatch.setattr(runner, "submit_task_run", boom)

    summary = await runner.tick(NOW)

    assert summary["claimed"] == 2
    assert summary["queued"] == 1
    assert summary["failed"] == 1
    assert env.repo.rows[broken["_id"]]["last_status"] == runner.STATUS_FAILED
    assert env.repo.rows[broken["_id"]]["next_run_at"] > NOW
    assert env.repo.rows[healthy["_id"]]["last_status"] == runner.STATUS_QUEUED


@pytest.mark.asyncio
async def test_concurrent_ticks_claim_once(env):
    """同一任务被两个 tick 同时抢：只有一个能提交（claim 的 CAS）。"""
    task = _seed(env.repo)

    first, second = await asyncio.gather(runner.tick(NOW), runner.tick(NOW))

    assert first["queued"] + second["queued"] == 1
    assert len(env.runs.inserted) == 1
    assert env.repo.rows[task["_id"]]["run_count"] == 1


@pytest.mark.asyncio
async def test_second_tick_does_not_reclaim_before_next_run(env):
    task = _seed(env.repo)
    await runner.tick(NOW)

    again = await runner.tick(NOW)

    assert again["claimed"] == 0
    assert len(env.runs.inserted) == 1
    assert env.repo.rows[task["_id"]]["run_count"] == 1


@pytest.mark.asyncio
async def test_claim_recovers_after_crash(env):
    """领取后进程崩溃（没写 mark_result）：下一次 tick 用新快照仍能领到。"""
    task = _seed(env.repo)
    assert await env.repo.claim_due(NOW) != []
    # 模拟崩溃：next_run_at 仍在过去、last_run_at 已被 claim 写成本次时刻；
    # 新快照里的 expected 就是那个时间戳，所以下一次 tick 仍能领到。
    claimed_again = await env.repo.claim_due(NOW)
    assert [row["_id"] for row in claimed_again] == [task["_id"]]


@pytest.mark.asyncio
async def test_run_now_rejects_foreign_and_unapproved(env):
    task = _seed(env.repo, review_status="pending")
    with pytest.raises(runner.ScheduledError) as excinfo:
        await runner.run_now(task)
    assert excinfo.value.code == "AI_SCHEDULE_NOT_APPROVED"
    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
async def test_run_now_submits_and_counts(env):
    task = _seed(env.repo)
    run_id = await runner.run_now(task, now=NOW)
    assert run_id
    assert env.repo.run_tasks[run_id] == task["_id"]
    assert env.runs.inserted[0]["client_msg_id"].startswith(f"sched:{task['_id']}:")


@pytest.mark.asyncio
async def test_run_now_reports_quota_exhausted(env):
    task = _seed(env.repo)
    env.quota["allowed"] = False
    with pytest.raises(runner.ScheduledError) as excinfo:
        await runner.run_now(task, now=NOW)
    assert excinfo.value.code == "AI_SCHEDULE_QUOTA_EXHAUSTED"
    assert excinfo.value.status_code == 429


# ---------------------------------------------------------------------------
# 排程计算
# ---------------------------------------------------------------------------

def test_next_run_for_interval_is_at_least_fifteen_minutes():
    task = {"schedule_kind": "interval", "interval_minutes": 1}
    assert runner.next_run_at(task, after=NOW) == NOW + timedelta(minutes=15)


def test_next_run_for_cron_uses_shanghai_timezone():
    task = {"schedule_kind": "cron", "cron_expr": "0 3 * * *"}
    upcoming = runner.next_run_at(task, after=NOW.replace(hour=4))
    assert upcoming == datetime(2026, 10, 28, 3, 0, 0)


def test_status_constants_fit_the_column():
    """``last_status`` 是 VARCHAR2(16)：常量超长会变成 ORA-12899。"""
    for value in (
        runner.STATUS_QUEUED,
        runner.STATUS_FAILED,
        runner.STATUS_SKIPPED_REVIEW,
        runner.STATUS_SKIPPED_BUSY,
        runner.STATUS_SKIPPED_NO_KEY,
        runner.STATUS_SKIPPED_QUOTA,
        runner.STATUS_SKIPPED_OWNER,
        runner.STATUS_SKIPPED_FLAGGED,
    ):
        assert len(value) <= 16, value
