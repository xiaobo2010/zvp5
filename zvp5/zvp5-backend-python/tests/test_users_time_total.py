"""``GET /api/users/{user_oid}/time`` 的 ``total`` 字段。

历史 bug：路由直接读 ``result["total"]``，而 ``calculate_user_time`` 只回
``on-campus`` / ``off-campus`` / ``social-practice`` 三档 —— 于是这个端点
**恒定 500**（``KeyError: 'total'``），三档数字明明都算出来了。

钉住两件事：
1. ``total`` 是三档之和（与其它时长端点字段同名、口径相同）；
2. 现算（float）与走 ``USER_TIME`` 缓存（NUMBER → Decimal）两条路径都能求和。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from routers import users_router

USER_OID = "64b7f0c2e1a3b4c5d6e7fa01"
ADMIN = {"id": "64b7f0c2e1a3b4c5d6e7fa02", "per": ["admin"]}


@pytest.mark.asyncio
async def test_total_is_the_sum_of_the_three_buckets(monkeypatch) -> None:
    async def fake_calculate(user_oid, start=None, end=None):
        return {
            "on-campus": 12.0,
            "off-campus": 7.5,
            "social-practice": 0.5,
        }

    monkeypatch.setattr(users_router, "calculate_user_time", fake_calculate)

    response = await users_router.read_user_time(USER_OID, user=ADMIN)

    assert response == {
        "status": "ok",
        "code": 200,
        "data": {
            "onCampus": 12.0,
            "offCampus": 7.5,
            "socialPractice": 0.5,
            "total": 20.0,
        },
    }


@pytest.mark.asyncio
async def test_total_accepts_the_cached_decimal_shapes(monkeypatch) -> None:
    """走 ``USER_TIME`` 缓存时三个值是 NUMBER（Decimal），不能 Decimal + float。"""

    async def fake_calculate(user_oid, start=None, end=None):
        return {
            "on-campus": Decimal("3.5"),
            "off-campus": Decimal("2"),
            "social-practice": Decimal("0.25"),
        }

    monkeypatch.setattr(users_router, "calculate_user_time", fake_calculate)

    response = await users_router.read_user_time(USER_OID, user=ADMIN)

    assert response["data"]["total"] == 5.75


@pytest.mark.asyncio
async def test_date_range_path_still_returns_total(monkeypatch) -> None:
    seen: list[tuple] = []

    async def fake_calculate(user_oid, start=None, end=None):
        seen.append((user_oid, start, end))
        return {"on-campus": 1.0, "off-campus": 0.0, "social-practice": 0.0}

    monkeypatch.setattr(users_router, "calculate_user_time", fake_calculate)

    response = await users_router.read_user_time(
        USER_OID, start="2026-09-01", end="2026-09-30", user=ADMIN
    )

    assert len(seen) == 1 and seen[0][0] == USER_OID
    assert response["data"] == {
        "onCampus": 1.0,
        "offCampus": 0.0,
        "socialPractice": 0.0,
        "total": 1.0,
    }
