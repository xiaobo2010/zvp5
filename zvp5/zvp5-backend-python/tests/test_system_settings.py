"""在线设置中心（``services.settings_runtime`` + ``/api/system/settings``）。

钉死五件事：

* 注册表覆盖 v1 白名单的全部键，且默认值**就是**现有代码常量（不是另抄一份）；
* ``set_many`` 先校验后落库，并立刻改写进程内快照（同步读取点马上看到新值）；
* 非法值抛 :class:`SettingsValidationError`，路由层转 400 且 detail 带键名；
* 库读失败回落代码默认值，不抛异常；
* 用户级配额覆盖 → 全局设置 → 硬编码常量的优先级。

另加一条 V036 的离线镜像断言（V036 == models == V001），因为
``python -m db.ddl_check`` 只比对 models 与 V001，不覆盖迁移文件本身。

    cd <repo root> && .venv/bin/python -m pytest tests/test_system_settings.py -q
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends, ddl_check
from db.ids import normalize_oid
from routers import system_settings_router
from services import settings_runtime
from typings.log import inject_log
from util.object_id import get_current_user

ADMIN_OID = "64b7f0c2e1a3b4c5d6e7fa01"
TARGET_OID = "64b7f0c2e1a3b4c5d6e7fa02"

EXPECTED_KEYS = {
    "ai.allowed_roles",
    "ai.rate_limit_per_minute",
    "ai.prompt_capture_roles",
    "ai.prompt_retention_days",
    "ai.prompt_max_chars",
    "ai.context_limit_default",
    # 压缩阈值（0~1 小数）：默认 0.80 是线上行为的一部分，改它要过设置中心。
    "ai.context_compact_ratio",
    "ai.agent_token_budget",
    "ai.agent_max_iterations",
    "ai.role_quota",
    "ai.schedule_quota_department",
    "ai.chat_max_body_bytes",
    "ai.chat_max_messages",
    "ai.chat_max_content_chars",
    "ai.safety_guard_enabled",
    "ai.safety_guard_timeout_seconds",
    "ops.upload_daily_max_files",
    "ops.upload_daily_max_bytes",
    "ops.max_query_count",
    "ops.activity_description_max_length",
    "ops.log_retention_days",
    "ops.export_retention_days",
}


class _FakeSettingsRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.writes: list[tuple[dict, str | None]] = []
        self.fail = False

    async def all_rows(self):
        if self.fail:
            raise RuntimeError("adb down")
        return [
            {
                "setting_key": key,
                "value": row["value"],
                "invalid": False,
                "updated_by": row["updated_by"],
                "updated_at": row["updated_at"],
            }
            for key, row in self.rows.items()
        ]

    async def upsert_many(self, values, actor_oid=None):
        if self.fail:
            raise RuntimeError("adb down")
        self.writes.append((dict(values), actor_oid))
        for key, value in values.items():
            self.rows[key] = {
                "value": value,
                "updated_by": actor_oid,
                "updated_at": datetime.now(),
            }


class _FakeQuotaRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.writes: list[tuple[str, int | None, int | None, str | None]] = []
        self.fail = False

    async def all_rows(self):
        if self.fail:
            raise RuntimeError("adb down")
        return [
            {
                "user_oid": oid,
                "daily_max_files": row["daily_max_files"],
                "daily_max_bytes": row["daily_max_bytes"],
                "updated_by": row["updated_by"],
                "updated_at": row["updated_at"],
            }
            for oid, row in self.rows.items()
        ]

    async def set(self, user_oid, daily_max_files, daily_max_bytes, actor_oid=None):
        if self.fail:
            raise RuntimeError("adb down")
        self.writes.append((user_oid, daily_max_files, daily_max_bytes, actor_oid))
        if daily_max_files is None and daily_max_bytes is None:
            self.rows.pop(user_oid, None)
            return
        self.rows[user_oid] = {
            "daily_max_files": daily_max_files,
            "daily_max_bytes": daily_max_bytes,
            "updated_by": actor_oid,
            "updated_at": datetime.now(),
        }


class _FakeUsersRepo:
    def __init__(self, exists: bool = True) -> None:
        self.exists = exists

    async def get_by_oid(self, oid):
        if not self.exists:
            return None
        return {"_id": oid, "id": oid, "per": ["admin"]}


class _FakeLog:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.types: list[str] = []

    def with_text(self, text: str, type: str = "", level: str = "") -> "_FakeLog":
        self.texts.append(text)
        self.types.append(type)
        return self

    async def insert_log(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _clean_snapshot():
    settings_runtime.reset_cache()
    yield
    settings_runtime.reset_cache()


@pytest.fixture
def repos(monkeypatch):
    settings = _FakeSettingsRepo()
    quotas = _FakeQuotaRepo()
    monkeypatch.setattr(backends, "get_system_settings_repo", lambda *a, **k: settings)
    monkeypatch.setattr(
        backends, "get_user_quota_overrides_repo", lambda *a, **k: quotas
    )
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: _FakeUsersRepo())
    return settings, quotas


async def _allow_reauth(*_args, **_kwargs):
    """本文件只验业务语义；再认证本身由 ``test_admin_reauth_gate`` 专测。"""
    return None


def _api(monkeypatch, settings, quotas) -> tuple[TestClient, _FakeLog]:
    async def _allow(_user):
        return {"_id": ADMIN_OID}

    async def _name(_oid: str) -> str:
        return "管理员"

    monkeypatch.setattr(system_settings_router, "_require_system_admin", _allow)
    monkeypatch.setattr(
        system_settings_router, "require_password_verification", _allow_reauth
    )
    monkeypatch.setattr(system_settings_router, "get_user_display_name", _name)

    log = _FakeLog()
    app = FastAPI()
    app.include_router(system_settings_router.router, prefix="/api/system")
    app.dependency_overrides[get_current_user] = lambda: {
        "_id": ADMIN_OID,
        "id": ADMIN_OID,
        "per": ["admin"],
    }
    app.dependency_overrides[inject_log] = lambda: log
    return TestClient(app), log


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

def test_registry_is_exactly_the_v1_whitelist() -> None:
    assert set(settings_runtime.registry()) == EXPECTED_KEYS


def test_registry_defaults_come_from_the_code_constants() -> None:
    import config

    from services.agent import context as agent_context
    from services.ai import config as ai_config
    from util import attachment

    defaults = {
        spec.key: spec.default for spec in settings_runtime.registry().values()
    }

    assert defaults["ai.allowed_roles"] == list(config.AI_ALLOWED_ROLES)
    assert defaults["ai.rate_limit_per_minute"] == dict(
        config.AI_RATE_LIMIT_PER_MINUTE
    )
    assert defaults["ai.prompt_capture_roles"] == list(config.AI_PROMPT_CAPTURE_ROLES)
    assert defaults["ai.prompt_retention_days"] == config.AI_PROMPT_RETENTION_DAYS
    assert defaults["ai.prompt_max_chars"] == config.AI_PROMPT_MAX_CHARS
    assert defaults["ai.context_limit_default"] == agent_context.DEFAULT_CONTEXT_LIMIT
    assert defaults["ai.context_compact_ratio"] == agent_context.DEFAULT_COMPACT_RATIO
    assert defaults["ai.agent_token_budget"] == ai_config._AGENT_TOKEN_BUDGET
    assert defaults["ai.agent_max_iterations"] == ai_config._AGENT_MAX_ITERATIONS
    assert defaults["ai.role_quota"] == config.AI_ROLE_QUOTA
    assert defaults["ops.upload_daily_max_files"] == attachment.DAILY_MAX_FILES
    assert defaults["ops.upload_daily_max_bytes"] == attachment.DAILY_MAX_BYTES
    assert defaults["ops.max_query_count"] == config.MAX_QUERY_COUNT
    assert (
        defaults["ops.activity_description_max_length"]
        == config.MAX_ACTIVITY_DESCRIPTION_LENGTH
    )
    assert defaults["ops.log_retention_days"] == config.APP_LOGS_RETENTION_DAYS
    assert defaults["ops.export_retention_days"] == config.EXPORT_TASKS_RETENTION_DAYS


def test_every_spec_has_group_label_and_description() -> None:
    for spec in settings_runtime.registry().values():
        assert spec.group in {"ai", "ops"}, spec.key
        assert spec.label and spec.description, spec.key
        # ``float`` 只有压缩阈值一个：值本身是 0~1 的小数，用 int 表达会丢掉精度。
        assert spec.type in {"int", "bool", "str", "list", "dict", "float"}, spec.key


# ---------------------------------------------------------------------------
# set_many 落库 + 缓存失效
# ---------------------------------------------------------------------------

def test_set_many_persists_and_readers_see_it_immediately(repos) -> None:
    settings, _quotas = repos

    changed = asyncio.run(
        settings_runtime.set_many(
            {"ai.rate_limit_per_minute": {"ocr": 3, "chat": 7}}, ADMIN_OID
        )
    )

    assert settings.writes == [
        ({"ai.rate_limit_per_minute": {"ocr": 3, "chat": 7}}, ADMIN_OID)
    ]
    assert changed[0]["key"] == "ai.rate_limit_per_minute"
    assert changed[0]["updated_by"] == ADMIN_OID

    # 同步读取点（服务层真实函数）立刻看到新值，不需要 refresh。
    from services.ai import config as ai_config

    assert ai_config.rate_limit_per_minute("chat") == 7
    assert settings_runtime.get_sync("ai.rate_limit_per_minute")["ocr"] == 3


def test_set_many_rejects_before_writing_anything(repos) -> None:
    settings, _quotas = repos
    import asyncio

    with pytest.raises(settings_runtime.SettingsValidationError) as excinfo:
        asyncio.run(
            settings_runtime.set_many(
                {
                    "ai.prompt_retention_days": 7,
                    "ai.prompt_max_chars": 0,
                },
                ADMIN_OID,
            )
        )

    assert excinfo.value.key == "ai.prompt_max_chars"
    assert settings.writes == []
    assert settings_runtime.get_override("ai.prompt_retention_days") is None


# ---------------------------------------------------------------------------
# 非法值
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("ai.prompt_retention_days", 0),
        ("ai.prompt_retention_days", "30"),
        ("ai.agent_max_iterations", 10_000),
        ("ai.allowed_roles", []),
        ("ai.rate_limit_per_minute", {"vision": 10}),
        ("ai.role_quota", {"admin": {"image": 1}}),
        ("ops.upload_daily_max_files", -1),
        ("ai.unknown_key", 1),
    ],
)
def test_invalid_values_are_rejected_with_the_key(key, value, repos) -> None:
    import asyncio

    with pytest.raises(settings_runtime.SettingsValidationError) as excinfo:
        asyncio.run(settings_runtime.set_many({key: value}, ADMIN_OID))
    assert excinfo.value.key == key
    assert excinfo.value.reason


def test_put_settings_rejects_invalid_value_with_400(repos, monkeypatch) -> None:
    settings, quotas = repos
    http, log = _api(monkeypatch, settings, quotas)

    response = http.put(
        "/api/system/settings",
        json={"items": [{"key": "ai.prompt_retention_days", "value": 0}]},
    )

    assert response.status_code == 400, response.text
    assert "ai.prompt_retention_days" in response.json()["detail"]
    assert settings.writes == []
    assert log.texts == []


def test_put_settings_unknown_key_is_400(repos, monkeypatch) -> None:
    settings, quotas = repos
    http, _log = _api(monkeypatch, settings, quotas)

    response = http.put(
        "/api/system/settings",
        json={"items": [{"key": "ai.nope", "value": 1}]},
    )

    assert response.status_code == 400, response.text
    assert "ai.nope" in response.json()["detail"]


def test_put_settings_writes_audit_log(repos, monkeypatch) -> None:
    settings, quotas = repos
    http, log = _api(monkeypatch, settings, quotas)

    response = http.put(
        "/api/system/settings",
        json={"items": [{"key": "ops.max_query_count", "value": 40}]},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["items"][0]["value"] == 40
    assert log.types == ["system_settings_update"]
    assert "ops.max_query_count" in log.texts[0]
    assert "30" in log.texts[0] and "40" in log.texts[0]


def test_get_settings_returns_items_and_defaults(repos, monkeypatch) -> None:
    settings, quotas = repos
    http, _log = _api(monkeypatch, settings, quotas)

    asyncio.run(
        settings_runtime.set_many({"ops.max_query_count": 40}, ADMIN_OID)
    )

    response = http.get("/api/system/settings")
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    items = {item["key"]: item for item in data["items"]}

    assert set(items) == EXPECTED_KEYS
    assert items["ops.max_query_count"]["value"] == 40
    assert items["ops.max_query_count"]["updated_by"] == ADMIN_OID
    # 未改的键回落到默认值，且 defaults 永远给代码常量。
    assert items["ai.prompt_retention_days"]["value"] == 30
    assert data["defaults"]["ops.max_query_count"] == 30


# ---------------------------------------------------------------------------
# 库失败回落默认值
# ---------------------------------------------------------------------------

def test_db_failure_falls_back_to_defaults(repos) -> None:
    import asyncio

    settings, quotas = repos
    asyncio.run(
        settings_runtime.set_many({"ops.max_query_count": 99}, ADMIN_OID)
    )
    settings.fail = True
    quotas.fail = True

    asyncio.run(settings_runtime.refresh())  # 不抛

    assert settings_runtime.get_override("ops.max_query_count") is None
    assert settings_runtime.get_sync("ops.max_query_count") == 30
    assert settings_runtime.effective_daily_limits(TARGET_OID) == (50, 100 * 1024 * 1024)


def test_db_failure_at_first_load_does_not_raise(repos) -> None:
    import asyncio

    settings, quotas = repos
    settings.fail = True
    quotas.fail = True

    data = asyncio.run(settings_runtime.all_settings(force=True))

    assert len(data["items"]) == len(EXPECTED_KEYS)
    assert all(
        item["value"] == data["defaults"][item["key"]] for item in data["items"]
    )


# ---------------------------------------------------------------------------
# 用户覆盖优先级
# ---------------------------------------------------------------------------

def test_user_override_beats_global_setting(repos) -> None:
    import asyncio

    from util import attachment

    assert asyncio.run(
        settings_runtime.set_many(
            {"ops.upload_daily_max_files": 2, "ops.upload_daily_max_bytes": 2048},
            ADMIN_OID,
        )
    )
    assert attachment.effective_daily_limits(TARGET_OID) == (2, 2048)

    # 只覆盖文件数：字节数仍继承全局。
    data = asyncio.run(
        settings_runtime.set_user_quota(TARGET_OID, 5, None, ADMIN_OID)
    )
    assert data["daily_max_files"] == 5
    assert data["daily_max_bytes"] is None
    assert data["effective"] == {"daily_max_files": 5, "daily_max_bytes": 2048}
    assert attachment.effective_daily_limits(TARGET_OID) == (5, 2048)
    # 别的用户不受影响。
    assert attachment.effective_daily_limits(ADMIN_OID) == (2, 2048)

    # 两列都 None = 清除覆盖、回到全局。
    cleared = asyncio.run(
        settings_runtime.set_user_quota(TARGET_OID, None, None, ADMIN_OID)
    )
    assert cleared["daily_max_files"] is None
    assert cleared["effective"] == {"daily_max_files": 2, "daily_max_bytes": 2048}
    assert settings_runtime.user_quota_override(TARGET_OID) is None


def test_user_override_wins_over_hardcoded_default_without_global_override(repos) -> None:
    import asyncio

    from util import attachment

    asyncio.run(settings_runtime.set_user_quota(TARGET_OID, 1, 10, ADMIN_OID))
    assert attachment.effective_daily_limits(TARGET_OID) == (1, 10)
    assert attachment.effective_daily_limits(ADMIN_OID) == (
        attachment.DAILY_MAX_FILES,
        attachment.DAILY_MAX_BYTES,
    )


def test_check_quota_uses_the_effective_limit(repos) -> None:
    import asyncio

    from util import attachment

    attachment._quota.clear()
    attachment._quota_day = None

    asyncio.run(settings_runtime.set_user_quota(TARGET_OID, 1, None, ADMIN_OID))
    attachment.record_upload(TARGET_OID, 1)
    with pytest.raises(attachment.QuotaExceeded):
        attachment.check_quota(TARGET_OID)

    # 没有覆盖的另一个用户照旧能传。
    attachment.check_quota(ADMIN_OID)


def test_user_quota_routes(repos, monkeypatch) -> None:

    settings, quotas = repos
    http, log = _api(monkeypatch, settings, quotas)

    assert http.get(f"/api/system/user-quotas/{TARGET_OID}").json()["data"] == {
        "user_oid": TARGET_OID,
        "daily_max_files": None,
        "daily_max_bytes": None,
        "effective": {
            "daily_max_files": 50,
            "daily_max_bytes": 100 * 1024 * 1024,
        },
    }

    response = http.put(
        f"/api/system/user-quotas/{TARGET_OID}",
        json={"daily_max_files": 3, "daily_max_bytes": None},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["effective"] == {
        "daily_max_files": 3,
        "daily_max_bytes": 100 * 1024 * 1024,
    }
    assert quotas.writes == [(normalize_oid(TARGET_OID), 3, None, ADMIN_OID)]
    assert log.types == ["system_user_quota_update"]

    cleared = http.put(
        f"/api/system/user-quotas/{TARGET_OID}",
        json={"daily_max_files": None, "daily_max_bytes": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["data"]["daily_max_files"] is None
    assert quotas.rows == {}


def test_user_quota_route_rejects_unknown_user(repos, monkeypatch) -> None:
    settings, quotas = repos
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: _FakeUsersRepo(False))
    http, _log = _api(monkeypatch, settings, quotas)

    response = http.put(
        f"/api/system/user-quotas/{TARGET_OID}",
        json={"daily_max_files": 3},
    )
    assert response.status_code == 404, response.text


def test_user_quota_route_rejects_bad_oid(repos, monkeypatch) -> None:
    settings, quotas = repos
    http, _log = _api(monkeypatch, settings, quotas)

    response = http.get("/api/system/user-quotas/not-an-oid")
    assert response.status_code == 400, response.text


# ---------------------------------------------------------------------------
# V036 离线镜像：迁移 == models == V001
# ---------------------------------------------------------------------------

def test_v036_migration_mirrors_models_and_v001() -> None:
    ddl_dir = ddl_check.DDL_PATH.parent
    migration = ddl_check.parse_schema(
        ddl_check.extract_execute_immediate(
            (ddl_dir / "V036__system_settings.sql").read_text(encoding="utf-8")
        )
    )
    modeled = ddl_check.parse_models()
    v001 = ddl_check.parse_v001()

    assert set(migration["tables"]) == {"system_settings", "user_quota_overrides"}
    for table in ("system_settings", "user_quota_overrides"):
        assert migration["tables"][table] == modeled["tables"][table], table
        assert v001["tables"][table] == modeled["tables"][table], table

    audit_index = ("ai_prompt_audit", False, ("created_at",))
    assert audit_index in migration["indexes"]
    assert audit_index in modeled["indexes"]
    assert audit_index in v001["indexes"]
