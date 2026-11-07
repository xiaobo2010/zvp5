"""成员级义工状态审核 ``PUT /v2/activities/{aid}/members/{sid}/status``。

钉住四件事：

1. 权限与「加成员/删成员」同权（``volunteer_member.validate_create_permission``）：
   只有 admin/department 能审核，普通用户 403 且不产生任何写；
2. 校验顺序：活动 404 → 成员 404 → 归属校验 404 → 同状态 400，全部不写库；
3. 状态落库后**立刻**用 ``calculate_user_time(allow_cache=False)`` 重算回填，
   下一次 ``allow_cache=True`` 读到的就是新值（effective→refused 不再计入）；
4. 审计日志类型 ``activity_member_review``、文案含操作者/活动/成员名。

    cd <repo root> && .venv/bin/python -m pytest tests/test_member_status_review.py -q
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from db import backends
from routers import activities_v2_router
from util import calculate as calc

ADMIN_OID = "000000000000000000000001"
MEMBER_OID = "000000000000000000000002"
ACTIVITY_ID = "000000000000000000000010"
OTHER_ACTIVITY_ID = "000000000000000000000011"
SESSION_ID = "000000000000000000000101"
OTHER_SESSION_ID = "000000000000000000000102"

ADMIN = {"id": ADMIN_OID, "per": ["admin"]}
PLAIN_USER = {"id": ADMIN_OID, "per": ["student"]}


def _activity(oid: str, name: str, status: str = "effective") -> dict:
    return {"_id": oid, "name": name, "status": status}


def _member(session_id: str, activity: str, status: str = "effective") -> dict:
    return {
        "_id": session_id,
        "activity": activity,
        "member": MEMBER_OID,
        "status": status,
        "mode": "on-campus",
        "duration": 2.0,
    }


class _Log:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.types: list[str] = []
        self.inserts = 0

    def with_text(self, text, *, type="", level=""):
        self.texts.append(text)
        self.types.append(type)
        return self

    async def insert_log(self, session=None):
        self.inserts += 1


class _ActivitiesRepo:
    def __init__(self, state: dict) -> None:
        self.state = state

    async def get(self, oid):
        return self.state["activities"].get(oid)

    async def list_effective(self, date_start=None, date_end=None):
        return [
            activity
            for activity in self.state["activities"].values()
            if activity["status"] == "effective"
        ]


class _MembersRepo:
    def __init__(self, state: dict) -> None:
        self.state = state

    async def get_by_oid(self, oid):
        for row in self.state["rows"]:
            if row["_id"] == oid:
                return dict(row)
        return None

    async def update_fields(self, oid, fields):
        self.state["updates"].append((oid, dict(fields)))
        for row in self.state["rows"]:
            if row["_id"] == oid:
                row.update(fields)

    async def list_for_member(self, user_id, status=None, activity_oids=None):
        rows = [dict(row) for row in self.state["rows"] if row["member"] == user_id]
        if status is not None:
            rows = [row for row in rows if row["status"] == status]
        if activity_oids is not None:
            accepted = set(activity_oids)
            rows = [row for row in rows if row["activity"] in accepted]
        return rows


class _UsersRepo:
    def __init__(self, state: dict) -> None:
        self.state = state

    async def get_by_oid(self, oid):
        return self.state["users"].get(oid)

    async def get_names(self, oids):
        return {
            oid: self.state["users"][oid]["name"]
            for oid in dict.fromkeys(oids)
            if oid in self.state["users"] and self.state["users"][oid].get("name")
        }


class _TimeRepo:
    def __init__(self, state: dict) -> None:
        self.state = state

    async def get_for_user(self, user_oid):
        return self.state["cache"].get(user_oid)

    async def upsert_for_user(self, user_oid, on_campus, off_campus, social):
        self.state["cache_writes"].append(
            (user_oid, on_campus, off_campus, social)
        )
        self.state["cache"][user_oid] = {
            "on_campus_raw": on_campus,
            "off_campus_raw": off_campus,
            "social_practice": social,
        }


@pytest.fixture
def state(monkeypatch):
    state = {
        "activities": {
            ACTIVITY_ID: _activity(ACTIVITY_ID, "支教活动"),
            OTHER_ACTIVITY_ID: _activity(OTHER_ACTIVITY_ID, "另一活动"),
        },
        "rows": [_member(SESSION_ID, ACTIVITY_ID)],
        "users": {
            ADMIN_OID: {"_id": ADMIN_OID, "name": "管理员", "id": "20240001"},
            MEMBER_OID: {"_id": MEMBER_OID, "name": "张三", "id": "20241726"},
        },
        "updates": [],
        "cache": {},
        "cache_writes": [],
        "log": _Log(),
    }

    monkeypatch.setattr(
        backends, "get_activities_repo", lambda session=None: _ActivitiesRepo(state)
    )
    monkeypatch.setattr(
        backends, "get_members_repo", lambda session=None: _MembersRepo(state)
    )
    monkeypatch.setattr(
        backends, "get_users_repo", lambda session=None: _UsersRepo(state)
    )
    monkeypatch.setattr(
        backends, "get_time_repo", lambda session=None: _TimeRepo(state)
    )
    return state


async def _call(
    state,
    *,
    activity_id=ACTIVITY_ID,
    session_id=SESSION_ID,
    status="refused",
    user=ADMIN,
):
    return await activities_v2_router.modify_activity_member_status_v2(
        activity_id,
        session_id,
        activities_v2_router.PutMemberStatus(status=status),
        user=user,
        log=state["log"],
    )


@pytest.mark.asyncio
async def test_plain_user_gets_403_and_nothing_is_written(state):
    state["cache"][MEMBER_OID] = {
        "on_campus_raw": 2.0,
        "off_campus_raw": 0.0,
        "social_practice": 0.0,
    }

    with pytest.raises(HTTPException) as failure:
        await _call(state, user=PLAIN_USER)

    assert failure.value.status_code == 403
    assert state["updates"] == []
    assert state["cache_writes"] == []
    assert state["log"].inserts == 0
    assert state["cache"][MEMBER_OID]["on_campus_raw"] == 2.0


@pytest.mark.asyncio
async def test_missing_activity_is_404(state):
    with pytest.raises(HTTPException) as failure:
        await _call(state, activity_id="0000000000000000000000ff")

    assert failure.value.status_code == 404
    assert failure.value.detail == "Activity not found"
    assert state["updates"] == []
    assert state["log"].inserts == 0


@pytest.mark.asyncio
async def test_missing_member_is_404(state):
    with pytest.raises(HTTPException) as failure:
        await _call(state, session_id="0000000000000000000000ff")

    assert failure.value.status_code == 404
    assert failure.value.detail == "Activity member not found"
    assert state["updates"] == []
    assert state["log"].inserts == 0


@pytest.mark.asyncio
async def test_member_of_another_activity_is_404(state):
    state["rows"].append(_member(OTHER_SESSION_ID, OTHER_ACTIVITY_ID))

    with pytest.raises(HTTPException) as failure:
        await _call(state, session_id=OTHER_SESSION_ID)

    assert failure.value.status_code == 404
    assert failure.value.detail == "Activity member not found"
    assert state["updates"] == []
    assert state["log"].inserts == 0


@pytest.mark.asyncio
async def test_same_status_is_400_and_skips_cache_refresh(state):
    with pytest.raises(HTTPException) as failure:
        await _call(state, status="effective")

    assert failure.value.status_code == 400
    assert failure.value.detail == "Activity member status is not changed"
    assert state["updates"] == []
    assert state["cache_writes"] == []
    assert state["log"].inserts == 0


@pytest.mark.asyncio
async def test_refused_review_drops_the_hours_from_the_cached_time(state):
    # 先有一份「effective 计入 2 小时」的缓存，确认读路径确实走缓存。
    state["cache"][MEMBER_OID] = {
        "on_campus_raw": 2.0,
        "off_campus_raw": 0.0,
        "social_practice": 0.0,
    }
    before = await calc.calculate_user_time(MEMBER_OID, allow_cache=True)
    assert before == {"on-campus": 2.0, "off-campus": 0.0, "social-practice": 0.0}

    result = await _call(state, status="refused")

    assert result == {"id": SESSION_ID, "status": "refused"}
    assert state["updates"] == [(SESSION_ID, {"status": "refused"})]
    # 重算走 allow_cache=False：从有效成员重新聚合，结果回填缓存。
    assert state["cache_writes"] == [(MEMBER_OID, 0.0, 0.0, 0.0)]
    after = await calc.calculate_user_time(MEMBER_OID, allow_cache=True)
    assert after == {"on-campus": 0.0, "off-campus": 0.0, "social-practice": 0.0}

    assert state["log"].types == ["activity_member_review"]
    assert state["log"].inserts == 1
    assert state["log"].texts == [
        "用户 管理员 将义工活动《支教活动》成员 张三 的状态审核为 refused"
    ]


@pytest.mark.asyncio
async def test_pending_review_also_drops_the_hours(state):
    state["cache"][MEMBER_OID] = {
        "on_campus_raw": 2.0,
        "off_campus_raw": 0.0,
        "social_practice": 0.0,
    }

    await _call(state, status="pending")

    after = await calc.calculate_user_time(MEMBER_OID, allow_cache=True)
    assert after == {"on-campus": 0.0, "off-campus": 0.0, "social-practice": 0.0}
    assert state["rows"][0]["status"] == "pending"
