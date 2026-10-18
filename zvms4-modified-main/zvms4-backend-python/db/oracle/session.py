"""Session helpers for the Oracle async engine.

The sessionmaker is built lazily so importing this module performs no I/O.
Three entry points are provided:

* :func:`get_session` - a FastAPI dependency that yields a session and closes
  it afterwards.
* :func:`get_oracle_session` - a FastAPI dependency that yields a session
  (kept as a named alias for call sites expecting the Oracle-specific name).
* :func:`session_scope` - an async context manager for non-FastAPI callers.

ADB is the only backend, so no session entry point is conditional.

Pool-wait instrumentation (:func:`install_pool_wait_probe`) lives here because
this module owns the engine's sessionmaking; it only *measures* the existing
checkout path and never changes what is acquired, rolled back or closed.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import engine as oracle_engine

_logger = logging.getLogger(__name__)

# ``PERF_POOL_WAIT_MS``: how long a *blocking* pool checkout must take to count
# as "the pool made the request wait". 50ms is well above queue hand-off noise
# (microseconds) and well below a real ADB round trip, so it does not fire for
# connection creation/reuse.
POOL_WAIT_MS_DEFAULT = 50.0

_sessionmaker: Optional[async_sessionmaker] = None

_install_lock = threading.Lock()
_pool_wait_lock = threading.Lock()
_pool_wait_count = 0
_pool_wait_max_ms = 0.0
_pool_wait_probe_installed = False


def get_sessionmaker() -> async_sessionmaker:
    """Return the process-wide sessionmaker, creating it on first use.

    Installing the pool-wait probe here means it is in place before any session
    (and therefore any checkout) exists.
    """
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = oracle_engine.create_sessionmaker()
        install_pool_wait_probe()
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yield a session and always close it."""
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        await session.close()


async def get_oracle_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: yield a session and always close it.

    Alias of :func:`get_session`; kept under the Oracle-specific name for
    call sites that expect it. ADB is the only backend, so a session is
    always created.
    """
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        await session.close()


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Async context manager yielding a session that is always closed."""
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        await session.close()


# --------------------------------------------------------------------------- #
# pool wait probe
# --------------------------------------------------------------------------- #


def pool_wait_threshold_ms() -> float:
    """``PERF_POOL_WAIT_MS`` (default 50ms); a broken value falls back silently."""
    raw = os.environ.get("PERF_POOL_WAIT_MS")
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return POOL_WAIT_MS_DEFAULT
    return value if value >= 0 and value == value else POOL_WAIT_MS_DEFAULT


def pool_wait_count() -> int:
    """How many blocking checkouts this process saw take >= the threshold."""
    with _pool_wait_lock:
        return _pool_wait_count


def pool_wait_max_ms() -> float:
    """Longest observed blocking-checkout wait, in milliseconds (0.0 if none)."""
    with _pool_wait_lock:
        return _pool_wait_max_ms


def reset_pool_wait_stats() -> None:
    """Test/admin hook: forget observed waits (does not touch the pool)."""
    global _pool_wait_count, _pool_wait_max_ms
    with _pool_wait_lock:
        _pool_wait_count = 0
        _pool_wait_max_ms = 0.0


def record_pool_wait(elapsed_ms: float) -> bool:
    """Count one wait when ``elapsed_ms`` reaches the threshold.

    Sub-threshold hand-offs are deliberately not counted: a counter that ticks
    on every checkout would be noise, not a signal. Nothing is fabricated — an
    idle pool reports 0.
    """
    try:
        elapsed = float(elapsed_ms)
    except (TypeError, ValueError):
        return False
    if elapsed != elapsed or elapsed < pool_wait_threshold_ms():  # NaN guard
        return False
    global _pool_wait_count, _pool_wait_max_ms
    with _pool_wait_lock:
        _pool_wait_count += 1
        if elapsed > _pool_wait_max_ms:
            _pool_wait_max_ms = elapsed
    return True


def install_pool_wait_probe(pool=None) -> bool:
    """Time real "pool is full" waits by wrapping the pool's blocking queue get.

    ``QueuePool._do_get`` calls ``self._pool.get(wait, timeout)`` and ``wait`` is
    true *exactly* when the pool is exhausted (``checkedout == size +
    max_overflow``).  Wrapping that one call therefore measures queue waiting
    only — never connection creation, pre-ping or statement execution — and
    ``block=False`` calls (a free connection was available) are timed not at all.

    The wrapper is installed on the pool's queue *instance*, so no event loop is
    bound early and no library class is patched globally. Returns ``True`` when
    this call installed the probe.

    ``pool`` is injectable for tests; production passes nothing and the probe is
    attached to the shared engine's pool.
    """
    global _pool_wait_probe_installed
    with _install_lock:
        if _pool_wait_probe_installed:
            return False
        try:
            target = oracle_engine.get_engine().pool if pool is None else pool
        except Exception as exc:  # no engine configured (tests, tooling)
            _logger.debug("pool wait probe: engine unavailable (%s)", exc)
            return False

        queue = getattr(target, "_pool", None)
        original_get = getattr(queue, "get", None)
        if queue is None or not callable(original_get):
            _logger.debug("pool wait probe: pool %r has no queue", target)
            return False
        if getattr(queue, "_zvms_wait_probe", False):
            _pool_wait_probe_installed = True
            return False

        def timed_get(block: bool = True, timeout=None):
            if not block:
                return original_get(block, timeout)
            started = time.perf_counter()
            try:
                return original_get(block, timeout)
            finally:
                record_pool_wait((time.perf_counter() - started) * 1000)

        try:
            queue.get = timed_get  # instance attribute shadows the bound method
            queue._zvms_wait_probe = True
        except (AttributeError, TypeError) as exc:  # pragma: no cover - exotic pool
            _logger.debug("pool wait probe: cannot wrap %r (%s)", queue, exc)
            return False

        _pool_wait_probe_installed = True
        return True
