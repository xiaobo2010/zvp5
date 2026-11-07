"""``/api/v3/ban/violations`` 的离线契约（上报端点 + 管理员证据列表）。

钉住三件事：

* ``POST`` 对**任意登录用户**开放，入参截断、未知信号不 422（由服务层忽略）；
* ``GET`` 只给 admin，行形状与 ``/records`` 同一命名约定（``user_id`` = 学号）；
* 分页参数越界是 422，而不是静默夹到边界。

全部离线：违规仓储与 ``violation_guard.record`` 都打桩，不连 Oracle。
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import ban_violations_router
from services import violation_guard
from util.object_id import get_current_user

PREFIX = "/api/v3/ban"
ADMIN = {"id": "000000000000000000000001", "per": ["admin"]}
STUDENT = {"id": "000000000000000000000002", "per": ["student"]}

CREATED = datetime(2026, 10, 20, 12, 0, 0)

ROWS = [
    {
        "_id": "0000000000000000000000bb",
        "user_oid": STUDENT["id"],
        "user_name": "李四",
        "user_business_id": "2026002",
        "trigger": "platform_misuse",
        "signal": "iframe",
        "xuehai_id": "1234567890",
        "detail": "top!=self",
        "created_at": CREATED,
    },
    {
        "_id": "0000000000000000000000cc",
        "user_oid": "000000000000000000000003",
        "user_name": None,
        "user_business_id": None,
        "trigger": "platform_misuse",
        "signal": "devtools",
        "xuehai_id": None,
        "detail": None,
        "created_at": CREATED,
    },
]


@pytest.fixture
def api(monkeypatch):
    calls = {"pages": [], "records": []}
    log = SimpleNamespace(
        entries=[],
        with_text=lambda text, type="", level="": (
            log.entries.append({"text": text, "type": type, "level": level}) or log
        ),
    )

    async def insert_log():
        return None

    log.insert_log = insert_log

    async def list_page(query, page, size):
        calls["pages"].append((query, page, size))
        return ROWS, len(ROWS)

    async def record(user, signals, xuehai_id=""):
        calls["records"].append({"user": user, "signals": signals, "xuehai": xuehai_id})
        return {
            "recorded": len(signals),
            "duplicates": 0,
            "banned": False,
            "rule_code": "platform_misuse",
        }

    monkeypatch.setattr(
        backends,
        "get_violations_repo",
        lambda *a, **k: SimpleNamespace(list_page=list_page),
        raising=False,
    )
    monkeypatch.setattr(violation_guard, "record", record)

    app = FastAPI()
    app.include_router(ban_violations_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: STUDENT
    app.dependency_overrides[ban_violations_router.inject_log] = lambda: log
    return TestClient(app), calls, log


# ---------------------------------------------------------------------------
# POST /violations
# ---------------------------------------------------------------------------


def test_any_logged_in_user_can_report(api) -> None:
    http, calls, _log = api
    response = http.post(
        f"{PREFIX}/violations",
        json={"signals": [{"signal": "iframe", "detail": "top!=self"}]},
        headers={"Xuehai-Id": "1234567890"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "recorded": 1,
        "duplicates": 0,
        "banned": False,
        "rule_code": "platform_misuse",
    }
    assert calls["records"] == [
        {
            "user": STUDENT,
            "signals": [{"signal": "iframe", "detail": "top!=self"}],
            "xuehai": "1234567890",
        }
    ]


def test_report_truncates_long_detail(api) -> None:
    http, calls, _log = api
    http.post(
        f"{PREFIX}/violations",
        json={"signals": [{"signal": "devtools", "detail": "x" * 900}]},
    )

    detail = calls["records"][0]["signals"][0]["detail"]
    assert len(detail) == violation_guard.MAX_DETAIL_CHARS


def test_unknown_signal_is_not_rejected_by_the_router(api) -> None:
    """信号名拼错不该丢整批：路由原样透传，服务层忽略未知值。"""
    http, calls, _log = api
    response = http.post(
        f"{PREFIX}/violations",
        json={"signals": [{"signal": "screenshot"}, {"signal": "iframe"}]},
    )

    assert response.status_code == 200
    assert [item["signal"] for item in calls["records"][0]["signals"]] == [
        "screenshot",
        "iframe",
    ]


def test_report_writes_audit_log(api) -> None:
    http, _calls, log = api
    http.post(
        f"{PREFIX}/violations",
        json={"signals": [{"signal": "iframe"}]},
    )

    entry = log.entries[-1]
    assert entry["type"] == "platform_misuse_report"
    assert entry["level"] == "info"
    assert "iframe" not in entry["text"]  # 文案只报数量，不重复贴证据
    assert "1" in entry["text"]


def test_empty_batch_is_accepted(api) -> None:
    http, calls, _log = api
    body = http.post(f"{PREFIX}/violations", json={"signals": []}).json()

    assert body["recorded"] == 0
    assert calls["records"][0]["signals"] == []


# ---------------------------------------------------------------------------
# GET /violations
# ---------------------------------------------------------------------------


def test_non_admin_cannot_list_violations(api) -> None:
    http, calls, _log = api
    response = http.get(f"{PREFIX}/violations")

    assert response.status_code == 403
    assert response.json()["detail"] == "权限不足"
    assert calls["pages"] == []


def test_violations_shape_and_metadata(api) -> None:
    http, calls, _log = api
    http.app.dependency_overrides[get_current_user] = lambda: ADMIN

    body = http.get(f"{PREFIX}/violations?query=2026&page=2&perpage=10").json()

    assert body["status"] == "ok"
    assert body["code"] == 200
    assert body["metadata"] == {"size": 2, "page": 2, "perpage": 10}
    assert calls["pages"] == [("2026", 2, 10)]
    assert body["data"][0] == {
        "_id": "0000000000000000000000bb",
        "user_oid": STUDENT["id"],
        "user_name": "李四",
        "user_id": "2026002",
        "trigger": "platform_misuse",
        "signal": "iframe",
        "detail": "top!=self",
        "xuehai_id": "1234567890",
        "created_at": "2026-10-20T12:00:00",
    }
    # 用户行消失后证据仍可见：姓名/学号为 None。
    assert body["data"][1]["user_name"] is None
    assert body["data"][1]["user_id"] is None


@pytest.mark.parametrize("qs", ["page=0", "perpage=0", "perpage=101", "query=" + "x" * 129])
def test_out_of_range_paging_is_422(api, qs) -> None:
    http, calls, _log = api
    http.app.dependency_overrides[get_current_user] = lambda: ADMIN

    assert http.get(f"{PREFIX}/violations?{qs}").status_code == 422
    assert calls["pages"] == []


# ---------------------------------------------------------------------------
# POST /violations —— 入参上界（审计 P0，放大式 DoS）
# ---------------------------------------------------------------------------


def test_batch_of_twenty_signals_is_accepted(api) -> None:
    """上界本身必须放行：批量语义不变，20 条全部原样交给服务层。"""
    http, calls, _log = api
    signals = [{"signal": "iframe", "detail": f"frame-{index}"} for index in range(20)]

    response = http.post(f"{PREFIX}/violations", json={"signals": signals})

    assert response.status_code == 200, response.text
    assert response.json()["recorded"] == 20
    assert calls["records"][0]["signals"] == signals


def test_batch_above_twenty_signals_is_422(api) -> None:
    """21 条直接 422（不静默截断）：不让一次请求放大成任意多次写入。"""
    http, calls, _log = api
    signals = [{"signal": "iframe"} for _ in range(21)]

    response = http.post(f"{PREFIX}/violations", json={"signals": signals})

    assert response.status_code == 422, response.text
    assert calls["records"] == [], "超限批次不得进入服务层"


def test_signal_name_of_64_chars_is_accepted(api) -> None:
    http, calls, _log = api
    signal = "s" * 64

    response = http.post(f"{PREFIX}/violations", json={"signals": [{"signal": signal}]})

    assert response.status_code == 200, response.text
    assert calls["records"][0]["signals"] == [{"signal": signal, "detail": None}]


def test_signal_name_above_64_chars_is_422(api) -> None:
    http, calls, _log = api

    response = http.post(
        f"{PREFIX}/violations", json={"signals": [{"signal": "s" * 65}]}
    )

    assert response.status_code == 422, response.text
    assert calls["records"] == []

