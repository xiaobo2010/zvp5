"""缓存策略的护栏：TTL 下限 + 事务型写的失效补偿。

背景：读穿缓存的正确性完全依赖写路径失效。仓储代理（``db/cached_repos.py``）
覆盖到的写会自动 forget + bump，但**自带 session 的事务写**会整体旁路代理
（``use_cache=session is None``），DB 控制台更是直接跑任意 SQL。TTL 一旦放长，
这两类旁路就必须显式补偿，否则读到的就是分钟级的旧数据。
"""

from __future__ import annotations

import pytest

from db import cache
from db import cached_repos
from routers import system_db_content_router as content_router


def test_repo_ttls_are_long_but_bounded() -> None:
    """TTL 放宽的前提是「写路径都会失效」，所以这里只钉下限与合理性上界。"""
    ttl_names = [
        "TTL_AUTH",
        "TTL_USER",
        "TTL_NAMES",
        "TTL_CLASS_MEMBERS",
        "TTL_GROUP",
        "TTL_GROUP_LIST",
        "TTL_ACTIVITY",
        "TTL_ACTIVITIES_EFFECTIVE",
        "TTL_CONVERSATION",
    ]
    for name in ttl_names:
        value = getattr(cached_repos, name)
        assert 60 <= value <= 24 * 3600, f"{name}={value} 超出合理区间"

    # 认证/封禁相关必须比「几乎不变」的对象短：一旦某条写路径漏了失效，
    # 封禁晚生效的代价远大于班级名单旧一分钟。
    assert cached_repos.TTL_AUTH < cached_repos.TTL_GROUP
    assert cached_repos.TTL_ACTIVITY < cached_repos.TTL_ACTIVITIES_EFFECTIVE


def test_jitter_spreads_expiry() -> None:
    ttl = cached_repos._ttl(1000.0)
    assert 900.0 <= ttl <= 1100.0


@pytest.mark.asyncio
async def test_content_console_invalidates_all_cached_scopes(monkeypatch) -> None:
    """DB 控制台写下任意行后，每个带缓存的域都必须自增版本号。"""
    bumped: list[str] = []

    async def fake_bump(scope: str) -> None:
        bumped.append(scope)

    monkeypatch.setattr(cache, "bump_epoch", fake_bump)
    await content_router._invalidate_read_caches()

    # 集合与 db.cache 的键空间对齐（漏域 = 控制台改完仍读到旧值），顺序保持契约。
    assert set(bumped) == set(cache.SCOPE_PREFIXES)
    assert bumped == [
        "users",
        "groups",
        "activities",
        "conversations",
        "notifications",
        "changelog",
        "roles",
        "settings",
    ]
