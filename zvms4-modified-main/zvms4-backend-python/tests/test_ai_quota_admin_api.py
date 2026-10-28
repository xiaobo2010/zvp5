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
from db.repos.protocols import SHARED_OWNER_OID
from routers import ai_router
from services.ai import quota as quota_service
from services.ai.config import DEFAULT_QUOTA, role_quota
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


class _FakeUsersRepo:
    """``UsersRepo`` 的最小替身：admin 额度视图只读 ``roles``。

    映射里没有的 oid 就是「查不到用户」，回 ``None``（不能编造空角色）。
    """

    def __init__(self, roles: dict[str, list[str]] | None = None) -> None:
        self.roles = dict(roles or {})

    async def get_for_auth(self, oid: str):
        if oid not in self.roles:
            return None
        return {"_id": oid, "roles": list(self.roles[oid])}


@pytest.fixture
def api(monkeypatch):
    repo = _FakeQuotaRepo()
    users = _FakeUsersRepo({TARGET_OID: ["department"]})
    log = _FakeLog()
    monkeypatch.setattr(backends, "get_ai_quota_repo", lambda *a, **k: repo)
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: users)

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
    assert data["ocr"]["limit"] == 0
    assert data["chat"]["limit"] == 0
    # student 是 V043 角色策略表里的角色，默认额度为 0（不开放）。
    assert data["ocr"]["source"] == "role_default"
    assert data["chat"]["source"] == "role_default"


def test_admin_reads_other_user_quota(api) -> None:
    client, repo, _log = api
    repo.seed(TARGET_OID, "ocr", used=7, limit=20)

    body = client.get(f"{PREFIX}/quota/{TARGET_OID}").json()

    assert body["code"] == 200
    assert body["data"]["ocr"] == {
        "period": quota_service.period_now(),
        "used": 7,
        "limit": 20,
        # 库里有行：上限是显式值，管理端展示时必须与扣费读到的同一个数。
        "source": "explicit",
    }


def test_admin_default_baseline_comes_from_the_target_users_roles(api) -> None:
    """展示口径要与扣费口径一致：目标用户的 admin 角色 → ocr 500 / chat 1_000_000。

    之前 ``admin_snapshot`` 不带 roles，管理页会回落到 ``DEFAULT_QUOTA``
    （chat 200_000），比真实生效值少一个数量级。
    """
    client, _repo, _log = api
    client.app.dependency_overrides[get_current_user] = lambda: ADMIN
    # 让目标用户是 admin，而不是 fixture 默认的 department。
    users = backends.get_users_repo()
    users.roles[TARGET_OID] = ["admin"]

    data = client.get(f"{PREFIX}/quota/{TARGET_OID}").json()["data"]

    role_limits = role_quota()["admin"]
    assert data["ocr"]["limit"] == role_limits["ocr"]
    assert data["chat"]["limit"] == role_limits["chat"]
    assert data["ocr"]["source"] == "role_default"
    assert data["chat"]["source"] == "role_default"


def test_snapshot_uses_roles_for_the_default_baseline(api) -> None:
    """仓储行不存在时，``snapshot`` 直接吃 ``roles`` 算默认值（服务层契约）。"""
    _client, _repo, _log = api

    data = asyncio.run(quota_service.snapshot(TARGET_OID, roles=["admin"]))

    assert data["ocr"] == {
        "period": quota_service.period_now(),
        "used": 0,
        "limit": role_quota()["admin"]["ocr"],
        "source": "role_default",
    }
    assert data["chat"]["limit"] == role_quota()["admin"]["chat"]
    # 不传 roles（例如查不到用户）时回全局默认，而不是编一个角色。
    fallback = asyncio.run(quota_service.snapshot(TARGET_OID))
    assert fallback["ocr"]["limit"] == DEFAULT_QUOTA["ocr"]
    assert fallback["chat"]["source"] == "global_default"


def test_admin_reads_unknown_user_falls_back_to_defaults(api) -> None:
    client, _repo, _log = api

    body = client.get(f"{PREFIX}/quota/{MISSING_OID}").json()

    assert body["code"] == 200
    assert body["data"]["ocr"]["used"] == 0
    assert body["data"]["ocr"]["limit"] == DEFAULT_QUOTA["ocr"]
    # 查不到用户：按全局默认展示，并标明来源，不能让管理页以为这是角色算出来的。
    assert body["data"]["ocr"]["source"] == "global_default"
    assert body["data"]["chat"]["source"] == "global_default"


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


def test_admin_can_set_site_level_engine_quota(api) -> None:
    """站点级 ``ocr_<engine>`` 也是合法 kind：管理端要能直接给单条引擎设额度。

    这些 kind 由 ``site_consume`` 内部记账；不放行的话，管理员只能改
    ``settings.AI_SITE_QUOTA`` 再重启，管理接口对站点额度等于失效。
    写的必须是**站点哨兵行**（``SHARED_OWNER_OID``），否则只是一条永不生效的
    孤儿记录：``site_consume`` 读的是哨兵行，管理员以为调了额度其实没调。
    """
    client, repo, log = api

    body = client.put(
        f"{PREFIX}/quota/{TARGET_OID}",
        json={"kind": "ocr_tencent", "limit_count": 1000},
    ).json()

    assert body["code"] == 200
    assert body["data"] == {
        "period": quota_service.period_now(),
        "kind": "ocr_tencent",
        "used": 0,
        "limit": 1000,
    }
    period = quota_service.period_now()
    assert repo.rows[(SHARED_OWNER_OID, period, "ocr_tencent")]["limit_count"] == 1000
    # 目标用户身上不该多出任何站点 kind 的行。
    assert (TARGET_OID, period, "ocr_tencent") not in repo.rows
    assert log.entries[-1]["type"] == "ai_quota_set"


def test_site_level_quota_does_not_leak_into_the_user_view(api) -> None:
    client, _repo, _log = api

    client.put(
        f"{PREFIX}/quota/{TARGET_OID}",
        json={"kind": "ocr_rapidocr", "limit_count": 1234},
    )

    data = client.get(f"{PREFIX}/quota/{TARGET_OID}").json()["data"]

    # 管理端的用户视图只有用户级两种能力：站点额度不是「这个人的额度」。
    assert set(data) == {"ocr", "chat"}
    assert data["ocr"]["limit"] == DEFAULT_QUOTA["ocr"]


def test_admin_can_reset_site_level_used(api) -> None:
    """重置站点 kind 同样落在哨兵行上。"""
    client, repo, _log = api
    repo.seed(SHARED_OWNER_OID, "ocr_tencent", used=42, limit=1000)

    body = client.post(
        f"{PREFIX}/quota/{TARGET_OID}/reset", json={"kind": "ocr_tencent"}
    ).json()

    assert body["data"] == {
        "period": quota_service.period_now(),
        "kind": "ocr_tencent",
        "used": 0,
        "limit": 1000,
    }
    assert repo.rows[(SHARED_OWNER_OID, quota_service.period_now(), "ocr_tencent")][
        "used"
    ] == 0


def test_site_level_set_limit_is_what_site_consume_reads(api) -> None:
    """设完站点额度后 ``site_consume`` 必须认新上限（否则「永不生效」又回来了）。"""
    client, _repo, _log = api

    client.put(
        f"{PREFIX}/quota/{TARGET_OID}",
        json={"kind": "ocr_groq", "limit_count": 1},
    )

    allowed, used, limit = asyncio.run(quota_service.site_consume("groq", 1))
    assert (allowed, used, limit) == (True, 1, 1)
    allowed, used, limit = asyncio.run(quota_service.site_consume("groq", 1))
    assert (allowed, used, limit) == (False, 1, 1)


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
