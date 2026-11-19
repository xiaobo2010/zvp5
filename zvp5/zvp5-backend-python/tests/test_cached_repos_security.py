"""封禁事务单元的缓存失效时序与列表查询 key 的隐私回归测试。

两条契约：

1. ``CachedUsersRepo.ban_unit`` 只在**内层提交之后**失效一次。事务内
   （``_use_cache=False`` 的 caller-owned session）的 ``set_ban`` /
   ``set_ban_if_stronger`` / ``clear_ban`` 不得提前 ``FORGET``：提交前删 key
   等于把旧值重新灌进缓存，提交后又没人再删。
2. ``list_classes`` / ``list_permission_groups`` 的 Redis key 只带查询串摘要，
   用户输入的原文（可能是姓名片段、班级名）不落 key 空间。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest

from db import cache
from db.cached_repos import CachedGroupsRepo, CachedUsersRepo

OID = "0123456789abcdef01234567"


@pytest.fixture
def forget_calls(monkeypatch):
    calls: list[tuple[str, ...]] = []

    async def _forget(*keys: str) -> None:
        calls.append(keys)

    monkeypatch.setattr(cache, "forget", _forget)
    return calls


@pytest.fixture
def remembered_keys(monkeypatch):
    keys: list[str] = []

    async def _remember(key, ttl, loader, **_kwargs):
        keys.append(key)
        return await loader()

    monkeypatch.setattr(cache, "remember", _remember)
    return keys


class FakeBanUsersRepo:
    """可控的 ``ban_unit``：commit / rollback 由用例显式观察。"""

    def __init__(self, forget_calls: list[tuple[str, ...]]) -> None:
        self.session = object()
        self.writes: list[tuple[str, str]] = []
        self.commits = 0
        self.rollbacks = 0
        self.forgets_at_commit: int | None = None
        self._forget_calls = forget_calls

    @asynccontextmanager
    async def ban_unit(self, oid: str):
        self.locked = oid
        try:
            yield self.session
        except BaseException:
            self.rollbacks += 1
            raise
        self.forgets_at_commit = len(self._forget_calls)
        self.commits += 1

    async def set_ban(self, oid: str, **_kwargs) -> None:
        self.writes.append(("set_ban", oid))

    async def set_ban_if_stronger(self, oid: str, *, until, **_kwargs):
        self.writes.append(("set_ban_if_stronger", oid))
        return until

    async def clear_ban(self, oid: str, **_kwargs) -> None:
        self.writes.append(("clear_ban", oid))


@pytest.mark.asyncio
async def test_ban_unit_forgets_once_after_inner_commit(forget_calls) -> None:
    inner = FakeBanUsersRepo(forget_calls)
    repo = CachedUsersRepo(inner)
    session_repo = CachedUsersRepo(inner, use_cache=False)

    async with repo.ban_unit(OID) as session:
        assert session is inner.session
        await session_repo.set_ban(
            OID, reason="test", until=None, source="admin", rule_code=None
        )
        await session_repo.clear_ban(OID)
        # 事务尚未提交：不允许任何失效。
        assert forget_calls == []
        assert inner.forgets_at_commit is None

    assert inner.commits == 1
    assert inner.rollbacks == 0
    assert inner.forgets_at_commit == 0
    assert forget_calls == [(f"zvms:user:{OID}", f"zvms:auth:{OID}")]


@pytest.mark.asyncio
async def test_ban_unit_rollback_does_not_forget(forget_calls) -> None:
    inner = FakeBanUsersRepo(forget_calls)
    repo = CachedUsersRepo(inner)
    session_repo = CachedUsersRepo(inner, use_cache=False)

    with pytest.raises(RuntimeError):
        async with repo.ban_unit(OID):
            await session_repo.set_ban(
                OID, reason="test", until=None, source="admin", rule_code=None
            )
            raise RuntimeError("boom")

    assert inner.rollbacks == 1
    assert inner.commits == 0
    assert forget_calls == []


@pytest.mark.asyncio
async def test_direct_ban_writes_still_forget_immediately(forget_calls) -> None:
    inner = FakeBanUsersRepo(forget_calls)
    repo = CachedUsersRepo(inner)

    await repo.set_ban(OID, reason="test", until=None, source="admin", rule_code=None)
    assert len(forget_calls) == 1

    await repo.clear_ban(OID)
    assert len(forget_calls) == 2

    effective = await repo.set_ban_if_stronger(
        OID, reason="rule", until=None, source="rule", rule_code="x"
    )
    assert effective is None
    assert len(forget_calls) == 3


class FakeGroupsRepo:
    def __init__(self) -> None:
        self.queries: list[tuple[str, str]] = []

    async def list_classes(self, query: str = "", page: int = 1, size=None):
        self.queries.append(("classes", query))
        return [], 0

    async def list_permission_groups(self, query: str = "", permissions=None):
        self.queries.append(("pgroups", query))
        return []


@pytest.mark.asyncio
async def test_group_list_keys_hide_query_text(remembered_keys) -> None:
    inner = FakeGroupsRepo()
    repo = CachedGroupsRepo(inner)

    await repo.list_classes("阿", 1)
    await repo.list_classes("波", 1)
    await repo.list_classes("阿", 1)
    await repo.list_permission_groups("阿", ["user.read"])
    await repo.list_permission_groups("波", ["user.read"])

    assert inner.queries == [
        ("classes", "阿"),
        ("classes", "波"),
        ("classes", "阿"),
        ("pgroups", "阿"),
        ("pgroups", "波"),
    ]
    assert len(remembered_keys) == 5
    assert all("阿" not in key and "波" not in key for key in remembered_keys)
    # 不同 query 不撞 key；同一 query 的 key 稳定复用。
    assert remembered_keys[0] != remembered_keys[1]
    assert remembered_keys[0] == remembered_keys[2]
    assert remembered_keys[3] != remembered_keys[4]
