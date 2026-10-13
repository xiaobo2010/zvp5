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

# A row as seen by callers: the Mongo document shape, backend-independent.
Row = dict[str, Any]


class UsersRepo(Protocol):
    """Data access for ``zvms_new.users``.

    Satisfied by both the Mongo adapter and the Oracle implementation.
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

    async def set_roles(self, oid: str, roles: list[str]) -> None:
        """Replace a user's ``roles`` whitelist."""
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
        "permission"}})`` (``generate_reports`` / ``compute_batch_origins`` /
        ``compute_group_indicators``). Class rows carry no ``type`` in Mongo and
        a NULL ``TYPE`` in Oracle, so the Oracle filter is ``type IS NULL``.
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

    async def list_by_activity(self, activity_oid: str) -> list[Row]:
        """Return every membership of an activity (no join, no pagination).

        Lifts ``activity_statistics_router`` / ``mcp_router``'s
        ``find({"activity": ...})`` reads; callers use ``duration`` for stats.
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

    async def clear_for_all(self) -> None:
        """Delete every total-time row (``tasks/compute_time.compute_time``)."""
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
        """Create or replace a user's per-academic-year row.

        Lifts ``tasks/compute_time.compute_ay_time`` (whole-row replace).
        """
        ...

    async def clear_academic_years(self) -> None:
        """Delete every per-academic-year row (``compute_ay_time``)."""
        ...

    async def clear_with_origin(self) -> None:
        """Delete every per-origin breakdown row (``compute_batch_origins``)."""
        ...

    async def clear_with_origin_stats(self) -> None:
        """Delete every ``time_with_origin_stat`` row (``compute_batch_origins``)."""
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
        page: int = -1,
        size: int = 10,
    ) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for the admin log browser.

        Lifts ``routers/logs_router.py.read_logs``: ``query`` is a case-sensitive
        *contains* on ``url``/``data`` (``re.escape``d; empty = no filter),
        ``performer`` an ``user`` equality, ``types``/``levels`` OR-lists on
        ``type``/``level``, and ``time_start``/``time_end`` are **exclusive**
        bounds on the ``timestamp`` float. Ordered by ``timestamp`` desc;
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

        Lifts ``main.py::mark_all_tasks_failed``.
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
