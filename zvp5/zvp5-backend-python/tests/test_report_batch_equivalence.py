"""批量报表/导出 == 逐用户结果 的等价性测试（不连库）。

两条夜间链路（``tasks.compute_time.generate_reports`` 与
``routers.exports_router._process_task``）从「逐用户查库」改成「整批预加载 +
内存组装」后，输出必须逐字节不变。这里用同一份假数据集同时跑：

* 逐用户老路径：``build_statement_data(id, period)``（不带任何预加载参数）
  与 ``calculate_user_time`` / ``generate_description``；
* 批量新路径：``generate_reports``（整批）与 ``_process_task``（整批）。

对比的是序列化后的 JSON（唯一被抹掉的是 ``export_time``，它取 ``now()``）。

顺带钉住查询次数：批量路径里 ``list_for_member`` / ``get_by_oid`` 必须为 0，
``list_by_members`` 只出现一次，查询数与用户数无关。

Run from the repository root::

    ./.venv/bin/python -m pytest tests/test_report_batch_equivalence.py -q
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import datetime
from types import SimpleNamespace

import pytest

from config import (
    BASE_OFF_CAMPUS,
    BASE_ON_CAMPUS,
    MAX_EXCEED_DISCOUNT,
    OFF_TO_ON_RATE,
    ON_TO_OFF_RATE,
)
from db import backends
from routers import exports_router as exports
from tasks import compute_time
from util import calculate as calc
from util import statement
from util.statement import statement_data_to_json

CLASS_A = "0000000000000000000000a1"
CLASS_B = "0000000000000000000000a2"
TEACHER = "000000000000000000000201"
U1 = "000000000000000000000001"
U2 = "000000000000000000000002"
U3 = "000000000000000000000003"
U4 = "000000000000000000000004"

ACT_ON = "000000000000000000000101"
ACT_OFF = "000000000000000000000102"
ACT_PENDING = "000000000000000000000103"

CLASSES = [
    {"_id": CLASS_A, "name": "高一(1)班", "description": ""},
    {"_id": CLASS_B, "name": "高一(2)班", "description": ""},
]

USERS = [
    {"_id": U1, "id": "20240001", "name": "学生一", "class_id": CLASS_A, "roles": []},
    {"_id": U2, "id": "20240002", "name": "学生二", "class_id": CLASS_A, "roles": []},
    {"_id": U3, "id": "20250003", "name": "学生三", "class_id": CLASS_B, "roles": []},
    {"_id": U4, "id": "20250004", "name": "学生四", "class_id": CLASS_B, "roles": []},
]

TEACHERS = {
    TEACHER: {"_id": TEACHER, "id": "20200002", "name": "李四", "roles": []},
}


def _activity(oid: str, name: str, day: int, approver: str, status: str) -> dict:
    return {
        "_id": oid,
        "name": name,
        "description": "",
        "type": "on-campus",
        "status": status,
        "date": datetime(2026, 9, day),
        "createdAt": datetime(2026, 8, 1),
        "updatedAt": datetime(2026, 8, 1),
        "appointee": U1,
        "approver": approver,
        "creator": U1,
        "place": "",
        "origin": "labor",
    }


ACTIVITIES = {
    ACT_ON: _activity(ACT_ON, "校园清扫", 1, TEACHER, "effective"),
    ACT_OFF: _activity(ACT_OFF, "图书馆整理", 2, "AUTHORITY", "effective"),
    ACT_PENDING: _activity(ACT_PENDING, "审核中活动", 3, TEACHER, "pending"),
}

# 行序 = 数据库 rowid 序。批量的 list_by_members 与逐用户的 list_for_member
# 都按这个顺序返回，所以分组后每个人的相对顺序必须保持不动。
MEMBERS = [
    {"_id": "000000000000000000000301", "activity": ACT_ON, "member": U1, "status": "effective", "mode": "on-campus", "duration": 2.5},
    {"_id": "000000000000000000000302", "activity": ACT_OFF, "member": U1, "status": "effective", "mode": "off-campus", "duration": 1.0},
    {"_id": "000000000000000000000303", "activity": ACT_PENDING, "member": U1, "status": "effective", "mode": "on-campus", "duration": 99.0},
    {"_id": "000000000000000000000304", "activity": ACT_ON, "member": U2, "status": "pending", "mode": "on-campus", "duration": 50.0},
    {"_id": "000000000000000000000305", "activity": ACT_ON, "member": U2, "status": "effective", "mode": "social-practice", "duration": 3.0},
    {"_id": "000000000000000000000306", "activity": ACT_OFF, "member": U3, "status": "effective", "mode": "off-campus", "duration": 4.0},
    {"_id": "000000000000000000000307", "activity": ACT_ON, "member": U3, "status": "effective", "mode": "on-campus", "duration": 6.5},
]


class _Repos:
    """四个 repo 的最小实现 + 调用计数。"""

    def __init__(self) -> None:
        self.calls: Counter[str] = Counter()

    # -- users ---------------------------------------------------------
    async def list_all(self):
        self.calls["users.list_all"] += 1
        return [dict(user) for user in USERS]

    async def get_by_oid(self, oid):
        self.calls["users.get_by_oid"] += 1
        for user in USERS:
            if user["_id"] == oid:
                return dict(user)
        return TEACHERS.get(oid)

    async def get_many_by_oids(self, oids):
        self.calls["users.get_many_by_oids"] += 1
        wanted = {str(oid) for oid in oids}
        return {
            oid: dict(doc)
            for oid, doc in {**{u["_id"]: u for u in USERS}, **TEACHERS}.items()
            if oid in wanted
        }

    # -- groups --------------------------------------------------------
    async def get_group(self, oid):
        self.calls["groups.get"] += 1
        for group in CLASSES:
            if group["_id"] == str(oid):
                return dict(group)
        return None

    async def list_all_groups(self):
        self.calls["groups.list_all"] += 1
        return [dict(group) for group in CLASSES]

    async def list_non_permission(self):
        self.calls["groups.list_non_permission"] += 1
        return [dict(group) for group in CLASSES]

    # -- activities ----------------------------------------------------
    async def get_activity(self, oid):
        self.calls["activities.get"] += 1
        return ACTIVITIES.get(str(oid))

    async def list_effective(self, date_start=None, date_end=None):
        self.calls["activities.list_effective"] += 1
        rows = [a for a in ACTIVITIES.values() if a["status"] == "effective"]
        if date_start is not None and date_end is not None:
            rows = [a for a in rows if date_start <= a["date"] <= date_end]
        return rows

    # -- members -------------------------------------------------------
    async def list_for_member(self, member_oid, status=None, activity_oids=None):
        self.calls["members.list_for_member"] += 1
        accepted = None if activity_oids is None else {str(o) for o in activity_oids}
        rows = [row for row in MEMBERS if row["member"] == str(member_oid)]
        if status is not None:
            rows = [row for row in rows if row["status"] == status]
        if accepted is not None:
            rows = [row for row in rows if row["activity"] in accepted]
        return [dict(row) for row in rows]

    async def list_by_members(self, member_oids):
        self.calls["members.list_by_members"] += 1
        wanted = {str(oid) for oid in member_oids}
        return [dict(row) for row in MEMBERS if row["member"] in wanted]

    async def sum_modes_for_member(self, member_oid):
        # ``calculate_user_time`` 的默认分支：单成员三档聚合，口径与批量版一致
        # （双重有效 + 审核中的活动不计入）。
        self.calls["members.sum_modes_for_member"] += 1
        totals = {"on-campus": 0.0, "off-campus": 0.0, "social-practice": 0.0}
        for row in MEMBERS:
            if row["member"] != str(member_oid) or row["status"] != "effective":
                continue
            activity = ACTIVITIES.get(row["activity"])
            if activity is None or activity["status"] != "effective":
                continue
            totals[row["mode"]] += row["duration"]
        return totals

    # -- time ----------------------------------------------------------
    async def get_for_user(self, user_oid):
        self.calls["time.get_for_user"] += 1
        return None

    async def list_by_users(self, user_oids):
        self.calls["time.list_by_users"] += 1
        return []

    async def upsert_for_user(self, user_oid, on_campus, off_campus, social):
        self.calls["time.upsert_for_user"] += 1

    async def upsert_many_for_users(self, entries):
        self.calls["time.upsert_many_for_users"] += 1
        self.last_upsert = list(entries)


class _DailyExportsRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    async def delete_all(self) -> int:
        self.rows.clear()
        return 0

    async def insert(self, document: dict) -> str:
        oid = f"export-{len(self.rows) + 1}"
        self.rows[oid] = {**document, "data": list(document.get("data") or [])}
        return oid

    async def update_fields(self, oid: str, fields: dict) -> None:
        self.rows[oid].update(fields)


@pytest.fixture
def repos(monkeypatch):
    state = _Repos()

    users = SimpleNamespace(
        list_all=state.list_all,
        get_by_oid=state.get_by_oid,
        get_many_by_oids=state.get_many_by_oids,
    )
    groups = SimpleNamespace(
        get=state.get_group,
        list_all=state.list_all_groups,
        list_non_permission=state.list_non_permission,
    )
    activities = SimpleNamespace(
        get=state.get_activity, list_effective=state.list_effective
    )
    members = SimpleNamespace(
        list_for_member=state.list_for_member,
        list_by_members=state.list_by_members,
        sum_modes_for_member=state.sum_modes_for_member,
    )
    times = SimpleNamespace(
        get_for_user=state.get_for_user,
        list_by_users=state.list_by_users,
        upsert_for_user=state.upsert_for_user,
        upsert_many_for_users=state.upsert_many_for_users,
    )

    monkeypatch.setattr(backends, "get_users_repo", lambda session=None: users)
    monkeypatch.setattr(backends, "get_groups_repo", lambda session=None: groups)
    monkeypatch.setattr(backends, "get_activities_repo", lambda session=None: activities)
    monkeypatch.setattr(backends, "get_members_repo", lambda session=None: members)
    monkeypatch.setattr(backends, "get_time_repo", lambda session=None: times)
    return state


def _normalized(data, spreadsheet: dict, filename: str) -> str:
    """序列化后抹掉 ``export_time``（唯一的 ``now()`` 依赖）。"""
    payload = json.loads(statement_data_to_json(data, spreadsheet, filename))
    payload["user_data"]["export_time"] = "<now>"
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False)


def _period(entry: int) -> tuple[datetime, datetime]:
    return compute_time._academic_period(entry)


async def _batch_inputs(user_oids: list[str]):
    """复刻 ``generate_reports`` 的预加载步骤，返回组装所需的所有 map。"""
    activities = await backends.get_activities_repo().list_effective()
    activity_map = {str(activity["_id"]): activity for activity in activities}
    memberships: dict[str, list] = {}
    for row in await backends.get_members_repo().list_by_members(user_oids):
        if row["status"] != "effective":
            continue
        memberships.setdefault(str(row["member"]), []).append(row)
    approvers = await backends.get_users_repo().get_many_by_oids(
        list(
            {
                activity["approver"]
                for activity in activities
                if statement.is_valid_oid(activity.get("approver", ""))
            }
        )
    )
    stats = await calc.calculate_user_time_bulk(user_oids, allow_cache=False)
    by_entry: dict[int, list[str]] = {}
    for user in USERS:
        if str(user["_id"]) in user_oids:
            by_entry.setdefault(int(user["id"][:4]), []).append(str(user["_id"]))
    year_stats: dict[str, dict] = {}
    for entry, oids in by_entry.items():
        year_stats.update(
            await calc.calculate_user_time_bulk(
                oids, *_period(entry), allow_cache=False
            )
        )
    return activity_map, memberships, approvers, stats, year_stats


@pytest.mark.asyncio
async def test_batch_statement_equals_per_user_statement(repos):
    """``assemble_statement`` + 批量 map == 无预加载参数的 ``build_statement_data``。"""
    user_oids = [user["_id"] for user in USERS]
    activity_map, memberships, approvers, stats, year_stats = await _batch_inputs(
        user_oids
    )

    for user in USERS:
        oid = str(user["_id"])
        period = _period(int(user["id"][:4]))
        legacy_data, legacy_sheet = await statement.build_statement_data(
            oid, period
        )
        items = memberships.get(oid, [])
        batch_data, batch_sheet = statement.assemble_statement(
            oid=oid,
            user=user,
            class_name=next(
                group["name"] for group in CLASSES if group["_id"] == user["class_id"]
            ),
            period=period,
            language="zh",
            stats=stats[oid],
            stats_this_year=year_stats[oid],
            memberships=items,
            activities=[activity_map.get(str(item["activity"])) for item in items],
            approvers=approvers,
        )

        filename = f"./export/{batch_sheet['group']}/{user['id']}_{user['name']}.json"
        assert _normalized(batch_data, batch_sheet, filename) == _normalized(
            legacy_data, legacy_sheet, filename
        ), oid


@pytest.mark.asyncio
async def test_generate_reports_bytes_match_per_user_path(repos, monkeypatch, tmp_path):
    """整批 ``generate_reports`` 的每份 JSON == 逐用户老路径。"""
    captured: list[tuple[object, dict, str]] = []

    def fake_write(data, spreadsheet, filename):
        captured.append((data, spreadsheet, filename))

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        backends, "get_daily_exports_repo", lambda *a, **k: _DailyExportsRepo()
    )
    monkeypatch.setattr(compute_time, "write_statement_json", fake_write)

    await compute_time.generate_reports()

    expected = []
    for class_ in CLASSES:
        for user in USERS:
            if user["class_id"] != class_["_id"]:
                continue
            oid = str(user["_id"])
            data, spreadsheet = await statement.build_statement_data(
                oid, _period(int(user["id"][:4]))
            )
            filename = (
                f"./export/{class_['name']}/{user['id']}_{user['name']}.json"
            )
            expected.append(
                (
                    filename,
                    user["id"],
                    _normalized(data, spreadsheet, filename),
                )
            )

    produced = [
        (
            "./export/" + filename.split("/export/", 1)[1],
            spreadsheet["id"],
            _normalized(data, spreadsheet, "./export/" + filename.split("/export/", 1)[1]),
        )
        for data, spreadsheet, filename in captured
    ]
    assert produced == expected


@pytest.mark.asyncio
async def test_generate_reports_has_no_per_user_queries(repos, monkeypatch, tmp_path, caplog):
    """批量路径：0 次 list_for_member / get_by_oid，1 次 list_by_members。"""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        backends, "get_daily_exports_repo", lambda *a, **k: _DailyExportsRepo()
    )
    monkeypatch.setattr(
        compute_time, "write_statement_json", lambda *args: None
    )

    await compute_time.generate_reports()

    assert repos.calls["members.list_for_member"] == 0
    assert repos.calls["users.get_by_oid"] == 0
    assert repos.calls["activities.get"] == 0
    # 1 次显式批量读 + 每个入学年份各 1 次（calculate_user_time_bulk 内部）。
    assert repos.calls["members.list_by_members"] == 1 + 3
    assert repos.calls["activities.list_effective"] == 1 + 3
    assert repos.calls["time.upsert_for_user"] == 0
    assert repos.calls["time.upsert_many_for_users"] == 1
    assert repos.calls["users.list_all"] == 1
    assert repos.calls["groups.list_non_permission"] == 1
    # 老路径 4 个用户 ≈ 11 次/人 = 44 次；批量恒定 12 次，且与人数无关。
    assert sum(repos.calls.values()) == 12


@pytest.mark.asyncio
async def test_time_export_rows_match_per_user_path(repos, monkeypatch):
    """``_process_task`` 的 time 导出 == 逐用户 ``calculate_user_time`` 的结果。"""
    pushed: list[dict] = []

    async def get_task(value):
        return {
            "variant": "time",
            "export_start": None,
            "export_end": None,
            "allow_cache": True,
            "include_description": True,
        }

    async def nothing(*args, **kwargs):
        return None

    async def push_result(task_id, rows, fields):
        pushed.extend(rows)

    monkeypatch.setattr(
        backends,
        "get_tasks_repo",
        lambda: SimpleNamespace(
            get_by_uuid=get_task, update_fields=nothing, push_result=push_result
        ),
    )

    await exports.process_task(str(uuid.uuid4()))

    expected = []
    for user in USERS:
        user_time = await calc.calculate_user_time(
            str(user["_id"]), allow_cache=True, attach_description=True
        )
        more_on_campus = min(
            round(max(user_time["off-campus"] - BASE_OFF_CAMPUS, 1) * OFF_TO_ON_RATE, 0),
            MAX_EXCEED_DISCOUNT,
        )
        more_off_campus = min(
            round(max(user_time["on-campus"] - BASE_ON_CAMPUS, 1) * ON_TO_OFF_RATE, 0),
            MAX_EXCEED_DISCOUNT,
        )
        user_time["on-campus"] += more_on_campus
        user_time["off-campus"] += more_off_campus
        expected.append(
            {
                "_id": str(user["_id"]),
                "Name": user["name"],
                "ID": str(user["id"]),
                "Group": next(
                    group["name"]
                    for group in CLASSES
                    if group["_id"] == user["class_id"]
                ),
                "On Campus": user_time["on-campus"],
                "Off Campus": user_time["off-campus"],
                "Social Practice": user_time["social-practice"],
                "Description": user_time["description"],
            }
        )

    assert pushed == expected
    assert repos.calls["users.list_all"] == 1
    assert repos.calls["groups.list_all"] == 1
    assert repos.calls["users.get_by_oid"] == 0


@pytest.mark.asyncio
async def test_users_export_matches_per_user_path(repos, monkeypatch):
    pushed: list[dict] = []

    async def get_task(value):
        return {"variant": "users", "export_start": None, "export_end": None}

    async def nothing(*args, **kwargs):
        return None

    async def push_result(task_id, rows, fields):
        pushed.extend(rows)

    monkeypatch.setattr(
        backends,
        "get_tasks_repo",
        lambda: SimpleNamespace(
            get_by_uuid=get_task, update_fields=nothing, push_result=push_result
        ),
    )

    await exports.process_task(str(uuid.uuid4()))

    assert pushed == [
        {
            "_id": str(user["_id"]),
            "Name": user["name"],
            "ID": str(user["id"]),
            "Group": next(
                group["name"] for group in CLASSES if group["_id"] == user["class_id"]
            ),
            "On Campus": None,
            "Off Campus": None,
            "Social Practice": None,
        }
        for user in USERS
    ]
    assert repos.calls["users.list_all"] == 1
    assert repos.calls["groups.list_all"] == 1
