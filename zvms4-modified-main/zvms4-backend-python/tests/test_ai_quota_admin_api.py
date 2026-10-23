"""``/api/ai/quota`` 的额度读写契约（自助 + admin）。

覆盖四件事：

* 用户查自己 / admin 查别人 / 非 admin 查别人 403；
* 管理员设额走 ``set_limit``，**不动**已用量；设完额度后 ``consume`` 认新上限；
* 重置把 ``used`` 清零并保留 ``limit_count``；行不存在时幂等成功、回落默认额；
* 非法 ``kind`` / 负数 ``limit_count`` 回 400 + 稳定错误码，而不是 500。

全部离线：额度仓储打桩（内存 dict），服务的月份口径、默认额、校验与路由层的
权限/响应包装都是真实代码。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import ai_router
from services.ai import quota as quota_service
from services.ai.config import DEFAULT_QUOTA
from util.object_id import get_current_user

PREFIX = "/api/ai"
ADMIN_OID = "64b7f0c2e1a3b4c5d6e7fa01"
STUDENT_OID = "64b7f0c2e1a3b4c5d6e7fa02"
TARGET_OID = "64b7f0c2e1a3b4c5d6e7fa03"
MISSING_OID = "64b7f0c2e1a3b4c5d6e7fa99"

ADMIN = {"id": ADMIN_OID, "per": ["admin"]}
STUDENT = {"id": STUDENT_OID, "per": ["student"]}


class _FakeQuotaRepo:
    """``AIQuotaRepo`` 的内存实现：行形状与 Oracle 版一致。"""

    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], dict] = {}

    @staticmethod
    def _key(user_oid: str, period: str, kind: str) -> tuple[str, str, str]:
        return (user_oid, period, kind)

    def seed(self, user_oid: str, kind: str, used: int, limit: int) -> None:
        period = quota_service.period_now()
        self.rows[self._key(user_oid, period, kind)] = {
            "_id": "0" * 24,
            "user_oid": user_oid,
            "period": period,
            "kind": kind,
            "used": used,
            "limit_count": limit,
            "updated_at": None,
        }

    async def get(self, user_oid: str, period: str, kind: str):
        row = self.rows.get(self._key(user_oid, period, kind))
        return dict(row) if row is not None else None

    async def consume(
        self, user_oid: str, period: str, kind: str, amount: int, default_limit: int
    ):
        key = self._key(user_oid, period, kind)
        row = self.rows.get(key)
        if row is None:
            row = {
                "_id": "0" * 24,
                "user_oid": user_oid,
                "period": period,
                "kind": kind,
                "used": 0,
                "limit_count": default_limit,
                "updated_at": None,
            }
            self.rows[key] = row
        if row["used"] + amount <= row["limit_count"]:
            row["used"] += amount
            return True, row["used"], row["limit_count"]
        return False, row["used"], row["limit_count"]

    async def set_limit(
        self, user_oid: str, period: str, kind: str, limit_count: int
    ) -> None:
        key = self._key(user_oid, period, kind)
        row = self.rows.get(key)
        if row is None:
            row = {
                "_id": "0" * 24,
                "user_oid": user_oid,
                "period": period,
                "kind": kind,
                "used": 0,
                "limit_count": limit_count,
                "updated_at": None,
            }
            self.rows[key] = row
            return
        row["limit_count"] = limit_count

    async def reset_used(self, user_oid: str, period: str, kind: str) -> int:
        row = self.rows.get(self._key(user_oid, period, kind))
        if row is None:
            return 0
        row["used"] = 0
        return 1


class _FakeLog:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def with_text(self, text: str, type: str = "", level: str = ""):
        self.entries.append({"text": text, "type": type, "level": level})
        return self

    async def insert_log(self) -> None:
        return None


@pytest.fixture
def api(monkeypatch):
    repo = _FakeQuotaRepo()
    log = _FakeLog()
    monkeypatch.setattr(backends, "get_ai_quota_repo", lambda *a, **k: repo)

    app = FastAPI()
    app.include_router(ai_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    app.dependency_overrides[ai_router.inject_log] = lambda: log
    client = TestClient(app)
    return client, repo, log


def _as(client: TestClient, user: dict) -> None:
    client.app.dependency_overrides[get_current_user] = lambda: user


# ---------------------------------------------------------------------------
# 读
# ---------------------------------------------------------------------------


def test_user_reads_own_quota(api) -> None:
    client, _repo, _log = api
    _as(client, STUDENT)

    body = client.get(f"{PREFIX}/quota").json()

    assert body["status"] == "ok"
    assert body["code"] == 200
    data = body["data"]
    assert set(data) == {"ocr", "chat"}
    assert data["ocr"]["used"] == 0
    assert data["ocr"]["limit"] == DEFAULT_QUOTA["ocr"]
    assert data["chat"]["limit"] == DEFAULT_QUOTA["chat"]


def test_admin_reads_other_user_quota(api) -> None:
    client, repo, _log = api
    repo.seed(TARGET_OID, "ocr", used=7, limit=20)

    body = client.get(f"{PREFIX}/quota/{TARGET_OID}").json()

    assert body["code"] == 200
    assert body["data"]["ocr"] == {
        "period": quota_service.period_now(),
        "used": 7,
        "limit": 20,
    }


def test_admin_reads_unknown_user_falls_back_to_defaults(api) -> None:
    client, _repo, _log = api

    body = client.get(f"{PREFIX}/quota/{MISSING_OID}").json()

    assert body["code"] == 200
    assert body["data"]["ocr"]["used"] == 0
    assert body["data"]["ocr"]["limit"] == DEFAULT_QUOTA["ocr"]


def test_non_admin_cannot_read_other_user_quota(api) -> None:
    client, _repo, _log = api
    _as(client, STUDENT)

    response = client.get(f"{PREFIX}/quota/{TARGET_OID}")

    assert response.status_code == 403
    assert response.json()["detail"] == "Permission denied"


# ---------------------------------------------------------------------------
# 设额
# ---------------------------------------------------------------------------


def test_admin_set_limit_keeps_used(api) -> None:
    client, repo, log = api
    repo.seed(TARGET_OID, "ocr", used=3, limit=10)

    body = client.put(
        f"{PREFIX}/quota/{TARGET_OID}", json={"kind": "ocr", "limit_count": 5}
    ).json()

    assert body["code"] == 200
    assert body["data"] == {
        "period": quota_service.period_now(),
        "kind": "ocr",
        "used": 3,
        "limit": 5,
    }
    assert repo.rows[(TARGET_OID, quota_service.period_now(), "ocr")]["used"] == 3
    assert log.entries[-1]["type"] == "ai_quota_set"


def test_set_limit_then_consume_honours_new_ceiling(api) -> None:
    client, _repo, _log = api

    client.put(
        f"{PREFIX}/quota/{TARGET_OID}", json={"kind": "ocr", "limit_count": 2}
    )

    allowed, used, limit = asyncio.run(
        quota_service.consume(TARGET_OID, "ocr", 2)
    )
    assert (allowed, used, limit) == (True, 2, 2)

    allowed, used, limit = asyncio.run(quota_service.consume(TARGET_OID, "ocr", 1))
    assert (allowed, used, limit) == (False, 2, 2)


def test_set_limit_creates_row_for_new_user(api) -> None:
    client, repo, _log = api

    body = client.put(
        f"{PREFIX}/quota/{MISSING_OID}", json={"kind": "chat", "limit_count": 0}
    ).json()

    assert body["data"]["used"] == 0
    assert body["data"]["limit"] == 0
    assert repo.rows[(MISSING_OID, quota_service.period_now(), "chat")][
        "limit_count"
    ] == 0


def test_non_admin_cannot_set_limit(api) -> None:
    client, repo, _log = api
    _as(client, STUDENT)

    response = client.put(
        f"{PREFIX}/quota/{TARGET_OID}", json={"kind": "ocr", "limit_count": 5}
    )

    assert response.status_code == 403
    assert repo.rows == {}


# ---------------------------------------------------------------------------
# 重置
# ---------------------------------------------------------------------------


def test_reset_zeroes_used_and_keeps_limit(api) -> None:
    client, repo, log = api
    repo.seed(TARGET_OID, "chat", used=42, limit=100)

    body = client.post(
        f"{PREFIX}/quota/{TARGET_OID}/reset", json={"kind": "chat"}
    ).json()

    assert body["code"] == 200
    assert body["data"] == {
        "period": quota_service.period_now(),
        "kind": "chat",
        "used": 0,
        "limit": 100,
    }
    assert log.entries[-1]["type"] == "ai_quota_reset"


def test_reset_missing_row_is_idempotent(api) -> None:
    client, repo, _log = api

    response = client.post(
        f"{PREFIX}/quota/{MISSING_OID}/reset", json={"kind": "ocr"}
    )

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["used"] == 0
    assert data["limit"] == DEFAULT_QUOTA["ocr"]
    # 幂等不等于建行：没用量就不该凭空造一条额度记录。
    assert repo.rows == {}


def test_non_admin_cannot_reset(api) -> None:
    client, repo, _log = api
    _as(client, STUDENT)
    repo.seed(TARGET_OID, "ocr", used=1, limit=10)

    response = client.post(
        f"{PREFIX}/quota/{TARGET_OID}/reset", json={"kind": "ocr"}
    )

    assert response.status_code == 403
    assert repo.rows[(TARGET_OID, quota_service.period_now(), "ocr")]["used"] == 1


# ---------------------------------------------------------------------------
# 参数校验
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["image", "", "OCR", "chat "])
def test_set_limit_rejects_bad_kind(api, kind: str) -> None:
    client, _repo, _log = api

    response = client.put(
        f"{PREFIX}/quota/{TARGET_OID}", json={"kind": kind, "limit_count": 5}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "AI_QUOTA_BAD_KIND"


@pytest.mark.parametrize("kind", ["image", ""])
def test_reset_rejects_bad_kind(api, kind: str) -> None:
    client, _repo, _log = api

    response = client.post(
        f"{PREFIX}/quota/{TARGET_OID}/reset", json={"kind": kind}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "AI_QUOTA_BAD_KIND"


@pytest.mark.parametrize("limit_count", [-1, -100])
def test_set_limit_rejects_negative_limit(api, limit_count: int) -> None:
    client, _repo, _log = api

    response = client.put(
        f"{PREFIX}/quota/{TARGET_OID}",
        json={"kind": "ocr", "limit_count": limit_count},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "AI_QUOTA_BAD_LIMIT"


def test_set_limit_accepts_zero(api) -> None:
    client, _repo, _log = api

    response = client.put(
        f"{PREFIX}/quota/{TARGET_OID}", json={"kind": "ocr", "limit_count": 0}
    )

    assert response.status_code == 200
    assert response.json()["data"]["limit"] == 0


def test_bad_oid_is_400(api) -> None:
    client, _repo, _log = api

    response = client.get(f"{PREFIX}/quota/not-an-oid")

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid Object ID"
