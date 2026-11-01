"""Attachment tombstone retention job (design §3.3).

``OracleFilesRepo.purge_deleted`` (``db/repos/oracle/files.py:592``, statement
``_purge_deleted_stmt`` at ``:290``) nulls the ``content`` BLOB of rows that are
``status='deleted'`` and whose ``deleted_at`` predates a cutoff, keeping the
metadata tombstone. It was implemented but had **no caller**; this module is
that caller and ``main.py`` schedules :func:`purge_expired_attachments` daily at
03:00 HKT (``id="daily_purge_files"``).

The retention window is read *defensively*: ``config.py`` is a deployment-local
file, so a key added to the repository's copy may be absent on a host that
predates it — ``from config import FILE_DELETED_RETENTION_DAYS`` would raise
``ImportError`` at import time and stop the whole application from starting.
Hence ``import config`` + ``getattr`` with a code default.

Scope note: this clears bytes only. The design's later stage (deleting the row
itself) is deliberately **not** implemented here.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import config
from db import backends

_logger = logging.getLogger(__name__)

# Matches the §3.3 window for rejected/quarantined rows (30 days).
DEFAULT_RETENTION_DAYS = 30


def retention_days() -> int:
    """Return the retention window in days, never raising on bad config.

    Missing key, non-integer value and non-positive values all fall back to
    :data:`DEFAULT_RETENTION_DAYS`.
    """
    raw = getattr(config, "FILE_DELETED_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS
    return days if days > 0 else DEFAULT_RETENTION_DAYS


def retention_cutoff(now: datetime, days: int) -> datetime:
    """Pure helper: the cutoff a tombstone must precede to lose its bytes."""
    return now - timedelta(days=days)


async def purge_expired_attachments() -> int:
    """Clear bytes of tombstones older than the retention window.

    Returns the purged row count (``0`` when nothing matched). Failures are
    logged, never raised: a bad row or a transient DB error must not kill the
    scheduler or the process.
    """
    try:
        days = retention_days()
        cutoff = retention_cutoff(datetime.now(), days)
        purged = await backends.get_files_repo().purge_deleted(cutoff)
        _logger.info(
            "purge_expired_attachments: purged %s tombstone row(s); "
            "retention=%sd; cutoff=%s",
            purged,
            days,
            cutoff.isoformat(),
        )
        return int(purged)
    except Exception as exc:
        _logger.exception("purge_expired_attachments failed: %r", exc)
        return 0
