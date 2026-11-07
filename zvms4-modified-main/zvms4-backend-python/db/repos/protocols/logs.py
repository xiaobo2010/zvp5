"""Runtime application logs (``app_logs``)."""

from __future__ import annotations

from typing import Protocol

from datetime import datetime

from .common import Row


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
        exclude_types: list[str] | None = None,
    ) -> tuple[list[Row], int]:
        """Return ``(rows, total)`` for the admin log browser.

        Lifts ``routers/logs_router.py.read_logs``: ``query`` is a case-sensitive
        *contains* on ``url``/``data`` (``re.escape``d; empty = no filter),
        ``performer`` an ``user`` equality, ``types``/``levels`` OR-lists on
        ``type``/``level``, ``time_start``/``time_end`` are **exclusive** bounds
        on the ``timestamp`` float, and ``xuehai_id`` an equality on the captured
        device 学海 ID (empty = no filter). Ordered by ``timestamp`` desc;
        ``page == -1`` means "no offset".

        ``exclude_types`` is an OR-blacklist on ``type`` that only applies when
        ``types`` is empty: the admin browser uses it to hide ``login_fail`` by
        default, while an explicit ``types`` whitelist always wins.
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
