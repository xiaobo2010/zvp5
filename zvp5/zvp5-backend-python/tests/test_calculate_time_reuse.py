"""``calculate_user_time`` 的两种口径：默认聚合 vs 预取/日期窗路径。

默认分支（无日期窗、未预取活动）只发**一条**按成员的聚合：不再拉全部有效
活动 + 该人全部成员行在 Python 里求和。其余分支（日期窗 / 预取 activities）
保持原样，两条路径的结果必须逐字段相同。
"""

from datetime import datetime

import pytest

from db import backends
from util import calculate as calc

USER = "000000000000000000000001"
ACTIVITY_A = "000000000000000000000010"
ACTIVITY_B = "000000000000000000000011"

MEMBERS = [
    {"mode": "on-campus", "duration": 2.5, "activity": ACTIVITY_A},
    {"mode": "off-campus", "duration": 1.0, "activity": ACTIVITY_A},
    {"mode": "on-campus", "duration": 4.0, "activity": ACTIVITY_B},
]

TOTALS = {"on-campus": 6.5, "off-campus": 1.0, "social-practice": 0.0}


@pytest.fixture
def repos(monkeypatch):
    state = {"list_effective": [], "member_queries": [], "member_totals": [], "upserts": []}

    async def list_effective(*args):
        state["list_effective"].append(args)
        return [{"_id": ACTIVITY_A}, {"_id": ACTIVITY_B}]

    async def list_for_member(user_id, status=None, activity_oids=None):
        state["member_queries"].append((user_id, status, list(activity_oids or [])))
        accepted = set(activity_oids or [])
        return [row for row in MEMBERS if row["activity"] in accepted]

    async def sum_modes_for_member(user_id):
        state["member_totals"].append(user_id)
        return dict(TOTALS)

    async def get_for_user(user_id):
        return None

    async def upsert_for_user(user_id, on_campus, off_campus, social_practice):
        state["upserts"].append((user_id, on_campus, off_campus, social_practice))

    monkeypatch.setattr(
        backends, "get_activities_repo", lambda session=None: _Activities(list_effective)
    )
    monkeypatch.setattr(
        backends,
        "get_members_repo",
        lambda session=None: _Members(list_for_member, sum_modes_for_member),
    )
    monkeypatch.setattr(
        backends,
        "get_time_repo",
        lambda session=None: _Times(get_for_user, upsert_for_user),
    )
    return state


class _Activities:
    def __init__(self, list_effective):
        self.list_effective = list_effective


class _Members:
    def __init__(self, list_for_member, sum_modes_for_member):
        self.list_for_member = list_for_member
        self.sum_modes_for_member = sum_modes_for_member


class _Times:
    def __init__(self, get_for_user, upsert_for_user):
        self.get_for_user = get_for_user
        self.upsert_for_user = upsert_for_user


@pytest.mark.asyncio
async def test_default_branch_uses_one_aggregate_instead_of_the_whole_table(repos, capsys):
    result = await calc.calculate_user_time(USER, allow_cache=False)

    assert result == TOTALS
    # 默认分支：一次按成员聚合；不再拉全表活动、不再拉成员行。
    assert repos["member_totals"] == [USER]
    assert repos["list_effective"] == []
    assert repos["member_queries"] == []
    # 缓存回填仍是同一个 upsert（三档取值与聚合一致）。
    assert repos["upserts"] == [(USER, 6.5, 1.0, 0.0)]
    assert capsys.readouterr().out == ""


@pytest.mark.asyncio
async def test_preset_activities_match_the_internal_lookup(repos, capsys):
    baseline = await calc.calculate_user_time(USER, allow_cache=False)

    preset = await calc.calculate_user_time(
        USER, allow_cache=False, activities=[{"_id": ACTIVITY_A}, {"_id": ACTIVITY_B}]
    )

    assert preset == baseline
    # 预取路径照旧：一次成员行查询，不再走单成员聚合。
    assert repos["member_queries"] == [(USER, "effective", [ACTIVITY_A, ACTIVITY_B])]
    assert repos["member_totals"] == [USER]
    assert repos["list_effective"] == []
    assert capsys.readouterr().out == ""


@pytest.mark.asyncio
async def test_preset_activities_match_with_a_date_range(repos, capsys):
    start, end = datetime(2026, 1, 1), datetime(2026, 12, 31)

    baseline = await calc.calculate_user_time(USER, start, end, allow_cache=False)
    preset = await calc.calculate_user_time(
        USER,
        start,
        end,
        allow_cache=False,
        activities=[{"_id": ACTIVITY_A}, {"_id": ACTIVITY_B}],
    )

    assert preset == baseline
    assert repos["list_effective"] == [(start, end)]
    assert repos["member_queries"] == [
        (USER, "effective", [ACTIVITY_A, ACTIVITY_B]),
        (USER, "effective", [ACTIVITY_A, ACTIVITY_B]),
    ]
    # 日期窗口径不受单成员聚合影响。
    assert repos["member_totals"] == []
    assert capsys.readouterr().out == ""


@pytest.mark.asyncio
async def test_member_without_effective_rows_still_gets_zeroes(repos, monkeypatch):
    async def sum_modes_for_member(user_id):
        repos["member_totals"].append(user_id)
        return None

    monkeypatch.setattr(
        backends,
        "get_members_repo",
        lambda session=None: _Members(None, sum_modes_for_member),
    )

    result = await calc.calculate_user_time(USER, allow_cache=False)

    assert result == {"on-campus": 0.0, "off-campus": 0.0, "social-practice": 0.0}
    assert repos["member_totals"] == [USER]


@pytest.mark.asyncio
async def test_preset_activities_do_not_bypass_the_cache(repos, monkeypatch):
    async def get_for_user(user_id):
        return {"on_campus_raw": 7.0, "off_campus_raw": 1.0, "social_practice": 0.0}

    async def upsert_for_user(*args):
        raise AssertionError("no write on a cache hit")

    monkeypatch.setattr(
        backends,
        "get_time_repo",
        lambda session=None: _Times(get_for_user, upsert_for_user),
    )

    result = await calc.calculate_user_time(
        USER, allow_cache=True, activities=[{"_id": ACTIVITY_A}]
    )

    assert result == {"on-campus": 7.0, "off-campus": 1.0, "social-practice": 0.0}
    assert repos["list_effective"] == []
    assert repos["member_queries"] == []
    assert repos["member_totals"] == []
