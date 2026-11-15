"""``/api/v1/notifications`` 的离线契约测试。

覆盖四件事：

* 权限：用户端只需登录；管理端每个写/管理读请求都只认**当前** ``admin``；
* CRUD：创建/编辑/停用/重新启用/删除的成功与失败语义；
* 版本：全局版本只在成功写时 +1，单条版本每次成功编辑 +1，失败不推进；
* 审计：发布/编辑/删除日志使用数据库里的真实显示名。

全部离线：通知仓储与日志依赖打桩，不连 Oracle。
"""

from __future__ import annotations

import hashlib
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import notifications_router
from typings.log import inject_log
from util.object_id import get_current_user

PREFIX = "/api/v1/notifications"
ADMIN = {"id": "000000000000000000000001", "per": ["admin"]}
STUDENT = {"id": "000000000000000000000002", "per": ["student"]}
CREATED = datetime(2026, 10, 21, 9, 30, 0)
UPDATED = datetime(2026, 10, 21, 10, 15, 0)


def content_hash(title: str, content: str, active: bool) -> str:
    return hashlib.sha256(
        f"{title}\n{content}\n{'1' if active else '0'}".encode("utf-8")
    ).hexdigest()


class FakeNotificationsRepo:
    """In-memory repo with the same version rules as the Oracle implementation."""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.versions: dict[str, list[dict]] = {}
        # (user_oid, notification_oid) -> {"version": int, "read_at": datetime}
        self.reads: dict[tuple[str, str], dict] = {}
        self.version = 0
        self._seq = 0

    def _new_id(self) -> str:
        self._seq += 1
        return f"0d000000000000000000{self._seq:04d}"

    async def get_state(self):
        return {"version": self.version, "updated_at": UPDATED}

    def _ordered(self):
        return sorted(
            self.rows.values(),
            key=lambda row: (row["created_at"], row["_id"]),
            reverse=True,
        )

    async def list_active(self):
        return (
            [dict(row) for row in self._ordered() if row["active"]],
            self.version,
        )

    async def list_all(self):
        return [dict(row) for row in self._ordered()], self.version

    async def get(self, oid):
        row = self.rows.get(oid)
        return dict(row) if row else None

    def _history(self, oid: str) -> list[dict]:
        return self.versions.setdefault(oid, [])

    def _snapshot(
        self,
        oid: str,
        row: dict,
        version: int,
        op: str,
        *,
        author_oid: str | None,
        author_name: str,
        source_version: int | None = None,
    ) -> dict:
        snapshot = {
            "_id": f"0e{self._seq:022d}",
            "notification_oid": oid,
            "version": version,
            "title": row["title"],
            "content": row["content"],
            "active": row["active"],
            "op": op,
            "source_version": source_version,
            "content_hash": content_hash(
                row["title"], row["content"], row["active"]
            ),
            "author_oid": author_oid,
            "author_name": author_name,
            "created_at": UPDATED,
        }
        self._history(oid).append(snapshot)
        return snapshot

    async def create(self, *, title, content, author_oid, author_name):
        oid = self._new_id()
        row = {
            "_id": oid,
            "title": title,
            "content": content,
            "active": True,
            "author_oid": author_oid,
            "author_name": author_name,
            "version": 1,
            "created_at": CREATED,
            "updated_at": CREATED,
        }
        self.rows[oid] = row
        self._snapshot(
            oid,
            row,
            1,
            "create",
            author_oid=author_oid,
            author_name=author_name,
        )
        self.version += 1
        return dict(row), self.version

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
            return None, self.version
        if title is not None:
            row["title"] = title
        if content is not None:
            row["content"] = content
        if active is not None:
            row["active"] = active
        row["version"] += 1
        row["updated_at"] = UPDATED
        self._snapshot(
            oid,
            row,
            row["version"],
            "update",
            author_oid=author_oid or row["author_oid"],
            author_name=author_name or row["author_name"],
        )
        self.version += 1
        return dict(row), self.version

    async def delete(self, oid, *, author_oid=None, author_name=None):
        row = self.rows.get(oid)
        if row is None:
            return None, self.version
        highest = max(
            [snapshot["version"] for snapshot in self._history(oid)] + [row["version"]]
        )
        self._snapshot(
            oid,
            row,
            highest + 1,
            "delete",
            author_oid=author_oid or row["author_oid"],
            author_name=author_name or row["author_name"],
        )
        self.rows.pop(oid)
        # 与 Oracle 实现一致：通知硬删时连同其已读行一起清掉。
        for key in [key for key in self.reads if key[1] == oid]:
            self.reads.pop(key)
        self.version += 1
        return dict(row), self.version

    async def list_versions(self, oid):
        history = sorted(
            self._history(oid), key=lambda snapshot: snapshot["version"], reverse=True
        )
        return [dict(snapshot) for snapshot in history], self.version

    async def rollback(self, oid, *, target_version, author_oid, author_name):
        target = next(
            (
                snapshot
                for snapshot in self._history(oid)
                if snapshot["version"] == target_version
            ),
            None,
        )
        if target is None:
            return None, self.version, None

        row = self.rows.get(oid)
        if row is None:
            highest = max(
                [snapshot["version"] for snapshot in self._history(oid)] + [0]
            )
            row = {
                "_id": oid,
                "title": target["title"],
                "content": target["content"],
                "active": target["active"],
                "author_oid": target["author_oid"] or author_oid,
                "author_name": target["author_name"] or author_name,
                "version": highest + 1,
                "created_at": UPDATED,
                "updated_at": UPDATED,
            }
            self.rows[oid] = row
        else:
            row["title"] = target["title"]
            row["content"] = target["content"]
            row["active"] = target["active"]
            row["version"] += 1
            row["updated_at"] = UPDATED

        snapshot = self._snapshot(
            oid,
            row,
            row["version"],
            "rollback",
            author_oid=author_oid,
            author_name=author_name,
            source_version=target_version,
        )
        self.version += 1
        return dict(row), self.version, snapshot

    # --- 已读状态（V053）--------------------------------------------------

    async def list_read(self, user_oid):
        return {
            notification_oid: record["version"]
            for (owner, notification_oid), record in self.reads.items()
            if owner == user_oid
        }

    async def mark_read(self, user_oid, notification_oid, now):
        row = self.rows.get(notification_oid)
        if row is None:
            return None
        # 版本取通知行的**当前**值：客户端不能补写更大的版本号。
        self.reads[(user_oid, notification_oid)] = {
            "version": row["version"],
            "read_at": now,
        }
        return notification_oid, row["version"]


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


async def _allow_reauth(*_args, **_kwargs):
    """本文件只验业务语义；再认证本身由 ``test_admin_reauth_gate`` 专测。"""
    return None


@pytest.fixture
def api(monkeypatch):
    repo = FakeNotificationsRepo()
    log = FakeLog()

    async def display_name(_oid):
        return "张老师"

    monkeypatch.setattr(
        backends, "get_notifications_repo", lambda *a, **k: repo
    )
    monkeypatch.setattr(notifications_router, "get_user_display_name", display_name)

    monkeypatch.setattr(
        notifications_router, "require_password_verification", _allow_reauth
    )
    app = FastAPI()
    app.include_router(notifications_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    app.dependency_overrides[inject_log] = lambda: log
    return TestClient(app), repo, log


def _as(http, user) -> None:
    http.app.dependency_overrides[get_current_user] = lambda: user


CREATE_PAYLOAD = {"title": "活动调整", "content": "本周活动改到周六。"}


# ---------------------------------------------------------------------------
# 权限
# ---------------------------------------------------------------------------


def test_user_list_allows_any_logged_in_user(api) -> None:
    http, _repo, _log = api
    _as(http, STUDENT)

    response = http.get(PREFIX)
    assert response.status_code == 200
    assert response.json()["data"] == {"version": 0, "notifications": []}


def test_admin_endpoints_reject_non_admin(api) -> None:
    http, repo, log = api
    _as(http, STUDENT)

    assert http.get(f"{PREFIX}/admin").status_code == 403
    assert http.post(PREFIX, json=CREATE_PAYLOAD).status_code == 403
    assert http.put(
        f"{PREFIX}/0d0000000000000000000001", json={"active": False}
    ).status_code == 403
    assert http.delete(f"{PREFIX}/0d0000000000000000000001").status_code == 403

    # 没有写发生，版本也没有被推进，审计日志也不应出现。
    assert repo.version == 0
    assert repo.rows == {}
    assert log.insert_count == 0


# ---------------------------------------------------------------------------
# POST create
# ---------------------------------------------------------------------------


def test_admin_create_binds_author_and_bumps_global_version(api) -> None:
    http, repo, log = api
    response = http.post(PREFIX, json=CREATE_PAYLOAD)

    assert response.status_code == 201
    body = response.json()
    assert body["code"] == 201
    assert body["data"]["version"] == 1

    notice = body["data"]["notification"]
    assert notice["title"] == "活动调整"
    assert notice["content"] == "本周活动改到周六。"
    assert notice["version"] == 1
    # 发布人来自认证 oid + 当前 USERS 显示名，客户端不能伪造。
    assert notice["author_id"] == ADMIN["id"]
    assert notice["author_name"] == "张老师"
    assert notice["active"] is True
    assert notice["created_at"] == "2026-10-21T09:30:00"
    assert notice["updated_at"] == "2026-10-21T09:30:00"

    stored = next(iter(repo.rows.values()))
    assert stored["author_oid"] == ADMIN["id"]
    assert stored["author_name"] == "张老师"
    assert repo.version == 1

    assert log.type == "notification_create"
    assert "张老师" in log.text
    assert "活动调整" in log.text
    assert log.insert_count == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "   ", "content": "正文"},
        {"title": "标题", "content": "   "},
        {"title": "x" * 201, "content": "正文"},
        {"title": "标题", "content": "x" * 4001},
        # 2000 个汉字 = 6000 UTF-8 字节，超过 VARCHAR2(4000) 的字节上限。
        {"title": "标题", "content": "中" * 2000},
        {"title": "标题"},
    ],
)
def test_create_rejects_invalid_payload(api, payload) -> None:
    http, repo, log = api
    assert http.post(PREFIX, json=payload).status_code == 422
    assert repo.rows == {}
    assert repo.version == 0
    assert log.insert_count == 0


# ---------------------------------------------------------------------------
# GET list + update semantics
# ---------------------------------------------------------------------------


def test_user_list_returns_only_active_notices_and_global_version(api) -> None:
    http, repo, _log = api
    http.post(PREFIX, json=CREATE_PAYLOAD)
    second = http.post(
        PREFIX, json={"title": "第二条", "content": "内容"}
    ).json()["data"]["notification"]

    # 停用第二条后，用户端只应看到第一条，但全局版本仍是两次写的和。
    http.put(f"{PREFIX}/{second['id']}", json={"active": False})

    body = http.get(PREFIX).json()
    assert body["data"]["version"] == 3
    assert [row["title"] for row in body["data"]["notifications"]] == ["活动调整"]
    assert body["data"]["notifications"][0]["active"] is True


def test_update_bumps_notice_and_global_but_keeps_author(api) -> None:
    http, repo, log = api
    created = http.post(PREFIX, json=CREATE_PAYLOAD).json()["data"]["notification"]

    body = http.put(
        f"{PREFIX}/{created['id']}",
        json={"title": "活动再次调整", "content": "改到周日。"},
    ).json()

    notice = body["data"]["notification"]
    assert body["data"]["version"] == 2
    assert notice["version"] == 2
    assert notice["title"] == "活动再次调整"
    assert notice["content"] == "改到周日。"
    # 发布人与发送时间不可变
    assert notice["author_id"] == created["author_id"]
    assert notice["author_name"] == created["author_name"]
    assert notice["created_at"] == created["created_at"]
    assert notice["updated_at"] == "2026-10-21T10:15:00"
    assert repo.version == 2

    assert log.type == "notification_update"
    assert "张老师" in log.text
    assert "活动再次调整" in log.text


def test_update_cannot_overwrite_author_fields(api) -> None:
    """客户端塞进来的 author_* 字段必须被忽略；发布人是服务端事实。"""
    http, _repo, _log = api
    created = http.post(PREFIX, json=CREATE_PAYLOAD).json()["data"]["notification"]

    notice = http.put(
        f"{PREFIX}/{created['id']}",
        json={
            "title": "改标题",
            "author_id": "00000000000000000000ff",
            "author_name": "伪造发布人",
            "created_at": "1999-01-01T00:00:00",
        },
    ).json()["data"]["notification"]

    assert notice["author_id"] == ADMIN["id"]
    assert notice["author_name"] == "张老师"
    assert notice["created_at"] == created["created_at"]


def test_deactivate_and_reactivate_are_edits(api) -> None:
    http, repo, _log = api
    created = http.post(PREFIX, json=CREATE_PAYLOAD).json()["data"]["notification"]

    off = http.put(f"{PREFIX}/{created['id']}", json={"active": False}).json()
    assert off["data"]["notification"]["active"] is False
    assert off["data"]["notification"]["version"] == 2
    assert off["data"]["version"] == 2

    admin = http.get(f"{PREFIX}/admin").json()
    assert admin["data"]["version"] == 2
    assert admin["data"]["notifications"][0]["active"] is False

    # 用户端看不到停用通知
    assert http.get(PREFIX).json()["data"]["notifications"] == []

    on = http.put(f"{PREFIX}/{created['id']}", json={"active": True}).json()
    assert on["data"]["notification"]["active"] is True
    assert on["data"]["notification"]["version"] == 3
    assert on["data"]["version"] == 3
    assert [n["id"] for n in http.get(PREFIX).json()["data"]["notifications"]] == [
        created["id"]
    ]
    assert repo.version == 3


def test_update_rejects_empty_payload_bad_oid_and_unknown(api) -> None:
    http, repo, log = api
    created = http.post(PREFIX, json=CREATE_PAYLOAD).json()["data"]["notification"]
    version = repo.version

    assert http.put(f"{PREFIX}/{created['id']}", json={}).status_code == 422
    assert http.put(f"{PREFIX}/not-an-oid", json={"title": "x"}).status_code == 400
    unknown = http.put(
        f"{PREFIX}/0d000000000000000000ffff", json={"title": "x"}
    )
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "通知不存在"

    assert repo.version == version
    assert log.insert_count == 1  # only the create log


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


def test_delete_removes_notice_bumps_version_and_logs(api) -> None:
    http, repo, log = api
    created = http.post(PREFIX, json=CREATE_PAYLOAD).json()["data"]["notification"]

    response = http.delete(f"{PREFIX}/{created['id']}")
    assert response.status_code == 204
    assert repo.rows == {}
    assert repo.version == 2
    assert http.get(PREFIX).json()["data"] == {"version": 2, "notifications": []}
    assert http.get(f"{PREFIX}/admin").json()["data"] == {
        "version": 2,
        "notifications": [],
    }

    assert log.type == "notification_delete"
    assert "张老师" in log.text
    assert "活动调整" in log.text


def test_delete_unknown_is_404_and_does_not_bump(api) -> None:
    http, repo, log = api
    response = http.delete(f"{PREFIX}/0d000000000000000000ffff")

    assert response.status_code == 404
    assert response.json()["detail"] == "通知不存在"
    assert repo.version == 0
    assert log.insert_count == 0


def test_admin_list_shape_includes_inactive(api) -> None:
    http, _repo, _log = api
    created = http.post(PREFIX, json=CREATE_PAYLOAD).json()["data"]["notification"]
    http.put(f"{PREFIX}/{created['id']}", json={"active": False})

    body = http.get(f"{PREFIX}/admin").json()
    assert body["status"] == "ok"
    assert body["code"] == 200
    assert body["data"]["version"] == 2
    assert body["data"]["notifications"][0]["active"] is False
    assert body["data"]["notifications"][0]["author_name"] == "张老师"


def test_admin_list_exposes_source_ref_to_tell_generated_notices_apart(api) -> None:
    """管理端只靠 ``source_ref`` 才能把版本自动公告跟人写的公告分开。

    用户侧列表在 SQL 层就排除了这些条目，管理端不筛（仍可停用/回滚），
    于是前端必须能拿到 ``source_ref`` 才能按需求把它们从管理列表里隐藏。
    """
    http, repo, _log = api
    manual = http.post(PREFIX, json=CREATE_PAYLOAD).json()["data"]["notification"]
    generated_oid = "0d00000000000000000000ff"
    changelog_oid = "0c00000000000000000000aa"
    repo.rows[generated_oid] = {
        "_id": generated_oid,
        "title": "v4.3.0",
        "content": "- 修复若干问题",
        "active": True,
        "author_oid": "000000000000000000000000",
        "author_name": "SYSTEM",
        "version": 1,
        "source_ref": changelog_oid,
        "created_at": UPDATED,
        "updated_at": UPDATED,
    }

    body = http.get(f"{PREFIX}/admin").json()
    notices = {item["id"]: item for item in body["data"]["notifications"]}
    assert notices[manual["id"]]["source_ref"] is None
    assert notices[generated_oid]["source_ref"] == changelog_oid


# ---------------------------------------------------------------------------
# 内容哈希
# ---------------------------------------------------------------------------


def test_view_hash_matches_contract_algorithm(api) -> None:
    http, _repo, _log = api
    created = http.post(PREFIX, json=CREATE_PAYLOAD).json()["data"]["notification"]

    assert created["content_hash"] == content_hash(
        "活动调整", "本周活动改到周六。", True
    )
    assert len(created["content_hash"]) == 64
    assert created["content_hash"] == created["content_hash"].lower()

    edited = http.put(
        f"{PREFIX}/{created['id']}", json={"active": False}
    ).json()["data"]["notification"]
    # active 参与哈希：正文没变，但停用本身也是一次内容变化。
    assert edited["content_hash"] != created["content_hash"]
    assert edited["content_hash"] == content_hash(
        "活动调整", "本周活动改到周六。", False
    )


def test_audit_logs_carry_version_and_hash_prefix(api) -> None:
    http, _repo, log = api
    created = http.post(PREFIX, json=CREATE_PAYLOAD).json()["data"]["notification"]
    assert log.type == "notification_create"
    assert "版本 1" in log.text
    assert created["content_hash"][:12] in log.text

    edited = http.put(
        f"{PREFIX}/{created['id']}", json={"title": "新标题"}
    ).json()["data"]["notification"]
    assert log.type == "notification_update"
    assert "版本 2" in log.text
    assert edited["content_hash"][:12] in log.text

    http.delete(f"{PREFIX}/{created['id']}")
    assert log.type == "notification_delete"
    assert "版本 2" in log.text
    assert edited["content_hash"][:12] in log.text


# ---------------------------------------------------------------------------
# 版本历史
# ---------------------------------------------------------------------------


def test_versions_endpoint_lists_history_newest_first(api) -> None:
    http, _repo, _log = api
    created = http.post(PREFIX, json=CREATE_PAYLOAD).json()["data"]["notification"]
    http.put(f"{PREFIX}/{created['id']}", json={"title": "改过的标题"})

    body = http.get(f"{PREFIX}/{created['id']}/versions").json()

    assert body["status"] == "ok"
    assert body["code"] == 200
    assert body["data"]["version"] == 2
    versions = body["data"]["versions"]
    assert [row["version"] for row in versions] == [2, 1]
    assert [row["op"] for row in versions] == ["update", "create"]
    assert versions[1]["content_hash"] == created["content_hash"]
    assert versions[0]["title"] == "改过的标题"
    assert versions[0]["source_version"] is None
    assert versions[0]["author_id"] == ADMIN["id"]
    assert versions[0]["author_name"] == "张老师"
    assert versions[0]["created_at"] == "2026-10-21T10:15:00"


def test_versions_requires_admin_and_valid_oid(api) -> None:
    http, _repo, log = api
    created = http.post(PREFIX, json=CREATE_PAYLOAD).json()["data"]["notification"]
    _as(http, STUDENT)

    assert http.get(f"{PREFIX}/{created['id']}/versions").status_code == 403
    assert http.post(
        f"{PREFIX}/{created['id']}/rollback", json={"version": 1}
    ).status_code == 403
    assert log.insert_count == 1  # 只有创建那条

    _as(http, ADMIN)
    assert http.get(f"{PREFIX}/not-an-oid/versions").status_code == 400
    assert http.post(
        f"{PREFIX}/not-an-oid/rollback", json={"version": 1}
    ).status_code == 400


def test_versions_of_unknown_notice_is_empty_list(api) -> None:
    http, _repo, _log = api
    body = http.get(f"{PREFIX}/0d000000000000000000ffff/versions").json()
    assert body["data"] == {"versions": [], "version": 0}


def test_versions_survive_delete(api) -> None:
    http, repo, _log = api
    created = http.post(PREFIX, json=CREATE_PAYLOAD).json()["data"]["notification"]
    http.delete(f"{PREFIX}/{created['id']}")

    body = http.get(f"{PREFIX}/{created['id']}/versions").json()
    versions = body["data"]["versions"]

    assert [row["op"] for row in versions] == ["delete", "create"]
    assert [row["version"] for row in versions] == [2, 1]
    assert versions[0]["active"] is True  # 删除前的状态
    assert versions[0]["content_hash"] == created["content_hash"]
    assert body["data"]["version"] == repo.version == 2


# ---------------------------------------------------------------------------
# 回滚
# ---------------------------------------------------------------------------


def test_rollback_existing_notice_appends_revision(api) -> None:
    http, repo, log = api
    created = http.post(PREFIX, json=CREATE_PAYLOAD).json()["data"]["notification"]
    edited = http.put(
        f"{PREFIX}/{created['id']}", json={"title": "改过的标题"}
    ).json()["data"]["notification"]

    body = http.post(
        f"{PREFIX}/{created['id']}/rollback", json={"version": 1}
    ).json()

    notice = body["data"]["notification"]
    assert body["code"] == 200
    assert notice["id"] == created["id"]
    assert notice["title"] == "活动调整"
    assert notice["content"] == "本周活动改到周六。"
    assert notice["active"] is True
    assert notice["version"] == 3
    assert body["data"]["version"] == 3
    assert body["data"]["content_hash"] == created["content_hash"]
    assert repo.version == 3

    versions = http.get(f"{PREFIX}/{created['id']}/versions").json()["data"][
        "versions"
    ]
    assert [row["op"] for row in versions] == ["rollback", "update", "create"]
    assert versions[0]["source_version"] == 1
    assert versions[0]["content_hash"] == created["content_hash"]
    # 回滚只还原内容；发布人和发送时间仍是不可变事实。
    assert edited["author_id"] == notice["author_id"]
    assert notice["created_at"] == created["created_at"]

    assert log.type == "notification_rollback"
    assert "版本 1" in log.text
    assert "当前版本 3" in log.text
    assert notice["content_hash"][:12] in log.text


def test_rollback_deleted_notice_revives_same_oid(api) -> None:
    http, repo, log = api
    created = http.post(PREFIX, json=CREATE_PAYLOAD).json()["data"]["notification"]
    http.put(f"{PREFIX}/{created['id']}", json={"content": "改过的正文。"})
    http.delete(f"{PREFIX}/{created['id']}")
    assert repo.rows == {}

    body = http.post(
        f"{PREFIX}/{created['id']}/rollback", json={"version": 1}
    ).json()

    notice = body["data"]["notification"]
    assert notice["id"] == created["id"]
    assert notice["content"] == "本周活动改到周六。"
    assert notice["version"] == 4  # 历史最大版本 3（create/update/delete）+ 1
    assert body["data"]["content_hash"] == created["content_hash"]
    assert repo.rows[created["id"]]["version"] == 4
    assert http.get(PREFIX).json()["data"]["notifications"][0]["id"] == created["id"]

    versions = http.get(f"{PREFIX}/{created['id']}/versions").json()["data"][
        "versions"
    ]
    assert [row["op"] for row in versions] == [
        "rollback",
        "delete",
        "update",
        "create",
    ]
    assert versions[0]["source_version"] == 1
    assert log.type == "notification_rollback"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"version": "1"},
        {"version": 0},
        {"version": -1},
        {"version": 1.5},
        {"version": True},
        {"version": None},
    ],
)
def test_rollback_rejects_invalid_version(api, payload) -> None:
    http, repo, log = api
    created = http.post(PREFIX, json=CREATE_PAYLOAD).json()["data"]["notification"]
    version = repo.version
    log.insert_count = 0

    response = http.post(f"{PREFIX}/{created['id']}/rollback", json=payload)
    assert response.status_code == 400
    assert response.json()["detail"] == "版本号非法"
    assert repo.version == version
    assert log.insert_count == 0


def test_rollback_unknown_version_is_404(api) -> None:
    http, repo, log = api
    created = http.post(PREFIX, json=CREATE_PAYLOAD).json()["data"]["notification"]
    log.insert_count = 0

    response = http.post(f"{PREFIX}/{created['id']}/rollback", json={"version": 99})
    assert response.status_code == 404
    assert response.json()["detail"] == "版本不存在"
    assert repo.version == 1
    assert log.insert_count == 0


# ---------------------------------------------------------------------------
# 已读状态（V053，跟着账号走）
# ---------------------------------------------------------------------------


def test_read_map_is_empty_for_a_fresh_account(api) -> None:
    http, _repo, _log = api
    _as(http, STUDENT)

    body = http.get(f"{PREFIX}/read").json()
    assert body["status"] == "ok"
    assert body["code"] == 200
    assert body["data"] == {"read": {}}


def test_any_logged_in_user_can_mark_read_and_list_read(api) -> None:
    """已读是用户自己的状态：非 admin 也能读写，且不需要管理端权限。"""
    http, repo, log = api
    created = http.post(PREFIX, json=CREATE_PAYLOAD).json()["data"]["notification"]
    log.insert_count = 0

    _as(http, STUDENT)
    response = http.post(f"{PREFIX}/{created['id']}/read")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["code"] == 200
    assert body["data"] == {"oid": created["id"], "version": 1}

    assert http.get(f"{PREFIX}/read").json()["data"] == {
        "read": {created["id"]: 1}
    }
    # 标记已读不写审计日志，也不推进任何版本号。
    assert log.insert_count == 0
    assert repo.version == 1


def test_mark_read_is_idempotent_and_refreshes_to_current_version(api) -> None:
    http, repo, _log = api
    created = http.post(PREFIX, json=CREATE_PAYLOAD).json()["data"]["notification"]
    _as(http, STUDENT)

    first = http.post(f"{PREFIX}/{created['id']}/read").json()
    second = http.post(f"{PREFIX}/{created['id']}/read").json()

    assert first["data"] == second["data"] == {"oid": created["id"], "version": 1}
    # 同一 (user, notification) 只有一行：重复标记不会长成流水。
    assert len(repo.reads) == 1
    assert repo.version == 1  # 全局版本没被标记已读推进

    # 通知被编辑后 version + 1；再标记一次，落的是**当前**版本。
    _as(http, ADMIN)
    edited = http.put(
        f"{PREFIX}/{created['id']}", json={"title": "改过的标题"}
    ).json()["data"]["notification"]
    _as(http, STUDENT)
    third = http.post(f"{PREFIX}/{created['id']}/read").json()
    assert third["data"]["version"] == edited["version"] == 2
    assert len(repo.reads) == 1
    assert http.get(f"{PREFIX}/read").json()["data"] == {
        "read": {created["id"]: 2}
    }


def test_edit_after_read_does_not_auto_invalidate_the_read_row(api) -> None:
    """编辑通知不写回已读行：旧的 version 留着，靠客户端比较判定为未读。

    服务端若替所有人把已读行刷成新版本，等于「编辑即已读」，用户永远看不到
    这条通知又改了 —— 那才是真正的语义错误。
    """
    http, repo, _log = api
    created = http.post(PREFIX, json=CREATE_PAYLOAD).json()["data"]["notification"]
    _as(http, STUDENT)
    http.post(f"{PREFIX}/{created['id']}/read")

    _as(http, ADMIN)
    edited = http.put(
        f"{PREFIX}/{created['id']}", json={"content": "改过的正文。"}
    ).json()["data"]["notification"]

    # 服务端存的仍是读那一刻的版本；客户端据 notice.version 判未读。
    _as(http, STUDENT)
    assert http.get(f"{PREFIX}/read").json()["data"]["read"] == {created["id"]: 1}
    assert edited["version"] == 2
    assert repo.reads[(STUDENT["id"], created["id"])]["version"] == 1
    assert edited["version"] > repo.reads[(STUDENT["id"], created["id"])]["version"]


def test_read_state_is_per_user(api) -> None:
    http, _repo, _log = api
    created = http.post(PREFIX, json=CREATE_PAYLOAD).json()["data"]["notification"]
    second = http.post(
        PREFIX, json={"title": "第二条", "content": "内容"}
    ).json()["data"]["notification"]

    _as(http, STUDENT)
    http.post(f"{PREFIX}/{created['id']}/read")

    _as(http, ADMIN)
    assert http.get(f"{PREFIX}/read").json()["data"] == {"read": {}}
    http.post(f"{PREFIX}/{second['id']}/read")
    assert http.get(f"{PREFIX}/read").json()["data"] == {
        "read": {second["id"]: 1}
    }

    _as(http, STUDENT)
    assert http.get(f"{PREFIX}/read").json()["data"] == {
        "read": {created["id"]: 1}
    }


def test_mark_read_unknown_notice_is_404(api) -> None:
    http, repo, _log = api
    _as(http, STUDENT)

    response = http.post(f"{PREFIX}/0d000000000000000000ffff/read")
    assert response.status_code == 404
    assert response.json()["detail"] == "通知不存在"
    assert repo.reads == {}


def test_mark_read_rejects_malformed_oid(api) -> None:
    http, repo, _log = api
    _as(http, STUDENT)

    response = http.post(f"{PREFIX}/not-an-oid/read")
    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid Object ID"
    assert repo.reads == {}


def test_delete_drops_the_read_rows_of_that_notice(api) -> None:
    http, repo, _log = api
    created = http.post(PREFIX, json=CREATE_PAYLOAD).json()["data"]["notification"]
    _as(http, STUDENT)
    http.post(f"{PREFIX}/{created['id']}/read")
    assert repo.reads

    _as(http, ADMIN)
    assert http.delete(f"{PREFIX}/{created['id']}").status_code == 204
    assert repo.reads == {}
    assert http.get(f"{PREFIX}/read").json()["data"] == {"read": {}}


def test_list_responses_never_carry_per_user_read_state(api) -> None:
    """列表缓存的是对所有人相同的那一份，掺进 per-user 状态就会串号。"""
    http, _repo, _log = api
    created = http.post(PREFIX, json=CREATE_PAYLOAD).json()["data"]["notification"]
    _as(http, STUDENT)
    http.post(f"{PREFIX}/{created['id']}/read")

    user_body = http.get(PREFIX).json()
    _as(http, ADMIN)
    admin_body = http.get(f"{PREFIX}/admin").json()

    assert set(user_body["data"]) == {"version", "notifications"}
    assert set(admin_body["data"]) == {"version", "notifications"}
    for notice in user_body["data"]["notifications"]:
        assert "read" not in notice
        assert "read_at" not in notice
    for notice in admin_body["data"]["notifications"]:
        assert "read" not in notice
        assert "read_at" not in notice
