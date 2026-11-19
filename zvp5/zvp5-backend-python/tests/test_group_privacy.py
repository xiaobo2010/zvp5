from contextlib import asynccontextmanager
from io import BytesIO
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import groups_router
from util.object_id import get_current_user
from util.user import public_user

GROUP = "000000000000000000000010"
USER = "000000000000000000000001"


def test_public_projection_drops_raw_hash_even_without_prior_masking():
    assert public_user({"password": "secret-hash", "session_id": "secret"}) == {"password": None}
    assert public_user({"password": False}) == {"password": False}
    assert public_user(
        {"id": "1", "eligibility": ["admin"], "past": ["20230001"]}
    ) == {"id": "1"}


@asynccontextmanager
async def _fake_session_scope():
    yield SimpleNamespace()


@pytest.fixture
def client(monkeypatch):
    row = {"_id": USER, "id": "123", "name": "Name", "roles": ["student"], "class_id": GROUP,
           "class_name": "Class", "banned": False, "password": "secret-hash"}
    state = {"user": {"id": USER, "per": ["admin"]}, "rows": [row]}

    async def list_by_class(oid):
        return [dict(row) for row in state["rows"]]

    async def get_by_oid(oid):
        return {"_id": USER, "class_id": "000000000000000000000099"}

    async def get(oid):
        return {"_id": GROUP, "name": "Group"}

    async def get_permission_group(oid):
        return None

    async def members_page(group_oid, query, page, size):
        # 仓储层只投影固定列（见 db/repos/oracle/groups.py:_member_row），
        # 这里刻意带上密码哈希，验证端点不会把它原样回给客户端。
        return [dict(row) for row in state["rows"]], len(state["rows"])

    monkeypatch.setattr(backends, "get_users_repo", lambda session=None: SimpleNamespace(list_by_class=list_by_class, get_by_oid=get_by_oid))
    monkeypatch.setattr(
        backends,
        "get_groups_repo",
        lambda session=None: SimpleNamespace(
            get=get,
            get_permission_group=get_permission_group,
            members_page=members_page,
        ),
    )
    monkeypatch.setattr(groups_router, "session_scope", _fake_session_scope)
    app = FastAPI()
    app.include_router(groups_router.router, prefix="/api/groups")
    app.include_router(groups_router.router, prefix="/api/v1/groups")
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    return TestClient(app), state


@pytest.mark.parametrize("path", [f"/api/v1/groups/{GROUP}/users"])
def test_class_members_do_not_expose_credentials_or_unknown_internal_fields(client, path):
    http, _ = client
    response = http.get(path)
    assert response.status_code == 200
    assert "secret" not in response.text
    assert "session_id" not in response.text
    assert "future_internal" not in response.text


def test_secretary_cannot_download_another_class_template(client):
    http, state = client
    state["user"] = {"id": USER, "per": ["secretary"]}
    response = http.get(f"/api/groups/{GROUP}/template?export_format=excel")
    assert response.status_code == 403


def test_empty_class_template_is_valid_and_import_compatible(client):
    http, state = client
    state["rows"] = []
    response = http.get(f"/api/groups/{GROUP}/template?export_format=excel")
    assert response.status_code == 200
    assert pd.read_excel(BytesIO(response.content)).columns.to_list() == [
        "_id", "Name", "ID", "Group", "On Campus", "Off Campus", "Social Practice"
    ]
