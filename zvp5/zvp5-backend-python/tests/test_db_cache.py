"""Redis 读穿缓存（db/cache.py + db/cached_repos.py）的单元测试。

不连真实 Redis：用内存版客户端注入，覆盖序列化、命中/未命中、故障降级、
epoch 失效与代理层的写入失效。真实 Redis 的连通性由部署 smoke 覆盖。
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from decimal import Decimal

import pytest

from db import cache
from db.cached_repos import (
    CachedAIConversationsRepo,
    CachedActivitiesRepo,
    CachedGroupsRepo,
    CachedUsersRepo,
)

OID = "0123456789abcdef01234567"
OTHER = "fedcba9876543210fedcba98"


class FakeClient:
    """够用的 async Redis 替身：字符串 value + px。"""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}
        self.fail = False
        self.calls = {"get": 0, "set": 0, "delete": 0, "incr": 0}

    def _check(self) -> None:
        if self.fail:
            raise ConnectionError("redis down")

    async def get(self, key: str):
        self._check()
        self.calls["get"] += 1
        return self.data.get(key)

    async def set(self, key: str, value: str, px: int | None = None):
        self._check()
        self.calls["set"] += 1
        self.data[key] = value
        self.ttls[key] = px
        return True

    async def delete(self, *keys: str) -> int:
        self._check()
        self.calls["delete"] += 1
        removed = 0
        for key in keys:
            removed += 1 if self.data.pop(key, None) is not None else 0
        return removed

    async def incr(self, key: str) -> int:
        self._check()
        self.calls["incr"] += 1
        value = int(self.data.get(key, "0")) + 1
        self.data[key] = str(value)
        return value

    async def aclose(self) -> None:
        return None


@pytest.fixture(autouse=True)
def fake_cache() -> FakeClient:
    client = FakeClient()
    cache._install_client_for_tests(client)
    yield client
    cache._reset_for_tests()


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 序列化
# ---------------------------------------------------------------------------

def test_roundtrip_preserves_types() -> None:
    value = {
        "_id": OID,
        "name": "张三",
        "ban_until": datetime(2026, 10, 1, 12, 30, 45),
        "activity_date": date(2026, 9, 30),
        "hours": Decimal("3.5"),
        "roles": ["admin", "department"],
        "nested": {"ok": True, "missing": None},
    }
    restored = cache._decode(cache._encode(value))
    assert restored["ban_until"] == value["ban_until"]
    assert restored["activity_date"] == value["activity_date"]
    assert restored["hours"] == Decimal("3.5")
    assert restored["roles"] == ["admin", "department"]
    assert restored["nested"] == {"ok": True, "missing": None}


def test_roundtrip_tolerates_plain_strings() -> None:
    assert cache._decode(cache._encode({"$dt": "not-a-tag"})) == {"$dt": "not-a-tag"}


# ---------------------------------------------------------------------------
# remember / forget / epoch
# ---------------------------------------------------------------------------

def test_remember_hits_cache(fake_cache: FakeClient) -> None:
    calls = {"n": 0}

    async def loader():
        calls["n"] += 1
        return {"v": 1}

    async def main():
        first = await cache.remember("k", 30, loader)
        second = await cache.remember("k", 30, loader)
        return first, second

    assert run(main()) == ({"v": 1}, {"v": 1})
    assert calls["n"] == 1
    assert fake_cache.ttls["k"] == 30000


def test_remember_does_not_cache_none() -> None:
    calls = {"n": 0}

    async def loader():
        calls["n"] += 1
        return None

    async def main():
        await cache.remember("k", 30, loader)
        await cache.remember("k", 30, loader)

    run(main())
    assert calls["n"] == 2


def test_remember_fails_open(fake_cache: FakeClient) -> None:
    fake_cache.fail = True

    async def loader():
        return {"v": 2}

    assert run(cache.remember("k", 30, loader)) == {"v": 2}
    assert cache._degraded_until > 0


def test_forget_and_epoch(fake_cache: FakeClient) -> None:
    async def main():
        await cache.remember("k", 30, _const(1))
        await cache.forget("k")
        assert "k" not in fake_cache.data
        assert await cache.epoch("users") == 0
        await cache.bump_epoch("users")
        assert await cache.epoch("users") == 1

    run(main())


def _const(value):
    async def loader():
        return value

    return loader


# ---------------------------------------------------------------------------
# 代理层
# ---------------------------------------------------------------------------

class FakeUsersRepo:
    def __init__(self) -> None:
        self.calls = {"get_by_oid": 0, "get_for_auth": 0, "get_names": 0, "list_by_class": 0}
        self.doc = {"_id": OID, "name": "张三", "roles": []}
        self.auth = {"_id": OID, "roles": []}

    async def get_by_oid(self, oid: str):
        self.calls["get_by_oid"] += 1
        return dict(self.doc)

    async def get_for_auth(self, oid: str):
        self.calls["get_for_auth"] += 1
        return dict(self.auth)

    async def get_names(self, oids):
        self.calls["get_names"] += 1
        return {oid: "张三" for oid in oids}

    async def list_by_class(self, class_oid: str):
        self.calls["list_by_class"] += 1
        return [dict(self.doc)]

    async def set_password(self, oid: str, password_hash: str | None) -> None:
        self.doc["password"] = password_hash

    async def create(self, *args, **kwargs) -> str:
        return OID


def test_users_repo_caches_reads() -> None:
    inner = FakeUsersRepo()
    repo = CachedUsersRepo(inner)

    async def main():
        await repo.get_by_oid(OID)
        await repo.get_by_oid(OID)
        await repo.get_for_auth(OID)
        await repo.get_for_auth(OID)
        await repo.get_names([OID, OTHER])
        await repo.get_names([OTHER, OID])  # 顺序不同 = 同一个 key

    run(main())
    assert inner.calls["get_by_oid"] == 1
    assert inner.calls["get_for_auth"] == 1
    assert inner.calls["get_names"] == 1


def test_users_repo_write_invalidates() -> None:
    inner = FakeUsersRepo()
    repo = CachedUsersRepo(inner)

    async def main():
        await repo.get_by_oid(OID)
        await repo.set_password(OID, "new-hash")
        await repo.get_by_oid(OID)

    run(main())
    assert inner.calls["get_by_oid"] == 2


def test_users_repo_names_epoch_bump() -> None:
    inner = FakeUsersRepo()
    repo = CachedUsersRepo(inner)

    async def main():
        await repo.get_names([OID])
        await repo.create("1001", "新生", ["student"], None, None)
        await repo.get_names([OID])

    run(main())
    assert inner.calls["get_names"] == 2


def test_session_passed_bypasses_cache() -> None:
    inner = FakeUsersRepo()
    repo = CachedUsersRepo(inner, use_cache=False)

    async def main():
        await repo.get_by_oid(OID)
        await repo.get_by_oid(OID)

    run(main())
    assert inner.calls["get_by_oid"] == 2


class FakeGroupsRepo:
    def __init__(self) -> None:
        self.calls = 0

    async def get(self, oid: str):
        self.calls += 1
        return {"_id": oid, "name": "1 班"}

    async def rename(self, oid: str, name: str) -> None:
        return None


def test_groups_repo_invalidates_on_write() -> None:
    inner = FakeGroupsRepo()
    repo = CachedGroupsRepo(inner)

    async def main():
        await repo.get(OID)
        await repo.get(OID)
        await repo.rename(OID, "2 班")
        await repo.get(OID)

    run(main())
    assert inner.calls == 2


class FakeActivitiesRepo:
    def __init__(self, status: str = "pending") -> None:
        self.calls = 0
        self.status = status
        self.cas_calls: list[tuple[str, str, dict]] = []

    async def get(self, oid: str):
        self.calls += 1
        return {"_id": oid, "status": self.status}

    async def list_effective(self, date_start=None, date_end=None):
        self.calls += 1
        return []

    async def cas_status(self, oid: str, expected: str, fields: dict) -> bool:
        self.cas_calls.append((oid, expected, fields))
        if self.status != expected:
            return False
        self.status = fields["status"]
        return True


def test_activities_effective_only_cached_without_range() -> None:
    inner = FakeActivitiesRepo()
    repo = CachedActivitiesRepo(inner)

    async def main():
        await repo.list_effective()
        await repo.list_effective()
        await repo.list_effective(date_start="2026-01-01", date_end="2026-02-01")
        await repo.list_effective(date_start="2026-01-01", date_end="2026-02-01")

    run(main())
    assert inner.calls == 1 + 2


def test_activities_cas_invalidates_detail_cache_for_the_next_review() -> None:
    """第一次审核成功后，详情缓存必须失效，第二次审核才读得到新状态。

    审核路由是「get → CAS」：若 ``cas_status`` 不失效 ``activity:{oid}``，
    第二次审核会拿缓存里的旧状态当 ``expected``，CAS 落空 → 409
    「状态已被其他操作修改」——同一页面短时间内连续审核必然失败。
    """
    inner = FakeActivitiesRepo(status="pending")
    repo = CachedActivitiesRepo(inner)

    async def main():
        first = await repo.get(OID)  # 模拟打开审核页，缓存 pending
        changed = await repo.cas_status(OID, first["status"], {"status": "effective"})
        second = await repo.get(OID)  # 第二次审核读到的必须是 effective
        return first, changed, second, inner

    first, changed, second, inner = run(main())
    assert first["status"] == "pending"
    assert changed is True
    assert inner.status == "effective"
    assert second["status"] == "effective"
    assert inner.calls == 2  # 缓存命中一次 + 失效后回源一次


def test_activities_failed_cas_also_drops_the_stale_detail_cache() -> None:
    """CAS 落空说明缓存里的 expected 已过期：失败也要失效，重试才会读到真实值。"""
    inner = FakeActivitiesRepo(status="pending")
    repo = CachedActivitiesRepo(inner)

    async def main():
        await repo.get(OID)  # 缓存 pending
        inner.status = "effective"  # 另一个审核者已先写库（缓存未同步）
        changed = await repo.cas_status(OID, "pending", {"status": "refused"})
        after = await repo.get(OID)
        return changed, after, inner

    changed, after, inner = run(main())
    assert changed is False
    assert after["status"] == "effective"
    assert inner.calls == 2


class FakeConversationsRepo:
    def __init__(self) -> None:
        self.calls = 0

    async def get(self, oid: str, owner_oid: str):
        self.calls += 1
        return {"_id": oid, "owner_oid": owner_oid}

    async def latest_by_scene(self, owner_oid: str, scene: str):
        return {"_id": OID, "owner_oid": owner_oid}


def test_conversation_owner_is_checked_after_cache_hit() -> None:
    inner = FakeConversationsRepo()
    repo = CachedAIConversationsRepo(inner)

    async def main():
        mine = await repo.get(OID, OID)
        theirs = await repo.get(OID, OTHER)  # 同一 key，命中别人的行
        again = await repo.get(OID, OTHER)
        return mine, theirs, again

    mine, theirs, again = run(main())
    assert mine == {"_id": OID, "owner_oid": OID}
    assert theirs is None and again is None
    assert inner.calls == 1


class FakeGroupsRepoWithArgs:
    def __init__(self) -> None:
        self.seen: tuple = ()

    async def list_classes(self, *args, **kwargs):
        self.seen = args
        return [], 0


def test_list_classes_does_not_invent_a_size_default() -> None:
    inner = FakeGroupsRepoWithArgs()
    repo = CachedGroupsRepo(inner)

    run(repo.list_classes())
    # 代理必须原样透传（两参），仓储自己的 _CLASS_LIST_CAP 才作数。
    assert inner.seen == ("", 1)
