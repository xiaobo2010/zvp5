"""测试用的最小 Redis 替身（只覆盖 db.state / db.cache 用到的命令）。

用法::

    from fake_redis import FakeRedis
    from db import cache

    client = FakeRedis()
    cache._install_client_for_tests(client)
    ...
    cache._reset_for_tests()

``tests/conftest.py`` 的 autouse fixture 会在每个用例前后自动 reset，不会串味。
"""

from __future__ import annotations


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.ttl: dict[str, int] = {}
        self.zsets: dict[str, dict[str, float]] = {}
        self.fail = False
        self.calls = {"get": 0, "set": 0, "delete": 0, "incr": 0}

    def _check(self) -> None:
        if self.fail:
            raise ConnectionError("redis down")

    async def get(self, key: str):
        self._check()
        self.calls["get"] += 1
        return self.kv.get(key)

    async def set(self, key: str, value: str, px: int | None = None, nx: bool = False):
        self._check()
        if nx and key in self.kv:
            return None
        self.calls["set"] += 1
        self.kv[key] = value
        self.ttl[key] = px or -1
        return True

    async def delete(self, *keys: str):
        self._check()
        self.calls["delete"] += 1
        removed = 0
        for key in keys:
            removed += 1 if self.kv.pop(key, None) is not None else 0
            # 真 Redis 的 DEL 连 TTL 一起丢；不跟着清会让 pttl 谎报「还活着」。
            self.ttl.pop(key, None)
        return removed

    async def pttl(self, key: str):
        self._check()
        return self.ttl.get(key, -2)

    async def incr(self, key: str):
        self._check()
        self.calls["incr"] += 1
        value = int(self.kv.get(key, "0")) + 1
        self.kv[key] = str(value)
        return value

    async def pexpire(self, key: str, ms: int):
        self._check()
        self.ttl[key] = ms
        return True

    async def zremrangebyscore(self, key: str, low, high):
        self._check()
        zset = self.zsets.get(key, {})
        low_v, high_v = float(low), float(high)
        self.zsets[key] = {
            member: score
            for member, score in zset.items()
            if not (low_v <= score <= high_v)
        }
        return 0

    async def zcard(self, key: str):
        self._check()
        return len(self.zsets.get(key, {}))

    async def zrange(self, key: str, start: int, end: int, withscores: bool = False):
        self._check()
        items = sorted(self.zsets.get(key, {}).items(), key=lambda kv: kv[1])
        sliced = items[start : end + 1] if end >= 0 else items[start:]
        return [(m, s) for m, s in sliced] if withscores else [m for m, _ in sliced]

    async def zadd(self, key: str, mapping: dict[str, float]):
        self._check()
        self.zsets.setdefault(key, {}).update({m: float(s) for m, s in mapping.items()})
        return 1

    async def scan_iter(self, match: str | None = None, count: int | None = None):
        self._check()
        prefix = (match or "").rstrip("*")
        for key in list(self.kv):
            if key.startswith(prefix):
                yield key

    async def aclose(self) -> None:
        return None
