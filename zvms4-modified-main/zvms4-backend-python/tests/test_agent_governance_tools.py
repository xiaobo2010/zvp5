"""平台治理工具：注册表形状、只读预览、脱敏与提案闸。

离线：没有数据库、没有上游。仓储与旁路（审计、`run_query`）全部换成替身，
验的是**工具层自己负责的那部分**：

* 注册表（5 读 + 4 写）与「写工具三件套」（impl + prepare + dual_auth_action）；
* 读工具现查权限（``guard``），且从不声明 ``prepare`` / 双人码；
* ``prepare`` 只读：GC 预览/清理草案只做聚合计数，绝不调用删除；
* 密钥材料不出现在任何返回里（连 ``key_hint`` 也不给）；
* 公告不按人复制（一条公告 = 一行 ``NOTIFICATIONS``），``all`` 直接拒绝；
* 审计摘要里的手机号/邮箱脱敏。
"""

from __future__ import annotations

import contextlib
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from db import backends
from services.agent import ops_auth, ops_tools, tools, tools_governance
from services.agent.tool_impl import AgentError

ADMIN = {"id": "000000000000000000000001", "per": ["admin"], "roles": ["admin"]}
DEPARTMENT = {"id": "000000000000000000000002", "per": ["department"]}
TARGET = {
    "_id": "000000000000000000000010",
    "id": "20251135",
    "name": "张三",
    "roles": ["student"],
}

EXPECTED_READ = {
    "ai_gateway_status",
    "ai_prompt_audit_list",
    "attachment_stats",
    "attachment_gc_preview",
    "volunteer_audit",
}
EXPECTED_WRITE = {
    "ai_api_key_revoke",
    "ai_quota_set",
    "attachment_gc_run",
    "notify_send",
}
GOVERNANCE_TOOLS = EXPECTED_READ | EXPECTED_WRITE


def _tool(name: str) -> tools.ToolDef:
    return tools.TOOLS[name]


async def _prepare(name: str, **kwargs):
    prepare = _tool(name).prepare
    assert prepare is not None, f"{name} 缺少 prepare"
    return await prepare(ADMIN, **kwargs)


async def _impl(name: str, **kwargs):
    return await _tool(name).impl(ADMIN, **kwargs)


@contextlib.asynccontextmanager
async def _fake_scope():
    yield object()


class _FakeUsers:
    def __init__(self, users: list[dict]) -> None:
        self._users = users

    async def get_by_oid(self, oid):
        return next((user for user in self._users if user["_id"] == oid), None)

    async def find_by_business_id(self, business_id):
        return next((user for user in self._users if user["id"] == business_id), None)

    async def list_all(self):
        return list(self._users)


class _FakeFiles:
    def __init__(self, snapshot=None, purge=None) -> None:
        self.snapshot = snapshot or {}
        self.purge = list(purge or [])
        self.snapshot_calls: list[dict] = []
        self.purge_calls: list[tuple] = []

    async def count_snapshot(self, **kwargs):
        self.snapshot_calls.append(kwargs)
        return self.snapshot

    async def purge_ocr_uploads(self, cutoff, batch_limit=200):
        self.purge_calls.append((cutoff, batch_limit))
        return self.purge.pop(0) if self.purge else {
            "originals": 0,
            "thumbs": 0,
            "ocr_rows": 0,
            "events": 0,
        }


class _FakeCallLogs:
    def __init__(self, stats: dict) -> None:
        self._stats = stats
        self.calls: list[tuple] = []

    async def stats(self, *, since, until=None):
        self.calls.append((since, until))
        return self._stats


class _FakeQuotaRepo:
    def __init__(self, rows: dict) -> None:
        self._rows = rows

    async def get(self, user_oid, period, kind):
        return self._rows.get(kind)


class _FakeApiKeys:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def get_by_oid(self, oid):
        return next((row for row in self._rows if row["_id"] == oid), None)


class _FakeNotifications:
    def __init__(self) -> None:
        self.created: list[dict] = []

    async def create(self, *, title, content, author_oid, author_name, source_ref=None):
        self.created.append(
            {
                "title": title,
                "content": content,
                "author_oid": author_oid,
                "author_name": author_name,
            }
        )
        return ({"_id": "f" * 24, "title": title, "content": content}, 7)


class _FakePromptAudit:
    def __init__(self, rows=None, detail=None, total=0) -> None:
        self._rows = rows or []
        self._detail = detail
        self._total = total
        self.list_calls: list[dict] = []

    async def list_page(self, **kwargs):
        self.list_calls.append(kwargs)
        return self._rows

    async def count(self, **kwargs):
        return self._total

    async def get_by_oid(self, oid):
        return self._detail


class _FakeActivities:
    def __init__(self, rows: list[dict], total: int | None = None) -> None:
        self._rows = rows
        self._total = len(rows) if total is None else total

    async def list_raw(self, **kwargs):
        return list(self._rows), self._total


class _FakeMembers:
    def __init__(self, by_activity: dict[str, list[dict]]) -> None:
        self._by_activity = by_activity

    async def list_by_activity(self, activity_oid, status=None):
        return list(self._by_activity.get(activity_oid, []))


def _bind(monkeypatch, **repos) -> None:
    for name, repo in repos.items():
        monkeypatch.setattr(
            backends, f"get_{name}_repo", lambda session=None, _repo=repo: _repo
        )


@pytest.fixture(autouse=True)
def fake_scope(monkeypatch):
    """``run_query`` 只需要一个可传入的会话替身。"""
    monkeypatch.setattr(tools_governance, "session_scope", _fake_scope)


@pytest.fixture
def audits(monkeypatch) -> list[dict]:
    written: list[dict] = []

    async def _record(operator_oid, *, action, detail="", log_type="system_op", level="warning"):
        written.append(
            {
                "operator": operator_oid,
                "action": action,
                "detail": detail,
                "log_type": log_type,
                "level": level,
            }
        )

    monkeypatch.setattr(tools_governance, "audit_system_op", _record)
    return written


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------


def test_governance_tools_are_registered_with_frozen_kinds() -> None:
    assert GOVERNANCE_TOOLS <= set(tools.TOOLS)
    for name in EXPECTED_READ:
        assert _tool(name).kind == tools.READ, name
    for name in EXPECTED_WRITE:
        assert _tool(name).kind == tools.WRITE, name


def test_read_tools_are_guarded_but_never_proposed() -> None:
    for name in EXPECTED_READ:
        tool = _tool(name)
        assert tool.prepare is None, name
        assert tool.dual_auth_action is None, name
        assert tool.guard is ops_auth.ensure_system_admin, name
        assert tool.permission is ops_tools._admin_only, name


def test_write_tools_are_previewed_proposals_with_dual_auth() -> None:
    for name in EXPECTED_WRITE:
        tool = _tool(name)
        assert tool.impl is not None, name
        assert tool.prepare is not None, name
        assert tool.guard is ops_auth.ensure_system_admin, name
        assert tool.dual_auth_action == ops_tools.WRITE_AUTH_ACTION, name


def test_params_are_well_formed_json_schema() -> None:
    for name in GOVERNANCE_TOOLS:
        params = _tool(name).params
        assert params["type"] == "object", name
        assert isinstance(params["properties"], dict), name
        assert set(params["required"]) <= set(params["properties"]), name
        assert params["additionalProperties"] is False, name


@pytest.mark.parametrize("name", sorted(GOVERNANCE_TOOLS))
def test_non_admin_is_denied_by_permission(name: str) -> None:
    assert tools.run_allowed(_tool(name), ADMIN) is True
    assert tools.run_allowed(_tool(name), DEPARTMENT) is False


def test_bounds_come_from_module_constants() -> None:
    gateway = _tool("ai_gateway_status").params["properties"]["days"]
    assert gateway["maximum"] == tools_governance.GATEWAY_DAYS_MAX
    quota_limit = _tool("ai_quota_set").params["properties"]["limit_count"]
    assert quota_limit["maximum"] == tools_governance.QUOTA_LIMIT_MAX
    batches = _tool("attachment_gc_run").params["properties"]["max_batches"]
    assert batches["maximum"] == tools_governance.ATTACHMENT_GC_MAX_BATCHES


# ---------------------------------------------------------------------------
# 脱敏 / 截断
# ---------------------------------------------------------------------------


def test_mask_contacts_hides_mobile_and_email() -> None:
    masked = tools_governance._mask_contacts("联系 13800138000 或 a.b@example.com")
    assert "13800138000" not in masked
    assert "138****00" in masked
    assert "a.b@example.com" not in masked


def test_cap_degrades_oversized_payload() -> None:
    payload = {"kind": "x", "content": "a" * 20000}
    capped = tools_governance._cap(payload)
    assert capped["truncated"] is True
    assert len(capped["content"]) == ops_tools.MAX_TOOL_RESULT_CHARS


# ---------------------------------------------------------------------------
# ai_gateway_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ai_gateway_status_reports_quota_stats_and_key_anomalies(
    monkeypatch, audits
) -> None:
    stats = {
        "daily": [],
        "by_provider": [],
        "by_model": [
            {"model": "m-small", "calls": 1, "errors": 0, "total_tokens": 10},
            {"model": "m-big", "calls": 9, "errors": 2, "total_tokens": 90},
        ],
        "by_purpose": [{"purpose": "agent", "calls": 10}],
        "by_user": [
            {"user_oid": "u1", "name": "甲", "calls": 3, "errors": 0, "total_tokens": 1, "units": 1},
            {"user_oid": "u2", "name": "乙", "calls": 7, "errors": 1, "total_tokens": 2, "units": 2},
        ],
        "by_status": [
            {"status": "success", "calls": 8},
            {"status": "error", "calls": 2},
        ],
        "cache": {"hit_rate": 0.5},
    }
    now = datetime.now()
    key_rows = [
        {
            "OID": "a" * 24,
            "NAME": "ci",
            "USER_OID": TARGET["_id"],
            "ENABLED": 0,
            "EXPIRES_AT": (now + timedelta(days=2)).isoformat(sep=" "),
        },
        {
            "OID": "b" * 24,
            "NAME": "old",
            "USER_OID": TARGET["_id"],
            "ENABLED": 1,
            "EXPIRES_AT": (now - timedelta(days=1)).isoformat(sep=" "),
        },
    ]

    async def _run_query(session, sql, *, limit=100):
        assert "token_hash" not in sql
        assert "key_hint" not in sql
        return {"rows": key_rows, "columns": [], "truncated": False}

    monkeypatch.setattr(tools_governance.db_content, "run_query", _run_query)
    _bind(
        monkeypatch,
        users=_FakeUsers([TARGET]),
        ai_call_logs=_FakeCallLogs(stats),
        ai_quota=_FakeQuotaRepo({}),
    )
    async def _admin_snapshot(oid, roles=None):
        return _snapshot()

    monkeypatch.setattr(
        tools_governance.quota, "admin_snapshot", _admin_snapshot
    )

    result = await _impl("ai_gateway_status", user_oid=TARGET["_id"], days=3)

    assert result["window"]["days"] == 3
    assert result["calls"]["total"] == 10
    # 模型按调用量降序，只留前 5
    assert result["calls"]["by_model"][0]["model"] == "m-big"
    assert [row["user_oid"] for row in result["top_callers"]] == ["u2", "u1"]
    assert result["quota"]["user"]["chat"]["limit"] == 200
    assert result["quota"]["site"], "站点额度快照不该为空"

    anomalies = result["api_keys"]["anomalies"]
    assert [row["oid"] for row in anomalies] == ["a" * 24, "b" * 24]
    assert anomalies[0]["reasons"] == ["disabled", "expiring_soon"]
    assert anomalies[1]["reasons"] == ["expired"]
    assert result["api_keys"]["counts"] == {
        "disabled": 1,
        "expired": 1,
        "expiring_soon": 1,
    }

    # 密钥材料一个字节都不许出现（note 里提到字段名属于说明文字，只看数据本身）
    assert "zvk_" not in json.dumps(result, ensure_ascii=False)
    for row in anomalies:
        assert set(row) == {
            "oid",
            "name",
            "owner_oid",
            "enabled",
            "expires_at",
            "reasons",
        }


def _snapshot() -> dict:
    return {
        "chat": {"period": "202610", "used": 5, "limit": 200, "source": "role_default"},
        "ocr": {"period": "202610", "used": 1, "limit": 100, "source": "explicit"},
    }


# ---------------------------------------------------------------------------
# ai_prompt_audit_list
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_prompt_audit_list_never_returns_body(monkeypatch, audits) -> None:
    repo = _FakePromptAudit(
        rows=[
            {
                "_id": "c" * 24,
                "user_oid": TARGET["_id"],
                "purpose": "agent",
                "roles_snapshot": '["admin"]',
                "prompt_chars": 42,
                "created_at": datetime.now(),
            }
        ],
        total=1,
    )
    _bind(monkeypatch, ai_prompt_audit=repo)

    result = await _impl("ai_prompt_audit_list", days=7, page=1, page_size=10)

    assert result["pagination"]["total"] == 1
    assert result["entries"][0]["prompt_chars"] == 42
    assert "prompt_text" not in json.dumps(result)
    assert audits == [], "列表不该写访问审计"


@pytest.mark.asyncio
async def test_prompt_audit_detail_writes_access_audit(monkeypatch, audits) -> None:
    oid = "d" * 24
    repo = _FakePromptAudit(
        detail={
            "_id": oid,
            "user_oid": TARGET["_id"],
            "purpose": "agent",
            "roles_snapshot": None,
            "prompt_text": "请帮我查活动",
            "created_at": datetime.now(),
        }
    )
    _bind(monkeypatch, ai_prompt_audit=repo)

    result = await _impl("ai_prompt_audit_list", oid=oid)

    assert result["prompt_text"] == "请帮我查活动"
    assert audits[0]["log_type"] == "ai_prompt_access"
    assert audits[0]["level"] == "info"
    assert oid in audits[0]["detail"]


@pytest.mark.asyncio
async def test_prompt_audit_detail_404_for_unknown_oid(monkeypatch) -> None:
    _bind(monkeypatch, ai_prompt_audit=_FakePromptAudit())
    with pytest.raises(AgentError) as exc:
        await _impl("ai_prompt_audit_list", oid="e" * 24)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# attachment_stats / gc preview
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_attachment_stats_aggregates_buckets_and_orphans(monkeypatch) -> None:
    snapshot = {
        "groups": [
            {
                "kind": "original",
                "purpose": "volunteer_order",
                "status": "ready",
                "ocr_status": "done",
                "files": 2,
                "bytes": 100,
            },
            {
                "kind": "thumb",
                "purpose": "volunteer_order",
                "status": "ready",
                "ocr_status": "done",
                "files": 1,
                "bytes": 10,
            },
            {
                "kind": "original",
                "purpose": None,
                "status": "verified",
                "ocr_status": "pending",
                "files": 4,
                "bytes": 40,
            },
        ],
        "orphans": {"thumb_without_parent": 3, "original_without_thumb": 5},
    }
    _bind(monkeypatch, files=_FakeFiles(snapshot=snapshot))

    result = await _impl("attachment_stats")

    assert result["totals"] == {"files": 7, "bytes": 150}
    assert [(row["key"], row["files"]) for row in result["by_kind"]] == [
        ("original", 6),
        ("thumb", 1),
    ]
    assert ("(none)", 4) in [(row["key"], row["files"]) for row in result["by_purpose"]]
    assert result["orphans"]["thumb_without_parent"] == 3
    assert result["orphans"]["original_without_thumb"] == 5


@pytest.mark.asyncio
async def test_gc_preview_is_read_only_and_explains_scope(monkeypatch) -> None:
    repo = _FakeFiles(snapshot={"gc": {"candidates": 7}})
    _bind(monkeypatch, files=repo)
    monkeypatch.setattr(tools_governance.ocr_upload_gc, "batch_size", lambda: 3)

    result = await _impl("attachment_gc_preview", retention_days=5)

    assert result["candidates"] == 7
    assert result["est_batches"] == 3
    cutoff = repo.snapshot_calls[0]["gc_cutoff"]
    assert cutoff < datetime.now() - timedelta(days=4)
    assert result["scope"].startswith("purpose='volunteer_order'")
    assert repo.purge_calls == [], "预览绝不能调用删除"


# ---------------------------------------------------------------------------
# volunteer_audit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_volunteer_audit_flags_zero_members_gaps_and_zero_duration(
    monkeypatch,
) -> None:
    now = datetime.now()
    activities = [
        {"_id": "1" * 24, "name": "无人报名", "date": now - timedelta(days=2), "status": "effective"},
        {"_id": "2" * 24, "name": "有缺口", "date": now - timedelta(days=3), "status": "effective"},
        {"_id": "3" * 24, "name": "零时长", "date": now - timedelta(days=4), "status": "effective"},
    ]
    members = {
        "1" * 24: [],
        "2" * 24: [
            {"_id": "m1", "member": "u1", "status": "effective", "mode": "on-campus", "duration": 4.0},
            {"_id": "m2", "member": "u2", "status": "effective", "mode": "on-campus", "duration": 2.0},
        ],
        "3" * 24: [
            {"_id": "m3", "member": "u3", "status": "effective", "mode": "off-campus", "duration": 0.0},
        ],
    }
    _bind(
        monkeypatch,
        activities=_FakeActivities(activities),
        members=_FakeMembers(members),
    )

    result = await _impl("volunteer_audit", days=30)

    assert result["activities_scanned"] == 3
    assert result["counts"] == {
        "zero_member_activities": 1,
        "duration_gaps": 1,
        "zero_duration_records": 1,
    }
    assert result["zero_member_activities"][0]["activity_name"] == "无人报名"
    gap = result["duration_gaps"][0]
    assert gap["member_oid"] == "u2"
    assert gap["standard_hours"] == 4.0
    assert gap["missing_hours"] == 2.0
    assert result["zero_duration_records"][0]["member_oid"] == "u3"
    assert result["standard_basis"] == "max_duration_within_activity_mode"


@pytest.mark.asyncio
async def test_volunteer_audit_ignores_non_effective_members(monkeypatch) -> None:
    now = datetime.now()
    activity = {"_id": "4" * 24, "name": "待审", "date": now, "status": "effective"}
    members = {
        "4" * 24: [
            {"_id": "m1", "member": "u1", "status": "pending", "mode": "on-campus", "duration": 0.0},
        ]
    }
    _bind(
        monkeypatch,
        activities=_FakeActivities([activity]),
        members=_FakeMembers(members),
    )

    result = await _impl("volunteer_audit")

    assert result["counts"]["zero_duration_records"] == 0
    assert result["counts"]["zero_member_activities"] == 0


# ---------------------------------------------------------------------------
# ai_api_key_revoke
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_key_revoke_prepare_shows_metadata_without_secrets(
    monkeypatch,
) -> None:
    key = {
        "_id": "f" * 24,
        "name": "ci",
        "user_oid": TARGET["_id"],
        "enabled": True,
        "expires_at": None,
        "last_used_at": None,
        "token_hash": "deadbeef",
        "key_hint": "zvk_abcdwxyz",
    }
    _bind(monkeypatch, ai_api_keys=_FakeApiKeys([key]))

    draft = await _prepare("ai_api_key_revoke", key_oid="f" * 24, reason="泄露")

    assert draft.preview["from_enabled"] is True
    assert draft.preview["to_enabled"] is False
    text = json.dumps(draft.preview, ensure_ascii=False) + draft.summary
    assert "deadbeef" not in text
    assert "zvk_" not in text
    assert "hint" not in text


@pytest.mark.asyncio
async def test_api_key_revoke_impl_soft_revokes_and_audits(monkeypatch, audits) -> None:
    key = {
        "_id": "f" * 24,
        "name": "ci",
        "user_oid": TARGET["_id"],
        "enabled": True,
        "expires_at": None,
    }
    _bind(monkeypatch, ai_api_keys=_FakeApiKeys([key]))
    calls: list[tuple] = []

    async def _update(oid, actor, fields):
        calls.append((oid, fields))
        return {"oid": oid, "name": "ci", "enabled": False}

    async def _invalidate(oid=None):
        calls.append(("invalidate", oid))

    monkeypatch.setattr(tools_governance.api_keys, "update", _update)
    monkeypatch.setattr(tools_governance.api_keys, "invalidate", _invalidate)

    result = await _impl("ai_api_key_revoke", key_oid="f" * 24, reason="泄露")

    assert calls[0] == ("f" * 24, {"enabled": False})
    assert ("invalidate", "f" * 24) in calls
    assert result["revoked"] is True
    assert result["enabled"] is False
    assert audits[0]["action"] == "agent_ai_api_key_revoke"
    assert "泄露" in audits[0]["detail"]


# ---------------------------------------------------------------------------
# ai_quota_set
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_quota_set_prepare_reports_transition(monkeypatch) -> None:
    _bind(monkeypatch, users=_FakeUsers([TARGET]))
    async def _admin_snapshot(oid, roles=None):
        return _snapshot()

    monkeypatch.setattr(
        tools_governance.quota, "admin_snapshot", _admin_snapshot
    )

    draft = await _prepare("ai_quota_set", user_oid=TARGET["_id"], kind="chat", limit_count=50)

    assert draft.preview["from"]["limit"] == 200
    assert draft.preview["to"]["limit"] == 50
    assert "200 → 50" in draft.summary


@pytest.mark.asyncio
async def test_quota_set_impl_calls_service_and_audits(monkeypatch, audits) -> None:
    _bind(monkeypatch, users=_FakeUsers([TARGET]))
    seen: list[tuple] = []

    async def _set_limit(user_oid, kind, limit_count):
        seen.append((user_oid, kind, limit_count))
        return {"period": "202610", "kind": kind, "used": 5, "limit": limit_count}

    monkeypatch.setattr(tools_governance.quota, "set_user_limit", _set_limit)

    result = await _impl(
        "ai_quota_set", user_oid=TARGET["_id"], kind="chat", limit_count=50
    )

    assert seen == [(TARGET["_id"], "chat", 50)]
    assert result["limit"] == 50
    assert audits[0]["action"] == "agent_ai_quota_set"


@pytest.mark.asyncio
async def test_quota_set_rejects_unknown_kind() -> None:
    with pytest.raises(AgentError) as exc:
        await _impl("ai_quota_set", user_oid=TARGET["_id"], kind="image", limit_count=1)
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# attachment_gc_run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gc_run_prepare_counts_without_deleting(monkeypatch) -> None:
    repo = _FakeFiles(snapshot={"gc": {"candidates": 5}})
    _bind(monkeypatch, files=repo)
    monkeypatch.setattr(tools_governance.ocr_upload_gc, "batch_size", lambda: 3)

    draft = await _prepare("attachment_gc_run", retention_days=3, max_batches=2)

    assert draft.preview["candidates"] == 5
    assert draft.preview["upper_bound"] == 5
    assert repo.purge_calls == [], "prepare 只允许只读聚合"


@pytest.mark.asyncio
async def test_gc_run_stops_on_short_batch_and_audits(monkeypatch, audits) -> None:
    repo = _FakeFiles(
        purge=[
            {"originals": 3, "thumbs": 3, "ocr_rows": 3, "events": 9},
            {"originals": 1, "thumbs": 1, "ocr_rows": 1, "events": 3},
        ]
    )
    _bind(monkeypatch, files=repo)
    monkeypatch.setattr(tools_governance.ocr_upload_gc, "batch_size", lambda: 3)

    result = await _impl("attachment_gc_run", retention_days=3, max_batches=5)

    assert result["batches"] == 2, "短批即停：凑不满一批说明已经清空"
    assert result["deleted"] == {
        "originals": 4,
        "thumbs": 4,
        "ocr_rows": 4,
        "events": 12,
    }
    assert audits[0]["action"] == "agent_attachment_gc_run"
    assert "originals=4" in audits[0]["detail"]


# ---------------------------------------------------------------------------
# notify_send
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notify_send_rejects_broadcast() -> None:
    with pytest.raises(AgentError) as exc:
        await _prepare(
            "notify_send", title="通知", content="正文", target={"kind": "all"}
        )
    assert exc.value.status_code == 403
    assert "全站广播" in exc.value.message


@pytest.mark.asyncio
async def test_notify_send_rejects_oversized_role(monkeypatch) -> None:
    users = [
        {"_id": f"{index:024x}", "id": str(index), "name": f"u{index}", "roles": ["student"]}
        for index in range(tools_governance.NOTIFY_RECIPIENTS_MAX + 1)
    ]
    _bind(monkeypatch, users=_FakeUsers(users))

    with pytest.raises(AgentError) as exc:
        await _prepare(
            "notify_send",
            title="通知",
            content="正文",
            target={"kind": "role", "value": "student"},
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_notify_send_creates_one_notice_with_audience_prefix(
    monkeypatch, audits
) -> None:
    repo = _FakeNotifications()
    _bind(monkeypatch, users=_FakeUsers([TARGET]), notifications=repo)

    async def _display_name(oid):
        return "管理员"

    monkeypatch.setattr(tools_governance, "get_user_display_name", _display_name)

    result = await _impl(
        "notify_send",
        title="例会改期",
        content="本周例会改到周五",
        target={"kind": "user", "value": TARGET["id"]},
    )

    assert len(repo.created) == 1, "公告板是一行一条，不能按人复制"
    created = repo.created[0]
    assert created["title"].startswith("【定向通知：张三】")
    assert created["author_oid"] == ADMIN["id"]
    assert created["author_name"] == "管理员"
    assert result["recipients"] == 1
    assert result["delivery"] == "global_board"
    assert audits[0]["action"] == "agent_notify_send"
    assert "recipients=1" in audits[0]["detail"]


@pytest.mark.asyncio
async def test_notify_send_role_target_resolves_members(monkeypatch, audits) -> None:
    repo = _FakeNotifications()
    _bind(
        monkeypatch,
        users=_FakeUsers(
            [
                TARGET,
                {"_id": "0" * 23 + "9", "id": "2", "name": "李四", "roles": ["admin", "student"]},
                {"_id": "0" * 23 + "8", "id": "3", "name": "王五", "roles": ["admin"]},
            ]
        ),
        notifications=repo,
    )

    async def _display_name(oid):
        return "管理员"

    monkeypatch.setattr(tools_governance, "get_user_display_name", _display_name)

    result = await _impl(
        "notify_send",
        title="补录提醒",
        content="请补录时长",
        target={"kind": "role", "value": "student"},
    )

    assert result["recipients"] == 2
    assert repo.created[0]["title"].startswith("【角色 student】")


@pytest.mark.asyncio
async def test_notify_send_titles_are_clipped_to_column_width(monkeypatch, audits) -> None:
    repo = _FakeNotifications()
    _bind(monkeypatch, users=_FakeUsers([TARGET]), notifications=repo)

    async def _display_name(oid):
        return "管理员"

    monkeypatch.setattr(tools_governance, "get_user_display_name", _display_name)

    await _impl(
        "notify_send",
        title="标" * tools_governance.NOTIFY_TITLE_MAX,
        content="正文",
        target={"kind": "user", "value": TARGET["_id"]},
    )

    assert len(repo.created[0]["title"]) <= tools_governance.NOTIFY_TITLE_MAX


def test_audience_title_masks_contacts() -> None:
    title = tools_governance._audience_title("正文", "定向通知：13800138000")
    assert "13800138000" not in title
    assert "138****00" in title
