"""内嵌成员名单契约（离线：假 repo + 计数 spy，无数据库/网络）。

两个统计端点各新增一个**只增不改**的顶层键，且**零额外查询**：

* ``GET /v2/groups/{id}/statistics/compliance`` → ``member_rosters``，按模式、
  按桶给出成员名单。桶键必须与同响应顶层的桶键逐字一致（含 ``"Completed"``），
  同一模式内各桶名单互斥且并集 = 该模式候选集合；条目只含
  ``{_id, id, name, duration}``，绝不外泄 password / session_id。
三个新键/不变性质：新键**无条件**出现（形状不依赖数据内容），桶键与顶层逐字一致，
同模式内各桶名单互斥且并集 = 该模式候选集合，条目只含
``{_id, id, name, duration}``，绝不外泄 password / session_id。

本模块钉死三件事：

1. **零额外查询**：加字段前后 repo 调用次数一致（``list_by_class`` 1 次、
   ``list_by_users`` 1 次、``list_by_activity`` 1 次、``get_many_by_oids`` 1 次、
   ``list_all`` 1 次；逐条 ``get_by_oid`` 0 次；不触碰 activities / members 表）。
2. **形状与互斥**：桶键集合 == 顶层键集合；同模式内并集 == 候选集合；
   条目键集合 == ``{_id, id, name, duration}``。
3. **既有契约不变**：分析端点去掉新键后仍是原来的六个键；空活动仍走
   ``empty_analysis_payload()``（不含 ``mode_members``，那是独立的无数据形状）。

    cd <repo root> && .venv/bin/python -m pytest tests/test_statistics_roster_embed.py -q
"""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import activity_statistics_router, groups_v2_router
from util.object_id import get_current_user

# ── A：/v2/groups/{id}/statistics/compliance ─────────────────────────────────

GROUPS_PREFIX = "/api/v2/groups"
CLASS_OID = "64b7f0c2e1a3b4c5d6e7f801"
SECRETARY_OID = "64b7f0c2e1a3b4c5d6e7f805"
MEMBER_OIDS = [
    "64b7f0c2e1a3b4c5d6e7f811",
    "64b7f0c2e1a3b4c5d6e7f812",
    "64b7f0c2e1a3b4c5d6e7f813",
]
# 每人的三种模式时长，用于把三人铺进不同桶（单位：小时）。
# BASE_ON_CAMPUS=25 / BASE_OFF_CAMPUS=15 / BASE_SOCIAL_PRACTICE=18。
TIMES = {
    MEMBER_OIDS[0]: {"on-campus": 25.0, "off-campus": 0.0, "social-practice": 0.0},
    MEMBER_OIDS[1]: {"on-campus": 5.0, "off-campus": 15.0, "social-practice": 9.0},
    MEMBER_OIDS[2]: {"on-campus": 2.5, "off-campus": 3.0, "social-practice": 18.0},
}

EN_DASH = "\u2013"
# 与 handler 里 convert_key_name 的产物逐字同形（含 en dash 与 Completed）。
BUCKETS = {
    "on-campus": [f"0%{EN_DASH}20%", f"20%{EN_DASH}40%", "Completed"],
    "off-campus": [f"0%{EN_DASH}20%", f"20%{EN_DASH}40%", "Completed"],
    "social-practice": [f"0%{EN_DASH}20%", f"40%{EN_DASH}60%", "Completed"],
}
ENTRY_KEYS = {"_id", "id", "name", "duration"}


class _FakeUsersRepo:
    """``list_by_class`` 返回带身份字段的用户行，并记录每一次调用。

    ``password`` / ``session_id`` 故意放进行里：名单投影若漏了白名单，
    下面的断言会立刻把它们抓出来。
    """

    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.class_calls: list[str] = []
        self.by_oid_calls: list[str] = []

    async def list_by_class(self, class_oid: str):
        self.class_calls.append(class_oid)
        return [dict(row) for row in self.rows]

    async def get_by_oid(self, oid: str):
        self.by_oid_calls.append(oid)
        raise AssertionError("名单必须走 list_by_class，不得逐条 get_by_oid")


class _FakeTimeRepo:
    """全员缓存命中：``list_by_users`` 一次给出三人的时长。"""

    def __init__(self) -> None:
        self.ids_calls: list[list[str]] = []

    async def list_by_users(self, user_oids, *args, **kwargs):
        self.ids_calls.append(list(user_oids))
        return [
            {
                "user": oid,
                "on_campus_raw": TIMES[oid]["on-campus"],
                "off_campus_raw": TIMES[oid]["off-campus"],
                "social_practice": TIMES[oid]["social-practice"],
            }
            for oid in user_oids
        ]


class _ExplodingRepo:
    """若被调用即失败：用来证明「零额外查询」。"""

    def __init__(self, name: str) -> None:
        self.name = name

    async def list_effective(self, *args, **kwargs):
        raise AssertionError(f"本次请求不应读 {self.name}")

    async def list_by_members(self, *args, **kwargs):
        raise AssertionError(f"本次请求不应读 {self.name}")

    async def get_permission_group(self, *args, **kwargs):
        raise AssertionError(f"本次请求不应读 {self.name}")

    async def get_by_oid(self, *args, **kwargs):
        raise AssertionError(f"本次请求不应读 {self.name}")


def _group_row(oid: str, index: int) -> dict:
    return {
        "_id": oid,
        "id": f"2023000{index}",
        "name": f"成员{index}",
        "password": "should-never-leak",
        "session_id": "session-should-never-leak",
        "class_id": CLASS_OID,
    }


def _compliance_client(monkeypatch, rows: list[dict]):
    users = _FakeUsersRepo(rows)
    times = _FakeTimeRepo()

    monkeypatch.setattr(backends, "get_users_repo", lambda s=None: users)
    monkeypatch.setattr(backends, "get_time_repo", lambda s=None: times)
    monkeypatch.setattr(backends, "get_activities_repo", lambda s=None: _ExplodingRepo("activities"))
    monkeypatch.setattr(backends, "get_members_repo", lambda s=None: _ExplodingRepo("members"))
    monkeypatch.setattr(backends, "get_groups_repo", lambda s=None: _ExplodingRepo("groups"))

    app = FastAPI()
    app.include_router(groups_v2_router.router, prefix=GROUPS_PREFIX)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": SECRETARY_OID,
        "per": ["secretary"],
        "class_id": CLASS_OID,
    }
    return TestClient(app), users, times


def test_compliance_member_rosters_add_no_reads_and_keep_the_bucket_keys(
    monkeypatch,
) -> None:
    rows = [_group_row(oid, index) for index, oid in enumerate(MEMBER_OIDS)]
    client, users, times = _compliance_client(monkeypatch, rows)

    response = client.get(f"{GROUPS_PREFIX}/{CLASS_OID}/statistics/compliance")

    assert response.status_code == 200
    body = response.json()

    # 零额外查询：类名单 1 次、批量时长 1 次、逐条 0 次，其余表完全不碰。
    assert users.class_calls == [CLASS_OID]
    assert users.by_oid_calls == []
    assert times.ids_calls == [MEMBER_OIDS]

    rosters = body["member_rosters"]
    assert set(rosters) == {"on-campus", "off-campus", "social-practice"}

    for mode, labels in BUCKETS.items():
        # 桶键与顶层逐字一致（顺序也一致）。
        assert list(body[mode]) == labels
        assert list(rosters[mode]) == labels
        # 互斥且并集 = 该模式候选集合（每人恰好落一个桶）。
        seen = [entry["_id"] for label in labels for entry in rosters[mode][label]]
        assert sorted(seen) == sorted(MEMBER_OIDS)
        assert len(seen) == len(set(seen))
        # 每个桶的名单条数 == 顶层该桶的计数。
        for label in labels:
            assert len(rosters[mode][label]) == body[mode][label]


def test_compliance_roster_entries_are_the_exact_projection(monkeypatch) -> None:
    rows = [_group_row(oid, index) for index, oid in enumerate(MEMBER_OIDS)]
    client, _users, _times = _compliance_client(monkeypatch, rows)

    body = client.get(
        f"{GROUPS_PREFIX}/{CLASS_OID}/statistics/compliance"
    ).json()

    for mode, labels in BUCKETS.items():
        for label in labels:
            for entry in body["member_rosters"][mode][label]:
                # 只含白名单四字段：password / session_id 绝不外泄。
                assert set(entry) == ENTRY_KEYS

    # 具体落桶 + 时长与 user_times 同源（1 位小数）。
    on_campus = body["member_rosters"]["on-campus"]
    assert on_campus["Completed"] == [
        {"_id": MEMBER_OIDS[0], "id": "20230000", "name": "成员0", "duration": 25.0}
    ]
    assert on_campus[f"20%{EN_DASH}40%"] == [
        {"_id": MEMBER_OIDS[1], "id": "20230001", "name": "成员1", "duration": 5.0}
    ]
    assert on_campus[f"0%{EN_DASH}20%"] == [
        {"_id": MEMBER_OIDS[2], "id": "20230002", "name": "成员2", "duration": 2.5}
    ]

    text = json.dumps(body)
    assert "should-never-leak" not in text
    assert "session-should-never-leak" not in text


def test_compliance_emits_the_roster_unconditionally(monkeypatch) -> None:
    """即使类名单没有 name / 学号，新键也无条件出现（形状不依赖数据）。"""
    rows = [{"_id": oid} for oid in MEMBER_OIDS]
    client, _users, _times = _compliance_client(monkeypatch, rows)

    body = client.get(
        f"{GROUPS_PREFIX}/{CLASS_OID}/statistics/compliance"
    ).json()

    assert set(body) == {
        "on-campus",
        "off-campus",
        "social-practice",
        "member_rosters",
    }
    # 桶键仍与顶层逐字一致；缺身份的成员保留在名单里，只是 id/name 为空串。
    assert list(body["member_rosters"]["on-campus"]) == BUCKETS["on-campus"]
    assert body["member_rosters"]["on-campus"]["Completed"] == [
        {"_id": MEMBER_OIDS[0], "id": "", "name": "", "duration": 25.0}
    ]


# ── B：/v2/statistics/activities/{aid}/analysis ──────────────────────────────

STATS_PREFIX = "/api/v2/statistics/activities"
ACTIVITY_ID = "64b7f0c2e1a3b4c5d6e7f802"
U1 = "64b7f0c2e1a3b4c5d6e7f821"
U2 = "64b7f0c2e1a3b4c5d6e7f822"
GHOST = "64b7f0c2e1a3b4c5d6e7f823"
ANALYSIS_KEYS = {"total", "bin_width", "description", "bins", "modes", "classes"}


class _SpyMembersRepo:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[dict] = []

    async def list_by_activity(self, activity_oid: str, status: str | None = None):
        self.calls.append({"activity_oid": activity_oid, "status": status})
        return [
            dict(row) for row in self.rows if status is None or row["status"] == status
        ]

    async def get_by_oid(self, oid: str):
        raise AssertionError("分析端点不得逐条读 membership")


class _SpyUsersRepo:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.batch_calls: list[list[str]] = []
        self.by_oid_calls: list[str] = []

    async def get_many_by_oids(self, oids):
        self.batch_calls.append(list(oids))
        wanted = set(oids)
        return {row["_id"]: row for row in self.rows if row["_id"] in wanted}

    async def get_by_oid(self, oid: str):
        self.by_oid_calls.append(oid)
        raise AssertionError("分析端点不得逐条 get_by_oid")


class _SpyGroupsRepo:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls = 0

    async def list_all(self):
        self.calls += 1
        return list(self.rows)


def _analysis_client(monkeypatch, *, members, users, classes):
    monkeypatch.setattr(backends, "get_members_repo", lambda *a, **k: members)
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: users)
    monkeypatch.setattr(backends, "get_groups_repo", lambda *a, **k: classes)

    app = FastAPI()
    app.include_router(activity_statistics_router.router, prefix=STATS_PREFIX)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": "64b7f0c2e1a3b4c5d6e7f8ff",
        "per": ["secretary"],
    }
    return TestClient(app)


def _user_doc(oid: str, index: int) -> dict:
    return {
        "_id": oid,
        "id": f"2023100{index}",
        "name": f"张三{index}",
        "class_id": "c1",
        "password": "should-never-leak",
        "session_id": "session-should-never-leak",
    }


def _membership(oid: str, mode: str, duration: float) -> dict:
    return {
        "_id": f"row-{oid}",
        "activity": ACTIVITY_ID,
        "member": oid,
        "status": "effective",
        "mode": mode,
        "duration": duration,
    }


def test_analysis_mode_members_add_no_reads_and_keep_the_old_keys(
    monkeypatch,
) -> None:
    members = _SpyMembersRepo(
        [
            _membership(U1, "on-campus", 1.5),
            _membership(U2, "off-campus", 2.0),
            _membership(GHOST, "on-campus", 9.9),  # 用户查不到 → 跳过
        ]
    )
    users = _SpyUsersRepo([_user_doc(U1, 1), _user_doc(U2, 2)])
    classes = _SpyGroupsRepo([{"_id": "c1", "name": "高一(1)班"}])
    client = _analysis_client(monkeypatch, members=members, users=users, classes=classes)

    response = client.get(f"{STATS_PREFIX}/{ACTIVITY_ID}/analysis")

    assert response.status_code == 200
    body = response.json()

    # 零额外查询：memberships 1 次、profiles 1 次、classes 1 次，逐条 0 次。
    assert members.calls == [{"activity_oid": ACTIVITY_ID, "status": "effective"}]
    assert users.batch_calls == [[U1, U2, GHOST]]
    assert users.by_oid_calls == []
    assert classes.calls == 1

    # 既有六键不变；新键是追加的第七个，无条件出现。
    assert set(body) == ANALYSIS_KEYS | {"mode_members"}
    assert body["total"] == 3  # 与原实现一致：GHOST 也计入聚合
    assert body["bins"] and body["modes"] and body["classes"]

    assert set(body["mode_members"]) == {"on-campus", "off-campus"}
    assert body["mode_members"] == {
        "on-campus": [
            {"_id": U1, "id": "20231001", "name": "张三1", "duration": 1.5}
        ],
        "off-campus": [
            {"_id": U2, "id": "20231002", "name": "张三2", "duration": 2.0}
        ],
    }

    for entries in body["mode_members"].values():
        for entry in entries:
            assert set(entry) == ENTRY_KEYS
    text = json.dumps(body)
    assert "should-never-leak" not in text
    assert "session-should-never-leak" not in text


def test_analysis_without_effective_members_stays_the_empty_payload(
    monkeypatch,
) -> None:
    members = _SpyMembersRepo([_membership(U1, "on-campus", 1.0) | {"status": "pending"}])
    client = _analysis_client(
        monkeypatch,
        members=members,
        users=_SpyUsersRepo([]),
        classes=_SpyGroupsRepo([]),
    )

    body = client.get(f"{STATS_PREFIX}/{ACTIVITY_ID}/analysis").json()

    assert body == {
        "total": 0,
        "bin_width": 0.5,
        "description": None,
        "bins": [],
        "modes": [],
        "classes": [],
    }
    assert "mode_members" not in body


def test_analysis_emits_mode_members_unconditionally(
    monkeypatch,
) -> None:
    """profile 没有 name / 学号时，新键仍出现（旧六个键逐字节不变）。"""
    members = _SpyMembersRepo([_membership(U1, "on-campus", 1.5)])
    users = _SpyUsersRepo([{"_id": U1, "class_id": "c1"}])
    client = _analysis_client(
        monkeypatch,
        members=members,
        users=users,
        classes=_SpyGroupsRepo([{"_id": "c1", "name": "高一(1)班"}]),
    )

    body = client.get(f"{STATS_PREFIX}/{ACTIVITY_ID}/analysis").json()

    assert set(body) == ANALYSIS_KEYS | {"mode_members"}
    assert body["mode_members"] == {
        "on-campus": [{"_id": U1, "id": "", "name": "", "duration": 1.5}]
    }
