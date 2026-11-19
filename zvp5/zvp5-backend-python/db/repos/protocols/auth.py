"""Login-side identity plumbing: device bindings, one-shot passwords and
cross-device challenges (``USER_DEVICE_BINDINGS`` / ``TEMP_LOGIN_PASSWORDS``
/ ``DEVICE_CHALLENGES``)."""

from __future__ import annotations

from typing import Protocol

from datetime import datetime

from .common import Row


class BindingsRepo(Protocol):
    """Data access for ``USER_DEVICE_BINDINGS`` (学海 ID 设备绑定, observe-only).

    Satisfied by ``db.repos.oracle.bindings.OracleBindingsRepo``. Rows use the
    Mongo document shape ``{"_id": <hex str>, "user_oid": str, "xuehai_id": str,
    "device_id": str | None, "source": str, "status": str, "note": str | None,
    "created_at": datetime, "updated_at": datetime}``. ``source`` is
    ``auto``/``admin`` and ``status`` is ``active``/``revoked``.
    """

    async def get_active_for_user(self, user_oid: str) -> list[Row]:
        """Return only the user's ``active`` bindings."""
        ...

    async def upsert(
        self, user_oid: str, xuehai_id: str, source: str, note: str | None
    ) -> None:
        """Create a binding, or reactivate the existing ``(user, xuehai)`` row.

        When a row already exists for the pair its ``status`` is flipped back to
        ``active`` and ``updated_at`` refreshed — no new row is inserted, so the
        ``(user_oid, xuehai_id)`` uniqueness holds.
        """


class TempPasswordsRepo(Protocol):
    """Data access for ``TEMP_LOGIN_PASSWORDS`` (一次性临时密码, V014).

    Satisfied by ``db.repos.oracle.temp_passwords.OracleTempPasswordsRepo``.
    Rows are ``{"_id": <hex str>, "target_user_oid": str,
    "created_by_oid": str, "code_hash": str, "creator_xuehai_id": str | None,
    "expires_at": datetime, "used_at": datetime | None,
    "revoked_at": datetime | None, "created_at": datetime}``.

    Only the bcrypt hash is stored — the plaintext code appears once, in the
    creation response. There is deliberately no "read the code back" method.
    """

    async def insert(
        self,
        target_user_oid: str,
        created_by_oid: str,
        code_hash: str,
        creator_xuehai_id: str | None,
        expires_at: datetime,
    ) -> str:
        """Store a hashed one-shot code and return its new ``_id``."""
        ...

    async def issue_for_user(
        self,
        target_user_oid: str,
        created_by_oid: str,
        code_hash: str,
        creator_xuehai_id: str | None,
        expires_at: datetime,
    ) -> str:
        """Atomically revoke the target's open codes then insert the new one.

        Implementations must use one transaction. A partial success would
        either leave the user with no code or leave two live codes, both of
        which make the next login failure walk the account toward a ban.
        """
        ...

    async def list_active_for_user(
        self, target_user_oid: str, limit: int = 3
    ) -> list[Row]:
        """Codes that are unused, unrevoked and unexpired, newest first.

        The default stays small on purpose: the login path bcrypt-compares every
        row, and with "one active code + a 30-minute window" there is normally a
        single usable row. A caller that truly wants a listing passes a bigger
        ``limit`` explicitly.
        """
        ...

    async def mark_used(self, oid: str) -> int:
        """Mark a code used, **only** when ``used_at IS NULL``; returns row count.

        ``0`` means the code was already spent (or does not exist) and must be
        treated as a failure — that guard is what makes the code single-use.
        """
        ...

    async def revoke_expired(self, cutoff: datetime) -> int:
        """Revoke codes with ``expires_at < cutoff``; returns the row count.

        Only untouched rows are revoked: an already-used code keeps its
        ``used_at`` as audit evidence.
        """
        ...


class DeviceChallengesRepo(Protocol):
    """Data access for ``DEVICE_CHALLENGES`` (异设备登录挑战, V014).

    Satisfied by
    ``db.repos.oracle.device_challenges.OracleDeviceChallengesRepo``. Rows are
    ``{"_id": <hex str>, "token_hash": str, "user_oid": str, "xuehai_id": str,
    "attempts": int, "expires_at": datetime, "consumed_at": datetime | None,
    "created_at": datetime}``.

    ``token_hash`` is a SHA-256 hex digest of the one-time token (the plaintext
    is only returned to the client), unique per challenge. ``attempts`` lives in
    the DB so the "third failure bans" rule survives multiple workers;
    :meth:`increment_attempts` is the atomic counter and ``consumed_at`` keeps a
    challenge single-use.
    """

    async def insert(
        self,
        token_hash: str,
        user_oid: str,
        xuehai_id: str,
        expires_at: datetime,
    ) -> str:
        """Store a challenge and return its new ``_id``."""
        ...

    async def get(self, token_hash: str) -> Row | None:
        """Fetch the challenge, or ``None`` when unknown.

        No row lock: each call owns its session, so a lock would be released on
        return. Concurrency is carried by the atomic ``attempts = attempts + 1``
        update and the single-use guard on ``consumed_at``.
        """
        ...

    async def increment_attempts(self, oid: str) -> int:
        """Add one failure and return the **new** attempt count."""
        ...

    async def consume(self, oid: str) -> None:
        """Stamp ``consumed_at`` so the challenge cannot be used twice."""
        ...
