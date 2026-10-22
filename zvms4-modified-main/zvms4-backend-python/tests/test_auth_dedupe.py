"""One authentication per request, even across separate dependency callables.

A write endpoint depends on ``get_current_user`` and on ``inject_log``, whose
``authenticated_log_user`` lives in another module and therefore cannot share
FastAPI's own dependency cache. Both used to read the user row and the roles,
so every write request paid for authentication twice.
"""

from types import SimpleNamespace

import pytest
from starlette.requests import Request

from db import backends
from util import cert, object_id

OID = "000000000000000000000001"


def make_request() -> Request:
    return Request(
        {"type": "http", "method": "GET", "path": "/", "headers": [], "query_string": b""}
    )


@pytest.fixture
def reads(monkeypatch):
    calls: list[str] = []
    user = {"_id": OID, "roles": ["student"], "password": None, "session_id": "v1"}

    async def get_by_oid(oid):
        calls.append(oid)
        return user if oid == OID else None

    monkeypatch.setattr(backends, "get_users_repo", lambda: SimpleNamespace(get_by_oid=get_by_oid))
    return calls


def token(kind: str = "long"):
    return cert.jwt_encode(OID, [], ["admin"], kind, session_id="v1")


@pytest.mark.asyncio
async def test_route_and_log_dependency_share_one_user_read(reads):
    signed = token()
    request = make_request()

    current = await object_id.get_current_user(signed, request=request)
    logger = await object_id.authenticated_log_user(signed, request=request)

    assert current["id"] == logger["id"] == OID
    assert reads == [OID]


@pytest.mark.asyncio
async def test_cache_never_satisfies_a_scope_the_token_cannot_serve(reads):
    signed = token()
    request = make_request()

    await object_id.get_current_user(signed, request=request)

    with pytest.raises(Exception) as failure:
        await object_id.compulsory_temporary_token(signed, request=request)

    assert getattr(failure.value, "status_code", None) == 401
    assert reads == [OID]


@pytest.mark.asyncio
async def test_no_request_means_no_cache_and_no_extra_reads(reads):
    signed = token()

    first = await object_id.get_current_user(signed)
    second = await object_id.authenticated_log_user(signed)

    assert first["id"] == second["id"] == OID
    assert reads == [OID, OID]


@pytest.mark.asyncio
async def test_cached_temp_session_still_raises_the_log_floor(reads):
    signed = cert.jwt_encode(OID, [], ["admin"], "temporary", session_id="v1", tp=True)
    request = make_request()

    await object_id.authenticated_log_user(signed, request=request)
    assert object_id.log_level_floor.get() == "warning"

    object_id.log_level_floor.set(None)
    await object_id.authenticated_log_user(signed, request=request)
    assert object_id.log_level_floor.get() == "warning"
    assert reads == [OID]
