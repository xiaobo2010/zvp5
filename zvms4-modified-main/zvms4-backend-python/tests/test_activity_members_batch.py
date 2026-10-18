"""``activities_v3`` 成员接口的两处批量化契约（离线：假 repo + 假 session）。

GET ``/{aid}/members``：

1. 一页成员行只批量取一次用户（``get_many_by_oids`` 调用 1 次，``get_by_oid`` 0 次）；
2. 每行 ``user`` 是 v1 ``read_user`` 的 ``user_info`` 同形摘要，且**绝不含 password**；
3. 用户查不到那行 ``user=None``；既有字段（``member_name`` 等）逐字段原样。

POST ``/{aid}/members``：

4. 目标用户一次批量查、既有三元组一次批量查 ⇒ 单成员不再各查一次；
5. 既有三元组走 ``update_fields``，新三元组走 ``add``；
6. 用户不存在 / 类型不匹配的错误分支与文案不变，且不发生写。

    cd <repo root> && .venv/bin/python -m pytest tests/test_activity_members_batch.py -q
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from db import backends
from routers import activities_v3_router as activities
from util.object_id import get_current_user

PREFIX = "/api/v3/activities"
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
        self.writes: list[tuple[str, str, str, float]] = []

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

    async def update_fields(self, oid, fields):
        self.writes.append(("update", oid, "", fields["duration"]))

    async def add(self, activity_oid, oid, mode, duration, status):
        self.writes.append(("add", oid, mode, duration))


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

    async def get_by_oid(self, oid):
        self.single_calls.append(oid)
        return self.users.get(oid)


class _ActivitiesRepo:
    def __init__(self, atype: str = "hybrid") -> None:
        self.atype, self.updates = atype, []

    async def get_for_update(self, activity_oid):
        return {"_id": activity_oid, "type": self.atype}

    async def update(self, activity_oid, fields):
        self.updates.append(fields)


class _Log:
    def __init__(self) -> None:
        self.inserts = 0

    def with_text(self, *args, **kwargs):
        return self

    async def insert_log(self, session=None):
        self.inserts += 1


def _bulk(monkeypatch, *, existing, users, atype="hybrid"):
    state = {
        "session": _Session(),
        "members": _BulkMembersRepo(existing),
        "users": _BulkUsersRepo(users),
        "activities": _ActivitiesRepo(atype),
        "log": _Log(),
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
    assert len(state["activities"].updates) == 1
    assert state["activities"].updates[0]["status"] == "pending"
    assert "updatedAt" in state["activities"].updates[0]
    assert state["log"].inserts == 1


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


# 大小写混合的 oid：``validate_object_id``（utils.py:6-10）经 ``normalize_oid``
# （db/ids.py:70-80）转小写，handler 在 members.py 归一化边界之前已把 key 折成小写。
MIXED_OID = "abcdefabcdefabcdefabcdef"


@pytest.mark.asyncio
async def test_bulk_import_normalizes_mixed_case_oids_without_duplicate_rows(
    monkeypatch,
) -> None:
    """同一成员以不同大小写出现 ⇒ 归一化后合并成一个三元组、只写一次。

    归一化发生在 handler ``routers/activities_v3_router.py:182``
    （``str(validate_object_id(member.member))``，utils.py 走 ``normalize_oid`` 转小写），
    先于 ``additions`` 聚合（:183）。因此小写/大写两条 payload 落到同一 key，
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

    逐成员判定顺序从 ``routers/activities_v3_router.py:197-201`` 推出：
    ``for oid, mode in additions`` 按 payload 插入顺序迭代（:180-183），
    成员内先查类型（:198-199「义工类型不匹配」）后查存在（:200-201「成员不存在」）。
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
