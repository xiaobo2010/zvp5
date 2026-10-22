"""附件读权限：secretary 同班才可读，且只查 owner 一次（离线）。

``_can_read`` 的 secretary 分支原先是「查自己 + 查 owner」两次用户读：调用方
手上的认证结果里已经带着本人 ``class_id``（``util.object_id._authenticate``
返回的 ``class_id`` 键，``None`` = 无班级），再查一次自己纯属浪费，还会在
「本人刚被删/刚换班」的窗口里给出与认证结论不一致的判定。

这里把授权语义原样钉住，同时钉死查询次数：

* 同班 secretary → 200；
* 他班 secretary → 403；
* owner → 200；
* admin → 200（不看班级）；
* 无班级的 secretary → 403；
* 每次判定只读 owner 一行（``lookups == [OWNER]``）。

从仓库根运行::

    ./.venv/bin/python -m pytest tests/golden/attachments/test_attachment_read_permission.py -q
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import attachments_router
from util.object_id import get_current_user

PREFIX = "/api/attachments"
OWNER = "64b7f0c2e1a3b4c5d6e7aa01"
SECRETARY = "64b7f0c2e1a3b4c5d6e7aa02"
OTHER_CLASS_SECRETARY = "64b7f0c2e1a3b4c5d6e7aa03"
ADMIN = "64b7f0c2e1a3b4c5d6e7aa04"
NO_CLASS_SECRETARY = "64b7f0c2e1a3b4c5d6e7aa05"
FILE_OID = "64b7f0c2e1a3b4c5d6e7ab01"
OWNER_CLASS = "64b7f0c2e1a3b4c5d6e7ac01"
OTHER_CLASS = "64b7f0c2e1a3b4c5d6e7ac02"


class _FakeUsersRepo:
    """记录每一次按 oid 的读，用来断言「没有多查一次自己」。"""

    def __init__(self, rows: dict[str, dict]) -> None:
        self.rows = rows
        self.lookups: list[str] = []

    async def get_by_oid(self, oid: str):
        self.lookups.append(oid)
        return self.rows.get(oid)


class _FakeFilesRepo:
    def __init__(self, row: dict) -> None:
        self.row = row

    async def get_meta(self, oid: str):
        return self.row if oid == self.row["_id"] else None


class _FakeLog:
    def with_text(self, *_args, **_kwargs):
        return self

    async def insert_log(self) -> None:
        return None


def _row() -> dict:
    return {
        "_id": FILE_OID,
        "owner_oid": OWNER,
        "kind": "original",
        "mime": "image/png",
        "size_bytes": 123,
        "sha256": "c" * 64,
        "original_name": "x.png",
        "status": "stored",
        "ocr_status": "pending",
        "created_at": None,
    }


def _users() -> _FakeUsersRepo:
    return _FakeUsersRepo(
        {
            OWNER: {"_id": OWNER, "class_id": OWNER_CLASS},
            SECRETARY: {"_id": SECRETARY, "class_id": OWNER_CLASS},
            OTHER_CLASS_SECRETARY: {"_id": OTHER_CLASS_SECRETARY, "class_id": OTHER_CLASS},
            ADMIN: {"_id": ADMIN},
            NO_CLASS_SECRETARY: {"_id": NO_CLASS_SECRETARY},
        }
    )


def _client(monkeypatch, user: dict) -> tuple[TestClient, _FakeUsersRepo]:
    users = _users()
    monkeypatch.setattr(
        attachments_router.backends, "get_files_repo", lambda *a, **k: _FakeFilesRepo(_row())
    )
    monkeypatch.setattr(attachments_router.backends, "get_users_repo", lambda *a, **k: users)
    monkeypatch.setattr(attachments_router, "ZVMSLog", _FakeLog)

    app = FastAPI()
    app.include_router(attachments_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[attachments_router.inject_log] = lambda: _FakeLog()
    return TestClient(app), users


def test_same_class_secretary_can_read_and_queries_the_owner_once(monkeypatch) -> None:
    client, users = _client(
        monkeypatch,
        {"id": SECRETARY, "per": ["secretary"], "class_id": OWNER_CLASS},
    )

    response = client.get(f"{PREFIX}/{FILE_OID}/meta")

    assert response.status_code == 200, response.text
    assert users.lookups == [OWNER], "本人 class_id 取自认证结果，不该再查一次自己"


def test_other_class_secretary_is_denied(monkeypatch) -> None:
    client, users = _client(
        monkeypatch,
        {"id": OTHER_CLASS_SECRETARY, "per": ["secretary"], "class_id": OTHER_CLASS},
    )

    response = client.get(f"{PREFIX}/{FILE_OID}/meta")

    assert response.status_code == 403, response.text
    assert users.lookups == [OWNER]


def test_secretary_without_a_class_is_denied(monkeypatch) -> None:
    client, users = _client(
        monkeypatch, {"id": NO_CLASS_SECRETARY, "per": ["secretary"], "class_id": None}
    )

    response = client.get(f"{PREFIX}/{FILE_OID}/meta")

    assert response.status_code == 403, response.text
    assert users.lookups == [], "无班级连 owner 都不必读"


def test_owner_can_read_own_attachment(monkeypatch) -> None:
    client, users = _client(monkeypatch, {"id": OWNER, "per": ["student"], "class_id": OWNER_CLASS})

    response = client.get(f"{PREFIX}/{FILE_OID}/meta")

    assert response.status_code == 200, response.text
    assert response.json()["data"]["sha256"] == "c" * 64
    assert users.lookups == []


def test_admin_can_read_other_peoples_attachment(monkeypatch) -> None:
    client, users = _client(monkeypatch, {"id": ADMIN, "per": ["admin"], "class_id": None})

    response = client.get(f"{PREFIX}/{FILE_OID}/meta")

    assert response.status_code == 200, response.text
    assert "sha256" not in response.json()["data"], "sha256 只给 owner"
    assert users.lookups == []


def test_stranger_is_denied(monkeypatch) -> None:
    client, users = _client(
        monkeypatch,
        {"id": "64b7f0c2e1a3b4c5d6e7aa09", "per": ["student"], "class_id": OWNER_CLASS},
    )

    response = client.get(f"{PREFIX}/{FILE_OID}/meta")

    assert response.status_code == 403, response.text
    assert users.lookups == []
