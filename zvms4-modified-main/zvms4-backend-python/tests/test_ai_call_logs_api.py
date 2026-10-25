"""``GET /api/ai/logs`` 的冻结契约：admin 门禁、分页数学、过滤、时间闭区间与富化。

端点背后的仓储已经在 ``test_ai_retention`` / ``test_ai_repo_sql`` 里钉过；这里只
钉**路由有没有把参数正确送到仓储、有没有把仓储的结果按契约整形**。仓储是内存桩：
本文件不连库、不出网。

为什么每条都要单独钉：
* 门禁漏了 = 任意用户能读到全站调用流水（谁、用了哪个模型、花了多少额度）；
* 分页参数漏传 = 前端翻页永远拿到第 1 页，而 ``metadata.size`` 看起来是对的；
* 过滤值静默忽略 = 「筛出来是空的」与「条件写错了」长得一模一样；
* 时间端点开区间 = 按秒排查时那一秒的调用凭空消失。
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import settings  # noqa: F401  (services.ai.config 读它，先导入保证顺序一致)
from db import backends
from routers import ai_router
from util.object_id import get_current_user

ADMIN = "000000000000000000000001"
USER = "000000000000000000000003"
OTHER = "000000000000000000000004"

# 三个时刻：早 / 中 / 晚（都是 naive 本地时间，与 CREATED_AT 列口径一致）。
T0 = datetime(2026, 10, 20, 8, 0, 0)
T1 = datetime(2026, 10, 21, 8, 0, 0)
T2 = datetime(2026, 10, 22, 8, 0, 0)


def _row(
    oid: str,
    *,
    user_oid: str = USER,
    capability: str = "chat",
    status: str = "success",
    key_scope: str | None = "shared",
    created_at: datetime = T1,
) -> dict:
    """仓储 ``_row`` 的键集（``_id`` 而不是 ``oid``），值域取真实枚举。"""
    return {
        "_id": oid,
        "user_oid": user_oid,
        "capability": capability,
        "endpoint": "/api/ai/v1/chat/completions",
        "provider": "lab",
        "model": "gpt-x",
        "status": status,
        "http_status": 200 if status == "success" else 502,
        "error_code": None if status == "success" else "AI_UPSTREAM_ERROR",
        "latency_ms": 42,
        "units": 1,
        "input_bytes": 128,
        "request_id": f"req-{oid[:4]}",
        "key_scope": key_scope,
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "attempt_no": None,
        "purpose": None,
        "first_token_ms": None,
        "engine_kind": None,
        "attachment_oid": None,
        "created_at": created_at,
    }


class FakeCallLogsRepo:
    """内存 ``AICallLogsRepo``：只实现本端点用到的两个方法。

    过滤语义与真仓储一致：``None`` = 不过滤，时间边界是闭区间，行序新在前。
    """

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = list(rows or [])
        self.page_calls: list[dict] = []
        self.count_calls: list[dict] = []

    @staticmethod
    def _matching(rows: list[dict], **filters) -> list[dict]:
        def keep(row: dict) -> bool:
            if filters.get("user_oid") is not None:
                if row["user_oid"] != filters["user_oid"]:
                    return False
            if filters.get("capability") is not None:
                if row["capability"] != filters["capability"]:
                    return False
            if filters.get("status") is not None:
                if row["status"] != filters["status"]:
                    return False
            if filters.get("key_scope") is not None:
                if row["key_scope"] != filters["key_scope"]:
                    return False
            if filters.get("request_id") is not None:
                if row["request_id"] != filters["request_id"]:
                    return False
            if filters.get("provider") is not None:
                if row["provider"] != filters["provider"]:
                    return False
            if filters.get("model") is not None:
                if row["model"] != filters["model"]:
                    return False
            if filters.get("purpose") is not None:
                if row["purpose"] != filters["purpose"]:
                    return False
            # 闭区间：端点那一秒要算命中。
            if filters.get("time_start") is not None:
                if row["created_at"] < filters["time_start"]:
                    return False
            if filters.get("time_end") is not None:
                if row["created_at"] > filters["time_end"]:
                    return False
            return True

        return [row for row in rows if keep(row)]

    async def list_page(
        self,
        *,
        user_oid=None,
        capability=None,
        status=None,
        key_scope=None,
        request_id=None,
        provider=None,
        model=None,
        purpose=None,
        time_start=None,
        time_end=None,
        page: int = 1,
        page_size: int = 20,
    ) -> list[dict]:
        filters = {
            "user_oid": user_oid,
            "capability": capability,
            "status": status,
            "key_scope": key_scope,
            "request_id": request_id,
            "provider": provider,
            "model": model,
            "purpose": purpose,
            "time_start": time_start,
            "time_end": time_end,
        }
        self.page_calls.append({**filters, "page": page, "page_size": page_size})
        matched = self._matching(self.rows, **filters)
        matched.sort(key=lambda row: (row["created_at"], row["_id"]), reverse=True)
        start = (page - 1) * page_size
        return [dict(row) for row in matched[start : start + page_size]]

    async def total_count(
        self,
        *,
        user_oid=None,
        capability=None,
        status=None,
        key_scope=None,
        request_id=None,
        provider=None,
        model=None,
        purpose=None,
        time_start=None,
        time_end=None,
    ) -> int:
        filters = {
            "user_oid": user_oid,
            "capability": capability,
            "status": status,
            "key_scope": key_scope,
            "request_id": request_id,
            "provider": provider,
            "model": model,
            "purpose": purpose,
            "time_start": time_start,
            "time_end": time_end,
        }
        self.count_calls.append(filters)
        return len(self._matching(self.rows, **filters))


class FakeUsersRepo:
    """``get_user_display_names`` 的数据源；查不到的 oid 不出现在结果里。"""

    def __init__(self, names: dict[str, str] | None = None) -> None:
        self.names = dict(names or {})

    async def get_names(self, oids: list[str]) -> dict[str, str]:
        return {oid: self.names[oid] for oid in oids if oid in self.names}


def _client(user: dict | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/ai")
    app.dependency_overrides[get_current_user] = lambda: user or {
        "id": ADMIN,
        "per": ["admin"],
    }
    return TestClient(app)


@pytest.fixture
def repo(monkeypatch) -> FakeCallLogsRepo:
    fake = FakeCallLogsRepo()
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: fake)
    return fake


@pytest.fixture
def users(monkeypatch) -> FakeUsersRepo:
    fake = FakeUsersRepo({USER: "张三", OTHER: "李四"})
    monkeypatch.setattr(backends, "get_users_repo", lambda session=None: fake)
    return fake


# ---------------------------------------------------------------------------
# 门禁
# ---------------------------------------------------------------------------


def test_logs_are_admin_only(repo, users):
    repo.rows = [_row("a" * 24)]

    response = _client({"id": USER, "per": []}).get("/api/ai/logs")

    assert response.status_code == 403
    assert response.json() == {"detail": "Permission denied"}
    # 被拒的请求不该碰仓储（否则日志端点会变成未授权用户的计数探针）。
    assert repo.page_calls == []


# ---------------------------------------------------------------------------
# 分页数学
# ---------------------------------------------------------------------------


def test_pagination_is_passed_through_and_total_is_reported(repo, users):
    repo.rows = [_row(f"{i:024x}", created_at=datetime(2026, 10, 20, 8, i)) for i in range(5)]

    response = _client().get("/api/ai/logs", params={"page": 2, "perpage": 2})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["code"] == 200
    # 第 2 页 = 第 3、4 条（新在前），不是第 1 页。
    assert [row["_id"] for row in body["data"]] == [f"{2:024x}", f"{1:024x}"]
    # 总数是**过滤后的总行数**，不是本页行数。
    assert body["metadata"] == {"size": 5}
    assert repo.page_calls[0]["page"] == 2
    assert repo.page_calls[0]["page_size"] == 2
    assert repo.count_calls[0]["user_oid"] is None


def test_defaults_are_page_1_and_20(repo, users):
    response = _client().get("/api/ai/logs")

    assert response.status_code == 200
    assert repo.page_calls[0]["page"] == 1
    assert repo.page_calls[0]["page_size"] == 20


@pytest.mark.parametrize(
    "params",
    [
        {"page": 0},  # 1-based
        {"perpage": 0},
        {"perpage": 101},  # 上限 100
    ],
)
def test_out_of_range_pagination_is_422(repo, users, params):
    """越界是 FastAPI 的校验错误（422），不是本端点的业务错误码。"""
    assert _client().get("/api/ai/logs", params=params).status_code == 422


def test_newest_first(repo, users):
    repo.rows = [
        _row("a" * 24, created_at=T0),
        _row("b" * 24, created_at=T2),
        _row("c" * 24, created_at=T1),
    ]

    body = _client().get("/api/ai/logs").json()

    assert [row["_id"] for row in body["data"]] == ["b" * 24, "c" * 24, "a" * 24]


# ---------------------------------------------------------------------------
# 过滤
# ---------------------------------------------------------------------------


def test_capability_filter(repo, users):
    repo.rows = [
        _row("a" * 24, capability="chat"),
        _row("b" * 24, capability="ocr"),
    ]

    body = _client().get("/api/ai/logs", params={"capability": "ocr"}).json()

    assert [row["_id"] for row in body["data"]] == ["b" * 24]
    assert body["metadata"]["size"] == 1


def test_status_filter(repo, users):
    repo.rows = [
        _row("a" * 24, status="success"),
        _row("b" * 24, status="timeout"),
        _row("c" * 24, status="rejected"),
    ]

    body = _client().get("/api/ai/logs", params={"status": "timeout"}).json()

    assert [row["_id"] for row in body["data"]] == ["b" * 24]


def test_key_scope_filter(repo, users):
    repo.rows = [
        _row("a" * 24, key_scope="personal"),
        _row("b" * 24, key_scope="server"),
        _row("c" * 24, key_scope=None),  # 存量行：不属于任何 scope
    ]

    body = _client().get("/api/ai/logs", params={"key_scope": "server"}).json()

    assert [row["_id"] for row in body["data"]] == ["b" * 24]


def test_filters_combine(repo, users):
    repo.rows = [
        _row("a" * 24, capability="chat", status="success", user_oid=USER),
        _row("b" * 24, capability="chat", status="error", user_oid=USER),
        _row("c" * 24, capability="chat", status="error", user_oid=OTHER),
        _row("d" * 24, capability="ocr", status="error", user_oid=USER),
    ]

    body = _client().get(
        "/api/ai/logs",
        params={"capability": "chat", "status": "error", "user_oid": USER},
    ).json()

    assert [row["_id"] for row in body["data"]] == ["b" * 24]
    assert body["metadata"]["size"] == 1


def test_user_oid_is_normalised_before_reaching_the_repo(repo, users):
    repo.rows = [_row("a" * 24, user_oid=USER)]

    # 大写 hex 也是合法 oid：仓储按小写比较，路由必须先归一化。
    body = _client().get("/api/ai/logs", params={"user_oid": USER.upper()}).json()

    assert [row["_id"] for row in body["data"]] == ["a" * 24]
    assert repo.page_calls[0]["user_oid"] == USER


def test_invalid_user_oid_is_400(repo, users):
    response = _client().get("/api/ai/logs", params={"user_oid": "not-an-oid"})

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid Object ID"}
    assert repo.page_calls == []


@pytest.mark.parametrize(
    "params",
    [
        {"capability": "vision"},
        {"status": "unknown"},
        {"status": "SUCCESS"},  # 枚举区分大小写：库里存的是小写
        {"key_scope": "team"},
    ],
)
def test_invalid_filter_values_are_400_bad_filter(repo, users, params):
    response = _client().get("/api/ai/logs", params=params)

    assert response.status_code == 400
    assert response.json() == {"detail": "AI_LOGS_BAD_FILTER"}
    assert repo.page_calls == []


def test_empty_filter_values_mean_no_filter(repo, users):
    """``?capability=`` 是前端「未选择」的常见形态，不能变成 400 或空结果。"""
    repo.rows = [_row("a" * 24)]

    body = _client().get(
        "/api/ai/logs", params={"capability": "", "status": "", "key_scope": ""}
    ).json()

    assert [row["_id"] for row in body["data"]] == ["a" * 24]
    assert repo.page_calls[0]["capability"] is None
    assert repo.page_calls[0]["status"] is None
    assert repo.page_calls[0]["key_scope"] is None


# ---------------------------------------------------------------------------
# 时间边界（闭区间）与 epoch 转换
# ---------------------------------------------------------------------------


def test_time_bounds_are_inclusive(repo, users):
    start = int(T1.timestamp())
    end = int(T2.timestamp())
    repo.rows = [
        _row("a" * 24, created_at=T0),  # 早于起点
        _row("b" * 24, created_at=T1),  # 恰好等于起点 -> 命中
        _row("c" * 24, created_at=T2),  # 恰好等于终点 -> 命中
        _row("d" * 24, created_at=datetime(2026, 10, 23, 8, 0, 0)),  # 晚于终点
    ]

    body = _client().get(
        "/api/ai/logs", params={"time_start": start, "time_end": end}
    ).json()

    assert [row["_id"] for row in body["data"]] == ["c" * 24, "b" * 24]
    assert body["metadata"]["size"] == 2


def test_epoch_seconds_are_converted_to_naive_local_datetimes(repo, users):
    repo.rows = [_row("a" * 24, created_at=T1)]
    start = int(T1.timestamp())

    _client().get("/api/ai/logs", params={"time_start": start, "time_end": start})

    sent = repo.page_calls[0]
    assert sent["time_start"] == datetime.fromtimestamp(start)
    assert sent["time_end"] == datetime.fromtimestamp(start)
    # 必须是 naive：带 tzinfo 的边界会被当成另一个时区，闭区间就会偏移。
    assert sent["time_start"].tzinfo is None


def test_start_after_end_yields_empty_page_not_error(repo, users):
    """空区间是合法查询（前端清空条件时的中间态），不是 400。"""
    repo.rows = [_row("a" * 24, created_at=T1)]

    body = _client().get(
        "/api/ai/logs",
        params={"time_start": int(T2.timestamp()), "time_end": int(T0.timestamp())},
    ).json()

    assert body["data"] == []
    assert body["metadata"]["size"] == 0


# ---------------------------------------------------------------------------
# 富化与行形状
# ---------------------------------------------------------------------------


def test_user_name_is_enriched_and_unknown_falls_back(repo, users):
    repo.rows = [
        _row("a" * 24, user_oid=USER, created_at=T2),
        _row("b" * 24, user_oid=OTHER, created_at=T1),
        _row("c" * 24, user_oid="f" * 24, created_at=T0),  # 账号已删/查不到
    ]

    body = _client().get("/api/ai/logs").json()

    names = {row["_id"]: row["user_name"] for row in body["data"]}
    assert names == {"a" * 24: "张三", "b" * 24: "李四", "c" * 24: "未知用户"}


def test_row_keeps_the_repository_key_set(repo, users):
    repo.rows = [_row("a" * 24)]

    row = _client().get("/api/ai/logs").json()["data"][0]

    # ``_id`` 是仓储行形状，``oid`` 是对外契约与前端 ``row-key`` 用的名字：
    # 两个同值都给，另加 user_name 富化。
    assert set(row) == {
        "_id",
        "oid",
        "user_oid",
        "capability",
        "endpoint",
        "provider",
        "model",
        "status",
        "http_status",
        "error_code",
        "latency_ms",
        "units",
        "input_bytes",
        "request_id",
        "key_scope",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "attempt_no",
        "purpose",
        "first_token_ms",
        "engine_kind",
        "attachment_oid",
        "created_at",
        "user_name",
    }
    assert row["_id"] == "a" * 24
    assert row["oid"] == "a" * 24
    assert isinstance(row["created_at"], str)  # JSON 里是 ISO 字符串
