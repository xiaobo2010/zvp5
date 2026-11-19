"""``GET /api/v1/users`` 的 ``query`` 上限 64：与同类端点同一口径。

``query`` 会经 ``re.escape`` 进 ``REGEXP_LIKE``；没有长度上限时，长度本身就是
可由调用方控制的成本。超长直接 422（由 FastAPI 的 Query 校验给出）。
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import users_router
from util.object_id import get_current_user

ADMIN = "000000000000000000000001"
PATH = "/api/v1/users"


def _client(monkeypatch) -> tuple[TestClient, list]:
    calls = []

    async def list_page(query, role, page, size):
        calls.append({"query": query, "role": role, "page": page, "size": size})
        return [], 0

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda *a, **k: SimpleNamespace(list_page=list_page),
    )
    app = FastAPI()
    app.include_router(users_router.router, prefix=PATH)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": ADMIN,
        "per": ["admin"],
    }
    return TestClient(app), calls


def test_query_of_65_characters_is_rejected(monkeypatch) -> None:
    client, calls = _client(monkeypatch)

    response = client.get(PATH, params={"query": "a" * 65})

    assert response.status_code == 422
    assert calls == []


def test_query_of_64_characters_is_accepted(monkeypatch) -> None:
    client, calls = _client(monkeypatch)

    response = client.get(PATH, params={"query": "a" * 64})

    assert response.status_code == 200
    assert calls == [{"query": "a" * 64, "role": "", "page": 1, "size": 10}]


def test_oversized_size_is_still_accepted_by_the_query_layer(monkeypatch) -> None:
    """``size`` 的 ``le=1000`` 不动：老客户端不该 422，夹取在语句层做。"""
    client, calls = _client(monkeypatch)

    response = client.get(PATH, params={"size": 1000})

    assert response.status_code == 200
    assert calls[0]["size"] == 1000
