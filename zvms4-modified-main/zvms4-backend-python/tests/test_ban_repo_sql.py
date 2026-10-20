"""封禁数据层的离线契约（schema 形状 + 生成的 SQL），不连库。

覆盖三件服务层会依赖、但一旦写错就会静默出错的事：

* ``_user_doc`` 的封禁元数据**只在非 NULL 时出现**——永久封禁必须表现为
  ``"ban_until" not in user``，否则服务层无法区分「永久」与「已到期时间戳」。
* ``set_ban`` / ``clear_ban`` 是**一条** UPDATE：闸门 ``banned`` 与四列元数据
  必须同一语句落下，并且照旧轮换 ``session_id``（旧 token 立即失效）。这里断言
  编译后的 SQL 列集合与绑定值，而不是手写 SQL 子串。
* ``count_distinct_xuehai`` 的谓词必须排除 Blank 设备号。**不能**写
  ``xuehai_id != ''``：Oracle 把空串当 NULL，``col != NULL`` 恒为 NULL，会把
  整张表过滤成 0 行；实现用 ``length(trim(col)) > 0``，这里把这条钉住。

Nothing is executed: statements are compiled against the Oracle dialect with
values inlined, no engine, no connection.

Run from the repository root::

    ./.venv/bin/python -m pytest tests/test_ban_repo_sql.py -q
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.dialects import oracle

from db.repos.oracle import app_logs as app_logs_repo
from db.repos.oracle import ban_records as ban_records_repo
from db.repos.oracle import ban_rules as ban_rules_repo
from db.repos.oracle import users as users_repo

USER_OID = "000000000000000000000002"
OTHER_OID = "000000000000000000000003"
UNTIL = datetime(2026, 11, 1, 0, 0, 0)

DIALECT = oracle.dialect()


def _sql(stmt) -> str:
    """Compile with bound values inlined; whitespace collapsed for matching."""
    return " ".join(
        str(stmt.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True})).split()
    )


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
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# _user_doc: 四个新字段仅在非 NULL 时出现
# ---------------------------------------------------------------------------

def test_user_doc_fills_ban_metadata_when_present():
    doc = users_repo._user_doc(
        _mapping(
            banned=1,
            ban_reason="同学号多设备登录：3 台设备",
            ban_until=UNTIL,
            ban_source="rule",
            ban_rule="multi_device_login",
        )
    )
    assert doc["banned"] is True
    assert doc["ban_reason"] == "同学号多设备登录：3 台设备"
    assert doc["ban_until"] == UNTIL
    assert doc["ban_source"] == "rule"
    assert doc["ban_rule"] == "multi_device_login"


def test_user_doc_omits_ban_metadata_for_permanent_ban():
    """永久封禁 = banned 存在、ban_until 不存在（Mongo 的「字段缺失」语义）。"""
    doc = users_repo._user_doc(
        _mapping(banned=1, ban_reason="", ban_source="admin")
    )
    assert doc["banned"] is True
    assert doc["ban_source"] == "admin"
    assert "ban_until" not in doc
    assert "ban_rule" not in doc


def test_user_doc_has_no_ban_keys_for_clean_user():
    doc = users_repo._user_doc(_mapping())
    for key in ("banned", "ban_reason", "ban_until", "ban_source", "ban_rule"):
        assert key not in doc


# ---------------------------------------------------------------------------
# set_ban / clear_ban: 一条 UPDATE，含会话轮换
# ---------------------------------------------------------------------------

def test_set_ban_stmt_is_one_update_with_metadata_and_session_rotation():
    sql = _sql(
        users_repo._set_ban_stmt(
            USER_OID, "多设备登录", UNTIL, "rule", "multi_device_login"
        )
    )
    assert sql.startswith("UPDATE users SET ")
    assert "banned=1" in sql
    assert "ban_reason=" in sql
    assert "ban_until=" in sql
    assert "ban_source=" in sql
    assert "ban_rule=" in sql
    assert "session_id=" in sql
    assert f"WHERE users.oid = '{USER_OID}'" in sql
    # 值必须落在各自列上，不能错位
    assert "'多设备登录'" in sql
    assert "'rule'" in sql
    assert "'multi_device_login'" in sql
    assert "2026-11-01" in sql


def test_set_ban_stmt_leaves_ban_until_null_for_permanent_ban():
    sql = _sql(
        users_repo._set_ban_stmt(USER_OID, "违规", None, "admin", None)
    )
    assert "banned=1" in sql
    assert "ban_until=NULL" in sql
    assert "ban_rule=NULL" in sql
    assert "session_id=" in sql


def test_clear_ban_stmt_nulls_metadata_and_rotates_session():
    sql = _sql(users_repo._clear_ban_stmt(USER_OID))
    assert sql.startswith("UPDATE users SET ")
    assert "banned=0" in sql
    for col in ("ban_reason", "ban_until", "ban_source", "ban_rule"):
        assert f"{col}=NULL" in sql
    assert "session_id=" in sql
    assert f"WHERE users.oid = '{USER_OID}'" in sql


def test_set_banned_compat_shim_forwards_to_new_writers(monkeypatch):
    """旧接口 ``set_banned(oid, 1/0)`` 必须落到新语句上，而不是另开一条路。"""
    seen: list[str] = []

    class FakeSession:
        async def execute(self, stmt):
            seen.append(_sql(stmt))

        async def commit(self):
            seen.append("COMMIT")

    async def _run(banned: int) -> list[str]:
        repo = users_repo.OracleUsersRepo(session=FakeSession())
        await repo.set_banned(USER_OID, banned)
        return seen

    import asyncio

    seen.clear()
    asyncio.run(_run(1))
    assert any("banned=1" in s for s in seen)
    assert any("ban_source='admin'" in s for s in seen)
    assert seen[-1] != "COMMIT"  # 注入的 session 由调用方提交

    seen.clear()
    asyncio.run(_run(0))
    assert any("banned=0" in s for s in seen)
    assert any("ban_until=NULL" in s for s in seen)


# ---------------------------------------------------------------------------
# count_distinct_xuehai: DISTINCT + 时间窗 + 非空设备号
# ---------------------------------------------------------------------------

def test_count_distinct_xuehai_stmt_filters_blank_and_counts_distinct():
    since = 1760000000.0
    sql = _sql(app_logs_repo._count_distinct_xuehai_stmt(USER_OID, since))
    assert "count(DISTINCT app_logs.xuehai_id)" in sql
    assert f"app_logs.app_user = '{USER_OID}'" in sql
    assert "app_logs.ltype = 'user_login'" in sql
    # Oracle 的 '' 陷阱：必须用 length(trim(...)) > 0，绝不能出现 xuehai_id != ''
    assert "length(trim(app_logs.xuehai_id)) > 0" in sql
    assert "xuehai_id != ''" not in sql
    assert f"app_logs.ts_raw >= {since}" in sql


# ---------------------------------------------------------------------------
# BAN_RECORDS / BAN_RULES 语句形状
# ---------------------------------------------------------------------------

def test_ban_record_insert_binds_permanent_as_number():
    sql = _sql(
        ban_records_repo._insert_stmt(
            "0a00000000000000000000ff",
            USER_OID,
            "rule",
            "multi_device_login",
            "多设备",
            True,
            UNTIL,
            None,
            OTHER_OID,
        )
    )
    assert sql.startswith("INSERT INTO ban_records ")
    assert "permanent" in sql
    assert "'rule'" in sql
    assert "'multi_device_login'" in sql
    assert f"'{USER_OID}'" in sql
    assert f"'{OTHER_OID}'" in sql


def test_ban_record_count_is_scoped_to_user_and_rule():
    sql = _sql(ban_records_repo._count_for_user_rule_stmt(USER_OID, "multi_device_login"))
    assert "count(*)" in sql
    assert f"ban_records.user_oid = '{USER_OID}'" in sql
    assert "ban_records.rule_code = 'multi_device_login'" in sql


def test_ban_record_page_joins_users_and_counts_windowed_total():
    sql = _sql(ban_records_repo._page_stmt("", 1, 20))
    assert "LEFT OUTER JOIN users ON ban_records.user_oid = users.oid" in sql
    assert "users.name AS user_name" in sql
    assert "users.business_id AS user_business_id" in sql
    assert "count(*) OVER () AS total_count" in sql
    assert "ORDER BY ban_records.started_at DESC" in sql


def test_ban_rules_update_by_code_patches_only_given_fields():
    sql = _sql(
        ban_rules_repo._update_by_code_stmt("multi_device_login", {"enabled": 0}, UNTIL)
    )
    assert sql.startswith("UPDATE ban_rules SET ")
    assert "enabled=0" in sql
    assert "updated_at=" in sql
    assert "config=" not in sql
    assert "penalties=" not in sql
    assert "WHERE ban_rules.code = 'multi_device_login'" in sql


def test_ban_rules_row_maps_flags_to_bools():
    row = ban_rules_repo._rule_row(
        {
            "oid": "0b0000000000000000000001",
            "code": "multi_device_login",
            "name": "同学号多设备登录",
            "enabled": 1,
            "config": '{"threshold":3,"window_hours":24}',
            "penalties": "[1,2,7]",
            "builtin": 1,
            "created_at": UNTIL,
            "updated_at": UNTIL,
        }
    )
    assert row["_id"] == "0b0000000000000000000001"
    assert row["enabled"] is True
    assert row["builtin"] is True
    # JSON 保持文本：解析是服务层的决定，数据层不猜每类规则的 schema
    assert row["config"] == '{"threshold":3,"window_hours":24}'
    assert row["penalties"] == "[1,2,7]"


def test_ban_records_row_maps_permanent_and_joined_columns():
    row = ban_records_repo._record_row(
        {
            "oid": "0c00000000000000000000ff",
            "user_oid": USER_OID,
            "source": "admin",
            "rule_code": None,
            "reason": "违规",
            "permanent": 1,
            "started_at": UNTIL,
            "expires_at": None,
            "operator_oid": OTHER_OID,
            "created_at": UNTIL,
            "user_name": "李四",
            "user_business_id": "2026002",
            "operator_name": "管理员",
        }
    )
    assert row["permanent"] is True
    assert row["user_name"] == "李四"
    assert row["user_business_id"] == "2026002"
    # 操作人姓名来自第二个 LEFT JOIN（USERS 别名 op）；规则封禁时为 None。
    assert row["operator_name"] == "管理员"
    assert row["expires_at"] is None


@pytest.mark.parametrize("column", ["ban_reason", "ban_until", "ban_source", "ban_rule"])
def test_user_columns_project_the_new_ban_columns(column):
    names = [col.name for col in users_repo._user_columns()]
    assert column in names
