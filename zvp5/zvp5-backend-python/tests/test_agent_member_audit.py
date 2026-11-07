"""活动域写操作的审计旁路离线用例。

两个工具（改时长 / 审核状态）落库后必须各写**一条** ``audit_system_op``，且：

* 审计类型沿用 REST 端点的既有 ltype（``activity_member_update`` /
  ``activity_member_review``），不新造枚举；
* 审计失败不能把「已经写完的」操作报成失败；
* 校验阶段就被拒（活动不存在、状态没变）时一行审计都不该有。

全离线：仓储是内存桩，时长重算与审计都是替身。

跑法：``.venv/bin/python -m pytest tests/test_agent_member_audit.py -q``
"""

from __future__ import annotations

import pytest

from db import backends
from services.agent import tool_impl

ADMIN_ID = "000000000000000000000001"
ADMIN = {"id": ADMIN_ID, "per": ["admin"]}
ACTIVITY = "64b7f0c2e1a3b4c5d6e7fc01"
MEMBER = "64b7f0c2e1a3b4c5d6e7fc02"
MEMBER_USER = "64b7f0c2e1a3b4c5d6e7fc03"


class FakeActivitiesRepo:
    async def get(self, oid: str):
        if oid != ACTIVITY:
            return None
        return {"_id": ACTIVITY, "name": "图书馆志愿", "type": "hybrid"}


class FakeMembersRepo:
    def __init__(self) -> None:
        self.updates: list[tuple[str, dict]] = []
        self.row = {
            "_id": MEMBER,
            "activity": ACTIVITY,
            "member": MEMBER_USER,
            "mode": "on-campus",
            "duration": 3.0,
            "status": "pending",
        }

    async def get_by_oid(self, oid: str):
        return dict(self.row) if oid == MEMBER else None

    async def update_fields(self, oid: str, fields: dict) -> None:
        self.updates.append((oid, dict(fields)))
        self.row.update(fields)


@pytest.fixture
def audits(monkeypatch):
    """替换审计写入，返回收到的调用列表。"""
    rows: list[dict] = []

    async def _audit(
        operator_oid, *, action, detail="", log_type="system_op", level="warning"
    ) -> None:
        rows.append(
            {
                "operator_oid": operator_oid,
                "action": action,
                "detail": detail,
                "log_type": log_type,
            }
        )

    async def _noop(*_args, **_kwargs) -> None:
        return None

    monkeypatch.setattr(tool_impl, "audit_system_op", _audit)
    monkeypatch.setattr(tool_impl, "calculate_user_time", _noop)
    monkeypatch.setattr(
        backends, "get_activities_repo", lambda session=None: FakeActivitiesRepo()
    )
    monkeypatch.setattr(
        backends, "get_members_repo", lambda session=None: FakeMembersRepo()
    )
    return rows


@pytest.mark.asyncio
async def test_duration_write_writes_one_audit(audits) -> None:
    result = await tool_impl.apply_member_duration(
        activity_id=ACTIVITY, member_id=MEMBER, duration=6.5, user=ADMIN
    )

    assert result["duration"] == 6.5
    assert len(audits) == 1
    entry = audits[0]
    assert entry["operator_oid"] == ADMIN_ID
    assert entry["log_type"] == "activity_member_update"
    assert ACTIVITY in entry["detail"] and MEMBER in entry["detail"]


@pytest.mark.asyncio
async def test_status_write_writes_one_audit(audits) -> None:
    result = await tool_impl.apply_member_status(
        activity_id=ACTIVITY, member_id=MEMBER, status="effective", user=ADMIN
    )

    assert result["status"] == "effective"
    assert len(audits) == 1
    entry = audits[0]
    assert entry["operator_oid"] == ADMIN_ID
    assert entry["log_type"] == "activity_member_review"
    assert "effective" in entry["detail"]


@pytest.mark.asyncio
async def test_rejected_writes_leave_no_audit(audits) -> None:
    with pytest.raises(tool_impl.AgentError):
        await tool_impl.apply_member_status(
            activity_id=ACTIVITY, member_id=MEMBER, status="pending", user=ADMIN
        )
    with pytest.raises(tool_impl.AgentError):
        await tool_impl.apply_member_duration(
            activity_id="64b7f0c2e1a3b4c5d6e7fcff",
            member_id=MEMBER,
            duration=1.0,
            user=ADMIN,
        )

    assert audits == []


@pytest.mark.asyncio
async def test_audit_operator_comes_from_execution_context(audits) -> None:
    """``tools.py`` 的实现不传 user 时，操作者由 executor 的 ContextVar 带入。

    出作用域后再写一次：审计不能残留上一个请求的操作者（ContextVar 必须还原）。
    """
    with tool_impl.operator_context(ADMIN):
        await tool_impl.apply_member_duration(
            activity_id=ACTIVITY, member_id=MEMBER, duration=1.5
        )
    assert audits[0]["operator_oid"] == ADMIN_ID

    await tool_impl.apply_member_duration(
        activity_id=ACTIVITY, member_id=MEMBER, duration=1.0
    )
    assert audits[1]["operator_oid"] == ""


@pytest.mark.asyncio
async def test_audit_failure_does_not_break_the_write(monkeypatch, audits) -> None:
    async def _boom(*_args, **_kwargs):
        raise RuntimeError("audit down")

    monkeypatch.setattr(tool_impl, "audit_system_op", _boom)

    result = await tool_impl.apply_member_duration(
        activity_id=ACTIVITY, member_id=MEMBER, duration=2.0, user=ADMIN
    )

    assert result["duration"] == 2.0
