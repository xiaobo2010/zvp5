"""Repository protocols for the ADB data layer.

These are pure :class:`typing.Protocol` interfaces: no I/O, no implementations.
Every domain has exactly one protocol, satisfied by the Oracle implementation:

* the Oracle implementations (``db.repos.oracle.*``) — SQLAlchemy Core over ADB.

Row shape contract
------------------
Mongo rows are plain ``dict``s keyed by the original document field names
(``_id``, ``id``, ``class_id``, ``on-campus`` …). Oracle implementations must
return **the same dict shape** (``_id`` normalised to a lowercase hex string)
so callers in ``routers/`` and ``util/`` are drop-in compatible. Returning
ORM/Row objects is a contract violation — the adapters translate.

DB-specific values (``ObjectId``, ``datetime``, ``None`` for NULL) are likewise
the adapters' responsibility to normalise; this file only pins the *interfaces*.
"""

from __future__ import annotations

from typing import Any, Protocol

from datetime import datetime

# A row as seen by callers: the Mongo document shape, backend-independent.
Row = dict[str, Any]


class UsersRepo(Protocol):
    """Data access for ``zvms_new.users``.

    Satisfied by both the Mongo adapter and the Oracle implementation.

    Full-document rows carry the ban metadata additively — ``ban_reason`` /
    ``ban_until`` / ``ban_source`` / ``ban_rule`` appear **only when non-NULL**,
    mirroring the Mongo "absent field" semantics (``"ban_until" in row`` keeps
    its meaning). ``banned`` is the login gate: ``True`` or absent.
    """

    async def get_by_oid(self, oid: str) -> Row | None:
        """Return the user with the given ``_id`` (hex string), or ``None``."""
        ...

    async def find_by_business_id(self, business_id: str) -> Row | None:
        """Return the user with the given 学号 (``id`` field), or ``None``."""
        ...

    async def list_page(
        self, query: str, role: str, page: int, size: int
    ) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for one page, filtered by keyword/role.

        ``page`` is 1-based. ``total`` is the full match count, not the page size.
        """
        ...

    async def list_banned_page(
        self, query: str, page: int, size: int
    ) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for one page of currently banned users.

        Source of truth is the ``banned`` gate on the user row (not the
        ``BAN_RECORDS`` ledger), so a user who was unbanned is absent even
        though the ledger still holds their history.
        """
        ...

    async def create(
        self,
        business_id: str,
        name: str,
        roles: list[str],
        password_hash: str | None,
        class_oid: str | None,
    ) -> str:
        """Insert a user and return its new ``_id`` as a hex string.

        ``password_hash`` is ``None`` when the caller wants the initial-state
        semantics (login compares against the plain 学号); the column is nullable
        on purpose.
        """
        ...

    async def set_password(self, oid: str, password_hash: str | None) -> None:
        """Set (or clear, with ``None``) a user's ``password`` hash."""
        ...

    async def set_password_guarded(
        self, oid: str, password_hash: str, actor_oid: str
    ) -> int:
        """Atomically set a password, guarded by the target's role.

        A single ``UPDATE ... WHERE oid = :oid AND (actor = oid OR the target
        has no admin role)``: the write lands only when ``actor_oid``
        is the target themself or the target is not an admin-level account. The
        role test and the write share one statement so a concurrent promotion of
        the target to ``admin`` cannot slip through between check and write.
        Returns the affected row count (``0`` = rejected by the guard).
        """
        ...

    async def set_roles(self, oid: str, roles: list[str]) -> None:
        """Replace a user's ``roles`` whitelist."""
        ...

    async def set_banned(self, oid: str, banned: int) -> None:
        """Set a user's ``banned`` flag (``1`` to ban, ``0`` to unban).

        Compatibility shim for the pre-V013 call sites: ``1`` forwards to
        :meth:`set_ban` (``source="admin"``, no reason, permanent), ``0`` to
        :meth:`clear_ban`.
        """
        ...

    async def set_ban(
        self,
        oid: str,
        *,
        reason: str,
        until: datetime | None,
        source: str,
        rule_code: str | None,
    ) -> None:
        """Ban a user and record **why** (``banned = 1``).

        ``until is None`` means permanent (``BAN_UNTIL`` stays NULL); otherwise
        the caller-side expiry task unbans at that naive ``datetime``.
        ``source`` is ``admin`` or ``rule``; ``rule_code`` names the automatic
        rule and is ``None`` for a manual ban. The session id is rotated, so any
        live token of the banned account dies with the same write.
        """
        ...

    async def clear_ban(self, oid: str) -> None:
        """Unban a user and drop the ban metadata (``banned = 0``, four columns NULL).

        The metadata is cleared rather than kept: an unbanned row must not look
        like it still carries a reason/expiry, and the audit trail lives in
        ``ban_records``. Rotates the session id like every other identity write.
        """
        ...

    async def set_class(self, user_oid: str, class_oid: str | None) -> None:
        """Point a user at ``class_oid`` (a group id), or detach with ``None``."""
        ...

    async def detach_class(self, class_oid: str) -> int:
        """Detach every user currently pointing at ``class_oid``.

        Sets ``class_id = None`` for all such users and returns the number of
        affected rows. Used when a class is deleted.
        """
        ...

    async def get_names(self, oids: list[str]) -> dict[str, str]:
        """Map each existing ``oid`` to its display ``name``; unknown ids omitted."""
        ...

    async def get_many_by_oids(self, oids: list[str]) -> dict[str, Row]:
        """Map each existing ``oid`` to its **full** user document.

        Batch form of :meth:`get_by_oid` for list endpoints (member/statistics
        fan-out); unknown ids are omitted. Dependencies are de-duplicated and
        looked up in <=1000-element ``IN`` chunks.
        """
        ...

    async def list_all(self) -> list[Row]:
        """Return every user document (``users.find({})``).

        Lifts the bare ``find({})`` reads in ``routers/exports_router.py`` and
        ``tasks/compute_time.py``. Rows are the full user shape (optional fields
        omitted when NULL, mirroring ``get_by_oid``).
        """
        ...

    async def list_by_id_prefix(self, prefix: str) -> list[Row]:
        """Return users whose 学号 (``id``) starts with ``prefix``.

        Lifts ``util/calculate.py::time_with_origin``'s grade branch
        (``{"id": {"$regex": "^<prefix>"}}`` used verbatim — no escaping).
        """
        ...

    async def list_by_class(self, class_oid: str) -> list[Row]:
        """Return users whose ``class_id`` is ``class_oid``.

        Lifts ``time_with_origin``'s group branch and ``tasks/compute_time``'s
        per-class reads (``{"class_id": <oid>}``).
        """
        ...

    async def search(self, query: str, logged_in: bool) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for ``routers/times_router.read_times``.

        ``logged_in`` selects between the two filter shapes verbatim: the
        three-branch ``$or`` (``name``/``id``/``past`` ``$regex`` — *not*
        escaped) and the unlogged exact ``{"id": query}``. Rows carry the
        ``_id``/``name``/``id``/``class_id`` projection.
        """
        ...

    async def list_by_name_or_id(self, pattern: str) -> list[Row]:
        """Return full user docs matching name/id ``$regex`` (empty = all users).

        Lifts ``routers/activities_v2_router.get_activity_members_v2``'s
        searched-users lookup; ``pattern`` is already ``re.escape``d by the
        caller and applied verbatim.
        """
        ...

    async def group_oids_by_id_prefix(self) -> list[Row]:
        """Group users by the first four characters of ``id``.

        Lifts ``tasks/compute_time``'s aggregation; each row is
        ``{"_id": <prefix str>, "originalIds": [<oid str>, ...]}``.
        """
        ...

    async def add_past(self, oid: str, value: str) -> None:
        """Append ``value`` to the user's ``past`` identity list (``$push``).

        Lifts ``routers/users_router.py.add_past``. The list is created when the
        field is currently absent/NULL.
        """
        ...

    async def remove_past(self, oid: str, value: str) -> None:
        """Remove **every** ``past`` entry equal to ``value`` (``$pull``).

        Lifts ``routers/users_router.py.delete_past`` (which resolves the index
        to a value and then ``$pull``s it, removing all matching entries while
        preserving the order of the survivors).
        """
        ...

    async def set_primary_xuehai(self, oid: str, xuehai_id: str) -> None:
        """Bind the account's 主学海设备 (``primary_xuehai_id``).

        The session id is **not** rotated: a primary device is not a credential,
        so switching devices must not log the account out everywhere. The
        "only from the current primary device" rule is the endpoint's job.
        """
        ...

    async def clear_primary_xuehai(self, oid: str) -> None:
        """Unbind the 主学海设备 (column back to NULL = no device check).

        Like :meth:`set_primary_xuehai`, no session rotation.
        """
        ...

    async def list_banned_by_xuehai(self, xuehai_id: str) -> list[Row]:
        """Users whose ``primary_xuehai_id == xuehai_id`` and ``banned = 1``.

        Raw filter only: an expired ban keeps ``banned = 1`` by design, so the
        caller decides whether it is *currently* in force with
        ``util.ban.ban_is_active``. Rows are full user documents.
        """
        ...

    async def primary_owned_by_other(self, xuehai_id: str, user_oid: str) -> bool:
        """Whether ``xuehai_id`` is **someone else's** primary device.

        The device id is self-reported by the client, so it can be forged. The
        ban path uses this to refuse writing a ``BANNED_DEVICES`` row for a
        forged victim id: only the account gets banned, never a device that
        belongs to another user.
        """
        ...


class GroupsRepo(Protocol):
    """Data access for ``zvms_new.groups`` (class groups only).

    Satisfied by both the Mongo adapter and the Oracle implementation. The row
    shapes returned by :meth:`get` / :meth:`list_classes` /
    :meth:`list_non_permission` are ``{_id, name, description}`` and those from
    :meth:`list_permission_groups` / :meth:`get_permission_group` are
    ``{_id, name, type: "permission", permissions, description}``. ``description``
    is always present and normalised to ``""`` when absent/NULL.
    """

    async def get(self, oid: str) -> Row | None:
        """Return the group with the given ``_id``, or ``None``."""
        ...

    async def list_classes(
        self, query: str, page: int, size: int
    ) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for one page of classes matching ``query``."""
        ...

    async def create(self, name: str) -> str:
        """Insert a group and return its new ``_id`` as a hex string."""
        ...

    async def rename(self, oid: str, name: str) -> None:
        """Rename a group."""
        ...

    async def delete(self, oid: str) -> None:
        """Delete a group. Callers are responsible for detaching its members."""
        ...

    async def members_page(
        self, group_oid: str, query: str, page: int, size: int
    ) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for one page of the group's members."""
        ...

    async def activities_page(
        self,
        group_oid: str,
        search: str,
        sort: str,
        asc: bool,
        page: int,
        size: int,
    ) -> tuple[list[Row], int]:
        """Return ``(activities, total)`` for one page of a class's activities.

        Rows are the canonical activity documents (same shape as
        :meth:`ActivitiesRepo.get`): every activity that at least one member of
        ``group_oid``'s class belongs to. ``search`` is a case-insensitive
        regex over ``name``/``description`` (empty = no filter), ``sort``/
        ``asc`` order the rows (unknown/empty ``sort`` falls back to
        ``_id`` descending). ``total`` counts the matching activities, i.e.
        every distinct activity of the class, not ``memberships``.

        Lifts ``routers/groups_v2_router.py::get_group_activities_v2``.
        """
        ...

    async def list_all(self) -> list[Row]:
        """Return every group (``groups.find()``) as ``{_id, name}`` rows.

        Lifts ``util/get_class.py::get_classname``'s unfiltered ``find()``. The
        Oracle ``GROUPS`` table only models class columns, so the permission
        columns (``type``/``permissions``) are not carried here.
        """
        ...

    async def list_non_permission(self) -> list[Row]:
        """Return every non-permission group as ``{_id, name}`` rows.

        Lifts ``tasks/compute_time.py``'s ``find({"type": {"$ne":
        "permission"}})`` (``generate_reports``). Class rows carry no ``type`` in
        Mongo and a NULL ``TYPE`` in Oracle, so the Oracle filter is
        ``type IS NULL``.
        """
        ...

    async def list_permission_groups(
        self, query: str = "", permissions: list[str] | None = None
    ) -> list[Row]:
        """Return the permission groups (``type == "permission"``).

        Rows use the shape ``{"_id": <hex str>, "name": str,
        "type": "permission", "permissions": list[str]}``. ``query`` is applied
        as a case-insensitive *contains* on ``name`` (``re.escape``d, empty = no
        filter); ``permissions`` restricts to groups whose ``permissions`` array
        intersects the given values (empty/``None`` = no filter).

        Lifts ``routers/users_router.py`` (``permissions $in`` lookup) and
        ``routers/groups_v2_router.py`` (name ``$regex`` lookup).
        """
        ...

    async def get_permission_group(self, oid: str) -> Row | None:
        """Return the permission group with ``_id == oid``, or ``None``.

        Same ``{_id, name, type: "permission", permissions, description}`` shape
        as :meth:`list_permission_groups`. Lifts
        ``routers/groups_v3_router.py``'s
        ``find_one({"_id": ..., "type": "permission"})``.
        """
        ...

    async def set_description(self, oid: str, value: str) -> None:
        """Set a group's ``description`` (``$set`` / ``UPDATE ... SET description``).

        Lifts ``routers/groups_router.py``'s
        ``PUT /groups/{gid}/description``.
        """
        ...


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
    ) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for one page, filtered by keyword/type/status.

        ``query`` is a case-insensitive *contains* on ``name`` only. ``atypes``
        is an OR-list of activity types (``on-campus`` / ``off-campus`` /
        ``social-practice`` / ``hybrid``); empty/``None`` means "no type filter".
        ``status`` is an optional equality filter.
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
        oids`` plus a ``contains`` on ``name`` (the raw ``query`` used verbatim
        as a Mongo ``$regex``; an empty ``query`` adds no filter), ordered by
        ``_id`` desc. ``page == -1`` means "no pagination".
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

        Flavour of the v2/mcp/data-fix list endpoints that return the Mongo
        document shape verbatim (not the canonical projection): ``_id`` is a
        lowercase hex string, extra document fields survive, and ``sort`` may
        name any field (``None`` = natural order, ``page`` ``None`` = no
        pagination). ``search`` is applied as a case-insensitive ``contains``
        on ``name`` **verbatim** (callers ``re.escape`` it themselves; empty =
        no name filter); ``atypes`` is an OR-list; ``status`` equality.
        """
        ...

    async def types_summary(self) -> list[Row]:
        """Return per-type activity counts with a status breakdown.

        Each row is ``{"type": <activity type>, "count": <total int>,
        "status_breakdown": {<status>: <count int>}}``. When several docs share
        a type, their counts are summed and their statuses merged; one row per
        distinct type. Lifts ``routers/mcp_router.mcp_get_activity_types_summary``
        (the upstream ``$group``+``$project`` aggregation, which the Mongo driver
        rejects).
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

    async def remove(self, oid: str) -> None:
        """Delete a membership row by its ``_id``."""
        ...

    async def set_status(self, oid: str, status: str) -> None:
        """Set a membership's approval status."""
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

        Lifts ``activity_statistics_router`` / ``mcp_router``'s
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
        size: int,
    ) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for one page of a user's activity memberships.

        Each row is ``{"member": <membership doc>, "activity": <activity doc or
        None>}``: the membership is joined to its activity, and a membership
        whose activity is gone is reported with ``activity=None`` while still
        counting towards ``total``. ``search`` is an optional keyword predicate
        on the activity's ``name``/``description``; ``sort``/``asc`` order the
        rows. Lifts ``routers/users_v2_router.py::get_user_activities_v2``.
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

        Flavour of ``activities_v2_router.get_activity_members_v2`` that
        returns the Mongo document shape verbatim (``_id`` hex string):
        ``activity`` equality plus an optional ``member IN`` set (``None`` =
        no member filter, ``[]`` = match nothing), arbitrary ``sort`` field and
        optional pagination (``page`` ``None`` = no pagination).
        """
        ...


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


class AppLogsRepo(Protocol):
    """Data access for ``zvms_new.app_logs`` (runtime application logs).

    Satisfied by both the Mongo adapter and the Oracle implementation. Rows use
    the Mongo document shape ``{"_id": <hex str>, "url": str, "user": str,
    "data": str, "timestamp": float, "type": str, "level": str,
    "xuehai_id": str}`` (``timestamp`` is the raw float epoch seconds). The
    Oracle table renames these to ``APP_USER``/``LTYPE``/``LOG_LEVEL``/
    ``TS_AT``/``TS_RAW``/``XUEHAI_ID``; the adapters translate.

    Listing rows additionally carry ``user_name`` / ``user_id`` (display name
    and ``business_id``) resolved from ``users``: ``user`` itself is an oid and
    is unreadable for a human. Both are ``None`` when the log has no user
    (anonymous request) or the account has been deleted, so they never filter
    rows out.
    """

    async def insert(
        self,
        url: str,
        user: str,
        data: str,
        timestamp: float,
        ltype: str,
        level: str,
        xuehai_id: str,
    ) -> None:
        """Insert one application log entry (``typings/log.py``'s write path)."""
        ...

    async def list_page(
        self,
        query: str = "",
        performer: str = "",
        types: list[str] | None = None,
        levels: list[str] | None = None,
        time_start: int | None = None,
        time_end: int | None = None,
        xuehai_id: str = "",
        page: int = -1,
        size: int = 10,
    ) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for the admin log browser.

        Lifts ``routers/logs_router.py.read_logs``: ``query`` is a case-sensitive
        *contains* on ``url``/``data`` (``re.escape``d; empty = no filter),
        ``performer`` an ``user`` equality, ``types``/``levels`` OR-lists on
        ``type``/``level``, ``time_start``/``time_end`` are **exclusive** bounds
        on the ``timestamp`` float, and ``xuehai_id`` an equality on the captured
        device 学海 ID (empty = no filter). Ordered by ``timestamp`` desc;
        ``page == -1`` means "no offset".
        """
        ...

    async def list_for_user(
        self, user_oid: str, query: str = "", page: int = -1, size: int = 10
    ) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for one user's log history.

        Lifts ``routers/users_router.py.read_logs`` (``/{user_oid}/logs``):
        ``user`` equality plus an optional *raw* (not escaped) ``url``/``data``
        contains, ordered by ``timestamp`` desc; ``page == -1`` = no offset.
        """
        ...

    async def count_expired(self, cutoff: datetime) -> int:
        """Count the rows :meth:`purge_batch` would eventually delete.

        Read-only; shared by the retention preview and the purge loop. ``cutoff``
        is a naive ``datetime`` compared against ``TS_AT``.
        """
        ...

    async def purge_batch(self, cutoff: datetime, batch_size: int) -> int:
        """Delete at most ``batch_size`` expired rows; return the deleted count.

        The retention predicate (design §2): ``TS_AT`` precedes ``cutoff`` and the
        row is neither a protected type (``db.repos.oracle.app_logs``
        ``PROTECTED_LTYPES``) nor warning-level. ``0`` means nothing is left to
        purge; the caller loops (``tasks.retention``). One commit per call.
        """
        ...

    async def count_distinct_xuehai(self, user_oid: str, since_ts: float) -> int:
        """Count distinct 学海 device ids one user logged in from since ``since_ts``.

        The multi-device-login rule's evidence query: ``app_user = user_oid``
        (the log writer stores the user's oid), ``ltype = 'user_login'`` only
        (other event types may echo a device id without being a login), a
        non-blank ``xuehai_id``, and ``ts_raw >= since_ts`` (epoch seconds on
        the raw float column, so the window never depends on the derived
        ``ts_at``). ``since_ts`` is inclusive; the caller derives it from the
        rule's ``window_hours``.
        """
        ...


class TasksRepo(Protocol):
    """Data access for ``zvms_new.tasks`` (runtime export queue).

    Satisfied by both the Mongo adapter and the Oracle implementation. Rows use
    the Mongo document shape ``{"_id": <hex str>, "id": <uuid.UUID>,
    "status": str, "format": str, "variant": str, "export_start": datetime,
    "export_end": datetime, "task_start": datetime, "task_end": datetime,
    "percentage": float, "errmsg": str, "result": list, "allow_cache": bool,
    "include_description": bool}``. ``id`` is accepted/returned as a
    :class:`uuid.UUID`; the Oracle ``TASK_UUID`` column stores its canonical
    string form.
    """

    async def create(self, document: Row) -> str:
        """Insert a task from its Mongo-shaped ``document``; return the new ``_id``.

        Lifts the ``insert_one(document)`` writes in ``routers/exports_router.py``.
        """
        ...

    async def get_by_uuid(self, task_uuid: Any) -> Row | None:
        """Return the task with ``id == task_uuid`` (UUID), or ``None``.

        Lifts the ``find_one({"id": UUID(...)})`` lookups in
        ``routers/exports_router.py``.
        """
        ...

    async def get(self, oid: str) -> Row | None:
        """Return the task with the given ``_id`` (hex string), or ``None``."""
        ...

    async def list_all(self) -> list[Row]:
        """Return every task (``tasks.find({})``) as full rows.

        Lifts ``routers/exports_router.py.get_export_list``.
        """
        ...

    async def update_fields(self, task_uuid: Any, fields: Row) -> None:
        """Apply a partial ``$set`` of Mongo-keyed ``fields`` to the task.

        Lifts the ``$set`` half of the ``update_one`` calls in
        ``routers/exports_router.py.process_task``.
        """
        ...

    async def push_result(
        self, task_uuid: Any, rows: list[Row], fields: Row | None = None
    ) -> None:
        """Append ``rows`` to the task's ``result`` list (``$push $each``).

        Optionally also applies a ``$set`` of Mongo-keyed ``fields`` in the same
        update, mirroring the combined ``{"$set": ..., "$push": {"result":
        {"$each": ...}}}`` writes in ``routers/exports_router.py.process_task``.
        """
        ...

    async def delete(self, task_uuid: Any) -> None:
        """Delete the task with ``id == task_uuid`` (``delete_one``)."""
        ...

    async def fail_pending(self, reason: str) -> int:
        """Mark every non-``completed`` task ``failed``; return the row count.

        Lifts ``main.py::mark_all_tasks_failed``. ``task_end`` is stamped as well
        so the failed rows carry an age the retention purge can read.
        """
        ...

    async def count_expired_finished(self, cutoff: datetime) -> int:
        """Count the finished rows :meth:`purge_finished_batch` would delete.

        Read-only; shared by the retention preview and the purge loop.
        """
        ...

    async def purge_finished_batch(self, cutoff: datetime, batch_size: int) -> int:
        """Delete at most ``batch_size`` finished rows older than ``cutoff``.

        The retention predicate (design §2): ``status IN ('completed','failed')``
        and ``COALESCE(task_end, task_start)`` precedes ``cutoff``. ``0`` means
        nothing is left to purge; the caller loops (``tasks.retention``). One
        commit per call.
        """
        ...


class ImportsRepo(Protocol):
    """Data access for ``zvms_new.imports`` (import job records).

    The Oracle ``CONTENT`` column is a ``BLOB``; ``content`` is accepted as
    plain ``bytes``.
    """

    async def create(
        self,
        name: str,
        filename: str,
        description: str,
        user: str,
        date: str,
        content: Any,
    ) -> str:
        """Insert an import record and return its new ``_id`` as a hex string.

        Lifts ``routers/imports_router.py.upload_activity_excel``'s
        ``insert_one`` (``date`` is an ISO string, ``content`` the uploaded
        bytes)."""
        ...


class DailyExportsRepo(Protocol):
    """Data access for ``zvms_new.daily_exports`` (statement export cache).

    Satisfied by both the Mongo adapter and the Oracle implementation. Rows use
    the Mongo document shape ``{"_id": <hex str>, "created_at": datetime,
    "status": str, "data": list}`` (``data`` is a JSON array of spreadsheet
    rows).
    """

    async def delete_all(self) -> int:
        """Delete every row (``delete_many({})``); return the deleted row count.

        Lifts ``tasks/compute_time.py::generate_reports``.
        """
        ...

    async def insert(self, document: Row) -> str:
        """Insert one document and return its new ``_id`` as a hex string.

        ``document`` carries ``created_at`` / ``status`` / ``data``.
        """
        ...

    async def update_fields(self, oid: str, fields: Row) -> None:
        """Apply a partial ``$set`` of ``fields`` to the row."""
        ...

    async def push_data(self, oid: str, item: Row) -> None:
        """Append ``item`` to the row's ``data`` array (``$push``).

        Lifts ``tasks/compute_time.py``'s per-statement ``update_one``.
        """
        ...

    async def find_one(self, oid: str) -> Row | None:
        """Return the row with ``_id == oid`` (``find_one({"_id": ...})``), or ``None``."""
        ...


class FilesRepo(Protocol):
    """Data access for the Files/attachments tables (``FILES`` / ``FILE_OCR`` /
    ``FILE_EVENTS``; ``docs/FILES_API_DESIGN.md`` §3).

    Satisfied by ``db.repos.oracle.files.OracleFilesRepo``. Row shapes are the
    Mongo document dicts keyed by the physical columns:

    * a file row is ``{"_id", "owner_oid", "kind", "parent_oid", "mime",
      "size_bytes", "width", "height", "sha256", "original_name", "purpose",
      "entity_type", "entity_oid", "status", "ocr_status", "ocr_retry",
      "created_at", "updated_at", "deleted_at"}`` — **``content`` is never
      projected**, so metadata reads never materialise the BLOB;
    * an OCR row is ``{"_id", "file_oid", "engine", "engine_version", "text",
      "confidence", "raw", "created_at"}``;
    * an event row is ``{"_id", "file_oid", "actor_oid", "action",
      "from_status", "to_status", "detail", "created_at"}``.

    ``FILES.STATUS`` is the single source of truth for the serve gate
    (``stored``/``processing``/``ready``/``verified``/``rejected``/
    ``quarantined``/``deleted``); ``FILE_EVENTS`` is an append-only audit copy.
    Only bytes are read through :meth:`get_content`; every other read projects
    the metadata columns.
    """

    async def create(self, owner_oid, kind, parent_oid, mime, size_bytes, width,
                     height, sha256, content: bytes, original_name, purpose) -> str:
        """Insert a file row and return its new ``_id`` as a hex string.

        ``content`` is the normalised bytes (bound to the ``CONTENT`` BLOB);
        ``status`` starts ``stored`` and ``ocr_status`` ``pending``.
        """
        ...

    async def get_meta(self, oid: str) -> Row | None:
        """Return the file's metadata row (never ``content``), or ``None``."""
        ...

    async def get_content(self, oid: str) -> bytes | None:
        """Return the file's stored bytes, or ``None`` when absent/NULL."""
        ...

    async def find_by_sha256(self, owner_oid: str, sha256: str, kind: str) -> Row | None:
        """Return the newest metadata row for ``(owner, sha256, kind)``, or ``None``.

        Same-owner dedup lookup only — cross-owner hashes are never shared.
        """
        ...

    async def list_page(self, owner_oid: str | None, purpose: str | None, status: str | None,
                        entity_type: str | None, entity_oid: str | None,
                        page: int, size: int) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for one page, filtered by the given columns.

        ``None`` filters are "no filter"; ``page == -1`` means "no pagination".
        Rows are metadata only (never ``content``).
        """
        ...

    async def thumb_for(self, parent_oid: str) -> Row | None:
        """Return the thumbnail row whose ``parent_oid`` is the given original, or ``None``."""
        ...

    async def set_status(self, oid: str, status: str) -> None:
        """Set a file's ``status`` (and bump ``updated_at``)."""
        ...

    async def set_entity(self, oid: str, entity_type: str, entity_oid: str) -> None:
        """Bind a file to ``(entity_type, entity_oid)``."""
        ...

    async def set_ocr(self, oid: str, ocr_status: str, *, bump_retry: bool = False) -> None:
        """Set a file's ``ocr_status``; optionally increment ``ocr_retry``."""
        ...

    async def soft_delete(self, oid: str) -> None:
        """Soft-delete a file (``status='deleted'`` + ``deleted_at``)."""
        ...

    async def purge_deleted(self, before: Any) -> int:
        """Tombstone ``deleted`` files older than ``before`` and return the row count.

        Sets ``content = NULL`` while keeping the metadata row.
        """
        ...

    async def add_ocr(self, file_oid, engine, engine_version, text, confidence, raw) -> str:
        """Insert an OCR result and return its new ``_id`` as a hex string."""
        ...

    async def get_ocr(self, file_oid: str) -> Row | None:
        """Return the newest OCR result for ``file_oid``, or ``None``."""
        ...

    async def add_event(self, file_oid, actor_oid, action, from_status, to_status, detail) -> str:
        """Append an audit event and return its new ``_id`` as a hex string."""
        ...

    async def list_events(self, file_oid: str) -> list[Row]:
        """Return ``file_oid``'s audit events in chronological order."""
        ...


class BindingsRepo(Protocol):
    """Data access for ``USER_DEVICE_BINDINGS`` (学海 ID 设备绑定, observe-only).

    Satisfied by ``db.repos.oracle.bindings.OracleBindingsRepo``. Rows use the
    Mongo document shape ``{"_id": <hex str>, "user_oid": str, "xuehai_id": str,
    "device_id": str | None, "source": str, "status": str, "note": str | None,
    "created_at": datetime, "updated_at": datetime}``. ``source`` is
    ``auto``/``admin`` and ``status`` is ``active``/``revoked``.
    """

    async def list_for_user(self, user_oid: str) -> list[Row]:
        """Return every binding of a user, **including** ``revoked`` rows."""
        ...

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
        ...

    async def revoke(self, user_oid: str, xuehai_id: str) -> None:
        """Mark the ``(user, xuehai)`` binding ``revoked`` (no-op if absent)."""
        ...


class BanRulesRepo(Protocol):
    """Data access for ``BAN_RULES`` (规则封禁配置, V013).

    Satisfied by ``db.repos.oracle.ban_rules.OracleBanRulesRepo``. Rows use the
    Mongo document shape ``{"_id": <24-char lowercase hex str>, "code": str,
    "name": str, "enabled": bool, "config": str, "penalties": str,
    "builtin": bool, "created_at": datetime, "updated_at": datetime}``.

    ``config`` / ``penalties`` stay **JSON text**: the DB only validates them
    (``IS JSON``), and parsing is the service layer's decision (it owns the
    schema of each rule). ``code`` is the stable identity the login path quotes;
    :meth:`update_by_code` never touches it.
    """

    async def list_all(self) -> list[Row]:
        """Return every rule, ordered by ``code`` asc (built-ins first is a UI choice)."""
        ...

    async def get_by_code(self, code: str) -> Row | None:
        """Return the rule with ``code == code``, or ``None``."""
        ...

    async def update_by_code(
        self,
        code: str,
        *,
        enabled: bool | None = None,
        config: str | None = None,
        penalties: str | None = None,
    ) -> int:
        """Patch a rule's tunables and bump ``updated_at``; return the row count.

        ``None`` means "leave unchanged" (so ``enabled=False`` is expressible).
        ``0`` = no rule with that ``code`` — the caller turns it into a 404.
        ``code`` / ``name`` / ``builtin`` are immutable here.
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

    async def list_page(self, page: int, size: int) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for one page, newest ban first.

        ``page`` is 1-based; ``total`` counts devices, not page size.
        """
        ...


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
