"""角色策略的 Redis 版本闸（共享接口 A 的消费侧）。

钉死四件事：

* 版本号没变 → 刷新不碰 ADB（每 5 秒只剩一条 GET）；
* 写策略（``replace_role``）自增版本号 → 别的进程下一轮只读一次库；
* Redis 不可用 → 退回原来的「每 5 秒读库」；
* 管理页的 ``force=True`` 仍然强制回源。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime

import pytest

from db import cache
from services.roles import policy

STAMP = datetime(2026, 10, 1, 12, 0, 0)


class FakeRedis:
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


class FakePolicyRepo:
    """``OracleRolePoliciesRepo`` 的内存版（语义与 SQL 版一致）。"""

    def __init__(self) -> None:
        self.features: dict[tuple[str, str], bool] = {}
        self.quotas: dict[tuple[str, str], int] = {}
        self.calls = 0

    async def list_all(self) -> dict:
        self.calls += 1
        return {
            "features": [
                {
                    "role": role,
                    "feature_key": key,
                    "enabled": enabled,
                    "updated_at": STAMP,
                    "updated_by": "seed",
                }
                for (role, key), enabled in self.features.items()
            ],
            "quotas": [
                {
                    "role": role,
                    "kind": kind,
                    "monthly_limit": limit,
                    "updated_at": STAMP,
                    "updated_by": "seed",
                }
                for (role, kind), limit in self.quotas.items()
            ],
        }

    async def upsert_role(
        self, role: str, features: dict[str, bool], quotas: dict[str, int], actor=None
    ) -> dict:
        self.features = {
            (item_role, key): value
            for (item_role, key), value in self.features.items()
            if item_role != role
        }
        self.quotas = {
            (item_role, kind): value
            for (item_role, kind), value in self.quotas.items()
            if item_role != role
        }
        self.features.update({(role, key): value for key, value in features.items()})
        self.quotas.update({(role, kind): value for kind, value in quotas.items()})
        return {"role": role, "features": features, "quotas": quotas}


@pytest.fixture(autouse=True)
def _clean_state():
    policy.reset_cache()
    cache._reset_for_tests()
    yield
    policy.reset_cache()
    cache._reset_for_tests()


@pytest.fixture
def redis() -> FakeRedis:
    fake = FakeRedis()
    cache._install_client_for_tests(fake)
    return fake


@pytest.fixture
def repo(monkeypatch) -> FakePolicyRepo:
    fake = FakePolicyRepo()
    monkeypatch.setattr(policy, "_repo", lambda: fake)
    return fake


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 版本闸
# ---------------------------------------------------------------------------

def test_unchanged_version_does_not_touch_the_database(
    redis: FakeRedis, repo: FakePolicyRepo
) -> None:
    repo.quotas[("student", "af")] = 5

    run(policy.refresh())
    assert repo.calls == 1

    run(policy.refresh())
    run(policy.refresh())

    assert repo.calls == 1
    assert policy.monthly_limit(["student"], "af") == 5


def test_version_bump_reads_the_database_once(
    redis: FakeRedis, repo: FakePolicyRepo
) -> None:
    run(policy.refresh())
    assert repo.calls == 1

    repo.quotas[("student", "af")] = 42
    run(cache.bump_epoch("roles"))

    run(policy.refresh())
    run(policy.refresh())

    assert repo.calls == 2
    assert policy.monthly_limit(["student"], "af") == 42


def test_redis_unavailable_falls_back_to_reading_every_time(
    redis: FakeRedis, repo: FakePolicyRepo
) -> None:
    redis.fail = True

    run(policy.refresh())
    run(policy.refresh())

    assert repo.calls == 2


def test_five_minute_fallback_rereads_without_a_version_change(
    redis: FakeRedis, repo: FakePolicyRepo
) -> None:
    run(policy.refresh())
    repo.quotas[("student", "af")] = 77

    run(policy.refresh())
    assert repo.calls == 1

    policy._last_full_read = time.monotonic() - (policy.FULL_RELOAD_SECONDS + 1)
    run(policy.refresh())

    assert repo.calls == 2
    assert policy.monthly_limit(["student"], "af") == 77


def test_all_policies_force_bypasses_the_gate(
    redis: FakeRedis, repo: FakePolicyRepo
) -> None:
    run(policy.refresh())
    repo.features[("student", "ai.chat")] = True

    run(policy.all_policies(force=True))

    assert repo.calls == 2
    assert policy.feature_enabled(["student"], "ai.chat") is True


def test_replace_role_bumps_the_version_and_clears_role_keys(
    redis: FakeRedis, repo: FakePolicyRepo
) -> None:
    redis.data["zvms:role:student"] = '{"stale":true}'

    view = run(
        policy.replace_role(
            "student", {key: False for key in policy.FEATURE_KEYS}, {"af": 9, "schedule": 0}
        )
    )

    assert view["quotas"]["af"] == 9
    # 写事件广播：版本 +1（带 TTL），该域缓存前缀被清掉。
    assert run(cache.version("roles")) == 1
    assert redis.ttls["zvms:epoch:roles"] == cache.EPOCH_TTL_SECONDS
    assert "zvms:role:student" not in redis.data

    # 本进程快照已是最新，且记录了新版本 → 下一轮刷新不再读库。
    calls = repo.calls
    run(policy.refresh())
    assert repo.calls == calls
    assert policy.monthly_limit(["student"], "af") == 9
