"""``GET /api/v1/users/{user_id}/time``（合并后唯一的 v2 实现）。

合并时该端点留下了 v2 版本：它把 ``calculate_user_time`` 的三档原始结果
（``on-campus`` / ``off-campus`` / ``social-practice``，kebab-case）原样返回，
``allow_cache`` 透传。已删除的 v1 实现额外把它整形成 ``onCampus`` /
``offCampus`` / ``socialPractice`` + ``total`` 四字段，前端
``src/api/user/time.ts`` 读取的是三档原始字段，因此那些整形没有消费者。

钉住三件事：
1. 响应就是时间计算的返回值本身（不二次加工、不丢字段）；
2. ``allow_cache=False`` 原样透传到计算层（前端「重算」按钮依赖它）；
3. Decimal（走 ``USER_TIME`` 缓存）不会被强转成 float，口径与计算层一致。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from routers import users_router

USER_OID = "64b7f0c2e1a3b4c5d6e7fa01"
ADMIN = {"id": "64b7f0c2e1a3b4c5d6e7fa02", "per": ["admin"]}


@pytest.mark.asyncio
async def test_returns_the_calculation_payload_verbatim(monkeypatch) -> None:
    async def fake_calculate(user_oid, allow_cache=True):
        return {
            "on-campus": 12.0,
            "off-campus": 7.5,
            "social-practice": 0.5,
        }

    monkeypatch.setattr(users_router, "calculate_user_time", fake_calculate)

    response = await users_router.get_user_time_v2(USER_OID, user=ADMIN)

    assert response == {
        "on-campus": 12.0,
        "off-campus": 7.5,
        "social-practice": 0.5,
    }


@pytest.mark.asyncio
async def test_allow_cache_is_forwarded(monkeypatch) -> None:
    seen: list[tuple] = []

    async def fake_calculate(user_oid, allow_cache=True):
        seen.append((user_oid, allow_cache))
        return {"on-campus": 0.0, "off-campus": 0.0, "social-practice": 0.0}

    monkeypatch.setattr(users_router, "calculate_user_time", fake_calculate)

    await users_router.get_user_time_v2(USER_OID, user=ADMIN, allow_cache=False)

    assert seen == [(USER_OID, False)]


@pytest.mark.asyncio
async def test_cached_decimal_values_pass_through(monkeypatch) -> None:
    """走 ``USER_TIME`` 缓存时三个值是 NUMBER（Decimal）：不做强制转换。"""

    async def fake_calculate(user_oid, allow_cache=True):
        return {
            "on-campus": Decimal("3.5"),
            "off-campus": Decimal("2"),
            "social-practice": Decimal("0.25"),
        }

    monkeypatch.setattr(users_router, "calculate_user_time", fake_calculate)

    response = await users_router.get_user_time_v2(USER_OID, user=ADMIN)

    assert response["on-campus"] == Decimal("3.5")
    assert response["off-campus"] == Decimal("2")
    assert response["social-practice"] == Decimal("0.25")
