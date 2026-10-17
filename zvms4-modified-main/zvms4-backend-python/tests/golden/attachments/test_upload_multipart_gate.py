"""Offline tests for the ``POST /api/attachments`` multipart gate.

Regression target (2026-10, reproduced end to end): the endpoint read the form
by hand (``await request.form()``) and then validated the ``file`` part with
``isinstance(upload, fastapi.UploadFile)``. ``fastapi.UploadFile`` *subclasses*
``starlette.datastructures.UploadFile`` while ``request.form()`` always yields the
Starlette base class (FastAPI only rewraps on the declarative ``File(...)``
injection path), so the check was False for every real upload and every
file-bearing request got 400 ``Missing file part``.

The endpoint is exercised through a throwaway ``FastAPI()`` with the production
prefix (``main.py``: ``include_router(attachments_router.router,
prefix="/api/attachments")``); importing ``main`` would pull in APScheduler,
socket.io and the compute-time task graph. Only the non-tested periphery is
stubbed: the users/files repositories (fake spies), the current user and the
request log. The tested path is real, including the multipart parse, the part
validation and Pillow's normalisation pipeline (a genuine PNG is POSTed).

Run from the repository root::

    ./.venv/bin/python -m pytest tests/golden/attachments/ -q
"""

from __future__ import annotations

import hashlib
import io

from fastapi import FastAPI, Request, UploadFile as FastAPIFile
from fastapi.testclient import TestClient
from PIL import Image
from starlette.datastructures import UploadFile as StarletteUploadFile

from db import backends
from routers import attachments_router
from typings.log import inject_log
from util.object_id import get_current_user

PREFIX = "/api/attachments"

MANAGER_OID = "64b7f0c2e1a3b4c5d6e7f901"
FIRST_FILE_OID = "64b7f0c2e1a3b4c5d6e7f902"
FIRST_THUMB_OID = "64b7f0c2e1a3b4c5d6e7f903"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeLog:
    """Stand-in for ``ZVMSLog``: records text, never touches the DB."""

    def __init__(self) -> None:
        self.texts: list[str] = []
        self.inserts = 0

    def with_text(self, text: str):
        self.texts.append(text)
        return self

    async def insert_log(self) -> None:
        self.inserts += 1


class _FakeUsersRepo:
    async def get_by_oid(self, oid: str):
        return {"_id": oid, "id": oid, "per": ["admin"]}


class _FakeFilesRepo:
    """In-memory files repo: enough for the upload path, records every write."""

    def __init__(self) -> None:
        self.created: list[dict] = []
        self.events: list[tuple] = []
        self.entities: list[tuple] = []
        self._rows: dict[str, dict] = {}
        self._seq = 0

    async def find_by_sha256(self, owner_oid, sha256, kind):
        return None

    async def create(self, owner_oid, kind, parent_oid, mime, size_bytes, width,
                     height, sha256, content: bytes, original_name, purpose) -> str:
        self._seq += 1
        oid = FIRST_FILE_OID if kind == "original" else FIRST_THUMB_OID
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
            "entity_type": None,
            "entity_oid": None,
            "status": "stored",
            "ocr_status": "pending",
            "created_at": None,
        }
        self.created.append({**row, "content": content})
        self._rows[oid] = row
        return oid

    async def get_meta(self, oid: str):
        return self._rows.get(oid)

    async def set_entity(self, oid, entity_type, entity_oid) -> None:
        self.entities.append((oid, entity_type, entity_oid))

    async def add_event(self, file_oid, actor_oid, action, from_status, to_status, detail) -> str:
        self.events.append((file_oid, actor_oid, action, from_status, to_status, detail))
        return "64b7f0c2e1a3b4c5d6e7f9ff"


def _manager() -> dict:
    return {"_id": MANAGER_OID, "id": MANAGER_OID, "per": ["admin"]}


def _client(user: dict | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(attachments_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: user or _manager()
    app.dependency_overrides[inject_log] = lambda: _FakeLog()
    return TestClient(app)


def _patch_repos(monkeypatch) -> _FakeFilesRepo:
    repo = _FakeFilesRepo()
    monkeypatch.setattr(backends, "get_files_repo", lambda *a, **k: repo)
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: _FakeUsersRepo())
    return repo


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (12, 8), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# The gate itself
# ---------------------------------------------------------------------------


def test_hand_read_form_part_is_accepted_by_the_gate(monkeypatch) -> None:
    """Pin the trap: ``request.form()`` yields a part the gate must accept.

    A minimal FastAPI sub-app performs exactly the endpoint's hand read. The
    point is the *relationship* between the two classes: the hand-read part is an
    instance of the Starlette class and **not** of ``fastapi.UploadFile``, which
    is why the gate has to test against the Starlette one.
    """
    app = FastAPI()
    observed: dict = {}

    @app.post("/probe")
    async def probe(request: Request):
        form = await request.form(max_files=5, max_fields=10)
        part = form.get("file")
        observed["part"] = part
        observed["accepted"] = isinstance(part, StarletteUploadFile)
        observed["fastapi_class_accepts"] = isinstance(part, FastAPIFile)
        return {"ok": True}

    with TestClient(app) as client:
        response = client.post(
            "/probe", files={"file": ("tiny.png", _png_bytes(), "image/png")}
        )

    assert response.status_code == 200
    assert observed["accepted"] is True, "hand-read file part must satisfy the gate"
    assert observed["fastapi_class_accepts"] is False, (
        "sanity check for the regression: fastapi.UploadFile must not be the class "
        "instances of request.form() are built from"
    )
    assert type(observed["part"]) is StarletteUploadFile
    assert hasattr(observed["part"], "read") and hasattr(observed["part"], "filename")


def test_real_file_upload_passes_the_gate(monkeypatch) -> None:
    """End-to-end: a real PNG is accepted, normalised and stored (201)."""
    repo = _patch_repos(monkeypatch)
    raw = _png_bytes()

    response = _client().post(
        f"{PREFIX}",
        files={"file": ("photo.png", raw, "image/png")},
        data={"purpose": "volunteer_order", "original_name": "photo.png"},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    # The endpoint inlines its status: HTTP 200 + body ``code`` 201 (the module
    # deliberately does not use ``util/response.py``, design C13).
    assert body["code"] == 201
    assert body["data"]["_id"] == FIRST_FILE_OID
    assert body["data"]["thumb_oid"] == FIRST_THUMB_OID
    # Pillow re-encoded the PNG to JPEG for both the original and the thumb.
    assert body["data"]["mime"] == "image/jpeg"
    assert body["data"]["kind"] == "original"
    assert body["data"]["original_name"] == "photo.png"
    assert [row["kind"] for row in repo.created] == ["original", "thumb"]
    original, thumb = repo.created
    assert original["parent_oid"] is None
    assert thumb["parent_oid"] == FIRST_FILE_OID
    assert original["content"].startswith(b"\xff\xd8")  # JPEG magic
    assert original["sha256"] == hashlib.sha256(original["content"]).hexdigest()
    assert original["size_bytes"] == len(original["content"]) > 0
    assert (original["width"], original["height"]) == (12, 8)
    assert repo.events == [
        (FIRST_FILE_OID, MANAGER_OID, "upload", None, "stored", "")
    ]


def test_missing_file_part_is_still_400(monkeypatch) -> None:
    """No file part at all keeps the original 400 + ``Missing file part`` text."""
    repo = _patch_repos(monkeypatch)

    response = _client().post(f"{PREFIX}", data={"purpose": "volunteer_order"})

    assert response.status_code == 400
    assert response.json() == {"detail": "Missing file part"}
    assert repo.created == []


def test_text_field_named_file_is_still_rejected(monkeypatch) -> None:
    """A *text* field called ``file`` is not a file part: the gate must still 400."""
    repo = _patch_repos(monkeypatch)

    response = _client().post(
        f"{PREFIX}", data={"file": "not-a-file", "purpose": "volunteer_order"}
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Missing file part"}
    assert repo.created == []


def test_xuehai_device_is_still_blocked(monkeypatch) -> None:
    """The device gate (``reject_xuehai_device``) is untouched by the fix."""
    repo = _patch_repos(monkeypatch)

    response = _client().post(
        f"{PREFIX}",
        files={"file": ("photo.png", _png_bytes(), "image/png")},
        headers={"Xuehai-Id": "12345"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "学海设备不支持访问媒体库"}
    assert repo.created == []


def test_non_manager_is_still_blocked(monkeypatch) -> None:
    """Permissions are not loosened: a student gets 403, never a stored file."""
    repo = _patch_repos(monkeypatch)

    response = _client({"id": MANAGER_OID, "_id": MANAGER_OID, "per": ["student"]}).post(
        f"{PREFIX}", files={"file": ("photo.png", _png_bytes(), "image/png")}
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Permission denied"}
    assert repo.created == []
