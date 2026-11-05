"""设置中心的 Redis 版本闸（共享接口 A 的消费侧）。

钉死五件事：

* 版本号没变、且上次回源不到 5 分钟 → 刷新**不碰 ADB**（每 5 秒只剩一条 GET）；
* 别的进程写设置（``cache.flush_scopes(["settings"])``）→ 版本号变 → 本进程
  下一次刷新只读一次库；
* Redis 不可用 → 退回改造前的「每 5 秒读库」，行为不坏；
* 5 分钟强制回源：绕过 API 直接改库也能在 5 分钟内生效；
* 旧 ``ai.role_quota``（V051 之前的 ``ocr`` / ``chat``）归并进 ``af`` 而不是
  每次刷新刷一条 warning。
"""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

from db import backends, cache
from services import settings_runtime


class FakeRedis:
    """版本闸够用的替身：get / incr / expire / scan_iter / unlink。"""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}
        self.fail = False

    def _check(self) -> None:
        if self.fail:
            raise ConnectionError("redis down")

    async def get(self, key: str):
        self._check()
        return self.data.get(key)

    async def incr(self, key: str) -> int:
        self._check()
        value = int(self.data.get(key, "0")) + 1
        self.data[key] = str(value)
        return value

    async def expire(self, key: str, seconds: int) -> bool:
        self._check()
        self.ttls[key] = int(seconds)
        return True

    async def scan_iter(self, match: str | None = None, count: int | None = None):
        self._check()
        for key in list(self.data):
            if match is None or key.startswith(match.rstrip("*")):
                yield key

    async def unlink(self, *keys: str) -> int:
        self._check()
        removed = 0
        for key in keys:
            removed += 1 if self.data.pop(key, None) is not None else 0
        return removed


class FakeSettingsRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.calls = 0
        self.fail = False

    async def all_rows(self):
        self.calls += 1
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
        for key, value in values.items():
            self.rows[key] = {
                "value": value,
                "updated_by": actor_oid,
                "updated_at": None,
            }


class FakeQuotaRepo:
    async def all_rows(self):
        return []


@pytest.fixture(autouse=True)
def _clean_state():
    settings_runtime.reset_cache()
    cache._reset_for_tests()
    yield
    settings_runtime.reset_cache()
    cache._reset_for_tests()


@pytest.fixture
def redis() -> FakeRedis:
    fake = FakeRedis()
    cache._install_client_for_tests(fake)
    return fake


@pytest.fixture
def repo(monkeypatch) -> FakeSettingsRepo:
    fake = FakeSettingsRepo()
    monkeypatch.setattr(backends, "get_system_settings_repo", lambda *a, **k: fake)
    monkeypatch.setattr(
        backends, "get_user_quota_overrides_repo", lambda *a, **k: FakeQuotaRepo()
    )
    return fake


def run(coro):
    return asyncio.run(coro)


def _seed(repo: FakeSettingsRepo, key: str, value) -> None:
    repo.rows[key] = {"value": value, "updated_by": "seed", "updated_at": None}


# ---------------------------------------------------------------------------
# 版本闸
# ---------------------------------------------------------------------------

def test_unchanged_version_does_not_touch_the_database(
    redis: FakeRedis, repo: FakeSettingsRepo
) -> None:
    _seed(repo, "ops.max_query_count", 1234)

    run(settings_runtime.refresh())
    assert repo.calls == 1

    run(settings_runtime.refresh())
    run(settings_runtime.refresh())

    assert repo.calls == 1
    assert settings_runtime.get_sync("ops.max_query_count") == 1234


def test_version_bump_reads_the_database_once(
    redis: FakeRedis, repo: FakeSettingsRepo
) -> None:
    run(settings_runtime.refresh())
    assert repo.calls == 1

    # 别的进程改了库并通过 API 广播：版本号 +1。
    _seed(repo, "ops.max_query_count", 4321)
    run(cache.bump_epoch("settings"))

    run(settings_runtime.refresh())
    run(settings_runtime.refresh())

    assert repo.calls == 2
    assert settings_runtime.get_sync("ops.max_query_count") == 4321


def test_redis_unavailable_falls_back_to_reading_every_time(
    redis: FakeRedis, repo: FakeSettingsRepo
) -> None:
    redis.fail = True

    run(settings_runtime.refresh())
    run(settings_runtime.refresh())

    # 版本读不到 → 闸门禁用 → 与改造前一致：每轮都读库。
    assert repo.calls == 2


def test_five_minute_fallback_rereads_without_a_version_change(
    redis: FakeRedis, repo: FakeSettingsRepo
) -> None:
    run(settings_runtime.refresh())
    assert repo.calls == 1

    # 有人绕过 API 直接改库：版本号不变，只能靠兜底回源。
    _seed(repo, "ops.max_query_count", 9999)
    run(settings_runtime.refresh())
    assert repo.calls == 1

    settings_runtime._last_full_read = time.monotonic() - (
        settings_runtime.FULL_RELOAD_SECONDS + 1
    )
    run(settings_runtime.refresh())

    assert repo.calls == 2
    assert settings_runtime.get_sync("ops.max_query_count") == 9999


def test_forced_refresh_bypasses_the_gate(
    redis: FakeRedis, repo: FakeSettingsRepo
) -> None:
    run(settings_runtime.refresh())
    _seed(repo, "ops.max_query_count", 777)
    # 版本没变，但管理页按 force 口径必须看到库里当前值。
    run(settings_runtime.refresh(force=True))

    assert repo.calls == 2
    assert settings_runtime.get_sync("ops.max_query_count") == 777


def test_set_many_bumps_the_version_and_clears_cfg_keys(
    redis: FakeRedis, repo: FakeSettingsRepo
) -> None:
    redis.data["zvms:cfg:ops.max_query_count"] = '{"stale":true}'
    run(settings_runtime.refresh())
    assert repo.calls == 1
    assert run(cache.version("settings")) == 0

    run(settings_runtime.set_many({"ops.max_query_count": 2048}, "admin"))

    # 写事件广播：版本 +1（带 TTL），该域缓存前缀被清掉。
    assert run(cache.version("settings")) == 1
    assert redis.ttls["zvms:epoch:settings"] == cache.EPOCH_TTL_SECONDS
    assert "zvms:cfg:ops.max_query_count" not in redis.data

    # 本进程快照已是最新，且记录了新版本 → 下一轮刷新不再读库。
    assert settings_runtime.get_sync("ops.max_query_count") == 2048
    run(settings_runtime.refresh())
    assert repo.calls == 1


# ---------------------------------------------------------------------------
# ai.role_quota 的旧 kind 兼容
# ---------------------------------------------------------------------------

def test_legacy_kinds_are_folded_into_af() -> None:
    assert settings_runtime._validate_role_quota(
        {"admin": {"ocr": 100, "chat": 200}}
    ) == {"admin": {"af": 300}}


def test_legacy_kinds_are_ignored_when_af_is_present() -> None:
    assert settings_runtime._validate_role_quota(
        {"admin": {"af": 500, "ocr": 100}}
    ) == {"admin": {"af": 500}}


def test_af_and_legacy_kinds_can_mix_across_roles() -> None:
    assert settings_runtime._validate_role_quota(
        {"admin": {"af": 10}, "department": {"chat": 20, "ocr": 30}}
    ) == {"admin": {"af": 10}, "department": {"af": 50}}


def test_unknown_kind_is_still_rejected() -> None:
    with pytest.raises(ValueError):
        settings_runtime._validate_role_quota({"admin": {"embedding": 10}})


def test_legacy_notice_is_logged_once_per_process(caplog) -> None:
    caplog.set_level(logging.INFO, logger="services.settings_runtime")

    settings_runtime._validate_role_quota({"admin": {"ocr": 1}})
    settings_runtime._validate_role_quota({"admin": {"ocr": 1}})
    settings_runtime._validate_role_quota({"admin": {"ocr": 1}})

    records = [
        record
        for record in caplog.records
        if record.levelno == logging.INFO and "ocr" in record.getMessage()
    ]
    assert len(records) == 1


def test_legacy_row_in_db_no_longer_warns_on_every_refresh(
    redis: FakeRedis, repo: FakeSettingsRepo, caplog
) -> None:
    caplog.set_level(logging.INFO, logger="services.settings_runtime")
    _seed(repo, "ai.role_quota", {"admin": {"ocr": 1200, "chat": 3400}})

    run(settings_runtime.refresh())
    run(cache.bump_epoch("settings"))
    run(settings_runtime.refresh())

    assert settings_runtime.get_sync("ai.role_quota") == {"admin": {"af": 4600}}
    warnings = [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING and "ai.role_quota" in record.getMessage()
    ]
    assert warnings == []
