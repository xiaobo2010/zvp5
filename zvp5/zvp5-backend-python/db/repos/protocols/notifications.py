"""Changelog entries and in-site notifications.

Kept together because a changelog entry is what generates a notification:
``NotificationsRepo.get_by_source_ref`` keys on ``CHANGELOG.OID``."""

from __future__ import annotations

from typing import Protocol

from .common import Row


class ChangelogRepo(Protocol):
    """Data access for ``zvms_new.changelog`` (release version + notes).

    Satisfied by both the Mongo adapter and the Oracle implementation. Every
    row uses the shape ``{"_id": <24-char lowercase hex str>, "version": str,
    "date": str, "items": list[str], "updated_at": datetime}``.
    """

    async def list_all(self) -> list[Row]:
        """Return every release entry, sorted by ``date`` desc then ``version`` desc."""
        ...

    async def get_by_oid(self, oid: str) -> Row | None:
        """Return the entry with the given ``_id`` (hex string), or ``None``."""
        ...

    async def create(self, version: str, date: str, items: list[str]) -> str:
        """Insert an entry and return its new ``_id`` as a hex string."""
        ...

    async def update(
        self, oid: str, version: str, date: str, items: list[str]
    ) -> int:
        """Replace an entry's fields and return the number of matched rows."""
        ...

    async def delete(self, oid: str) -> int:
        """Delete an entry and return the number of deleted rows."""
        ...


class NotificationsRepo(Protocol):
    """Data access for ``NOTIFICATIONS`` + ``NOTIFICATION_STATE`` (站内通知, V015).

    Satisfied by ``db.repos.oracle.notifications.OracleNotificationsRepo``.
    Rows use the shape::

        {"_id": <24-char lowercase hex str>,
         "title": str,
         "content": str,
         "active": bool,
         "author_oid": str,
         "author_name": str,
         "version": int,
         "source_ref": str | None,
         "created_at": datetime,
         "updated_at": datetime}

    ``author_oid`` / ``author_name`` / ``created_at`` are immutable after
    create: they record the real authenticated sender and send time, and are
    deliberately not touched by :meth:`update`. ``version`` is the per-notice
    revision (1 on create, +1 on every successful edit/deactivate).

    ``NOTIFICATION_STATE`` is the singleton ``id = 1`` row holding the global
    notification version. Every successful create / edit / delete bumps it in
    the **same transaction** as the row change; failed writes (unknown oid) bump
    nothing. List methods read the state **before** the rows so a notice created
    between the two statements can never be hidden behind an older global
    version in the response.

    Version history (``NOTIFICATION_VERSIONS``, V017): every successful write
    also inserts a full-content snapshot in the same transaction, with the
    ``op`` that produced it (``create`` / ``update`` / ``delete`` /
    ``rollback``). Snapshot rows use the shape::

        {"_id": <24-char lowercase hex str>,
         "notification_oid": str,
         "version": int,
         "title": str,
         "content": str,
         "active": bool,
         "op": str,
         "source_version": int | None,
         "content_hash": str,
         "author_oid": str | None,
         "author_name": str,
         "created_at": datetime}

    ``content_hash`` is the sha256 of the content fields, so two revisions are
    equivalent exactly when their hashes match. Snapshots carry no FK: the
    history must outlive the notice it describes.
    """

    async def get_state(self) -> Row:
        """Return ``{"version": int, "updated_at": datetime | None}``.

        A missing state row (only possible on an unseeded database) reads as
        version ``0`` with ``updated_at = None``; it is not repaired here.
        """
        ...

    async def list_active(self) -> tuple[list[Row], int]:
        """Return ``(active_rows, global_version)``, newest first.

        Reads ``NOTIFICATION_STATE`` first, then the active rows. This ordering
        is the response-consistency contract: a concurrent create may add a row
        the client sees even though the returned version is older, but never the
        reverse (a new global version with the new notice missing).
        """
        ...

    async def list_all(self) -> tuple[list[Row], int]:
        """Return ``(all_rows, global_version)`` for the admin management UI.

        Includes inactive/deactivated rows; same read ordering as
        :meth:`list_active`.
        """
        ...

    async def get(self, oid: str) -> Row | None:
        """Return the notification with ``_id == oid``, or ``None``."""
        ...

    async def get_by_source_ref(self, source_ref: str) -> Row | None:
        """Return the notice generated from ``source_ref``, or ``None``.

        ``source_ref`` is the oid of the object the notice was generated from
        (``CHANGELOG.OID`` today, V038). Because ``uq_notifications_source_ref``
        is unique, at most one row can match; hand-written notices have
        ``source_ref IS NULL`` and never match a non-null value.
        """
        ...

    async def create(
        self,
        *,
        title: str,
        content: str,
        author_oid: str,
        author_name: str,
        source_ref: str | None = None,
    ) -> tuple[Row, int]:
        """Insert an active notification and return ``(row, global_version)``.

        ``version`` starts at 1 and ``created_at == updated_at``. The state bump
        shares the insert's transaction, so a committed notification always has
        a committed version increment.

        ``source_ref`` marks the notice as generated from another object (the
        CHANGELOG oid, V038). It is ``None`` for admin-written notices; the
        unique constraint allows any number of NULLs, so hand-written notices
        are unaffected. One source may own at most one notice.
        """
        ...

    async def update(
        self,
        oid: str,
        *,
        title: str | None = None,
        content: str | None = None,
        active: bool | None = None,
        author_oid: str | None = None,
        author_name: str | None = None,
    ) -> tuple[Row | None, int]:
        """Patch supplied fields, bump the notice revision, return ``(row, version)``.

        ``None`` means "leave unchanged" (the columns are NOT NULL, so it cannot
        be a stored value). A successful update sets ``version = version + 1``
        and refreshes ``updated_at``; ``author_oid`` / ``author_name`` /
        ``created_at`` are never modified. An unknown ``oid`` returns
        ``(None, current_global_version)`` and bumps nothing.

        The row is selected with ``FOR UPDATE`` before the patch so concurrent
        edits produce consecutive revisions instead of a lost update. The
        snapshot's ``author_oid`` / ``author_name`` describe the *actor*
        (falling back to the notice's publisher when omitted).
        """
        ...

    async def delete(
        self,
        oid: str,
        *,
        author_oid: str | None = None,
        author_name: str | None = None,
    ) -> tuple[Row | None, int]:
        """Hard-delete a notification; return ``(deleted_row, global_version)``.

        The row is selected with ``FOR UPDATE`` before deletion so the returned
        snapshot is exactly what was removed (the router logs its title). A
        delete snapshot is written first in the same transaction, taking the
        next free revision; the notice row itself is not bumped. An unknown
        ``oid`` returns ``(None, current_global_version)`` and bumps nothing.
        The audit log row survives the deletion.
        """
        ...

    async def list_versions(self, oid: str) -> tuple[list[Row], int]:
        """Return ``(version_rows, global_version)``, newest revision first.

        Works for deleted notices too (snapshots are independent of the notice
        row). An oid with no history returns an empty list, not ``None``: the
        absence of a notice is not an error here, the router decides.
        """
        ...

    async def rollback(
        self,
        oid: str,
        *,
        target_version: int,
        author_oid: str,
        author_name: str,
    ) -> tuple[Row | None, int, Row | None]:
        """Restore ``target_version``'s content; return ``(row, version, snapshot)``.

        A missing target snapshot returns ``(None, current_global_version,
        None)``; the router maps that to 404. When the notice still exists the
        restore takes the same ``FOR UPDATE`` + ``version + 1`` path as
        :meth:`update`; when it was deleted the content is re-inserted under the
        **original oid** with ``version`` one past the highest historical
        revision, so client-side per-id markers stay meaningful. Either way a
        ``rollback`` snapshot with ``source_version = target_version`` is
        appended and the global version bumps in the same transaction.
        """
        ...
