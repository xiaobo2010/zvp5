"""管理员附件审计面：列表过滤 / 单条详情 / 状态裁决。

三条链路各自的「静默出错」点，这里逐个钉住：

* **列表过滤**：参数必须真的落到仓储（过滤条件解析错一天，审计界面就少一批
  图）；非法取值必须 400，不能返回空页（空页等于「没有素材」，没人会去查）；
  ``scope=all`` 的 manager 闸不能被新增的 ``owner_oid`` 参数绕过。
* **``has_ocr``**：整页一次聚合查询。逐行 ``get_ocr`` 会把每行的 CLOB + raw
  JSON 拉回来只为回答一个布尔值，所以这里同时断言「批查询 1 次、逐行查询 0 次」。
* **状态裁决**：400 / 409 / 200 三种结局，以及 ``FILE_EVENTS`` 落的动作名必须
  是 ``audit_verified`` / ``audit_rejected``（与旧 ``/verify`` 的 ``verify`` 分开，
  否则事件线里分不清「审计判的」和「上传者自查证」）。

离线：files/users 仓储逐方法打桩，``inject_log`` 换假日志；不连 ADB。

    cd <repo root> && .venv/bin/python -m pytest tests/test_attachment_audit.py -q
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from db.repos.oracle import files as files_repo
from db.repos.oracle.files import OracleFilesRepo
from routers import attachments_router
from typings.log import inject_log
from util.object_id import get_current_user

PREFIX = "/api/attachments"
ADMIN_OID = "64b7f0c2e1a3b4c5d6e7fa01"
DEPT_OID = "64b7f0c2e1a3b4c5d6e7fa02"
STUDENT_OID = "64b7f0c2e1a3b4c5d6e7fa03"
OWNER_OID = "64b7f0c2e1a3b4c5d6e7fa04"
OTHER_OID = "64b7f0c2e1a3b4c5d6e7fa05"

CREATED = datetime(2026, 10, 1, 9, 30, 0)


class _FakeLog:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def with_text(self, text: str, **_kwargs):
        self.texts.append(text)
        return self

    async def insert_log(self) -> None:
        return None


class _FakeUsersRepo:
    def __init__(self, owners: dict[str, dict] | None = None) -> None:
        self.owners = owners or {}
        self.calls: list[str] = []

    async def get_by_oid(self, oid: str):
        self.calls.append(oid)
        return self.owners.get(oid)


class _StubFilesRepo:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = rows or []
        self.meta: dict[str, dict] = {}
        self.ocr_rows: dict[str, dict | None] = {}
        self.events: list[dict] = []
        self.list_calls: list[dict] = []
        self.ocr_batches: list[list[str]] = []
        self.get_ocr_calls: list[str] = []
        self.event_calls: list[tuple[str, int | None]] = []
        self.status_writes: list[tuple[str, str]] = []
        self.events_written: list[dict] = []

    async def list_page(self, owner, purpose, status, entity_type, entity_oid, page, size, **filters):
        self.list_calls.append(
            {
                "owner": owner,
                "purpose": purpose,
                "status": status,
                "entity_type": entity_type,
                "entity_oid": entity_oid,
                "page": page,
                "size": size,
                **filters,
            }
        )
        return self.rows, len(self.rows)

    async def file_oids_with_ocr(self, oids: list[str]) -> set[str]:
        self.ocr_batches.append(list(oids))
        return {oid for oid in oids if self.ocr_rows.get(oid)}

    async def get_meta(self, oid: str):
        return self.meta.get(oid)

    async def get_ocr(self, oid: str):
        self.get_ocr_calls.append(oid)
        return self.ocr_rows.get(oid)

    async def list_events(self, oid: str, *, limit=None):
        self.event_calls.append((oid, limit))
        return list(self.events)

    async def set_status(self, oid: str, status: str) -> None:
        self.status_writes.append((oid, status))
        row = self.meta.get(oid)
        if row is not None:
            row["status"] = status

    async def add_event(self, file_oid, actor_oid, action, from_status, to_status, detail) -> str:
        self.events_written.append(
            {
                "file_oid": file_oid,
                "actor_oid": actor_oid,
                "action": action,
                "from_status": from_status,
                "to_status": to_status,
                "detail": detail,
            }
        )
        return "64b7f0c2e1a3b4c5d6e7faff"


def _row(oid: str, **overrides) -> dict:
    row = {
        "_id": oid,
        "owner_oid": OWNER_OID,
        "kind": "original",
        "mime": "image/jpeg",
        "size_bytes": 1024,
        "width": 800,
        "height": 600,
        "sha256": "a" * 64,
        "original_name": "order.jpg",
        "purpose": "volunteer_order",
        "entity_type": None,
        "entity_oid": None,
        "status": "stored",
        "ocr_status": "done",
        "created_at": CREATED,
        "updated_at": CREATED,
        "deleted_at": None,
    }
    row.update(overrides)
    return row


@pytest.fixture
def harness(monkeypatch):
    repo = _StubFilesRepo()
    users = _FakeUsersRepo(
        {
            OWNER_OID: {"_id": OWNER_OID, "id": "2026007", "name": "李四"},
            ADMIN_OID: {"_id": ADMIN_OID, "id": "2026001", "name": "管理员"},
        }
    )
    monkeypatch.setattr(backends, "get_files_repo", lambda *a, **k: repo)
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: users)

    state = {"user": {"_id": ADMIN_OID, "id": ADMIN_OID, "per": ["admin"]}}

    app = FastAPI()
    app.include_router(attachments_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    app.dependency_overrides[inject_log] = lambda: _FakeLog()

    return SimpleNamespace(
        client=TestClient(app), repo=repo, users=users, state=state
    )


def _as(state, oid: str, per: list[str]) -> None:
    state["user"] = {"_id": oid, "id": oid, "per": per}


# ---------------------------------------------------------------------------
# 1. 列表过滤
# ---------------------------------------------------------------------------


def test_list_forwards_audit_filters_to_the_repository(harness):
    response = harness.client.get(
        f"{PREFIX}",
        params={
            "scope": "all",
            "owner_oid": OWNER_OID,
            "created_from": "2026-10-01",
            "created_to": "2026-10-01",
            "ocr_status": "done",
            "kind": "original",
        },
    )

    assert response.status_code == 200, response.text
    call = harness.repo.list_calls[-1]
    assert call["owner"] == OWNER_OID
    assert call["ocr_status"] == "done"
    assert call["kind"] == "original"
    assert call["created_from"] == datetime(2026, 10, 1, 0, 0, 0)
    # created_to 是半开上界：只给日期时「含当天」= 次日 00:00。
    assert call["created_to"] == datetime(2026, 10, 2, 0, 0, 0)


def test_list_keeps_a_full_timestamp_as_is(harness):
    response = harness.client.get(
        f"{PREFIX}",
        params={"scope": "all", "created_to": "2026-10-01T12:34:56"},
    )

    assert response.status_code == 200, response.text
    call = harness.repo.list_calls[-1]
    assert call["created_to"] == datetime(2026, 10, 1, 12, 34, 56)


@pytest.mark.parametrize(
    "params",
    [
        {"ocr_status": "bogus"},
        {"kind": "bogus"},
        {"created_from": "not-a-date"},
        {"created_to": "2026-13-45"},
    ],
)
def test_list_rejects_unknown_filter_values(harness, params):
    response = harness.client.get(f"{PREFIX}", params={"scope": "all", **params})

    assert response.status_code == 400, response.text
    # 非法过滤条件不能落到仓储：否则会变成「没有数据」的假答案。
    assert harness.repo.list_calls == []


def test_scope_all_is_still_manager_gated_with_owner_filter(harness):
    _as(harness.state, STUDENT_OID, ["student"])

    response = harness.client.get(
        f"{PREFIX}", params={"scope": "all", "owner_oid": OTHER_OID}
    )

    assert response.status_code == 403
    assert harness.repo.list_calls == []


def test_department_may_audit_every_owner(harness):
    _as(harness.state, DEPT_OID, ["department"])

    response = harness.client.get(
        f"{PREFIX}", params={"scope": "all", "owner_oid": OTHER_OID}
    )

    assert response.status_code == 200, response.text
    assert harness.repo.list_calls[-1]["owner"] == OTHER_OID


def test_scope_mine_ignores_the_owner_filter(harness):
    _as(harness.state, STUDENT_OID, ["student"])

    response = harness.client.get(f"{PREFIX}", params={"owner_oid": OTHER_OID})

    assert response.status_code == 200, response.text
    # 同一个参数名在 scope=mine 下不得变成「换个 owner」的入口。
    assert harness.repo.list_calls[-1]["owner"] == STUDENT_OID


def test_invalid_owner_oid_is_400(harness):
    response = harness.client.get(
        f"{PREFIX}", params={"scope": "all", "owner_oid": "not-an-oid"}
    )

    assert response.status_code == 400
    assert harness.repo.list_calls == []


# ---------------------------------------------------------------------------
# 2. has_ocr：整页一次查询
# ---------------------------------------------------------------------------


def test_has_ocr_costs_one_batch_query_per_page(harness):
    first, second, third = (f"64b7f0c2e1a3b4c5d6e7fa0{i}" for i in range(1, 4))
    harness.repo.rows = [_row(first), _row(second), _row(third)]
    harness.repo.ocr_rows = {first: {"text": "abc", "raw": {}}, third: {"text": "x", "raw": {}}}

    response = harness.client.get(f"{PREFIX}", params={"scope": "all"})

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert [item["has_ocr"] for item in data] == [True, False, True]

    # 一次批查询带全页 oid；逐行 get_ocr 一次都不该发生。
    assert harness.repo.ocr_batches == [[first, second, third]]
    assert harness.repo.get_ocr_calls == []


def test_list_items_stay_backwards_compatible(harness):
    oid = "64b7f0c2e1a3b4c5d6e7fa11"
    harness.repo.rows = [_row(oid)]

    item = harness.client.get(f"{PREFIX}", params={"scope": "all"}).json()["data"][0]

    # 现有字段一个不少（旧前端照旧能渲染），只是多了两个字段。
    assert set(item) >= {
        "_id",
        "kind",
        "mime",
        "size_bytes",
        "width",
        "height",
        "purpose",
        "original_name",
        "entity_type",
        "entity_oid",
        "status",
        "ocr_status",
        "created_at",
        "owner_oid",
    }
    assert item["deleted_at"] is None
    assert item["has_ocr"] is False


def test_file_oids_with_ocr_runs_a_single_statement():
    """仓储层：3 个 oid 只发 1 条语句（分片只在 >500 时才有第二条）。"""

    class _Result:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return self._rows

    class _Session:
        def __init__(self) -> None:
            self.statements = []

        async def execute(self, stmt):
            self.statements.append(stmt)
            return _Result([(oid,) for oid in ("a" * 24, "b" * 24)])

    session = _Session()
    repo = OracleFilesRepo(session=session)
    found = asyncio.run(repo.file_oids_with_ocr(["a" * 24, "b" * 24, "c" * 24]))

    assert found == {"a" * 24, "b" * 24}
    assert len(session.statements) == 1
    assert "DISTINCT" in str(session.statements[0]).upper()


# ---------------------------------------------------------------------------
# 3. 单条审计详情
# ---------------------------------------------------------------------------


def _seed_audit_rows(harness) -> str:
    oid = "64b7f0c2e1a3b4c5d6e7fa21"
    harness.repo.meta[oid] = _row(oid)
    harness.repo.ocr_rows[oid] = {
        "engine": "paddle",
        "engine_version": "v3",
        "text": "王五 2026级 志愿时长 3",
        "confidence": 0.93,
        "raw": {"_zvms_lines": [{"text": "王五", "box": [1, 2, 3, 4]}]},
        "created_at": CREATED,
    }
    harness.repo.events = [
        {
            "_id": "64b7f0c2e1a3b4c5d6e7fa22",
            "actor_oid": OWNER_OID,
            "action": "upload",
            "from_status": None,
            "to_status": "stored",
            "detail": "",
            "created_at": CREATED,
        }
    ]
    return oid


def test_audit_detail_returns_owner_ocr_and_timeline(harness):
    oid = _seed_audit_rows(harness)

    response = harness.client.get(f"{PREFIX}/{oid}/audit")

    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["_id"] == oid
    assert data["status"] == "stored"
    # owner 学号/姓名：一次 get_by_oid，不按行扫。
    assert data["owner"] == {"_id": OWNER_OID, "student_id": "2026007", "name": "李四"}
    assert harness.users.calls == [OWNER_OID]

    assert data["has_ocr"] is True
    assert data["ocr"]["text"] == "王五 2026级 志愿时长 3"
    assert data["ocr"]["lines"] == [{"text": "王五", "box": [1, 2, 3, 4]}]
    assert data["ocr"]["status"] == "done"
    # 与前端 ``AttachmentAuditDetail`` 对齐的扁平字段（同一份数据的第二种形态）。
    assert data["owner_name"] == "李四"
    assert data["owner_id"] == "2026007"
    assert data["ocr_text"] == "王五 2026级 志愿时长 3"
    assert data["ocr_engine"] == "paddle"
    assert data["ocr_confidence"] == 0.93
    assert data["lines"] == [{"text": "王五", "box": [1, 2, 3, 4]}]

    assert data["events"][0]["action"] == "upload"
    assert data["events"][0]["created_at"] == CREATED.isoformat()
    # 时间线窗口固定 50 条（最近的 50 条，仍按时间正序返回）。
    assert data["events_limit"] == 50
    assert harness.repo.event_calls == [(oid, 50)]


def test_audit_detail_does_not_leak_sha_to_department(harness):
    _as(harness.state, DEPT_OID, ["department"])
    oid = _seed_audit_rows(harness)

    data = harness.client.get(f"{PREFIX}/{oid}/audit").json()["data"]

    # sha256 仍是 owner/admin 专属（design §8.6 / P1-5）。
    assert "sha256" not in data


def test_audit_detail_includes_sha_for_admin(harness):
    oid = _seed_audit_rows(harness)

    data = harness.client.get(f"{PREFIX}/{oid}/audit").json()["data"]

    assert data["sha256"] == "a" * 64


def test_audit_detail_without_ocr_reports_empty_lines(harness):
    oid = _seed_audit_rows(harness)
    harness.repo.ocr_rows.pop(oid)

    data = harness.client.get(f"{PREFIX}/{oid}/audit").json()["data"]

    assert data["has_ocr"] is False
    assert data["ocr"]["text"] is None
    assert data["ocr"]["lines"] == []
    assert data["ocr_text"] is None
    assert data["lines"] == []


def test_audit_detail_requires_manager(harness):
    _as(harness.state, STUDENT_OID, ["student"])
    oid = _seed_audit_rows(harness)

    response = harness.client.get(f"{PREFIX}/{oid}/audit")

    assert response.status_code == 403
    assert harness.repo.event_calls == []


def test_audit_detail_missing_row_is_404(harness):
    response = harness.client.get(f"{PREFIX}/64b7f0c2e1a3b4c5d6e7fa99/audit")
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 4. 状态裁决
# ---------------------------------------------------------------------------


def test_verdict_writes_status_and_event(harness):
    oid = _seed_audit_rows(harness)

    response = harness.client.patch(
        f"{PREFIX}/{oid}/status", json={"status": "verified", "note": "清楚可辨"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {
        "status": "verified",
        "previous_status": "stored",
        "note": "清楚可辨",
    }
    assert harness.repo.status_writes == [(oid, "verified")]
    assert harness.repo.events_written == [
        {
            "file_oid": oid,
            "actor_oid": ADMIN_OID,
            "action": "audit_verified",
            "from_status": "stored",
            "to_status": "verified",
            "detail": "清楚可辨",
        }
    ]


def test_rejection_uses_its_own_action(harness):
    oid = _seed_audit_rows(harness)

    response = harness.client.patch(
        f"{PREFIX}/{oid}/status", json={"status": "rejected"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["note"] == ""
    assert harness.repo.events_written[0]["action"] == "audit_rejected"
    assert harness.repo.events_written[0]["detail"] == ""


def test_note_is_truncated_to_500(harness):
    oid = _seed_audit_rows(harness)

    response = harness.client.patch(
        f"{PREFIX}/{oid}/status", json={"status": "verified", "note": "x" * 600}
    )

    assert response.status_code == 200, response.text
    assert len(response.json()["data"]["note"]) == 500
    assert len(harness.repo.events_written[0]["detail"]) == 500


@pytest.mark.parametrize("status", ["deleted", "stored", "ready", "", "VERIFIED"])
def test_invalid_verdict_is_400_without_side_effects(harness, status):
    oid = _seed_audit_rows(harness)

    response = harness.client.patch(
        f"{PREFIX}/{oid}/status", json={"status": status}
    )

    assert response.status_code == 400, response.text
    assert harness.repo.status_writes == []
    assert harness.repo.events_written == []


@pytest.mark.parametrize("current", ["verified", "rejected"])
def test_repeated_verdict_is_409(harness, current):
    oid = _seed_audit_rows(harness)
    harness.repo.meta[oid]["status"] = current

    response = harness.client.patch(
        f"{PREFIX}/{oid}/status", json={"status": current}
    )

    assert response.status_code == 409, response.text
    assert harness.repo.status_writes == []
    assert harness.repo.events_written == []


def test_deleted_row_cannot_be_adjudicated(harness):
    oid = _seed_audit_rows(harness)
    harness.repo.meta[oid]["status"] = "deleted"

    response = harness.client.patch(
        f"{PREFIX}/{oid}/status", json={"status": "verified"}
    )

    assert response.status_code == 409, response.text
    assert harness.repo.status_writes == []


def test_verdict_requires_manager(harness):
    _as(harness.state, STUDENT_OID, ["student"])
    oid = _seed_audit_rows(harness)

    response = harness.client.patch(
        f"{PREFIX}/{oid}/status", json={"status": "verified"}
    )

    assert response.status_code == 403
    assert harness.repo.status_writes == []


def test_verdict_missing_row_is_404(harness):
    response = harness.client.patch(
        f"{PREFIX}/64b7f0c2e1a3b4c5d6e7fa99/status", json={"status": "verified"}
    )

    assert response.status_code == 404


def test_verdict_on_switching_between_verdicts_is_allowed(harness):
    """已裁决 → 另一种裁决：允许（改判是审计的日常），事件线记下 from/to。"""
    oid = _seed_audit_rows(harness)
    harness.repo.meta[oid]["status"] = "verified"

    response = harness.client.patch(
        f"{PREFIX}/{oid}/status", json={"status": "rejected", "note": "复核推翻"}
    )

    assert response.status_code == 200, response.text
    assert harness.repo.events_written[0]["from_status"] == "verified"
    assert harness.repo.events_written[0]["to_status"] == "rejected"


def test_repo_list_page_accepts_new_filters_offline():
    """仓储签名断言：新过滤参数是 keyword-only，旧的位置调用不受影响。"""
    import inspect

    signature = inspect.signature(files_repo.OracleFilesRepo.list_page)
    filters = signature.parameters
    for name in ("created_from", "created_to", "ocr_status", "kind"):
        assert filters[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert filters[name].default is None
    # 旧的六个位置参数顺序不变（其它调用点按位置传）。
    positional = [
        name
        for name, param in filters.items()
        if param.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    ]
    assert positional == [
        "self",
        "owner_oid",
        "purpose",
        "status",
        "entity_type",
        "entity_oid",
        "page",
        "size",
    ]
