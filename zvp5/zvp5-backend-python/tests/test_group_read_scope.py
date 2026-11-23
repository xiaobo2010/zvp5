"""班级成员 / 活动 / 时长读取的班号边界，全离线。

``GET /groups/{id}`` 早就只允许「管理员/学生会或本班学生」；同一层级的三个
列表端点此前没有这道闸，任何登录学生拿到班号就能枚举别的班。这里同时验证：

* 三个端点对跨班普通学生返回 403（且在建任何会话之前就拒绝）；
* 管理员 / 学生会不受班号限制；
* 本班学生仍然可以读自己的班。

仓储用 fake 注入，当前用户来自 ``dependency_overrides``，不连 ADB。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from routers import groups_router
from routers.groups_router import _ensure_group_read_scope
from util.object_id import get_current_user

PREFIX = "/api/v1/groups"
CLASS_OID = "64b7f0c2e1a3b4c5d6e7f801"
OTHER_CLASS_OID = "64b7f0c2e1a3b4c5d6e7f802"
STUDENT_OID = "64b7f0c2e1a3b4c5d6e7f804"


def _client(user: dict) -> TestClient:
    app = FastAPI()
    app.include_router(groups_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _student(class_id: str | None = OTHER_CLASS_OID) -> dict:
    return {
        "_id": STUDENT_OID,
        "id": STUDENT_OID,
        "per": ["student"],
        "class_id": class_id,
    }


@pytest.mark.parametrize("suffix", ["/users", "/activities", "/time"])
def test_cross_class_read_is_denied_before_touching_repositories(suffix: str) -> None:
    # 三个端点都需要仓储才能返回 200；如果边界没挡住，这里会因未注入的
    # repo/会话直接 500，而不是预期的 403。
    response = _client(_student()).get(f"{PREFIX}/{CLASS_OID}{suffix}")

    assert response.status_code == 403
    assert response.json() == {"detail": "权限不足"}


@pytest.mark.asyncio
async def test_same_class_student_passes_scope_check() -> None:
    await _ensure_group_read_scope(_student(CLASS_OID), CLASS_OID)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "department"])
async def test_manager_passes_any_class_scope(role: str) -> None:
    await _ensure_group_read_scope(
        {"_id": STUDENT_OID, "id": STUDENT_OID, "per": [role], "class_id": OTHER_CLASS_OID},
        CLASS_OID,
    )


@pytest.mark.asyncio
async def test_student_without_class_is_denied(monkeypatch) -> None:
    class _Users:
        async def get_by_oid(self, oid: str):
            return {"_id": oid, "class_id": None}

    monkeypatch.setattr(
        groups_router.backends,
        "get_users_repo",
        lambda *args, **kwargs: _Users(),
    )

    with pytest.raises(HTTPException) as denied:
        await _ensure_group_read_scope(
            {"_id": STUDENT_OID, "id": STUDENT_OID, "per": ["student"]},
            CLASS_OID,
        )

    assert denied.value.status_code == 403
