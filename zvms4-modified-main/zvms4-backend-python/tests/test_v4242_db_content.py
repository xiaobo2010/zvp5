"""Stage 2 数据库内容管理端点（``/api/system/db/content``）的离线用例。

全部 hermetic：替身会话 ``FakeSession`` 用内存表驱动，**不建任何真实连接**（本机
没有 ADB 访问权限，任何连库尝试都会挂住）。替身按语句形态分发，并把每次
``execute`` 记进 ``calls``，所以「双人码/confirm 失败时一行 SQL 都没执行」可以
直接断言。

覆盖：

1. 标识符闸：注入形态、超长名、不存在表/列 → 400/404。
2. 只读闸：非 SELECT/WITH 开头、多语句、``WITH ... DELETE``、字面量里的关键字。
3. 分页：``limit+1`` 判 ``has_more``，**不**出现 ``COUNT(*)``；只走 ``fetchmany``。
4. 序列化：LOB → ``{"$lob",...}``、TIMESTAMP → ISO8601、NUMBER → number。
5. 行 CRUD：按主键定位；无主键表 400、key 缺列 400、values 列名不在表内 400。
6. DDL：自保护表 403、破坏性语句 confirm 校验、单语句、已提交语义。
7. 鉴权：管理员闸、双人码失败零 SQL、审计不含列值。
8. 真实驱动形状：用户 SQL 原样交给驱动（``text()`` 不解析冒号）；朴素
   DATE/TIMESTAMP 的 ISO8601 字符串在绑定前转成 ``datetime``（避免 ORA-01861）；
   TSTZ 拒绝写入、LTZ 只接受 naive 会话本地时间。

跑法：``.venv/bin/python -m pytest tests/test_v4242_db_content.py -q``
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import system_db_content_router as router_module
from services import db_content
from util.cert import get_current_totp
from util.dual_auth import DualAuthError
from util.object_id import get_current_user

PREFIX = "/api/system/db/content"
OPERATOR = "000000000000000000000001"
APPROVER = "000000000000000000000002"
CODE = "123456"


# ---------------------------------------------------------------------------
# 替身数据库：内存表 + 按语句形态分发
# ---------------------------------------------------------------------------


class FakeResult:
    """最小结果对象：``keys/first/fetchall/fetchmany/rowcount``。"""

    def __init__(self, rows=(), columns=(), rowcount=0):
        self._rows = [tuple(row) for row in rows]
        self._columns = list(columns)
        self.rowcount = rowcount
        self.fetchmany_calls: list[int] = []
        self.fetchall_calls = 0

    def keys(self):
        return list(self._columns)

    def first(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        self.fetchall_calls += 1
        return list(self._rows)

    def fetchmany(self, size):
        # 记录请求的行数：用例据此证明「只取 limit+1」，没有全量拉取。
        self.fetchmany_calls.append(size)
        return self._rows[:size]


class FakeConnection:
    """只实现 ``exec_driver_sql``：模拟 AsyncConnection 的驱动级执行入口。"""

    def __init__(self, session):
        self.session = session

    async def exec_driver_sql(self, sql, params=None):
        self.session.driver_calls.append(sql)
        return await self.session.execute(sql, params)


class FakeSession:
    """替身会话：不连库，按 SQL 文本分发到内存表。"""

    def __init__(self, db):
        self.db = db
        self.calls: list[tuple[str, dict]] = []
        # 用户 SQL（/query、/ddl）走驱动级执行：单独记录，证明没有经过 text()。
        self.driver_calls: list[str] = []
        self.commits = 0
        self.results: list[FakeResult] = []
        # /query 的结果由用例预置（真实 SQL 由用户提供，替身无从推断）。
        self.pending: list[FakeResult | Exception] = []

    @property
    def executed(self) -> list[str]:
        return [sql for sql, _ in self.calls]

    async def connection(self):
        return FakeConnection(self)

    async def execute(self, stmt, params=None):
        sql = re.sub(r"\s+", " ", str(stmt)).strip()
        bound = dict(params or {})
        self.calls.append((sql, bound))

        if self.pending:
            item = self.pending.pop(0)
            if isinstance(item, Exception):
                raise item
            self.results.append(item)
            return item

        result = self._dispatch(sql, bound)
        self.results.append(result)
        return result

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        return None

    async def close(self):
        return None

    # -- 分发 ----------------------------------------------------------
    def _dispatch(self, sql: str, params: dict) -> FakeResult:
        upper = sql.upper()
        if upper.startswith("SELECT TABLE_NAME, NUM_ROWS FROM USER_TABLES"):
            name = params["name"]
            table = self.db.tables.get(name)
            if table is None:
                return FakeResult(columns=["TABLE_NAME", "NUM_ROWS"])
            return FakeResult(
                [(name, table.get("num_rows"))], ["TABLE_NAME", "NUM_ROWS"]
            )

        if upper.startswith("SELECT VIEW_NAME FROM USER_VIEWS WHERE"):
            name = params["name"]
            if name not in self.db.views:
                return FakeResult(columns=["VIEW_NAME"])
            return FakeResult([(name,)], ["VIEW_NAME"])

        if upper.startswith("SELECT TABLE_NAME, NUM_ROWS, LAST_ANALYZED"):
            rows = [
                (name, meta.get("num_rows"), meta.get("last_analyzed"))
                for name, meta in sorted(self.db.tables.items())
            ]
            return FakeResult(
                rows, ["TABLE_NAME", "NUM_ROWS", "LAST_ANALYZED"]
            )

        if upper.startswith("SELECT VIEW_NAME FROM USER_VIEWS ORDER BY"):
            return FakeResult(
                [(name,) for name in sorted(self.db.views)], ["VIEW_NAME"]
            )

        if upper.startswith("SELECT COLUMN_NAME, DATA_TYPE"):
            meta = self.db.entry(params["name"])
            if meta is None:
                return FakeResult()
            rows = [
                (name, data_type, length, nullable, default)
                for name, data_type, length, nullable, default in meta["columns"]
            ]
            return FakeResult(rows, ["COLUMN_NAME", "DATA_TYPE", "DATA_LENGTH", "NULLABLE", "DATA_DEFAULT"])

        if upper.startswith("SELECT CC.COLUMN_NAME"):
            meta = self.db.entry(params["name"])
            if meta is None:
                return FakeResult()
            return FakeResult([(name,) for name in meta.get("pk", [])], ["COLUMN_NAME"])

        if upper.startswith("INSERT INTO"):
            return self._insert(sql, params)
        if upper.startswith("UPDATE "):
            return self._update(sql, params)
        if upper.startswith("DELETE FROM"):
            return self._delete(sql, params)
        if upper.startswith("DROP TABLE"):
            name = sql.split()[2].strip('"').upper()
            existed = self.db.tables.pop(name, None) is not None
            return FakeResult(rowcount=0 if existed else 0)
        if upper.startswith('SELECT "'):
            return self._rows(sql, params)

        raise AssertionError(f"替身会话不认识的语句：{sql}")

    def _rows(self, sql: str, params: dict) -> FakeResult:
        match = re.match(r'^SELECT (.+?) FROM "([^"]+)"(.*)$', sql, re.S)
        assert match is not None, sql
        columns = [part.strip().strip('"') for part in match.group(1).split(",")]
        table = match.group(2)
        rest = match.group(3)

        meta = self.db.entry(table)
        assert meta is not None, f"unknown table in fake db: {table}"
        rows = [dict(row) for row in meta.get("rows", [])]

        order = re.search(r"ORDER BY (.+?)(?: OFFSET |$)", rest)
        if order is not None:
            for clause in reversed(order.group(1).split(",")):
                token = re.match(r'\s*"([^"]+)"\s+(ASC|DESC)', clause)
                assert token is not None, clause
                column, direction = token.group(1), token.group(2)
                rows.sort(
                    key=lambda row, c=column: (row.get(c) is None, row.get(c)),
                    reverse=direction == "DESC",
                )

        offset = int(params.get("row_offset", 0))
        fetch = params.get("row_fetch")
        page = rows[offset : offset + fetch] if fetch is not None else rows[offset:]
        return FakeResult(
            [tuple(row.get(column) for column in columns) for row in page], columns
        )

    def _insert(self, sql: str, params: dict) -> FakeResult:
        match = re.match(
            r'^INSERT INTO\s+"?([A-Za-z][\w$#]*)"?\s*\((.+?)\)\s*VALUES\s*\((.+?)\)$',
            sql,
            re.I,
        )
        assert match is not None, sql
        table = match.group(1).upper()
        columns = [part.strip().strip('"').upper() for part in match.group(2).split(",")]
        values = [_evaluate(part, params) for part in match.group(3).split(",")]
        self.db.tables[table]["rows"].append(dict(zip(columns, values)))
        return FakeResult(rowcount=1)

    def _update(self, sql: str, params: dict) -> FakeResult:
        match = re.match(
            r'^UPDATE\s+"?([A-Za-z][\w$#]*)"?\s+SET\s+(.+?)\s+WHERE\s+(.+)$', sql, re.I
        )
        assert match is not None, sql
        table = match.group(1).upper()
        sets = _clauses(match.group(2), params)
        wheres = _clauses(match.group(3), params)
        changed = 0
        for row in self.db.tables[table]["rows"]:
            if all(row.get(column) == value for column, value in wheres):
                for column, value in sets:
                    row[column] = value
                changed += 1
        return FakeResult(rowcount=changed)

    def _delete(self, sql: str, params: dict) -> FakeResult:
        match = re.match(
            r'^DELETE FROM\s+"?([A-Za-z][\w$#]*)"?\s+WHERE\s+(.+)$', sql, re.I
        )
        assert match is not None, sql
        table = match.group(1).upper()
        wheres = _clauses(match.group(2), params)
        rows = self.db.tables[table]["rows"]
        kept = [
            row
            for row in rows
            if not all(row.get(column) == value for column, value in wheres)
        ]
        removed = len(rows) - len(kept)
        self.db.tables[table]["rows"] = kept
        return FakeResult(rowcount=removed)


class FakeDb:
    def __init__(self, tables=None, views=None):
        self.tables = tables or {}
        self.views = views or {}

    def entry(self, name):
        return self.tables.get(name) or self.views.get(name)


# 替身只认简单字形：绑定变量、整/小数、单引号字面量、NULL。够覆盖用例里
# 出现的语句形态；复杂的 Oracle 表达式不在替身的责任范围内。
_ASSIGN_RE = re.compile(
    r'"?([A-Za-z][A-Za-z0-9_$#]*)"?\s*=\s*(:\w+|\'[^\']*\'|-?\d+(?:\.\d+)?|NULL)', re.I
)


def _evaluate(token: str, params: dict):
    token = token.strip()
    if token.startswith(":"):
        # 绑定参数名不带冒号（SQLAlchemy 的 params 字典形态）。
        return params.get(token[1:])
    if token.upper() == "NULL":
        return None
    if token.startswith("'"):
        return token[1:-1].replace("''", "'")
    if "." in token:
        return float(token)
    return int(token)


def _clauses(fragment: str, params: dict) -> list[tuple[str, object]]:
    return [
        (column.upper(), _evaluate(value, params))
        for column, value in _ASSIGN_RE.findall(fragment)
    ]


def _users_row(oid, name, score=Decimal("8.5"), bio="自我介绍", avatar=b"\x00\x01"):
    return {
        "OID": oid,
        "BUSINESS_ID": f"2024{oid[-2:]}",
        "NAME": name,
        "SCORE": score,
        "AVATAR": avatar,
        "BIO": bio,
        "CREATED_AT": datetime(2026, 10, 1, 12, 0, 0),
    }


def _make_db() -> FakeDb:
    tables = {
        "USERS": {
            "columns": [
                ("OID", "VARCHAR2", 24, "N", None),
                ("BUSINESS_ID", "VARCHAR2", 20, "N", None),
                ("NAME", "VARCHAR2", 50, "Y", None),
                ("SCORE", "NUMBER", 22, "Y", None),
                ("AVATAR", "BLOB", 4000, "Y", None),
                ("BIO", "CLOB", 4000, "Y", None),
                ("CREATED_AT", "TIMESTAMP(6)", 11, "Y", "SYSTIMESTAMP"),
                # 时区列只用于写入策略回归；真实 V001 约定全部是朴素 TIMESTAMP。
                ("TZ_AT", "TIMESTAMP(6) WITH TIME ZONE", 13, "Y", None),
                ("LTZ_AT", "TIMESTAMP(6) WITH LOCAL TIME ZONE", 11, "Y", None),
            ],
            "pk": ["OID"],
            "num_rows": 1234,
            "last_analyzed": datetime(2026, 10, 1, 0, 0, 0),
            "rows": [
                _users_row("000000000000000000000001", "张三"),
                _users_row("000000000000000000000002", "李四", Decimal("9.5")),
                _users_row("000000000000000000000003", "王五", Decimal("7")),
            ],
        },
        "NO_PK_LOG": {
            "columns": [
                ("MESSAGE", "VARCHAR2", 200, "Y", None),
                ("LEVEL", "VARCHAR2", 10, "Y", None),
            ],
            "pk": [],
            "num_rows": 2,
            "last_analyzed": None,
            "rows": [{"MESSAGE": "a", "LEVEL": "info"}, {"MESSAGE": "b", "LEVEL": "warn"}],
        },
        "LEGACY_TABLE": {
            "columns": [("ID", "NUMBER", 22, "N", None)],
            "pk": ["ID"],
            "num_rows": 1,
            "last_analyzed": None,
            "rows": [{"ID": 1}],
        },
        "CREDS": {
            "columns": [
                ("OID", "VARCHAR2", 24, "N", None),
                ("NAME", "VARCHAR2", 50, "Y", None),
                ("PASSWORD_HASH", "VARCHAR2", 200, "Y", None),
                ("SESSION_ID", "VARCHAR2", 24, "Y", None),
            ],
            "pk": ["OID"],
            "num_rows": 1,
            "last_analyzed": None,
            "rows": [
                {
                    "OID": "000000000000000000000009",
                    "NAME": "张三",
                    "PASSWORD_HASH": "$2b$12$secret",
                    "SESSION_ID": "sess-secret",
                }
            ],
        },
        # 自保护表：行的写操作必须被拒（契约 §4.4.2）。
        "DUAL_AUTH_USES": {
            "columns": [
                ("OID", "VARCHAR2", 24, "N", None),
                ("APPROVER_OID", "VARCHAR2", 24, "N", None),
            ],
            "pk": ["OID"],
            "num_rows": 3,
            "last_analyzed": None,
            "rows": [{"OID": "u1", "APPROVER_OID": "a1"}],
        },
    }
    views = {
        "USER_VIEW": {
            "columns": [("OID", "VARCHAR2", 24, "Y", None)],
            "rows": [{"OID": "x"}],
        }
    }
    return FakeDb(tables, views)


# ---------------------------------------------------------------------------
# HTTP 夹具
# ---------------------------------------------------------------------------


def _build(monkeypatch, db, *, real_dual_auth=False, code=None):
    state = {"per": ["admin"], "roles": ["admin"], "banned": False, "reject": False}
    session = FakeSession(db)

    class StubRepo:
        async def get_by_oid(self, oid):
            return {
                "_id": oid,
                "roles": list(state["roles"]),
                "banned": state["banned"],
            }

        async def list_admin_oids(self):
            return [OPERATOR, APPROVER]

        async def insert_dual_auth_use(self, approver, counter, action, operator):
            return "f" * 24

    repo = StubRepo()
    monkeypatch.setattr(
        backends, "get_users_repo", lambda session=None: repo
    )

    audits: list[dict] = []

    async def fake_audit(
        operator_oid, *, action, detail="", log_type="system_op", level="warning"
    ):
        audits.append(
            {
                "operator": operator_oid,
                "action": action,
                "detail": detail,
                "log_type": log_type,
                "level": level,
            }
        )

    monkeypatch.setattr(router_module, "audit_system_op", fake_audit)

    if not real_dual_auth:

        async def fake_dual(operator, totp_code, *, action):
            if state["reject"]:
                raise DualAuthError(403, "动态码无效或已过期")
            return APPROVER

        monkeypatch.setattr(router_module, "require_dual_auth", fake_dual)

    @asynccontextmanager
    async def scope():
        yield session

    monkeypatch.setattr(db_content, "session_scope", scope)

    app = FastAPI()
    app.include_router(router_module.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: {
        "id": OPERATOR,
        "per": state["per"],
    }
    return SimpleNamespace(
        client=TestClient(app),
        app=app,
        audits=audits,
        state=state,
        db=db,
        session=session,
        repo=repo,
        operator_code=code,
    )


@pytest.fixture
def fake_db() -> FakeDb:
    return _make_db()


@pytest.fixture
def harness(monkeypatch, fake_db):
    return _build(monkeypatch, fake_db)


# ---------------------------------------------------------------------------
# 1. 标识符闸
# ---------------------------------------------------------------------------


def test_identifier_accepts_legal_oracle_names():
    assert db_content.validate_identifier("users") == "USERS"
    assert db_content.validate_identifier("a$b#c") == "A$B#C"
    assert db_content.validate_identifier("A" + "b" * 29) == ("A" + "b" * 29).upper()


@pytest.mark.parametrize(
    "bad",
    [
        "users; DROP TABLE users",
        "users--",
        "1users",
        "a b",
        '"USERS"',
        "",
        "A" + "b" * 30,  # 31 位：超 Oracle 30 字节上限
        "用户",
    ],
)
def test_identifier_rejects_injection_and_overlong(bad):
    with pytest.raises(db_content.DbContentError) as exc:
        db_content.validate_identifier(bad)
    assert exc.value.status_code == 400


def test_unknown_table_is_404(fake_db):
    session = FakeSession(fake_db)
    with pytest.raises(db_content.DbContentError) as exc:
        asyncio.run(db_content.get_columns(session, "NOBODY"))
    assert exc.value.status_code == 404
    # 只有一次存在性探测，没有拼出任何 DDL/DML。
    assert len(session.calls) == 2  # user_tables + user_views


def test_injection_in_path_is_400_and_executes_nothing(harness):
    response = harness.client.get(f"{PREFIX}/table/users; DROP TABLE users/rows")
    assert response.status_code == 400
    assert harness.session.calls == []


def test_unknown_table_path_is_404(harness):
    response = harness.client.get(f"{PREFIX}/table/NO_SUCH_TABLE/columns")
    assert response.status_code == 404
    assert "不存在" in response.json()["detail"]


# ---------------------------------------------------------------------------
# 2. 只读闸
# ---------------------------------------------------------------------------


def test_readonly_gate_strips_comments_before_checking():
    sql = "  -- 注释里有 DELETE\n /* 块注释里也有 DROP */ SELECT OID FROM USERS"
    assert db_content.validate_readonly_sql(sql) == "SELECT OID FROM USERS"


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM USERS",
        "UPDATE USERS SET NAME = 'x'",
        "INSERT INTO USERS (OID) VALUES ('1')",
        "SELECT 1 FROM DUAL; DROP TABLE USERS",
        "WITH killed AS (SELECT 1 FROM DUAL) DELETE FROM USERS",
        "WITH killed AS (SELECT 1 FROM DUAL) SELECT * FROM killed; COMMIT",
        "BEGIN NULL; END",
        "(SELECT 1 FROM DUAL)",
    ],
)
def test_readonly_gate_rejects_writes_and_multi_statement(sql):
    with pytest.raises(db_content.DbContentError) as exc:
        db_content.validate_readonly_sql(sql)
    assert exc.value.status_code == 400


def test_readonly_gate_allows_keywords_inside_literals():
    sql = "SELECT OID FROM USERS WHERE NAME = 'DELETE; DROP TABLE'"
    # 分号在字面量里也一样会被拒：单语句判定宁可严一点（契约 §4.3.1）。
    with pytest.raises(db_content.DbContentError):
        db_content.validate_readonly_sql(sql)

    assert (
        db_content.validate_readonly_sql(
            "SELECT OID FROM USERS WHERE NAME = 'DELETE ME'"
        )
        == "SELECT OID FROM USERS WHERE NAME = 'DELETE ME'"
    )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT username, password FROM sys.user$",
        "SELECT * FROM dba_users",
        "SELECT * FROM v$session",
        "SELECT HTTPURITYPE('http://169.254.169.254/').getclob() FROM dual",
        "SELECT DBMS_LOCK.SLEEP(100) FROM dual",
        "SELECT * FROM USER_TRASH",
        "SELECT * FROM DUAL_AUTH_USES",
        "SELECT * FROM USERS@REMOTE_DB",
    ],
)
def test_readonly_gate_blocks_system_objects_and_external_calls(sql):
    with pytest.raises(db_content.DbContentError) as exc:
        db_content.validate_readonly_sql(sql)
    assert exc.value.status_code == 403


def test_readonly_gate_rejects_for_update_row_locks():
    """``FOR UPDATE`` 不在写关键字里，却能在只读端点上加行锁，必须单独拒绝。"""
    for sql in (
        "SELECT OID FROM USERS FOR UPDATE",
        "SELECT OID FROM USERS WHERE OID = 'x' FOR UPDATE NOWAIT",
        "select oid from users for update skip locked",
    ):
        with pytest.raises(db_content.DbContentError) as exc:
            db_content.validate_readonly_sql(sql)
        assert exc.value.status_code == 400

    # 普通只读不受影响
    assert (
        db_content.validate_readonly_sql("SELECT OID FROM USERS")
        == "SELECT OID FROM USERS"
    )


def test_query_uses_fetchmany_and_limit_plus_one(fake_db):
    session = FakeSession(fake_db)
    session.pending.append(
        FakeResult(
            [("a",), ("b",), ("c",)],
            ["OID"],
        )
    )

    data = asyncio.run(db_content.run_query(session, "SELECT OID FROM USERS", limit=2))

    assert data["columns"] == ["OID"]
    assert data["rows"] == [{"OID": "a"}, {"OID": "b"}]
    assert data["truncated"] is True
    assert session.results[0].fetchmany_calls == [3]
    # 契约 §4.5：不得用 COUNT(*)。
    assert not any("COUNT(" in sql.upper() for sql in session.executed)


def test_query_clamps_limit_to_contract_ceiling(fake_db):
    session = FakeSession(fake_db)
    session.pending.append(FakeResult([], ["X"]))
    asyncio.run(db_content.run_query(session, "SELECT 1 FROM DUAL", limit=99999))
    assert session.results[0].fetchmany_calls == [db_content.MAX_QUERY_LIMIT + 1]


def test_query_executes_user_sql_verbatim(monkeypatch, fake_db):
    """用户 SQL 不得经过 SQLAlchemy 的 ``text()`` 绑定解析。

    合法 Oracle SQL 里出现 ``:``（JSON 字面量、时间格式等）不代表有绑定参数；
    ``text()`` 会把 ``:name`` 当成绑定并在到达驱动前抛 ``StatementError`` → 500。
    """
    session = FakeSession(fake_db)
    session.pending.append(FakeResult([("ok",)], ["X"]))
    sql = "SELECT '{\"a\":1}' AS X FROM DUAL"

    def forbidden(*_args, **_kwargs):
        raise AssertionError("用户 SQL 不应经过 db_content.text()")

    monkeypatch.setattr(db_content, "text", forbidden)

    data = asyncio.run(db_content.run_query(session, sql, limit=5))

    assert session.driver_calls == [sql]
    assert data["rows"] == [{"X": "ok"}]


def test_query_timeout_is_504(monkeypatch, fake_db):
    monkeypatch.setattr(db_content, "QUERY_TIMEOUT_SECONDS", 0.01)
    session = FakeSession(fake_db)

    async def slow_execute(stmt, params=None):
        await asyncio.sleep(0.5)
        return FakeResult()

    session.execute = slow_execute  # type: ignore[method-assign]
    with pytest.raises(db_content.DbContentError) as exc:
        asyncio.run(db_content.run_query(session, "SELECT 1 FROM DUAL", limit=1))
    assert exc.value.status_code == 504


def test_query_endpoint_needs_no_dual_code(harness):
    harness.state["reject"] = True  # 双人码全拒，/query 仍然可用
    harness.session.pending.append(FakeResult([("6ad4",)], ["OID"]))
    response = harness.client.post(f"{PREFIX}/query", json={"sql": "SELECT OID FROM USERS"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["rows"] == [{"OID": "6ad4"}]
    assert data["truncated"] is False
    assert isinstance(data["elapsed_ms"], int)


def test_run_query_redacts_sensitive_columns(fake_db):
    session = FakeSession(fake_db)
    session.pending.append(
        FakeResult(
            [("$2b$12$secret", "sess-secret")],
            ["PASSWORD_HASH", "SESSION_ID"],
        )
    )
    data = asyncio.run(
        db_content.run_query(
            session, "SELECT PASSWORD_HASH, SESSION_ID FROM CREDS"
        )
    )
    assert data["rows"] == [{"PASSWORD_HASH": "***", "SESSION_ID": "***"}]


def test_query_endpoint_rejects_delete(harness):
    response = harness.client.post(f"{PREFIX}/query", json={"sql": "DELETE FROM USERS"})
    assert response.status_code == 400
    assert harness.session.calls == []


def test_query_endpoint_limit_over_500_is_422(harness):
    response = harness.client.post(
        f"{PREFIX}/query", json={"sql": "SELECT 1 FROM DUAL", "limit": 501}
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# 3. schema / columns
# ---------------------------------------------------------------------------


def test_schema_lists_tables_and_views(fake_db):
    data = asyncio.run(db_content.list_schema(FakeSession(fake_db)))
    assert data["total"] == 6  # 4 表 + 1 视图 + DUAL_AUTH_USES
    by_name = {item["name"]: item for item in data["tables"]}
    assert by_name["USERS"] == {
        "name": "USERS",
        "type": "TABLE",
        "num_rows": 1234,
        "last_analyzed": "2026-10-01T00:00:00",
    }
    assert by_name["USER_VIEW"]["type"] == "VIEW"
    assert by_name["USER_VIEW"]["num_rows"] is None
    assert by_name["NO_PK_LOG"]["last_analyzed"] is None


def test_columns_expose_pk_defaults_and_nullability(fake_db):
    data = asyncio.run(db_content.get_columns(FakeSession(fake_db), "users"))
    assert data["table"] == "USERS"
    assert data["primary_key"] == ["OID"]
    oid_col = data["columns"][0]
    assert oid_col == {
        "name": "OID",
        "type": "VARCHAR2",
        "length": 24,
        "nullable": False,
        "default": None,
        "primary_key": True,
    }
    assert data["columns"][6]["default"] == "SYSTIMESTAMP"
    assert data["columns"][6]["nullable"] is True


def test_columns_of_table_without_pk_returns_empty_list(fake_db):
    data = asyncio.run(db_content.get_columns(FakeSession(fake_db), "NO_PK_LOG"))
    assert data["primary_key"] == []
    assert all(column["primary_key"] is False for column in data["columns"])


# ---------------------------------------------------------------------------
# 4. 行读取与序列化
# ---------------------------------------------------------------------------


def test_rows_has_more_comes_from_limit_plus_one(fake_db):
    session = FakeSession(fake_db)
    data = asyncio.run(db_content.read_rows(session, "USERS", limit=2))

    assert data["limit"] == 2
    assert data["offset"] == 0
    assert data["has_more"] is True
    assert len(data["rows"]) == 2
    assert data["total_estimate"] == 1234
    assert data["primary_key"] == ["OID"]
    assert "row_fetch" in session.calls[-1][1]
    assert session.calls[-1][1]["row_fetch"] == 3
    assert not any("COUNT(" in sql.upper() for sql in session.executed)


def test_rows_last_page_has_no_more(fake_db):
    data = asyncio.run(db_content.read_rows(FakeSession(fake_db), "USERS", limit=200))
    assert data["has_more"] is False
    assert len(data["rows"]) == 3


def test_rows_default_order_is_primary_key(fake_db):
    session = FakeSession(fake_db)
    asyncio.run(db_content.read_rows(session, "USERS", limit=1))
    assert 'ORDER BY "OID" ASC' in session.executed[-1]


def test_rows_order_by_real_column_desc(fake_db):
    data = asyncio.run(
        db_content.read_rows(FakeSession(fake_db), "USERS", limit=1, order_by="score", desc=True)
    )
    assert data["rows"][0]["OID"] == "000000000000000000000002"  # SCORE 9.5


def test_rows_order_by_unknown_column_is_400(fake_db):
    with pytest.raises(db_content.DbContentError) as exc:
        asyncio.run(
            db_content.read_rows(FakeSession(fake_db), "USERS", order_by="NOT_A_COLUMN")
        )
    assert exc.value.status_code == 400


def test_rows_serialize_lobs_timestamp_and_number(fake_db):
    data = asyncio.run(db_content.read_rows(FakeSession(fake_db), "USERS", limit=1))
    row = data["rows"][0]

    assert row["BIO"] == {"$lob": "CLOB", "length": 4, "preview": "自我介绍"}
    assert row["AVATAR"] == {"$lob": "BLOB", "length": 2, "preview": None}
    assert row["SCORE"] == 8.5
    assert isinstance(row["SCORE"], float)
    assert row["CREATED_AT"] == "2026-10-01T12:00:00"


def test_lob_object_is_serialized_with_preview():
    class FakeLob:
        type = SimpleNamespace(name="DB_TYPE_CLOB")
        size = 5000

        def read(self, offset=1, amount=0):
            return "x" * amount

    payload = db_content.serialize_value(FakeLob())
    assert payload["$lob"] == "CLOB"
    assert payload["length"] == 5000
    assert payload["preview"] == "x" * db_content.LOB_PREVIEW_CHARS


def test_large_clob_preview_is_truncated(fake_db):
    fake_db.tables["USERS"]["rows"][0]["BIO"] = "y" * 5000
    data = asyncio.run(db_content.read_rows(FakeSession(fake_db), "USERS", limit=1))
    bio = data["rows"][0]["BIO"]
    assert bio["length"] == 5000
    assert len(bio["preview"]) == db_content.LOB_PREVIEW_CHARS


def test_read_rows_redacts_credential_columns(fake_db):
    data = asyncio.run(
        db_content.read_rows(FakeSession(fake_db), "CREDS", limit=1)
    )
    row = data["rows"][0]
    assert row["NAME"] == "张三"
    assert row["PASSWORD_HASH"] == "***"
    assert row["SESSION_ID"] == "***"


def test_rows_of_view_have_no_primary_key(fake_db):
    data = asyncio.run(db_content.read_rows(FakeSession(fake_db), "USER_VIEW", limit=10))
    assert data["primary_key"] == []
    assert data["total_estimate"] is None


# ---------------------------------------------------------------------------
# 5. 行 CRUD
# ---------------------------------------------------------------------------


def test_insert_row_binds_values(fake_db):
    session = FakeSession(fake_db)
    data = asyncio.run(
        db_content.insert_row(session, "USERS", {"OID": "0009", "NAME": "赵六"})
    )
    assert data == {"rowcount": 1}
    assert session.commits == 1
    assert fake_db.tables["USERS"]["rows"][-1]["OID"] == "0009"
    # 值只出现在绑定参数里，SQL 文本里没有用户值。
    sql, params = session.calls[-1]
    assert "赵六" not in sql and "0009" not in sql
    assert params == {"v0": "0009", "v1": "赵六"}


def test_insert_row_coerces_iso_timestamp_for_temporal_column(fake_db):
    """网格回填的是 ISO8601 字符串；绑定前必须转成 datetime（否则 ORA-01861）。"""
    session = FakeSession(fake_db)
    data = asyncio.run(
        db_content.insert_row(
            session,
            "USERS",
            {"OID": "0009", "NAME": "赵六", "CREATED_AT": "2026-10-01T12:00:00"},
        )
    )
    assert data == {"rowcount": 1}
    params = session.calls[-1][1]
    assert isinstance(params["v2"], datetime)
    assert params["v2"] == datetime(2026, 10, 1, 12, 0, 0)
    # 非时间列即便长得像时间也保持字符串。
    assert params["v1"] == "赵六"


def test_update_row_coerces_iso_timestamp_and_rejects_garbage(fake_db):
    session = FakeSession(fake_db)
    asyncio.run(
        db_content.update_row(
            session,
            "USERS",
            {"OID": "000000000000000000000001"},
            {"CREATED_AT": "2026-10-01T12:00:00"},
        )
    )
    assert isinstance(session.calls[-1][1]["s0"], datetime)

    with pytest.raises(db_content.DbContentError) as exc:
        asyncio.run(
            db_content.update_row(
                session,
                "USERS",
                {"OID": "000000000000000000000001"},
                {"CREATED_AT": "not-a-timestamp"},
            )
        )
    assert exc.value.status_code == 400
    assert "CREATED_AT" in exc.value.detail


def test_temporal_column_accepts_date_only_and_tz_suffix(fake_db):
    session = FakeSession(fake_db)
    asyncio.run(
        db_content.update_row(
            session,
            "USERS",
            {"OID": "000000000000000000000001"},
            {"CREATED_AT": "2026-10-01T12:00:00Z"},
        )
    )
    assert session.calls[-1][1]["s0"] == datetime(2026, 10, 1, 12, 0, 0)


def test_timestamp_with_time_zone_rejects_writes_instead_of_dropping_zone(fake_db):
    """TSTZ 的偏移无法通过网格往返；拒绝写入而不是悄悄改时间点。"""
    for value in (
        "2026-10-01T12:00:00+08:00",
        "2026-10-01T12:00:00",  # 无偏移同样无法确定原时区
    ):
        session = FakeSession(fake_db)
        with pytest.raises(db_content.DbContentError) as exc:
            asyncio.run(
                db_content.insert_row(session, "USERS", {"OID": "0009", "TZ_AT": value})
            )
        assert exc.value.status_code == 400
        assert "TIME ZONE" in exc.value.detail
        assert not any(sql.upper().startswith("INSERT") for sql in session.executed)


def test_local_time_zone_column_accepts_naive_session_time_only(fake_db):
    """LTZ 读回来是会话本地 naive 值；原样写回可往返，带偏移则拒绝。"""
    session = FakeSession(fake_db)
    asyncio.run(
        db_content.insert_row(
            session, "USERS", {"OID": "0009", "LTZ_AT": "2026-10-01T12:00:00"}
        )
    )
    assert session.calls[-1][1]["v1"] == datetime(2026, 10, 1, 12, 0, 0)

    with pytest.raises(db_content.DbContentError) as exc:
        asyncio.run(
            db_content.update_row(
                session,
                "USERS",
                {"OID": "0009"},
                {"LTZ_AT": "2026-10-01T12:00:00Z"},
            )
        )
    assert exc.value.status_code == 400
    assert "LOCAL TIME ZONE" in exc.value.detail


def test_insert_row_rejects_unknown_column(fake_db):
    with pytest.raises(db_content.DbContentError) as exc:
        asyncio.run(
            db_content.insert_row(FakeSession(fake_db), "USERS", {"NOPE": "x"})
        )
    assert exc.value.status_code == 400


def test_insert_row_rejects_empty_values(fake_db):
    with pytest.raises(db_content.DbContentError) as exc:
        asyncio.run(db_content.insert_row(FakeSession(fake_db), "USERS", {}))
    assert exc.value.status_code == 400


def test_update_row_requires_every_primary_key_column(fake_db):
    with pytest.raises(db_content.DbContentError) as exc:
        asyncio.run(
            db_content.update_row(FakeSession(fake_db), "USERS", {"NAME": "张三"}, {"NAME": "x"})
        )
    assert exc.value.status_code == 400
    assert "主键" in exc.value.detail


def test_update_row_rejects_non_primary_key_column(fake_db):
    with pytest.raises(db_content.DbContentError) as exc:
        asyncio.run(
            db_content.update_row(
                FakeSession(fake_db),
                "USERS",
                {"OID": "0001", "NAME": "张三"},
                {"NAME": "x"},
            )
        )
    assert exc.value.status_code == 400


def test_update_row_is_scoped_to_the_key(fake_db):
    session = FakeSession(fake_db)
    data = asyncio.run(
        db_content.update_row(
            session,
            "USERS",
            {"OID": "000000000000000000000002"},
            {"NAME": "改过的"},
        )
    )
    assert data == {"rowcount": 1}
    assert session.commits == 1
    assert fake_db.tables["USERS"]["rows"][1]["NAME"] == "改过的"
    assert fake_db.tables["USERS"]["rows"][0]["NAME"] == "张三"
    sql, params = session.calls[-1]
    assert "改过的" not in sql
    assert '"OID" = :k0' in sql


def test_delete_row_is_scoped_to_the_key(fake_db):
    session = FakeSession(fake_db)
    data = asyncio.run(
        db_content.delete_row(session, "USERS", {"OID": "000000000000000000000003"})
    )
    assert data == {"rowcount": 1}
    assert len(fake_db.tables["USERS"]["rows"]) == 2


def test_row_operations_on_table_without_primary_key_are_400(fake_db):
    session = FakeSession(fake_db)
    with pytest.raises(db_content.DbContentError) as update_exc:
        asyncio.run(
            db_content.update_row(session, "NO_PK_LOG", {"MESSAGE": "a"}, {"LEVEL": "x"})
        )
    assert update_exc.value.status_code == 400

    with pytest.raises(db_content.DbContentError) as delete_exc:
        asyncio.run(db_content.delete_row(session, "NO_PK_LOG", {"MESSAGE": "a"}))
    assert delete_exc.value.status_code == 400

    # 插入不需要主键，仍然可用。
    assert asyncio.run(
        db_content.insert_row(session, "NO_PK_LOG", {"MESSAGE": "c"})
    ) == {"rowcount": 1}


def test_row_write_on_protected_table_is_403(fake_db):
    """行 CRUD 也是 DML：删掉一条 dual_auth_uses 等于放开了动态码重放。"""
    session = FakeSession(fake_db)

    with pytest.raises(db_content.DbContentError) as insert_exc:
        asyncio.run(
            db_content.insert_row(session, "dual_auth_uses", {"OID": "u2"})
        )
    assert insert_exc.value.status_code == 403

    with pytest.raises(db_content.DbContentError) as update_exc:
        asyncio.run(
            db_content.update_row(
                session, "DUAL_AUTH_USES", {"OID": "u1"}, {"APPROVER_OID": "x"}
            )
        )
    assert update_exc.value.status_code == 403

    with pytest.raises(db_content.DbContentError) as delete_exc:
        asyncio.run(db_content.delete_row(session, "DUAL_AUTH_USES", {"OID": "u1"}))
    assert delete_exc.value.status_code == 403

    assert fake_db.tables["DUAL_AUTH_USES"]["rows"] == [{"OID": "u1", "APPROVER_OID": "a1"}]
    # 读取也必须拒绝：审批/凭据表的行本身就是敏感材料，不能走通用控制台。
    with pytest.raises(db_content.DbContentError) as read_exc:
        asyncio.run(db_content.read_rows(session, "DUAL_AUTH_USES", limit=10))
    assert read_exc.value.status_code == 403


# ---------------------------------------------------------------------------
# 6. DDL 闸
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE DUAL_AUTH_USES",
        "TRUNCATE TABLE USER_TRASH",
        "DELETE FROM CHANGELOG",
        "UPDATE CHANGELOG SET DATA = 'x' WHERE OID = '1'",
        "DROP TABLE USERS",
        "TRUNCATE TABLE USERS",
    ],
)
def test_ddl_rejects_self_protected_tables(sql):
    with pytest.raises(db_content.DbContentError) as exc:
        db_content.validate_ddl_sql(sql, confirm=sql.split()[-1])
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    "sql",
    [
        "GRANT DBA TO HACKER",
        "CREATE USER HACKER IDENTIFIED BY x",
        "ALTER SESSION SET CURRENT_SCHEMA=SYS",
        "ALTER SYSTEM KILL SESSION '1,1'",
    ],
)
def test_ddl_rejects_security_model_and_session_statements(sql):
    with pytest.raises(db_content.DbContentError) as exc:
        db_content.validate_ddl_sql(sql, None)
    assert exc.value.status_code == 403


def test_ddl_rejects_readonly_statement():
    with pytest.raises(db_content.DbContentError) as exc:
        db_content.validate_ddl_sql("SELECT * FROM LEGACY_TABLE FOR UPDATE", None)
    assert exc.value.status_code == 400


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE LEGACY_TABLE",
        "TRUNCATE TABLE LEGACY_TABLE",
        "DELETE FROM LEGACY_TABLE",
        "DELETE FROM LEGACY_TABLE WHERE 1=1",
        "UPDATE LEGACY_TABLE SET ID = 2",
        "UPDATE LEGACY_TABLE SET ID = 2 WHERE 1=1",
        "ALTER TABLE LEGACY_TABLE DROP COLUMN ID",
    ],
)
def test_destructive_statements_require_confirm(sql):
    with pytest.raises(db_content.DbContentError) as exc:
        db_content.validate_ddl_sql(sql, confirm=None)
    assert exc.value.status_code == 400
    assert "confirm" in exc.value.detail


def test_destructive_confirm_must_equal_target_table():
    with pytest.raises(db_content.DbContentError) as exc:
        db_content.validate_ddl_sql("DROP TABLE LEGACY_TABLE", confirm="LEGACY")
    assert exc.value.status_code == 400


def test_ddl_rejects_multiple_statements():
    with pytest.raises(db_content.DbContentError) as exc:
        db_content.validate_ddl_sql("DROP TABLE A; DROP TABLE B", confirm="A")
    assert exc.value.status_code == 400


def test_ddl_allows_single_trailing_semicolon():
    plan = db_content.validate_ddl_sql("  ALTER TABLE LEGACY_TABLE ADD (X NUMBER);  ", None)
    assert plan["sql"] == "ALTER TABLE LEGACY_TABLE ADD (X NUMBER)"
    assert plan["verb"] == "ALTER"
    assert plan["target"] == "LEGACY_TABLE"


def test_ddl_non_destructive_copy_is_allowed_without_confirm():
    plan = db_content.validate_ddl_sql(
        "INSERT INTO LEGACY_TABLE (ID) VALUES (2)", None
    )
    assert plan["destructive"] is False
    assert plan["summary"] == "INSERT INTO LEGACY_TABLE"


def test_ddl_summary_is_marked_up_for_drop():
    plan = db_content.validate_ddl_sql("drop table legacy_table", "legacy_table")
    assert plan["summary"] == "DROP TABLE LEGACY_TABLE"
    assert plan["is_ddl"] is True


def test_run_ddl_reports_null_rowcount_and_committed(fake_db):
    session = FakeSession(fake_db)
    data = asyncio.run(
        db_content.run_ddl(session, "DROP TABLE LEGACY_TABLE", "LEGACY_TABLE")
    )
    assert data == {
        "rowcount": None,
        "committed": True,
        "summary": "DROP TABLE LEGACY_TABLE",
    }
    assert session.commits == 1
    assert "LEGACY_TABLE" not in fake_db.tables


def test_run_ddl_executes_user_sql_verbatim(monkeypatch, fake_db):
    """``/ddl`` 同样必须把语句原样交给驱动，而不是让 ``text()`` 解析冒号。"""
    session = FakeSession(fake_db)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("用户 SQL 不应经过 db_content.text()")

    monkeypatch.setattr(db_content, "text", forbidden)

    data = asyncio.run(
        db_content.run_ddl(
            session,
            "UPDATE LEGACY_TABLE SET ID = 2 WHERE ID = 1",
            "LEGACY_TABLE",
        )
    )

    assert session.driver_calls == ["UPDATE LEGACY_TABLE SET ID = 2 WHERE ID = 1"]
    assert data["rowcount"] == 1
    assert session.commits == 1


def test_run_ddl_dml_reports_rowcount(fake_db):
    session = FakeSession(fake_db)
    data = asyncio.run(
        db_content.run_ddl(
            session, "UPDATE LEGACY_TABLE SET ID = 5 WHERE ID = 1", "LEGACY_TABLE"
        )
    )
    assert data["rowcount"] == 1
    assert data["committed"] is True


# ---------------------------------------------------------------------------
# 7. 鉴权与副作用
# ---------------------------------------------------------------------------


def test_endpoints_require_login():
    app = FastAPI()
    app.include_router(router_module.router, prefix=PREFIX)
    client = TestClient(app)
    assert client.get(f"{PREFIX}/schema").status_code == 401
    assert client.post(f"{PREFIX}/query", json={"sql": "SELECT 1 FROM DUAL"}).status_code == 401
    assert (
        client.post(f"{PREFIX}/ddl", json={"sql": "DROP TABLE X", "totp_code": CODE}).status_code
        == 401
    )


def test_non_admin_is_forbidden(harness):
    harness.state["per"] = ["student"]
    harness.state["roles"] = ["student"]
    assert harness.client.get(f"{PREFIX}/schema").status_code == 403
    assert harness.session.calls == []


def test_banned_admin_is_forbidden(harness):
    harness.state["banned"] = True
    assert harness.client.get(f"{PREFIX}/schema").status_code == 403


def test_bad_dual_code_executes_no_sql(harness):
    harness.state["reject"] = True
    response = harness.client.post(
        f"{PREFIX}/table/USERS/rows",
        json={"values": {"OID": "0009"}, "totp_code": "000000"},
    )
    assert response.status_code == 403
    assert harness.session.calls == []
    assert harness.audits == []


def test_bad_dual_code_on_ddl_executes_no_sql(harness):
    harness.state["reject"] = True
    response = harness.client.post(
        f"{PREFIX}/ddl",
        json={"sql": "DROP TABLE LEGACY_TABLE", "confirm": "LEGACY_TABLE", "totp_code": "000000"},
    )
    assert response.status_code == 403
    assert harness.session.calls == []
    assert "LEGACY_TABLE" in harness.db.tables


def test_invalid_ddl_is_rejected_before_dual_auth_and_sql(harness):
    """缺 confirm 属于语句层错误：先 400，不去消耗审批人动态码，也不开会话。"""
    response = harness.client.post(
        f"{PREFIX}/ddl",
        json={"sql": "DROP TABLE LEGACY_TABLE", "totp_code": CODE},
    )
    assert response.status_code == 400
    assert harness.session.calls == []


def test_ddl_endpoint_protected_table_is_403(harness):
    response = harness.client.post(
        f"{PREFIX}/ddl",
        json={"sql": "TRUNCATE TABLE USER_TRASH", "confirm": "USER_TRASH", "totp_code": CODE},
    )
    assert response.status_code == 403
    assert harness.session.calls == []


def test_ddl_endpoint_success_writes_ddl_audit(harness):
    response = harness.client.post(
        f"{PREFIX}/ddl",
        json={
            "sql": "DROP TABLE LEGACY_TABLE",
            "confirm": "LEGACY_TABLE",
            "totp_code": CODE,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok" and payload["code"] == 200
    assert payload["data"] == {
        "rowcount": None,
        "committed": True,
        "summary": "DROP TABLE LEGACY_TABLE",
    }
    assert "LEGACY_TABLE" not in harness.db.tables

    audit = harness.audits[-1]
    assert audit["log_type"] == "system_db_ddl"
    assert audit["action"] == "db_ddl"
    assert "DROP TABLE LEGACY_TABLE" in audit["detail"]
    assert len(audit["detail"]) <= 500


def test_dml_via_ddl_endpoint_uses_dml_audit_type(harness):
    response = harness.client.post(
        f"{PREFIX}/ddl",
        json={
            "sql": "INSERT INTO LEGACY_TABLE (ID) VALUES (7)",
            "totp_code": CODE,
        },
    )
    assert response.status_code == 200
    assert response.json()["data"] == {
        "rowcount": 1,
        "committed": True,
        "summary": "INSERT INTO LEGACY_TABLE",
    }
    assert harness.audits[-1]["log_type"] == "system_db_dml"
    assert harness.audits[-1]["action"] == "db_dml"


def test_row_write_endpoints_write_dml_audit_without_values(harness):
    response = harness.client.post(
        f"{PREFIX}/table/USERS/rows",
        json={
            "values": {"OID": "0009", "NAME": "秘密内容"},
            "totp_code": CODE,
        },
    )
    assert response.status_code == 200
    assert response.json()["data"] == {"rowcount": 1}

    audit = harness.audits[-1]
    assert audit["log_type"] == "system_db_dml"
    assert audit["action"] == "db_dml"
    assert "秘密内容" not in audit["detail"]  # 审计绝不带列值
    assert "OID" in audit["detail"] and "NAME" in audit["detail"]


def test_update_endpoint_requires_full_key(harness):
    response = harness.client.put(
        f"{PREFIX}/table/USERS/rows",
        json={"key": {"NAME": "张三"}, "values": {"NAME": "x"}, "totp_code": CODE},
    )
    assert response.status_code == 400
    assert harness.db.tables["USERS"]["rows"][0]["NAME"] == "张三"


def test_delete_endpoint_removes_row(harness):
    response = harness.client.request(
        "DELETE",
        f"{PREFIX}/table/USERS/rows",
        json={"key": {"OID": "000000000000000000000001"}, "totp_code": CODE},
    )
    assert response.status_code == 200
    assert response.json()["data"] == {"rowcount": 1}
    assert len(harness.db.tables["USERS"]["rows"]) == 2


def test_row_write_on_protected_table_is_403_over_http(harness):
    response = harness.client.post(
        f"{PREFIX}/table/DUAL_AUTH_USES/rows",
        json={"values": {"OID": "u2"}, "totp_code": CODE},
    )
    assert response.status_code == 403
    assert harness.db.tables["DUAL_AUTH_USES"]["rows"] == [
        {"OID": "u1", "APPROVER_OID": "a1"}
    ]
    assert harness.audits == []


def test_schema_and_rows_endpoints_work(harness):
    schema = harness.client.get(f"{PREFIX}/schema")
    assert schema.status_code == 200
    assert schema.json()["data"]["total"] == 6

    rows = harness.client.get(f"{PREFIX}/table/USERS/rows", params={"limit": 2})
    assert rows.status_code == 200
    assert rows.json()["data"]["has_more"] is True


def test_real_dual_auth_accepts_another_admin_code(monkeypatch, fake_db):
    """接真实 ``require_dual_auth``：审批人动态码有效即放行（不需要 monkeypatch）。"""
    code, _ = get_current_totp(APPROVER)
    env = _build(monkeypatch, fake_db, real_dual_auth=True)

    response = env.client.post(
        f"{PREFIX}/table/USERS/rows",
        json={"values": {"OID": "0009"}, "totp_code": code},
    )
    assert response.status_code == 200
    assert env.db.tables["USERS"]["rows"][-1]["OID"] == "0009"


def test_real_dual_auth_rejects_operator_own_code(monkeypatch, fake_db):
    """用自己的码不能自我批准（契约 §1.2）。"""
    code, _ = get_current_totp(OPERATOR)
    env = _build(monkeypatch, fake_db, real_dual_auth=True)

    response = env.client.post(
        f"{PREFIX}/table/USERS/rows",
        json={"values": {"OID": "0009"}, "totp_code": code},
    )
    assert response.status_code == 403
    assert env.session.calls == []
