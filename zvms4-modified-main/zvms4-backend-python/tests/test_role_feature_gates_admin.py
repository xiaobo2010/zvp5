"""管理域「角色功能开关」（V043）闸门。

两批端点，两种口径：

* 开关真的能咬到的端点（``activity.stats`` / ``attachment.manage``）：关掉 →
  403 且**没有任何副作用**（不写库、不落审计），开着（``department`` 默认）
  → 与改造前一致。
* 下放型端点（``export.time`` / ``import.activity`` / ``logs.read`` /
  ``notify.publish`` / ``ban.read`` / ``quota.manage``）：基判原先是「仅
  admin」，现在改成「admin **或**该角色开关已开」。``ADMIN_ONLY_FEATURE_KEYS``
  的默认值全关，所以线上行为没变；管理员勾上之后非 admin 才真的能进。每个 key
  钉三件事：
  1. 开关关 → 非 admin 仍是**原来的**文案（``权限不足`` / ``Permission
     denied``），且不碰任何仓储（闸门在业务之前）；
  2. 开关开 → 同一个请求能过闸门，走到业务逻辑；
  3. ``admin`` 在全部开关关掉时照旧可用（admin 是策略层的代码短路）。

``quota.manage`` 还有一条数据边界：非 admin 只能管**本班**用户（任一方班级为空
即拒绝，fail-closed）。

离线：所有仓储逐方法打桩，``inject_log`` 换假日志；不连 ADB。

    cd <repo root> && .venv/bin/python -m pytest tests/test_role_feature_gates_admin.py -q
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import (
    activity_statistics_router,
    attachments_router,
    ban_router,
    ban_violations_router,
    exports_router,
    imports_router,
    logs_router,
    notifications_router,
    system_settings_router,
)
from services.roles import policy
from typings.log import inject_log
from util.object_id import get_current_user

ADMIN_OID = "64b7f0c2e1a3b4c5d6e7fb01"
DEPT_OID = "64b7f0c2e1a3b4c5d6e7fb02"
SECRETARY_OID = "64b7f0c2e1a3b4c5d6e7fb03"
OWNER_OID = "64b7f0c2e1a3b4c5d6e7fb04"

FEATURE_DENIED = "当前角色无权使用该功能"

USER_ADMIN = {"_id": ADMIN_OID, "id": ADMIN_OID, "per": ["admin"]}
USER_DEPT = {"_id": DEPT_OID, "id": DEPT_OID, "per": ["department"]}
USER_SECRETARY = {"_id": SECRETARY_OID, "id": SECRETARY_OID, "per": ["secretary"]}


class _FakeLog:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.types: list[str] = []

    def with_text(self, text: str, type: str = "", level: str = "") -> "_FakeLog":
        self.texts.append(text)
        self.types.append(type)
        return self

    async def insert_log(self, *_args, **_kwargs) -> None:
        return None


async def _allow_reauth(*_args, **_kwargs):
    """本文件只验业务语义；再认证本身由 ``test_admin_reauth_gate`` 专测。"""
    return None


@pytest.fixture(autouse=True)
def _no_reauth(monkeypatch):
    monkeypatch.setattr(ban_router, "require_password_verification", _allow_reauth)
    monkeypatch.setattr(notifications_router, "require_password_verification", _allow_reauth)
    monkeypatch.setattr(system_settings_router, "require_password_verification", _allow_reauth)


def _app(router, prefix: str, user: dict) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix=prefix)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[inject_log] = lambda: _FakeLog()
    return TestClient(app)


@pytest.fixture(autouse=True)
def _fresh_policy():
    """每个用例都从「库里没有任何覆盖」开始，也就是 DEFAULTS。"""
    policy.reset_cache()
    yield
    policy.reset_cache()


def _override(monkeypatch, role: str, key: str, enabled: bool) -> None:
    monkeypatch.setitem(policy._features, (role, key), enabled)


async def _noop(*_args, **_kwargs) -> None:
    """后台任务/风控评估的替身：测的是闸门，不是导出流水线。"""
    return None


async def _name_of_dept(_oid: str) -> str:
    return "学生会"


def _override(monkeypatch, role: str, key: str, enabled: bool) -> None:
    monkeypatch.setitem(policy._features, (role, key), enabled)


# ---------------------------------------------------------------------------
# activity.stats
# ---------------------------------------------------------------------------


class _MembersRepo:
    def __init__(self, durations: list[float]) -> None:
        self.durations = durations
        self.calls: list[tuple[str, str | None]] = []

    async def list_by_activity(self, activity_id: str, status: str | None = None):
        self.calls.append((activity_id, status))
        return [{"duration": duration} for duration in self.durations]


@pytest.mark.parametrize("user", [USER_DEPT, USER_SECRETARY], ids=["department", "secretary"])
def test_activity_stats_off_blocks_readers_and_skips_the_repository(user, monkeypatch):
    repo = _MembersRepo([10.0, 20.0])
    monkeypatch.setattr(backends, "get_members_repo", lambda *a, **k: repo)
    _override(monkeypatch, user["per"][0], "activity.stats", False)

    response = _app(
        activity_statistics_router.router,
        "/api/v2/statistics/activities",
        user,
    ).get("/api/v2/statistics/activities/act1/description")

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == FEATURE_DENIED
    assert repo.calls == []


def test_activity_stats_on_keeps_secretary_default(monkeypatch):
    repo = _MembersRepo([10.0, 20.0])
    monkeypatch.setattr(backends, "get_members_repo", lambda *a, **k: repo)

    response = _app(
        activity_statistics_router.router,
        "/api/v2/statistics/activities",
        USER_SECRETARY,
    ).get("/api/v2/statistics/activities/act1/description")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mean"] == 15.0 and body["total"] == 2
    assert repo.calls == [("act1", "effective")]


# ---------------------------------------------------------------------------
# attachment.manage
# ---------------------------------------------------------------------------


class _FilesRepo:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []
        self.meta: dict[str, dict] = {}
        self.ocr_rows: dict[str, dict | None] = {}
        self.list_calls: list[dict] = []
        self.creates: list[dict] = []
        self.status_writes: list[tuple[str, str]] = []
        self.events: list[dict] = []
        self.event_reads: list[str] = []

    async def list_page(self, owner, purpose, status, entity_type, entity_oid, page, size, **filters):
        self.list_calls.append({"owner": owner, **filters})
        return self.rows, len(self.rows)

    async def file_oids_with_ocr(self, oids: list[str]) -> set[str]:
        return {oid for oid in oids if self.ocr_rows.get(oid)}

    async def get_meta(self, oid: str):
        return self.meta.get(oid)

    async def get_ocr(self, oid: str):
        return self.ocr_rows.get(oid)

    async def list_events(self, oid: str, *, limit=None):
        self.event_reads.append(oid)
        return []

    async def create(self, *_args, **_kwargs):
        self.creates.append({"args": _args})
        return "64b7f0c2e1a3b4c5d6e7fcff"

    async def set_status(self, oid: str, status: str) -> None:
        self.status_writes.append((oid, status))

    async def add_event(self, file_oid, actor_oid, action, from_status, to_status, detail) -> str:
        self.events.append({"file_oid": file_oid, "action": action})
        return "64b7f0c2e1a3b4c5d6e7fcfe"


class _UsersRepo:
    def __init__(self) -> None:
        self.by_oid: dict[str, dict] = {}

    async def get_by_oid(self, oid: str):
        return self.by_oid.get(oid)


def _attachment_row(oid: str = "64b7f0c2e1a3b4c5d6e7fd01") -> dict:
    return {
        "_id": oid,
        "owner_oid": OWNER_OID,
        "kind": "original",
        "mime": "image/jpeg",
        "size_bytes": 1024,
        "width": 800,
        "height": 600,
        "sha256": "a" * 64,
        "original_name": "order.jpg",
        "purpose": "volunteer_order",
        "entity_type": None,
        "entity_oid": None,
        "status": "stored",
        "ocr_status": None,
        "created_at": None,
        "deleted_at": None,
    }


@pytest.fixture
def attachments(monkeypatch):
    files = _FilesRepo()
    users = _UsersRepo()
    users.by_oid[OWNER_OID] = {"_id": OWNER_OID, "id": "2026007", "name": "李四"}
    monkeypatch.setattr(backends, "get_files_repo", lambda *a, **k: files)
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: users)
    log = _FakeLog()
    app = FastAPI()
    app.include_router(attachments_router.router, prefix="/api/attachments")
    state = {"client": None}

    def client_for(user: dict) -> TestClient:
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[inject_log] = lambda: log
        return TestClient(app)

    return SimpleNamespace(app=app, files=files, users=users, log=log, client_for=client_for, state=state)


def test_attachment_manage_off_blocks_upload_before_any_write(attachments, monkeypatch):
    _override(monkeypatch, "department", "attachment.manage", False)
    client = attachments.client_for(USER_DEPT)
    attachments.users.by_oid[DEPT_OID] = {"_id": DEPT_OID, "banned": False}

    response = client.post(
        "/api/attachments",
        files={"file": ("order.jpg", b"jpeg-bytes", "image/jpeg")},
        data={"purpose": "volunteer_order"},
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == FEATURE_DENIED
    # 无副作用：没有建库记录、没有 FILE_EVENTS、没有审计日志、没扣上传配额。
    assert attachments.files.creates == []
    assert attachments.files.events == []
    assert attachments.log.texts == []


def test_attachment_manage_off_blocks_audit_detail(attachments, monkeypatch):
    oid = "64b7f0c2e1a3b4c5d6e7fd01"
    attachments.files.meta[oid] = _attachment_row(oid)
    _override(monkeypatch, "department", "attachment.manage", False)

    response = attachments.client_for(USER_DEPT).get(f"/api/attachments/{oid}/audit")

    assert response.status_code == 403, response.text
    assert attachments.files.event_reads == []
    assert attachments.log.texts == []


def test_attachment_manage_off_blocks_status_verdict_before_write(attachments, monkeypatch):
    oid = "64b7f0c2e1a3b4c5d6e7fd01"
    attachments.files.meta[oid] = _attachment_row(oid)
    _override(monkeypatch, "department", "attachment.manage", False)

    response = attachments.client_for(USER_DEPT).patch(
        f"/api/attachments/{oid}/status",
        json={"status": "verified", "note": "ok"},
    )

    assert response.status_code == 403, response.text
    assert attachments.files.status_writes == []
    assert attachments.files.events == []


def test_attachment_manage_on_keeps_department_audit_detail(attachments):
    oid = "64b7f0c2e1a3b4c5d6e7fd01"
    attachments.files.meta[oid] = _attachment_row(oid)

    response = attachments.client_for(USER_DEPT).get(f"/api/attachments/{oid}/audit")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["_id"] == oid
    assert data["owner_id"] == "2026007"
    assert attachments.files.event_reads == [oid]


def test_attachment_manage_off_leaves_the_list_acl_untouched(attachments, monkeypatch):
    oid = "64b7f0c2e1a3b4c5d6e7fd01"
    attachments.files.rows = [_attachment_row(oid)]
    _override(monkeypatch, "department", "attachment.manage", False)
    client = attachments.client_for(USER_DEPT)

    listing_all = client.get("/api/attachments", params={"scope": "all"})
    listing_mine = client.get("/api/attachments", params={"scope": "mine"})

    # 只收紧了写/审核口：读列表照旧按行级 ACL（scope=all 仍要 manager，mine 仍锁本人）。
    assert listing_all.status_code == 200, listing_all.text
    assert listing_mine.status_code == 200, listing_mine.text
    assert attachments.files.list_calls[-2]["owner"] is None
    assert attachments.files.list_calls[-1]["owner"] == DEPT_OID


# ---------------------------------------------------------------------------
# 下放型端点：admin 或「该角色开关已开」，否则旧文案 403
# ---------------------------------------------------------------------------

_ADMIN_ONLY_CASES = [
    pytest.param(
        exports_router.router,
        "/api/exports",
        "post",
        "/time",
        {"json": {"format": "excel"}},
        "export.time",
        "Permission denied",
        id="export.time",
    ),
    pytest.param(
        imports_router.router,
        "/api/imports",
        "post",
        "/activities",
        {
            "params": {"name": "秋季义工", "desc": "导入"},
            "files": {
                "payload": ("import.xlsx", b"PK\x03\x04", "application/vnd.ms-excel")
            },
        },
        "import.activity",
        "权限不足",
        id="import.activity",
    ),
    pytest.param(
        logs_router.router,
        "/api/logs",
        "get",
        "",
        {},
        "logs.read",
        "Permission denied",
        id="logs.read",
    ),
    pytest.param(
        notifications_router.router,
        "/api/v3/notifications",
        "post",
        "",
        {"json": {"title": "通知", "content": "内容"}},
        "notify.publish",
        "权限不足",
        id="notify.publish",
    ),
    pytest.param(
        ban_router.router,
        "/api/v3/ban",
        "get",
        "/rules",
        {},
        "ban.read",
        "权限不足",
        id="ban.read.rules",
    ),
    pytest.param(
        ban_violations_router.router,
        "/api/v3/ban",
        "get",
        "/violations",
        {},
        "ban.read",
        "权限不足",
        id="ban.read.violations",
    ),
]


class _Forbidden:
    """任何仓储访问都算「闸门没挡住」：拿到 getter 就记账，调方法直接失败。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, *args, **kwargs):
        self.calls.append("getter")
        return self

    def __getattr__(self, name: str):
        def _record(*_args, **_kwargs):
            self.calls.append(name)
            raise AssertionError(f"闸门没有挡住就该调用仓储方法 {name}")

        return _record


@pytest.mark.parametrize(
    "router,prefix,method,path,kwargs,key,detail", _ADMIN_ONLY_CASES
)
def test_switch_off_keeps_the_legacy_403_for_department(
    router, prefix, method, path, kwargs, key, detail, monkeypatch
):
    """默认关 = 改造前行为：同一句旧文案，且一个仓储方法都没被调用。"""
    guarded = _Forbidden()
    for getter in (
        "get_tasks_repo",
        "get_imports_repo",
        "get_activities_repo",
        "get_members_repo",
        "get_applogs_repo",
        "get_notifications_repo",
        "get_ban_rules_repo",
        "get_violations_repo",
    ):
        monkeypatch.setattr(backends, getter, guarded)

    response = getattr(_app(router, prefix, USER_DEPT), method)(prefix + path, **kwargs)

    assert response.status_code == 403, response.text
    # 这些 key 的默认值是关：department 拿不到，文案与改造前逐字一致。
    assert response.json()["detail"] == detail
    assert response.json()["detail"] != FEATURE_DENIED
    assert guarded.calls == []


@pytest.mark.parametrize(
    "key,repo_getter,payload",
    [
        ("logs.read", "get_applogs_repo", None),
        ("ban.read", "get_ban_rules_repo", None),
        ("notify.publish", "get_notifications_repo", None),
    ],
)
def test_switch_on_lets_department_read_admin_surfaces(
    key, repo_getter, payload, monkeypatch
):
    """开关打开后，同一条请求能过闸门 —— 证明开关真的接管了旧的 admin-only。"""
    _override(monkeypatch, "department", key, True)

    class _EmptyRepo:
        async def list_all(self):
            return ([], 0) if key == "notify.publish" else []

    class _EmptyLogsRepo:
        async def list_page(self, **_kwargs):
            return [], 0

    repo = _EmptyLogsRepo() if key == "logs.read" else _EmptyRepo()
    monkeypatch.setattr(backends, repo_getter, lambda *a, **k: repo)

    cases = {
        "logs.read": (logs_router.router, "/api/logs", "get", ""),
        "ban.read": (ban_router.router, "/api/v3/ban", "get", "/rules"),
        "notify.publish": (
            notifications_router.router,
            "/api/v3/notifications",
            "get",
            "/admin",
        ),
    }
    router, prefix, method, path = cases[key]

    response = getattr(_app(router, prefix, USER_DEPT), method)(prefix + path)

    assert response.status_code == 200, response.text


def test_ban_read_switch_also_opens_the_violations_list(monkeypatch):
    _override(monkeypatch, "department", "ban.read", True)

    class _ViolationsRepo:
        async def list_page(self, query, page, perpage):
            return [], 0

    monkeypatch.setattr(
        backends, "get_violations_repo", lambda *a, **k: _ViolationsRepo()
    )

    response = _app(ban_violations_router.router, "/api/v3/ban", USER_DEPT).get(
        "/api/v3/ban/violations"
    )

    assert response.status_code == 200, response.text


def test_ban_write_stays_admin_only_even_with_ban_read_on(monkeypatch):
    """``ban.read`` 只下放读：新增规则仍然只有 admin 能写。"""
    _override(monkeypatch, "department", "ban.read", True)

    response = _app(ban_router.router, "/api/v3/ban", USER_DEPT).post(
        "/api/v3/ban/rules",
        json={
            "code": "new_rule",
            "name": "新规则",
            "trigger": "foreign_device_login",
            "penalties": [1],
        },
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "权限不足"


def test_export_time_switch_lets_department_start_a_task(monkeypatch):
    _override(monkeypatch, "department", "export.time", True)
    created: list[dict] = []

    class _TasksRepo:
        async def create(self, document):
            created.append(document)

    monkeypatch.setattr(backends, "get_tasks_repo", lambda *a, **k: _TasksRepo())
    monkeypatch.setattr(exports_router, "process_task", _noop)
    monkeypatch.setattr(exports_router, "_run_risk_export", _noop)
    monkeypatch.setattr(exports_router, "get_user_display_name", _name_of_dept)

    response = _app(exports_router.router, "/api/exports", USER_DEPT).post(
        "/api/exports/time", json={"format": "excel"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["code"] == 201
    assert created and created[0]["variant"] == "time"


def test_other_exports_stay_admin_only(monkeypatch):
    """只下放时长报表：用户列表导出仍是 admin。"""
    _override(monkeypatch, "department", "export.time", True)

    response = _app(exports_router.router, "/api/exports", USER_DEPT).post(
        "/api/exports/users"
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "Permission denied"


def test_import_activity_switch_lets_department_past_the_gate(monkeypatch):
    """开关打开 → 请求到达业务校验（坏文件 400），而不是 403。"""
    _override(monkeypatch, "department", "import.activity", True)

    response = _app(imports_router.router, "/api/imports", USER_DEPT).post(
        "/api/imports/activities",
        params={"name": "秋季义工", "desc": "导入"},
        files={
            "payload": ("import.xlsx", b"PK\x03\x04", "application/vnd.ms-excel")
        },
    )

    assert response.status_code == 400, response.text
    assert response.json()["detail"].startswith("Invalid workbook")
# ---------------------------------------------------------------------------
# quota.manage：开关 + 同班（非 admin 的数据边界）
# ---------------------------------------------------------------------------


class _QuotaUsersRepo:
    """按 oid 返回用户文档；``class_id`` 缺失或 None = 未分班。"""

    def __init__(self, users: dict[str, dict]) -> None:
        self.users = users

    async def get_by_oid(self, oid: str):
        return self.users.get(oid)


def _quota_client(monkeypatch, *, actor_class, target_class) -> tuple[TestClient, list]:
    users = _QuotaUsersRepo(
        {
            DEPT_OID: {"_id": DEPT_OID, "class_id": actor_class},
            OWNER_OID: {"_id": OWNER_OID, "class_id": target_class},
        }
    )
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: users)
    reads: list[str] = []

    async def _get_quota(oid):
        reads.append(oid)
        return {"user_oid": oid}

    monkeypatch.setattr(
        system_settings_router.settings_runtime, "get_user_quota", _get_quota
    )
    return _app(system_settings_router.router, "/api/system", USER_DEPT), reads


def test_quota_manage_off_blocks_department_before_any_read(monkeypatch):
    """默认关：非 admin 连查都不该查（不碰仓储、不读配额）。"""
    users = _QuotaUsersRepo({})
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: users)
    reads: list[str] = []

    async def _get_quota(oid):
        reads.append(oid)
        return {}

    monkeypatch.setattr(
        system_settings_router.settings_runtime, "get_user_quota", _get_quota
    )

    response = _app(system_settings_router.router, "/api/system", USER_DEPT).get(
        f"/api/system/user-quotas/{OWNER_OID}"
    )

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "权限不足"
    assert reads == []


def test_quota_manage_on_allows_the_same_class(monkeypatch):
    _override(monkeypatch, "department", "quota.manage", True)
    client, reads = _quota_client(
        monkeypatch, actor_class="class-a", target_class="class-a"
    )

    response = client.get(f"/api/system/user-quotas/{OWNER_OID}")

    assert response.status_code == 200, response.text
    assert reads == [OWNER_OID]
    assert response.json()["data"]["user_oid"] == OWNER_OID


def test_quota_manage_on_allows_writing_the_same_class(monkeypatch):
    _override(monkeypatch, "department", "quota.manage", True)
    users = _QuotaUsersRepo(
        {
            DEPT_OID: {"_id": DEPT_OID, "class_id": "class-a"},
            OWNER_OID: {"_id": OWNER_OID, "class_id": "class-a"},
        }
    )
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: users)
    writes: list[tuple] = []

    async def _set_quota(oid, files, size, actor):
        writes.append((oid, files, size, actor))
        return {"user_oid": oid}

    monkeypatch.setattr(
        system_settings_router.settings_runtime, "set_user_quota", _set_quota
    )
    monkeypatch.setattr(
        system_settings_router, "get_user_display_name", _name_of_dept
    )

    response = _app(system_settings_router.router, "/api/system", USER_DEPT).put(
        f"/api/system/user-quotas/{OWNER_OID}", json={"daily_max_files": 3}
    )

    assert response.status_code == 200, response.text
    assert writes == [(OWNER_OID, 3, None, DEPT_OID)]


def test_quota_manage_on_blocks_another_class(monkeypatch):
    _override(monkeypatch, "department", "quota.manage", True)
    client, reads = _quota_client(
        monkeypatch, actor_class="class-a", target_class="class-b"
    )

    response = client.get(f"/api/system/user-quotas/{OWNER_OID}")

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == "权限不足"
    assert reads == []


@pytest.mark.parametrize(
    "actor_class,target_class",
    [(None, "class-b"), ("class-a", None), (None, None), ("", "")],
    ids=["actor-unassigned", "target-unassigned", "both-unassigned", "both-empty"],
)
def test_quota_manage_on_fails_closed_when_a_class_is_missing(
    monkeypatch, actor_class, target_class
):
    """任一方没有班级 → 拒绝：查不到班级不等于「同班」。"""
    _override(monkeypatch, "department", "quota.manage", True)
    client, reads = _quota_client(
        monkeypatch, actor_class=actor_class, target_class=target_class
    )

    response = client.get(f"/api/system/user-quotas/{OWNER_OID}")

    assert response.status_code == 403, response.text
    assert reads == []


def test_quota_manage_on_refuses_to_touch_an_admin_even_in_the_same_class(monkeypatch):
    """同班也不行：把管理员配额调小是一次针对管理员的定向限流。"""
    _override(monkeypatch, "department", "quota.manage", True)
    users = _QuotaUsersRepo(
        {
            DEPT_OID: {"_id": DEPT_OID, "class_id": "class-a"},
            OWNER_OID: {"_id": OWNER_OID, "class_id": "class-a", "roles": ["admin"]},
        }
    )
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: users)
    reads: list[str] = []

    async def _get_quota(oid):
        reads.append(oid)
        return {}

    monkeypatch.setattr(
        system_settings_router.settings_runtime, "get_user_quota", _get_quota
    )

    response = _app(system_settings_router.router, "/api/system", USER_DEPT).get(
        f"/api/system/user-quotas/{OWNER_OID}"
    )

    assert response.status_code == 403, response.text
    assert reads == []


# ---------------------------------------------------------------------------
# admin 恒可用：所有开关都关掉也不影响管理员
# ---------------------------------------------------------------------------


def test_admin_reads_admin_surfaces_even_with_every_switch_off(monkeypatch):
    for role in policy.EDITABLE_ROLES:
        for key in policy.FEATURE_KEYS:
            monkeypatch.setitem(policy._features, (role, key), False)

    rules: list[dict] = []

    class _RulesRepo:
        async def list_all(self):
            return rules

    class _LogsRepo:
        async def list_page(self, **_kwargs):
            return [], 0

    class _NotificationsRepo:
        async def list_all(self):
            return [], 7

    monkeypatch.setattr(backends, "get_ban_rules_repo", lambda *a, **k: _RulesRepo())
    monkeypatch.setattr(backends, "get_applogs_repo", lambda *a, **k: _LogsRepo())
    monkeypatch.setattr(
        backends, "get_notifications_repo", lambda *a, **k: _NotificationsRepo()
    )

    assert _app(ban_router.router, "/api/v3/ban", USER_ADMIN).get(
        "/api/v3/ban/rules"
    ).status_code == 200
    assert _app(logs_router.router, "/api/logs", USER_ADMIN).get("/api/logs").status_code == 200
    assert _app(notifications_router.router, "/api/v3/notifications", USER_ADMIN).get(
        "/api/v3/notifications/admin"
    ).status_code == 200


# ---------------------------------------------------------------------------
# DEFAULTS 本身：这些 key 的默认值就是改造前的行为
# ---------------------------------------------------------------------------


def test_defaults_keep_the_pre_change_behaviour_for_the_new_keys():
    """department 默认 7 开 / 6 关：关着的正是改造前 admin-only 的六项。"""
    department = policy.DEFAULTS["department"]["features"]
    assert {
        key for key, value in department.items() if value
    } == {
        "ai.chat",
        "ai.ocr",
        "ai.agent",
        "ai.schedule",
        "ai.keys",
        "activity.stats",
        "attachment.manage",
    }
    assert {
        key for key, value in department.items() if not value
    } == set(policy.ADMIN_ONLY_FEATURE_KEYS)
    assert set(policy.ADMIN_ONLY_FEATURE_KEYS) == {
        "export.time",
        "import.activity",
        "logs.read",
        "notify.publish",
        "ban.read",
        "quota.manage",
    }
    assert set(policy.DEFAULTS["secretary"]["features"]) == set(policy.FEATURE_KEYS)
    assert {
        key for key, value in policy.DEFAULTS["secretary"]["features"].items() if value
    } == {"activity.stats", "ai.ocr"}
    assert not any(policy.DEFAULTS["student"]["features"].values())
