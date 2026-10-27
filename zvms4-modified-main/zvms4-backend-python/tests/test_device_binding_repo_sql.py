"""设备绑定/临时密码数据层的离线契约（schema 形状 + 生成的 SQL），不连库。

覆盖服务层会依赖、一旦写错就会静默出错的几件事：

* ``USERS.PRIMARY_XUEHAI_ID`` 必须在 ``_user_columns()`` 投影里、``_user_doc()``
  **只在非 NULL 时**输出——「未绑定」必须表现为字段缺失（``"primary_xuehai_id"
  in user`` 才有意义），跟 ``ban_until`` 的 Mongo 语义一致。
* ``set/clear_primary_xuehai`` 是**不带** ``session_id`` 赋值的 UPDATE：主设备
  不是身份凭证，换设备不该把账号在所有端踢下线。这条断言比「值写对了」更重
  要，所以直接钉编译后的 SET 列集合。
* ``list_banned_by_xuehai`` 只做 ``primary_xuehai_id = ? AND banned = 1`` 的原始
  筛选，「此刻是否生效」留给 ``util.ban.ban_is_active``。这里确认 SQL 里**没有**
  ``ban_until`` 比较（有的话就是第二套判定口径）。
* 三张新表的语句形状：设备封禁的 upsert 覆盖全列、临时密码的活跃过滤三条件齐
  全（未用/未撤销/未过期）+ ``created_at DESC``、挑战的 ``SELECT ... FOR UPDATE``
  与 ``attempts + 1``。
* ``V014`` 与 ``db/models.py`` 的列集合/索引一致——``db.ddl_check`` 只比对 V001，
  V014 是存量实例的补丁，它漂了不会有人报错，只能靠这里钉住。

Nothing is executed: statements are compiled against the Oracle dialect with
values inlined, no engine, no connection.

Run from the repository root::

    ./.venv/bin/python -m pytest tests/test_device_binding_repo_sql.py -q
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.dialects import oracle

from db import backends, ddl_check, models
from db.models import device_challenges as device_challenges_tbl
from db.models import temp_login_passwords as temp_passwords_tbl
from db.repos.oracle import device_bans as device_bans_repo
from db.repos.oracle import device_challenges as device_challenges_repo
from db.repos.oracle import temp_passwords as temp_passwords_repo
from db.repos.oracle import users as users_repo

USER_OID = "000000000000000000000002"
CREATOR_OID = "000000000000000000000003"
XUEHAI_ID = "2026000001"
UNTIL = datetime(2026, 11, 1, 0, 0, 0)
EXPIRES = datetime(2026, 10, 21, 12, 30, 0)

DIALECT = oracle.dialect()

V014_PATH = (
    Path(__file__).resolve().parents[1]
    / "db"
    / "oracle"
    / "ddl"
    / "V014__device_binding_and_temp_password.sql"
)


def _sql(stmt) -> str:
    """Compile with bound values inlined; whitespace collapsed for matching."""
    return " ".join(
        str(stmt.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True})).split()
    )


def _set_columns(stmt) -> set[str]:
    """The column names on the SET side of an UPDATE (kwargs/Column both allowed)."""
    return {
        (key if isinstance(key, str) else key.name).lower() for key in stmt._values
    }


def _where(stmt) -> str:
    """The compiled WHERE-tail of a statement (projection columns excluded)."""
    sql = _sql(stmt)
    return sql.split(" WHERE ", 1)[1] if " WHERE " in sql else ""


def _mapping(**overrides) -> dict:
    """``_user_columns()`` 投影出来的一行（未覆盖的列一律 NULL/假值）。"""
    base = {
        "oid": USER_OID,
        "business_id": "2026002",
        "name": "李四",
        "roles": ["student"],
        "class_oid": None,
        "password_hash": "x",
        "session_id": None,
        "sex": None,
        "eligibility": None,
        "past": None,
        "banned": 0,
        "ban_reason": None,
        "ban_until": None,
        "ban_source": None,
        "ban_rule": None,
        "primary_xuehai_id": None,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# USERS: 投影 + 文档形状
# ---------------------------------------------------------------------------

def test_user_columns_project_primary_xuehai_id():
    names = [col.name for col in users_repo._user_columns()]
    assert "primary_xuehai_id" in names


def test_user_doc_emits_primary_xuehai_only_when_bound():
    bound = users_repo._user_doc(_mapping(primary_xuehai_id=XUEHAI_ID))
    assert bound["primary_xuehai_id"] == XUEHAI_ID

    unbound = users_repo._user_doc(_mapping())
    assert "primary_xuehai_id" not in unbound


# ---------------------------------------------------------------------------
# USERS: set / clear 不轮换会话
# ---------------------------------------------------------------------------

def test_set_primary_xuehai_updates_one_column_without_session_rotation():
    stmt = users_repo._set_primary_xuehai_stmt(USER_OID, XUEHAI_ID)
    sql = _sql(stmt)
    assert sql.startswith("UPDATE users SET ")
    assert _set_columns(stmt) == {"primary_xuehai_id"}
    assert f"WHERE users.oid = '{USER_OID}'" in sql
    assert "'2026000001'" in sql
    assert "session_id" not in sql


def test_clear_primary_xuehai_nulls_the_column_without_session_rotation():
    stmt = users_repo._clear_primary_xuehai_stmt(USER_OID)
    sql = _sql(stmt)
    assert sql.startswith("UPDATE users SET ")
    assert _set_columns(stmt) == {"primary_xuehai_id"}
    assert "primary_xuehai_id=NULL" in sql
    assert "session_id" not in sql


# ---------------------------------------------------------------------------
# USERS: list_banned_by_xuehai 只做原始筛选
# ---------------------------------------------------------------------------

def test_list_banned_by_xuehai_filters_primary_and_banned_only():
    stmt = users_repo._list_banned_by_xuehai_stmt(XUEHAI_ID)
    sql = _sql(stmt)
    where = _where(stmt)
    assert "FROM users" in sql
    assert f"users.primary_xuehai_id = '{XUEHAI_ID}'" in where
    assert "users.banned = 1" in where
    # 「此刻是否生效」是服务层的判定（util.ban.ban_is_active），SQL 里不许出现
    # 第二套时间口径。（投影里的 ban_until 只是列名，不参与筛选。）
    assert "ban_until" not in where


def test_list_banned_by_xuehai_returns_full_user_docs():
    class FakeResult:
        def all(self):
            return [type("R", (), {"_mapping": _mapping(primary_xuehai_id=XUEHAI_ID)})()]

    seen: list[str] = []

    class FakeSession:
        async def execute(self, stmt):
            seen.append(_sql(stmt))
            return FakeResult()

    import asyncio

    repo = users_repo.OracleUsersRepo(session=FakeSession())
    docs = asyncio.run(repo.list_banned_by_xuehai(XUEHAI_ID))
    assert len(docs) == 1
    assert docs[0]["_id"] == USER_OID
    assert docs[0]["primary_xuehai_id"] == XUEHAI_ID
    assert seen  # 语句确实下发过


# ---------------------------------------------------------------------------
# BANNED_DEVICES
# ---------------------------------------------------------------------------

def test_device_ban_upsert_covers_every_writable_column():
    sql = _sql(
        device_bans_repo._update_stmt(
            XUEHAI_ID, "连错临时密码 3 次", None, "rule",
            "foreign_device_login", CREATOR_OID, UNTIL,
        )
    )
    assert sql.startswith("UPDATE banned_devices SET ")
    for col in ("reason", "until", "source", "rule_code", "operator_oid", "created_at"):
        assert f"{col}=" in sql
    assert "until=NULL" in sql  # 永久封禁
    assert "'rule'" in sql
    assert "'foreign_device_login'" in sql
    assert f"WHERE banned_devices.xuehai_id = '{XUEHAI_ID}'" in sql


def test_device_ban_insert_keeps_null_until_semantics():
    stmt = device_bans_repo._insert_stmt(
        XUEHAI_ID, None, None, "admin", None, CREATOR_OID, UNTIL
    )
    sql = _sql(stmt)
    assert sql.startswith("INSERT INTO banned_devices ")
    assert "until" in sql
    provided = {(k if isinstance(k, str) else k.name).lower() for k in stmt._values}
    required = {
        col.name.lower()
        for col in device_bans_repo.banned_devices_tbl.c
        if not col.nullable and col.server_default is None
    }
    assert required - provided == set()


def test_device_ban_get_and_delete_are_keyed_by_xuehai_id():
    get_sql = _sql(device_bans_repo._get_stmt(XUEHAI_ID))
    assert "FROM banned_devices" in get_sql
    assert f"banned_devices.xuehai_id = '{XUEHAI_ID}'" in get_sql

    delete_sql = _sql(device_bans_repo._delete_stmt(XUEHAI_ID))
    assert delete_sql.startswith("DELETE FROM banned_devices ")
    assert f"banned_devices.xuehai_id = '{XUEHAI_ID}'" in delete_sql


def test_device_ban_page_counts_window_and_orders_newest_first():
    sql = _sql(device_bans_repo._page_stmt(2, 20))
    assert "count(*) OVER () AS total_count" in sql
    assert "ORDER BY banned_devices.created_at DESC" in sql
    assert "OFFSET 20 ROWS" in sql


def test_device_ban_row_has_no_oid_and_keeps_null_until():
    row = device_bans_repo._ban_row(
        {
            "xuehai_id": XUEHAI_ID,
            "reason": None,
            "until": None,
            "source": "rule",
            "rule_code": "foreign_device_login",
            "operator_oid": None,
            "created_at": UNTIL,
        }
    )
    assert "_id" not in row  # 设备号本身就是主键/标识
    assert row["xuehai_id"] == XUEHAI_ID
    assert row["until"] is None  # NULL = 永久
    assert row["source"] == "rule"


# ---------------------------------------------------------------------------
# TEMP_LOGIN_PASSWORDS
# ---------------------------------------------------------------------------

def test_temp_password_insert_covers_every_not_null_column():
    stmt = temp_passwords_repo._insert_stmt(
        "0d00000000000000000000ff",
        USER_OID,
        CREATOR_OID,
        "$2b$12$hash",
        XUEHAI_ID,
        EXPIRES,
        UNTIL,
    )
    sql = _sql(stmt)
    assert sql.startswith("INSERT INTO temp_login_passwords ")
    assert "'$2b$12$hash'" in sql
    provided = {(k if isinstance(k, str) else k.name).lower() for k in stmt._values}
    required = {
        col.name.lower()
        for col in temp_passwords_tbl.c
        if not col.nullable and col.server_default is None
    }
    assert required - provided == set()
    # used_at / revoked_at 显式写 NULL：新建的码既没用过也没撤销。
    assert {"used_at", "revoked_at"} <= provided


def test_temp_password_active_filter_is_unused_unrevoked_and_unexpired():
    stmt = temp_passwords_repo._list_active_stmt(USER_OID, EXPIRES, 10)
    sql = _sql(stmt)
    assert f"temp_login_passwords.target_user_oid = '{USER_OID}'" in sql
    assert "temp_login_passwords.used_at IS NULL" in sql
    assert "temp_login_passwords.revoked_at IS NULL" in sql
    assert (
        "temp_login_passwords.expires_at > "
        "TO_DATE('2026-10-21 12:30:00', 'YYYY-MM-DD HH24:MI:SS')"
    ) in sql
    # 新的在前：管理员先看到最近签发的那张
    assert "ORDER BY temp_login_passwords.created_at DESC" in sql
    assert "FETCH FIRST 10 ROWS ONLY" in sql


def test_temp_password_mark_used_is_guarded_by_used_at_null():
    sql = _sql(temp_passwords_repo._mark_used_stmt(USER_OID, UNTIL))
    assert sql.startswith("UPDATE temp_login_passwords SET ")
    assert "used_at=TO_DATE('2026-11-01 00:00:00', 'YYYY-MM-DD HH24:MI:SS')" in sql
    assert f"temp_login_passwords.oid = '{USER_OID}'" in sql
    # 守卫：已用过的码不能被改成「刚用过」（否则并发两次都能成功）
    assert "temp_login_passwords.used_at IS NULL" in sql


def test_temp_password_revoke_expired_skips_used_rows():
    sql = _sql(temp_passwords_repo._revoke_expired_stmt(EXPIRES, UNTIL))
    assert "revoked_at=TO_DATE('2026-11-01 00:00:00', 'YYYY-MM-DD HH24:MI:SS')" in sql
    assert "temp_login_passwords.used_at IS NULL" in sql
    assert "temp_login_passwords.revoked_at IS NULL" in sql
    assert "temp_login_passwords.expires_at < TO_DATE('2026-10-21 12:30:00', 'YYYY-MM-DD HH24:MI:SS')" in sql


def test_temp_password_revoke_open_for_user_replaces_previous_codes():
    """签发新码前的作废：只碰该用户「仍可用」的码，且写 used_at（被取代）。"""
    sql = _sql(temp_passwords_repo._revoke_open_for_user_stmt(USER_OID, UNTIL))

    # 生产 ADB 会话 pdml_enabled=YES：不串行化，同事务的 INSERT 会撞 ORA-12838。
    assert sql.startswith("UPDATE /*+ NO_PARALLEL */ temp_login_passwords SET ")
    assert "used_at=TO_DATE('2026-11-01 00:00:00', 'YYYY-MM-DD HH24:MI:SS')" in sql
    assert f"temp_login_passwords.target_user_oid = '{USER_OID}'" in sql
    assert "temp_login_passwords.used_at IS NULL" in sql
    assert "temp_login_passwords.revoked_at IS NULL" in sql
    assert (
        "temp_login_passwords.expires_at > "
        "TO_DATE('2026-11-01 00:00:00', 'YYYY-MM-DD HH24:MI:SS')"
    ) in sql
    # 过期码由 revoke_expired 统一处理，这里不改写它们的时间戳。
    assert "revoked_at=" not in sql


def test_temp_password_row_maps_hex_id():
    row = temp_passwords_repo._password_row(
        {
            "oid": "0d00000000000000000000ff",
            "target_user_oid": USER_OID,
            "created_by_oid": CREATOR_OID,
            "code_hash": "$2b$12$hash",
            "creator_xuehai_id": None,
            "expires_at": EXPIRES,
            "used_at": None,
            "revoked_at": None,
            "created_at": UNTIL,
        }
    )
    assert row["_id"] == "0d00000000000000000000ff"
    assert row["creator_xuehai_id"] is None
    assert row["used_at"] is None


# ---------------------------------------------------------------------------
# DEVICE_CHALLENGES
# ---------------------------------------------------------------------------

def test_device_challenge_get_does_not_lock_the_row():
    sql = _sql(device_challenges_repo._get_stmt("a" * 64))
    assert "FROM device_challenges" in sql
    assert f"device_challenges.token_hash = '{'a' * 64}'" in sql
    # 并发安全由 ``UPDATE attempts = attempts + 1`` 的语句级原子性保证。
    # 刻意不加 FOR UPDATE：仓库自持的 session 一返回就关，锁也一起放掉，是假锁。
    assert "FOR UPDATE" not in sql


def test_device_challenge_increment_is_relative_not_absolute():
    sql = _sql(device_challenges_repo._increment_stmt(USER_OID))
    assert sql.startswith("UPDATE device_challenges SET ")
    assert "attempts=(device_challenges.attempts + 1)" in sql
    assert f"WHERE device_challenges.oid = '{USER_OID}'" in sql


def test_device_challenge_insert_starts_attempts_at_zero():
    stmt = device_challenges_repo._insert_stmt(
        "0e00000000000000000000ff",
        "b" * 64,
        USER_OID,
        XUEHAI_ID,
        EXPIRES,
        UNTIL,
    )
    sql = _sql(stmt)
    assert sql.startswith("INSERT INTO device_challenges ")
    assert "attempts" in sql
    provided = {(k if isinstance(k, str) else k.name).lower() for k in stmt._values}
    required = {
        col.name.lower()
        for col in device_challenges_tbl.c
        if not col.nullable and col.server_default is None
    }
    assert required - provided == set()
    assert "attempts=0" not in sql  # INSERT 用 VALUES 列表，不是 SET 语法
    assert ", 0, TO_DATE('2026-10-21 12:30:00'" in sql
    assert "consumed_at" in provided
    assert ", NULL, TO_DATE('2026-11-01 00:00:00'" in sql
    # 存的是哈希，明文 token 不该出现在任何一列里
    assert "'" + "b" * 64 + "'" in sql


def test_device_challenge_consume_stamps_consumed_at():
    sql = _sql(device_challenges_repo._consume_stmt(USER_OID, UNTIL))
    assert "consumed_at=TO_DATE('2026-11-01 00:00:00', 'YYYY-MM-DD HH24:MI:SS')" in sql
    assert f"WHERE device_challenges.oid = '{USER_OID}'" in sql


def test_device_challenge_row_maps_attempts_to_int():
    row = device_challenges_repo._challenge_row(
        {
            "oid": "0e00000000000000000000ff",
            "token_hash": "b" * 64,
            "user_oid": USER_OID,
            "xuehai_id": XUEHAI_ID,
            "attempts": 2,
            "expires_at": EXPIRES,
            "consumed_at": None,
            "created_at": UNTIL,
        }
    )
    assert row["attempts"] == 2
    assert isinstance(row["attempts"], int)
    assert row["consumed_at"] is None


# ---------------------------------------------------------------------------
# V014 ↔ db/models.py 一致性
# ---------------------------------------------------------------------------

def _v014_schema() -> dict:
    text = V014_PATH.read_text(encoding="utf-8")
    return ddl_check.parse_schema(ddl_check.extract_execute_immediate(text))


def test_v014_creates_the_three_new_tables_with_the_modeled_columns():
    v014 = _v014_schema()["tables"]
    modeled = ddl_check.parse_models()["tables"]
    for name in ("temp_login_passwords", "device_challenges", "banned_devices"):
        assert name in v014, f"V014 缺少表 {name}"
        assert v014[name]["columns"] == modeled[name]["columns"], name
        assert v014[name]["pk"] == modeled[name]["pk"], name
        assert v014[name]["ck"] == modeled[name]["ck"], name


def test_device_challenge_attempts_column_is_wide_enough_to_count_past_ten():
    """第 10 次提交也要能落库：``NUMBER(1)`` 上限 9，写 10 就是 ORA-01438 → 500。

    ``db.ddl_check`` 只校验 V001，V014 是存量实例的补丁，两边都钉住。
    """
    modeled = ddl_check.parse_models()["tables"]["device_challenges"]["columns"][
        "attempts"
    ]
    v014 = _v014_schema()["tables"]["device_challenges"]["columns"]["attempts"]
    v001_text = ddl_check.DDL_PATH.read_text(encoding="utf-8")
    v001 = ddl_check.parse_schema(
        ddl_check.extract_execute_immediate(v001_text)
    )["tables"]["device_challenges"]["columns"]["attempts"]
    # 三处必须一致，且 3 位（上限 999）而不是 1 位（上限 9）
    assert v014 == modeled
    assert v001 == modeled
    assert modeled[0] == "number(3)"


def test_v014_adds_the_primary_xuehai_column_and_its_index():
    text = V014_PATH.read_text(encoding="utf-8")
    assert "ALTER TABLE users ADD primary_xuehai_id VARCHAR2(32 CHAR)" in text
    assert "CREATE INDEX ix_users_primary_xuehai ON users (primary_xuehai_id)" in text

    schema = _v014_schema()
    new_tables = {"temp_login_passwords", "device_challenges", "banned_devices"}
    modeled_indexes = ddl_check.parse_models()["indexes"]
    new_indexes = {idx for idx in modeled_indexes if idx[0] in new_tables}
    assert new_indexes
    # V014 只补它自己带来的索引：新表的索引必须一字不差地出现在补丁里。
    # V021 之后给 device_challenges 补了计数索引，不在 V014 的范围内。
    added_later = {("device_challenges", False, ("user_oid", "created_at"))}
    assert new_indexes - added_later == {
        idx for idx in schema["indexes"] if idx[0] in new_tables
    }
    assert ("users", False, ("primary_xuehai_id",)) in modeled_indexes


def test_v014_seeds_the_foreign_device_rule_without_colliding_with_v013():
    text = V014_PATH.read_text(encoding="utf-8")
    v013 = (
        V014_PATH.parent / "V013__add_ban_rules.sql"
    ).read_text(encoding="utf-8")

    assert "MERGE INTO ban_rules t" in text
    assert "t.code = ''foreign_device_login''" in text
    assert "''{\"max_attempts\":3}''" in text
    assert "''[1,3,7,14,30,null]''" in text
    assert "''foreign_device_login'', ''非主学海设备登录''" in text

    v013_oid = "0b0000000000000000000001"
    new_oid = "0b0000000000000000000002"
    assert new_oid in text
    assert v013_oid in v013
    assert new_oid not in v013  # 两个内置规则不能共用 oid（ban_rules 的主键）


def test_v014_swallows_the_idempotency_error_codes():
    """重复执行必须无害：列已存在吞 -1430，对象已存在吞 -955，索引吞 -1408。"""
    text = V014_PATH.read_text(encoding="utf-8")
    assert "SQLCODE != -1430" in text  # ALTER TABLE ADD 列
    assert "SQLCODE != -955" in text  # CREATE TABLE
    assert "SQLCODE NOT IN (-955, -1408)" in text  # CREATE INDEX
    # 建索引的块里不能只吞 -955：否则重跑时 ORA-01408 会让迁移中断
    index_blocks = text.split("EXECUTE IMMEDIATE 'CREATE")[1:]
    assert index_blocks, "V014 里应有 CREATE INDEX"
    for block in index_blocks:
        assert "-1408" in block.split("END;")[0]


# ---------------------------------------------------------------------------
# backends: 三个新工厂
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "factory, expected",
    [
        (backends.get_device_bans_repo, "OracleDeviceBansRepo"),
        (backends.get_temp_passwords_repo, "OracleTempPasswordsRepo"),
        (backends.get_device_challenges_repo, "OracleDeviceChallengesRepo"),
    ],
)
def test_backends_factories_return_oracle_implementations(factory, expected):
    repo = factory()
    assert type(repo).__name__ == expected


def test_models_metadata_carries_the_new_tables():
    for name in ("temp_login_passwords", "device_challenges", "banned_devices"):
        assert models.get_table(name) is not None
    assert "primary_xuehai_id" in models.users.c


def test_temp_password_default_ttl_matches_spec():
    """规格里的 30 分钟：测试用固定时间点，仅确认 timedelta 数学没写反。"""
    created = datetime(2026, 10, 21, 12, 0, 0)
    assert created + timedelta(minutes=30) == EXPIRES
