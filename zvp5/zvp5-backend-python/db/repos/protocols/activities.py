"""Activities and their memberships (``activities`` / ``activity_members``)."""

from __future__ import annotations

from typing import Any, Protocol

from .common import Row


class ActivitiesRepo(Protocol):
    """Data access for ``zvms_new.activities``.

    Satisfied by both the Mongo adapter and the Oracle implementation.
    """

    async def get(self, oid: str) -> Row | None:
        """Return the activity with the given ``_id``, or ``None``."""
        ...

    async def list_page(
        self,
        query: str,
        atypes: list[str] | None,
        status: str | None,
        page: int,
        size: int,
        sort: str | None = None,
        asc: bool = False,
    ) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for one page, filtered by keyword/type/status.

        ``query`` is a case-insensitive *contains* on ``name`` only. ``atypes``
        is an OR-list of activity types (``on-campus`` / ``off-campus`` /
        ``social-practice`` / ``hybrid``); empty/``None`` means "no type filter".
        ``status`` is an optional equality filter. ``sort`` is an optional
        whitelisted column name (``None`` keeps ``oid DESC``).
        """
        ...

    async def create(
        self,
        name: str,
        description: str,
        atype: str,
        creator_oid: str,
        appointee_oid: str,
        origin: str,
        activity_date: Any,
    ) -> str:
        """Insert an activity and return its new ``_id`` as a hex string.

        Timestamps (``date``/``createdAt``/``updatedAt``) are set by the adapter;
        ``status`` starts as ``pending`` and ``approver`` as the empty sentinel.
        """
        ...

    async def update(self, oid: str, fields: Row) -> None:
        """Apply a partial ``$set`` of ``fields`` to the activity."""
        ...

    async def cas_status(self, oid: str, expected: str, fields: Row) -> bool:
        """Apply ``fields`` only while the activity's status is still ``expected``.

        Compare-and-set counterpart of :meth:`update` for review transitions:
        returns ``False`` when the row no longer carries ``expected`` (another
        transaction won the race), so the caller can answer 409 instead of
        silently overwriting a concurrent review.
        """
        ...

    async def delete(self, oid: str) -> None:
        """Delete an activity. Callers are responsible for its members."""
        ...

    async def list_effective(
        self, date_start: Any = None, date_end: Any = None
    ) -> list[Row]:
        """Return every ``effective`` activity, optionally within a date range.

        Lifts ``util/calculate.py::calculate_user_time``: ``{"status":
        "effective"}`` and, when both ``date_start``/``date_end`` are given,
        ``date`` bounded inclusively by them.
        """
        ...

    async def list_by_oids(
        self, oids: list[str], query: str, page: int = -1, size: int = 10
    ) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for activities restricted to ``oids``.

        Lifts ``util/calculate.py::get_activities_related_to_user``: ``_id IN
        oids`` plus a case-insensitive **literal** ``contains`` on ``name``
        (``%``/``_``/``\\`` escaped; an empty ``query`` adds no filter), ordered
        by ``_id`` desc. ``page == -1`` means "no pagination".
        """
        ...

    async def list_raw(
        self,
        search: str = "",
        atypes: list[str] | None = None,
        status: str | None = None,
        sort: str | None = None,
        asc: bool = False,
        page: int | None = None,
        size: int = 10,
    ) -> tuple[list[Row], int]:
        """Return ``(docs, total)`` of **raw** activity documents.

        Flavour of the v2/agent/data-fix list endpoints that return the Mongo
        document shape verbatim (not the canonical projection): ``_id`` is a
        lowercase hex string, extra document fields survive, and ``sort`` may
        name any field (``None`` = natural order, ``page`` ``None`` = no
        pagination). ``search`` is applied as a case-insensitive **literal**
        ``contains`` on ``name`` (LIKE wildcards escaped by the repo; empty = no
        name filter); ``atypes`` is an OR-list; ``status`` equality.
        """
        ...

    async def types_summary(self) -> list[Row]:
        """Return per-type activity counts with a status breakdown.

        Each row is ``{"type": <activity type>, "count": <total int>,
        "status_breakdown": {<status>: <count int>}}``. When several docs share
        a type, their counts are summed and their statuses merged; one row per
        distinct type. Lifts the ``activity_types_summary`` read now served by the
        agent tool (the upstream ``$group``+``$project`` aggregation, which the
        Mongo driver rejects).
        """
        ...


class MembersRepo(Protocol):
    """Data access for ``zvms_new.activity_members``.

    Satisfied by both the Mongo adapter and the Oracle implementation.
    """

    async def list_for_activity(
        self, activity_oid: str, query: str, page: int, size: int
    ) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for one page of an activity's members."""
        ...

    async def list_for_user(
        self,
        user_oid: str,
        status: str | None,
        page: int,
        size: int,
    ) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for one page of a user's memberships."""
        ...

    async def add(
        self,
        activity_oid: str,
        member_oid: str,
        mode: str,
        duration: float,
        status: str,
    ) -> str:
        """Insert a membership row and return its new ``_id`` as a hex string.

        Uniqueness of ``(member, activity, mode)`` is an application-level
        invariant; callers upsert by first looking up the triple.
        """
        ...

    async def add_many(
        self,
        activity_oid: str,
        entries: list[tuple[str, str, float]],
        status: str,
    ) -> None:
        """Insert membership rows in bulk (``entries`` = ``(member_oid, mode, duration)``).

        The Excel-import path: one transaction over array-bound executemany,
        so a bad row rolls back the whole import. Same uniqueness invariant
        as :meth:`add`.
        """
        ...

    async def remove(self, oid: str) -> None:
        """Delete a membership row by its ``_id``."""
        ...

    async def update_durations_many(
        self, entries: list[tuple[str, float]], *, delta: bool = True
    ) -> None:
        """Bulk-update existing memberships' durations (``entries`` = ``(oid, hours)``).

        One array-bound ``executemany`` batch per call instead of one update per
        row. ``delta=True`` adds in SQL (``duration_hours + :delta``) so
        concurrent increments cannot lose an update; ``delta=False`` sets the
        given value (the overwrite semantics of the add-member endpoint).
        """
        ...

    async def sum_modes_for_member(self, member_oid: str) -> Row | None:
        """Return one member's per-mode totals, or ``None`` when there are none.

        Same double-effective predicate as the user-time rebuild (only
        ``activities.status = 'effective'`` memberships whose own status is
        ``effective``), so ``util/calculate.calculate_user_time`` can read the
        three buckets in a single row instead of summing rows in Python.
        """
        ...

    async def set_status(self, oid: str, status: str) -> None:
        """Set a membership's approval status."""
        ...

    async def cas_status(self, oid: str, expected: str, status: str) -> bool:
        """Set the status only while it still equals ``expected``; return success.

        Compare-and-set counterpart of :meth:`set_status` for review
        transitions: ``False`` means a concurrent review already moved the row,
        so the caller must not recalculate times or write an audit entry.
        """
        ...

    async def set_mode(self, oid: str, mode: str) -> None:
        """Set a membership's mode."""
        ...

    async def get_by_oid(self, oid: str) -> Row | None:
        """Return the membership with the given ``_id``, or ``None``."""
        ...

    async def get_by_triple(
        self, activity_oid: str, member_oid: str, mode: str
    ) -> Row | None:
        """Return the membership for ``(member, activity, mode)``, or ``None``.

        Lifts the application-level dedup lookup shared by the v2/v3 add-member
        endpoints.
        """
        ...

    async def list_by_activity(
        self, activity_oid: str, status: str | None = None
    ) -> list[Row]:
        """Return every membership of an activity (no join, no pagination).

        Lifts ``activity_statistics_router``'s
        ``find({"activity": ...})`` reads; callers use ``duration`` for stats.
        ``status`` optionally keeps only memberships in that approval state
        (``None`` = every state, the original behaviour).
        """
        ...

    async def list_for_member(
        self,
        member_oid: str,
        status: str | None = None,
        activity_oids: list[str] | None = None,
    ) -> list[Row]:
        """Return a member's memberships, optionally filtered by status/activity.

        Lifts ``util/calculate.py`` / ``util/statement.py``'s bare
        ``find({"member": ...})`` (plus optional ``status`` / ``activity IN``).
        """
        ...

    async def list_by_members(self, member_oids: list[str]) -> list[Row]:
        """Return memberships whose ``member`` is in ``member_oids`` (no pagination).

        Lifts ``util/calculate.py::get_activities_related_to_user``.
        """
        ...

    async def list_by_activity_and_members(
        self, activity_oid: str, member_oids: list[str]
    ) -> list[Row]:
        """Return an activity's memberships for the given ``member_oids``.

        Batch form of :meth:`get_by_triple` (same ``(member, mode)`` key space) so
        a bulk import can decide "update vs insert" in memory instead of issuing
        one lookup per member.
        """
        ...

    async def list_for_user_activities(
        self,
        user_oid: str,
        search: str,
        sort: str,
        asc: bool,
        page: int,
        size: int | None,
    ) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for one page of a user's activity memberships.

        Each row is ``{"member": <membership doc>, "activity": <activity doc or
        None>}``: the membership is joined to its activity, and a membership
        whose activity is gone is reported with ``activity=None`` while still
        counting towards ``total``. ``search`` is an optional keyword predicate
        on the activity's ``name``/``description``; ``sort``/``asc`` order the
        rows. Lifts ``routers/users_router.py::get_user_activities_v2``.

        ``size=None`` means "the whole list in one statement" (no OFFSET/FETCH);
        the legacy ``page=-1`` sentinel of ``routers/users_router.py`` maps onto
        it. ``page`` is only used when ``size`` is given.
        """
        ...

    async def count_for_activity(
        self,
        activity_oid: str,
        status: str | None = None,
        member_oids: list[str] | None = None,
    ) -> int:
        """Count an activity's memberships, optionally by status/member set."""
        ...

    async def count_distinct_members(self, activity_oid: str) -> int:
        """Count the **people** in an activity (one user, several modes = one).

        The immediate time-refresh guard needs a population size, not a row
        count: a single user may hold one row per counting mode.
        """
        ...

    async def remove_for_activity(self, activity_oid: str) -> None:
        """Delete every membership of an activity (callers delete the activity)."""
        ...

    async def update_fields(self, oid: str, fields: Row) -> None:
        """Apply a partial ``$set`` of ``fields`` to a membership row."""
        ...

    async def set_duration_for_member(
        self, activity_oid: str, member_oid: str, duration: float
    ) -> None:
        """Set the duration on the ``(activity, member)`` membership.

        Lifts ``activities_router.update_activity_member_duration``.
        """
        ...

    async def list_raw(
        self,
        activity_oid: str,
        member_oids: list[str] | None = None,
        sort: str | None = None,
        asc: bool = True,
        page: int | None = None,
        size: int = 10,
    ) -> tuple[list[Row], int]:
        """Return ``(docs, total)`` of **raw** membership documents.

        Flavour of ``services.agent.tool_impl.list_activity_members`` that
        returns the Mongo document shape verbatim (``_id`` hex string):
        ``activity`` equality plus an optional ``member IN`` set (``None`` =
        no member filter, ``[]`` = match nothing), arbitrary ``sort`` field and
        optional pagination (``page`` ``None`` = no pagination).
        """
        ...
