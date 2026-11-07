"""Volunteer-time aggregates and the derived indicator caches.

``TimeRepo`` (``time`` / ``time_academic_year`` / ``time_with_origin``)
and ``StatsCacheRepo`` (``indicators`` / ``group_indicators``) stay
together: both are written by the same nightly recompute and share the
per-user aggregation vocabulary."""

from __future__ import annotations

from typing import Any, Protocol

from datetime import datetime

from .common import Row


class TimeRepo(Protocol):
    """Data access for ``zvms_new.time`` / ``time_academic_year`` / ``time_with_origin``.

    Satisfied by both the Mongo adapter and the Oracle implementation.
    """

    async def get_for_user(self, user_oid: str) -> Row | None:
        """Return the user's total time row (``on_campus_raw`` etc.), or ``None``."""
        ...

    async def upsert_for_user(
        self,
        user_oid: str,
        on_campus_raw: float,
        off_campus_raw: float,
        social_practice: float,
    ) -> None:
        """Create or replace the user's aggregate time row."""
        ...

    async def upsert_many_for_users(
        self, entries: list[tuple[str, float, float, float]]
    ) -> None:
        """Recompute total-time rows for many users in one transaction.

        ``entries`` = ``(user_oid, on_campus_raw, off_campus_raw,
        social_practice)``; duplicate user oids take the last occurrence,
        matching the per-user upsert's final state. Either the whole batch
        lands or none of it does.
        """
        ...

    async def list_academic_years(self, user_oid: str) -> list[Row]:
        """Return the user's per-academic-year time rows."""
        ...

    async def get_with_origin(self, user_oid: str) -> Row | None:
        """Return the user's per-origin breakdown row, or ``None``."""
        ...

    async def upsert_with_origin(self, user_oid: str, data: Row) -> None:
        """Create or replace the user's per-origin breakdown.

        ``data`` carries ``by_origin`` and ``percentage_by_origin`` maps.
        """
        ...

    async def list_by_users(
        self,
        user_oids: list[str],
        sort_field: str | None = None,
        descending: bool = False,
        page: int | None = None,
        size: int | None = None,
    ) -> list[Row]:
        """Return total-time rows for ``user_oids``.

        Lifts ``times_router.read_times`` (optional sort field/direction +
        pagination) and ``tasks/compute_time``'s bare ``user IN`` reads.
        """
        ...

    async def list_by_search(
        self,
        query: str,
        logged_in: bool,
        sort_field: str | None = None,
        descending: bool = False,
        page: int | None = None,
        size: int | None = None,
    ) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for one page of total-time rows, joined to users.

        Rows are the total-time documents widened with the owner's ``name`` and
        ``id`` (``business_id``). ``query`` is the same user predicate as
        :meth:`UsersRepo.search` (``logged_in=False`` matches ``id`` exactly,
        ``logged_in=True`` matches ``name``/``id``/``past`` case-insensitively),
        ``sort_field`` maps onto the raw time columns (anything unrecognised
        keeps ``_id`` ascending), and ``total`` counts the **matching users**,
        not the returned rows. Lifts ``routers/times_router.py::read_times``.
        """
        ...

    async def list_by_class(
        self,
        class_oid: str,
        query: str = "",
        sort_field: str | None = None,
        descending: bool = False,
        page: int | None = None,
        size: int | None = None,
    ) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for one page of a class's total-time rows.

        Same row shape and ``total`` semantics as :meth:`list_by_search`, with
        the user filter pinned to ``class_oid`` and an optional case-insensitive
        ``query`` regex over ``name``/``id``. Lifts
        ``routers/groups_v2_router.py::read_users``.
        """
        ...

    async def list_with_origin(self, user_oids: list[str]) -> list[Row]:
        """Return the per-origin rows for ``user_oids``.

        Lifts ``util/calculate.py::time_with_origin``'s ``user IN`` read.
        """
        ...

    async def upsert_with_origin_stat(
        self,
        stat_key: str,
        scope: str,
        scope_id: str,
        require_percentage: int,
        data: Row,
    ) -> None:
        """Create or replace a ``time_with_origin_stat`` row keyed by ``stat_key``."""
        ...

    async def rebuild_user_time(self, now: datetime | None = None) -> int:
        """Single-transaction replacement of every total-time row; returns the count.

        ``tasks/compute_time.rebuild_statistics``: delete-all + one array-bound
        insert built from the per-user aggregation (zero-time users included).
        Never commits — the caller owns the unit of work.
        """
        ...

    async def rebuild_academic_years(self, now: datetime | None = None) -> int:
        """Single-transaction replacement of every per-academic-year row; returns the count.

        Full history: one row per user per relevant 学年 (entry + capped
        graduation year), sourced from the per-``(user, 学年)`` SQL aggregation.
        Never commits.
        """
        ...

    async def aggregate_totals_by_user(self) -> list[Row]:
        """Return one ``{user_oid, grade, class_oid, on_campus_raw, ...}`` row per user.

        ``grade`` is ``substr(business_id,1,4)`` (no numeric filtering, matching
        the old ``compute_indicators``); zero-time users are included.
        """
        ...

    async def aggregate_time_by_origin(self) -> list[Row]:
        """Return ``{user_oid, origin, mode, total}`` sums over double-effective rows."""
        ...

    async def rebuild_with_origin(self, rows: list[Row], now: datetime | None = None) -> int:
        """Single-transaction replacement of every per-origin row; returns the count.

        ``rows`` carry ``user_oid`` and the JSON ``data`` dict; oids and
        ``updated_at`` are minted by the implementation. Never commits.
        """
        ...

    async def rebuild_with_origin_stat(
        self, rows: list[Row], now: datetime | None = None
    ) -> int:
        """Single-transaction replacement of every ``time_with_origin_stat`` row; returns the count.

        ``rows`` carry ``stat_key``/``scope``/``scope_id``/``require_percentage``
        plus the JSON ``data`` dict. The caller supplies the (new) ``scope_id``
        for group scope. Never commits.
        """
        ...

    async def upsert_academic_year(
        self,
        user_oid: str,
        entry: str,
        academic_year: int,
        start_of_year: Any,
        end_of_year: Any,
        on_campus: float,
        off_campus: float,
        social_practice: float,
    ) -> None:
        """Create or replace a user's per-academic-year row (whole-row replace).

        Retained for the read/compat surface; the nightly recompute now writes
        ``time_academic_year`` in bulk via :meth:`rebuild_academic_years`.
        """
        ...


class StatsCacheRepo(Protocol):
    """Data access for the derived cache collections.

    Covers ``indicators`` (global, single row) and ``group_indicators`` (one row
    per group). Satisfied by both the Mongo adapter and the Oracle implementation.
    """

    async def get_indicators(self) -> Row | None:
        """Return the cached global indicators row, or ``None``."""
        ...

    async def upsert_indicators(self, percentiles: Row, indicators: Row) -> None:
        """Replace the single global indicators row."""
        ...

    async def list_group_indicators(self) -> list[Row]:
        """Return every cached per-group indicators row."""
        ...

    async def upsert_group_indicators(
        self, group_oid: str, percentiles: Row, indicators: Row
    ) -> None:
        """Create or replace the indicators row for one group."""
        ...

    async def rebuild_indicators(
        self, percentiles: Row, indicators: Row, now: datetime | None = None
    ) -> None:
        """Single-transaction replacement of the single global indicators row.

        ``tasks/compute_time.rebuild_statistics``: delete-then-insert (the table
        is single-row by contract). Never commits — the caller owns the unit of
        work.
        """
        ...

    async def rebuild_group_indicators(
        self, rows: list[Row], now: datetime | None = None
    ) -> int:
        """Single-transaction replacement of every global group-indicators row; returns the count.

        ``rows`` carry ``group_oid`` plus the ``percentiles``/``indicators`` JSON
        dicts; oids and ``updated_at`` are minted by the implementation. Never
        commits.
        """
        ...
