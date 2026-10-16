"""Unit tests for ``util.permission.user.validate_read_group_permission``.

Fully offline: ``db.backends`` factories are monkeypatched to return fake spies,
so no engine, session or connection is ever created. The function is called
directly (not through a route) to pin down every branch of the new
``allow_class`` opt-in.

Run from the repository root::

    ./.venv/bin/python -m pytest tests/golden/permissions/test_group_read_permission.py -q

``pytest-asyncio`` is installed in strict mode (no ``asyncio_mode`` override in
any config), so the async tests carry an explicit ``@pytest.mark.asyncio``.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from db import backends
from db.ids import InvalidOid
from util.permission.user import validate_read_group_permission

CLASS_OID = "64b7f0c2e1a3b4c5d6e7f801"
OTHER_CLASS_OID = "64b7f0c2e1a3b4c5d6e7f802"
PERMISSION_OID = "64b7f0c2e1a3b4c5d6e7f803"
STUDENT_OID = "64b7f0c2e1a3b4c5d6e7f804"
SECRETARY_OID = "64b7f0c2e1a3b4c5d6e7f805"
MALFORMED = "not-an-oid"


class _FakeGroupsRepo:
    """Groups repo stand-in: permission side is an explicit oid whitelist.

    ``get_permission_group`` returns a row only for ``permission_oids``; every
    other oid is reported as a class group (``None``), which is exactly what the
    permission check keys off. Calls are recorded so a test can prove the
    malformed-id guard short-circuited before touching the repo.
    """

    def __init__(self, permission_oids=()) -> None:
        self.permission_oids = set(permission_oids)
        self.permission_calls: list[str] = []

    async def get_permission_group(self, oid: str):
        self.permission_calls.append(oid)
        if oid in self.permission_oids:
            return {
                "_id": oid,
                "name": "某权限组",
                "type": "permission",
                "permissions": [],
            }
        return None


class _FakeUsersRepo:
    """Users repo stand-in for the secretary branch (``get_zvms_new_user``)."""

    def __init__(self, users: dict[str, dict]) -> None:
        self.users = users

    async def get_by_oid(self, oid: str):
        return self.users.get(oid)


@pytest.fixture()
def groups(monkeypatch) -> _FakeGroupsRepo:
    repo = _FakeGroupsRepo(permission_oids={PERMISSION_OID})
    users = _FakeUsersRepo(
        {
            STUDENT_OID: {"_id": STUDENT_OID, "class_id": CLASS_OID},
            SECRETARY_OID: {"_id": SECRETARY_OID, "class_id": CLASS_OID},
        }
    )
    monkeypatch.setattr(backends, "get_groups_repo", lambda *a, **k: repo)
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: users)
    return repo


def _student() -> dict:
    return {"_id": STUDENT_OID, "id": STUDENT_OID, "per": ["student"]}


def _secretary() -> dict:
    return {"_id": SECRETARY_OID, "id": SECRETARY_OID, "per": ["secretary"]}


def _admin() -> dict:
    return {"_id": STUDENT_OID, "id": STUDENT_OID, "per": ["admin"]}


def _department() -> dict:
    return {"_id": STUDENT_OID, "id": STUDENT_OID, "per": ["department"]}


async def _forbidden(user: dict, group: str, *, allow_class: bool) -> HTTPException:
    with pytest.raises(HTTPException) as excinfo:
        await validate_read_group_permission(
            user, group, "volunteer", allow_class=allow_class
        )
    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "权限不足"
    return excinfo.value


@pytest.mark.asyncio
async def test_student_may_read_a_class_group_when_opted_in(groups) -> None:
    assert (
        await validate_read_group_permission(
            _student(), CLASS_OID, "volunteer", allow_class=True
        )
        is True
    )
    assert groups.permission_calls == [CLASS_OID]


@pytest.mark.asyncio
async def test_student_may_not_read_a_permission_group_even_when_opted_in(groups) -> None:
    await _forbidden(_student(), PERMISSION_OID, allow_class=True)


@pytest.mark.asyncio
async def test_student_is_still_rejected_without_the_opt_in(groups) -> None:
    # Pins the four list endpoints in groups_v2_router: they call without
    # allow_class, so a plain student must keep getting 403 there.
    exception = await _forbidden(_student(), CLASS_OID, allow_class=False)
    assert not isinstance(exception, InvalidOid)
    assert groups.permission_calls == []  # the class rule never ran


@pytest.mark.asyncio
async def test_secretary_reads_their_own_class(groups) -> None:
    assert (
        await validate_read_group_permission(
            _secretary(), CLASS_OID, "volunteer", allow_class=False
        )
        is True
    )


@pytest.mark.asyncio
async def test_secretary_is_rejected_for_another_class(groups) -> None:
    await _forbidden(_secretary(), OTHER_CLASS_OID, allow_class=False)


@pytest.mark.asyncio
async def test_secretary_is_rejected_for_a_permission_group_with_opt_in(groups) -> None:
    await _forbidden(_secretary(), PERMISSION_OID, allow_class=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("user_factory", [_admin, _department])
@pytest.mark.parametrize(
    "group", [CLASS_OID, OTHER_CLASS_OID, PERMISSION_OID, MALFORMED]
)
async def test_admin_and_department_always_pass(groups, user_factory, group) -> None:
    assert (
        await validate_read_group_permission(
            user_factory(), group, "volunteer", allow_class=True
        )
        is True
    )


@pytest.mark.asyncio
async def test_malformed_group_id_is_a_plain_403_not_an_invalid_oid(groups) -> None:
    with pytest.raises(HTTPException) as excinfo:
        await validate_read_group_permission(
            _student(), MALFORMED, "volunteer", allow_class=True
        )
    assert type(excinfo.value) is HTTPException  # never InvalidOid -> never a 500
    assert excinfo.value.status_code == 403
    # is_valid_oid short-circuits the class rule before any repo access.
    assert groups.permission_calls == []
