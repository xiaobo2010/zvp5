"""Rules, violations and the ban ledgers (``BAN_RULES`` / ``BAN_VIOLATIONS``
/ ``BAN_RECORDS`` / ``BANNED_DEVICES`` / ``DEVICE_BAN_COUNTERS``).

All five describe one escalation pipeline (signal → verdict → ledger →
device counter), so they are deliberately kept in one module."""

from __future__ import annotations

from typing import Protocol

from datetime import datetime

from .common import Row


class BanRulesRepo(Protocol):
    """Data access for ``BAN_RULES`` (规则封禁配置, V013 / V016).

    Satisfied by ``db.repos.oracle.ban_rules.OracleBanRulesRepo``. Rows use the
    Mongo document shape ``{"_id": <24-char lowercase hex str>, "code": str,
    "name": str, "trigger": str | None, "enabled": bool, "config": str,
    "penalties": str, "builtin": bool, "created_at": datetime,
    "updated_at": datetime}``.

    ``config`` / ``penalties`` stay **JSON text**: the DB only validates them
    (``IS JSON``), and parsing is the service layer's decision (it owns the
    schema of each rule). ``code`` is the administrator-facing identity;
    ``trigger`` is the **evaluation** identity — the login / report paths look a
    rule up by trigger, so at most one rule may own one trigger.
    """

    async def list_all(self) -> list[Row]:
        """Return every rule, ordered by ``code`` asc (built-ins first is a UI choice)."""
        ...

    async def get_by_code(self, code: str) -> Row | None:
        """Return the rule with ``code == code``, or ``None``."""
        ...

    async def get_by_trigger(self, trigger: str) -> Row | None:
        """Return the rule owning ``trigger``, or ``None`` (unique index ⇒ ≤ 1)."""
        ...

    async def insert(
        self,
        code: str,
        name: str,
        trigger: str,
        *,
        enabled: bool,
        config: str,
        penalties: str,
        builtin: bool = False,
    ) -> str:
        """Create one rule and return its new ``_id`` (hex string).

        ``code`` / ``trigger`` uniqueness is enforced by the DB; a violation is
        the caller's 409, not something this layer retries.
        """
        ...

    async def delete_by_code(self, code: str) -> int:
        """Delete the rule with ``code``; return the row count (``0`` ⇒ absent)."""
        ...

    async def update_by_code(
        self,
        code: str,
        *,
        name: str | None = None,
        enabled: bool | None = None,
        config: str | None = None,
        penalties: str | None = None,
    ) -> int:
        """Patch a rule's tunables and bump ``updated_at``; return the row count.

        ``None`` means "leave unchanged" (so ``enabled=False`` is expressible).
        ``0`` = no rule with that ``code`` — the caller turns it into a 404.
        ``code`` / ``trigger`` / ``builtin`` are immutable here.
        """
        ...


class ViolationsRepo(Protocol):
    """Data access for ``BAN_VIOLATIONS`` (客户端上报的违规信号, V016).

    Rows use the Mongo document shape ``{"_id": <hex str>, "user_oid": str,
    "trigger": str, "signal": str, "xuehai_id": str | None,
    "detail": str | None, "created_at": datetime}``; :meth:`list_page` rows add
    ``user_name`` / ``user_id`` from the ``USERS`` join (``None`` if the account
    is gone).

    The table is append-only: counting inside a window (``count_since``) is what
    turns repeated signals into a ban, and re-counting the same row must never
    be possible.
    """

    async def insert(
        self,
        user_oid: str,
        trigger: str,
        signal: str,
        *,
        xuehai_id: str | None = None,
        detail: str | None = None,
        batch_id: str | None = None,
    ) -> str:
        """Append one violation and return its new ``_id``.

        ``batch_id`` is generated once per report and reused for every signal in
        that report, so one page load counts as one incident.
        """
        ...

    async def exists_recent(
        self, user_oid: str, signal: str, since: datetime
    ) -> bool:
        """Whether the user already reported ``signal`` at/after ``since``.

        This is the de-duplication gate: the same signal must not inflate the
        counter (and hand out a ban) just because a page was reloaded.
        """
        ...

    async def count_since(
        self, user_oid: str, trigger: str, since: datetime
    ) -> int:
        """Violation count for ``(user_oid, trigger)`` at/after ``since``."""
        ...

    async def count_batches_since(
        self, user_oid: str, trigger: str, since: datetime
    ) -> int:
        """Distinct report count for ``(user_oid, trigger)`` at/after ``since``.

        Counts ``DISTINCT COALESCE(batch_id, oid)``: rows written before batches
        existed each count as their own report.
        """
        ...

    async def list_page(
        self, query: str, page: int, size: int
    ) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for one page, newest first.

        ``page`` is 1-based; ``query`` matches 姓名 / 学号 (empty ⇒ no filter).
        """
        ...


class BanRecordsRepo(Protocol):
    """Data access for ``BAN_RECORDS`` (封禁流水, V013).

    Satisfied by ``db.repos.oracle.ban_records.OracleBanRecordsRepo``. Rows use
    the Mongo document shape ``{"_id": <hex str>, "user_oid": str,
    "user_name": str, "user_business_id": str, "source": "admin" | "rule",
    "rule_code": str | None, "reason": str | None, "permanent": bool,
    "started_at": datetime, "expires_at": datetime | None,
    "operator_oid": str | None, "created_at": datetime}`` (``user_name`` /
    ``user_business_id`` come from the ``USERS`` join and are ``None`` if the
    account is gone).

    This is the escalation ledger: the number of rows for one
    ``(user_oid, rule_code)`` pair selects the next penalty step.
    """

    async def insert(
        self,
        user_oid: str,
        *,
        source: str,
        rule_code: str | None = None,
        reason: str | None = None,
        permanent: bool = False,
        started_at: datetime | None = None,
        expires_at: datetime | None = None,
        operator_oid: str | None = None,
    ) -> str:
        """Append one ban record and return its new ``_id``.

        ``started_at`` defaults to ``datetime.now()`` (naive, schema convention);
        ``expires_at is None`` with ``permanent=True`` for an indefinite ban.
        Append-only — records are never updated or deleted by this layer.
        """
        ...

    async def count_for_user_rule(self, user_oid: str, rule_code: str) -> int:
        """Count a user's previous records for one rule (the penalty index).

        ``0`` = first offence (use ``penalties[0]``); N = the N-th repeat.
        """
        ...

    async def delete_for_user_rule(
        self, user_oid: str, rule_code: str | None = None
    ) -> int:
        """Delete a user's ban records (optionally one rule); return the count.

        Backs the admin "reset penalty ladder" action: the ladder index is the
        row count, so emptying it sends the next offence back to
        ``penalties[0]``. Current ban state (``USERS.BANNED``) is untouched —
        unbanning is ``UsersRepo.clear_ban``.
        """
        ...

    async def list_page(
        self, page: int, size: int, query: str
    ) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for one page of the ban ledger, newest first.

        ``query`` is a case-insensitive *contains* over the joined user's
        ``name`` / 学号 (empty = no filter); ``page`` is 1-based, ``page == -1``
        means "no offset". ``total`` counts matching records, not users.
        """
        ...


class DeviceBansRepo(Protocol):
    """Data access for ``BANNED_DEVICES`` (设备级封禁, V014).

    Satisfied by ``db.repos.oracle.device_bans.OracleDeviceBansRepo``. Rows are
    ``{"xuehai_id": str, "reason": str | None, "until": datetime | None,
    "source": "rule" | "admin", "rule_code": str | None,
    "operator_oid": str | None, "created_at": datetime}`` — the 学海 device id is
    the primary key, so there is no ``_id``: it *is* the identity.

    The primary key is also the upsert key (one current verdict per device).
    ``until is None`` means permanent. Account bans are **not** mirrored here;
    the service layer derives them from ``users.primary_xuehai_id``.
    """

    async def get(self, xuehai_id: str) -> Row | None:
        """Return the device's current ban row, or ``None`` when not banned."""
        ...

    async def upsert(
        self,
        xuehai_id: str,
        *,
        reason: str | None,
        until: datetime | None,
        source: str,
        rule_code: str | None,
        operator_oid: str | None,
    ) -> None:
        """Ban a device, overwriting any existing verdict for that device.

        ``until is None`` = permanent; ``source`` is ``rule`` or ``admin``.
        ``created_at`` is refreshed so it always describes the current verdict.
        """
        ...

    async def delete(self, xuehai_id: str) -> int:
        """Lift a device ban; returns the affected row count (``0`` = not banned)."""
        ...

    async def list_page(
        self,
        page: int,
        size: int,
        query: str = "",
        include_expired: bool = False,
        now: datetime | None = None,
    ) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for one page, newest ban first.

        ``page`` is 1-based; ``total`` counts devices, not page size. A
        non-empty ``query`` filters by device id containment.
        ``include_expired=False``（默认）只列仍然生效的封禁。
        """
        ...


class DeviceBanCountersRepo(Protocol):
    """Data access for ``DEVICE_BAN_COUNTERS`` (设备侧处罚梯度, V039).

    Satisfied by ``db.repos.oracle.device_ban_counters.OracleDeviceBanCountersRepo``.
    Rows are ``{"xuehai_id": str, "ban_count": int, "last_banned_at": datetime | None}``.
    """

    async def get(self, xuehai_id: str) -> Row | None:
        """Return the device's counter row, or ``None`` when never banned."""
        ...

    async def increment(self, xuehai_id: str, at: datetime | None = None) -> int:
        """Increment and return the new ``ban_count`` (first offence starts at 1)."""
        ...

    async def get_counts(self, xuehai_ids: list[str]) -> dict[str, int]:
        """Batch ``{xuehai_id: ban_count}``; ids without a row are omitted."""
        ...

    async def delete(self, xuehai_id: str) -> int:
        """Reset the gradient; returns the affected row count (``0`` = no row)."""
        ...
