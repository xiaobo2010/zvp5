"""Offline tests for the FILES statement builders' status filters.

Regression target (2026-10-17, reproduced from service logs + live rows): the
attachment ``6ad389487a1f1855f9526b1a`` was soft-deleted (``status='deleted'``)
and the very same photo was re-uploaded a few seconds later. The re-upload hit
:meth:`OracleFilesRepo.find_by_sha256` and got the **tombstone** row back, so the
API answered 200 with an oid whose bytes are gone: every subsequent
``GET /api/attachments/{oid}/thumb`` was a 410 and the UI showed "上传失败".
Separately, the activity's attachment list (its caller passes no ``status``)
included the soft-deleted row, so re-opening the dialog showed a ghost item.

Two gates are pinned here, both offline (SQL is *compiled*, never executed):

* the dedup lookup only ever returns a **servable** row
  (``status IN ('stored','ready','verified')`` — design
  ``docs/FILES_API_DESIGN.md`` §3.3 / §4 #2, the same gate
  ``routers/attachments_router.py`` applies before serving, and §8.6 for the
  "same owner only" dedup rule);
* the list page and its count share one WHERE builder (``_list_conds``) and both
  exclude **every dead status** (``deleted`` / ``rejected`` / ``quarantined`` —
  the ones the serve gate answers 410 for) by default, while keeping in-flight
  ones (``processing``) and honouring an explicit ``status`` — including
  ``status='deleted'`` — with exact matching.

Assertions read the predicates the compiler actually emitted for
``files.status`` (operator + values), never a hand-written SQL string, so a
changed status set or a dropped predicate fails rather than passing on a
substring coincidence. ``NOT IN`` and ``IN`` are distinguished explicitly: the
Oracle dialect renders the default listing filter as ``status NOT IN (...)``, and
a pattern that only looked for ``IN`` would read that as a whitelist.

Run from the repository root::

    ./.venv/bin/python -m pytest tests/golden/attachments/ -q
"""

from __future__ import annotations

import re

from sqlalchemy.dialects import oracle

from db.repos.oracle import files as files_repo

OWNER_OID = "68c3da8c46711c1f1f0475cd"
ENTITY_OID = "6aa48ec6ea44db1fda77c4d4"
SHA256 = "b" * 64

# ``files.status <op> <value>`` as the Oracle dialect compiles it. ``NOT IN``
# must be tried before ``IN`` (alternation is ordered), otherwise the default
# listing filter ``status NOT IN (...)`` would be misread as a whitelist.
_STATUS_PREDICATE = re.compile(
    r"files\.status\s*(NOT\s+IN|IN|!=|=)\s*(\([^()]*\)|'[^']*')", re.IGNORECASE
)


def _sql(stmt) -> str:
    """Compile a statement to Oracle SQL text with values inlined (no DB)."""
    return str(
        stmt.compile(dialect=oracle.dialect(), compile_kwargs={"literal_binds": True})
    )


def _status_predicates(sql: str) -> list[tuple[str, object]]:
    """Every predicate the WHERE places on ``files.status``.

    Returns ``[(op, value), ...]`` where ``op`` is ``"IN"`` / ``"NOT IN"``
    (value: frozenset of strings) or ``"!="`` / ``"="`` (value: the literal
    string).
    """
    found: list[tuple[str, object]] = []
    for op, raw in _STATUS_PREDICATE.findall(sql):
        op = re.sub(r"\s+", " ", op.upper())
        if op in ("IN", "NOT IN"):
            values = frozenset(
                item.strip().strip("'") for item in raw.strip("()").split(",") if item.strip()
            )
            found.append((op, values))
        else:
            found.append((op, raw.strip("'")))
    return found


def _dedup_sql() -> str:
    return _sql(files_repo._find_by_sha256_stmt(OWNER_OID, SHA256, "original"))


def _page_sql(status: str | None) -> str:
    return _sql(
        files_repo._list_page_stmt(None, None, status, "activity", ENTITY_OID, 1, 20)
    )


def _count_sql(status: str | None) -> str:
    return _sql(files_repo._count_stmt(None, None, status, "activity", ENTITY_OID))


# ---------------------------------------------------------------------------
# (1) dedup must not return an unservable (e.g. tombstoned) row
# ---------------------------------------------------------------------------


def test_dedup_lookup_is_restricted_to_servable_statuses() -> None:
    """The dedup lookup whitelists exactly the statuses the serve gate allows."""
    sql = _dedup_sql()
    predicates = _status_predicates(sql)

    assert len(predicates) == 1, f"expected one status predicate, got {predicates}"
    op, values = predicates[0]
    assert op == "IN", f"dedup must whitelist statuses, got a {op} predicate"
    assert values == frozenset({"stored", "ready", "verified"}), values
    assert "deleted" not in values
    assert "quarantined" not in values and "rejected" not in values


def test_dedup_gate_matches_the_router_serve_gate() -> None:
    """The repo constant and the serving gate cannot drift apart.

    ``routers.attachments_router.SERVE_STATUSES`` is what turns a
    non-servable row into a 410; if the two sets diverged, dedup could again
    return an oid that is guaranteed to 410.
    """
    from routers.attachments_router import SERVE_STATUSES

    assert frozenset(files_repo._SERVE_STATUSES) == SERVE_STATUSES


def test_dedup_keeps_owner_sha_kind_scope_and_single_row() -> None:
    """Same-owner dedup semantics (design §8.6) survive the status predicate."""
    sql = _dedup_sql()

    assert f"files.owner_oid = '{OWNER_OID}'" in sql
    assert f"files.sha256 = '{SHA256}'" in sql
    assert "files.kind = 'original'" in sql
    assert "files.content" not in sql, "dedup must never project the BLOB"
    assert "FETCH FIRST 1 ROWS ONLY" in sql
    assert not any(op == "=" for op, _ in _status_predicates(sql))


# ---------------------------------------------------------------------------
# (2) listing hides every dead status by default, page and count alike
# ---------------------------------------------------------------------------

# The schema CHECK (``db/models.py``: ``ck_files_status``) also allows
# ``processing``; it is in-flight, not dead, and stays visible by default.
IN_FLIGHT_STATUSES = frozenset({"processing"})


def _schema_statuses() -> frozenset[str]:
    """The status vocabulary of ``ck_files_status``, read from the model."""
    from db.models import files as files_tbl

    for constraint in files_tbl.constraints:
        if getattr(constraint, "name", "") != "ck_files_status":
            continue
        match = re.search(r"IN\s*\(([^()]*)\)", str(constraint.sqltext), re.IGNORECASE)
        assert match is not None, constraint.sqltext
        return frozenset(
            item.strip().strip("'") for item in match.group(1).split(",") if item.strip()
        )
    raise AssertionError("ck_files_status not found in db.models.files")


def test_default_list_and_count_exclude_every_dead_status() -> None:
    """With no explicit ``status`` neither query may return a dead-status row."""
    default = [("NOT IN", frozenset({"deleted", "rejected", "quarantined"}))]
    page_sql = _page_sql(None)
    count_sql = _count_sql(None)

    assert _status_predicates(page_sql) == default, page_sql
    assert _status_predicates(count_sql) == default, count_sql
    # Other filters are untouched and still applied on both sides.
    for sql in (page_sql, count_sql):
        assert "files.entity_type = 'activity'" in sql
        assert f"files.entity_oid = '{ENTITY_OID}'" in sql


def test_default_listing_keeps_in_flight_statuses() -> None:
    """``processing`` is in-flight, not dead: it must stay in the default view.

    A whitelist-shaped fix (``status IN _SERVE_STATUSES``) would hide it — and
    every future in-flight status — from the listing.
    """
    for sql in (_page_sql(None), _count_sql(None)):
        predicates = _status_predicates(sql)
        assert [op for op, _ in predicates] == ["NOT IN"], predicates
        excluded = predicates[0][1]
        assert "processing" not in excluded
        assert excluded == frozenset({"deleted", "rejected", "quarantined"})


def test_dead_statuses_do_not_drift_from_the_schema_and_serve_gate() -> None:
    """``_DEAD_STATUSES`` is exactly the schema minus serve statuses minus in-flight.

    Every status the schema allows must be classified deliberately: servable,
    dead, or the documented in-flight exception. Adding one to the CHECK without
    deciding its listing behaviour fails here.
    """
    dead = frozenset(files_repo._DEAD_STATUSES)
    serve = frozenset(files_repo._SERVE_STATUSES)
    schema = _schema_statuses()

    assert dead.isdisjoint(serve), dead & serve
    assert all(status not in serve for status in files_repo._DEAD_STATUSES)
    assert IN_FLIGHT_STATUSES <= schema
    assert serve | dead | IN_FLIGHT_STATUSES == schema, schema ^ (serve | dead | IN_FLIGHT_STATUSES)
    # No overlap and no duplicates inside the constant itself.
    assert len(set(files_repo._DEAD_STATUSES)) == len(files_repo._DEAD_STATUSES)


def test_default_list_and_count_share_the_same_status_filter() -> None:
    """The paged rows and the reported total must use the same WHERE."""
    assert _status_predicates(_page_sql(None)) == _status_predicates(_count_sql(None))


def test_explicit_status_still_matches_exactly() -> None:
    """Passing ``status`` restores equality — the audit views survive."""
    for status in (
        "stored",
        "ready",
        "verified",
        "processing",
        "deleted",
        "rejected",
        "quarantined",
    ):
        expected = [("=", status)]
        assert _status_predicates(_page_sql(status)) == expected, status
        assert _status_predicates(_count_sql(status)) == expected, status


def test_list_conds_is_the_single_source_for_page_and_count() -> None:
    """Both statements are built from the very same condition list."""
    default_conds = files_repo._list_conds(None, None, None, None, None)
    default_sql = [_sql(cond) for cond in default_conds]
    assert len(default_conds) == 1, "only the default status exclusion is expected"
    assert _status_predicates(default_sql[0]) == [
        ("NOT IN", frozenset({"deleted", "rejected", "quarantined"}))
    ], default_sql[0]

    page_sql = _page_sql(None)
    count_sql = _count_sql(None)
    for cond_sql in default_sql:
        assert cond_sql in page_sql, cond_sql
        assert cond_sql in count_sql, cond_sql

    rejected_conds = files_repo._list_conds(None, None, "rejected", None, None)
    assert len(rejected_conds) == 1
    assert "= 'rejected'" in _sql(rejected_conds[0])
    assert _sql(rejected_conds[0]) in _count_sql("rejected")

    deleted_conds = files_repo._list_conds(None, None, "deleted", None, None)
    assert len(deleted_conds) == 1
    assert "= 'deleted'" in _sql(deleted_conds[0])
    assert _sql(deleted_conds[0]) in _count_sql("deleted")
