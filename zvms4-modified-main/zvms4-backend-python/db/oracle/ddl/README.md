# db/oracle/ddl — Oracle ADB schema

Versioned, hand-written Oracle DDL for the `zvms_new` → Oracle ADB migration.

| File | Purpose |
| ---- | ------- |
| `V001__initial_schema.sql` | 16 tables + 9 indexes for all of `zvms_new`. Idempotent. |
| `V002__add_users_banned.sql` | Adds `USERS.BANNED NUMBER(1)` + `ck_users_banned` to an existing instance. Idempotent. |
| `V003__relax_activities_text_null.sql` | Relaxes `ACTIVITIES.DESCRIPTION/APPROVER/PLACE` to nullable (Mongo `''` → Oracle NULL). Idempotent. |
| `V004__retire_mongo_tables.sql` | Adds `APP_LOGS`/`EXPORT_TASKS`/`IMPORTS` and extends `GROUPS` with `TYPE`/`PERMISSIONS` on an existing instance. Idempotent. |
| `V005__add_groups_description.sql` | Adds `GROUPS.DESCRIPTION VARCHAR2(1000)` on an existing instance. Idempotent. |
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

Design conventions (JSON→CLOB+`IS JSON`, ObjectId→`VARCHAR2(24)` lowercase hex,
naive `TIMESTAMP`, deferred `GROUP_INDICATORS` FK) are documented in the header
of `V001__initial_schema.sql` and in `docs/ADB_SCHEMA_SPEC.md` §1–§4.

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
