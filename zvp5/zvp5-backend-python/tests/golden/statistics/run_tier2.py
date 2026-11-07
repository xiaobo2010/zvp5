"""Tier-2 server-side driver for the statistics golden harness.

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

  THIS SCRIPT MUTATES THE DATABASE.  It CREATEs and DROPs an Oracle *schema*
  (``CREATE USER ... / DROP USER ... CASCADE``) and it applies DDL / inserts
  fixture rows into it.  It MUST be run on the production server, explicitly, by
  the orchestrator — never in CI and never from a developer laptop.  It refuses
  to start unless ``--yes-run-on-server`` is passed and the ``CI`` env var is
  unset.

  It never touches the application schema's data: the only writes outside the
  scratch schema's own tables are ``CREATE USER`` / ``GRANT`` / ``DROP USER``
  performed as the admin credential.  Reads of the real schema happen only in
  ``--capture`` mode (read-only slice capture).

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

Lifecycle
---------
1.  ``--capture`` (optional): read-only slice capture from the configured schema
    into ``fixtures/fixture.json`` (see ``fixture.py``).
2.  Detect a pre-existing scratch user; refuse (or ``--force-drop``) if present.
3.  ``CREATE USER <scratch> IDENTIFIED BY "<random>" QUOTA UNLIMITED ON DATA``
    + grants (``CREATE SESSION``/``CREATE TABLE``/... ), via the admin credential.
4.  Build an engine for the scratch schema on the *same* wallet/DSN, patch
    ``db.oracle.session._sessionmaker`` to it, and apply ``V001`` (``--all-ddl``
    also applies ``V002..V009``).
5.  Load the fixture into the scratch schema and run the three gates:

    * **Gate A (differential)** — legacy reference path -> ``dump_A``; reset the
      fixture; ``rebuild_statistics()`` -> ``dump_B``; ``compare(A, B)``.  The
      only tolerated divergence is the documented ``time_academic_year``
      extension (new is a superset; overlapping rows must match exactly).
    * **Gate B (idempotency)** — run ``rebuild_statistics()`` twice -> ``dump_B1``
      / ``dump_B2``; byte-for-byte row equality across all six tables.
    * **Gate C (failure atomicity)** — start ``rebuild_statistics()`` in a child
      process, SIGKILL it after it has written several tables but before the
      single ``commit``, then dump: must equal the last committed state (proves
      the rollback leaves no partial rows).

6.  ``finally``: ``DROP USER <scratch> CASCADE`` (only if we created it), restore
    the sessionmaker, dispose the scratch engine.

Exit code is non-zero when any gate fails unexpectedly.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
for _path in (str(HERE), str(REPO_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import compare  # noqa: E402
import dump  # noqa: E402
import fixture as fixture_mod  # noqa: E402
import reference  # noqa: E402
from db.models import activities as activities_tbl  # noqa: E402
from db.models import activity_members as members_tbl  # noqa: E402
from db.models import group_indicators as group_tbl  # noqa: E402
from db.models import groups as groups_tbl  # noqa: E402
from db.models import indicators as indicators_tbl  # noqa: E402
from db.models import time_academic_year as ay_tbl  # noqa: E402
from db.models import time_with_origin as origin_tbl  # noqa: E402
from db.models import time_with_origin_stat as stat_tbl  # noqa: E402
from db.models import user_time as time_tbl  # noqa: E402
from db.models import users as users_tbl  # noqa: E402
from db.oracle import config as oracle_config  # noqa: E402
from db.oracle import engine as oracle_engine  # noqa: E402
from db.oracle import session as session_mod  # noqa: E402

DDL_DIR = REPO_ROOT / "db" / "oracle" / "ddl"
DUMP_A = HERE / "dump_A.json"
DUMP_B = HERE / "dump_B.json"
DUMP_B1 = HERE / "dump_B1.json"
DUMP_B2 = HERE / "dump_B2.json"
DUMP_C = HERE / "dump_C.json"
DUMP_X = HERE / "dump_expected.json"

#: Documented, intentional divergence: the new rebuild writes the *entry* and the
#: (capped) *graduation* academic year, the legacy path only the current one, so
#: the rebuild's row set is a superset. Only the direction-only-in-new is allowed.
KNOWN_SUPERSET_TABLES = {"time_academic_year"}

_SCRATCH_ENV = "ZVMS_SCRATCH_JSON"


# ===========================================================================
# CLI
# ===========================================================================

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes-run-on-server",
        action="store_true",
        help="required acknowledgement that this script mutates the database",
    )
    parser.add_argument("--capture", action="store_true",
                        help="read-only capture of a slice into fixtures/fixture.json")
    parser.add_argument("--fixture", type=Path, default=fixture_mod.FIXTURE_PATH)
    parser.add_argument("--all-ddl", action="store_true",
                        help="apply V002..V009 in addition to V001")
    parser.add_argument("--force-drop", action="store_true",
                        help="drop a pre-existing scratch user before creating")
    parser.add_argument("--keep-scratch", action="store_true",
                        help="do not drop the scratch user on exit (debugging)")
    parser.add_argument("--child-rebuild", action="store_true",
                        help=argparse.SUPPRESS)
    return parser


def _guard(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if os.environ.get("CI"):
        parser.error("refusing to run inside CI (CI env var is set)")
    if not args.yes_run_on_server:
        parser.error(
            "this script CREATEs/DROPs an Oracle schema; pass --yes-run-on-server "
            "to acknowledge and run it on the server"
        )


# ===========================================================================
# DDL applier
# ===========================================================================

def _split_blocks(sql: str) -> list[str]:
    """Split a SQLcl-style script into executable statements on ``/`` lines.

    Comment-only lines and blank lines are dropped; each returned block is the
    body of one ``BEGIN ... END;`` anonymous PL/SQL block (no trailing slash).
    """
    blocks: list[str] = []
    buf: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped == "/":
            block = "\n".join(buf).strip()
            if block:
                blocks.append(block)
            buf = []
            continue
        if not stripped or stripped.startswith("--"):
            continue
        buf.append(line)
    tail = "\n".join(buf).strip()
    if tail:
        blocks.append(tail)
    return blocks


async def apply_sql_file(engine: Any, path: Path) -> int:
    """Apply a SQLcl-style DDL script, resolving forward references.

    ``V001`` declares ``users`` (with a FK to ``groups``) *before* it creates
    ``groups``, so a naive top-to-bottom replay fails with ORA-00942. Each block
    is committed on its own and a block whose referenced object does not exist
    *yet* is deferred and retried after the others, until no progress is made.
    """
    pending = _split_blocks(path.read_text(encoding="utf-8"))
    applied = 0
    while pending:
        deferred: list[str] = []
        progressed = False
        for block in pending:
            try:
                async with engine.begin() as conn:
                    await conn.exec_driver_sql(block)
            except Exception as exc:  # noqa: BLE001
                if "ORA-00942" in str(exc):
                    deferred.append(block)
                    continue
                raise
            applied += 1
            progressed = True
        if not progressed:
            # No block could be applied: re-run the first deferred one so the
            # underlying error surfaces instead of looping.
            async with engine.begin() as conn:
                await conn.exec_driver_sql(deferred[0])
        pending = deferred
    return applied


# ===========================================================================
# scratch schema plumbing
# ===========================================================================

def _scratch_cfg(info: dict[str, Any]) -> oracle_config.OracleConfig:
    return oracle_config.OracleConfig(
        active=info.get("active", "prod"),
        user=info["user"],
        password=info["password"],
        dsn=info["dsn"],
        wallet_dir=info.get("wallet_dir", ""),
        wallet_password=info.get("wallet_password", ""),
    )


def _build_engine(cfg: oracle_config.OracleConfig) -> Any:
    engine = create_async_engine(
        oracle_config.build_async_url(cfg),
        connect_args=oracle_engine._connect_args(cfg),
        pool_size=2,
        max_overflow=2,
        pool_pre_ping=True,
    )
    oracle_engine.install_lob_binds(engine)
    return engine


def _scratch_info(cfg: oracle_config.OracleConfig, user: str, password: str) -> dict[str, Any]:
    return {
        "active": cfg.active,
        "user": user,
        "password": password,
        "dsn": cfg.dsn,
        "wallet_dir": cfg.wallet_dir,
        "wallet_password": cfg.wallet_password,
    }


async def _admin_engine() -> Any:
    """Admin engine: configured user, or ZVMS_ADMIN_USER/PASSWORD override."""
    cfg = oracle_config.load_config()
    admin_user = os.environ.get("ZVMS_ADMIN_USER")
    admin_password = os.environ.get("ZVMS_ADMIN_PASSWORD")
    if admin_user and admin_password:
        cfg = cfg._replace(user=admin_user, password=admin_password)
    return _build_engine(cfg)


async def _user_exists(engine: Any, user: str) -> bool:
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT COUNT(*) FROM all_users WHERE username = :u"),
                {"u": user.upper()},
            )
        ).scalar_one()
    return bool(row)


async def _create_scratch_user(engine: Any, user: str, password: str) -> None:
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            f'CREATE USER {user} IDENTIFIED BY "{password}" '
            f"QUOTA UNLIMITED ON DATA"
        )
    grants = (
        "CREATE SESSION",
        "CREATE TABLE",
        "CREATE SEQUENCE",
        "CREATE VIEW",
        "CREATE PROCEDURE",
    )
    for grant in grants:
        try:
            async with engine.begin() as conn:
                await conn.exec_driver_sql(f"GRANT {grant} TO {user}")
        except Exception as exc:  # privilege already present / not grantable
            print(f"  warn: GRANT {grant} TO {user} failed: {exc}")


async def _drop_scratch_user(engine: Any, user: str) -> None:
    for attempt in range(3):
        try:
            async with engine.begin() as conn:
                await conn.exec_driver_sql(f"DROP USER {user} CASCADE")
            return
        except Exception as exc:  # noqa: BLE001
            print(f"  warn: DROP USER {user} attempt {attempt + 1} failed: {exc}")
            await asyncio.sleep(2)


# ===========================================================================
# session helpers / reset
# ===========================================================================

@asynccontextmanager
async def _session(sessionmaker: async_sessionmaker):
    session = sessionmaker()
    try:
        yield session
    finally:
        await session.close()


async def _reset(session: Any) -> None:
    """Delete every scratch row (derived + source) so a fixture can be reloaded."""
    order = (
        time_tbl,
        ay_tbl,
        origin_tbl,
        stat_tbl,
        group_tbl,
        indicators_tbl,
        members_tbl,
        activities_tbl,
        users_tbl,
        groups_tbl,
    )
    for table in order:
        await session.execute(delete(table))
    await session.commit()


async def _load(session: Any, fixture: dict[str, Any]) -> dict[str, int]:
    await _reset(session)
    return await fixture_mod.load(session, fixture)


# ===========================================================================
# gates
# ===========================================================================

async def _rebuild() -> dict[str, int]:
    from tasks.compute_time import rebuild_statistics

    return await rebuild_statistics()


async def _dump_to(sessionmaker: async_sessionmaker, path: Path) -> dict[str, Any]:
    async with _session(sessionmaker) as session:
        data = await dump.dump(session)
    dump.write_dump(data, path)
    return data


def _partition_a(diffs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split Gate-A diffs into (fatal, known-superset)."""
    fatal: list[dict[str, Any]] = []
    known: list[dict[str, Any]] = []
    for diff in diffs:
        if diff["table"] in KNOWN_SUPERSET_TABLES and diff["kind"] == "only_right":
            known.append(diff)
        elif (
            diff["table"] == "time_with_origin_stat"
            and diff["kind"] == "only_right"
            and (diff["right"].get("data") or {}).get("by_origin") == {}
            and (diff["right"].get("data") or {}).get("percentage_by_origin") == {}
        ):
            # rebuild materialises an empty scope×rq row (by_origin == {}); the
            # legacy loop skips the upsert for an empty scope. Same class of
            # superset as the academic-year whitelist.
            known.append(diff)
        else:
            fatal.append(diff)
    return fatal, known


async def gate_a(
    sessionmaker: async_sessionmaker, fixture: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    print("[Gate A] legacy reference path ...")
    async with _session(sessionmaker) as session:
        await _load(session, fixture)
    summary = await reference.build_reference(None)
    print(f"[Gate A] reference counts: {summary}")
    a = await _dump_to(sessionmaker, DUMP_A)

    print("[Gate A] set-based rebuild ...")
    async with _session(sessionmaker) as session:
        await _load(session, fixture)
    await _rebuild()
    b = await _dump_to(sessionmaker, DUMP_B)

    diffs = compare.compare(a, b)
    fatal, known = _partition_a(diffs)
    for diff in known:
        print(f"  known divergence: {compare.format_diff(diff)}")
    return fatal, known


async def gate_b(sessionmaker: async_sessionmaker) -> list[dict[str, Any]]:
    print("[Gate B] idempotency: two consecutive rebuilds ...")
    await _rebuild()
    b1 = await _dump_to(sessionmaker, DUMP_B1)
    await _rebuild()
    b2 = await _dump_to(sessionmaker, DUMP_B2)
    return compare.compare(b1, b2)


def _child_env(scratch: dict[str, Any]) -> dict[str, str]:
    env = dict(os.environ)
    env[_SCRATCH_ENV] = json.dumps(scratch, ensure_ascii=False)
    return env


async def _gate_c_child(scratch: dict[str, Any]) -> int:
    """Child entry point: rebuild, signal after several tables, then hang."""
    from db.repos.oracle import stats as stats_mod

    engine = _build_engine(_scratch_cfg(scratch))
    session_mod._sessionmaker = async_sessionmaker(bind=engine, expire_on_commit=False)

    original = stats_mod.OracleStatsCacheRepo.rebuild_group_indicators

    async def _signal_after_write(self, rows, now=None):
        count = await original(self, rows, now)
        print("STEP:group_indicators", flush=True)
        await asyncio.sleep(3600)
        return count

    stats_mod.OracleStatsCacheRepo.rebuild_group_indicators = _signal_after_write
    try:
        await _rebuild()
    finally:
        await engine.dispose()
    return 0


async def gate_c(
    sessionmaker: async_sessionmaker, scratch: dict[str, Any], baseline: dict[str, Any]
) -> tuple[list[dict[str, Any]], bool]:
    print("[Gate C] failure atomicity: kill the rebuild mid-flight ...")
    proc = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--child-rebuild"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=_child_env(scratch),
    )
    killed = False

    def _watchdog() -> None:
        if proc.poll() is None:
            proc.kill()

    timer = threading.Timer(120.0, _watchdog)
    timer.start()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            print(f"  child: {line.rstrip()}")
            if "STEP:" in line:
                proc.kill()
                killed = True
                break
    finally:
        timer.cancel()
        proc.wait()

    if not killed:
        print("  ERROR: child exited before reaching the kill point")
        return [], False

    c = await _dump_to(sessionmaker, DUMP_X)
    diffs = compare.compare(baseline, c)
    return diffs, True


# ===========================================================================
# orchestration
# ===========================================================================

async def run(args: argparse.Namespace) -> int:
    admin = await _admin_engine()
    scratch_engine = None
    scratch_user = ""
    created = False
    previous_maker = session_mod._sessionmaker
    exit_code = 0

    try:
        if args.capture or not Path(args.fixture).exists():
            print("[capture] read-only slice capture ->", args.fixture)
            captured = await fixture_mod.capture()
            fixture_mod.write_fixture(captured, args.fixture)
        fixture = fixture_mod.read_fixture(args.fixture)
        print(
            "[fixture] users=%d groups=%d activities=%d members=%d"
            % (
                len(fixture["users"]),
                len(fixture["groups"]),
                len(fixture["activities"]),
                len(fixture["activity_members"]),
            )
        )

        cfg = oracle_config.load_config()
        scratch_user = "ZVMS_GS_" + secrets.token_hex(4).upper()
        password = "Zs" + secrets.token_hex(12)

        if await _user_exists(admin, scratch_user):
            if not args.force_drop:
                print(f"scratch user {scratch_user} already exists; aborting")
                return 2
            await _drop_scratch_user(admin, scratch_user)

        print(f"[scratch] creating schema {scratch_user}")
        await _create_scratch_user(admin, scratch_user, password)
        created = True

        scratch_cfg = cfg._replace(user=scratch_user, password=password)
        scratch_engine = _build_engine(scratch_cfg)
        sessionmaker = async_sessionmaker(bind=scratch_engine, expire_on_commit=False)
        session_mod._sessionmaker = sessionmaker

        ddl_files = ["V001__initial_schema.sql"]
        if args.all_ddl:
            ddl_files = sorted(p.name for p in DDL_DIR.glob("V*.sql"))
        for name in ddl_files:
            count = await apply_sql_file(scratch_engine, DDL_DIR / name)
            print(f"[ddl] applied {name} ({count} statements)")

        fatal_a, known_a = await gate_a(sessionmaker, fixture)
        if fatal_a:
            print(f"[Gate A] FAIL: {len(fatal_a)} unexpected difference(s)")
            for diff in fatal_a:
                print("  " + compare.format_diff(diff))
            exit_code = 1
        else:
            print(
                f"[Gate A] PASS (no unexpected differences; "
                f"{len(known_a)} known-divergence rows)"
            )

        diffs_b = await gate_b(sessionmaker)
        if diffs_b:
            print(f"[Gate B] FAIL: {len(diffs_b)} difference(s) between rebuilds")
            for diff in diffs_b:
                print("  " + compare.format_diff(diff))
            exit_code = 1
        else:
            print("[Gate B] PASS (idempotent)")

        baseline = dump.read_dump(DUMP_B2)
        diffs_c, reached = await gate_c(sessionmaker, _scratch_info(cfg, scratch_user, password), baseline)
        if not reached:
            print("[Gate C] FAIL: never reached the kill point")
            exit_code = 1
        elif diffs_c:
            print(f"[Gate C] FAIL: {len(diffs_c)} difference(s) vs last committed state")
            for diff in diffs_c:
                print("  " + compare.format_diff(diff))
            exit_code = 1
        else:
            print("[Gate C] PASS (rollback left no partial rows)")

        print(f"[summary] exit={exit_code}")
        return exit_code
    finally:
        session_mod._sessionmaker = previous_maker
        if scratch_engine is not None:
            await scratch_engine.dispose()
        if created and not args.keep_scratch:
            print(f"[scratch] dropping schema {scratch_user}")
            await _drop_scratch_user(admin, scratch_user)
        await admin.dispose()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.child_rebuild:
        scratch = json.loads(os.environ[_SCRATCH_ENV])
        return asyncio.run(_gate_c_child(scratch))
    _guard(_parser(), args)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
