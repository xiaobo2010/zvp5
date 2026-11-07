# `tests/golden/statistics` — statistics rework verification harness

Canonical harness for the volunteer-time statistics rework: the nightly job moved
from *"no-WHERE full-table `DELETE` + per-user Python `DELETE`/`INSERT`"* to a
**single-transaction, set-based rebuild** (`tasks/compute_time.py::rebuild_statistics`)
that recomputes six derived tables — `user_time`, `time_academic_year`,
`time_with_origin`, `time_with_origin_stat`, `indicators`, `group_indicators` —
with SQL doing the aggregation and Python only assembling rows.

The harness answers one question: **is the new SQL equivalent to the old code?**

It does that by **differential testing** — it never re-implements the maths a
third time. The reference side reuses the legacy functions that still live in the
tree (`util/calculate.py`, `tasks/compute_time.py` helpers), the new side calls
the real `rebuild_statistics`, and `compare.py` diffs the two dumps.

---

## Two tiers

### Tier 1 — offline, no database (`test_tier1.py`)

Pure unit tests of the five Python helpers the rebuild uses to assemble rows in
Python (`_adjusted_frame`, `_grade_rollups`, `_group_rollup_rows`,
`_origin_user_rows`, `_origin_stat_rows`), checked against hand-computed values
for a 5-user fixture (3 grades / 2 classes + 1 class-less user). Covers the empty
inputs and the class-less / no-origin / all-filtered-out branches.

```bash
# from zvms4-backend-python/ (repo root of the backend)
python3 -m compileall -q tests/golden/statistics
.venv/bin/pytest tests/golden/statistics/test_tier1.py -q
# fallback if pytest is unavailable:
python3 -m unittest discover -s tests/golden/statistics -p 'test_tier1.py'
```

Tier 1 is safe anywhere: it imports `tasks.compute_time` (which itself does no
I/O at import time) and nothing else.

### Tier 2 — server-side, mutates the database (`run_tier2.py`)

**READ THIS FIRST.** Tier 2 creates and drops an Oracle *schema*
(`CREATE USER … / DROP USER … CASCADE`) and applies DDL + inserts rows into it.
It must be run **on the production server, explicitly, by the orchestrator** —
never in CI and never from a laptop. It refuses to start unless
`--yes-run-on-server` is passed and the `CI` env var is unset.

```bash
# run from zvms4-backend-python/ on the server, as/with an ADMIN credential:
ZVMS_ADMIN_USER=ADMIN ZVMS_ADMIN_PASSWORD=... \
  .venv/bin/python tests/golden/statistics/run_tier2.py --yes-run-on-server

# capture a fresh read-only slice first (or it auto-captures when the fixture is missing):
.venv/bin/python tests/golden/statistics/run_tier2.py --yes-run-on-server --capture
```

Useful flags: `--all-ddl` (also apply `V002..V009`, not just `V001`),
`--force-drop` (drop a pre-existing scratch user), `--keep-scratch` (debug),
`--fixture PATH`.

Exit code is `0` only when every gate passes; any unexpected difference, a
non-idempotent rebuild, or a non-atomic failure makes it non-zero.

---

## What each gate proves

| Gate | Sequence | Proves |
| --- | --- | --- |
| **A — differential** | load fixture → legacy `reference.py` → `dump_A`; reset fixture → `rebuild_statistics()` → `dump_B`; `compare(A, B)` | the new set-based SQL produces the *same* derived rows as the old per-user code |
| **B — idempotency** | `rebuild_statistics()` → `dump_B1`; `rebuild_statistics()` → `dump_B2`; `compare(B1, B2)` | running the rebuild twice on unchanged inputs is a no-op (deterministic `DELETE`+`INSERT`, no drift) |
| **C — failure atomicity** | start `rebuild_statistics()` in a child process, `SIGKILL` it after several tables have been written but **before** the single `commit`, dump, compare to the last committed state | the single-transaction design rolls the whole unit of work back — no partial/derived-table corruption survives a crash |

Gate C works by monkeypatching `OracleStatsCacheRepo.rebuild_group_indicators` in
the child to print `STEP:group_indicators` (the 4th table) and then sleep; the
parent waits for that marker and `SIGKILL`s the process. Because nothing was
committed, Oracle discards the transaction and the dump must equal Gate B's
`dump_B2`.

---

## Scratch-schema lifecycle

`run_tier2.py` owns exactly one ephemeral schema per run:

1. **detect** — `SELECT … FROM all_users WHERE username = :u`; abort if the
   (randomly named) scratch user already exists, unless `--force-drop`.
2. **create** — `CREATE USER ZVMS_GS_<8 hex> IDENTIFIED BY "<random>" QUOTA
   UNLIMITED ON DATA`, then `GRANT CREATE SESSION / CREATE TABLE / …`
   (individual grants that the ADB admin cannot issue are warned, not fatal).
3. **connect** — a second engine on the *same* wallet/DSN, with the scratch
   user/password; `db.oracle.session._sessionmaker` is patched to it so
   `rebuild_statistics`, the legacy helpers and the repos all target scratch.
4. **migrate** — `V001__initial_schema.sql` is applied by a local applier that
   splits the `BEGIN … EXECUTE IMMEDIATE …; END; /` blocks on `/` lines and
   strips comments. `V001` is the consolidated fresh schema, so `V002..V008` are
   no-ops on it; `--all-ddl` applies `V009` too (the remap-gated
   `group_indicators → groups` FK — harmless on a freshly-loaded scratch schema
   because `group_indicators` is empty at constraint-validation time).
5. **run** the three gates.
6. **cleanup (always, in `finally`)** — restore `_sessionmaker`, dispose the
   scratch engine, `DROP USER <scratch> CASCADE` (only if this run created it).
   Cleanup runs on failure too.

The admin credential: by default the configured `ORACLE_USER`; override with
`ZVMS_ADMIN_USER` / `ZVMS_ADMIN_PASSWORD` when the app schema is not the ADB
admin. Creating a user requires the admin role — otherwise you get ORA-01031.

### Read-only slice capture

`fixture.py::capture()` reads the configured schema (the real one, *before* the
sessionmaker patch) and samples ~3 grades × up to 4 classes × 60–120 users plus
all of their `activity_members`, the referenced activities and a few unreferenced
effective ones. It is **read-only** and desensitises:

* `users.name` is never read into the fixture;
* `business_id` → `"<first 4 chars><6 random digits>"` (the grade prefix the SQL
  keys on survives, the real 学号 does not); uniqueness is preserved;
* ObjectIds are kept verbatim (24-hex, no PII).

It warns (does not fail) when the source schema cannot supply a boundary case:
zero-time users, `class_oid IS NULL` users, activities with no members, or ≥2
distinct origins.

---

## Frozen formats

### Fixture (`fixtures/fixture.json`, git-ignored)

```json
{
  "users":            [{"oid", "business_id", "class_oid"(nullable)}],
  "groups":           [{"oid", "name"}],
  "activities":       [{"oid", "status", "activity_date"(ISO), "origin"}],
  "activity_members": [{"activity_oid", "member_oid", "member_status",
                        "member_mode", "duration_hours"}]
}
```

`load(session, fixture)` inserts into **whatever schema the session is connected
to** and synthesises the NOT-NULL columns the statistics domain never reads
(`users.name`, `activities.atype`/`name`/`creator_oid`/`appointee_oid`/timestamps,
`activity_members.oid`). It commits once.

### Dump (`dump*.json`, git-ignored)

`dump.py` reads the six tables with the `db.models` Core metadata, drops
`oid`/`updated_at`, and sorts every list by **business key** (never `oid`), so two
dumps of the same logical state are byte-identical:

```json
{
  "user_time":            [{"user_oid","on_campus_raw","off_campus_raw","social_practice"}],
  "time_academic_year":   [{"user_oid","entry_year","academic_year","on_campus","off_campus","social_practice"}],
  "time_with_origin":     [{"user_oid","data"}],
  "time_with_origin_stat":[{"stat_key","scope","scope_id","require_percentage","data"}],
  "indicators":           {"percentiles","indicators"},
  "group_indicators":     [{"group_oid","percentiles","indicators"}]
}
```

`data`/`percentiles`/`indicators` are JSON columns; the `JSONText` TypeDecorator
returns them as Python `dict`s. If `indicators` has no row the dump emits
`{"percentiles": null, "indicators": null}`.

### Compare rules (`compare.py`)

* floats compare with `math.isclose(rel_tol=1e-9)` — Oracle `SUM` reorders
  additions versus pandas' serial sum, so a bit-exact float comparison would be a
  false alarm;
* int/float mixes are numeric; `bool`/`str` compare exactly (numeric-looking
  strings are never coerced);
* dict key sets compare **exactly** — percentile maps (`round(x, 1)` value labels
  → percentile labels) must have identical key sets and values;
* list tables are aligned by business key first, then compared field by field;
* every difference is a structured record `{table, path, kind, left, right}` with
  `kind ∈ {only_left, only_right, value}`.

---

## Registered intentional differences (reference vs. the *old deployed* path)

The reference runs the legacy code **as it stands today**. Where that differs from
what production ran before the rework — or where the new path deliberately extends
the old one — the difference is registered here:

| Id | Where | Change | Effect on the differential |
| --- | --- | --- | --- |
| **F1** | `util/calculate.py::calculate_user_time_with_origin` | now filters `member_status == "effective"` **and** the activity's own `status == "effective"` (the original counted every membership status and every activity), keeping the origin breakdown consistent with the `user_time` recompute | reference and rebuild agree on `time_with_origin`; the *old deployed* path would have counted pending/refused members and pending activities |
| **F2** | `util/calculate.py::time_with_origin` | collects into `kept` instead of `users.remove(...)` while iterating | reference no longer skips elements; old deployed path could |
| **F4** | `reference.py::_build` (step 2) | `entry = business_id[:4]` instead of the legacy `oid[:4]` (ObjectId's first 4 hex chars — meaningless) | makes the reference's `entry_year` match the new SQL's grade prefix |
| **group-indicators once** | `reference.py` (step 3) | legacy `compute_tasks` ran `compute_group_indicators` **twice** (directly and via `compute_indicators`); the reference runs it once | no double-write |
| **academic-year superset** | `tasks/compute_time.py` / `reference.py` | the rebuild writes the **entry** and the capped **graduation** 学年; the legacy loop wrote only the current 学年 | Gate A tolerates `only_right` rows for `time_academic_year`; every overlapping row and any `only_left`/value diff is still fatal |
| **stat empty scope×rq superset** | `tasks/compute_time.py` / `reference.py` | the rebuild materialises an *empty* `time_with_origin_stat` row (both `by_origin` and `percentage_by_origin` empty) for a scope×require-percentage with no qualifying users; the legacy `time_with_origin` loop skips the upsert for an empty scope | Gate A tolerates `only_right` rows for `time_with_origin_stat` **only** when the extra row is empty; any non-empty extra row, and every `only_left`/value diff, is still fatal |

The `academic-year superset` row is the one structural divergence Gate A cannot make
disappear: the rebuild's `time_academic_year` row set is a strict **superset** of the
legacy one. The `stat empty scope×rq superset` row is the same class of superset one
table over. `run_tier2.py::_partition_a` therefore treats *extra* academic-year rows
and *empty* extra `time_with_origin_stat` rows as reported known divergences and
keeps everything else strict (a legacy row missing from the rebuild, or a mismatched
value, still fails the gate).

> **Known gap — academic-year whitelist has no upper bound.** The `only_right`
> tolerance for `time_academic_year` accepts *any* number of extra rows. A
> submission-error rollback that left the `time_academic_year` write path partially
> committed once inflated this count and was only caught by inspecting the gate
> output by hand (see RC-2 in the fix report). Recommendation for a follow-up: add a
> threshold warning (e.g. fail when known `only_right` rows exceed a fixture-derived
> bound) so a real regression cannot hide inside an unbounded superset. **Not
> implemented here — note only.**

---

## Risks / notes for the orchestrator

* **Tier 2 is a DB mutation.** Do not wire it into CI. `run_tier2.py` guards on
  `--yes-run-on-server` + an unset `CI`, but that is a seatbelt, not a lock.
* **Admin credential required.** `CREATE USER`/`DROP USER` must use the ADB
  admin, not the application schema user.
* **Gate A academic-year whitelist.** The whitelist is by design (see the table
  above) but it does mean Gate A cannot verify the *new* academic-year rows
  against the legacy path. The new semantics (`{entry, min(entry+2, cur_ay)}`) are
  covered by construction and by Gate B, not by the differential.
* **`--keep-scratch` leaves a schema behind.** Only for debugging; remember to
  `DROP USER … CASCADE` manually.
* Captured fixtures and dumps contain real ObjectIds (and, pre-desensitisation,
  real 学号) — both are git-ignored, keep it that way.
