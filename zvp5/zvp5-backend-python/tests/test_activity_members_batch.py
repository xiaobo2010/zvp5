"""``activities_v3`` 成员接口的两处批量化契约（离线：假 repo + 假 session）。

GET ``/{aid}/members``：

1. 一页成员行只批量取一次用户（``get_many_by_oids`` 调用 1 次，``get_by_oid`` 0 次）；
2. 每行 ``user`` 是 v1 ``read_user`` 的 ``user_info`` 同形摘要，且**绝不含 password**；
3. 用户查不到那行 ``user=None``；既有字段（``member_name`` 等）逐字段原样。

POST ``/{aid}/members``：

4. 目标用户一次批量查、既有三元组一次批量查 ⇒ 单成员不再各查一次；
5. 既有三元组走一次 ``update_durations_many``、新三元组走一次 ``add_many``
   （N 个成员 2 条 executemany，而不是 2N 条单行语句）；
6. 用户不存在 / 类型不匹配的错误分支与文案不变，且不发生写。

7. 默认（不带 ``overwrite``）仍是时长累加，行为与改造前一致；
8. ``overwrite=true`` 时既有三元组的 ``duration`` 直接设为本次值，不当累加；
9. ``overwrite=true`` 且同一请求内同一 ``(member, mode)`` 出现多行 ⇒ 取最后一行。

    cd <repo root> && .venv/bin/python -m pytest tests/test_activity_members_batch.py -q
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from db import backends
from routers import activities_router as activities
from util.object_id import get_current_user

PREFIX = "/api/v1/activities"
ACTIVITY_ID = "000000000000000000000010"
MEMBER_OIDS = [f"{index:024x}" for index in range(10, 20)]
GHOST_OID = "0000000000000000000000aa"
MANAGER = {"id": MEMBER_OIDS[0], "per": ["admin"]}

USER_KEYS = {
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


def _user_doc(oid: str, **overrides) -> dict:
    doc = {
        "_id": oid,
        "id": f"2026{oid[-3:]}",
        "name": f"成员{oid[-2:]}",
        "sex": "M",
        "roles": ["student"],
        "class_id": "0000000000000000000000ff",
        "past": ["旧班级"],
        "banned": 0,
        "eligibility": ["volunteer"],
        "password": "should-never-leak",
        "session_id": "v1",
    }
    doc.update(overrides)
    return doc


def _member_row(oid: str) -> dict:
    return {
        "_id": f"row-{oid}",
        "activity": ACTIVITY_ID,
        "member": oid,
        "member_name": f"成员{oid[-2:]}",
        "mode": "on-campus",
        "status": "effective",
        "duration": 1.5,
    }


class _MembersRepo:
    def __init__(self, rows, session=None) -> None:
        self.rows, self.session = rows, session

    async def list_for_activity(self, activity_oid, query, page, size):
        return [dict(row) for row in self.rows], len(self.rows)


class _UsersRepo:
    def __init__(self, users, session=None) -> None:
        self.users, self.session = users, session
        self.batch_calls: list[list[str]] = []
        self.single_calls: list[str] = []

    async def get_many_by_oids(self, oids):
        self.batch_calls.append(list(oids))
        return {
            oid: self.users[oid]
            for oid in dict.fromkeys(oids)
            if oid in self.users
        }

    async def get_by_oid(self, oid):
        self.single_calls.append(oid)
        return self.users.get(oid)


@pytest.fixture()
def embed(monkeypatch):
    """GET 成员页：假 session + 记录调用的假 users/members repo。"""
    session = object()
    members = _MembersRepo([_member_row(oid) for oid in MEMBER_OIDS])
    users = _UsersRepo({oid: _user_doc(oid) for oid in MEMBER_OIDS})

    @asynccontextmanager
    async def scope():
        yield session

    monkeypatch.setattr(activities, "session_scope", scope)
    monkeypatch.setattr(
        backends, "get_members_repo", lambda s=None: _bind(members, s)
    )
    monkeypatch.setattr(backends, "get_users_repo", lambda s=None: _bind(users, s))

    app = FastAPI()
    app.include_router(activities.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: MANAGER
    return TestClient(app), users, members, session


def _bind(repo, session):
    repo.session = session
    return repo


def test_member_page_is_manager_only() -> None:
    app = FastAPI()
    app.include_router(activities.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": MEMBER_OIDS[0],
        "per": ["student"],
    }

    response = TestClient(app).get(f"{PREFIX}/{ACTIVITY_ID}/members")

    assert response.status_code == 403
    assert response.json()["detail"] == "权限不足"


def test_member_page_reads_the_users_once_and_embeds_them(embed) -> None:
    client, users, members, session = embed

    response = client.get(f"{PREFIX}/{ACTIVITY_ID}/members")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 10
    # 一次批量、零逐条：这正是前端 10 张卡不再回打 /api/users/{id} 的依据。
    assert users.batch_calls == [MEMBER_OIDS]
    assert users.single_calls == []
    # 同一个 session_scope 交给两个 repo（一次 checkout）。
    assert users.session is session and members.session is session


def test_embedded_user_shape_matches_v1_read_user_and_keeps_old_fields(embed) -> None:
    client, _users, _members, _session = embed

    members = client.get(f"{PREFIX}/{ACTIVITY_ID}/members").json()["members"]

    for row, oid in zip(members, MEMBER_OIDS):
        assert set(row) == {
            "_id",
            "activity",
            "member",
            "member_name",
            "mode",
            "status",
            "duration",
            "user",
        }
        # 既有字段原样（契约只增不改）
        assert row["_id"] == f"row-{oid}"
        assert row["activity"] == ACTIVITY_ID
        assert row["member"] == oid
        assert row["member_name"] == f"成员{oid[-2:]}"
        assert row["mode"] == "on-campus"
        assert row["status"] == "effective"
        assert row["duration"] == 1.5
        # 内嵌摘要与 v1 read_user 的 user_info 同形，密码/会话不进响应
        assert set(row["user"]) == USER_KEYS
        assert row["user"] == {
            "_id": oid,
            "id": f"2026{oid[-3:]}",
            "name": f"成员{oid[-2:]}",
            "sex": "M",
            "roles": ["student"],
            "class_id": "0000000000000000000000ff",
            "group": ["0000000000000000000000ff"],
            "past": ["旧班级"],
            "banned": False,
        }


def test_missing_user_row_embeds_null(monkeypatch) -> None:
    session = object()
    members = _MembersRepo([_member_row(GHOST_OID)])
    users = _UsersRepo({})  # 查不到用户（历史悬空 member）

    @asynccontextmanager
    async def scope():
        yield session

    monkeypatch.setattr(activities, "session_scope", scope)
    monkeypatch.setattr(backends, "get_members_repo", lambda s=None: members)
    monkeypatch.setattr(backends, "get_users_repo", lambda s=None: users)

    app = FastAPI()
    app.include_router(activities.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: MANAGER

    row = TestClient(app).get(f"{PREFIX}/{ACTIVITY_ID}/members").json()["members"][0]

    assert row["user"] is None
    assert row["member"] == GHOST_OID
    assert row["member_name"] == f"成员{GHOST_OID[-2:]}"


class _Session:
    """假 session：只提供 handler 用到的 async context manager。"""

    @asynccontextmanager
    async def begin(self):
        yield self


class _BulkMembersRepo:
    def __init__(self, existing: dict[tuple[str, str], float]) -> None:
        self.existing = dict(existing)
        self.bulk_calls: list[tuple[str, tuple[str, ...]]] = []
        self.triple_calls: list[tuple[str, str]] = []
        # 逐行写入的内容（语义断言）与批次形状（往返次数断言）分开记。
        self.writes: list[tuple[str, str, str, float]] = []
        self.write_calls: list[tuple[str, int]] = []

    async def list_by_activity_and_members(self, activity_oid, member_oids):
        self.bulk_calls.append((activity_oid, tuple(member_oids)))
        return [
            {
                "_id": f"{oid}:{mode}",
                "activity": activity_oid,
                "member": oid,
                "mode": mode,
                "duration": self.existing[(oid, mode)],
            }
            for oid in dict.fromkeys(member_oids)
            for mode in ("on-campus", "off-campus", "social-practice")
            if (oid, mode) in self.existing
        ]

    async def get_by_triple(self, activity_oid, oid, mode):
        self.triple_calls.append((oid, mode))
        raise AssertionError("批量导入不应逐条 get_by_triple")

    async def update_durations_many(self, entries, *, delta=True):
        self.write_calls.append(("update", len(entries)))
        for key, hours in entries:
            member, mode = key.split(":")
            base = self.existing.get((member, mode), 0.0)
            value = base + hours if delta else hours
            self.existing[(member, mode)] = value
            self.writes.append(("update", key, "", value))

    async def add_many(self, activity_oid, entries, status):
        self.write_calls.append(("add", len(entries)))
        for oid, mode, duration in entries:
            self.existing[(oid, mode)] = duration
            self.writes.append(("add", oid, mode, duration))

    async def update_fields(self, oid, fields):
        raise AssertionError("批量导入不应逐行 update_fields")

    async def add(self, activity_oid, oid, mode, duration, status):
        raise AssertionError("批量导入不应逐行 add")


class _BulkUsersRepo:
    def __init__(self, users: dict[str, dict]) -> None:
        self.users = dict(users)
        self.batch_calls: list[list[str]] = []
        self.single_calls: list[str] = []

    async def get_many_by_oids(self, oids):
        self.batch_calls.append(list(oids))
        return {
            oid: self.users[oid] for oid in dict.fromkeys(oids) if oid in self.users
        }

    async def get_names(self, oids):
        # 日志文案用批量姓名查询，避免把 oid 直接写进审计详情。
        return {
            oid: self.users[oid]["name"]
            for oid in dict.fromkeys(oids)
            if oid in self.users and self.users[oid].get("name")
        }

    async def get_by_oid(self, oid):
        self.single_calls.append(oid)
        return self.users.get(oid)


class _ActivitiesRepo:
    def __init__(self, atype: str = "hybrid", status: str = "effective") -> None:
        self.atype, self.status, self.updates = atype, status, []

    async def get_for_update(self, activity_oid):
        return {
            "_id": activity_oid,
            "type": self.atype,
            "status": self.status,
            "name": "支教活动",
        }

    async def update(self, activity_oid, fields):
        self.updates.append(fields)


class _Log:
    def __init__(self) -> None:
        self.inserts = 0
        self.texts: list[str] = []

    def with_text(self, text, *args, **kwargs):
        self.texts.append(text)
        return self

    async def insert_log(self, session=None):
        self.inserts += 1


def _bulk(monkeypatch, *, existing, users, atype="hybrid", status="effective"):
    state = {
        "session": _Session(),
        "members": _BulkMembersRepo(existing),
        "users": _BulkUsersRepo(users),
        "activities": _ActivitiesRepo(atype, status),
        "log": _Log(),
        "scheduled": [],
    }

    @asynccontextmanager
    async def scope():
        yield state["session"]

    monkeypatch.setattr(activities, "session_scope", scope)
    monkeypatch.setattr(backends, "get_members_repo", lambda s=None: state["members"])
    monkeypatch.setattr(backends, "get_users_repo", lambda s=None: state["users"])
    monkeypatch.setattr(
        backends, "get_activities_repo", lambda s=None: state["activities"]
    )
    # v3 在提交后调 v2 的后台重算（延迟 import，所以补丁打在 v2 模块上有效）。
    # 这里只记录调度，不让 fire-and-forget 任务真的去读假仓库。
    monkeypatch.setattr(
        activities, "_schedule_user_time_refresh", state["scheduled"].append
    )
    return state


def _new_member(oid=MEMBER_OIDS[0], mode="on-campus", duration=1.5):
    return activities.NewActivityMember(member=oid, mode=mode, duration=duration)


@pytest.mark.asyncio
async def test_bulk_import_batches_reads_and_upserts_in_memory(monkeypatch) -> None:
    existing = {(MEMBER_OIDS[0], "on-campus"): 3.0}
    state = _bulk(
        monkeypatch,
        existing=existing,
        users={oid: _user_doc(oid) for oid in MEMBER_OIDS[:2]},
    )

    await activities.add_activity_member_v3(
        ACTIVITY_ID,
        [
            _new_member(MEMBER_OIDS[0], duration=2.0),  # 已有三元组 → update
            _new_member(MEMBER_OIDS[1], mode="off-campus", duration=1.0),  # 新增
        ],
        MANAGER,
        state["log"],
    )

    # 用户一次批量、零逐条；既有三元组一次批量、零逐条
    assert state["users"].batch_calls == [MEMBER_OIDS[:2]]
    assert state["users"].single_calls == []
    assert state["members"].bulk_calls == [(ACTIVITY_ID, tuple(MEMBER_OIDS[:2]))]
    assert state["members"].triple_calls == []
    # 既有：3.0 + 2.0；新增：原样
    assert state["members"].writes == [
        ("update", f"{MEMBER_OIDS[0]}:on-campus", "", 5.0),
        ("add", MEMBER_OIDS[1], "off-campus", 1.0),
    ]
    # 一条 update executemany + 一条 insert executemany。
    assert state["members"].write_calls == [("update", 1), ("add", 1)]
    assert len(state["activities"].updates) == 1
    assert state["activities"].updates[0]["status"] == "pending"
    assert "updatedAt" in state["activities"].updates[0]
    assert state["log"].inserts == 1
    # 提交后按活动排一次后台重算：缓存里的 user_time 当场失效，不等 01:00。
    assert state["scheduled"] == [ACTIVITY_ID]
    # 审计详情必须是人名 + 活动名，不能把 oid 直接写进去。
    assert state["log"].texts == [
        "用户 成员0a 为义工活动《支教活动》增加时长：成员0a on-campus +2.0；成员0b off-campus +1.0"
    ]
    assert MEMBER_OIDS[0] not in state["log"].texts[0]
    assert MEMBER_OIDS[1] not in state["log"].texts[0]


@pytest.mark.asyncio
async def test_bulk_import_keeps_the_missing_member_error(monkeypatch) -> None:
    state = _bulk(monkeypatch, existing={}, users={})  # 用户库为空

    with pytest.raises(HTTPException) as failure:
        await activities.add_activity_member_v3(
            ACTIVITY_ID, [_new_member()], MANAGER, state["log"]
        )

    assert failure.value.status_code == 400
    assert failure.value.detail == "成员不存在"
    assert state["members"].writes == []  # 未发生写
    assert state["activities"].updates == []
    assert state["log"].inserts == 0


@pytest.mark.asyncio
async def test_bulk_import_rejects_refused_activity(monkeypatch) -> None:
    state = _bulk(
        monkeypatch,
        existing={},
        users={MEMBER_OIDS[0]: _user_doc(MEMBER_OIDS[0])},
        status="refused",
    )

    with pytest.raises(HTTPException) as failure:
        await activities.add_activity_member_v3(
            ACTIVITY_ID, [_new_member()], MANAGER, state["log"]
        )

    assert failure.value.status_code == 400
    assert failure.value.detail == "当前活动状态不允许登记成员"
    assert state["members"].writes == []
    assert state["activities"].updates == []
    assert state["log"].inserts == 0


@pytest.mark.asyncio
async def test_bulk_import_keeps_the_type_mismatch_error(monkeypatch) -> None:
    state = _bulk(
        monkeypatch,
        existing={},
        users={MEMBER_OIDS[0]: _user_doc(MEMBER_OIDS[0])},
        atype="on-campus",
    )

    with pytest.raises(HTTPException) as failure:
        await activities.add_activity_member_v3(
            ACTIVITY_ID, [_new_member(mode="off-campus")], MANAGER, state["log"]
        )

    assert failure.value.status_code == 400
    assert failure.value.detail == "义工类型不匹配"
    assert state["members"].writes == []
    assert state["log"].inserts == 0


# 大小写混合的 oid：``validate_object_id``（util.oid）经 ``db.ids.normalize_oid``
# （db/ids.py:70-80）转小写，handler 在 members.py 归一化边界之前已把 key 折成小写。
MIXED_OID = "abcdefabcdefabcdefabcdef"


@pytest.mark.asyncio
async def test_bulk_import_normalizes_mixed_case_oids_without_duplicate_rows(
    monkeypatch,
) -> None:
    """同一成员以不同大小写出现 ⇒ 归一化后合并成一个三元组、只写一次。

    归一化发生在 handler ``routers/activities_router.py:876``
    （``str(validate_object_id(member.member))``，util.oid 走 ``normalize_oid`` 转小写），
    先于 ``additions`` 聚合（:877）。因此小写/大写两条 payload 落到同一 key，
    ``duration`` 相加而非各插一行。
    """
    state = _bulk(
        monkeypatch,
        existing={},
        users={MIXED_OID: _user_doc(MIXED_OID)},
    )

    await activities.add_activity_member_v3(
        ACTIVITY_ID,
        [
            _new_member(MIXED_OID, duration=1.0),
            _new_member(MIXED_OID.upper(), duration=2.0),  # 同一成员，大写
        ],
        MANAGER,
        state["log"],
    )

    # 归一化 → 去重：批量用户查询、既有三元组查询都只带一个小写 oid。
    assert state["users"].batch_calls == [[MIXED_OID]]
    assert state["members"].bulk_calls == [(ACTIVITY_ID, (MIXED_OID,))]
    # 只写一行，时长相加（1.0 + 2.0），不是两行。
    assert state["members"].writes == [("add", MIXED_OID, "on-campus", 3.0)]
    assert state["log"].inserts == 1


@pytest.mark.asyncio
async def test_bulk_import_reports_the_first_error_in_payload_order(monkeypatch) -> None:
    """混合 payload：payload 顺序中第一个出错成员的错误先抛。

    逐成员判定顺序从 ``routers/activities_router.py:898-902`` 推出：
    ``for oid, mode in additions`` 按 payload 插入顺序迭代（:876-880），
    成员内先查类型（:899-900「义工类型不匹配」）后查存在（:901-902「成员不存在」）。
    """
    # (a) 未知用户在前、类型不匹配在后 ⇒ 未知用户先被判定 → 「成员不存在」。
    state = _bulk(
        monkeypatch,
        existing={},
        users={MEMBER_OIDS[1]: _user_doc(MEMBER_OIDS[1])},
        atype="on-campus",
    )

    with pytest.raises(HTTPException) as first:
        await activities.add_activity_member_v3(
            ACTIVITY_ID,
            [
                _new_member(GHOST_OID, mode="on-campus"),  # 用户不存在（类型合法）
                _new_member(MEMBER_OIDS[1], mode="off-campus"),  # 类型不匹配（用户存在）
            ],
            MANAGER,
            state["log"],
        )

    assert first.value.status_code == 400
    assert first.value.detail == "成员不存在"
    assert state["members"].writes == []  # 未发生任何写
    assert state["activities"].updates == []
    assert state["log"].inserts == 0

    # (b) 顺序对调 ⇒ 类型不匹配先被判定 → 「义工类型不匹配」。
    flipped = _bulk(
        monkeypatch,
        existing={},
        users={MEMBER_OIDS[1]: _user_doc(MEMBER_OIDS[1])},
        atype="on-campus",
    )

    with pytest.raises(HTTPException) as second:
        await activities.add_activity_member_v3(
            ACTIVITY_ID,
            [
                _new_member(MEMBER_OIDS[1], mode="off-campus"),  # 类型不匹配
                _new_member(GHOST_OID, mode="on-campus"),  # 用户不存在
            ],
            MANAGER,
            flipped["log"],
        )

    assert second.value.status_code == 400
    assert second.value.detail == "义工类型不匹配"
    assert flipped["members"].writes == []
    assert flipped["activities"].updates == []
    assert flipped["log"].inserts == 0


@pytest.mark.asyncio
async def test_bulk_import_without_overwrite_still_accumulates(monkeypatch) -> None:
    """默认（``overwrite`` 缺省）语义不变：既有三元组继续累加。

    路由签名 ``routers/activities_router.py`` 里 ``overwrite`` 是
    ``Query(False)``，因此老调用方（不传该参数）拿到的是原行为。
    """
    existing = {(MEMBER_OIDS[0], "on-campus"): 3.0}
    state = _bulk(
        monkeypatch,
        existing=existing,
        users={MEMBER_OIDS[0]: _user_doc(MEMBER_OIDS[0])},
    )

    await activities.add_activity_member_v3(
        ACTIVITY_ID,
        [_new_member(MEMBER_OIDS[0], duration=2.0)],
        MANAGER,
        state["log"],
        False,
    )

    assert state["members"].writes == [
        ("update", f"{MEMBER_OIDS[0]}:on-campus", "", 5.0)
    ]
    # 审计详情沿用「+时长」写法。
    assert state["log"].texts == [
        "用户 成员0a 为义工活动《支教活动》增加时长：成员0a on-campus +2.0"
    ]


@pytest.mark.asyncio
async def test_bulk_import_overwrite_sets_duration_instead_of_accumulating(
    monkeypatch,
) -> None:
    """``overwrite=True``：既有记录的 ``duration`` 直接设为本次值（3.0 → 2.0）。"""
    existing = {(MEMBER_OIDS[0], "on-campus"): 3.0}
    state = _bulk(
        monkeypatch,
        existing=existing,
        users={MEMBER_OIDS[0]: _user_doc(MEMBER_OIDS[0])},
    )

    await activities.add_activity_member_v3(
        ACTIVITY_ID,
        [_new_member(MEMBER_OIDS[0], duration=2.0)],
        MANAGER,
        state["log"],
        True,
    )

    assert state["members"].writes == [
        ("update", f"{MEMBER_OIDS[0]}:on-campus", "", 2.0)
    ]
    assert len(state["activities"].updates) == 1
    assert state["log"].inserts == 1
    # 设置语义的详情不带「+」，避免审计记录被读成累加。
    assert state["log"].texts == [
        "用户 成员0a 为义工活动《支教活动》设置时长：成员0a on-campus 2.0"
    ]


@pytest.mark.asyncio
async def test_bulk_import_reject_existing_blocks_the_whole_batch(monkeypatch) -> None:
    """``reject_existing=True``：命中既有三元组就整批 409，一行都不写。

    登记表改成「只新增」后，前端靠这个参数兜住「没搜索就直接提交」的情况，
    否则 v3 的累加语义会把同一个人同一模式的时长悄悄翻倍。
    """
    existing = {(MEMBER_OIDS[0], "on-campus"): 3.0}
    state = _bulk(
        monkeypatch,
        existing=existing,
        users={
            MEMBER_OIDS[0]: _user_doc(MEMBER_OIDS[0]),
            MEMBER_OIDS[1]: _user_doc(MEMBER_OIDS[1]),
        },
    )

    with pytest.raises(HTTPException) as exc:
        await activities.add_activity_member_v3(
            ACTIVITY_ID,
            [
                _new_member(MEMBER_OIDS[0], duration=2.0),
                _new_member(MEMBER_OIDS[1], duration=1.0),
            ],
            MANAGER,
            state["log"],
            False,
            True,
        )

    assert exc.value.status_code == 409
    assert state["members"].writes == []
    assert state["activities"].updates == []
    assert state["log"].inserts == 0


@pytest.mark.asyncio
async def test_bulk_import_reject_existing_allows_new_members(monkeypatch) -> None:
    """没命中既有记录时 ``reject_existing`` 不拦新增。"""
    state = _bulk(
        monkeypatch,
        existing={},
        users={MEMBER_OIDS[0]: _user_doc(MEMBER_OIDS[0])},
    )

    await activities.add_activity_member_v3(
        ACTIVITY_ID,
        [_new_member(MEMBER_OIDS[0], duration=2.0)],
        MANAGER,
        state["log"],
        False,
        True,
    )

    assert state["members"].writes == [("add", MEMBER_OIDS[0], "on-campus", 2.0)]
    assert len(state["activities"].updates) == 1


@pytest.mark.asyncio
async def test_bulk_import_writes_a_whole_batch_in_two_calls(monkeypatch) -> None:
    """N 个既有 + N 个新增 ⇒ 2 条 executemany，而不是 2N 条单行写。"""
    oids = [f"{index:024x}" for index in range(10, 16)]
    existing = {(oid, "on-campus"): 1.0 for oid in oids[:3]}
    state = _bulk(
        monkeypatch,
        existing=existing,
        users={oid: _user_doc(oid) for oid in oids},
    )

    await activities.add_activity_member_v3(
        ACTIVITY_ID,
        [_new_member(oid, duration=1.0) for oid in oids],
        MANAGER,
        state["log"],
    )

    assert state["members"].write_calls == [("update", 3), ("add", 3)]
    # 既有 3 条各自 +1.0，新增 3 条原样落库。
    assert state["members"].existing == {
        **{key: 2.0 for key in existing},
        **{(oid, "on-campus"): 1.0 for oid in oids[3:]},
    }
    assert len(state["log"].texts) == 1


@pytest.mark.asyncio
async def test_bulk_import_overwrite_takes_the_last_duplicate_row(
    monkeypatch,
) -> None:
    """同请求内重复 ``(member, mode)`` ⇒ overwrite 下取最后一行，只写一次。"""
    existing = {(MEMBER_OIDS[0], "on-campus"): 3.0}
    state = _bulk(
        monkeypatch,
        existing=existing,
        users={MEMBER_OIDS[0]: _user_doc(MEMBER_OIDS[0])},
    )

    await activities.add_activity_member_v3(
        ACTIVITY_ID,
        [
            _new_member(MEMBER_OIDS[0], duration=1.0),
            _new_member(MEMBER_OIDS[0], duration=2.5),  # 最后一行生效
        ],
        MANAGER,
        state["log"],
        True,
    )

    # 一次批量查询、一次写入：最后一行 2.5 既不是 1.0 也不是 3.0+1.0+2.5。
    assert state["users"].batch_calls == [[MEMBER_OIDS[0]]]
    assert state["members"].bulk_calls == [(ACTIVITY_ID, (MEMBER_OIDS[0],))]
    assert state["members"].writes == [
        ("update", f"{MEMBER_OIDS[0]}:on-campus", "", 2.5)
    ]
    assert state["log"].texts == [
        "用户 成员0a 为义工活动《支教活动》设置时长：成员0a on-campus 2.5"
    ]
