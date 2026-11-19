"""v4.2.4-2 L1（R1）：删除义工活动 / 删除成员的鉴权与权限口径。

覆盖验收点：
- 临时票（5 分钟 `temporary_token`）可通过两条 DELETE 的鉴权；
- 长期票（`access_token`）被拒 → 401；
- 删除活动：创建人可删、admin/department 可删、其余 403；
- 删除成员：口径保持 admin/department（非 admin 403）；
- 非法对象 id 被 `validate_object_id` 拦下 → 400。

依赖全部走真实链路（`util.object_id._authenticate` + JWT 编解码），只把仓库层
换成内存桩，避免测试触达 Oracle。
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import activities_router
from util import cert

SESSION = "sid-v1"

ADMIN = "000000000000000000000001"
CREATOR = "000000000000000000000002"
OTHER = "000000000000000000000003"
DEPARTMENT = "000000000000000000000004"
ACTIVITY = "000000000000000000000010"
MEMBER = "000000000000000000000020"


def make_user(oid: str, roles: list[str], name: str) -> dict:
    return {
        "_id": oid,
        "id": oid,
        "name": name,
        "roles": roles,
        "session_id": SESSION,
        "banned": False,
        "class_id": None,
    }


USERS = {
    ADMIN: make_user(ADMIN, ["admin"], "管理员"),
    CREATOR: make_user(CREATOR, ["student"], "创建人"),
    OTHER: make_user(OTHER, ["student"], "路人"),
    DEPARTMENT: make_user(DEPARTMENT, ["department"], "部门"),
}


def activity_doc(creator: str) -> dict:
    return {
        "_id": ACTIVITY,
        "name": "测试活动",
        "description": "d",
        "type": "on-campus",
        "creator": creator,
        "status": "effective",
        "date": "2026-01-01T00:00:00",
        "createdAt": "2026-01-01T00:00:00",
        "updatedAt": "2026-01-01T00:00:00",
        "appointee": CREATOR,
        "approver": "member",
        "place": "p",
        "origin": "labor",
    }


class UsersRepo:
    async def get_by_oid(self, oid):
        return USERS.get(str(oid))

    async def get_names(self, oids):
        return {str(o): USERS[str(o)]["name"] for o in oids if str(o) in USERS}


class ActivitiesRepo:
    def __init__(self, activity):
        self.activity = activity
        self.deleted: list[str] = []
        self.locked: list[str] = []

    async def get(self, oid):
        return self.activity if str(oid) == ACTIVITY else None

    async def get_for_update(self, oid):
        # 删除路径必须走行锁：先锁活动（父）再动成员行（子），与 v3 同序。
        self.locked.append(str(oid))
        return self.activity if str(oid) == ACTIVITY else None

    async def delete(self, oid):
        self.deleted.append(str(oid))


class MembersRepo:
    def __init__(self):
        self.removed: list[str] = []
        self.removed_for_activity: list[str] = []

    async def get_by_oid(self, oid):
        if str(oid) != MEMBER:
            return None
        return {
            "_id": MEMBER,
            "member": OTHER,
            "activity": ACTIVITY,
            "status": "effective",
            "mode": "on-campus",
            "duration": 2.0,
        }

    async def list_by_activity(self, activity_oid, status=None):
        return [
            {
                "_id": MEMBER,
                "member": OTHER,
                "activity": ACTIVITY,
                "status": "effective",
                "mode": "on-campus",
                "duration": 2.0,
            }
        ]

    async def remove(self, oid):
        self.removed.append(str(oid))

    async def remove_for_activity(self, activity_oid):
        self.removed_for_activity.append(str(activity_oid))


class AppLogsRepo:
    def __init__(self):
        self.rows: list[tuple] = []

    async def insert(self, *args, **kwargs):
        self.rows.append(args)


@pytest.fixture
def env(monkeypatch):
    """把仓库层替换成内存桩，返回可断言的对象。"""
    activities = ActivitiesRepo(activity_doc(CREATOR))
    members = MembersRepo()
    logs = AppLogsRepo()
    refreshed: list[tuple[str, str | None]] = []

    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: UsersRepo())
    monkeypatch.setattr(backends, "get_activities_repo", lambda *a, **k: activities)
    monkeypatch.setattr(backends, "get_members_repo", lambda *a, **k: members)
    monkeypatch.setattr(backends, "get_applogs_repo", lambda *a, **k: logs)

    # 删除活动在提交后按「删除前抓到的成员」重算：单测里不连 Oracle，只记录。
    async def refresh(member_ids, activity_id=None):
        refreshed.append((list(member_ids), activity_id))

    @asynccontextmanager
    async def scope():
        yield SimpleNamespace(begin=lambda: _begin())

    @asynccontextmanager
    async def _begin():
        yield None

    monkeypatch.setattr(activities_router, "session_scope", scope)
    monkeypatch.setattr(activities_router, "_refresh_user_time_for_members", refresh)

    # 删成员会立刻按该成员本人重算缓存：单测只记录，不触达 Oracle。
    time_refresh_calls: list[str] = []

    async def calculate(oid, allow_cache=True):
        time_refresh_calls.append((str(oid), allow_cache))
        return {}

    monkeypatch.setattr(activities_router, "calculate_user_time", calculate)

    return SimpleNamespace(
        activities=activities,
        members=members,
        logs=logs,
        refreshed=refreshed,
        time_refresh_calls=time_refresh_calls,
    )


@pytest.fixture
def client(env):
    app = FastAPI()
    app.include_router(activities_router.router, prefix="/api/v1/activities")
    return TestClient(app)


def token(oid: str, roles: list[str], kind: str = "short") -> str:
    return cert.jwt_encode(oid, [], roles, kind, session_id=SESSION)


def auth(oid: str, roles: list[str], kind: str = "short") -> dict:
    return {"Authorization": f"Bearer {token(oid, roles, kind)}"}


# --- 删除活动 -------------------------------------------------------------


def test_temporary_token_passes_authentication(client, env):
    """临时票（前端删除活动实际送的凭据）必须通过，不能再 401。"""
    response = client.delete(f"/api/v1/activities/{ACTIVITY}", headers=auth(ADMIN, ["admin"]))
    assert response.status_code == 200
    assert response.json() == {"detail": "Activity deleted successfully"}
    assert env.activities.deleted == [ACTIVITY]
    assert env.members.removed_for_activity == [ACTIVITY]
    assert env.logs.rows
    # 先锁活动（父）再删成员（子）：锁序与 v3 批量登记一致，避免 ORA-00060。
    assert env.activities.locked == [ACTIVITY]
    # 提交后按删除前抓到的成员重算——行已经没了，按活动反查只会得到空集。
    assert env.refreshed == [([OTHER], ACTIVITY)]


def test_long_lived_token_is_rejected(client, env):
    """长期票不得走临时票通道：scope 校验必须拦下（401，且无副作用）。"""
    response = client.delete(f"/api/v1/activities/{ACTIVITY}", headers=auth(ADMIN, ["admin"], "long"))
    assert response.status_code == 401
    assert env.activities.deleted == []


def test_creator_can_delete_without_admin_or_department(client, env):
    """创建人即使只有 student 角色也能删。"""
    response = client.delete(f"/api/v1/activities/{ACTIVITY}", headers=auth(CREATOR, ["student"]))
    assert response.status_code == 200
    assert env.activities.deleted == [ACTIVITY]


def test_department_can_delete(client, env):
    response = client.delete(
        f"/api/v1/activities/{ACTIVITY}", headers=auth(DEPARTMENT, ["department"])
    )
    assert response.status_code == 200


def test_non_creator_non_admin_is_forbidden(client, env):
    """既不是创建人、也不是 admin/department → 403，且不得产生任何删除副作用。"""
    response = client.delete(f"/api/v1/activities/{ACTIVITY}", headers=auth(OTHER, ["student"]))
    assert response.status_code == 403
    assert env.activities.deleted == []
    assert env.members.removed_for_activity == []
    assert env.logs.rows == []


def test_missing_activity_is_404(client, env):
    response = client.delete(
        "/api/v1/activities/0000000000000000000000ff", headers=auth(CREATOR, ["student"])
    )
    assert response.status_code == 404


def test_invalid_activity_id_is_rejected(client, env):
    response = client.delete("/api/v1/activities/not-an-object-id", headers=auth(ADMIN, ["admin"]))
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid Object ID"
    assert env.activities.deleted == []


# --- 删除成员 -------------------------------------------------------------


def test_member_delete_accepts_temporary_token_for_admin(client, env):
    response = client.delete(
        f"/api/v1/activities/{ACTIVITY}/members/{MEMBER}", headers=auth(ADMIN, ["admin"])
    )
    assert response.status_code == 200
    assert response.json() == {"detail": "Activity member deleted successfully"}
    assert env.members.removed == [MEMBER]
    assert env.logs.rows
    # 被删掉的那个人要单独重算：按活动重算只会覆盖剩下的人，他的缓存会一直错。
    assert env.time_refresh_calls == [(OTHER, False)]


def test_member_delete_rejects_long_lived_token(client, env):
    response = client.delete(
        f"/api/v1/activities/{ACTIVITY}/members/{MEMBER}", headers=auth(ADMIN, ["admin"], "long")
    )
    assert response.status_code == 401
    assert env.members.removed == []


def test_member_delete_keeps_admin_only_scope(client, env):
    """删除成员口径保持 admin/department：创建人身份不放行。"""
    response = client.delete(
        f"/api/v1/activities/{ACTIVITY}/members/{MEMBER}", headers=auth(CREATOR, ["student"])
    )
    assert response.status_code == 403
    assert env.members.removed == []


def test_member_delete_invalid_ids_are_rejected(client, env):
    response = client.delete(
        f"/api/v1/activities/{ACTIVITY}/members/not-an-object-id", headers=auth(ADMIN, ["admin"])
    )
    assert response.status_code == 400
    assert env.members.removed == []

    response = client.delete(
        "/api/v1/activities/not-an-object-id/members/" + MEMBER, headers=auth(ADMIN, ["admin"])
    )
    assert response.status_code == 400
    assert env.members.removed == []


def test_member_delete_missing_member_is_404(client, env):
    response = client.delete(
        f"/api/v1/activities/{ACTIVITY}/members/0000000000000000000000aa",
        headers=auth(ADMIN, ["admin"]),
    )
    assert response.status_code == 404
