"""``/api/v1/statistics/activities/grades/{grade}/*`` 的未知年级语义。

回归目标（2026-10 审计 P0）：两个 handler 直接 ``result["percentiles"][grade]``
/ ``result["indicators"][grade]``，任意 grade（拼错、已毕业的年级、大小写不同）
都触发 ``KeyError`` → 500。统计表里没有的年级是**资源不存在**，不是服务端故障；
这里选 404 而不是 422：``grade`` 是路径里的资源标识，格式完全合法（任意短字符串
都能是年级），只是没有对应数据；422 留给「参数本身不合法」（如分页越界），
而「暂无数据」用 404 才能让前端区分「查不到」与「你传错了」。

钉死：

* 已知年级 → 200，返回该年级的切片（原样，不包信封）；
* 未知年级 / 表里根本没有该键 / 键不是 dict → 404 + 中文文案，绝不 200 空数据；
* ``get_indicators()`` 返回 ``None`` → 仍是 ``{}``（既有契约不变）。

离线：stats repo 打桩，不连 Oracle。

    cd <repo root> && .venv/bin/python -m pytest tests/test_statistics_grade_lookup.py -q
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import activity_statistics_router
from util.object_id import get_current_user

PREFIX = "/api/v1/statistics/activities"
READER = {"id": "64b7f0c2e1a3b4c5d6e7fb01", "per": ["secretary"]}

PERCENTILES = {
    "2023": {"50%": 12.0, "90%": 40.0},
    "2024": {"50%": 8.0, "90%": 30.0},
}
INDICATORS = {
    "2023": {"mean": 14.5, "median": 12.0},
    "2024": {"mean": 10.0, "median": 8.0},
}

NOT_FOUND_DETAIL = "该年级暂无统计数据"


class _FakeStatsRepo:
    def __init__(self, payload) -> None:
        self.payload = payload
        self.calls = 0

    async def get_indicators(self):
        self.calls += 1
        return self.payload


@pytest.fixture
def api(monkeypatch):
    def _client(payload):
        repo = _FakeStatsRepo(payload)
        monkeypatch.setattr(backends, "get_stats_repo", lambda *a, **k: repo)

        app = FastAPI()
        app.include_router(activity_statistics_router.router, prefix=PREFIX)
        app.dependency_overrides[get_current_user] = lambda: READER
        return TestClient(app), repo

    return _client


def test_known_grade_returns_its_percentiles(api) -> None:
    http, repo = api({"percentiles": PERCENTILES, "indicators": INDICATORS})

    response = http.get(f"{PREFIX}/grades/2023/percentiles")

    assert response.status_code == 200, response.text
    assert response.json() == PERCENTILES["2023"]
    assert repo.calls == 1


def test_known_grade_returns_its_indicators(api) -> None:
    http, _repo = api({"percentiles": PERCENTILES, "indicators": INDICATORS})

    response = http.get(f"{PREFIX}/grades/2024/indicators")

    assert response.status_code == 200, response.text
    assert response.json() == INDICATORS["2024"]


@pytest.mark.parametrize("grade", ["2099", "abc", "2023%20"])
def test_unknown_grade_is_404_not_500(api, grade) -> None:
    http, _repo = api({"percentiles": PERCENTILES, "indicators": INDICATORS})

    for suffix in ("percentiles", "indicators"):
        response = http.get(f"{PREFIX}/grades/{grade}/{suffix}")

        assert response.status_code == 404, (suffix, response.text)
        assert response.json() == {"detail": NOT_FOUND_DETAIL}
        assert response.status_code != 200


def test_missing_section_is_404(api) -> None:
    """表里只有 indicators，没有 percentiles：未知段同样 404，不是 KeyError。"""
    http, _repo = api({"indicators": INDICATORS})

    response = http.get(f"{PREFIX}/grades/2023/percentiles")

    assert response.status_code == 404, response.text
    assert response.json() == {"detail": NOT_FOUND_DETAIL}


@pytest.mark.parametrize("section", [None, [], "2023"])
def test_non_mapping_section_is_404(api, section) -> None:
    """坏数据（NULL / 列表 / 字符串）退化成 404，而不是 TypeError → 500。"""
    http, _repo = api({"percentiles": section, "indicators": section})

    for suffix in ("percentiles", "indicators"):
        response = http.get(f"{PREFIX}/grades/2023/{suffix}")

        assert response.status_code == 404, (suffix, response.text)
        assert response.json() == {"detail": NOT_FOUND_DETAIL}


def test_empty_indicators_table_still_answers_empty_object(api) -> None:
    """``get_indicators() is None`` 保持既有契约：200 + ``{}``。"""
    http, repo = api(None)

    response = http.get(f"{PREFIX}/grades/2023/percentiles")

    assert response.status_code == 200, response.text
    assert response.json() == {}
    assert repo.calls == 1


def test_reader_gate_still_applies_before_the_lookup(api) -> None:
    http, repo = api({"percentiles": PERCENTILES, "indicators": INDICATORS})
    http.app.dependency_overrides[get_current_user] = lambda: {
        "id": "64b7f0c2e1a3b4c5d6e7fb02",
        "per": ["student"],
    }

    response = http.get(f"{PREFIX}/grades/2023/percentiles")

    assert response.status_code == 403
    assert response.json() == {"detail": "权限不足"}
    assert repo.calls == 0
