"""「审核中活动」在后端口径里必须整体不可见：明细、描述、时长缓存三条路径。

钉住两件事：

1. ``util/statement.build_statement_data`` 的明细行与 ``generate_description``
   只在**活动 effective 且成员 effective** 时才算数，与
   ``calculate_user_time`` 的双重有效口径逐项对齐（历史上明细只看成员状态，
   于是出现「明细里有这行、统计数字却不含它」）。
2. ``PUT /v2/activities/{id}/status`` 在状态真正落库后异步重算受影响成员的
   ``user_time`` 缓存，且重算失败/成员过多都不影响接口响应。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from db import backends
from routers import activities_v2_router
from util import calculate as calc
from util import statement

USER = "000000000000000000000001"
MEMBER_A = "000000000000000000000002"
MEMBER_B = "000000000000000000000003"
ACT_EFFECTIVE = "000000000000000000000010"
ACT_PENDING = "000000000000000000000011"


def _activity(oid: str, name: str, status: str) -> dict:
    return {
        "_id": oid,
        "name": name,
        "description": "",
        "type": "on-campus",
        "status": status,
        "date": datetime(2026, 9, 1),
        "createdAt": datetime(2026, 8, 1),
        "updatedAt": datetime(2026, 8, 1),
        "appointee": USER,
        "approver": "AUTHORITY",
        "creator": USER,
        "place": "",
        "origin": "labor",
    }


ACTIVITIES = {
    ACT_EFFECTIVE: _activity(ACT_EFFECTIVE, "已审活动", "effective"),
    ACT_PENDING: _activity(ACT_PENDING, "审核中活动", "pending"),
}

# 成员状态一律 effective：缺陷态就是「活动 pending + 成员 effective」。
MEMBERS = [
    {
        "_id": "000000000000000000000101",
        "activity": ACT_EFFECTIVE,
        "member": USER,
        "status": "effective",
        "mode": "on-campus",
        "duration": 2.0,
    },
    {
        "_id": "000000000000000000000102",
        "activity": ACT_PENDING,
        "member": USER,
        "status": "effective",
        "mode": "on-campus",
        "duration": 25.0,
    },
]


class _ActivitiesRepo:
    def __init__(self, state: dict):
        self.state = state

    async def list_effective(self, date_start=None, date_end=None):
        self.state["list_effective"].append((date_start, date_end))
        return [
            activity
            for activity in ACTIVITIES.values()
            if activity["status"] == "effective"
        ]

    async def get(self, oid):
        return ACTIVITIES.get(oid)


class _MembersRepo:
    def __init__(self, state: dict):
        self.state = state

    async def list_for_member(self, user_id, status=None, activity_oids=None):
        rows = [row for row in MEMBERS if row["member"] == user_id]
        if status is not None:
            rows = [row for row in rows if row["status"] == status]
        if activity_oids is not None:
            accepted = set(activity_oids)
            rows = [row for row in rows if row["activity"] in accepted]
        return rows


class _UsersRepo:
    async def get_by_oid(self, oid):
        return {"_id": oid, "name": "张三", "id": "20241726"}


class _TimeRepo:
    def __init__(self, state: dict):
        self.state = state

    async def get_for_user(self, user_oid):
        return None

    async def upsert_for_user(self, user_oid, on_campus, off_campus, social):
        self.state["upserts"].append((user_oid, on_campus, off_campus, social))


@pytest.fixture
def statement_state(monkeypatch):
    state = {"list_effective": [], "upserts": []}
    monkeypatch.setattr(
        backends, "get_activities_repo", lambda session=None: _ActivitiesRepo(state)
    )
    monkeypatch.setattr(
        backends, "get_members_repo", lambda session=None: _MembersRepo(state)
    )
    monkeypatch.setattr(backends, "get_users_repo", lambda session=None: _UsersRepo())
    monkeypatch.setattr(
        backends, "get_time_repo", lambda session=None: _TimeRepo(state)
    )

    async def classname(user_id):
        return "高一(1)班"

    monkeypatch.setattr(statement, "get_user_classname", classname)
    return state


@pytest.mark.asyncio
async def test_statement_detail_matches_double_effective_total(statement_state):
    data, spreadsheet = await statement.build_statement_data(
        USER, (datetime(2026, 1, 1), datetime(2026, 12, 31))
    )

    total = await calc.calculate_user_time(USER, allow_cache=False)

    assert [record.name for record in data.statement_data] == ["已审活动"]
    assert sum(record.duration for record in data.statement_data) == (
        total["on-campus"] + total["off-campus"] + total["social-practice"]
    )
    assert data.stats_data.total_hours == total["on-campus"]
    assert ACT_PENDING not in {record.name for record in data.statement_data}
    assert "审核中活动" not in spreadsheet["description"]
    assert "已审活动" in spreadsheet["description"]


async def _drain_refresh_tasks():
    tasks = list(activities_v2_router._time_refresh_tasks)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


class _Log:
    def __init__(self):
        self.texts = []

    def with_text(self, text, *args, **kwargs):
        self.texts.append(text)
        return self

    async def insert_log(self):
        return None


class _Session:
    def __init__(self):
        self.committed = False

    async def commit(self):
        self.committed = True


@pytest.fixture
def status_state(monkeypatch):
    state = {
        "status": "pending",
        "bulk_calls": [],
        "upserts": [],
        "sessions": [],
        "members": [{"member": MEMBER_A}, {"member": MEMBER_B}],
        "bulk_error": None,
        "list_calls": [],
    }

    class _Activities:
        async def get(self, oid):
            activity = dict(ACTIVITIES[ACT_EFFECTIVE])
            activity["status"] = state["status"]
            return activity

        async def cas_status(self, oid, expected, fields):
            # 只有 CAS 命中才真的落库：测试里的期望状态总是当前状态。
            if expected != state["status"]:
                return False
            state["status"] = fields["status"]
            return True

    async def list_by_activity(oid, status=None):
        state["list_calls"].append(oid)
        return list(state["members"])

    async def count_distinct_members(oid):
        # 人数闸门按 DISTINCT 成员算：同一人多条 mode 行只算一个。
        return len({row["member"] for row in state["members"]})

    async def bulk(user_ids, date_start=None, date_end=None, allow_cache=True, session=None):
        state["bulk_calls"].append(
            {
                "ids": list(user_ids),
                "allow_cache": allow_cache,
                "session": session,
            }
        )
        if state["bulk_error"] is not None:
            raise state["bulk_error"]
        return {
            user_id: {"on-campus": 1.0 * index, "off-campus": 0.0, "social-practice": 0.0}
            for index, user_id in enumerate(user_ids, start=1)
        }

    @asynccontextmanager
    async def scope():
        session = _Session()
        state["sessions"].append(session)
        yield session

    class _Times:
        async def upsert_for_user(self, user_oid, on_campus, off_campus, social):
            state["upserts"].append((user_oid, on_campus, off_campus, social))

    async def display_name(oid):
        return "用户"

    monkeypatch.setattr(
        activities_v2_router.backends, "get_activities_repo", lambda session=None: _Activities()
    )
    monkeypatch.setattr(
        activities_v2_router.backends,
        "get_members_repo",
        lambda session=None: SimpleNamespace(
            list_by_activity=list_by_activity,
            count_distinct_members=count_distinct_members,
        ),
    )
    monkeypatch.setattr(
        activities_v2_router.backends, "get_time_repo", lambda session=None: _Times()
    )
    monkeypatch.setattr(activities_v2_router, "session_scope", scope)
    monkeypatch.setattr(activities_v2_router, "calculate_user_time_bulk", bulk)
    monkeypatch.setattr(activities_v2_router, "get_user_display_name", display_name)
    activities_v2_router._time_refresh_tasks.clear()
    return state


@pytest.mark.asyncio
async def test_status_change_refreshes_member_time_cache(status_state):
    response = await activities_v2_router.modify_activity_status_v2(
        ACT_EFFECTIVE,
        activities_v2_router.PutActivityStatus(status="effective"),
        user={"id": USER, "per": ["admin"]},
        log=_Log(),
    )
    await _drain_refresh_tasks()

    assert response.status_code == 200
    assert len(status_state["bulk_calls"]) == 1
    call = status_state["bulk_calls"][0]
    assert call["ids"] == [MEMBER_A, MEMBER_B]
    assert call["allow_cache"] is False
    assert call["session"] is status_state["sessions"][0]
    assert status_state["sessions"][0].committed is True
    assert [(row[0], row[1]) for row in status_state["upserts"]] == [
        (MEMBER_A, 1.0),
        (MEMBER_B, 2.0),
    ]


@pytest.mark.asyncio
async def test_refresh_failure_does_not_affect_the_response(status_state, caplog):
    status_state["bulk_error"] = RuntimeError("advisory lock timeout")

    response = await activities_v2_router.modify_activity_status_v2(
        ACT_EFFECTIVE,
        activities_v2_router.PutActivityStatus(status="effective"),
        user={"id": USER, "per": ["admin"]},
        log=_Log(),
    )
    await _drain_refresh_tasks()

    assert response.status_code == 200
    assert status_state["upserts"] == []
    assert any(
        "重算 2 名成员时长失败" in record.getMessage() for record in caplog.records
    )


@pytest.mark.asyncio
async def test_large_activity_defers_refresh_to_the_daily_rebuild(status_state, caplog):
    caplog.set_level(logging.INFO, logger="routers.activities_v2_router")
    status_state["members"] = [{"member": f"{index:024x}"} for index in range(1, 502)]

    await activities_v2_router.modify_activity_status_v2(
        ACT_EFFECTIVE,
        activities_v2_router.PutActivityStatus(status="effective"),
        user={"id": USER, "per": ["admin"]},
        log=_Log(),
    )
    await _drain_refresh_tasks()

    assert status_state["bulk_calls"] == []
    assert status_state["upserts"] == []
    assert any(
        "501 名成员" in record.getMessage() and record.levelname == "INFO"
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_unchanged_status_schedules_no_refresh(status_state):
    with pytest.raises(HTTPException) as failure:
        await activities_v2_router.modify_activity_status_v2(
            ACT_EFFECTIVE,
            activities_v2_router.PutActivityStatus(status="pending"),
            user={"id": USER, "per": ["admin"]},
            log=_Log(),
        )

    assert failure.value.status_code == 400
    assert activities_v2_router._time_refresh_tasks == set()
    assert status_state["bulk_calls"] == []


@pytest.mark.asyncio
async def test_refresh_guard_counts_people_not_rows(status_state):
    """1 个人 3 条模式记录 = 1 人，不是 3 人；去重后只算一次。"""
    status_state["members"] = [
        {"member": MEMBER_A},
        {"member": MEMBER_A},
        {"member": MEMBER_A},
        {"member": MEMBER_B},
    ]

    await activities_v2_router.modify_activity_status_v2(
        ACT_EFFECTIVE,
        activities_v2_router.PutActivityStatus(status="effective"),
        user={"id": USER, "per": ["admin"]},
        log=_Log(),
    )
    await _drain_refresh_tasks()

    assert status_state["bulk_calls"][0]["ids"] == [MEMBER_A, MEMBER_B]
    assert status_state["list_calls"] == [ACT_EFFECTIVE]


@pytest.mark.asyncio
async def test_empty_activity_never_lists_members(status_state):
    """0 人直接返回：连成员行都不取（原先要先全表读一遍才判空）。"""
    status_state["members"] = []

    await activities_v2_router.modify_activity_status_v2(
        ACT_EFFECTIVE,
        activities_v2_router.PutActivityStatus(status="effective"),
        user={"id": USER, "per": ["admin"]},
        log=_Log(),
    )
    await _drain_refresh_tasks()

    assert status_state["list_calls"] == []
    assert status_state["bulk_calls"] == []
