"""CHANGELOG → SYSTEM 版本公告的离线契约（不连 Oracle）。

钉住四类容易写错且错了会静默伤到用户/审计的事：

* 渲染：标题/正文的形状，超长时**按条目边界**截断（不切半个汉字、不留半条）；
* 同步：一条 CHANGELOG 恰好一条公告，重复执行不重复建，编辑走 version +1；
* 回填：历史一律 inactive（不打扰用户），``--activate`` 只点亮指定版本；
* 挂接：CHANGELOG 的 POST / PUT / DELETE 各触发一次同步，且响应形状不变。

仓储全部打桩；路由用 ``dependency_overrides`` 替换登录与审计日志。
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import changelog_router
from services import changelog_notifications as svc
from typings.log import inject_log
from util.object_id import get_current_user

NOW = datetime(2026, 10, 26, 12, 0, 0)
ADMIN = {"id": "000000000000000000000001", "per": ["admin"]}
OLD_OID = "0c00000000000000000000b1"
MID_OID = "0c00000000000000000000b2"
NEW_OID = "0c00000000000000000000b3"


def _entry(oid: str, version: str, date: str, items: list[str]) -> dict:
    return {"_id": oid, "version": version, "date": date, "items": items}


# ---------------------------------------------------------------------------
# FAKES
# ---------------------------------------------------------------------------


class FakeNotificationsRepo:
    """内存版 ``NotificationsRepo``：create 恒 active，update 自增 version。"""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.calls: list[tuple] = []
        self._seq = 0

    def _new_id(self) -> str:
        self._seq += 1
        return f"0d000000000000000000{self._seq:04d}"

    async def get_by_source_ref(self, source_ref):
        for row in self.rows.values():
            if row["source_ref"] == source_ref:
                return dict(row)
        return None

    async def create(
        self, *, title, content, author_oid, author_name, source_ref=None
    ):
        oid = self._new_id()
        row = {
            "_id": oid,
            "title": title,
            "content": content,
            "active": True,
            "author_oid": author_oid,
            "author_name": author_name,
            "version": 1,
            "source_ref": source_ref,
            "created_at": NOW,
            "updated_at": NOW,
        }
        self.rows[oid] = row
        self.calls.append(("create", source_ref))
        return dict(row), 1

    async def update(
        self,
        oid,
        *,
        title=None,
        content=None,
        active=None,
        author_oid=None,
        author_name=None,
    ):
        row = self.rows.get(oid)
        if row is None:
            return None, 0
        if title is not None:
            row["title"] = title
        if content is not None:
            row["content"] = content
        if active is not None:
            row["active"] = bool(active)
        row["version"] += 1
        self.calls.append(("update", oid, active))
        return dict(row), 1


class FakeChangelogRepo:
    def __init__(self, entries: list[dict]) -> None:
        self.entries = {entry["_id"]: dict(entry) for entry in entries}
        self.created: list[tuple] = []

    async def list_all(self):
        return [dict(entry) for entry in self.entries.values()]

    async def get_by_oid(self, oid):
        entry = self.entries.get(oid)
        return dict(entry) if entry is not None else None

    async def create(self, version, date, items):
        oid = f"0e00000000000000000{len(self.created) + 1:05d}"
        self.entries[oid] = {
            "_id": oid,
            "version": version,
            "date": date,
            "items": list(items),
        }
        self.created.append((version, date, list(items)))
        return oid

    async def update(self, oid, version, date, items):
        entry = self.entries.get(oid)
        if entry is None:
            return 0
        entry.update(version=version, date=date, items=list(items))
        return 1

    async def delete(self, oid):
        return 1 if self.entries.pop(oid, None) is not None else 0


class FakeLog:
    def __init__(self) -> None:
        self.text = ""
        self.type = ""
        self.insert_count = 0

    def with_text(self, text, type="", level=""):
        self.text = text
        self.type = type
        return self

    async def insert_log(self, session=None):
        self.insert_count += 1


@pytest.fixture
def notification_repo(monkeypatch):
    repo = FakeNotificationsRepo()
    monkeypatch.setattr(
        backends, "get_notifications_repo", lambda *a, **k: repo
    )
    return repo


def _use_changelog(monkeypatch, entries: list[dict]) -> FakeChangelogRepo:
    repo = FakeChangelogRepo(entries)
    monkeypatch.setattr(backends, "get_changelog_repo", lambda *a, **k: repo)
    return repo


# ---------------------------------------------------------------------------
# 渲染
# ---------------------------------------------------------------------------


def test_build_fields_renders_header_and_item_list() -> None:
    title, content = svc.build_notification_fields(
        _entry("x", "v4.3.0-6", "2026-10-26", ["新增版本公告", "修复导出乱码"])
    )

    assert title == "v4.3.0-6 更新公告"
    assert content == (
        "## v4.3.0-6（2026-10-26）\n\n- 新增版本公告\n- 修复导出乱码"
    )


def test_build_fields_without_items_keeps_header_only() -> None:
    title, content = svc.build_notification_fields(
        _entry("x", "v4.3.0-6", "2026-10-26", [])
    )

    assert title == "v4.3.0-6 更新公告"
    assert content == "## v4.3.0-6（2026-10-26）"


def test_build_fields_clips_title_to_column_width() -> None:
    title, _content = svc.build_notification_fields(
        _entry("x", "v" * 300, "2026-10-26", ["一条"])
    )

    assert len(title) == svc.TITLE_MAX
    assert title == "v" * svc.TITLE_MAX


def test_build_fields_truncates_at_item_boundary_by_chars() -> None:
    items = [f"第{i:02d}条 " + "说" * 90 for i in range(80)]
    _title, content = svc.build_notification_fields(
        _entry("x", "v4.3.0-6", "2026-10-26", items)
    )

    assert len(content) <= svc.CONTENT_MAX_CHARS
    assert len(content.encode("utf-8")) <= svc.CONTENT_MAX_BYTES
    assert content.endswith(svc.TRUNCATED_MARKER)
    kept = content.splitlines()[2:-1]
    assert kept  # 至少留下一条，而不是只剩标记
    for line in kept:
        # 每一行都是一条完整的 items 条目，不会出现半条。
        assert line[2:] in items


def test_build_fields_truncates_on_utf8_bytes_for_chinese() -> None:
    # 1500 个汉字 ≈ 4500 字节：字符数远不到 4000，字节数已经超了。
    items = ["中" * 150 for _ in range(10)]
    _title, content = svc.build_notification_fields(
        _entry("x", "v4.3.0-6", "2026-10-26", items)
    )

    assert len(content) < svc.CONTENT_MAX_CHARS
    assert len(content.encode("utf-8")) <= svc.CONTENT_MAX_BYTES
    assert content.endswith(svc.TRUNCATED_MARKER)
    # 没有切出半个字符（能完整解码且以合法汉字结尾的条目为主）。
    content.encode("utf-8").decode("utf-8")
    kept = content.splitlines()[2:-1]
    assert kept and all(line[2:] == "中" * 150 for line in kept)


def test_build_fields_single_oversized_item_keeps_only_marker() -> None:
    _title, content = svc.build_notification_fields(
        _entry("x", "v4.3.0-6", "2026-10-26", ["长" * 5000])
    )

    assert content == f"## v4.3.0-6（2026-10-26）\n\n{svc.TRUNCATED_MARKER}"
    assert len(content.encode("utf-8")) <= svc.CONTENT_MAX_BYTES


# ---------------------------------------------------------------------------
# 同步
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sync_creates_system_notice_with_source_ref(notification_repo) -> None:
    entry = _entry(NEW_OID, "v4.3.0-6", "2026-10-26", ["新增版本公告"])

    row = await svc.sync_changelog_notification(entry)

    assert row is not None
    assert row["source_ref"] == NEW_OID
    assert row["author_oid"] == svc.SYSTEM_AUTHOR_OID
    assert row["author_name"] == "SYSTEM"
    assert row["active"] is True
    assert row["title"] == "v4.3.0-6 更新公告"
    assert row["content"] == "## v4.3.0-6（2026-10-26）\n\n- 新增版本公告"
    assert len(notification_repo.rows) == 1


@pytest.mark.asyncio
async def test_sync_updates_existing_notice_and_bumps_version(
    notification_repo,
) -> None:
    entry = _entry(NEW_OID, "v4.3.0-6", "2026-10-26", ["旧说明"])
    first = await svc.sync_changelog_notification(entry)
    assert first is not None

    updated = dict(entry, items=["新说明"])
    row = await svc.sync_changelog_notification(updated)

    assert row is not None
    assert row["_id"] == first["_id"]
    assert row["title"] == "v4.3.0-6 更新公告"
    assert row["content"].endswith("- 新说明")
    assert row["version"] == 2
    assert len(notification_repo.rows) == 1
    assert [call[0] for call in notification_repo.calls] == ["create", "update"]


@pytest.mark.asyncio
async def test_sync_is_idempotent_on_rerun(notification_repo) -> None:
    entry = _entry(NEW_OID, "v4.3.0-6", "2026-10-26", ["新增版本公告"])

    first = await svc.sync_changelog_notification(entry)
    second = await svc.sync_changelog_notification(entry)

    assert first is not None and second is not None
    assert first["_id"] == second["_id"]
    assert len(notification_repo.rows) == 1


@pytest.mark.asyncio
async def test_sync_accepts_http_serialized_entry(notification_repo) -> None:
    # 路由传进来的是 _serialize() 之后的形状（主键叫 id，不是 _id）。
    entry = {
        "id": NEW_OID,
        "version": "v4.3.0-6",
        "date": "2026-10-26",
        "items": ["新增版本公告"],
    }

    row = await svc.sync_changelog_notification(entry)

    assert row is not None
    assert row["source_ref"] == NEW_OID
    assert row["content"].endswith("- 新增版本公告")


@pytest.mark.asyncio
async def test_sync_swallows_repo_failure(monkeypatch) -> None:
    class Broken:
        async def get_by_source_ref(self, source_ref):
            raise RuntimeError("ADB 不可用")

    monkeypatch.setattr(backends, "get_notifications_repo", lambda *a, **k: Broken())

    assert await svc.sync_changelog_notification(
        _entry(NEW_OID, "v4.3.0-6", "2026-10-26", ["新增"])
    ) is None


# ---------------------------------------------------------------------------
# 停用
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_deactivate_flips_active_off(notification_repo) -> None:
    entry = _entry(NEW_OID, "v4.3.0-6", "2026-10-26", ["新增"])
    await svc.sync_changelog_notification(entry)

    assert await svc.deactivate_changelog_notification(NEW_OID) is True
    assert notification_repo.rows[next(iter(notification_repo.rows))]["active"] is False


@pytest.mark.asyncio
async def test_deactivate_missing_notice_returns_false(notification_repo) -> None:
    assert await svc.deactivate_changelog_notification(MID_OID) is False
    assert notification_repo.rows == {}


# ---------------------------------------------------------------------------
# 回填
# ---------------------------------------------------------------------------


def _history() -> list[dict]:
    return [
        _entry(OLD_OID, "v4.2.0", "2026-08-01", ["旧版本 A"]),
        _entry(MID_OID, "v4.2.1", "2026-09-01", ["旧版本 B"]),
        _entry(NEW_OID, "v4.3.0-6", "2026-10-26", ["新版本 C"]),
    ]


@pytest.mark.asyncio
async def test_backfill_creates_history_as_inactive(
    monkeypatch, notification_repo
) -> None:
    _use_changelog(monkeypatch, _history())

    stats = await svc.backfill_changelog_notifications()

    assert stats == {"created": 3, "activated": 0, "skipped": 0}
    assert len(notification_repo.rows) == 3
    assert all(row["active"] is False for row in notification_repo.rows.values())
    assert {row["source_ref"] for row in notification_repo.rows.values()} == {
        OLD_OID,
        MID_OID,
        NEW_OID,
    }


@pytest.mark.asyncio
async def test_backfill_activates_only_requested_version(
    monkeypatch, notification_repo
) -> None:
    _use_changelog(monkeypatch, _history())

    stats = await svc.backfill_changelog_notifications("v4.3.0-6")

    assert stats == {"created": 3, "activated": 1, "skipped": 0}
    active = [
        row["source_ref"] for row in notification_repo.rows.values() if row["active"]
    ]
    assert active == [NEW_OID]


@pytest.mark.asyncio
async def test_backfill_is_idempotent_and_keeps_activation(
    monkeypatch, notification_repo
) -> None:
    _use_changelog(monkeypatch, _history())
    await svc.backfill_changelog_notifications("v4.3.0-6")

    stats = await svc.backfill_changelog_notifications("v4.3.0-6")

    assert stats == {"created": 0, "activated": 0, "skipped": 3}
    assert len(notification_repo.rows) == 3
    active = [
        row["source_ref"] for row in notification_repo.rows.values() if row["active"]
    ]
    assert active == [NEW_OID]


# ---------------------------------------------------------------------------
# 路由挂接
# ---------------------------------------------------------------------------


async def _allow_reauth(*_args, **_kwargs):
    """本文件只验业务语义；再认证本身由 ``test_admin_reauth_gate`` 专测。"""
    return None


@pytest.fixture
def api(monkeypatch):
    repo = _use_changelog(
        monkeypatch, [_entry(OLD_OID, "v4.2.0", "2026-08-01", ["旧版本 A"])]
    )
    log = FakeLog()
    synced: list[dict] = []
    deactivated: list[str] = []

    async def fake_sync(entry):
        synced.append(entry)
        return None

    async def fake_deactivate(oid):
        deactivated.append(oid)
        return True

    async def display_name(_oid):
        return "张老师"

    monkeypatch.setattr(changelog_router, "sync_changelog_notification", fake_sync)
    monkeypatch.setattr(
        changelog_router, "deactivate_changelog_notification", fake_deactivate
    )
    monkeypatch.setattr(changelog_router, "get_user_display_name", display_name)
    monkeypatch.setattr(
        changelog_router, "require_password_verification", _allow_reauth
    )

    app = FastAPI()
    app.include_router(changelog_router.router, prefix="/api/changelog")
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    app.dependency_overrides[inject_log] = lambda: log
    return TestClient(app), repo, log, synced, deactivated


def test_post_creates_changelog_then_syncs_notice(api) -> None:
    http, _repo, _log, synced, _deactivated = api

    response = http.post(
        "/api/changelog",
        json={"version": "v4.3.0-6", "date": "2026-10-26", "items": ["新增公告"]},
    )

    assert response.status_code == 201
    assert len(synced) == 1
    assert synced[0]["version"] == "v4.3.0-6"
    assert synced[0]["items"] == ["新增公告"]
    assert response.json() == {
        "id": synced[0]["id"],
        "version": "v4.3.0-6",
        "date": "2026-10-26",
        "items": ["新增公告"],
    }


def test_put_syncs_reread_row(api) -> None:
    http, _repo, _log, synced, _deactivated = api

    response = http.put(
        f"/api/changelog/{OLD_OID}",
        json={"version": "v4.2.1", "date": "2026-09-01", "items": ["改过的说明"]},
    )

    assert response.status_code == 204
    assert len(synced) == 1
    assert synced[0]["id"] == OLD_OID
    assert synced[0]["items"] == ["改过的说明"]


def test_put_missing_row_does_not_sync(api) -> None:
    http, _repo, _log, synced, _deactivated = api

    response = http.put(
        f"/api/changelog/{MID_OID}",
        json={"version": "v9.9.9", "date": "2026-10-26", "items": ["不存在"]},
    )

    assert response.status_code == 204
    assert synced == []


def test_delete_deactivates_notice(api) -> None:
    http, _repo, _log, _synced, deactivated = api

    response = http.delete(f"/api/changelog/{OLD_OID}")

    assert response.status_code == 204
    assert deactivated == [OLD_OID]


def test_non_admin_cannot_trigger_sync(api) -> None:
    http, _repo, _log, synced, deactivated = api
    http.app.dependency_overrides[get_current_user] = lambda: {
        "id": "000000000000000000000002",
        "per": ["student"],
    }

    response = http.post(
        "/api/changelog",
        json={"version": "v4.3.0-6", "date": "2026-10-26", "items": ["x"]},
    )

    assert response.status_code == 403
    assert synced == [] and deactivated == []
