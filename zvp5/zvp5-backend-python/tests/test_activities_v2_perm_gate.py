"""``PUT /api/v1/activities/{id}/status`` 的 admin 放行闸门。

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
from db import cache
from db.cached_repos import CachedActivitiesRepo
from routers import activities_router

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

    async def cas_status(self, oid, expected, fields):
        # CAS：期望状态不符就一行都改不到（并发审核的输家）。
        self.state["cas_calls"].append((oid, expected, dict(fields)))
        if self.state.get("cas_conflict"):
            return False
        if self.state["activity"]["status"] != expected:
            return False
        self.state["updates"].append((oid, dict(fields)))
        self.state["activity"].update(fields)
        return True


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
        "cas_calls": [],
        "cas_conflict": False,
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
        activities_router, "_schedule_user_time_refresh", lambda activity_id: None
    )
    return state


async def _call(state, *, status="refused", user=ADMIN):
    return await activities_router.modify_activity_status_v2(
        ACTIVITY_ID,
        activities_router.PutActivityStatus(status=status),
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


@pytest.mark.asyncio
async def test_status_write_is_a_compare_and_set_on_the_expected_status(state):
    """写库带着「读到的旧状态」：CAS 命中才继续重算/审计。"""

    await _call(state, status="refused", user=ADMIN)

    assert state["cas_calls"] == [
        (ACTIVITY_ID, "effective", state["cas_calls"][0][2])
    ]
    fields = state["cas_calls"][0][2]
    assert fields["status"] == "refused"
    assert "approver" not in fields
    assert state["log"].inserts == 1


@pytest.mark.asyncio
async def test_lost_cas_race_is_409_without_refresh_or_audit(state):
    """并发下输掉审核的一方：409，且不得重算时长、不得写审计。"""
    state["cas_conflict"] = True

    with pytest.raises(HTTPException) as failure:
        await _call(state, status="refused", user=ADMIN)

    assert failure.value.status_code == 409
    assert failure.value.detail == "活动状态已被其他操作修改，请刷新后重试"
    assert state["updates"] == []
    assert state["log"].inserts == 0


@pytest.mark.asyncio
async def test_pending_approval_still_records_the_approver(state):
    """pending → effective 是「审核通过」：CAS 里仍要带 approver。"""
    state["activity"] = _activity("pending")

    await _call(state, status="effective", user=ADMIN)

    fields = state["cas_calls"][0][2]
    assert fields["status"] == "effective"
    assert fields["approver"] == ADMIN_OID


class _CacheClient:
    """只够 ``db.cache`` 用的内存 Redis 替身（get/set/delete/incr/expire）。"""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def get(self, key: str):
        return self.data.get(key)

    async def set(self, key: str, value: str, px: int | None = None):
        self.data[key] = value
        return True

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            removed += 1 if self.data.pop(key, None) is not None else 0
        return removed

    async def incr(self, key: str) -> int:
        value = int(self.data.get(key, "0")) + 1
        self.data[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int) -> bool:
        return True

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_successive_review_within_cache_ttl_is_not_blocked(state, monkeypatch):
    """第一次审核写库后详情缓存必须失效：紧接着的第二次审核不能拿旧状态当 CAS 期望。

    没有 ``CachedActivitiesRepo.cas_status`` 的失效时：第一次 pending→effective
    成功后缓存仍是 pending，第二次 effective→refused 会以 ``expected=pending``
    落空并被错误地回成 409「状态已被其他操作修改」。
    """
    state["activity"] = _activity("pending")
    cache._install_client_for_tests(_CacheClient())
    repo = CachedActivitiesRepo(_ActivitiesRepo(state))
    monkeypatch.setattr(backends, "get_activities_repo", lambda session=None: repo)

    await _call(state, status="effective", user=ADMIN)  # 先缓存 pending，再审核通过
    await _call(state, status="refused", user=ADMIN)  # 短时间内第二次审核

    assert state["activity"]["status"] == "refused"
    assert [fields["status"] for _oid, fields in state["updates"]] == [
        "effective",
        "refused",
    ]
    assert state["log"].inserts == 2


@pytest.mark.asyncio
async def test_stale_review_still_conflicts_and_a_retry_sees_fresh_state(state, monkeypatch):
    """并发保护不放松：缓存旧状态被 CAS 拒绝（409），但失败也要失效缓存，重试能成功。"""
    state["activity"] = _activity("pending")
    cache._install_client_for_tests(_CacheClient())
    repo = CachedActivitiesRepo(_ActivitiesRepo(state))
    monkeypatch.setattr(backends, "get_activities_repo", lambda session=None: repo)

    await repo.get(ACTIVITY_ID)  # 缓存 pending
    state["activity"]["status"] = "refused"  # 另一位审核者已先改库，缓存尚未同步

    with pytest.raises(HTTPException) as failure:
        await _call(state, status="effective", user=ADMIN)

    assert failure.value.status_code == 409
    assert state["log"].inserts == 0

    await _call(state, status="effective", user=ADMIN)  # 重试读到 refused，CAS 命中
    assert state["activity"]["status"] == "effective"
    assert state["log"].inserts == 1
