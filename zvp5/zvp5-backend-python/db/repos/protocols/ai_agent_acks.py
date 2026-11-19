"""Per-account acknowledgement of the AI agent first-use safety warning.

``AI_AGENT_ACKS`` (V054) holds one row per account that has confirmed the
warning; an empty table means "not confirmed yet". The row is keyed by
``user_oid`` with no separate oid and no FK: it is user data (not audit
material) and users may be hard-deleted.

Implementations must treat :meth:`AIAgentAcksRepo.ack` as **first-write-wins**:
a repeated call never moves ``acked_at`` and returns the stored timestamp.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class AIAgentAcksRepo(Protocol):
    """Data access for ``AI_AGENT_ACKS``.

    Session ownership follows the other Oracle repos: with no injected session
    each method opens (and commits) its own; an injected session is neither
    committed nor closed.
    """

    async def get(self, user_oid: str) -> datetime | None:
        """Return when the account confirmed the warning, or ``None``."""
        ...

    async def ack(self, user_oid: str, now: datetime) -> datetime:
        """Record the acknowledgement and return its timestamp.

        Idempotent: if a row already exists, the stored ``acked_at`` is
        returned unchanged. Two concurrent calls must both succeed (the loser
        reads back the winner's row) — no duplicate row, no 500.
        """
        ...
