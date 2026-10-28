"""``/api/ai/quota/requests`` 与 ``/api/ai/quota/users`` 的契约。

覆盖五件事：

* 提交：建 pending 行、同月同能力重复提交 409、非法 kind / 负上限 400；
* 权限：admin 与「有 ``quota.manage`` 的角色」可审批，学生与无该开关的
  department 一律 403；``GET /users`` 只认 admin；
* 通过：默认重置已用量（复用 ``quota.reset_used``）、可顺便设上限
  （``set_user_limit``）、落终态 + 审计；非 pending 再批 409；
* 拒绝：只置状态与备注，**不动**额度；
* 管理表格：``used`` / ``limit`` / ``source`` / ``pending_request`` 四件套。

全部离线：申请仓储、额度仓储、用户仓储都是内存桩，服务的状态机/校验、路由的
权限与响应包装、``quota.snapshot`` 的「source 口径」都是真实代码。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends, ddl_check
from routers import ai_quota_requests_router
from services.ai import quota as quota_service
from services.ai import quota_requests as requests_service
from services.roles import policy
from util.object_id import get_current_user

PREFIX = "/api/ai/quota"

ADMIN_OID = "64b7f0c2e1a3b4c5d6e7fa01"
STAFF_OID = "64b7f0c2e1a3b4c5d6e7fa02"
STUDENT_OID = "64b7f0c2e1a3b4c5d6e7fa03"
OTHER_OID = "64b7f0c2e1a3b4c5d6e7fa04"

ADMIN = {"id": ADMIN_OID, "per": ["admin"]}
STAFF = {"id": STAFF_OID, "per": ["department"]}
STUDENT = {"id": STUDENT_OID, "per": ["student"]}

NAMES = {
    ADMIN_OID: ("管理员", "20240001"),
    STAFF_OID: ("学生会", "20240002"),
    STUDENT_OID: ("小明", "20240003"),
    OTHER_OID: ("小红", "20240004"),
}
ROLES = {
    ADMIN_OID: ["admin"],
    STAFF_OID: ["department"],
    STUDENT_OID: ["student"],
    OTHER_OID: ["student"],
}


# ---------------------------------------------------------------------------
# 内存桩
# ---------------------------------------------------------------------------


class _FakeRequestsRepo:
    """``AIQuotaRequestsRepo`` 的内存实现：语义与 SQL 版一致（含条件更新）。"""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self._seq = 0

    def seed(
        self,
        user_oid: str,
        kind: str = "chat",
        status: str = "pending",
        *,
        period: str | None = None,
        requested_limit: int | None = None,
        minutes_ago: int = 5,
    ) -> dict:
        self._seq += 1
        oid = f"{self._seq:024x}"
        created = datetime.now() - timedelta(minutes=minutes_ago)
        row = {
            "_id": oid,
            "user_oid": user_oid,
            "period": period or quota_service.period_now(),
            "kind": kind,
            "status": status,
            "reason": "本月额度用完了",
            "requested_limit": requested_limit,
            "decided_by": None,
            "decided_at": None,
            "decision_note": None,
            "created_at": created,
            "updated_at": created,
        }
        self.rows[oid] = row
        return row

    async def create(
        self,
        user_oid: str,
        period: str,
        kind: str,
        *,
        reason: str | None = None,
        requested_limit: int | None = None,
    ) -> dict:
        self._seq += 1
        oid = f"{self._seq:024x}"
        now = datetime.now()
        row = {
            "_id": oid,
            "user_oid": user_oid,
            "period": period,
            "kind": kind,
            "status": "pending",
            "reason": reason,
            "requested_limit": requested_limit,
            "decided_by": None,
            "decided_at": None,
            "decision_note": None,
            "created_at": now,
            "updated_at": now,
        }
        self.rows[oid] = row
        return dict(row)

    async def get(self, oid: str) -> dict | None:
        row = self.rows.get(oid)
        return dict(row) if row is not None else None

    async def find_pending(
        self, user_oid: str, period: str, kind: str
    ) -> dict | None:
        hits = [
            row
            for row in self.rows.values()
            if row["user_oid"] == user_oid
            and row["period"] == period
            and row["kind"] == kind
            and row["status"] == "pending"
        ]
        if not hits:
            return None
        return dict(max(hits, key=lambda row: row["created_at"]))

    async def list_page(
        self, status: str | None, *, page: int = 1, size: int = 20
    ) -> tuple[list[dict], int]:
        rows = [
            row
            for row in self.rows.values()
            if status is None or row["status"] == status
        ]
        rows.sort(key=lambda row: (row["created_at"], row["_id"]), reverse=True)
        start = (page - 1) * size
        return [dict(row) for row in rows[start : start + size]], len(rows)

    async def list_for_user(self, user_oid: str, limit: int = 10) -> list[dict]:
        rows = [row for row in self.rows.values() if row["user_oid"] == user_oid]
        rows.sort(key=lambda row: (row["created_at"], row["_id"]), reverse=True)
        return [dict(row) for row in rows[:limit]]

    async def decide(
        self,
        oid: str,
        status: str,
        *,
        decided_by: str,
        note: str | None = None,
    ) -> dict | None:
        row = self.rows.get(oid)
        if row is None or row["status"] != "pending":
            return None
        row["status"] = status
        row["decided_by"] = decided_by
        row["decided_at"] = datetime.now()
        row["decision_note"] = note
        row["updated_at"] = row["decided_at"]
        return dict(row)

    async def pending_user_oids(
        self, user_oids: list[str], period: str
    ) -> set[str]:
        return {
            row["user_oid"]
            for row in self.rows.values()
            if row["user_oid"] in set(user_oids)
            and row["period"] == period
            and row["status"] == "pending"
        }


class _FakeQuotaRepo:
    """``AIQuotaRepo`` 的内存桩；``reset_used`` / ``set_limit`` 都记调用。"""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], dict] = {}
        self.resets: list[tuple[str, str, str]] = []
        self.limits: list[tuple[str, str, str, int]] = []

    def seed(self, user_oid: str, kind: str, used: int, limit: int) -> None:
        period = quota_service.period_now()
        self.rows[(user_oid, period, kind)] = {
            "_id": "0" * 24,
            "user_oid": user_oid,
            "period": period,
            "kind": kind,
            "used": used,
            "limit_count": limit,
            "updated_at": None,
        }

    async def get(self, user_oid: str, period: str, kind: str):
        row = self.rows.get((user_oid, period, kind))
        return dict(row) if row is not None else None

    async def consume(
        self, user_oid: str, period: str, kind: str, amount: int, default_limit: int
    ):
        row = self.rows.get((user_oid, period, kind))
        if row is None:
            return False, 0, default_limit
        if row["used"] + amount > row["limit_count"]:
            return False, row["used"], row["limit_count"]
        row["used"] += amount
        return True, row["used"], row["limit_count"]

    async def set_limit(
        self, user_oid: str, period: str, kind: str, limit_count: int
    ) -> None:
        self.limits.append((user_oid, period, kind, limit_count))
        row = self.rows.get((user_oid, period, kind))
        if row is None:
            self.rows[(user_oid, period, kind)] = {
                "_id": "0" * 24,
                "user_oid": user_oid,
                "period": period,
                "kind": kind,
                "used": 0,
                "limit_count": limit_count,
                "updated_at": None,
            }
            return
        row["limit_count"] = limit_count

    async def reset_used(self, user_oid: str, period: str, kind: str) -> int:
        self.resets.append((user_oid, period, kind))
        row = self.rows.get((user_oid, period, kind))
        if row is None:
            return 0
        row["used"] = 0
        return 1

    async def refund(self, user_oid: str, period: str, kind: str, amount: int) -> int:
        return 0


class _FakeUsersRepo:
    """``UsersRepo`` 的最小替身：``search`` / ``get_many_by_oids`` / ``get_for_auth``。"""

    def __init__(self, oids: list[str] | None = None) -> None:
        self.oids = list(oids or [])
        self.calls: list[dict] = []

    def _doc(self, oid: str) -> dict:
        name, business_id = NAMES[oid]
        return {
            "_id": oid,
            "name": name,
            "id": business_id,
            "class_id": None,
            "roles": list(ROLES[oid]),
        }

    async def search(self, query: str, logged_in: bool, limit=None, offset: int = 0):
        self.calls.append({"query": query, "limit": limit, "offset": offset})
        hits = [
            oid
            for oid in self.oids
            if not query
            or query in NAMES[oid][0]
            or query in NAMES[oid][1]
        ]
        page = hits[offset : (offset + limit) if limit is not None else None]
        rows = [
            {
                "_id": oid,
                "name": NAMES[oid][0],
                "id": NAMES[oid][1],
                "class_id": None,
            }
            for oid in page
        ]
        return rows, len(hits)

    async def get_many_by_oids(self, oids: list[str]) -> dict[str, dict]:
        return {oid: self._doc(oid) for oid in oids if oid in NAMES}

    async def get_for_auth(self, oid: str):
        if oid not in NAMES:
            return None
        return {"_id": oid, "roles": list(ROLES[oid])}


@pytest.fixture(autouse=True)
def _fresh_policy() -> None:
    """角色策略快照是模块级全局：每个用例从「库里没有任何覆盖」开始。"""
    policy.reset_cache()
    yield
    policy.reset_cache()


@pytest.fixture
def api(monkeypatch):
    repo = _FakeRequestsRepo()
    quota_repo = _FakeQuotaRepo()
    users = _FakeUsersRepo([ADMIN_OID, STAFF_OID, STUDENT_OID, OTHER_OID])
    audits: list[dict] = []

    async def _record_audit(operator_oid: str, **kwargs):
        audits.append({"operator": operator_oid, **kwargs})

    monkeypatch.setattr(requests_service, "_repo", lambda: repo)
    monkeypatch.setattr(backends, "get_ai_quota_repo", lambda *a, **k: quota_repo)
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: users)
    monkeypatch.setattr(requests_service, "audit_system_op", _record_audit)

    app = FastAPI()
    app.include_router(ai_quota_requests_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    return TestClient(app), repo, quota_repo, audits, users


def _as(client: TestClient, user: dict) -> None:
    client.app.dependency_overrides[get_current_user] = lambda: user


def _grant_quota_manage(role: str = "department", enabled: bool = True) -> None:
    """给角色开关打补丁：策略层的唯一状态就是这个进程内快照。"""
    policy._features[(role, "quota.manage")] = enabled


# ---------------------------------------------------------------------------
# 提交
# ---------------------------------------------------------------------------


def test_submit_creates_a_pending_request(api) -> None:
    client, repo, _quota_repo, _audits, _users = api
    _as(client, STUDENT)

    response = client.post(
        f"{PREFIX}/requests",
        json={"kind": "chat", "reason": "本月对话额度用完了", "requested_limit": 500},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "ok"
    assert body["code"] == 201
    data = body["data"]
    assert data["kind"] == "chat"
    assert data["status"] == "pending"
    assert data["user_oid"] == STUDENT_OID
    assert data["period"] == quota_service.period_now()
    assert data["reason"] == "本月对话额度用完了"
    assert data["requested_limit"] == 500
    assert len(data["_id"]) == 24
    assert repo.rows[data["_id"]]["user_oid"] == STUDENT_OID


def test_submit_keeps_zero_requested_limit_distinct_from_null(api) -> None:
    """``0`` = 「上限设成 0」，``NULL`` = 「只要求重置」：不能折叠成一个。"""
    client, repo, _quota_repo, _audits, _users = api
    _as(client, STUDENT)

    zero = client.post(
        f"{PREFIX}/requests", json={"kind": "chat", "requested_limit": 0}
    ).json()["data"]
    plain = client.post(f"{PREFIX}/requests", json={"kind": "ocr"}).json()["data"]

    assert zero["requested_limit"] == 0
    assert plain["requested_limit"] is None
    assert repo.rows[zero["_id"]]["requested_limit"] == 0


def test_submit_rejects_a_second_pending_request(api) -> None:
    client, repo, _quota_repo, _audits, _users = api
    _as(client, STUDENT)
    repo.seed(STUDENT_OID, "chat")

    response = client.post(f"{PREFIX}/requests", json={"kind": "chat"})

    assert response.status_code == 409
    assert response.json()["detail"] == "AI_QUOTA_REQUEST_PENDING"
    assert len(repo.rows) == 1


def test_pending_is_per_kind_and_per_user(api) -> None:
    """chat 有 pending 不挡 ocr；别人有 pending 不挡我。"""
    client, _repo, _quota_repo, _audits, _users = api
    _as(client, STUDENT)

    client.post(f"{PREFIX}/requests", json={"kind": "chat"})
    other_kind = client.post(f"{PREFIX}/requests", json={"kind": "ocr"})
    _as(client, {"id": OTHER_OID, "per": ["student"]})
    other_user = client.post(f"{PREFIX}/requests", json={"kind": "chat"})

    assert other_kind.status_code == 201
    assert other_user.status_code == 201


def test_submit_allowed_again_after_the_previous_one_is_decided(api) -> None:
    client, repo, _quota_repo, _audits, _users = api
    row = repo.seed(STUDENT_OID, "chat", status="rejected")
    _as(client, STUDENT)

    assert row["status"] == "rejected"
    response = client.post(f"{PREFIX}/requests", json={"kind": "chat"})

    assert response.status_code == 201


@pytest.mark.parametrize("kind", ["image", "", "OCR", "chat ", "ocr_tencent"])
def test_submit_rejects_bad_kind(api, kind: str) -> None:
    client, repo, _quota_repo, _audits, _users = api
    _as(client, STUDENT)

    response = client.post(f"{PREFIX}/requests", json={"kind": kind})

    assert response.status_code == 400
    assert response.json()["detail"] == "AI_QUOTA_REQUEST_BAD_KIND"
    assert repo.rows == {}


@pytest.mark.parametrize("limit", [-1, -500])
def test_submit_rejects_negative_limit(api, limit: int) -> None:
    client, _repo, _quota_repo, _audits, _users = api
    _as(client, STUDENT)

    response = client.post(
        f"{PREFIX}/requests", json={"kind": "chat", "requested_limit": limit}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "AI_QUOTA_REQUEST_BAD_LIMIT"


# ---------------------------------------------------------------------------
# 权限
# ---------------------------------------------------------------------------


def test_student_cannot_read_the_approval_queue(api) -> None:
    client, _repo, _quota_repo, _audits, _users = api
    _as(client, STUDENT)

    assert client.get(f"{PREFIX}/requests").status_code == 403


def test_department_without_the_feature_cannot_read_the_queue(api) -> None:
    """``quota.manage`` 默认只给 admin：学生会要由管理员在角色策略页下放。"""
    client, _repo, _quota_repo, _audits, _users = api
    _as(client, STAFF)

    assert client.get(f"{PREFIX}/requests").status_code == 403


def test_department_with_the_feature_can_read_the_queue(api) -> None:
    client, repo, _quota_repo, _audits, _users = api
    repo.seed(STUDENT_OID, "chat")
    _grant_quota_manage()
    _as(client, STAFF)

    response = client.get(f"{PREFIX}/requests")

    assert response.status_code == 200
    assert response.json()["data"]["total"] == 1


def test_student_cannot_approve_even_with_a_pending_row(api) -> None:
    client, repo, quota_repo, audits, _users = api
    row = repo.seed(STUDENT_OID, "chat")
    quota_repo.seed(STUDENT_OID, "chat", used=100, limit=100)
    _as(client, STUDENT)

    response = client.post(f"{PREFIX}/requests/{row['_id']}/approve", json={})

    assert response.status_code == 403
    assert repo.rows[row["_id"]]["status"] == "pending"
    assert quota_repo.rows[(STUDENT_OID, quota_service.period_now(), "chat")][
        "used"
    ] == 100
    assert audits == []


def test_department_with_the_feature_can_approve(api) -> None:
    client, repo, _quota_repo, _audits, _users = api
    row = repo.seed(STUDENT_OID, "chat")
    _grant_quota_manage()
    _as(client, STAFF)

    response = client.post(f"{PREFIX}/requests/{row['_id']}/approve", json={})

    assert response.status_code == 200
    assert response.json()["data"]["request"]["decided_by"] == STAFF_OID


def test_non_admin_cannot_read_the_user_table(api) -> None:
    client, _repo, _quota_repo, _audits, _users = api
    _grant_quota_manage()
    _as(client, STAFF)

    response = client.get(f"{PREFIX}/users")

    assert response.status_code == 403
    assert response.json()["detail"] == "Permission denied"


# ---------------------------------------------------------------------------
# 通过
# ---------------------------------------------------------------------------


def test_approve_resets_used_and_marks_approved(api) -> None:
    client, repo, quota_repo, audits, _users = api
    row = repo.seed(STUDENT_OID, "chat")
    quota_repo.seed(STUDENT_OID, "chat", used=100, limit=100)

    body = client.post(f"{PREFIX}/requests/{row['_id']}/approve", json={}).json()

    data = body["data"]
    assert data["request"]["status"] == "approved"
    assert data["request"]["decided_by"] == ADMIN_OID
    assert data["request"]["decided_at"] is not None
    # 走的是既有额度封装：reset_used 被调用，used 归零、上限保留。
    assert quota_repo.resets == [
        (STUDENT_OID, quota_service.period_now(), "chat")
    ]
    assert data["quota"]["chat"]["used"] == 0
    assert data["quota"]["chat"]["limit"] == 100
    assert quota_repo.rows[(STUDENT_OID, quota_service.period_now(), "chat")][
        "used"
    ] == 0
    # 审批留痕：ltype 复用既有约定，detail 带申请 oid 与被操作用户。
    assert audits and audits[-1]["operator"] == ADMIN_OID
    assert audits[-1]["action"] == "ai_quota_request_approved"
    assert audits[-1]["log_type"] == "system_settings_update"
    assert f"request={row['_id']}" in audits[-1]["detail"]
    assert f"user={STUDENT_OID}" in audits[-1]["detail"]


def test_approve_with_limit_sets_the_new_ceiling(api) -> None:
    client, repo, quota_repo, _audits, _users = api
    row = repo.seed(STUDENT_OID, "chat")
    quota_repo.seed(STUDENT_OID, "chat", used=100, limit=100)

    data = client.post(
        f"{PREFIX}/requests/{row['_id']}/approve", json={"limit": 500}
    ).json()["data"]

    assert quota_repo.limits == [
        (STUDENT_OID, quota_service.period_now(), "chat", 500)
    ]
    assert data["quota"]["chat"]["limit"] == 500
    # 默认 reset=true：设额不该把「重置」这件事吞掉。
    assert data["quota"]["chat"]["used"] == 0


def test_approve_without_reset_keeps_the_usage(api) -> None:
    client, repo, quota_repo, _audits, _users = api
    row = repo.seed(STUDENT_OID, "ocr")
    quota_repo.seed(STUDENT_OID, "ocr", used=42, limit=100)

    data = client.post(
        f"{PREFIX}/requests/{row['_id']}/approve",
        json={"reset": False, "limit": 200},
    ).json()["data"]

    assert quota_repo.resets == []
    assert data["quota"]["ocr"]["used"] == 42
    assert data["quota"]["ocr"]["limit"] == 200


def test_approve_uses_the_target_users_roles_for_the_snapshot(api) -> None:
    """展示口径必须是**目标用户**的角色，不能套管理员的（admin chat 差一个量级）。"""
    client, repo, _quota_repo, _audits, _users = api
    row = repo.seed(STAFF_OID, "chat")

    data = client.post(f"{PREFIX}/requests/{row['_id']}/approve", json={}).json()[
        "data"
    ]

    assert data["quota"]["chat"]["source"] == "role_default"
    assert data["quota"]["chat"]["limit"] == 200_000  # department 的默认 chat


def test_approving_twice_is_a_conflict(api) -> None:
    client, repo, _quota_repo, audits, _users = api
    row = repo.seed(STUDENT_OID, "chat", status="approved")

    response = client.post(f"{PREFIX}/requests/{row['_id']}/approve", json={})

    assert response.status_code == 409
    assert response.json()["detail"] == "AI_QUOTA_REQUEST_NOT_PENDING"
    assert audits == []


def test_approving_a_missing_request_is_404(api) -> None:
    client, _repo, _quota_repo, _audits, _users = api

    response = client.post(f"{PREFIX}/requests/{'0' * 24}/approve", json={})

    assert response.status_code == 404
    assert response.json()["detail"] == "AI_QUOTA_REQUEST_NOT_FOUND"


def test_approve_rejects_a_negative_limit(api) -> None:
    client, repo, quota_repo, _audits, _users = api
    row = repo.seed(STUDENT_OID, "chat")
    quota_repo.seed(STUDENT_OID, "chat", used=10, limit=10)

    response = client.post(
        f"{PREFIX}/requests/{row['_id']}/approve", json={"limit": -1}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "AI_QUOTA_REQUEST_BAD_LIMIT"
    assert repo.rows[row["_id"]]["status"] == "pending"
    assert quota_repo.resets == []


def test_approve_rejects_a_malformed_oid(api) -> None:
    client, _repo, _quota_repo, _audits, _users = api

    response = client.post(f"{PREFIX}/requests/not-an-oid/approve", json={})

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid Object ID"


# ---------------------------------------------------------------------------
# 拒绝
# ---------------------------------------------------------------------------


def test_reject_marks_rejected_and_leaves_the_quota_alone(api) -> None:
    client, repo, quota_repo, audits, _users = api
    row = repo.seed(STUDENT_OID, "chat")
    quota_repo.seed(STUDENT_OID, "chat", used=100, limit=100)

    data = client.post(
        f"{PREFIX}/requests/{row['_id']}/reject", json={"note": "本月预算已用尽"}
    ).json()["data"]

    assert data["status"] == "rejected"
    assert data["decision_note"] == "本月预算已用尽"
    assert data["decided_by"] == ADMIN_OID
    # 拒绝就是拒绝：不清用量、不改上限。
    assert quota_repo.resets == []
    assert quota_repo.limits == []
    assert quota_repo.rows[(STUDENT_OID, quota_service.period_now(), "chat")][
        "used"
    ] == 100
    assert audits[-1]["action"] == "ai_quota_request_rejected"


def test_rejecting_an_already_rejected_request_is_a_conflict(api) -> None:
    client, repo, _quota_repo, _audits, _users = api
    row = repo.seed(STUDENT_OID, "chat", status="rejected")

    response = client.post(f"{PREFIX}/requests/{row['_id']}/reject", json={})

    assert response.status_code == 409
    assert response.json()["detail"] == "AI_QUOTA_REQUEST_NOT_PENDING"


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------


def test_list_mine_returns_only_my_requests(api) -> None:
    client, repo, _quota_repo, _audits, _users = api
    mine = repo.seed(STUDENT_OID, "chat", minutes_ago=1)
    repo.seed(OTHER_OID, "chat", minutes_ago=2)
    repo.seed(STUDENT_OID, "ocr", minutes_ago=3)
    _as(client, STUDENT)

    data = client.get(f"{PREFIX}/requests/mine").json()["data"]

    assert {row["user_oid"] for row in data} == {STUDENT_OID}
    assert len(data) == 2
    assert data[0]["_id"] == mine["_id"]  # 新的在前


def test_list_mine_is_open_to_every_logged_in_user(api) -> None:
    client, repo, _quota_repo, _audits, _users = api
    repo.seed(STUDENT_OID, "chat")
    _grant_quota_manage("student", False)
    _as(client, STUDENT)

    assert client.get(f"{PREFIX}/requests/mine").status_code == 200


def test_approval_queue_carries_user_labels_and_paging(api) -> None:
    client, repo, _quota_repo, _audits, _users = api
    repo.seed(STUDENT_OID, "chat", minutes_ago=1)
    repo.seed(OTHER_OID, "ocr", minutes_ago=2)
    repo.seed(STAFF_OID, "chat", status="approved", minutes_ago=3)

    data = client.get(f"{PREFIX}/requests?status=pending&page=1&size=1").json()["data"]

    assert data["total"] == 2
    assert data["page"] == 1
    assert data["size"] == 1
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["user_name"] == NAMES[STUDENT_OID][0]
    assert item["user_id"] == NAMES[STUDENT_OID][1]


def test_approval_queue_rejects_an_unknown_status(api) -> None:
    client, _repo, _quota_repo, _audits, _users = api

    response = client.get(f"{PREFIX}/requests?status=whatever")

    assert response.status_code == 400
    assert response.json()["detail"] == "AI_QUOTA_REQUEST_BAD_STATUS"


# ---------------------------------------------------------------------------
# 管理表格
# ---------------------------------------------------------------------------


def test_user_table_carries_quota_and_pending_flag(api) -> None:
    client, repo, quota_repo, _audits, _users = api
    quota_repo.seed(STUDENT_OID, "chat", used=7, limit=10)
    quota_repo.seed(STUDENT_OID, "ocr", used=0, limit=0)
    repo.seed(STUDENT_OID, "chat")

    data = client.get(f"{PREFIX}/users").json()["data"]

    assert data["total"] == 4
    by_oid = {item["oid"]: item for item in data["items"]}
    student = by_oid[STUDENT_OID]
    assert student["name"] == "小明"
    assert student["id"] == "20240003"
    assert student["period"] == quota_service.period_now()
    assert student["chat"] == {
        "period": quota_service.period_now(),
        "used": 7,
        "limit": 10,
        "source": "explicit",
    }
    assert student["ocr"]["source"] == "explicit"
    assert student["pending_request"] is True
    assert by_oid[OTHER_OID]["pending_request"] is False


def test_user_table_uses_the_target_users_roles_as_baseline(api) -> None:
    """没有额度行时按目标用户的角色算默认值（staff 的 chat 是 200_000）。"""
    client, _repo, _quota_repo, _audits, _users = api

    data = client.get(f"{PREFIX}/users").json()["data"]
    by_oid = {item["oid"]: item for item in data["items"]}

    assert by_oid[STAFF_OID]["chat"]["limit"] == 200_000
    assert by_oid[STAFF_OID]["chat"]["source"] == "role_default"
    # admin 是代码里的无限额度：默认值来自角色表，不是全局 DEFAULT_QUOTA。
    assert by_oid[ADMIN_OID]["chat"]["limit"] > by_oid[STAFF_OID]["chat"]["limit"]


def test_user_table_filters_by_name_or_business_id(api) -> None:
    client, _repo, _quota_repo, _audits, users = api

    by_name = client.get(f"{PREFIX}/users?query=小红").json()["data"]
    by_id = client.get(f"{PREFIX}/users?query=20240003").json()["data"]

    assert [item["oid"] for item in by_name["items"]] == [OTHER_OID]
    assert [item["oid"] for item in by_id["items"]] == [STUDENT_OID]
    assert users.calls[-1]["query"] == "20240003"


def test_user_table_clamps_the_page_size(api) -> None:
    """``size`` 上限 50：管理页把「全部」编码成大数字时不该拖全表。"""
    client, _repo, _quota_repo, _audits, users = api

    data = client.get(f"{PREFIX}/users?size=999").json()["data"]

    assert data["size"] == 50
    assert users.calls[-1]["limit"] == 50


def test_user_table_empty_page_does_not_query_quotas(api, monkeypatch) -> None:
    client, _repo, quota_repo, _audits, _users = api

    def _boom(*_args, **_kwargs):  # pragma: no cover - 触发即失败
        raise AssertionError("empty page must not read quota rows")

    client.get(f"{PREFIX}/users?query=查无此人")
    monkeypatch.setattr(quota_repo, "get", _boom)

    data = client.get(f"{PREFIX}/users?query=查无此人").json()["data"]

    assert data["items"] == []
    assert data["total"] == 0


# ---------------------------------------------------------------------------
# DDL 镜像
# ---------------------------------------------------------------------------


def test_v044_matches_models_and_v001() -> None:
    """V044 建的表必须与 models / V001 逐字段一致（含列默认值与 CHECK）。"""
    from db import models
    from pathlib import Path

    path = Path(models.__file__).resolve().parent / "oracle" / "ddl" / (
        "V044__ai_quota_requests.sql"
    )
    migration = ddl_check.parse_schema(
        ddl_check.extract_execute_immediate(path.read_text(encoding="utf-8"))
    )
    modeled = ddl_check.parse_models()
    v001 = ddl_check.parse_v001()

    assert set(migration["tables"]) == {"ai_quota_requests"}
    assert migration["tables"]["ai_quota_requests"] == modeled["tables"][
        "ai_quota_requests"
    ]
    assert v001["tables"]["ai_quota_requests"] == modeled["tables"][
        "ai_quota_requests"
    ]
    assert migration["indexes"] <= modeled["indexes"]
    assert migration["indexes"] <= v001["indexes"]
    # 无外键：申请是审计材料，必须比账号活得久。
    assert modeled["tables"]["ai_quota_requests"]["fk"] == set()


def test_ddl_check_reports_no_drift() -> None:
    """冒烟：``python -m db.ddl_check`` 的同一个入口必须回 0。"""
    assert ddl_check.main() == 0
