"""Pool wait counter: real blocking-checkout timing (no fabricated values)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from db.oracle import session as oracle_session
from services import db_monitor


class FakeQueue:
    """Stand-in for ``AsyncAdaptedQueue``: sqlalchemy's ``get`` is synchronous."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.calls: list[tuple[bool, float | None]] = []

    def get(self, block: bool = True, timeout=None):
        self.calls.append((block, timeout))
        if self.delay:
            # time.sleep, not await: the production call is a sync method that
            # only blocks inside the greenlet.
            import time

            time.sleep(self.delay)
        return object()


def make_pool(queue: FakeQueue):
    return SimpleNamespace(_pool=queue, size=lambda: 5, checkedout=lambda: 5,
                           _max_overflow=5)


@pytest.fixture(autouse=True)
def clean_stats(monkeypatch):
    monkeypatch.delenv("PERF_POOL_WAIT_MS", raising=False)
    oracle_session.reset_pool_wait_stats()
    monkeypatch.setattr(oracle_session, "_pool_wait_probe_installed", False, raising=False)
    yield
    oracle_session.reset_pool_wait_stats()


def test_threshold_defaults_and_is_configurable(monkeypatch):
    assert oracle_session.pool_wait_threshold_ms() == 50.0
    monkeypatch.setenv("PERF_POOL_WAIT_MS", "5")
    assert oracle_session.pool_wait_threshold_ms() == 5.0
    monkeypatch.setenv("PERF_POOL_WAIT_MS", "0")
    assert oracle_session.pool_wait_threshold_ms() == 0.0
    monkeypatch.setenv("PERF_POOL_WAIT_MS", "not-a-number")
    assert oracle_session.pool_wait_threshold_ms() == 50.0
    monkeypatch.setenv("PERF_POOL_WAIT_MS", "-3")
    assert oracle_session.pool_wait_threshold_ms() == 50.0


def test_slow_blocking_checkout_counts_fast_and_non_blocking_do_not(monkeypatch):
    monkeypatch.setenv("PERF_POOL_WAIT_MS", "50")
    queue = FakeQueue(delay=0.08)
    assert oracle_session.install_pool_wait_probe(make_pool(queue)) is True

    # A blocking get (pool exhausted) above the threshold: one wait.
    queue.get(block=True, timeout=30)
    assert oracle_session.pool_wait_count() == 1
    assert oracle_session.pool_wait_max_ms() >= 50.0

    # A fast hand-off must not be counted.
    queue.delay = 0.001
    queue.get(block=True, timeout=30)
    assert oracle_session.pool_wait_count() == 1

    # A non-blocking get means a free connection existed: never a wait, even if
    # the call itself were slow.
    queue.delay = 0.2
    queue.get(block=False, timeout=30)
    assert oracle_session.pool_wait_count() == 1
    assert queue.calls == [(True, 30), (True, 30), (False, 30)]


def test_threshold_is_honoured(monkeypatch):
    queue = FakeQueue(delay=0.02)
    monkeypatch.setenv("PERF_POOL_WAIT_MS", "50")
    oracle_session.install_pool_wait_probe(make_pool(queue))
    queue.get(block=True, timeout=30)
    assert oracle_session.pool_wait_count() == 0  # 20ms < 50ms

    monkeypatch.setenv("PERF_POOL_WAIT_MS", "10")
    queue.get(block=True, timeout=30)
    assert oracle_session.pool_wait_count() == 1  # same 20ms >= 10ms


def test_install_is_idempotent_and_leaves_non_queue_pools_alone():
    queue = FakeQueue()
    pool = make_pool(queue)
    assert oracle_session.install_pool_wait_probe(pool) is True
    assert oracle_session.install_pool_wait_probe(pool) is False  # already wrapped
    # The queue is wrapped once, not twice: a single slow get counts once.
    queue.delay = 0.06
    queue.get(True, 30)
    assert oracle_session.pool_wait_count() == 1

    oracle_session.reset_pool_wait_stats()
    assert oracle_session.install_pool_wait_probe(SimpleNamespace()) is False


def test_record_pool_wait_rejects_garbage():
    assert oracle_session.record_pool_wait(None) is False
    assert oracle_session.record_pool_wait("abc") is False
    assert oracle_session.record_pool_wait(float("nan")) is False
    assert oracle_session.pool_wait_count() == 0


def test_pool_sections_report_the_real_counter(monkeypatch):
    monkeypatch.setattr(
        "db.oracle.engine.get_engine",
        lambda: SimpleNamespace(
            pool=SimpleNamespace(
                size=lambda: 5, checkedout=lambda: 3, overflow=lambda: -4,
                _max_overflow=5,
            )
        ),
    )
    monkeypatch.setattr(oracle_session, "pool_wait_count", lambda: 7)

    pool = db_monitor.pool_metrics()
    assert pool["waited"] == 7
    assert pool["overflow"] == 0  # negative overflow is clamped
    assert pool["capacity"] == 10

    legacy = db_monitor._pool_status()
    assert legacy["waited"] == 7
    assert legacy["overflow"] == 0


@pytest.mark.asyncio
async def test_get_session_helpers_still_acquire_and_close(monkeypatch):
    """The probe must not change the acquire/close contract of the helpers."""
    closed = {"count": 0}

    class FakeSession:
        async def close(self):
            closed["count"] += 1

    monkeypatch.setattr(oracle_session, "_sessionmaker", lambda: FakeSession())
    monkeypatch.setattr(oracle_session, "install_pool_wait_probe", lambda *a, **k: True)

    session = oracle_session.get_sessionmaker()()
    assert isinstance(session, FakeSession)
    await session.close()
    assert closed["count"] == 1

    async with oracle_session.session_scope() as scoped:
        assert isinstance(scoped, FakeSession)
    assert closed["count"] == 2


def test_real_queuepool_checkout_that_waits_is_counted(monkeypatch):
    """End-to-end: the production wrapper on a real SQLAlchemy QueuePool.

    Uses sqlite3 (stdlib) as the DBAPI so the whole checkout/checkin path —
    ``QueuePool._do_get`` -> ``self._pool.get(wait, timeout)`` — is exercised.
    """
    import sqlite3
    import threading
    import time as _time

    from sqlalchemy.dialects.sqlite import pysqlite
    from sqlalchemy.pool import QueuePool

    monkeypatch.setenv("PERF_POOL_WAIT_MS", "50")
    pool = QueuePool(
        lambda: sqlite3.connect(":memory:"),
        pool_size=1,
        max_overflow=0,
        timeout=5,
        dialect=pysqlite.dialect(),
    )
    assert oracle_session.install_pool_wait_probe(pool) is True

    holder = pool.connect()  # the only connection in the pool is now checked out

    def release_after_a_while():
        _time.sleep(0.1)
        holder.close()  # returns it, unblocking the waiter

    thread = threading.Thread(target=release_after_a_while)
    thread.start()
    started = _time.perf_counter()
    waiter = pool.connect()  # pool exhausted -> blocks on the queue
    waited_ms = (_time.perf_counter() - started) * 1000
    waiter.close()
    thread.join()

    assert waited_ms >= 50
    assert oracle_session.pool_wait_count() == 1
    assert oracle_session.pool_wait_max_ms() >= 50.0


def test_real_queuepool_free_checkout_is_not_counted():
    import sqlite3

    from sqlalchemy.dialects.sqlite import pysqlite
    from sqlalchemy.pool import QueuePool

    pool = QueuePool(
        lambda: sqlite3.connect(":memory:"),
        pool_size=2,
        max_overflow=0,
        timeout=5,
        dialect=pysqlite.dialect(),
    )
    oracle_session.install_pool_wait_probe(pool)
    first = pool.connect()
    second = pool.connect()  # a free slot exists: not a wait
    first.close()
    second.close()
    assert oracle_session.pool_wait_count() == 0
