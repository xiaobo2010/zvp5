"""按角色差异化月额度（``quota.default_limit_for``）的契约。

三条不变量：

* 角色按 ``admin > department`` 取第一个命中，不叠加、不取最大值；
* 未列出的角色回退 ``AI_DEFAULT_*``（老行为不变）；
* ``roles`` 只在**建行/新月份**时决定 ``limit_count``：行已存在就按库里的值走，
  所以 ``consume`` 必须把算出来的 default_limit 传给仓储，而不是自己写库。

全部离线：额度仓储是内存桩，服务的月份口径、角色表、校验都是真实代码。
"""

from __future__ import annotations

import asyncio

import pytest

import settings
from db import backends
from services.ai import quota as quota_service
from services.ai.config import DEFAULT_QUOTA, role_quota

USER = "64b7f0c2e1a3b4c5d6e7fa01"


class FakeQuotaRepo:
    """``AIQuotaRepo`` 的内存桩：记录每次 consume 拿到的 default_limit。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.rows: dict[tuple[str, str, str], dict] = {}

    async def get(self, user_oid: str, period: str, kind: str):
        row = self.rows.get((user_oid, period, kind))
        return dict(row) if row else None

    async def consume(
        self, user_oid: str, period: str, kind: str, amount: int, default_limit: int
    ) -> tuple[bool, int, int]:
        self.calls.append(
            {
                "user_oid": user_oid,
                "kind": kind,
                "amount": amount,
                "default_limit": default_limit,
            }
        )
        return True, amount, default_limit

    async def set_limit(
        self, user_oid: str, period: str, kind: str, limit_count: int
    ) -> None:
        self.rows[(user_oid, period, kind)] = {
            "used": 0,
            "limit_count": limit_count,
        }

    async def reset_used(self, user_oid: str, period: str, kind: str) -> None:
        row = self.rows.get((user_oid, period, kind))
        if row:
            row["used"] = 0


@pytest.fixture
def repo(monkeypatch) -> FakeQuotaRepo:
    fake = FakeQuotaRepo()
    monkeypatch.setattr(backends, "get_ai_quota_repo", lambda session=None: fake)
    monkeypatch.setattr(settings, "AI_ROLE_QUOTA", None, raising=False)
    return fake


def test_role_defaults_come_from_the_priority_table():
    assert quota_service.default_limit_for(["admin"], "ocr") == 500
    assert quota_service.default_limit_for(["department"], "ocr") == 100
    assert quota_service.default_limit_for(["admin", "department"], "chat") == 1_000_000


def test_admin_wins_when_both_roles_are_present():
    """同时有 admin 与 department 时按管理员算（顺序即优先级）。"""
    assert quota_service.default_limit_for(["department", "admin"], "chat") == 1_000_000


def test_roles_outside_the_tables_fall_back_to_the_default():
    # ``student`` / ``secretary`` 现在由角色策略表（V043）显式覆盖，不再是
    # 「未列出的角色」：它们的 DEFAULTS 就是 0（学生不开放聊天额度）。
    assert quota_service.default_limit_for(["student"], "ocr") == 0
    assert (
        quota_service.default_limit_for(["teacher"], "ocr")
        == DEFAULT_QUOTA["ocr"]
    )
    assert quota_service.default_limit_for(None, "chat") == DEFAULT_QUOTA["chat"]
    assert quota_service.default_limit_for([], "chat") == DEFAULT_QUOTA["chat"]


def test_non_string_roles_are_ignored():
    assert quota_service.default_limit_for([None, 123, "admin"], "ocr") == 500
    assert quota_service.default_limit_for([None, 123], "ocr") == DEFAULT_QUOTA["ocr"]


def test_site_kinds_use_the_site_quota():
    """站点额度没有角色维度，走站点口径，**不**回落到用户的 ``DEFAULT_QUOTA["ocr"]``。

    回落到用户额度会让站点额度一建就是 100（几十个用户一天打满、全站 OCR 503，
    2026-10-24 上线实测）。这条断言就是那个坑的回归守卫。
    """
    site_limit = quota_service.default_limit_for(None, "ocr_tencent")

    assert site_limit > 0
    assert site_limit != DEFAULT_QUOTA["ocr"]


def test_consume_without_roles_keeps_the_old_behaviour(repo):
    asyncio.run(quota_service.consume(USER, "ocr", 1))

    assert repo.calls[-1]["default_limit"] == DEFAULT_QUOTA["ocr"]


def test_consume_passes_the_role_limit_to_the_repo(repo):
    asyncio.run(quota_service.consume(USER, "chat", 1, roles=["admin"]))

    assert repo.calls[-1]["default_limit"] == 1_000_000


def test_existing_row_limit_wins_over_the_role_default(repo):
    """行里的 ``limit_count`` 是事实：管理员手调过就按库里的值。"""
    asyncio.run(quota_service.set_user_limit(USER, "ocr", 7))

    allowed, used, limit = asyncio.run(
        quota_service.peek(USER, "ocr", 1, roles=["admin"])
    )

    assert (allowed, used, limit) == (True, 0, 7)


def test_peek_uses_the_role_default_when_no_row_exists(repo):
    allowed, used, limit = asyncio.run(
        quota_service.peek(USER, "ocr", 1, roles=["admin"])
    )

    assert (allowed, used, limit) == (True, 0, 500)


def test_settings_only_govern_roles_the_policy_table_leaves_alone(monkeypatch, repo):
    """``ai.role_quota`` 仍是 admin 的配置入口；三个可编辑角色归 V043 管。

    角色策略表（``ROLE_QUOTAS``）排在 ``ai.role_quota`` 之前：管理员在「角色策略」
    页改的是它，让一张整表 JSON 把它悄悄覆盖掉会出现「页面显示 A、实际扣 B」。
    """
    monkeypatch.setattr(
        settings,
        "AI_ROLE_QUOTA",
        {"admin": {"ocr": 42}, "broken": {"ocr": True, "chat": "many"}},
        raising=False,
    )

    table = role_quota()

    assert table["admin"] == {"ocr": 42}
    assert "broken" not in table  # bool 与字符串都被丢弃
    assert quota_service.default_limit_for(["admin"], "ocr") == 42
    # 角色策略表有意见的角色不再被这张 JSON 覆盖。
    assert quota_service.default_limit_for(["department"], "ocr") == 100


def test_broken_settings_fall_back_to_the_code_table(monkeypatch, repo):
    monkeypatch.setattr(settings, "AI_ROLE_QUOTA", "not-a-dict", raising=False)

    assert quota_service.default_limit_for(["admin"], "ocr") == 500
