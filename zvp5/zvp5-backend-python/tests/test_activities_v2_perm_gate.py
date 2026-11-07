"""``PUT /api/v2/activities/{id}/status`` 的 admin 放行闸门。

回归的是 ``modify_activity_status_v2`` 里读错键的老 bug：判断写的是
``user.get("perm")``（已退役的键），现役用户字典只有 ``per``。键读不到时
``"admin" not in None`` 会抛 ``TypeError``，于是**任何**尝试修改非 pending
活动的请求都会 500，而不是走 400 的既定拒绝文案 —— admin 也一并被打死。

钉住三点：

1. admin 改非 pending 活动：不再 TypeError，正常落库；
2. 普通用户改非 pending 活动：400 + 既有 detail（闸门仍然生效）；
3. pending 活动不受该分支影响（原来就允许）。

    cd <repo root> && .venv/bin/python -m pytest tests/test_activities_v2_perm_gate.py -q
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from db import backends
from routers import activities_v2_router

ADMIN_OID = "000000000000000000000001"
PLAIN_OID = "000000000000000000000002"
ACTIVITY_ID = "000000000000000000000010"

ADMIN = {"id": ADMIN_OID, "per": ["admin"]}
DEPARTMENT_USER = {"id": PLAIN_OID, "per": ["department"]}
PLAIN_USER = {"id": PLAIN_OID, "per": ["student"]}


def _activity(status: str) -> dict:
    return {
        "_id": ACTIVITY_ID,
        "type": "on-campus",
        "name": "支教活动",
        "description": "说明",
        "date": datetime(2026, 11, 1, 9, 0, 0),
        "createdAt": datetime(2026, 10, 1, 9, 0, 0),
        "updatedAt": datetime(2026, 10, 1, 9, 0, 0),
        "appointee": PLAIN_OID,
        "approver": "authority",
        "creator": ADMIN_OID,
        "status": status,
        "place": "学校",
        "origin": "activities",
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
        return self.state["activity"]

    async def update(self, oid, fields):
        self.state["updates"].append((oid, dict(fields)))


class _UsersRepo:
    def __init__(self, state: dict) -> None:
        self.state = state

    async def get_names(self, oids):
        return {
            oid: self.state["names"][oid]
            for oid in dict.fromkeys(oids)
            if oid in self.state["names"]
        }


@pytest.fixture
def state(monkeypatch):
    state = {
        "activity": _activity("effective"),
        "updates": [],
        "log": _Log(),
        "names": {ADMIN_OID: "管理员", PLAIN_OID: "张三"},
    }
    monkeypatch.setattr(
        backends, "get_activities_repo", lambda session=None: _ActivitiesRepo(state)
    )
    monkeypatch.setattr(
        backends, "get_users_repo", lambda session=None: _UsersRepo(state)
    )
    # 后台时长重算与闸门无关，别在事件循环里留 fire-and-forget 任务。
    monkeypatch.setattr(
        activities_v2_router, "_schedule_user_time_refresh", lambda activity_id: None
    )
    return state


async def _call(state, *, status="refused", user=ADMIN):
    return await activities_v2_router.modify_activity_status_v2(
        ACTIVITY_ID,
        activities_v2_router.PutActivityStatus(status=status),
        user=user,
        log=state["log"],
    )


@pytest.mark.asyncio
async def test_admin_can_change_non_pending_activity(state):
    """老 bug 下这里会抛 TypeError（读 ``perm``），admin 也被挡。"""

    response = await _call(state, user=ADMIN)

    assert isinstance(response, JSONResponse)
    assert response.status_code == 200
    assert len(state["updates"]) == 1
    oid, fields = state["updates"][0]
    assert oid == ACTIVITY_ID
    assert fields["status"] == "refused"
    # 非 pending 活动不算「审核通过」，不带 approver。
    assert "approver" not in fields
    assert state["log"].types == ["activity_update"]
    assert state["log"].inserts == 1


@pytest.mark.asyncio
async def test_plain_user_is_still_rejected_on_non_pending_activity(state):
    with pytest.raises(HTTPException) as failure:
        await _call(state, user=PLAIN_USER)

    assert failure.value.status_code == 400
    assert failure.value.detail == (
        "Activity status can only be modified from pending to effective or refused"
    )
    assert state["updates"] == []


@pytest.mark.asyncio
async def test_pending_activity_is_untouched_by_the_admin_gate(state):
    state["activity"] = _activity("pending")

    await _call(state, status="effective", user=DEPARTMENT_USER)

    assert state["updates"][0][1]["status"] == "effective"


@pytest.mark.asyncio
async def test_admin_branch_reads_the_live_per_key(state):
    """用户字典里没有 ``perm`` 键也不再崩：分支只看 ``per``。"""

    state["activity"] = _activity("effective")

    await _call(state, user={"id": ADMIN_OID, "per": ["admin", "department"]})

    assert state["updates"][0][0] == ACTIVITY_ID
