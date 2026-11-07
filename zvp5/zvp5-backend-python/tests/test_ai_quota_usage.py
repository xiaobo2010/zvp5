"""``GET /api/ai/quota/usage`` 的冻结契约：本人用量统计。

钉五件事：

* ``period`` 缺省 = ``quota.period_now()``，显式传值按该自然月的半开区间取数
  （``[首日 00:00, 次月首日 00:00)``——闭区间会让跨月那一刻被两个月各算一次）；
* 非法 ``period`` 回 400，**不**静默回落到本月（回落出来的「一片 0」会被读成
  「这个月没用量」）；
* 聚合口径：``calls = COUNT(*)``（成功失败都算），``units`` / 三组 token 求和，
  ``provider`` / ``model`` 的 NULL 保留成独立分组；
* 空数据回全 0 + 空数组，而不是缺字段（前端少一层判空）；
* 只统计本人：仓储收到的 ``user_oid`` 恒为当前登录人，别人的行一条都不进合计。

路由注册顺序也在本文件里被隐式钉住：``/quota/usage`` 必须排在
``/quota/{user_oid}`` 之前，否则会被动态段吞成 ``user_oid="usage"``（那条要
admin 且校验 oid 形状，非 admin 会直接 403）。所以这里用一个**非 admin** 用户
打这个端点：200 就证明它没被动态段接走。

全离线：仓储是内存桩（按 ``[since, until)`` 过滤 + 分组），路由与响应组装是
真实代码。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import settings  # noqa: F401  (services.ai.config 读它，先导入保证顺序一致)
from db import backends
from routers import ai_router
from services.ai import quota as quota_service
from util.object_id import get_current_user

PREFIX = "/api/ai"
USER = "000000000000000000000003"
OTHER = "000000000000000000000004"

OCT = (datetime(2026, 10, 1), datetime(2026, 11, 1))
NOV = (datetime(2026, 11, 1), datetime(2026, 12, 1))

_KEYS = ("calls", "units", "prompt_tokens", "completion_tokens", "total_tokens")


def _row(
    provider: str | None,
    model: str | None,
    *,
    user_oid: str = USER,
    units: int = 1,
    prompt_tokens: int | None = 10,
    completion_tokens: int | None = 5,
    total_tokens: int | None = 15,
    created_at: datetime = datetime(2026, 10, 15, 8, 0, 0),
) -> dict:
    """一条流水的相关列；token 列可以为 None（上游没报 usage）。"""
    return {
        "user_oid": user_oid,
        "provider": provider,
        "model": model,
        "units": units,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "created_at": created_at,
    }


class FakeCallLogsRepo:
    """内存 ``AICallLogsRepo``：只实现本端点用到的 ``usage_breakdown``。

    过滤与分组语义照真仓储：只算本人、``[since, until)`` 半开、``provider`` /
    ``model`` 的 NULL 自成一组、排序 ``calls desc, provider asc, model asc``。
    """

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = list(rows or [])
        self.calls: list[dict] = []

    async def usage_breakdown(self, *, user_oid: str, since: datetime, until: datetime):
        self.calls.append({"user_oid": user_oid, "since": since, "until": until})
        groups: dict[tuple[str | None, str | None], dict] = {}
        for row in self.rows:
            if row["user_oid"] != user_oid:
                continue
            # 半开区间：次月首日 00:00 那一刻属于下个月。
            if not (since <= row["created_at"] < until):
                continue
            key = (row["provider"], row["model"])
            bucket = groups.get(key)
            if bucket is None:
                bucket = {"provider": key[0], "model": key[1], **dict.fromkeys(_KEYS, 0)}
                groups[key] = bucket
            bucket["calls"] += 1
            bucket["units"] += int(row["units"] or 0)
            bucket["prompt_tokens"] += int(row["prompt_tokens"] or 0)
            bucket["completion_tokens"] += int(row["completion_tokens"] or 0)
            bucket["total_tokens"] += int(row["total_tokens"] or 0)
        return sorted(
            groups.values(),
            key=lambda g: (-g["calls"], g["provider"] or "", g["model"] or ""),
        )


def _make_client(monkeypatch, rows=None, per=("student",)):
    repo = FakeCallLogsRepo(rows)
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: repo)

    app = FastAPI()
    app.include_router(ai_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: {"id": USER, "per": list(per)}
    return SimpleNamespace(client=TestClient(app), repo=repo)


# ---------------------------------------------------------------------------
# period：缺省 / 显式 / 非法
# ---------------------------------------------------------------------------


def test_usage_defaults_to_current_period(monkeypatch):
    month_start = datetime.now().replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    env = _make_client(
        monkeypatch,
        [
            _row("openai", "gpt-4o", created_at=month_start),
            _row("openai", "gpt-4o", units=3, created_at=datetime.now()),
        ],
    )

    response = env.client.get(f"{PREFIX}/quota/usage")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["code"] == 200
    assert body["data"]["period"] == quota_service.period_now()
    assert body["data"]["total"]["calls"] == 2
    assert body["data"]["total"]["units"] == 4
    # 缺省也要把「本月」的窗口算出来交给仓储（不是让仓储自己猜月份）。
    assert env.repo.calls[0]["since"] == month_start


def test_usage_explicit_period_selects_that_month(monkeypatch):
    env = _make_client(
        monkeypatch,
        [
            _row("openai", "gpt-4o", created_at=datetime(2026, 10, 1, 0, 0, 0)),
            _row("openai", "gpt-4o", created_at=datetime(2026, 10, 31, 23, 59, 59)),
            # 次月首日 00:00 —— 属于 11 月，不能算进 10 月。
            _row("openai", "gpt-4o", created_at=datetime(2026, 11, 1, 0, 0, 0)),
            _row("baidu", "ernie", created_at=datetime(2026, 11, 20)),
        ],
    )

    response = env.client.get(f"{PREFIX}/quota/usage", params={"period": "202610"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["period"] == "202610"
    assert data["total"]["calls"] == 2
    assert [item["provider"] for item in data["by_provider"]] == ["openai"]
    assert env.repo.calls == [
        {"user_oid": USER, "since": OCT[0], "until": OCT[1]}
    ]


@pytest.mark.parametrize("bad", ["2026-10", "202613", "20261", "abcd", "202600", ""])
def test_usage_invalid_period_is_400(monkeypatch, bad):
    env = _make_client(monkeypatch)

    response = env.client.get(f"{PREFIX}/quota/usage", params={"period": bad})

    assert response.status_code == 400
    assert response.json()["detail"] == "period 必须是 YYYYMM 格式"
    # 参数校验在仓储之前：错参数不该触发一次全表聚合。
    assert env.repo.calls == []


# ---------------------------------------------------------------------------
# 聚合口径
# ---------------------------------------------------------------------------


def test_usage_aggregates_by_provider_and_model(monkeypatch):
    """多渠道多模型多行，含 NULL provider（OCR / 未标注）：NULL 行要留下。"""
    env = _make_client(
        monkeypatch,
        [
            _row("openai", "gpt-4o", units=2, prompt_tokens=100, completion_tokens=40, total_tokens=140),
            _row("openai", "gpt-4o", units=1, prompt_tokens=50, completion_tokens=10, total_tokens=60),
            _row("openai", "gpt-4o-mini", units=1, prompt_tokens=20, completion_tokens=5, total_tokens=25),
            # 上游没报 usage：token 列 NULL，聚合时按 0 算，但行本身不能丢。
            _row("bailian", "qwen-max", units=4, prompt_tokens=None, completion_tokens=None, total_tokens=None),
            _row(None, None, units=1, prompt_tokens=7, completion_tokens=3, total_tokens=10),
        ],
    )

    response = env.client.get(f"{PREFIX}/quota/usage", params={"period": "202610"})

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["total"] == {
        "calls": 5,
        "units": 9,
        "prompt_tokens": 177,
        "completion_tokens": 58,
        "total_tokens": 235,
    }
    # calls desc：openai 3 次在前，bailian / NULL 各 1 次按 provider 定序（NULL 最前）。
    assert data["by_provider"] == [
        {"provider": "openai", "calls": 3, "units": 4, "prompt_tokens": 170, "completion_tokens": 55, "total_tokens": 225},
        {"provider": None, "calls": 1, "units": 1, "prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        {"provider": "bailian", "calls": 1, "units": 4, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    ]
    by_model = {(row["provider"], row["model"]): row for row in data["by_model"]}
    assert by_model[("openai", "gpt-4o")]["calls"] == 2
    assert by_model[("openai", "gpt-4o")]["units"] == 3
    assert by_model[("openai", "gpt-4o-mini")] == {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "calls": 1,
        "units": 1,
        "prompt_tokens": 20,
        "completion_tokens": 5,
        "total_tokens": 25,
    }
    assert by_model[(None, None)]["calls"] == 1
    # by_model 的合计必须等于 total：两张表来自同一次查询。
    assert sum(row["calls"] for row in data["by_model"]) == data["total"]["calls"]
    assert sum(row["units"] for row in data["by_model"]) == data["total"]["units"]


def test_usage_failed_calls_count_as_calls(monkeypatch):
    """``calls`` 是调用次数，不是成功次数（失败/被拒也占一行流水）。"""
    env = _make_client(
        monkeypatch,
        [
            _row("openai", "gpt-4o"),
            _row("openai", "gpt-4o", units=0, prompt_tokens=None, completion_tokens=None, total_tokens=None),
        ],
    )

    data = env.client.get(f"{PREFIX}/quota/usage", params={"period": "202610"}).json()["data"]

    assert data["total"]["calls"] == 2
    assert data["total"]["units"] == 1
    assert data["total"]["total_tokens"] == 15


def test_usage_empty_period_returns_zeros(monkeypatch):
    env = _make_client(monkeypatch)

    response = env.client.get(f"{PREFIX}/quota/usage", params={"period": "202610"})

    assert response.status_code == 200
    assert response.json()["data"] == {
        "period": "202610",
        "total": dict.fromkeys(_KEYS, 0),
        "by_provider": [],
        "by_model": [],
    }


def test_usage_excludes_other_users(monkeypatch):
    """别人的调用一条都不进合计，且仓储收到的 user_oid 是当前登录人。"""
    env = _make_client(
        monkeypatch,
        [
            _row("openai", "gpt-4o", user_oid=OTHER, units=99),
            _row("openai", "gpt-4o", user_oid=USER, units=2),
        ],
    )

    data = env.client.get(f"{PREFIX}/quota/usage", params={"period": "202610"}).json()["data"]

    assert data["total"]["calls"] == 1
    assert data["total"]["units"] == 2
    assert [row["provider"] for row in data["by_provider"]] == ["openai"]
    assert env.repo.calls[0]["user_oid"] == USER


def test_usage_is_not_swallowed_by_admin_route(monkeypatch):
    """非 admin 打 ``/quota/usage`` 必须是 200。

    ``/quota/{user_oid}`` 要求 admin + oid 形状校验，一旦动态段先匹配就会变成
    403/422；这条用例同时钉住注册顺序与「本人视图不做 admin 门禁」。
    """
    env = _make_client(monkeypatch, [_row("openai", "gpt-4o")], per=("student",))

    response = env.client.get(f"{PREFIX}/quota/usage", params={"period": "202610"})

    assert response.status_code == 200
    assert response.json()["data"]["total"]["calls"] == 1
