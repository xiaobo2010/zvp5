"""Offline execution-layer tests for the attachment serve gate (design §4/§6.2).

The SQL-builders are pinned by ``test_files_repo_tombstone_filters.py``; these
tests drive the real HTTP layer instead, because two contracts that the
frontend depends on live *only* in ``routers/attachments_router.py`` and have no
other guard:

* **meta stays 200 for a soft-deleted row.** ``GET /api/attachments/{oid}/meta``
  (``attachments_router.py:477-488``) never consults the serve gate, so a
  tombstone still answers ``200`` with ``data.status == "deleted"``. The
  attachment component relies on exactly that to silently drop ghost entries; a
  future "hardening" of meta to 404/410 would break it with no test failing.
  Confirmed against production: the tombstoned row's meta returned 200.
* **the serve gate is enforced at request time.** Bytes 410 for a tombstoned
  original (``:419``) and, for thumbnails, ``_require_servable(thumb)``
  (``:450``) is the *only* defence — ``thumb_for`` does not filter status at the
  SQL level — so a servable parent with a tombstoned latest thumb must still be
  a 410, never a 404/200 (``:444`` covers the mirrored case).

Everything is offline: the router is mounted on a throwaway ``FastAPI()`` (the
production prefix comes from ``main.py``'s ``include_router``), the repositories
and the request log are fakes, and both byte-access paths are exercised — Bearer
(token dependency overridden) and signed URL (``verify_signed`` monkeypatched).

Run from the repository root::

    ./.venv/bin/python -m pytest tests/golden/attachments/ -q
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import attachments_router
from util.object_id import get_current_user, optional_current_user

PREFIX = "/api/attachments"

# Real oids from the 2026-10-17 incident: the soft-deleted original that was
# re-uploaded, its owner, and the entity it was attached to.
OWNER_OID = "68c3da8c46711c1f1f0475cd"
DELETED_OID = "6ad389487a1f1855f9526b1a"
ENTITY_OID = "6aa48ec6ea44db1fda77c4d4"
SERVABLE_OID = "6aa48ec6ea44db1fda77c4d5"
DELETED_THUMB_OID = "6ad389487a1f1855f9526b1b"
SERVABLE_THUMB_OID = "6ad389487a1f1855f9526b1c"

JPEG = b"\xff\xd8\xff\xe0fake-jpeg-bytes"


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeLog:
    def __init__(self, *_args, **_kwargs) -> None:
        self.texts: list[str] = []
        self.inserts = 0

    def with_text(self, text: str):
        self.texts.append(text)
        return self

    async def insert_log(self) -> None:
        self.inserts += 1


class _FakeUsersRepo:
    def __init__(self) -> None:
        self.lookups: list[str] = []

    async def get_by_oid(self, oid: str):
        self.lookups.append(oid)
        return {"_id": oid, "id": oid, "per": ["admin"], "class_id": 1}


def _row(oid: str, status: str, *, kind: str = "original", parent_oid=None) -> dict:
    return {
        "_id": oid,
        "owner_oid": OWNER_OID,
        "kind": kind,
        "parent_oid": parent_oid,
        "mime": "image/jpeg",
        "size_bytes": len(JPEG),
        "width": 12,
        "height": 8,
        "sha256": "b" * 64,
        "original_name": "photo.png",
        "purpose": "volunteer_order",
        "entity_type": "activity",
        "entity_oid": ENTITY_OID,
        "status": status,
        "ocr_status": "pending",
        "ocr_retry": 0,
        "created_at": None,
        "updated_at": None,
        "deleted_at": None,
    }


class _FakeFilesRepo:
    """Metadata-only fake: every row has fetchable bytes unless stated.

    ``get_content`` always returns a payload, so a 410 in these tests can only
    come from the serve gate and never from "the BLOB happens to be empty".
    """

    def __init__(self, rows: dict[str, dict], thumbs: dict[str, dict] | None = None) -> None:
        self.rows = rows
        self.thumbs = thumbs or {}
        self.content_reads: list[str] = []
        self.thumb_lookups: list[str] = []

    async def get_meta(self, oid: str):
        return self.rows.get(oid)

    async def thumb_for(self, parent_oid: str):
        self.thumb_lookups.append(parent_oid)
        return self.thumbs.get(parent_oid)

    async def get_content(self, oid: str):
        self.content_reads.append(oid)
        return JPEG


def _manager() -> dict:
    return {"_id": OWNER_OID, "id": OWNER_OID, "per": ["admin"], "class_id": 1}


def _client(user: dict | None | object = ...) -> TestClient:
    """Router on a throwaway app. ``user`` overrides the candidate for both gates."""
    app = FastAPI()
    app.include_router(attachments_router.router, prefix=PREFIX)
    resolved = _manager() if user is ... else user
    app.dependency_overrides[get_current_user] = lambda: resolved
    app.dependency_overrides[optional_current_user] = lambda: resolved
    app.dependency_overrides[attachments_router.inject_log] = lambda: _FakeLog()
    return TestClient(app)


def _patch_repos(monkeypatch, repo: _FakeFilesRepo, users: _FakeUsersRepo | None = None):
    monkeypatch.setattr(attachments_router.backends, "get_files_repo", lambda *a, **k: repo)
    monkeypatch.setattr(
        attachments_router.backends, "get_users_repo", lambda *a, **k: users or _FakeUsersRepo()
    )
    # ``_log_byte_access`` builds a real ZVMSLog and inserts it — keep it offline.
    monkeypatch.setattr(attachments_router, "ZVMSLog", _FakeLog)
    return repo


def _deleted_repo() -> _FakeFilesRepo:
    return _FakeFilesRepo({DELETED_OID: _row(DELETED_OID, "deleted")})


# ---------------------------------------------------------------------------
# P1-a: the meta contract the frontend's ghost-entry pruning relies on
# ---------------------------------------------------------------------------


def test_meta_of_soft_deleted_row_is_200_with_deleted_status(monkeypatch) -> None:
    """Owner reading a tombstone's meta gets 200 + ``status='deleted'`` (never 410)."""
    repo = _patch_repos(monkeypatch, _deleted_repo())

    response = _client().get(f"{PREFIX}/{DELETED_OID}/meta")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["code"] == 200
    assert body["data"]["_id"] == DELETED_OID
    assert body["data"]["status"] == "deleted"
    # The owner still gets sha256 (design §8.6 / P1-5).
    assert body["data"]["sha256"] == "b" * 64
    assert repo.content_reads == [], "meta must never touch the BLOB"


def test_meta_of_soft_deleted_row_is_200_for_a_manager_viewer(monkeypatch) -> None:
    """The dialog's viewer (admin, not the owner) sees the same 200 + status."""
    repo = _patch_repos(monkeypatch, _deleted_repo())
    manager = {"_id": "64b7f0c2e1a3b4c5d6e7f9aa", "id": "64b7f0c2e1a3b4c5d6e7f9aa", "per": ["admin"]}

    response = _client(manager).get(f"{PREFIX}/{DELETED_OID}/meta")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["data"]["status"] == "deleted"
    assert "sha256" not in body["data"], "sha256 is owner-only"
    assert repo.content_reads == []


# ---------------------------------------------------------------------------
# P1-b: the serve gate at request time
# ---------------------------------------------------------------------------


def test_servable_original_bytes_are_200(monkeypatch) -> None:
    """Sanity: the harness really serves bytes when the gate passes."""
    repo = _patch_repos(
        monkeypatch,
        _FakeFilesRepo({SERVABLE_OID: _row(SERVABLE_OID, "stored")}),
    )

    response = _client().get(f"{PREFIX}/{SERVABLE_OID}")

    assert response.status_code == 200, response.text
    assert response.content == JPEG
    assert repo.content_reads == [SERVABLE_OID]


def test_deleted_original_bytes_are_410_for_the_owner(monkeypatch) -> None:
    """Bearer path: a tombstoned original is 410 and its BLOB is never read."""
    repo = _patch_repos(monkeypatch, _deleted_repo())

    response = _client().get(f"{PREFIX}/{DELETED_OID}")

    assert response.status_code == 410, response.text
    assert response.json() == {"detail": "Attachment unavailable"}
    assert repo.content_reads == []


def test_deleted_original_bytes_are_410_with_a_valid_signature(monkeypatch) -> None:
    """Signed path: deleting revokes the link immediately (design §4 #2 / §6.2)."""
    repo = _patch_repos(monkeypatch, _deleted_repo())
    seen: list[tuple] = []

    def _always_valid(oid, exp, purpose, sig):
        seen.append((oid, exp, purpose, sig))
        return True

    monkeypatch.setattr(attachments_router, "verify_signed", _always_valid)

    response = _client(None).get(
        f"{PREFIX}/{DELETED_OID}",
        params={"exp": "9999999999", "purpose": "view", "sig": "deadbeef"},
    )

    assert response.status_code == 410, response.text
    assert seen == [(DELETED_OID, "9999999999", "view", "deadbeef")]
    assert repo.content_reads == []


def test_deleted_thumb_is_410_even_though_its_bytes_exist(monkeypatch) -> None:
    """Servable parent + tombstoned latest thumb → 410 from ``:450``.

    ``thumb_for`` has no status filter, so ``_require_servable(thumb)`` is the
    only thing standing between a deleted thumbnail and its bytes. The fake
    always returns bytes for any oid, so a 200 here would leak them.
    """
    parent = _row(SERVABLE_OID, "stored")
    thumb = _row(DELETED_THUMB_OID, "deleted", kind="thumb", parent_oid=SERVABLE_OID)
    repo = _patch_repos(
        monkeypatch, _FakeFilesRepo({SERVABLE_OID: parent}, {SERVABLE_OID: thumb})
    )

    response = _client().get(f"{PREFIX}/{SERVABLE_OID}/thumb")

    assert response.status_code == 410, response.text
    assert response.json() == {"detail": "Attachment unavailable"}
    assert repo.thumb_lookups == [SERVABLE_OID]
    assert repo.content_reads == [], "the deleted thumb's bytes must not be read"


def test_thumb_of_deleted_parent_is_410(monkeypatch) -> None:
    """Mirror case (``:444``): a tombstoned parent is 410 before any thumb lookup."""
    parent = _row(DELETED_OID, "deleted")
    thumb = _row(SERVABLE_THUMB_OID, "stored", kind="thumb", parent_oid=DELETED_OID)
    repo = _patch_repos(
        monkeypatch, _FakeFilesRepo({DELETED_OID: parent}, {DELETED_OID: thumb})
    )

    response = _client().get(f"{PREFIX}/{DELETED_OID}/thumb")

    assert response.status_code == 410, response.text
    assert repo.thumb_lookups == []
    assert repo.content_reads == []


def test_servable_thumb_is_200(monkeypatch) -> None:
    """Sanity for the two tests above: servable parent + servable thumb serves."""
    parent = _row(SERVABLE_OID, "stored")
    thumb = _row(SERVABLE_THUMB_OID, "stored", kind="thumb", parent_oid=SERVABLE_OID)
    repo = _patch_repos(
        monkeypatch, _FakeFilesRepo({SERVABLE_OID: parent}, {SERVABLE_OID: thumb})
    )

    response = _client().get(f"{PREFIX}/{SERVABLE_OID}/thumb")

    assert response.status_code == 200, response.text
    assert response.content == JPEG
    assert repo.content_reads == [SERVABLE_THUMB_OID]
