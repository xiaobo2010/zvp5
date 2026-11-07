"""``users_v3.get_user_v3``：profile 增加 ``past`` / ``sex``（additive，离线）。

契约：profile 与 v1 ``read_user`` 的 ``user_info`` 逐字段同形（``past`` 缺省 ``[]``、
``sex`` 缺省 ``"unknown"``），且绝不带 ``password`` / ``session_id``。

    cd <repo root> && .venv/bin/python -m pytest tests/test_user_profile_fields.py -q
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import users_v3_router
from util.user import user_profile

PREFIX = "/api/v1/users"
TARGET = "000000000000000000000002"
CLASS = "0000000000000000000000ff"
READER = {"id": "000000000000000000000001", "per": ["student"]}

PROFILE_KEYS = {
    "_id",
    "id",
    "name",
    "sex",
    "roles",
    "class_id",
    "group",
    "past",
    "banned",
}


@pytest.fixture()
def client(monkeypatch):
    doc = {
        "_id": TARGET,
        "id": "2026002",
        "name": "李四",
        "sex": "F",
        "roles": ["student"],
        "class_id": CLASS,
        "past": ["高一(2)班"],
        "banned": 0,
        "eligibility": ["volunteer"],
        "password": "should-never-leak",
        "session_id": "v1",
    }

    async def get_by_oid(oid):
        return doc if oid == TARGET else None

    monkeypatch.setattr(
        backends, "get_users_repo", lambda *a, **k: SimpleNamespace(get_by_oid=get_by_oid)
    )

    app = FastAPI()
    app.include_router(users_v3_router.router, prefix=PREFIX)
    app.dependency_overrides[users_v3_router.get_current_user] = lambda: READER
    return TestClient(app)


def test_profile_carries_past_and_sex(client) -> None:
    body = client.get(f"{PREFIX}/{TARGET}").json()

    assert body["code"] == 200
    assert set(body["data"]) == PROFILE_KEYS
    assert body["data"]["past"] == ["高一(2)班"]
    assert body["data"]["sex"] == "F"
    assert body["data"]["class_id"] == CLASS
    assert body["data"]["group"] == [CLASS]
    assert body["data"]["banned"] is False


def test_profile_defaults_when_the_columns_are_null(monkeypatch) -> None:
    doc = {"_id": TARGET, "id": "2026002", "name": "李四", "roles": []}

    async def get_by_oid(oid):
        return doc if oid == TARGET else None

    monkeypatch.setattr(
        backends, "get_users_repo", lambda *a, **k: SimpleNamespace(get_by_oid=get_by_oid)
    )
    app = FastAPI()
    app.include_router(users_v3_router.router, prefix=PREFIX)
    app.dependency_overrides[users_v3_router.get_current_user] = lambda: READER

    data = TestClient(app).get(f"{PREFIX}/{TARGET}").json()["data"]

    assert data == {
        "_id": TARGET,
        "id": "2026002",
        "name": "李四",
        "sex": "unknown",
        "roles": [],
        "class_id": "",
        "group": [],
        "past": [],
        "banned": False,
    }


def test_user_profile_never_leaks_credentials() -> None:
    summary = user_profile(
        {
            "_id": TARGET,
            "id": "2026002",
            "name": "李四",
            "password": "should-never-leak",
            "session_id": "v1",
        }
    )
    assert set(summary) == PROFILE_KEYS
