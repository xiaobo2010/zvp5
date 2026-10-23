# db.oracle

Runtime **Oracle Autonomous Database (ADB)** connection layer, built on
SQLAlchemy 2.0 async + `python-oracledb` (thin mode). All application repositories
are selected through `db/backends.py`. Schema definitions and versioned migration
scripts are in [`ddl/`](ddl/README.md); the application does not apply them at startup.

## Instances: prod vs test

Two logical instances. The DB **user/password may be shared**, but the
**DSN, wallet and wallet password are per-instance**:

| Instance | DSN key               | Wallet dir key           | Wallet password key           |
| -------- | --------------------- | ------------------------ | ----------------------------- |
| `prod`   | `ORACLE_DSN`          | `ORACLE_WALLET_DIR`      | `ORACLE_WALLET_PASSWORD`      |
| `test`   | `ORACLE_TEST_DSN`     | `ORACLE_TEST_WALLET_DIR` | `ORACLE_TEST_WALLET_PASSWORD` |

Selection is driven by `ORACLE_ACTIVE` (`"prod"` or `"test"`, default `"prod"`).
ADB is the **only** backend — there is no on/off master switch and no
non-ADB fallback: the Oracle engine, session and repositories construct
unconditionally.

## Thin-mode wallet

`python-oracledb` thin mode requires an **`ewallet.pem`**; it does **not**
support the SSO `cwallet.sso` file (thick mode only). Our downloaded wallet
ships an **encrypted** `ewallet.pem`, so `wallet_password` is **mandatory** —
without it the driver falls back to an interactive "Enter PEM pass phrase"
prompt and hangs.

`wallet_password` is the **OCI wallet password** that decrypts `ewallet.pem`.
It is **not** `ORACLE_PASSWORD`, which is the DB user password.

## Config keys to fill

Add these to `settings.py` (placeholders are in `settings.example.py`):
`ORACLE_ACTIVE`, `ORACLE_USER`, `ORACLE_PASSWORD`,
`ORACLE_DSN`, `ORACLE_WALLET_DIR`, `ORACLE_WALLET_PASSWORD`,
`ORACLE_TEST_DSN`, `ORACLE_TEST_WALLET_DIR`, `ORACLE_TEST_WALLET_PASSWORD`.

## Recorded decision: JSON columns

The stack is locked to **SQLAlchemy 2.0.x**, which has no Oracle JSON DDL
type. JSON data is modelled as **`CLOB` + an `IS JSON` check constraint**.
`db/oracle/clob.py` installs the shared large-value bind hook.

## How a caller obtains a session

```python
from db.oracle.session import get_session, session_scope

# FastAPI dependency
@app.get("/example")
async def example(session=Depends(get_session)):
    ...

# Plain async code
async with session_scope() as session:
    ...
```

`db.oracle.engine` exposes `get_engine()`, `create_sessionmaker()`,
`dispose()` and `ping()` (runs `SELECT 1 FROM DUAL`). The engine is created
lazily on first use — importing any module here never connects.

## Assumptions

- Async URL uses the documented `oracle+oracledb_async://USER:PASSWORD@DSN`
  form; user/password are URL-encoded.
- Wallet args `config_dir` / `wallet_location` are passed through
  `connect_args` for thin mode, only when a wallet dir is configured.
- `wallet_password` is forwarded only when non-empty; it is the OCI wallet
  password (decrypts `ewallet.pem`), not the DB user password.
- Pool configuration is 8 persistent connections plus up to 8 overflow
  connections per API process (`ZVMS_DB_POOL_SIZE` / `ZVMS_DB_POOL_OVERFLOW`).
  `pool_pre_ping` defaults **off** and `pool_recycle` defaults to 600s
  (`ZVMS_DB_PRE_PING` / `ZVMS_DB_RECYCLE`): on the cross-region link to ADB a
  pre-ping probe costs a full extra round trip on every checkout, so connection
  freshness is guaranteed by proactive aging instead. See `_pool_settings()` in
  `engine.py` for the trade-off and when to turn pre-ping back on.

## Read-only monitoring

[`GET /api/system/db/status`](../../docs/database-status.md) uses the same active
instance and engine. It does not need OCI SDK credentials. Schema storage comes
from `USER_*` views; optional tablespace and performance observations use
`DBA_TABLESPACE_USAGE_METRICS`, `DBA_TABLESPACES` and `V$SYSMETRIC`. Missing read
privileges degrade the affected component rather than reporting zero usage.
No grants or configuration changes are performed by the endpoint.
