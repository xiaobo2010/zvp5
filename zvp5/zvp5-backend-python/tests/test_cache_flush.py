"""``db/cache.py`` 的批量失效面：``flush_prefix`` / ``flush_scopes`` / ``version``。

契约（共享接口 A）：

* ``flush_prefix`` 只删 ``zvms:<prefix>`` 开头的 key，用 SCAN + 批量 UNLINK，
  没有 ``UNLINK`` 时退回 ``DELETE``；
* ``flush_scopes`` 按合同里的域映射失效，并自增该域 epoch；未知域抛
  ``ValueError`` 且**一个 key 都不删**；
* 所有 epoch key 都带 30 天 TTL（不允许永久 key）。

不连真实 Redis：内存替身 ``FakeClient``。
"""

from __future__ import annotations

import asyncio
import fnmatch

import pytest

from db import cache


class FakeClient:
    """``scan_iter`` / ``unlink`` / ``delete`` / ``incr`` / ``expire`` 的内存版。"""

    def __init__(self, *, with_unlink: bool = True) -> None:
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int | None] = {}
        self.fail = False
        self.batches: list[list[str]] = []
        if with_unlink:
            self.unlink = self._unlink

    def _check(self) -> None:
        if self.fail:
            raise ConnectionError("redis down")

    async def _unlink(self, *keys: str) -> int:
        self._check()
        self.batches.append(list(keys))
        removed = 0
        for key in keys:
            removed += 1 if self.data.pop(key, None) is not None else 0
        return removed

    async def delete(self, *keys: str) -> int:
        self._check()
        self.batches.append(list(keys))
        removed = 0
        for key in keys:
            removed += 1 if self.data.pop(key, None) is not None else 0
        return removed

    async def scan_iter(self, match: str | None = None, count: int | None = None):
        self._check()
        for key in list(self.data):
            if match is None or fnmatch.fnmatchcase(key, match):
                yield key

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


@pytest.fixture
def client() -> FakeClient:
    fake = FakeClient()
    cache._install_client_for_tests(fake)
    yield fake
    cache._reset_for_tests()


def run(coro):
    return asyncio.run(coro)


def test_flush_prefix_only_touches_matching_prefix(client: FakeClient) -> None:
    client.data.update(
        {
            "zvms:user:aaa": "1",
            "zvms:user:bbb": "2",
            "zvms:auth:aaa": "3",
            "zvms:names:0:deadbeef": "4",
        }
    )

    removed = run(cache.flush_prefix("user"))

    assert removed == 2
    # ``zvms:auth`` 不以 ``zvms:user`` 开头，不该被顺手删掉。
    assert set(client.data) == {"zvms:auth:aaa", "zvms:names:0:deadbeef"}


def test_flush_prefix_deletes_in_batches(
    client: FakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cache, "UNLINK_BATCH", 2)
    client.data.update({f"zvms:conv:{i}": "x" for i in range(5)})

    removed = run(cache.flush_prefix("conv"))

    assert removed == 5
    assert client.data == {}
    # 5 个 key 按 2 个一批：2 + 2 + 1。
    assert [len(batch) for batch in client.batches] == [2, 2, 1]
    assert all(key.startswith("zvms:conv:") for batch in client.batches for key in batch)


def test_flush_prefix_falls_back_to_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeClient(with_unlink=False)
    cache._install_client_for_tests(fake)
    try:
        fake.data.update({"zvms:cfg:a": "1", "zvms:cfg:b": "2"})
        removed = run(cache.flush_prefix("cfg"))
        assert removed == 2
        assert fake.data == {}
    finally:
        cache._reset_for_tests()


def test_flush_prefix_is_a_noop_without_redis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache._reset_for_tests()
    monkeypatch.setattr(cache, "_aioredis", None)
    assert run(cache.flush_prefix("user")) == 0


def test_flush_scopes_uses_the_contract_mapping(client: FakeClient) -> None:
    client.data.update(
        {
            "zvms:user:a": "1",
            "zvms:auth:a": "1",
            "zvms:names:0:d": "1",
            "zvms:class:0:c": "1",
            "zvms:group:g": "1",
            "zvms:epoch:users": "0",
        }
    )

    removed = run(cache.flush_scopes(["users"]))

    assert removed == {"users": 4}
    assert set(client.data) == {"zvms:group:g", "zvms:epoch:users"}
    assert run(cache.epoch("users")) == 1


@pytest.mark.parametrize(
    ("scope", "seeded", "kept"),
    [
        ("groups", ["zvms:group:g", "zvms:pgroup:p", "zvms:glist:0:x", "zvms:pglist:0:x"], "zvms:user:u"),
        ("activities", ["zvms:activity:a", "zvms:activities:effective:0"], "zvms:user:u"),
        ("conversations", ["zvms:conv:c", "zvms:convlatest:o:s"], "zvms:user:u"),
        ("notifications", ["zvms:notif:0:active"], "zvms:user:u"),
        ("changelog", ["zvms:changelog:0:all"], "zvms:user:u"),
        ("settings", ["zvms:cfg:ai.allowed_roles"], "zvms:user:u"),
        ("roles", ["zvms:role:policy:student"], "zvms:user:u"),
    ],
)
def test_flush_scopes_covers_every_documented_domain(
    client: FakeClient, scope: str, seeded: list[str], kept: str
) -> None:
    client.data.update({key: "1" for key in seeded})
    client.data[kept] = "1"

    removed = run(cache.flush_scopes([scope]))

    assert removed == {scope: len(seeded)}
    assert set(client.data) == {kept, f"zvms:epoch:{scope}"}
    assert run(cache.epoch(scope)) == 1


def test_flush_scopes_rejects_unknown_scope(client: FakeClient) -> None:
    client.data.update({"zvms:user:a": "1"})

    with pytest.raises(ValueError):
        run(cache.flush_scopes(["users", "nope"]))

    # 未知域在动手之前就抛：一个 key 都不该少，epoch 也不该动。
    assert set(client.data) == {"zvms:user:a"}


def test_flush_scopes_dedupes_repeated_scope(client: FakeClient) -> None:
    client.data.update({"zvms:user:a": "1"})

    assert run(cache.flush_scopes(["users", "users"])) == {"users": 1}
    assert run(cache.epoch("users")) == 1


def test_epoch_always_gets_a_ttl(client: FakeClient) -> None:
    run(cache.bump_epoch("users"))

    key = "zvms:epoch:users"
    assert client.ttls[key] == cache.EPOCH_TTL_SECONDS == 30 * 24 * 3600
    assert run(cache.epoch("users")) == 1

    run(cache.bump_epoch("users"))
    assert run(cache.epoch("users")) == 2
    assert client.ttls[key] == cache.EPOCH_TTL_SECONDS


def test_bump_epoch_without_redis_is_a_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache._reset_for_tests()
    monkeypatch.setattr(cache, "_aioredis", None)
    run(cache.bump_epoch("users"))
    assert run(cache.epoch("users")) == 0


def test_version_mirrors_epoch_and_reports_unavailability(
    client: FakeClient,
) -> None:
    assert run(cache.version("settings")) == 0
    run(cache.bump_epoch("settings"))
    assert run(cache.version("settings")) == 1

    client.fail = True
    # 读不到时 ``version`` 仍是 0（epoch 语义），``version_or_none`` 才区分得开。
    assert run(cache.version("settings")) == 0
    assert run(cache.version_or_none("settings")) is None
