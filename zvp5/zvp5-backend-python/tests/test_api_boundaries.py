import pytest
from fastapi import FastAPI, HTTPException, Response
from fastapi.testclient import TestClient
from pydantic import ValidationError

from db import backends
from routers import attachments_router, groups_router, users_router
from typings.log import inject_log
from util.object_id import get_current_user

USER = "000000000000000000000001"
GROUP = "000000000000000000000010"


@pytest.mark.asyncio
async def test_missing_grade_statistics_are_a_client_error(monkeypatch):
    async def allow(*args, **kwargs):
        return None

    class UsersRepo:
        async def get_by_oid(self, oid):
            return {"_id": USER, "id": "20250001", "class_id": GROUP}

    class GroupsRepo:
        async def get(self, oid):
            return {"_id": GROUP, "name": "Class"}

    class StatsRepo:
        async def get_indicators(self):
            return {"percentiles": {"2024": {"on-campus": []}}}

        async def list_group_indicators(self):
            return [{"group": GROUP, "percentiles": {"on-campus": []}}]

    monkeypatch.setattr(users_router, "validate_read_user_permission", allow)
    monkeypatch.setattr(backends, "get_users_repo", lambda: UsersRepo())
    monkeypatch.setattr(backends, "get_groups_repo", lambda: GroupsRepo())
    monkeypatch.setattr(backends, "get_stats_repo", lambda: StatsRepo())

    with pytest.raises(HTTPException) as failure:
        await users_router.get_user_time_statistics_v2(
            USER, user={"id": USER, "per": []}
        )
    assert failure.value.status_code == 400
    assert "cohort" in failure.value.detail


def test_login_mode_is_a_closed_enum():
    assert users_router.AuthUser(
        id="20250001", mode="short", credential="ciphertext", purpose="verify"
    ).mode == "short"
    with pytest.raises(ValidationError):
        users_router.AuthUser(id="20250001", mode="otp", credential="ciphertext")
    assert users_router.AuthUser(
        id="20250001", purpose="unknown-purpose", credential="ciphertext"
    ).purpose == "unknown-purpose"


@pytest.mark.asyncio
async def test_initial_password_response_is_not_cacheable(monkeypatch):
    class UsersRepo:
        async def find_by_business_id(self, business_id):
            return None

        async def create(self, *args):
            return USER

        async def get_names(self, oids):
            return {}

    class Log:
        def with_text(self, *args, **kwargs):
            return self

        async def insert_log(self):
            return None

    monkeypatch.setattr(backends, "get_users_repo", lambda: UsersRepo())
    response = Response()
    generated = await users_router.create_user_v3(
        users_router.NewUserRequest(
            id="20240101", name="测试", roles=["student"]
        ),
        response=response,
        user={"id": USER, "per": ["admin"]},
        log=Log(),
    )
    assert generated["initial_password"]
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize(
    ("router", "prefix", "path"),
    [
        (groups_router.router, "/v2/groups", f"/v2/groups?page=0&size=10"),
        (
            attachments_router.router,
            "/attachments",
            "/attachments?page=0&size=20",
        ),
    ],
)
def test_zero_page_is_rejected_by_query_validation(monkeypatch, router, prefix, path):
    app = FastAPI()
    app.include_router(router, prefix=prefix)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": USER,
        "per": ["admin"],
    }
    app.dependency_overrides[inject_log] = lambda: None
    assert TestClient(app).get(path).status_code == 422
