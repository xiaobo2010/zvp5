# db/oracle/ddl — Oracle ADB schema

Versioned, hand-written Oracle DDL for the `zvms_new` → Oracle ADB migration.

| File | Purpose |
| ---- | ------- |
| `V001__initial_schema.sql` | 16 tables + 9 indexes for all of `zvms_new`. Idempotent. |
| `V002__add_users_banned.sql` | Adds `USERS.BANNED NUMBER(1)` + `ck_users_banned` to an existing instance. Idempotent. |
| `V003__relax_activities_text_null.sql` | Relaxes `ACTIVITIES.DESCRIPTION/APPROVER/PLACE` to nullable (Mongo `''` → Oracle NULL). Idempotent. |
| `V004__retire_mongo_tables.sql` | Adds `APP_LOGS`/`EXPORT_TASKS`/`IMPORTS` and extends `GROUPS` with `TYPE`/`PERMISSIONS` on an existing instance. Idempotent. |
| `V005__add_groups_description.sql` | Adds `GROUPS.DESCRIPTION VARCHAR2(1000)` on an existing instance. Idempotent. |
| `V007__add_files.sql` | Adds `FILES`/`FILE_OCR`/`FILE_EVENTS` + 8 indexes (Files/attachments API) on an existing instance. Idempotent. |
| `V008__add_app_logs_xuehai_index.sql` | Adds `ix_app_logs_xuehai_id` (APP_LOGS device 学海 ID filter) on an existing instance. Idempotent. |
| `V009__statistics_rework.sql` | Adds the deferred `GROUP_INDICATORS.GROUP_OID` → `GROUPS.OID` FK (`fk_group_indicators_group_oid`). Apply **after** the first `rebuild_statistics` (else ORA-02298 on legacy ids). Idempotent. |
| `V010__add_user_device_bindings.sql` | Adds `USER_DEVICE_BINDINGS` + `uq_udb_user_xuehai` UNIQUE + `ix_udb_xuehai_id` (学海 ID 身份追溯 MVP, observe-only) on an existing instance. Idempotent. |
| `V012__add_dual_auth_uses.sql` | Adds `DUAL_AUTH_USES` + `uq_dual_approver_counter` (Stage 2 双人授权 TOTP 防重放) on an existing instance. Idempotent. |
| `V013__add_ban_rules.sql` | Adds the `USERS` ban metadata columns (`BAN_REASON`/`BAN_UNTIL`/`BAN_SOURCE`/`BAN_RULE`), `BAN_RULES` + `BAN_RECORDS` (+ 2 indexes), the built-in `multi_device_login` rule seed and `ix_app_logs_user_ts` on an existing instance. Idempotent. |
| `../models.py` (`db/models.py`) | SQLAlchemy 2.0 Core `MetaData` mirror of V001. |
| `../ddl_check.py` (`db/ddl_check.py`) | Offline drift check between the two. |

`USERS.BANNED` is the login gate (`banned = 1` → the user cannot log in;
absent/`0` → not banned). It is declared in `V001` for fresh instances and
added by `V002` for instances where `V001` was already applied.

`ACTIVITIES.DESCRIPTION`/`APPROVER`/`PLACE` are nullable: Oracle treats the
empty string as NULL, so V001's original `DEFAULT '' NOT NULL` was
unsatisfiable for the `''` values production Mongo stores there. Fresh
instances get the nullable form from `V001`; existing instances are relaxed by
`V003`.

`APP_LOGS`, `EXPORT_TASKS` and `IMPORTS` are the last three retired Mongo
collections (`zvms_new.app_logs` / `tasks` / `imports`), and `GROUPS` gains the
permission-group columns `TYPE` and `PERMISSIONS`. Fresh instances get them from
`V001`; existing instances are extended by `V004`.

`GROUPS.DESCRIPTION` backs the group-description endpoint (currently no group
carries one). Fresh instances get it from `V001`; existing instances are
extended by `V005`.

`FILES` / `FILE_OCR` / `FILE_EVENTS` back the Files/attachments API
(`docs/FILES_API_DESIGN.md` §3): attachment metadata plus normalised bytes in a
`BLOB` (continuing the `IMPORTS.CONTENT` precedent), per-file OCR results, and an
append-only audit trail. `FILES.STATUS` is the single source of truth for the
serve gate; `OWNER_OID` carries no foreign key (same relaxation as `V006`).
Fresh instances get the three tables from `V001`; existing instances are
extended by `V007`.

`USER_DEVICE_BINDINGS` backs the 学海 ID 身份追溯 MVP: which 学海 device id a
user logs in from. It is **observe-only** — no login is ever blocked; the rows
feed the admin binding endpoints (`GET`/`PUT`/`DELETE /api/v3/users/{id}/bindings`)
and the login-time `device_bind` / `binding_mismatch` audit logs. `USER_OID`
carries a real FK to `USERS(OID)` (the table starts empty, so no legacy id can
violate it). A revoked row is kept (`status='revoked'`); the
`(user_oid, xuehai_id)` pair is unique so a re-bind reactivates in place. Fresh
instances get the table from `V001`; existing instances are extended by `V010`.

Design conventions (JSON→CLOB+`IS JSON`, ObjectId→`VARCHAR2(24)` lowercase hex,
naive `TIMESTAMP`, deferred `GROUP_INDICATORS` FK) are documented in the header
of `V001__initial_schema.sql` and in `docs/ADB_SCHEMA_SPEC.md` §1–§4.

`BAN_RULES` / `BAN_RECORDS` back 规则封禁（同学号多设备登录 …）while
`USERS.BANNED` stays the **only** login gate: the four new `USERS` columns
(`BAN_REASON`/`BAN_UNTIL`/`BAN_SOURCE`/`BAN_RULE`) only answer "why, until when,
and who/which rule". `BAN_UNTIL IS NULL` with `BANNED = 1` means a permanent ban;
an expiry task unbans rule bans at their `BAN_UNTIL`. `BAN_RECORDS` is the
append-only ledger that drives escalating penalties (one row per ban event, the
row count for `(USER_OID, RULE_CODE)` picks the next step from the rule's
`PENALTIES`). `BAN_RULES.CONFIG`/`PENALTIES` are **bounded** JSON documents and
therefore `VARCHAR2(2000 CHAR)` + `IS JSON` rather than CLOB. The built-in
`multi_device_login` rule is seeded with the fixed oid
`0b0000000000000000000001` via an idempotent `MERGE ... WHEN NOT MATCHED THEN
INSERT`, so re-running never overwrites admin edits. `ix_app_logs_user_ts`
(`APP_USER, TS_RAW`) backs the rule's evidence query
`COUNT(DISTINCT XUEHAI_ID)` over a time window. Fresh instances get all of this
from `V001` (same seed); existing instances are extended by `V013`.

## Applying V001

Targets, in order (spec §4): **`zvp5adb2` first, verify, then `zvp5adb`.**

Run the whole file as the schema owner with SQLcl (`sql`) or SQLcl/sqlplus:

```bash
# SQLcl (recommended)
sql admin@zvp5adb2 @V001__initial_schema.sql

# or sqlplus
sqlplus admin/<password>@zvp5adb2 @V001__initial_schema.sql
```

Each statement is wrapped in a PL/SQL `EXECUTE IMMEDIATE` block, so run the file
in a client that executes `/` line terminators (SQLcl / sqlplus both do).

### Verify after applying

```sql
-- 12 tables
SELECT table_name FROM user_tables ORDER BY table_name;

-- constraints per table (PK / UQ / FK / CHECK)
SELECT table_name, constraint_type, constraint_name
FROM   user_constraints
WHERE  table_name IN (
       'USERS','GROUPS','ACTIVITIES','ACTIVITY_MEMBERS','USER_TIME',
       'TIME_ACADEMIC_YEAR','TIME_WITH_ORIGIN','TIME_WITH_ORIGIN_STAT',
       'GROUP_INDICATORS','INDICATORS','DAILY_EXPORTS','LOGS')
ORDER BY table_name, constraint_type;

-- 7 indexes
SELECT index_name, table_name, uniqueness
FROM   user_indexes
WHERE  table_name IN ('USERS','ACTIVITIES','ACTIVITY_MEMBERS','TIME_ACADEMIC_YEAR');

-- CLOB columns with IS JSON checks
SELECT table_name, column_name FROM user_tab_columns
WHERE  data_type = 'CLOB' ORDER BY table_name, column_name;
```

Then repeat on `zvp5adb`.

## Idempotency

Every object is created inside:

```sql
BEGIN
  EXECUTE IMMEDIATE 'CREATE ...';
EXCEPTION
  WHEN OTHERS THEN
    IF SQLCODE != -955 THEN RAISE; END IF;   -- ORA-00955: name already used
END;
/
```

`CREATE INDEX` also swallows **ORA-01408** (index already exists):

```sql
IF SQLCODE NOT IN (-955, -1408) THEN RAISE; END IF;
```

So re-running `V001__initial_schema.sql` against an instance that already has
some or all objects is safe and is a no-op for the ones that exist. Any other
error is re-raised.

## Application user quota (ORA-01950)

The DB user that owns these objects needs **tablespace quota**, otherwise the
first `CREATE TABLE` fails with `ORA-01950: no privileges on tablespace
'<TS>'`. Grant it before applying:

```sql
ALTER USER zvp5_app QUOTA UNLIMITED ON DATA;   -- or a specific size, e.g. 2G
```

(`DATA` is the ADB default tablespace for the user's schema; adjust if yours
differs.)

## Offline drift check

`db/ddl_check.py` compares `db/models.py` against V001 **without any DB
access**. Run it after editing either side:

```bash
python -m db.ddl_check
```

It compiles each `Table` with the Oracle dialect, parses both sides into a
canonical structure (case-folded, whitespace-collapsed, `VARCHAR2(n CHAR)` ≙
`VARCHAR2(n)`, order- and name-insensitive for constraints/indexes) and prints
a per-table `PASS`/`FAIL`. Exit code is non-zero on any drift. See the module
docstring for the full normalization rules.
