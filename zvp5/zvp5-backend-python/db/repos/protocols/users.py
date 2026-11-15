"""Users, roles and warnings (``users`` / ``user_warnings`` / ``role_policies``)."""

from __future__ import annotations

from typing import Protocol

from datetime import datetime

from .common import Row


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

    async def get_for_auth(self, oid: str) -> Row | None:
        """Return only the columns the authentication path reads.

        Narrower than :meth:`get_by_oid` on purpose: ``past`` and
        ``eligibility`` are CLOBs that only profile/credential pages need,
        while every authenticated request reads this row.
        """
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
        self,
        query: str,
        page: int,
        size: int,
        include_expired: bool = False,
        now: datetime | None = None,
    ) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for one page of currently banned users.

        Source of truth is the ``banned`` gate on the user row (not the
        ``BAN_RECORDS`` ledger), so a user who was unbanned is absent even
        though the ledger still holds their history.

        ``include_expired=False``（默认）会把 ``ban_until`` 已过期的存量行
        排除：它们仍然 ``banned=1``，但实际已经能登录，算「当前封禁」会让
        管理员看到一张永远清不空的表。
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

    async def rotate_session(self, oid: str) -> None:
        """Rotate ``session_id`` so every token carrying the old ``sid`` is void."""
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

    async def set_ban_if_stronger(
        self,
        oid: str,
        *,
        reason: str,
        until: datetime | None,
        source: str,
        rule_code: str | None,
    ) -> datetime | None:
        """Ban atomically only when ``until`` outranks the current sentence.

        The comparison must live in the UPDATE predicate; a read-then-write
        pair lets a concurrent human permanent ban be shortened by a rule.
        Returns the currently effective ``ban_until`` (``None`` = permanent).
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
        (``{"id": {"$regex": "^<prefix>"}}``): a ``LIKE 'prefix%'`` prefix scan,
        with LIKE wildcards escaped so the prefix stays a literal.
        """
        ...

    async def list_by_class(self, class_oid: str) -> list[Row]:
        """Return users whose ``class_id`` is ``class_oid``.

        Lifts ``time_with_origin``'s group branch and ``tasks/compute_time``'s
        per-class reads (``{"class_id": <oid>}``).
        """
        ...

    async def search(
        self,
        query: str,
        logged_in: bool,
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for a page of users.

        ``logged_in`` selects between the two filter shapes: the three-branch
        ``$or`` (case-insensitive **literal** ``contains`` on ``name``/``id``/
        ``past``, LIKE wildcards escaped) and the unlogged exact
        ``{"id": query}``. Rows carry the
        ``_id``/``name``/``id``/``class_id`` projection.

        ``limit``/``offset`` push pagination into SQL (``OFFSET … FETCH``) and
        the total comes from a window count, so a page costs one round trip.
        ``limit is None`` keeps the unpaginated shape for callers that need
        every row (``routers/times_router.read_times``).
        """
        ...

    async def list_by_name_or_id(self, pattern: str) -> list[Row]:
        """Return full user docs matching a name/id keyword (empty = all users).

        Lifts ``routers/activities_v2_router.get_activity_members_v2``'s
        searched-users lookup; ``pattern`` is a plain keyword matched as a
        case-insensitive literal ``contains`` (LIKE wildcards escaped by the
        repo), not a regular expression.
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

    async def primary_owners(self, xuehai_ids: list[str]) -> dict[str, Row]:
        """``{xuehai_id: {"user_oid": str, "name": str}}`` in one round trip.

        Only ids that are actually someone's primary device appear; the device
        ban list uses this to avoid an N+1 owner lookup.
        """
        ...

    async def is_admin_primary_device(self, xuehai_id: str) -> bool:
        """Whether ``xuehai_id`` is **any admin's** 主学海设备.

        Read-only exemption probe used by the device ban gate: a rule-written
        device ban and an account-derived ban must not lock an admin's own
        tablet. Manual admin bans (``source="admin"``) are unaffected — the
        caller decides, this method only answers ownership.
        """
        ...


class UserWarningsRepo(Protocol):
    """Data access for ``USER_WARNINGS``（每月首次警告账本, V041）。

    Row shape: ``{"_id", "user_oid", "rule_code", "period", "reason",
    "detail", "created_at"}``。``period`` 是 ``YYYYMM``；唯一键
    ``(user_oid, rule_code, period)`` 让「月初清零」不需要定时任务，也
    正是 :meth:`try_record` 能把冲突翻译成 ``False`` 的原因。
    """

    async def try_record(
        self,
        user_oid: str,
        rule_code: str,
        period: str,
        reason: str | None = None,
        detail: str | None = None,
    ) -> bool:
        """写入本月第一条警告；本月已有则返回 ``False``。"""
        ...

    async def get(self, user_oid: str, rule_code: str, period: str) -> Row | None:
        """该用户该规则该月的警告行，没有则 ``None``。"""
        ...

    async def count_for_user_rule(self, user_oid: str, rule_code: str) -> int:
        """该用户该规则历史累计警告数。"""
        ...


class RolePoliciesRepo(Protocol):
    """Data access for ``ROLE_FEATURES`` / ``ROLE_QUOTAS``（角色策略, V043）。

    两张表是**稀疏覆盖层**：``(role, key)`` 没有行就等于「按代码内置
    ``services.roles.policy.DEFAULTS``」。所以 :meth:`get_role` 不返回 ``None``
    ——「这个角色配过没有」不是调用方关心的问题，「这个 key 有没有覆盖」才是，
    而后者就是 ``features`` / ``quotas`` 字典里有没有该键。

    Row shape（``list_all``）::

        {"features": [{"role", "feature_key", "enabled", "updated_at",
                       "updated_by"}, ...],
         "quotas":   [{"role", "kind", "monthly_limit", "updated_at",
                       "updated_by"}, ...]}

    ``enabled`` 已由 ``NUMBER(1)`` 翻成 ``bool``；``monthly_limit`` 是
    ``int | None``。``get_role`` / :meth:`upsert_role` 返回
    ``{"role", "features", "quotas", "updated_at", "updated_by"}``。
    """

    async def list_all(self) -> dict[str, list[Row]]:
        """两张全表一次取回（供进程内快照缓存）。"""
        ...

    async def get_role(self, role: str) -> Row:
        """某角色库里实际存在的覆盖行；没配过则是空 ``features``/``quotas``。"""
        ...

    async def upsert_role(
        self,
        role: str,
        features: dict[str, bool],
        quotas: dict[str, int | None],
        actor: str | None = None,
    ) -> Row:
        """**单事务全量替换**：没出现的 key 被删除，``None`` 额度不落行。"""
        ...
