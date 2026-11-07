"""路由层错误边界：非法输入必须是 4xx，约束冲突必须是 409，绝不 500。

覆盖审计里「未知 500 / 越界异常」的九处点位。全部走 TestClient + 内存桩，
不触达 Oracle。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from db import backends
from db.repos.oracle.users import OracleUsersRepo
from routers import (
    activities_v2_router,
    ban_router,
    groups_router,
    groups_v2_router,
    times_router,
    users_router,
    users_v3_router,
)
from services import device_guard
from typings.log import inject_log
from util.object_id import get_current_user

ADMIN = "000000000000000000000001"
TARGET = "000000000000000000000002"
GROUP = "000000000000000000000010"
ACTIVITY = "000000000000000000000020"


class StubLog:
    def with_text(self, *args, **kwargs):
        return self

    async def insert_log(self):
        return None


async def _allow_reauth(*_args, **_kwargs):
    """本文件只验业务语义；再认证本身由 ``test_admin_reauth_gate`` 专测。"""
    return None


@pytest.fixture(autouse=True)
def _no_reauth(monkeypatch):
    monkeypatch.setattr(ban_router, "require_password_verification", _allow_reauth)


def _client(router, prefix: str, user: dict | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix=prefix)
    app.dependency_overrides[get_current_user] = lambda: user or {
        "id": ADMIN,
        "per": ["admin"],
    }
    app.dependency_overrides[inject_log] = lambda: StubLog()
    return TestClient(app)


def _unique_violation() -> IntegrityError:
    return IntegrityError("INSERT", {}, Exception("ORA-00001"))


def test_user_time_rejects_unparsable_bounds(monkeypatch):
    async def boom(*args, **kwargs):
        raise AssertionError("非法时间参数不得进入时间计算")

    monkeypatch.setattr(users_router, "calculate_user_time", boom)
    http = _client(users_router.router, "/api/users")

    response = http.get(
        f"/api/users/{TARGET}/time",
        params={"start": "garbage", "end": "2026-01-01"},
    )
    assert response.status_code == 400
    assert "start" in response.json()["detail"]

    response = http.get(
        f"/api/users/{TARGET}/time",
        params={"start": "2026-01-01", "end": "garbage"},
    )
    assert response.status_code == 400
    assert "end" in response.json()["detail"]


def test_delete_past_rejects_non_numeric_index(monkeypatch):
    async def get_by_oid(oid):
        return {"_id": TARGET, "past": ["20240001"]}

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda *a, **k: SimpleNamespace(get_by_oid=get_by_oid),
    )
    http = _client(users_router.router, "/api/users")

    response = http.delete(f"/api/users/{TARGET}/past/abc")
    assert response.status_code == 400


def test_create_user_duplicate_race_is_conflict(monkeypatch):
    async def find_by_business_id(business_id):
        return None

    async def create(*args, **kwargs):
        raise _unique_violation()

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda *a, **k: SimpleNamespace(
            find_by_business_id=find_by_business_id, create=create
        ),
    )
    http = _client(users_v3_router.router, "/api/v1/users")

    response = http.post(
        "/api/v1/users", json={"id": "20250101", "name": "张三"}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "学号已存在"


def test_ban_rule_unique_race_is_conflict(monkeypatch):
    async def get_by_code(code):
        return None

    async def get_by_trigger(trigger):
        return None

    async def insert(*args, **kwargs):
        raise _unique_violation()

    monkeypatch.setattr(
        backends,
        "get_ban_rules_repo",
        lambda *a, **k: SimpleNamespace(
            get_by_code=get_by_code, get_by_trigger=get_by_trigger, insert=insert
        ),
    )
    http = _client(ban_router.router, "/api/v1/ban")

    response = http.post(
        "/api/v1/ban/rules",
        json={
            "code": "custom_rule",
            "name": "自定义规则",
            "trigger": "platform_misuse",
            "penalties": [1],
        },
    )
    assert response.status_code == 409


def _null_time_row():
    return {
        "_id": "time-1",
        "user": TARGET,
        "name": "张三",
        "id": "20250101",
        "on_campus_raw": None,
        "off_campus_raw": None,
        "social_practice": None,
        "updated_at": None,
    }


def test_times_rows_with_null_columns_do_not_500(monkeypatch):
    async def list_by_search(*args, **kwargs):
        return [_null_time_row()], 1

    monkeypatch.setattr(
        backends,
        "get_time_repo",
        lambda *a, **k: SimpleNamespace(list_by_search=list_by_search),
    )
    http = _client(times_router.router, "/api/times")

    response = http.get("/api/times", params={"query": "张"})
    assert response.status_code == 200
    row = response.json()["data"][0]
    assert row["on-campus"] == 0
    assert row["off-campus"] == 0
    assert row["social-practice"] == 0


def test_group_time_rows_with_null_columns_do_not_500(monkeypatch):
    async def list_by_class(*args, **kwargs):
        return [_null_time_row()], 1

    monkeypatch.setattr(
        backends,
        "get_time_repo",
        lambda *a, **k: SimpleNamespace(list_by_class=list_by_class),
    )
    http = _client(groups_v2_router.router, "/api/v1/groups")

    response = http.get(f"/api/v1/groups/{GROUP}/time")
    assert response.status_code == 200
    row = response.json()["data"][0]
    assert row["on-campus"] == 0
    assert row["off-campus"] == 0
    assert row["social-practice"] == 0


def test_activity_with_missing_fields_is_conflict_not_500(monkeypatch):
    async def get(oid):
        return {"_id": ACTIVITY, "name": "残缺活动"}

    monkeypatch.setattr(
        backends,
        "get_activities_repo",
        lambda *a, **k: SimpleNamespace(get=get),
    )
    http = _client(activities_v2_router.router, "/api/v1/activities")

    response = http.put(
        f"/api/v1/activities/{ACTIVITY}/status", json={"status": "effective"}
    )
    assert response.status_code == 409
    assert response.json()["detail"] == "活动数据不完整，无法完成该操作"


def test_group_times_missing_bulk_entry_defaults_to_zero(monkeypatch):
    users = [{"_id": TARGET, "name": "张三", "id": "20250101", "class_id": GROUP}]

    async def list_by_class(oid):
        return [dict(row) for row in users]

    async def get(oid):
        return {"_id": GROUP, "name": "一班"}

    async def bulk(user_ids, start, end, allow_cache=True):
        return {}

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda *a, **k: SimpleNamespace(list_by_class=list_by_class),
    )
    monkeypatch.setattr(
        backends, "get_groups_repo", lambda *a, **k: SimpleNamespace(get=get)
    )
    monkeypatch.setattr(groups_router, "calculate_user_time_bulk", bulk)
    http = _client(groups_router.router, "/api/groups")

    response = http.get(f"/api/groups/{GROUP}/time")
    assert response.status_code == 200
    row = response.json()["data"][0]
    assert row["on-campus"] == 0
    assert row["off-campus"] == 0
    assert row["social-practice"] == 0


class _Row:
    def __init__(self, mapping):
        self._mapping = mapping


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Lob:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload


class _Session:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, stmt):
        return _Result(self._rows)


@pytest.mark.asyncio
async def test_group_oids_reads_lob_and_survives_bad_json(caplog):
    rows = [
        _Row({"prefix": "2025", "original_ids": _Lob('["a", "b"]')}),
        _Row({"prefix": "2026", "original_ids": "{not-json"}),
        _Row({"prefix": "2027", "original_ids": None}),
    ]
    repo = OracleUsersRepo(_Session(rows))

    with caplog.at_level(logging.WARNING, logger="db.repos.oracle.users"):
        result = await repo.group_oids_by_id_prefix()

    assert result == [
        {"_id": "2025", "originalIds": ["a", "b"]},
        {"_id": "2026", "originalIds": []},
        {"_id": "2027", "originalIds": []},
    ]
    assert any("JSON_ARRAYAGG" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_challenge_row_without_id_is_invalid_not_500(monkeypatch):
    calls = {"increment": 0}

    class Challenges:
        async def get(self, token_hash):
            return {
                "expires_at": datetime.now() + timedelta(minutes=1),
                "consumed_at": None,
            }

        async def increment_attempts(self, oid):
            calls["increment"] += 1

    monkeypatch.setattr(
        backends, "get_device_challenges_repo", lambda *a, **k: Challenges()
    )

    with pytest.raises(HTTPException) as caught:
        await device_guard.verify_temp_password("challenge", "123456")

    assert caught.value.status_code == 403
    assert caught.value.detail["code"] == "TEMP_PASSWORD_INVALID"
    assert calls["increment"] == 0


@pytest.mark.asyncio
async def test_matched_temp_password_without_id_is_invalid(monkeypatch):
    future = datetime.now() + timedelta(minutes=5)

    class Challenges:
        async def get(self, token_hash):
            return {
                "_id": "challenge-1",
                "user_oid": TARGET,
                "xuehai_id": "20250101",
                "expires_at": future,
                "consumed_at": None,
            }

        async def increment_attempts(self, oid):
            return 1

        async def consume(self, oid):
            raise AssertionError("挑战不应被消费")

    class Passwords:
        async def list_active_for_user(self, user_oid, limit=None):
            return [
                {
                    "code_hash": "hash",
                    "expires_at": future,
                    "used_at": None,
                    "revoked_at": None,
                }
            ]

        async def mark_used(self, oid):
            raise AssertionError("没有 _id 的记录不应被消费")

    async def policy():
        return 3, True, {}

    monkeypatch.setattr(
        backends, "get_device_challenges_repo", lambda *a, **k: Challenges()
    )
    monkeypatch.setattr(
        backends, "get_temp_passwords_repo", lambda *a, **k: Passwords()
    )
    monkeypatch.setattr(device_guard, "_check_code", lambda code, hashed: True)
    monkeypatch.setattr(device_guard, "_temp_attempt_policy", policy)

    with pytest.raises(HTTPException) as caught:
        await device_guard.verify_temp_password("challenge", "123456")

    assert caught.value.status_code == 403
    assert caught.value.detail["code"] == "TEMP_PASSWORD_INVALID"
