"""成员时长覆盖 ``PUT /v2/activities/{aid}/members/{sid}/record``。

钉住四件事：

1. 归属校验：成员不属于路径上的活动 ⇒ 404（对齐成员状态端点，不泄露归属）；
2. 模式校验：非 hybrid 活动的 ``mode`` 必须等于活动类型，否则 400 且不写库；
3. 时长落库后**立刻**用 ``calculate_user_time(allow_cache=False)`` 重算回填，
   下一次 ``allow_cache=True`` 读到的就是新值；重算失败只 warning、不影响响应；
4. 权限与加/删成员同权（admin/department），普通用户 403 且不产生任何写；
   审计日志类型 ``activity_member_update``。

    cd <repo root> && .venv/bin/python -m pytest tests/test_member_record_update.py -q
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

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


def _activity(oid: str, name: str, *, atype: str = "on-campus") -> dict:
    return {"_id": oid, "name": name, "status": "effective", "type": atype}


def _member(
    session_id: str,
    activity: str,
    *,
    mode: str = "on-campus",
    duration: float = 2.0,
) -> dict:
    return {
        "_id": session_id,
        "activity": activity,
        "member": MEMBER_OID,
        "status": "effective",
        "mode": mode,
        "duration": duration,
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
        if self.state.get("update_error") is not None:
            raise self.state["update_error"]
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
        self.state["cache_writes"].append((user_oid, on_campus, off_campus, social))
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
        "update_error": None,
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
    document_id=SESSION_ID,
    duration=3.5,
    mode="on-campus",
    user=ADMIN,
):
    return await activities_v2_router.update_user_duration_v2(
        activity_id,
        document_id,
        activities_v2_router.UpdateUserRecord(duration=duration, mode=mode),
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
        await _call(state, document_id="0000000000000000000000ff")

    assert failure.value.status_code == 404
    assert failure.value.detail == "Activity member not found"
    assert state["updates"] == []
    assert state["log"].inserts == 0


@pytest.mark.asyncio
async def test_member_of_another_activity_is_404(state):
    """跨活动改时长：路径活动存在、成员也存在，但两者不绑定 ⇒ 404 且不写。"""
    state["rows"].append(_member(OTHER_SESSION_ID, OTHER_ACTIVITY_ID))

    with pytest.raises(HTTPException) as failure:
        await _call(state, document_id=OTHER_SESSION_ID)

    assert failure.value.status_code == 404
    assert failure.value.detail == "Activity member not found"
    assert state["updates"] == []
    assert state["cache_writes"] == []
    assert state["log"].inserts == 0


@pytest.mark.asyncio
async def test_mode_mismatch_with_activity_type_is_400(state):
    """活动是 on-campus，却想把记录改成 off-campus ⇒ 400（对齐加成员端点）。"""
    with pytest.raises(HTTPException) as failure:
        await _call(state, mode="off-campus")

    assert failure.value.status_code == 400
    assert failure.value.detail == "Activity type and member mode do not match"
    assert state["updates"] == []
    assert state["cache_writes"] == []
    assert state["log"].inserts == 0


@pytest.mark.asyncio
async def test_hybrid_activity_accepts_any_mode(state):
    state["activities"][ACTIVITY_ID] = _activity(ACTIVITY_ID, "支教活动", atype="hybrid")

    await _call(state, mode="social-practice", duration=1.0)

    assert state["updates"] == [(SESSION_ID, {"duration": 1.0, "mode": "social-practice"})]


@pytest.mark.asyncio
async def test_duration_update_refreshes_the_cached_time(state):
    # 先有一份「2.0 小时」的缓存，确认读路径确实走缓存。
    state["cache"][MEMBER_OID] = {
        "on_campus_raw": 2.0,
        "off_campus_raw": 0.0,
        "social_practice": 0.0,
    }
    before = await calc.calculate_user_time(MEMBER_OID, allow_cache=True)
    assert before == {"on-campus": 2.0, "off-campus": 0.0, "social-practice": 0.0}

    response = await _call(state, duration=3.5)

    assert response.status_code == 200
    assert state["updates"] == [(SESSION_ID, {"duration": 3.5, "mode": "on-campus"})]
    # 重算走 allow_cache=False：从有效成员重新聚合，结果回填缓存。
    assert state["cache_writes"] == [(MEMBER_OID, 3.5, 0.0, 0.0)]
    after = await calc.calculate_user_time(MEMBER_OID, allow_cache=True)
    assert after == {"on-campus": 3.5, "off-campus": 0.0, "social-practice": 0.0}

    assert state["log"].types == ["activity_member_update"]
    assert state["log"].inserts == 1
    assert state["log"].texts == [
        "用户 管理员 修改了义工活动《支教活动》中成员 张三 的记录（时长 3.5，模式 on-campus）"
    ]


@pytest.mark.asyncio
async def test_cache_refresh_failure_keeps_the_response(state, monkeypatch):
    """重算失败只 warning：时长已经落库，响应仍是成功。"""

    async def boom(*args, **kwargs):
        raise RuntimeError("cache backend down")

    monkeypatch.setattr(activities_v2_router, "calculate_user_time", boom)

    response = await _call(state, duration=4.0)

    assert response.status_code == 200
    assert state["updates"] == [(SESSION_ID, {"duration": 4.0, "mode": "on-campus"})]
    assert state["cache_writes"] == []
    assert state["log"].inserts == 1


class _OracleError(Exception):
    """oracledb 的 ``DatabaseError`` 形状：错误码挂在 ``.code`` 上。"""

    def __init__(self, code: int) -> None:
        self.code = code
        super().__init__(f"ORA-{code:05d}")


def _integrity_error(code: int) -> IntegrityError:
    return IntegrityError("UPDATE activity_members ...", {}, _OracleError(code))


@pytest.mark.asyncio
async def test_mode_change_colliding_with_the_unique_key_is_409(state):
    """改 mode 撞 uq_activity_members_dedup（ORA-00001）⇒ 409，不是 500。

    冲突路径上不重算、不写审计：库里什么都没变，缓存也就没理由动。
    """
    state["activities"][ACTIVITY_ID] = _activity(ACTIVITY_ID, "支教活动", atype="hybrid")
    state["update_error"] = _integrity_error(1)

    with pytest.raises(HTTPException) as failure:
        await _call(state, mode="off-campus")

    assert failure.value.status_code == 409
    assert failure.value.detail == (
        "该成员在此活动下已有相同计数模式的记录，请先删除或合并"
    )
    assert state["updates"] == []
    assert state["cache_writes"] == []
    assert state["log"].inserts == 0


@pytest.mark.asyncio
async def test_other_integrity_errors_still_bubble(state):
    """ORA-02291（FK）之类的完整性错误不能被这段 409 文案吞掉。"""
    state["update_error"] = _integrity_error(2291)

    with pytest.raises(IntegrityError):
        await _call(state, duration=1.0)

    assert state["cache_writes"] == []
    assert state["log"].inserts == 0
