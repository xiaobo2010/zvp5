"""Groups — class groups and permission groups (``groups``)."""

from __future__ import annotations

from typing import Protocol

from .common import Row


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
        literal ``contains`` over ``name``/``description`` (empty = no filter),
        ``sort``/``asc`` order the rows (unknown/empty ``sort`` falls back to
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
        as a case-insensitive *contains* on ``name`` (LIKE wildcards escaped,
        empty = no filter); ``permissions`` restricts to groups whose
        ``permissions`` array intersects the given values (empty/``None`` = no
        filter).

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
