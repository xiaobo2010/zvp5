"""``generate_reports`` 批量写 ``daily_exports.data`` 的离线单测。

背景：旧实现每个用户调一次 ``push_data``（读整个 JSON CLOB → append → 整列回写），
N 个用户就是 N 次全量 CLOB 读写，线上 perf_top_sql 里 ``UPDATE daily_exports
SET data=:data`` 单次 58 万 buffer gets。现在改成循环内只攒内存 list、循环结束
一次性 ``update_fields(oid, {"data": items})``。

这组用例钉住四件事：
1. 写 data 的动作只有 1 次（不退回逐条 ``push_data``）；
2. 落库顺序与逐条 append 完全一致（= 班级顺序 × 用户顺序）；
3. 单个用户生成失败仍然是「整批 failed + 抛出」；
4. ``find_one`` 拿回的 data 仍可直接喂 ``pd.DataFrame``（元素是 dict）。

全程假 repo + 临时目录，不连库、不写仓库。

注：逐用户的 ``build_statement_data`` 现已换成批量预加载 + 线程内
``assemble_statement``，所以失败注入点也跟着挪到了 ``assemble_statement``
（对外的「单个用户失败 → 整批 failed」契约不变）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from db import backends
from tasks import compute_time

CLASSES = [
    {"_id": "class-1", "name": "高一(1)班"},
    {"_id": "class-2", "name": "高一(2)班"},
]

USERS = {
    "class-1": [
        {"_id": "65f0000000000000000000a1", "id": "20240001", "name": "学生一", "class_id": "class-1"},
        {"_id": "65f0000000000000000000a2", "id": "20240002", "name": "学生二", "class_id": "class-1"},
        {"_id": "65f0000000000000000000a3", "id": "20240003", "name": "学生三", "class_id": "class-1"},
    ],
    "class-2": [
        {"_id": "65f0000000000000000000b1", "id": "20240004", "name": "学生四", "class_id": "class-2"},
        {"_id": "65f0000000000000000000b2", "id": "20240005", "name": "学生五", "class_id": "class-2"},
    ],
}


class _FakeDailyExportsRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.data_writes = 0
        self.push_data_calls = 0
        self.inserted: list[str] = []

    async def delete_all(self) -> int:
        cleared = len(self.rows)
        self.rows.clear()
        return cleared

    async def insert(self, document: dict) -> str:
        oid = f"export-{len(self.inserted) + 1}"
        self.inserted.append(oid)
        self.rows[oid] = {**document, "data": list(document.get("data") or [])}
        return oid

    async def update_fields(self, oid: str, fields: dict) -> None:
        self.rows[oid].update(fields)
        if "data" in fields:
            self.data_writes += 1

    async def push_data(self, oid: str, item: dict) -> None:
        self.push_data_calls += 1
        self.rows[oid]["data"].append(item)

    async def find_one(self, oid: str) -> dict:
        return {"_id": oid, **self.rows[oid]}


class _FakeGroupsRepo:
    async def list_non_permission(self) -> list[dict]:
        return [dict(c) for c in CLASSES]


class _FakeUsersRepo:
    async def list_all(self) -> list[dict]:
        return [dict(u) for users in USERS.values() for u in users]

    async def get_many_by_oids(self, oids) -> dict:
        return {}


class _FakeActivitiesRepo:
    async def list_effective(self, *args) -> list[dict]:
        return []


class _FakeMembersRepo:
    async def list_by_members(self, member_oids) -> list[dict]:
        return []


class _FakeTimeRepo:
    async def upsert_many_for_users(self, entries) -> None:
        return None


def _class_name(class_oid: str) -> str:
    return next(c["name"] for c in CLASSES if c["_id"] == class_oid)


def _spreadsheet(user: dict, class_name: str) -> dict:    return {
        "_id": user["_id"],
        "name": user["name"],
        "id": user["id"],
        "group": class_name,
        "on-campus": 1.5,
        "off-campus": 2.0,
        "social-practice": 3.0,
        "ay-on-campus": 0.5,
        "ay-off-campus": 1.0,
        "ay-social-practice": 1.5,
        "description": "共 6.5 小时",
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    """假 repo + 临时工作目录；返回 repo 供断言。"""
    repo = _FakeDailyExportsRepo()
    written: list[str] = []
    failing: set[str] = set()

    def fake_assemble_statement(
        *,
        oid,
        user,
        class_name,
        period,
        language,
        stats,
        stats_this_year,
        memberships,
        activities,
        approvers,
    ):
        if str(oid) in failing:
            raise RuntimeError("statement build failed")
        return {"user_data": {"database_id": user["_id"]}}, _spreadsheet(user, class_name)

    async def fake_bulk(user_ids, *args, **kwargs):
        return {
            str(user_id): {
                "on-campus": 0.0,
                "off-campus": 0.0,
                "social-practice": 0.0,
            }
            for user_id in user_ids
        }

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(backends, "get_daily_exports_repo", lambda *a, **k: repo)
    monkeypatch.setattr(backends, "get_groups_repo", lambda *a, **k: _FakeGroupsRepo())
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: _FakeUsersRepo())
    monkeypatch.setattr(
        backends, "get_activities_repo", lambda *a, **k: _FakeActivitiesRepo()
    )
    monkeypatch.setattr(
        backends, "get_members_repo", lambda *a, **k: _FakeMembersRepo()
    )
    monkeypatch.setattr(backends, "get_time_repo", lambda *a, **k: _FakeTimeRepo())
    monkeypatch.setattr(
        compute_time, "assemble_statement", fake_assemble_statement, raising=False
    )
    monkeypatch.setattr(
        compute_time, "calculate_user_time_bulk", fake_bulk, raising=False
    )
    monkeypatch.setattr(
        compute_time,
        "write_statement_json",
        lambda volunteer, spreadsheet, filename: written.append(filename),
        raising=False,
    )
    return SimpleNamespace(repo=repo, written=written, failing=failing)


@pytest.mark.asyncio
async def test_data_written_once_for_all_users(env):
    await compute_time.generate_reports()

    assert env.repo.data_writes == 1
    assert env.repo.push_data_calls == 0
    assert len(env.repo.inserted) == 1


@pytest.mark.asyncio
async def test_data_order_matches_sequential_append(env, tmp_path):
    await compute_time.generate_reports()

    # 落盘用绝对路径（cwd 下的 export/），元数据里保留 tar 包内的相对标签。
    assert env.written == [
        str(tmp_path / "export" / _class_name(class_oid) / f"{user['id']}_{user['name']}.json")
        for class_oid in ("class-1", "class-2")
        for user in USERS[class_oid]
    ]
    expected = [
        f"./export/{_class_name(class_oid)}/{user['id']}_{user['name']}.json"
        for class_oid in ("class-1", "class-2")
        for user in USERS[class_oid]
    ]

    row = env.repo.rows[env.repo.inserted[0]]
    assert [item["filename"] for item in row["data"]] == expected
    assert [item["id"] for item in row["data"]] == [
        user["id"] for class_oid in ("class-1", "class-2") for user in USERS[class_oid]
    ]


@pytest.mark.asyncio
async def test_single_user_failure_marks_failed_and_raises(env):
    env.failing.add(USERS["class-1"][1]["_id"])

    with pytest.raises(RuntimeError):
        await compute_time.generate_reports()

    row = env.repo.rows[env.repo.inserted[0]]
    assert row["status"] == "failed"
    assert row["data"] == []
    assert env.repo.data_writes == 0


@pytest.mark.asyncio
async def test_find_one_data_is_dataframe_ready(env):
    await compute_time.generate_reports()

    stored = await env.repo.find_one(env.repo.inserted[0])
    data = stored["data"]
    assert len(data) == sum(len(users) for users in USERS.values())
    assert all(isinstance(item, dict) for item in data)

    df = pd.DataFrame(data)
    assert len(df) == len(data)
    assert {"name", "id", "group", "on-campus", "filename"} <= set(df.columns)
    assert df["group"].tolist() == ["高一(1)班"] * 3 + ["高一(2)班"] * 2


@pytest.mark.asyncio
async def test_non_numeric_student_id_is_skipped_not_fatal(env, monkeypatch, caplog):
    """学号前 4 位不是数字时只跳过该用户，不能让整批报表 failed。"""
    dirty = {
        "_id": "65f0000000000000000000c1",
        "id": "S2024009",
        "name": "脏数据",
        "class_id": "class-1",
    }
    monkeypatch.setitem(USERS, "class-1", [*USERS["class-1"], dirty])

    await compute_time.generate_reports()

    row = env.repo.rows[env.repo.inserted[0]]
    assert row["status"] == "processing"  # 没有翻成 failed
    assert [item["id"] for item in row["data"]] == [
        "20240001",
        "20240002",
        "20240003",
        "20240004",
        "20240005",
    ]
    assert "skip user S2024009" in caplog.text


@pytest.mark.asyncio
async def test_artifacts_land_under_absolute_paths(env, tmp_path):
    """落盘位置与下载路由一致：cwd/data/export.tar.gz + cwd/export/summary.xlsx。"""
    await compute_time.generate_reports()

    archive = tmp_path / "data" / "export.tar.gz"
    assert archive.is_file()
    assert (tmp_path / "export" / "summary.xlsx").is_file()
