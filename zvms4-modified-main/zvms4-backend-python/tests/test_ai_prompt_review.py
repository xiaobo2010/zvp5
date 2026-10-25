"""prompt 审阅端点（``GET /api/ai/prompts`` 与 ``/{oid}``）的冻结契约。

三条不可退让的边界：

* **列表不回正文**：分页只是「看有哪些」，正文只在单条审阅里出现。
* **读正文必须留痕**：``GET /prompts/{oid}`` 每次调用写一条
  ``APP_LOGS``（``type=ai_prompt_access`` / ``level=info``），且审计里**不含正文**。
* **只有 admin**：门禁必须在碰仓储之前，否则未授权用户可以拿端点当探针。

全部离线：仓储与日志都是内存桩。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import settings  # noqa: F401  (services.ai.config 读它，先导入保证顺序一致)
from db import backends
from routers import ai_router
from typings.log import inject_log
from util.object_id import get_current_user

ADMIN = "000000000000000000000001"
USER = "000000000000000000000003"
OID = "a" * 24

CREATED = datetime(2026, 10, 24, 9, 30, 0)


class FakePromptAuditRepo:
    """``AIPromptAuditRepo`` 的内存替身：只实现审阅路径用到的三个方法。"""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = list(rows or [])
        self.page_calls: list[dict] = []
        self.count_calls: list[dict] = []
        self.detail_calls: list[str] = []

    async def list_page(self, *, user_oid=None, since=None, until=None, page=1, page_size=20):
        filters = {"user_oid": user_oid, "since": since, "until": until}
        self.page_calls.append({**filters, "page": page, "page_size": page_size})
        matched = [row for row in self.rows if _matches(row, user_oid, since, until)]
        matched.sort(key=lambda row: (row["created_at"], row["_id"]), reverse=True)
        start = (page - 1) * page_size
        return [_meta(row) for row in matched[start : start + page_size]]

    async def count(self, *, user_oid=None, since=None, until=None):
        self.count_calls.append(
            {"user_oid": user_oid, "since": since, "until": until}
        )
        return len([row for row in self.rows if _matches(row, user_oid, since, until)])

    async def get_by_oid(self, oid: str):
        self.detail_calls.append(oid)
        for row in self.rows:
            if row["_id"] == oid:
                return dict(row)
        return None


def _matches(row: dict, user_oid, since, until) -> bool:
    if user_oid is not None and row["user_oid"] != user_oid:
        return False
    if since is not None and row["created_at"] < since:
        return False
    if until is not None and row["created_at"] > until:
        return False
    return True


def _meta(row: dict) -> dict:
    """仓储 ``_meta_row`` 的形状：**没有** prompt_text。"""
    return {
        "_id": row["_id"],
        "user_oid": row["user_oid"],
        "purpose": row["purpose"],
        "roles_snapshot": row["roles_snapshot"],
        "created_at": row["created_at"],
        "prompt_chars": len(row["prompt_text"]),
    }


def _row(oid: str = OID, *, user_oid: str = USER, created_at: datetime = CREATED) -> dict:
    return {
        "_id": oid,
        "request_id": "req-1",
        "user_oid": user_oid,
        "purpose": "chat",
        "roles_snapshot": "department",
        "prompt_text": "帮我写一份活动通知",
        "created_at": created_at,
    }


class FakeUsersRepo:
    def __init__(self, names: dict[str, str] | None = None) -> None:
        self.names = dict(names or {})

    async def get_names(self, oids: list[str]) -> dict[str, str]:
        return {oid: self.names[oid] for oid in oids if oid in self.names}


class RecordingLog:
    """``ZVMSLog`` 的最小替身：``with_text`` 语义与真类一致（空串不覆盖）。"""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.text = ""
        self.type = ""
        self.level = "info"

    def with_text(self, text: str, type: str = "", level: str = ""):
        self.text = text
        if type:
            self.type = type
        if level:
            self.level = level
        return self

    async def insert_log(self):
        self.rows.append(
            {"data": self.text, "type": self.type, "level": self.level}
        )


def _client(user: dict | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/ai")
    app.dependency_overrides[get_current_user] = lambda: user or {
        "id": ADMIN,
        "per": ["admin"],
    }
    app.dependency_overrides[inject_log] = lambda: RecordingLog()
    return TestClient(app)


@pytest.fixture()
def repo(monkeypatch) -> FakePromptAuditRepo:
    fake = FakePromptAuditRepo([_row()])
    monkeypatch.setattr(
        backends, "get_ai_prompt_audit_repo", lambda session=None: fake
    )
    return fake


@pytest.fixture()
def users(monkeypatch) -> FakeUsersRepo:
    fake = FakeUsersRepo({USER: "张三"})
    monkeypatch.setattr(backends, "get_users_repo", lambda session=None: fake)
    return fake


# ---------------------------------------------------------------------------
# 列表：元数据，不含正文
# ---------------------------------------------------------------------------


def test_list_returns_metadata_without_the_body(repo, users):
    response = _client().get("/api/ai/prompts")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["code"] == 200
    (row,) = body["data"]
    assert "prompt_text" not in row, "列表绝不能回正文"
    assert row["oid"] == OID and row["_id"] == OID
    assert row["user_name"] == "张三"
    assert row["prompt_chars"] == len("帮我写一份活动通知")
    assert row["purpose"] == "chat"
    assert row["roles_snapshot"] == "department"
    assert isinstance(row["created_at"], str)
    assert body["metadata"] == {"size": 1}


def test_list_defaults_to_seven_days_and_page_one(repo, users):
    _client().get("/api/ai/prompts")

    sent = repo.page_calls[0]
    assert (sent["page"], sent["page_size"]) == (1, 20)
    # 滚动窗口：since ≈ 现在 - 7 天（容忍执行耗时）。
    expected = datetime.now() - timedelta(days=7)
    assert abs((sent["since"] - expected).total_seconds()) < 60
    # 总数与列表用同一组过滤条件（否则「页内条数」与「总数」永远对不上）。
    assert repo.count_calls[0]["since"] == sent["since"]
    assert repo.count_calls[0]["user_oid"] == sent["user_oid"]
    assert sent["user_oid"] is None


@pytest.mark.parametrize("params", [{"days": 0}, {"days": 91}, {"page": 0}, {"perpage": 101}])
def test_out_of_range_parameters_are_422(repo, users, params):
    assert _client().get("/api/ai/prompts", params=params).status_code == 422


def test_days_boundaries_are_accepted(repo, users):
    assert _client().get("/api/ai/prompts", params={"days": 1}).status_code == 200
    assert _client().get("/api/ai/prompts", params={"days": 90}).status_code == 200


def test_user_oid_filter_is_normalised(repo, users):
    _client().get("/api/ai/prompts", params={"user_oid": USER.upper()})

    assert repo.page_calls[0]["user_oid"] == USER


def test_invalid_user_oid_is_400(repo, users):
    response = _client().get("/api/ai/prompts", params={"user_oid": "not-an-oid"})

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid Object ID"}
    assert repo.page_calls == []


def test_list_is_admin_only(repo, users):
    response = _client({"id": USER, "per": ["department"]}).get("/api/ai/prompts")

    assert response.status_code == 403
    assert repo.page_calls == [] and repo.count_calls == []


# ---------------------------------------------------------------------------
# 详情：正文 + 访问审计
# ---------------------------------------------------------------------------


def test_detail_returns_the_body_and_writes_an_access_log(repo, users):
    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/ai")
    app.dependency_overrides[get_current_user] = lambda: {
        "id": ADMIN,
        "per": ["admin"],
    }
    log = RecordingLog()
    app.dependency_overrides[inject_log] = lambda: log

    response = TestClient(app).get(f"/api/ai/prompts/{OID}")

    assert response.status_code == 200
    row = response.json()["data"]
    assert row["prompt_text"] == "帮我写一份活动通知"
    assert row["oid"] == OID
    assert row["user_name"] == "张三"

    (audit_row,) = log.rows
    assert audit_row["type"] == "ai_prompt_access"
    # info 是承重取值：它随 APP_LOGS 保留期清理（login_fail 先例）。
    assert audit_row["level"] == "info"
    assert OID in audit_row["data"]
    assert USER in audit_row["data"]
    # 审计里不抄正文：正文已经在被审计的那张表里。
    assert "活动通知" not in audit_row["data"]


def test_detail_404_for_a_malformed_oid_without_touching_the_repo(repo, users):
    response = _client().get("/api/ai/prompts/not-an-oid")

    assert response.status_code == 404
    assert response.json() == {"detail": "AI_PROMPT_NOT_FOUND"}
    assert repo.detail_calls == []


def test_detail_404_for_an_unknown_oid(repo, users):
    missing = "f" * 24

    response = _client().get(f"/api/ai/prompts/{missing}")

    assert response.status_code == 404
    assert response.json() == {"detail": "AI_PROMPT_NOT_FOUND"}
    assert repo.detail_calls == [missing]


def test_detail_is_admin_only(repo, users):
    response = _client({"id": USER, "per": ["department"]}).get(
        f"/api/ai/prompts/{OID}"
    )

    assert response.status_code == 403
    assert repo.detail_calls == []
