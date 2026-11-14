"""成员记录读/删的归属与权限口径（离线：假仓库 + 直调 handler）。

``GET /v2/activities/{aid}/members/{sid}`` 原先完全不看路径上的活动，也不看
调用者是谁：任何人拿任意活动 ID 都能读到别人的时长记录；``DELETE`` 更直接
按 ``session_id`` 删，活动 ID 写错也照删不误。这里钉住修好之后的口径：

1. 本人读自己的记录 ⇒ 200；
2. 非本人的普通学生读别人的记录 ⇒ 403（与写路径同权：admin/department）；
3. admin/department 可以读；
4. 跨活动的 GET/DELETE ⇒ 404（不泄露真实归属，文案与既有 ``Activity member
   not found`` 一致）；
5. 删成员成功后按**被删的那个人**重算缓存（按活动重算覆盖不到他）。

    cd <repo root> && .venv/bin/python -m pytest tests/test_activity_member_access.py -q
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from db import backends
from routers import activities_v2_router

OWNER = "000000000000000000000001"
OTHER = "000000000000000000000002"
MANAGER = "000000000000000000000003"
ACTIVITY = "000000000000000000000010"
OTHER_ACTIVITY = "000000000000000000000011"
MEMBER_ROW = "000000000000000000000101"
OTHER_ROW = "000000000000000000000102"

STUDENT = {"id": OWNER, "per": ["student"]}
OTHER_STUDENT = {"id": OTHER, "per": ["student"]}
ADMIN = {"id": MANAGER, "per": ["admin"]}


def _member(session_id: str, activity: str, member: str) -> dict:
    return {
        "_id": session_id,
        "activity": activity,
        "member": member,
        "status": "effective",
        "mode": "on-campus",
        "duration": 2.0,
    }


class _MembersRepo:
    def __init__(self, state: dict) -> None:
        self.state = state

    async def get_by_oid(self, oid):
        for row in self.state["rows"]:
            if row["_id"] == oid:
                return dict(row)
        return None

    async def remove(self, oid):
        self.state["removed"].append(oid)


class _ActivitiesRepo:
    async def get(self, oid):
        if oid == ACTIVITY:
            return {"_id": ACTIVITY, "name": "支教活动"}
        return None


class _Log:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.inserts = 0

    def with_text(self, text, *args, **kwargs):
        self.texts.append(text)
        return self

    async def insert_log(self, session=None):
        self.inserts += 1


@pytest.fixture
def state(monkeypatch):
    state = {
        "rows": [
            _member(MEMBER_ROW, ACTIVITY, OWNER),
            _member(OTHER_ROW, OTHER_ACTIVITY, OTHER),
        ],
        "removed": [],
        "refreshed": [],
        "log": _Log(),
    }

    monkeypatch.setattr(
        backends, "get_members_repo", lambda session=None: _MembersRepo(state)
    )
    monkeypatch.setattr(
        backends, "get_activities_repo", lambda session=None: _ActivitiesRepo()
    )
    # 名字解析走 users 仓库：这里给一个足够用的批量实现。
    class _Users:
        async def get_names(self, oids):
            return {oid: f"用户{oid[-2:]}" for oid in oids}

    monkeypatch.setattr(backends, "get_users_repo", lambda session=None: _Users())

    async def calculate(oid, allow_cache=True):
        state["refreshed"].append((str(oid), allow_cache))
        return {}

    monkeypatch.setattr(activities_v2_router, "calculate_user_time", calculate)
    return state


async def _get(state, activity_id, session_id, user):
    return await activities_v2_router.get_activity_member_v2(
        activity_id, session_id, user=user
    )


async def _delete(state, activity_id, session_id, user):
    return await activities_v2_router.delete_activity_member_v2(
        activity_id, session_id, user=user, log=state["log"]
    )


@pytest.mark.asyncio
async def test_owner_can_read_their_own_record(state):
    row = await _get(state, ACTIVITY, MEMBER_ROW, STUDENT)

    assert row["_id"] == MEMBER_ROW
    assert row["member"] == OWNER


@pytest.mark.asyncio
async def test_other_student_cannot_read_someone_elses_record(state):
    with pytest.raises(HTTPException) as failure:
        await _get(state, ACTIVITY, MEMBER_ROW, OTHER_STUDENT)

    assert failure.value.status_code == 403
    assert failure.value.detail == "Permission denied"


@pytest.mark.asyncio
async def test_manager_can_read_someone_elses_record(state):
    row = await _get(state, ACTIVITY, MEMBER_ROW, ADMIN)

    assert row["_id"] == MEMBER_ROW


@pytest.mark.asyncio
async def test_reading_through_the_wrong_activity_is_404(state):
    state["rows"].append(_member(OTHER_ROW, OTHER_ACTIVITY, OWNER))

    with pytest.raises(HTTPException) as failure:
        await _get(state, ACTIVITY, OTHER_ROW, STUDENT)

    assert failure.value.status_code == 404
    assert failure.value.detail == "Activity member not found"


@pytest.mark.asyncio
async def test_deleting_through_the_wrong_activity_is_404_and_keeps_the_row(state):
    state["rows"].append(_member(OTHER_ROW, OTHER_ACTIVITY, OWNER))

    with pytest.raises(HTTPException) as failure:
        await _delete(state, ACTIVITY, OTHER_ROW, ADMIN)

    assert failure.value.status_code == 404
    assert failure.value.detail == "Activity member not found"
    assert state["removed"] == []
    assert state["refreshed"] == []
    assert state["log"].inserts == 0


@pytest.mark.asyncio
async def test_member_delete_refreshes_the_deleted_members_own_time(state):
    result = await _delete(state, ACTIVITY, MEMBER_ROW, ADMIN)

    assert result == {"detail": "Activity member deleted successfully"}
    assert state["removed"] == [MEMBER_ROW]
    # 关键点：重算的是「被删的那个人」，不是活动剩余成员。
    assert state["refreshed"] == [(OWNER, False)]
    assert state["log"].inserts == 1
