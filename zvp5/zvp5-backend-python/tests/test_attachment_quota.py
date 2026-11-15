"""``POST /api/attachments`` 的配额与附件异常映射。

回归目标（2026-10 审计 P0）：``check_quota()`` 原先在 ``try`` 之外，超配额时
``QuotaExceeded``（``AttachmentError`` 子类）逃出处理器 → 500，而 ``QuotaExceeded``
自己的 ``status_code = 429`` 从未生效。同一条链上 ``read_capped()`` 的
``PayloadTooLarge``（413）也一样裸奔。

钉死三件事：

* 超配额 = **429** + 中文文案（含每日配额数值），且不落任何附件行；
* 其它 ``AttachmentError`` 仍按各自的 ``status_code`` 映射（非法图片 = 422）；
* 配额计数必须发生在读文件之前（超配额请求不再走一遍图片管线）。

离线：files/users 仓储打桩，``inject_log`` 换成假日志；真实的 ``check_quota`` /
``read_capped`` / ``process_upload`` 都在链路上。

    cd <repo root> && .venv/bin/python -m pytest tests/test_attachment_quota.py -q
"""

from __future__ import annotations

import io

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image

from db import backends
from routers import attachments_router
from typings.log import inject_log
from util.attachment import DAILY_MAX_BYTES, DAILY_MAX_FILES, _quota, record_upload
from util.object_id import get_current_user

PREFIX = "/api/attachments"
MANAGER_OID = "64b7f0c2e1a3b4c5d6e7fa01"
FILE_OID = "64b7f0c2e1a3b4c5d6e7fa02"

QUOTA_DETAIL = (
    f"今日上传配额已用尽（每日上限 {DAILY_MAX_FILES} 个 / "
    f"{DAILY_MAX_BYTES // (1024 * 1024)} MB），请明天再试"
)


class _FakeLog:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def with_text(self, text: str, type: str = "", **_kwargs):
        self.texts.append(text)
        return self

    async def insert_log(self) -> None:
        return None


class _FakeUsersRepo:
    async def get_by_oid(self, oid: str):
        return {"_id": oid, "id": oid, "per": ["admin"], "banned": False}


class _SpyFilesRepo:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.rows: dict[str, dict] = {}

    async def find_by_sha256(self, owner_oid, sha256, kind):
        return None

    async def create(self, owner_oid, kind, parent_oid, mime, size_bytes, width,
                     height, sha256, content: bytes, original_name, purpose) -> str:
        oid = FILE_OID if kind == "original" else FILE_OID + "1"
        row = {
            "_id": oid,
            "owner_oid": owner_oid,
            "kind": kind,
            "parent_oid": parent_oid,
            "mime": mime,
            "size_bytes": size_bytes,
            "width": width,
            "height": height,
            "sha256": sha256,
            "original_name": original_name,
            "purpose": purpose,
            "status": "stored",
            "created_at": None,
        }
        self.created.append({**row, "content": content})
        self.rows[oid] = row
        return oid

    async def get_meta(self, oid: str):
        return self.rows.get(oid)

    async def set_entity(self, oid, entity_type, entity_oid) -> None:
        return None

    async def add_event(self, file_oid, actor_oid, action, from_status, to_status, detail) -> str:
        return "64b7f0c2e1a3b4c5d6e7faff"


@pytest.fixture(autouse=True)
def _clean_quota():
    _quota.clear()
    yield
    _quota.clear()


@pytest.fixture
def api(monkeypatch):
    repo = _SpyFilesRepo()
    monkeypatch.setattr(backends, "get_files_repo", lambda *a, **k: repo)
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: _FakeUsersRepo())

    app = FastAPI()
    app.include_router(attachments_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: {
        "_id": MANAGER_OID,
        "id": MANAGER_OID,
        "per": ["admin"],
    }
    app.dependency_overrides[inject_log] = lambda: _FakeLog()
    return TestClient(app), repo


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (12, 8), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


def _upload(http: TestClient, raw: bytes | None = None, name: str = "photo.png"):
    return http.post(
        PREFIX,
        files={"file": (name, raw if raw is not None else _png_bytes(), "image/png")},
        data={"purpose": "volunteer_order", "original_name": name},
    )


def test_quota_exceeded_is_429_with_chinese_detail(api) -> None:
    """当日文件数打满 → 429，detail 是含配额数值的中文文案。"""
    http, repo = api
    for _ in range(DAILY_MAX_FILES):
        record_upload(MANAGER_OID, 1)

    response = _upload(http)

    assert response.status_code == 429, response.text
    assert response.json() == {"detail": QUOTA_DETAIL}
    assert repo.created == []


def test_quota_bytes_exhausted_is_429(api) -> None:
    """字节数打满同样是 429（计数未到上限，走的是另一条判定）。"""
    http, repo = api
    record_upload(MANAGER_OID, DAILY_MAX_BYTES)

    response = _upload(http)

    assert response.status_code == 429, response.text
    assert response.json()["detail"] == QUOTA_DETAIL
    assert repo.created == []


def test_quota_is_counted_per_user(api) -> None:
    """别人的配额用量不影响当前用户：只是回归护栏，证明键包含 user oid。"""
    http, repo = api
    for _ in range(DAILY_MAX_FILES):
        record_upload("64b7f0c2e1a3b4c5d6e7faff", 1)

    response = _upload(http)

    assert response.status_code == 200, response.text
    assert response.json()["code"] == 201
    assert [row["kind"] for row in repo.created] == ["original", "thumb"]


def test_other_attachment_errors_keep_their_mapping(api) -> None:
    """非图片内容仍是 422（``InvalidImage``），没有被配额分支吃掉。"""
    http, repo = api

    response = _upload(http, raw=b"definitely not an image", name="notes.png")

    assert response.status_code == 422, response.text
    assert response.json() == {"detail": "File is not a readable image"}
    assert repo.created == []
