"""Offline tests for the attachment retention job (design §3.3).

Pins the contract of ``tasks/files_retention.py`` and its registration in
``main.startup_event``:

* ``retention_cutoff`` arithmetic (pure, fixed ``now``);
* the config read is **import-safe** — production ``config.py`` is a
  deployment-local file, so a plain ``from config import KEY`` would raise
  ``ImportError`` at import time and prevent the app from starting. Missing /
  non-integer / non-positive values must fall back to the built-in default;
* the job hands ``purge_deleted`` a cutoff ~ ``now - retention`` and logs the
  row count, including ``0``;
* a failing repository does **not** propagate out of the job (the scheduler and
  the process must survive);
* ``startup_event`` actually registers ``daily_purge_files`` at 03:00 HKT.

Everything is offline: no database, no network. ``get_files_repo`` is replaced
by a fake and ``main`` is imported only for the registration assertions (it
imports cleanly offline from the repository root — see
``tests/golden/attachments/conftest.py`` for the ``sys.path`` setup).

Run from the repository root::

    ./.venv/bin/python -m pytest tests/golden/attachments/test_files_retention.py -q
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

import pytest

import config
from db import backends
from tasks import files_retention
from tasks.files_retention import (
    DEFAULT_RETENTION_DAYS,
    purge_expired_attachments,
    retention_cutoff,
    retention_days,
)

# A fixed instant so cutoff arithmetic is asserted exactly.
NOW = datetime(2026, 5, 1, 3, 0, 0)


class _FakeFilesRepo:
    """Records ``purge_deleted`` calls; optionally raises."""

    def __init__(self, rowcount: int = 3, exc: Exception | None = None) -> None:
        self.calls: list[datetime] = []
        self.rowcount = rowcount
        self.exc = exc

    async def purge_deleted(self, before: datetime) -> int:
        self.calls.append(before)
        if self.exc is not None:
            raise self.exc
        return self.rowcount


@pytest.fixture
def fake_repo(monkeypatch: pytest.MonkeyPatch) -> _FakeFilesRepo:
    repo = _FakeFilesRepo()
    monkeypatch.setattr(backends, "get_files_repo", lambda session=None: repo)
    return repo


# --------------------------------------------------------------------------
# cutoff arithmetic (pure)
# --------------------------------------------------------------------------


def test_retention_cutoff_subtracts_default_window():
    assert retention_cutoff(NOW, 30) == datetime(2026, 4, 1, 3, 0, 0)


def test_retention_cutoff_zero_days_is_now():
    assert retention_cutoff(NOW, 0) == NOW


def test_retention_cutoff_is_exact_for_positive_days():
    assert retention_cutoff(NOW, 7) == NOW - timedelta(days=7)
    assert retention_cutoff(NOW, 1) == datetime(2026, 4, 30, 3, 0, 0)


# --------------------------------------------------------------------------
# defensive config read
# --------------------------------------------------------------------------


def test_default_retention_days_is_30():
    assert DEFAULT_RETENTION_DAYS == 30


def test_retention_days_falls_back_when_key_missing(monkeypatch):
    monkeypatch.delattr(config, "FILE_DELETED_RETENTION_DAYS", raising=False)
    assert retention_days() == DEFAULT_RETENTION_DAYS


@pytest.mark.parametrize("bad", ["thirty", "3.5", None, 0, -5, object()])
def test_retention_days_falls_back_on_invalid_value(monkeypatch, bad):
    monkeypatch.setattr(config, "FILE_DELETED_RETENTION_DAYS", bad, raising=False)
    assert retention_days() == DEFAULT_RETENTION_DAYS


def test_retention_days_honours_positive_config(monkeypatch):
    monkeypatch.setattr(config, "FILE_DELETED_RETENTION_DAYS", 14, raising=False)
    assert retention_days() == 14


def test_retention_days_accepts_numeric_string(monkeypatch):
    monkeypatch.setattr(config, "FILE_DELETED_RETENTION_DAYS", "45", raising=False)
    assert retention_days() == 45


# --------------------------------------------------------------------------
# job behaviour
# --------------------------------------------------------------------------


def test_job_purges_with_cutoff_of_retention_window(fake_repo):
    before = datetime.now()
    purged = asyncio.run(purge_expired_attachments())
    after = datetime.now()

    assert purged == fake_repo.rowcount
    assert len(fake_repo.calls) == 1
    cutoff = fake_repo.calls[0]
    assert before - timedelta(days=DEFAULT_RETENTION_DAYS) <= cutoff
    assert cutoff <= after - timedelta(days=DEFAULT_RETENTION_DAYS)


def test_job_cutoff_follows_configured_days(monkeypatch, fake_repo):
    monkeypatch.setattr(config, "FILE_DELETED_RETENTION_DAYS", 7, raising=False)
    before = datetime.now()
    asyncio.run(purge_expired_attachments())
    after = datetime.now()

    cutoff = fake_repo.calls[0]
    assert before - timedelta(days=7) <= cutoff <= after - timedelta(days=7)


def test_job_logs_purged_count(fake_repo, caplog):
    caplog.set_level(logging.INFO, logger="tasks.files_retention")
    asyncio.run(purge_expired_attachments())
    out = caplog.text
    assert f"purged {fake_repo.rowcount} tombstone row(s)" in out
    assert "retention=30d" in out


def test_job_logs_and_returns_zero_when_nothing_matches(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="tasks.files_retention")
    repo = _FakeFilesRepo(rowcount=0)
    monkeypatch.setattr(backends, "get_files_repo", lambda session=None: repo)

    assert asyncio.run(purge_expired_attachments()) == 0
    assert "purged 0 tombstone row(s)" in caplog.text


def test_job_swallows_repo_failure(monkeypatch, caplog):
    repo = _FakeFilesRepo(exc=RuntimeError("boom"))
    monkeypatch.setattr(backends, "get_files_repo", lambda session=None: repo)

    # Must not raise: a failure here would surface inside the scheduler.
    assert asyncio.run(purge_expired_attachments()) == 0
    out = caplog.text
    assert "purge_expired_attachments failed" in out
    assert "boom" in out


def test_job_swallows_failure_from_repo_lookup(monkeypatch, caplog):
    def _boom(session=None):
        raise RuntimeError("no engine")

    monkeypatch.setattr(backends, "get_files_repo", _boom)

    assert asyncio.run(purge_expired_attachments()) == 0
    assert "no engine" in caplog.text


# --------------------------------------------------------------------------
# scheduler registration
# --------------------------------------------------------------------------


def test_startup_registers_daily_purge_job():
    import main

    async def _boot_and_inspect():
        if main.scheduler.running:
            main.scheduler.shutdown(wait=False)
        await main.startup_event()
        try:
            return {job.id: job for job in main.scheduler.get_jobs()}
        finally:
            main.scheduler.shutdown(wait=False)

    jobs = asyncio.run(_boot_and_inspect())

    assert "daily_purge_files" in jobs
    trigger = jobs["daily_purge_files"].trigger
    fields = {field.name: str(field) for field in trigger.fields}
    assert fields["hour"] == "3"
    assert fields["minute"] == "0"
    assert fields["second"] == "0"
    assert str(trigger.timezone) == "Asia/Hong_Kong"

    # Pre-existing jobs must survive the new registration.
    assert "daily_compute_time" in jobs
    assert "daily_generate_reports" in jobs
