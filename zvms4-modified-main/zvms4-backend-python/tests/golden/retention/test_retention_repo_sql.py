"""Offline tests for the retention purge statement builders (design §2).

The two predicates pinned here are the *only* guard on irreversible deletion, so
they are asserted as the compiler emits them (operator + values), never as a
hand-written SQL substring:

* ``app_logs`` — ``TS_AT`` before the cutoff, minus the protected log types and
  minus warning-level rows. The ``IS NULL OR`` branches are load-bearing:
  Oracle treats ``''`` as NULL and :class:`typings.log.ZVMSLog` defaults
  ``type``/``level`` to ``''``, so a bare ``NOT (ltype IN ...)`` evaluates to
  NULL and would keep every ordinary row forever (oracle review M1).
* ``export_tasks`` — ``status IN ('completed','failed')`` and
  ``COALESCE(task_end, task_start)`` before the cutoff. The coalesce covers the
  shutdown-failed rows ``_fail_pending_stmt`` used to leave without
  ``task_end`` (oracle review M2); that statement is pinned below too, now that
  it stamps ``task_end``.

Nothing is executed: statements are compiled against the Oracle dialect with
values inlined, no engine, no connection. ``NOT IN`` is distinguished from
``IN`` explicitly — a pattern that only looked for ``IN`` would read the
protected-set exclusion as a whitelist.

Run from the repository root::

    ./.venv/bin/python -m pytest tests/golden/retention/ -q
"""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy.dialects import oracle

from db.repos.oracle import app_logs as app_logs_repo
from db.repos.oracle import tasks as tasks_repo

CUTOFF = datetime(2026, 8, 1, 3, 30, 0)
BATCH_SIZE = 5000

# Spelled out on purpose: the test must fail when the implementation's tuple is
# edited, not follow it.
PROTECTED_TYPES = (
    "security_alert",
    "ban_change",
    "passwd_change",
    "binding_mismatch",
    "device_bind",
    "device_unbind",
    "temp_password_create",
    "temp_password_login",
    "temp_password_fail",
    "device_mismatch",
    "device_ban",
    "ban_rule_skipped",
    "device_ban_skipped",
    "system_maintenance_enable",
    "system_maintenance_disable",
)
FINISHED_STATUSES = ("completed", "failed")

# ``app_logs.<column> <op> <value>`` as the Oracle dialect compiles it.
_COLUMN_PREDICATE = re.compile(
    r"app_logs\.(ltype|log_level)\s*(NOT\s+IN|IN|!=|=|<>)\s*(\([^()]*\)|'[^']*')",
    re.IGNORECASE,
)
_STATUS_PREDICATE = re.compile(
    r"export_tasks\.status\s*(NOT\s+IN|IN|!=|=|<>)\s*(\([^()]*\)|'[^']*')",
    re.IGNORECASE,
)


def _sql(stmt) -> str:
    """Compile a statement to Oracle SQL text with values inlined (no DB)."""
    return str(
        stmt.compile(dialect=oracle.dialect(), compile_kwargs={"literal_binds": True})
    )


def _values(raw: str) -> frozenset[str]:
    return frozenset(
        item.strip().strip("'") for item in raw.strip("()").split(",") if item.strip()
    )


def _column_predicates(sql: str, column: str) -> list[tuple[str, object]]:
    """Every predicate the WHERE places on one ``app_logs`` column."""
    found: list[tuple[str, object]] = []
    for name, op, raw in _COLUMN_PREDICATE.findall(sql):
        if name.lower() != column:
            continue
        op = re.sub(r"\s+", " ", op.upper())
        found.append((op, _values(raw) if op in ("IN", "NOT IN") else raw.strip("'")))
    return found


def _status_predicates(sql: str) -> list[tuple[str, object]]:
    """Every predicate the WHERE places on ``export_tasks.status``."""
    found: list[tuple[str, object]] = []
    for op, raw in _STATUS_PREDICATE.findall(sql):
        op = re.sub(r"\s+", " ", op.upper())
        found.append((op, _values(raw) if op in ("IN", "NOT IN") else raw.strip("'")))
    return found


def _logs_delete_sql(batch_size: int = BATCH_SIZE) -> str:
    return _sql(app_logs_repo._purge_batch_stmt(CUTOFF, batch_size))


def _logs_count_sql() -> str:
    return _sql(app_logs_repo._count_expired_stmt(CUTOFF))


def _tasks_delete_sql(batch_size: int = BATCH_SIZE) -> str:
    return _sql(tasks_repo._purge_finished_batch_stmt(CUTOFF, batch_size))


def _tasks_count_sql() -> str:
    return _sql(tasks_repo._count_expired_finished_stmt(CUTOFF))


# ---------------------------------------------------------------------------
# (1) app_logs: the frozen predicate, the NULL branches, the protected set
# ---------------------------------------------------------------------------


def test_app_logs_delete_is_the_frozen_predicate() -> None:
    """``DELETE FROM app_logs`` filtered on the indexed ``TS_AT`` and capped."""
    sql = _logs_delete_sql()

    assert sql.startswith("DELETE FROM app_logs")
    assert "app_logs.ts_at < " in sql
    assert "ROWNUM <= 5000" in sql
    # The batch size is a bind, not a string splice: it must be the only place
    # the number appears.
    assert sql.count("5000") == 1


def test_app_logs_null_branches_are_explicit() -> None:
    """``''`` is NULL in Oracle: both columns need an explicit ``IS NULL OR``."""
    sql = _logs_delete_sql()

    assert "(app_logs.ltype IS NULL OR (" in sql
    assert "(app_logs.log_level IS NULL OR " in sql
    # Third branch: a NULL ``TS_AT`` row is excluded by the comparison itself
    # (never true), not by a branch — it must not be resurrected by one.
    assert "app_logs.ts_at IS NULL" not in sql


def test_app_logs_protected_types_are_excluded_by_not_in() -> None:
    """The exclusion is a ``NOT IN`` of exactly the eight protected types."""
    predicates = _column_predicates(_logs_delete_sql(), "ltype")

    assert len(predicates) == 1, predicates
    op, values = predicates[0]
    assert op == "NOT IN", f"protected types must be excluded, got {op}"
    assert values == frozenset(PROTECTED_TYPES), values
    assert len(PROTECTED_TYPES) == 15


def test_app_logs_warning_level_is_protected() -> None:
    """Warning-level rows survive whatever their type."""
    predicates = _column_predicates(_logs_delete_sql(), "log_level")

    assert predicates == [("!=", "warning")], predicates


def test_app_logs_never_uses_ts_raw_or_a_whitelist() -> None:
    """Only ``TS_AT`` is filtered, and nothing re-admits a protected row."""
    sql = _logs_delete_sql()

    assert "ts_raw" not in sql, "TS_RAW has no index; TS_AT is the purge column"
    assert "ltype IN (" not in sql
    assert "log_level IN (" not in sql
    # A row exactly at the cutoff is not deleted: ``<``, never ``<=``.
    assert "app_logs.ts_at <=" not in sql


def test_app_logs_delete_uses_rownum_not_fetch_first() -> None:
    """``FETCH FIRST`` is not valid on a DELETE; the cap is ``ROWNUM``."""
    sql = _logs_delete_sql()

    assert "ROWNUM <= " in sql.upper()
    assert "FETCH FIRST" not in sql.upper()


def test_app_logs_count_shares_the_predicate_but_is_not_capped() -> None:
    """The preview counts everything the next run could delete (no ``ROWNUM``)."""
    sql = _logs_count_sql()
    delete_sql = _logs_delete_sql()

    assert sql.startswith("SELECT count(*)")
    assert "ROWNUM" not in sql.upper()
    for fragment in (
        "app_logs.ts_at < ",
        "(app_logs.ltype IS NULL OR (",
        "(app_logs.log_level IS NULL OR ",
    ):
        assert fragment in sql
        assert fragment in delete_sql


def test_app_logs_batch_size_is_the_only_bound_parameter() -> None:
    """A different batch size changes the cap and nothing else."""
    small = _logs_delete_sql(17)

    assert "ROWNUM <= 17" in small
    assert "ROWNUM <= 5000" not in small
    assert _column_predicates(small, "ltype") == [
        ("NOT IN", frozenset(PROTECTED_TYPES))
    ]


# ---------------------------------------------------------------------------
# (2) export_tasks: the frozen predicate and the coalesce
# ---------------------------------------------------------------------------


def test_export_tasks_delete_is_the_frozen_predicate() -> None:
    """``DELETE FROM export_tasks`` over finished rows only, capped by ``ROWNUM``."""
    sql = _tasks_delete_sql()

    assert sql.startswith("DELETE FROM export_tasks")
    assert "ROWNUM <= 5000" in sql
    assert sql.count("5000") == 1


def test_export_tasks_status_filter_is_a_whitelist_of_finished_statuses() -> None:
    """Explicitly ``IN ('completed','failed')``: the DB has no CHECK to lean on."""
    predicates = _status_predicates(_tasks_delete_sql())

    assert predicates == [("IN", frozenset(FINISHED_STATUSES))], predicates
    assert "export_tasks.status NOT IN" not in _tasks_delete_sql()


def test_export_tasks_age_uses_coalesce_of_end_then_start() -> None:
    """``COALESCE(task_end, task_start)`` — the only age shutdown-failed rows have."""
    sql = _tasks_delete_sql()

    assert (
        "coalesce(export_tasks.task_end, export_tasks.task_start) < "
        in sql.lower()
    )
    # A row exactly at the cutoff survives, and the coalesce is not reversed:
    # ``task_start`` alone would under-age rows that finished later.
    assert "coalesce(export_tasks.task_start, export_tasks.task_end)" not in sql.lower()
    assert "export_tasks.task_end < " not in sql


def test_export_tasks_delete_uses_rownum_not_fetch_first() -> None:
    sql = _tasks_delete_sql()

    assert "ROWNUM <= " in sql.upper()
    assert "FETCH FIRST" not in sql.upper()


def test_export_tasks_count_shares_the_predicate_but_is_not_capped() -> None:
    sql = _tasks_count_sql()
    delete_sql = _tasks_delete_sql()

    assert sql.startswith("SELECT count(*)")
    assert "ROWNUM" not in sql.upper()
    for fragment in (
        "export_tasks.status IN (",
        "coalesce(export_tasks.task_end, export_tasks.task_start) < ",
    ):
        assert fragment in sql, fragment
        assert fragment in delete_sql, fragment


# ---------------------------------------------------------------------------
# (3) the root-cause fix: fail_pending stamps task_end
# ---------------------------------------------------------------------------


def test_fail_pending_stamps_task_end() -> None:
    """Shutdown-failed rows must carry an age (oracle review M2)."""
    sql = _sql(tasks_repo._fail_pending_stmt("Program interrupted unexpectedly"))

    assert sql.startswith("UPDATE export_tasks SET status='failed'")
    assert "task_end=" in sql
    assert "task_end=NULL" not in sql.upper()
    assert "errmsg='Program interrupted unexpectedly'" in sql


def test_fail_pending_still_targets_every_unfinished_task() -> None:
    """The status filter is unchanged: only ``completed`` rows are spared."""
    sql = _sql(tasks_repo._fail_pending_stmt("boom"))

    assert "export_tasks.status != 'completed'" in sql
    assert "percentage" not in sql
