#!/usr/bin/env python3
"""Daily ADB backup: mirror the ADMIN schema from zvp5adb (primary) to zvp5adb2 (backup).

2026-10-14 rewrite. The previous version walked tables in alphabetical order and did
DELETE + INSERT per table with a commit after each. That is referentially unsafe in both
directions: ACTIVITIES is alphabetically before ACTIVITY_MEMBERS, so deleting the parent
while children existed raised ORA-02292 (the failure that broke the daily backup on
2026-10-14); the opposite order would raise ORA-02291 while re-inserting children whose
parents had not come back yet.

Now:
  1. DDL phase (Oracle DDL auto-commits, so it must happen before the single DML txn):
     create tables missing on the backup, recreate tables whose column set drifted.
  2. DML phase: ONE transaction -- delete in child->parent order, insert in parent->child
     order, single commit at the end.
  3. Best-effort parity of primary/unique/check constraints, plain indexes and FKs.
  4. Per-table row-count verification against the *same* read-only snapshot; SYNC_OK is only
     reported when every table matches. Without the snapshot, live tables (APP_LOGS,
     CHANGELOG) grow mid-run and the final counts legitimately disagree with what was
     loaded -- that is what made the first run of this script report SYNC_FAIL.

Runtime: expect 15-25 min. This ADB is ~150 ms away and LOB values cannot be batched into
prefetch, so every row of a CLOB/BLOB-bearing table costs about one round trip (measured:
USERS 410 s, TIME_WITH_ORIGIN 330 s, 856 s for a read-only pass over all 17 tables). The
obvious knobs were all measured and none help: oracledb 26 has no Connection.fetch_lobs,
and oracledb.defaults.fetch_lobs / cursor.prefetchrows / cursor.arraysize change nothing.

Set ADB_SYNC_DRY_RUN=1 to print the plan without touching the backup.
"""
import os, sys, time, datetime, fcntl, json
import oracledb

BASE = os.path.dirname(os.path.abspath(__file__))
ENV = lambda k, d: os.environ.get(k, d)
SCHEMA   = ENV("ADB_SCHEMA", "ADMIN")
SRC_DSN  = ENV("ADB_SRC_DSN", "zvp5adb_tp")
DST_DSN  = ENV("ADB_DST_DSN", "zvp5adb2_tp")
FETCH    = 5000
DRY_RUN  = ENV("ADB_SYNC_DRY_RUN", "") not in ("", "0", "false", "False")
LOCK     = os.path.join(BASE, "sync.lock")
LOG      = os.path.join(BASE, "sync.log")


def log(msg):
    print(f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}", flush=True)


def connect(wallet, pw_file, dsn):
    pw = open(os.path.join(BASE, pw_file)).read().strip()
    conn = oracledb.connect(user="ADMIN", password=pw, dsn=dsn,
                            config_dir=os.path.join(BASE, wallet),
                            wallet_location=os.path.join(BASE, wallet),
                            wallet_password=pw)
    return conn


def rotate():
    try:
        if os.path.exists(LOG) and os.path.getsize(LOG) > 5 * 1024 * 1024:
            os.replace(LOG, LOG + ".1")
    except OSError:
        pass


def fk_edges(cur):
    cur.execute("""select c.table_name, r.table_name
                   from user_constraints c
                   join user_constraints r on c.r_constraint_name = r.constraint_name
                   where c.constraint_type = 'R'""")
    return cur.fetchall()


def topo_order(tables, edges):
    """Parents before children. Falls back to alphabetical for any FK cycle."""
    parents = {t: set() for t in tables}
    for child, parent in edges:
        if child in parents and parent in parents and child != parent:
            parents[child].add(parent)
    order, done, pending = [], set(), set(tables)
    while pending:
        ready = sorted(t for t in pending if parents[t] <= done)
        if not ready:
            log("WARN FK cycle among %s; appending alphabetically" % ",".join(sorted(pending)))
            order += sorted(pending)
            break
        for t in ready:
            order.append(t)
            done.add(t)
            pending.discard(t)
    return order


def columns(cur, t):
    cur.execute("""select column_name from all_tab_columns
                   where owner = :1 and table_name = :2 order by column_id""", [SCHEMA, t])
    return [r[0] for r in cur.fetchall()]


def ddl(cur, otype, name):
    cur.execute("SELECT DBMS_METADATA.GET_DDL(:1, :2, :3) FROM DUAL", [otype, name, SCHEMA])
    d = cur.fetchone()[0]
    return d.read() if hasattr(d, "read") else d


def norm(v):
    """LOB -> bytes/str, JSON object -> text. Everything else binds as-is."""
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    if hasattr(v, "read"):
        return v.read()
    return v


def mirror_keys(sc, dc, t):
    """Best effort: copy standalone indexes onto a freshly (re)created table.

    PK/unique/check constraints already arrive inline in DBMS_METADATA.GET_DDL('TABLE', ...)
    (verified on GROUPS: CK_GROUPS_OID + PRIMARY KEY are part of the table DDL), so only
    non-constraint, non-LOB indexes need mirroring here. FKs are separate objects and are
    created after the data load by mirror_fks().
    """
    sc.execute("""select i.index_name from user_indexes i
                  where i.table_name = :1 and i.index_type != 'LOB'
                    and not exists (select 1 from user_constraints c
                                    where c.index_name = i.index_name
                                      and c.constraint_type in ('P','U'))""", [t])
    for (name,) in sc.fetchall():
        try:
            dc.execute(ddl(sc, "INDEX", name))
        except Exception as e:
            log(f"WARN mirror INDEX {name} on {t}: {str(e)[:110]}")


def check_map(cur, t):
    """Named CK_* check constraints of one table -> normalised predicate text."""
    cur.execute("""select constraint_name, search_condition_vc from user_constraints
                    where table_name = :1 and constraint_type = 'C'
                      and constraint_name like 'CK\\_%' escape '\\'""", [t])
    return {r[0]: " ".join((r[1] or "").split()) for r in cur.fetchall()}


def mirror_checks(sc, dc, tables, dry_run):
    """Align existing tables' named CHECK constraints with the source.

    Only missing tables used to get the source's inline constraints, so any enum widened on
    prod (AI_CALL_LOGS.status += 'warning', *_SOURCE += 'agent', ...) left the backup rejecting
    rows and the whole sync rolled back with ORA-02290. Comparing predicate text and
    recreating on drift keeps the backup loadable. Runs before the DML transaction (DDL
    auto-commits, so it must not sit between DELETE and INSERT).
    """
    for t in tables:
        try:
            s, d = check_map(sc, t), check_map(dc, t)
        except oracledb.DatabaseError as e:
            log(f"WARN CK parity inspect {t}: {str(e)[:110]}")
            continue
        for name, predicate in s.items():
            if d.get(name) == predicate:
                continue
            q = f'"{SCHEMA}"."{t}"'
            if name not in d:
                log(f"CK parity: creating {name} on backup ({t})")
                if dry_run:
                    continue
            else:
                log(f"CK parity: recreating {name} on backup ({t}) drifted")
                if dry_run:
                    continue
                try:
                    dc.execute(f"ALTER TABLE {q} DROP CONSTRAINT \"{name}\"")
                except oracledb.DatabaseError as e:
                    log(f"WARN CK parity drop {name}: {str(e)[:110]}")
                    continue
            try:
                dc.execute(ddl(sc, "CONSTRAINT", name).strip().rstrip(";"))
            except oracledb.DatabaseError as e:
                log(f"WARN CK parity create {name}: {str(e)[:110]}")
        for name in set(d) - set(s):
            log(f"CK parity: dropping extra {name} on backup ({t})")
            if dry_run:
                continue
            try:
                dc.execute(f'ALTER TABLE "{SCHEMA}"."{t}" DROP CONSTRAINT "{name}"')
            except oracledb.DatabaseError as e:
                log(f"WARN CK parity drop extra {name}: {str(e)[:110]}")


def fk_map(cur):
    cur.execute("select constraint_name, table_name from user_constraints where constraint_type = 'R'")
    return {r[0]: r[1] for r in cur.fetchall()}


def mirror_fks(sc, dc):
    """Align the backup's FK set with the source (after data load, so parents exist)."""
    sfk, dfk = fk_map(sc), fk_map(dc)
    for name in sorted(set(sfk) - set(dfk)):
        try:
            dc.execute(ddl(sc, "REF_CONSTRAINT", name))
            log(f"FK parity: created {name} on backup")
        except Exception as e:
            log(f"WARN FK parity create {name}: {str(e)[:110]}")
    for name in sorted(set(dfk) - set(sfk)):
        try:
            dc.execute(f'ALTER TABLE "{SCHEMA}"."{dfk[name]}" DROP CONSTRAINT "{name}"')
            log(f"FK parity: dropped extra {name} on backup")
        except Exception as e:
            log(f"WARN FK parity drop {name}: {str(e)[:110]}")


def main():
    rotate()
    t0 = time.time()
    src = connect("wallet1", "admin_pw1", SRC_DSN)
    dst = connect("wallet2", "admin_pw2", DST_DSN)
    log(f"OK connected src={SRC_DSN} dst={DST_DSN} schema={SCHEMA}{' DRY_RUN' if DRY_RUN else ''}")
    sc, dc = src.cursor(), dst.cursor()

    # Pin the whole run to one consistent snapshot. Otherwise every SELECT gets its own
    # read-committed view, so live tables (APP_LOGS, CHANGELOG) grow mid-run and the final
    # count comparison disagrees with what was actually loaded.
    sc.execute("SET TRANSACTION READ ONLY")
    try:
        sc.execute("select dbms_flashback.get_system_change_number from dual")
        log(f"snapshot SCN={sc.fetchone()[0]}")
    except oracledb.DatabaseError as e:
        log(f"WARN cannot read snapshot SCN: {str(e)[:80]}")

    sc.execute("SELECT table_name FROM all_tables WHERE owner = :1 ORDER BY table_name", [SCHEMA])
    tables = [r[0] for r in sc.fetchall()]
    log(f"source tables ({len(tables)}): {tables}")
    if not tables:
        log("WARN no tables in schema, nothing to sync")
        return

    ins_order = topo_order(tables, fk_edges(sc))
    del_order = list(reversed(ins_order))
    log(f"insert order (parents first): {ins_order}")

    dc.execute("SELECT table_name FROM all_tables WHERE owner = :1 ORDER BY table_name", [SCHEMA])
    dst_only = sorted(set(r[0] for r in dc.fetchall()) - set(tables))
    if dst_only:
        log(f"WARN tables only on backup (left untouched): {dst_only}")

    # ---- DDL phase -------------------------------------------------------
    created, recreated = [], []
    for t in tables:
        q = f'"{SCHEMA}"."{t}"'
        try:
            dc.execute(f"SELECT COUNT(*) FROM {q}")
            exists = True
        except oracledb.DatabaseError:
            exists = False
        src_cols = columns(sc, t)
        if not exists:
            body = ddl(sc, "TABLE", t)
            log(f"{q}: creating on backup (missing, {len(src_cols)} cols)")
            if not DRY_RUN:
                dc.execute(body)
                mirror_keys(sc, dc, t)
            created.append(t)
        else:
            dst_cols = columns(dc, t)
            if dst_cols != src_cols:
                body = ddl(sc, "TABLE", t)          # fetch before dropping
                log(f"{q}: COLUMN DRIFT src={len(src_cols)} dst={len(dst_cols)} -> drop+recreate")
                if not DRY_RUN:
                    dc.execute(f"DROP TABLE {q} CASCADE CONSTRAINTS")
                    dc.execute(body)
                    mirror_keys(sc, dc, t)
                recreated.append(t)

    # Align CHECK constraints before loading: stale enums reject new values (ORA-02290)
    # and the single DML transaction would roll back the whole backup.
    mirror_checks(sc, dc, tables, DRY_RUN)

    # ---- DML phase: one transaction, FK-safe order -----------------------
    loaded = 0
    for t in del_order:
        if not DRY_RUN:
            dc.execute(f'DELETE FROM "{SCHEMA}"."{t}"')
    for t in ins_order:
        q = f'"{SCHEMA}"."{t}"'
        t1 = time.time()
        sc.execute(f"SELECT * FROM {q}")
        cols = [d[0] for d in sc.description]
        collist = ",".join(f'"{c}"' for c in cols)
        binds = ",".join(f":{i + 1}" for i in range(len(cols)))
        n = 0
        while True:
            rows = sc.fetchmany(FETCH)
            if not rows:
                break
            if not DRY_RUN:
                dc.executemany(f"INSERT INTO {q} ({collist}) VALUES ({binds})",
                               [[norm(v) for v in row] for row in rows])
            n += len(rows)
        loaded += n
        log(f"{q}: {n} rows {'planned' if DRY_RUN else 'loaded'} ({time.time() - t1:.1f}s)")

    if DRY_RUN:
        log(f"SYNC_DRY_RUN tables={len(tables)} rows={loaded} "
            f"create={created} recreate={recreated} elapsed={time.time() - t0:.1f}s")
        return

    dst.commit()
    log(f"COMMIT ok ({time.time() - t0:.1f}s)")

    # ---- constraint parity + verification --------------------------------
    mirror_fks(sc, dc)
    mism = []
    for t in tables:
        q = f'"{SCHEMA}"."{t}"'
        sc.execute(f"SELECT COUNT(*) FROM {q}")
        a = sc.fetchone()[0]
        dc.execute(f"SELECT COUNT(*) FROM {q}")
        b = dc.fetchone()[0]
        if a != b:
            mism.append((t, a, b))
    if mism:
        log("SYNC_FAIL verify mismatch: " + "; ".join(f"{t} src={a} dst={b}" for t, a, b in mism))
        sys.exit(1)
    log(f"SYNC_OK tables={len(tables)} rows={loaded} elapsed={time.time() - t0:.1f}s")
    src.rollback()          # ends the read-only snapshot
    src.close()
    dst.close()


if __name__ == "__main__":
    lf = open(LOCK, "w")
    try:
        fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("another sync instance running, exit")
        sys.exit(0)
    try:
        main()
        sys.exit(0)
    except Exception as e:
        log(f"SYNC_FAIL {type(e).__name__}: {e}")
        sys.exit(1)
