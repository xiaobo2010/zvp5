"""AI gateway traffic and audit trails (``AI_CALL_LOGS`` / ``AI_AGENT_EVENTS``
/ ``AI_PROMPT_AUDIT``)."""

from __future__ import annotations

from typing import Any, Protocol

from datetime import datetime

from .common import Row


class AICallLogsRepo(Protocol):
    """Data access for ``AI_CALL_LOGS`` (AI 网关调用流水, V024).

    Rows use the Mongo document shape ``{"_id": <hex str>, "user_oid": str,
    "capability": "ocr" | "chat", "endpoint": str, "provider": str | None,
    "model": str | None, "status": "success" | "error" | "timeout" |
    "rejected" | "busy", "http_status": int | None, "error_code": str | None,
    "latency_ms": int | None, "units": int, "input_bytes": int | None,
    "request_id": str | None, "key_scope": "personal" | "shared" | "server" |
    None, "prompt_tokens": int | None, "completion_tokens": int | None,
    "total_tokens": int | None, "attempt_no": int | None, "purpose": str |
    None, "first_token_ms": int | None, "engine_kind": str | None,
    "attachment_oid": str | None, "round_no": int | None,
    "roles_snapshot": str | None, "cache_read_tokens": int | None,
    "cache_write_tokens": int | None, "cache_key": str | None,
    "created_at": datetime}``.

    The table is append-only in normal operation: :meth:`insert` is the only
    write, and nothing here updates a row. The one deletion path is
    :meth:`purge_batch` (保留期清理, called by :mod:`tasks.retention`), which
    removes whole expired rows in bounded batches — irreversibly, one commit per
    batch. ``units`` is the **Agent Fuel (AF)** this call consumed
    (``max(1, ceil(total_tokens / 1000 × model rate))``), so the ledger and
    ``AI_QUOTA.used`` can be reconciled against each other.
    ``key_scope`` records which kind of key paid for the call, so "platform
    spend" and "admin's own key" stay separable in the same report.

    Metadata only — prompt/completion text is never stored (a compliance
    decision, not a gap). ``attempt_no`` + ``request_id`` make one request's
    retry chain greppable, ``purpose`` records which UI scene asked, and the
    three token columns carry the upstream ``usage`` numbers.
    """

    async def insert(
        self,
        *,
        user_oid: str,
        capability: str,
        endpoint: str,
        provider: str | None = None,
        model: str | None = None,
        status: str = "success",
        http_status: int | None = None,
        error_code: str | None = None,
        latency_ms: int | None = None,
        units: int = 0,
        input_bytes: int | None = None,
        request_id: str | None = None,
        key_scope: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        attempt_no: int | None = None,
        purpose: str | None = None,
        first_token_ms: int | None = None,
        engine_kind: str | None = None,
        attachment_oid: str | None = None,
        round_no: int | None = None,
        roles_snapshot: str | None = None,
        cache_read_tokens: int | None = None,
        cache_write_tokens: int | None = None,
        cache_key: str | None = None,
    ) -> str:
        """Append one call record and return its new ``_id``.

        ``status`` defaults to ``success`` because that is the only outcome
        where the caller has nothing extra to pass; failures must say so
        explicitly.

        ``key_scope`` is ``personal`` (an admin's own key), ``shared`` (a
        common key), ``server`` (the server-side default config) or ``None``
        (unknown / rows written before the column existed). It never takes part
        in auth — it only makes the ledger auditable per funding source.

        The token columns are ``None`` unless the upstream reported ``usage``;
        ``None`` is the marker for "this row's ``units`` is an estimate".
        ``units`` itself is Agent Fuel (AF), not raw tokens: one AF is 1000
        tokens at the base price, scaled by ``AI_MODELS.RATE`` for the model
        that served the call (OCR is flat 1 AF per enqueue/recognition).
        ``attempt_no`` is 1-based within one request. ``purpose`` is the calling
        scene (open set: ``chat`` / ``markdown-assistant`` / ``ocr`` / ...), and
        ``engine_kind`` / ``attachment_oid`` are OCR-only trace fields.
        ``round_no`` is the agent round (1-based) and ``roles_snapshot`` freezes
        the caller's roles at call time; both are ``None`` outside the agent
        path (「未知 / 未采集」, not 0 / empty).

        ``cache_read_tokens`` / ``cache_write_tokens`` are the *normalised*
        prompt-prefix cache numbers (OpenAI ``cached_tokens``, Anthropic
        ``cache_read_input_tokens`` / ``cache_creation_input_tokens``, DeepSeek
        ``prompt_cache_hit_tokens``); ``cache_key`` is our stable prefix hash
        (first 16 hex chars of a sha256, no user content). All three are
        ``None`` — never 0 — when the upstream did not report them or nothing
        was injected.
        """
        ...

    async def count_since(self, user_oid: str, since: datetime) -> int:
        """Number of calls by ``user_oid`` at/after ``since`` (rate-limit window)."""
        ...

    async def count_chat_by_user_since(self, user_oid: str, since: datetime) -> int:
        """Number of **chat** calls by ``user_oid`` at/after ``since``.

        This is the evidence for the ``ai_chat_burst`` risk rule: only
        ``capability='chat'`` rows count (OCR has its own rate limit and is not
        "chatting"), and every row counts — ``rejected`` / ``busy`` included —
        because a client hammering a closed door is exactly the behaviour the
        rule is meant to surface.
        """
        ...

    async def list_page(
        self,
        *,
        user_oid: str | None = None,
        capability: str | None = None,
        status: str | None = None,
        key_scope: str | None = None,
        request_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        purpose: str | None = None,
        time_start: datetime | None = None,
        time_end: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> list[Row]:
        """Return one page, newest first (``created_at DESC, oid DESC``).

        All filters are optional; ``None`` means "no filter". ``page`` is
        1-based. ``time_start`` / ``time_end`` are **inclusive** bounds on
        ``created_at`` (naive local datetimes, the column's convention); the
        epoch-seconds → datetime conversion belongs to the router.

        ``request_id`` / ``provider`` / ``model`` / ``purpose`` are exact
        matches: they are "look up this known value" entry points.
        """
        ...

    async def total_count(
        self,
        *,
        user_oid: str | None = None,
        capability: str | None = None,
        status: str | None = None,
        key_scope: str | None = None,
        request_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        purpose: str | None = None,
        time_start: datetime | None = None,
        time_end: datetime | None = None,
    ) -> int:
        """Total rows matching the same filters as :meth:`list_page`."""
        ...

    async def stats(
        self,
        *,
        since: datetime,
        until: datetime | None = None,
    ) -> dict[str, Any]:
        """Aggregate calls in ``[since, until]``: per day plus per dimension.

        Returns ``{"daily": [{"date": "YYYY-MM-DD", "calls": int, "errors": int,
        "prompt_tokens": int, "completion_tokens": int, "total_tokens": int,
        "avg_latency_ms": int | None}], "by_provider": [...], "by_model": [...],
        "by_purpose": [...], "by_user": [...], "by_status": [...], "cache":
        {...}}``; the provider/model/purpose breakdown rows are
        ``{"<dimension>": str | None, "calls": int, "errors": int, "units": int,
        "total_tokens": int}``.

        ``by_user`` is the "who is spending it" view (top 50 by call count):
        ``{"user_oid": str, "name": str, "calls": int, "errors": int,
        "total_tokens": int, "units": int, "avg_latency_ms": int | None}``.
        ``name`` comes from a ``LEFT JOIN USERS`` so a deleted account still
        shows its row (falling back to the oid string — the audit row outlives
        the account). ``by_status`` is ``{"status": str | None, "calls": int}``:
        the split between success / error / rejected / busy that ``errors``
        alone cannot answer.

        ``cache`` is one window-wide row (not a list):
        ``{"calls": int, "cached_calls": int, "prompt_tokens": int,
        "cache_read_tokens": int, "cache_write_tokens": int,
        "hit_rate": float | None}``. ``cached_calls`` counts the rows that
        *reported* a hit count, and ``hit_rate`` is normalised hit tokens over
        ``prompt_tokens`` (which already includes the hit part), rounded to 4
        decimals; it is ``None`` when the window holds no usage-reported
        ``prompt_tokens`` at all (no denominator), never 0.

        ``errors`` counts every row whose ``status`` is not ``success``
        (``rejected`` / ``busy`` included). Token sums are 0 when the window
        holds no usage-reported rows — that is honestly "nothing observed",
        unlike the per-row NULL which means "unknown".

        All parts come from **one** window on purpose: separate calls would let
        the total and the breakdowns disagree across a minute boundary, and the
        cache hit rate's denominator (``prompt_tokens``) would no longer match
        its numerator.
        """
        ...

    async def usage_breakdown(
        self,
        *,
        user_oid: str,
        since: datetime,
        until: datetime,
    ) -> list[dict[str, Any]]:
        """One user's calls in ``[since, until)`` grouped by provider + model.

        Returns ``[{"provider": str | None, "model": str | None, "calls": int,
        "units": int, "prompt_tokens": int, "completion_tokens": int,
        "total_tokens": int}]``, ordered by ``calls`` desc then provider/model
        asc so the caller can render it as-is. ``provider`` / ``model`` keep
        their per-row NULL (OCR engines and unlabelled calls) instead of being
        dropped or merged into a string bucket.

        ``calls`` counts every row in the window (failures included — the quota
        page shows attempts, not just successes). The sums are ``COALESCE(..., 0)``
        so a window with no usage-reported rows reads 0, while the per-row NULL
        still means "unknown". The window is half-open on purpose: two adjacent
        periods must not both count a call made at midnight.
        """
        ...

    async def count_expired(self, cutoff: datetime) -> int:
        """Rows with ``created_at < cutoff`` (read-only; same predicate as the purge)."""
        ...

    async def purge_batch(self, cutoff: datetime, batch_size: int) -> int:
        """Delete at most ``batch_size`` rows with ``created_at < cutoff``.

        不可逆，按批提交：每批一个短 session、一次 commit，批循环在
        :mod:`tasks.retention`。返回实际删除行数，``0`` 表示已经删空。
        """
        ...


class AIAgentEventsRepo(Protocol):
    """Data access for ``AI_AGENT_EVENTS`` (agent 工具调用留痕, V033 + V034).

    Rows use the Mongo document shape ``{"_id": <32 hex>, "request_id": str |
    None, "round_no": int | None, "tool_name": str | None, "tool_kind": "read" |
    "write" | None, "args_hash": str | None, "args_summary": str | None,
    "args_payload": str | None, "preview": str | None, "result_summary": str |
    None, "status": "executed" | "proposed" | "executing" | "confirmed" |
    "rejected" | "expired" | "failed" | "denied", "actor_oid": str | None,
    "approver_oid": str | None, "confirmed_at": datetime | None, "created_at":
    datetime}``.

    A row is written for **every** tool-call attempt, including the ones the
    gateway refused (``denied``) and the proposals that never got confirmed
    (``rejected`` / ``expired``): only logging the happy path would hide the
    signal governance cares about most — the model probing outside the
    whitelist.

    ``args_hash`` is the sha256 of the **stored full arguments** — the
    ``ARGS_PAYLOAD`` canonical JSON (CLOB, V034), with ``ARGS_SUMMARY`` as the
    fallback for rows written before V034 — so confirmation can recompute and
    compare it. ``ARGS_SUMMARY`` (≤500) and ``PREVIEW`` are display-only and are
    never hashed; ``oid`` is a 32-hex ``uuid4().hex`` used directly as the
    proposal handle.

    This is the only audit table that allows an update: a confirmation moves a
    ``proposed`` row through ``executing`` to a terminal status. Every transition
    is a CAS on the expected current status, so a terminal row (``confirmed`` /
    ``rejected`` / ``expired`` / ``failed`` / ``denied``) can never be
    overwritten. ``args_hash`` / ``args_payload`` / ``args_summary`` /
    ``preview`` are never rewritten — otherwise "what was approved" and "what
    ran" could drift apart.
    """

    async def insert(
        self,
        *,
        oid: str,
        request_id: str | None = None,
        round_no: int | None = None,
        tool_name: str | None = None,
        tool_kind: str | None = None,
        args_hash: str | None = None,
        args_summary: str | None = None,
        args_payload: str | None = None,
        preview: str | None = None,
        result_summary: str | None = None,
        status: str = "executed",
        actor_oid: str | None = None,
        approver_oid: str | None = None,
        confirmed_at: datetime | None = None,
    ) -> str:
        """Append one tool-call event and return its ``oid`` (caller-supplied)."""
        ...

    async def get(self, oid: str) -> Row | None:
        """Return one event by ``oid``, or ``None``."""
        ...

    async def update_status(
        self,
        oid: str,
        status: str,
        *,
        from_status: str = "proposed",
        result_summary: str | None = None,
        approver_oid: str | None = None,
        confirmed_at: datetime | None = None,
    ) -> bool:
        """CAS: move a row whose status is ``from_status`` to ``status``.

        ``False`` means the row was not in the expected state (someone else won
        the race) — the caller answers 409/410 instead of executing twice.
        ``approver_oid`` / ``confirmed_at`` are written only when not ``None``.
        """
        ...

    async def update_status_unconditional(
        self, oid: str, status: str, *, result_summary: str | None = None
    ) -> bool:
        """Set ``status`` regardless of the current one — **data repair only**.

        Never use it for a state-machine transition: it would overwrite a
        terminal status and make an executed write look like it never happened.
        """
        ...

    async def list_for_request(
        self, request_id: str, *, after_round: int = 0
    ) -> list[Row]:
        """Every tool event of one run, in write order.

        ``after_round > 0`` keeps only ``round_no > after_round`` (poll deltas);
        the default ``0`` returns the whole timeline.
        """
        ...

    async def list_display_for_request(
        self, request_id: str, *, after_round: int = 0
    ) -> list[Row]:
        """Same rows/order as :meth:`list_for_request`, without the CLOB columns.

        ``args_payload`` / ``args_hash`` are not selected (they are filled with
        ``None``) — polling never needs the full canonical arguments.
        """
        ...

    async def list_by_actor(
        self,
        actor_oid: str,
        *,
        status: str | None = None,
        limit: int = 20,
    ) -> list[Row]:
        """One actor's events, newest first (optionally filtered by ``status``).

        ``GET /proposals`` uses this so the "only my proposals" rule lives in the
        query instead of in every caller.
        """
        ...

    async def count_expired(self, cutoff: datetime) -> int:
        """Rows with ``created_at < cutoff`` (read-only; same predicate as the purge)."""
        ...

    async def purge_batch(self, cutoff: datetime, batch_size: int) -> int:
        """Delete at most ``batch_size`` rows with ``created_at < cutoff``.

        The window follows ``AI_CALL_LOGS_RETENTION_DAYS``: 工具事件与调用流水
        是同一次 run 的两面，一边删了另一边还在，会让「这条提案是谁确认的」
        变成悬空的历史。不可逆，按批提交。
        """
        ...


class AIPromptAuditRepo(Protocol):
    """Data access for ``AI_PROMPT_AUDIT`` (受控的 prompt 正文留存, V033).

    Metadata rows (``list_page``) use ``{"_id": <24 hex>, "user_oid": str |
    None, "purpose": str | None, "roles_snapshot": str | None, "created_at":
    datetime, "prompt_chars": int}`` — **no body**. The detail row
    (``get_by_oid``) adds ``request_id`` and ``prompt_text``.

    The split is deliberate: paging through dozens of student conversations
    should not pull every body into memory, and the body is only readable
    through the single-review endpoint — which must write an ``APP_LOGS``
    access row in the same request.

    ``insert`` is append-only (``services.ai.prompt_audit`` owns masking and
    truncation); the only deletion path is :meth:`purge_before`, which removes
    whole expired rows in bounded batches for :mod:`tasks.retention`. Rewriting
    a row would make "what the student actually asked" untrustworthy.

    ``count`` is the pagination total for the same filters as :meth:`list_page`;
    :meth:`count_expired` answers the retention question ("how many rows are
    past the cutoff") and is a different predicate.
    """

    async def insert(
        self,
        *,
        request_id: str | None = None,
        user_oid: str | None = None,
        roles_snapshot: str | None = None,
        purpose: str | None = None,
        prompt_text: str | None = None,
    ) -> str:
        """Append one captured prompt and return its new ``_id``.

        ``prompt_text`` is stored as received: masking/truncation happens in
        :mod:`services.ai.prompt_audit`, and a defensive rewrite here would
        falsify "the table holds what the caller decided to keep".
        """
        ...

    async def list_page(
        self,
        *,
        user_oid: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> list[Row]:
        """One page of **metadata** (newest first, ``created_at DESC, oid DESC``).

        Body is never selected here; ``prompt_chars`` is computed in SQL
        (``LENGTH``) so the caller can show "how long was it" without reading it.
        """
        ...

    async def count(
        self,
        *,
        user_oid: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int:
        """Total rows matching :meth:`list_page`'s filters (pagination size)."""
        ...

    async def get_by_oid(self, oid: str) -> Row | None:
        """Return one row **including** ``prompt_text``, or ``None``."""
        ...

    async def count_expired(self, cutoff: datetime) -> int:
        """Rows with ``created_at < cutoff`` (read-only; purge predicate)."""
        ...

    async def purge_before(self, cutoff: datetime, batch_size: int) -> int:
        """Delete at most ``batch_size`` rows with ``created_at < cutoff``.

        不可逆，按批提交；``0`` 表示已经删空。
        """
        ...
