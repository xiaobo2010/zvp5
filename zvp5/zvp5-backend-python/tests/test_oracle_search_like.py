"""关键字匹配的统一口径：``LIKE``（转义通配符）而不是正则引擎。

两个契约：

* :func:`db.repos.oracle.search.like_pattern` 是**唯一**实现（以前 times /
  app_logs / device_bans 各抄一份），语义是「转义 ``%``/``_``/``\\`` + 小写 +
  两头加 ``%``」；
* 所有 Oracle 仓储的关键字谓词编译后都不再出现 ``regexp_like``——正则禁索引，
  跨洋 ADB 上就是全表扫描。

离线用例：语句只编译不执行。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects import oracle

from db.models import users as users_tbl
from db.repos.oracle import activities as activities_repo
from db.repos.oracle import app_logs as app_logs_repo
from db.repos.oracle import ban_records as ban_records_repo
from db.repos.oracle import groups as groups_repo
from db.repos.oracle import members as members_repo
from db.repos.oracle import times as times_repo
from db.repos.oracle import users as users_repo
from db.repos.oracle import violations as violations_repo
from db.repos.oracle.search import (
    LIKE_ESCAPE,
    SEARCH_QUERY_MAX,
    like_pattern,
    like_prefix_pattern,
)

DIALECT = oracle.dialect()
CLASS_OID = "000000000000000000000001"
USER_OID = "000000000000000000000002"


def _compile(stmt) -> str:
    return " ".join(
        str(
            stmt.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True})
        ).split()
    )


def _conds_sql(conds) -> str:
    stmt = select(func.count()).select_from(users_tbl).where(*conds)
    return _compile(stmt)


# ---------------------------------------------------------------------------
# like_pattern / like_prefix_pattern
# ---------------------------------------------------------------------------

def test_pattern_escapes_wildcards_and_lower_cases():
    assert like_pattern("LiSi") == "%lisi%"
    assert like_pattern("100%_a\\b") == "%100\\%\\_a\\\\b%"


def test_prefix_pattern_keeps_the_anchor():
    assert like_prefix_pattern("2026") == "2026%"
    assert like_prefix_pattern("20%_6") == "20\\%\\_6%"


def test_escape_character_matches_the_sql_clause():
    assert LIKE_ESCAPE == "\\"


def test_query_max_is_shared():
    assert SEARCH_QUERY_MAX == times_repo._SEARCH_QUERY_MAX


# ---------------------------------------------------------------------------
# 各仓储的关键字谓词
# ---------------------------------------------------------------------------

def test_users_filter_is_literal_like():
    sql = _conds_sql(users_repo._user_filter("李四", ""))
    assert "regexp_like" not in sql.lower()
    assert "lower(users.name) LIKE '%李四%' ESCAPE '\\'" in sql
    assert "lower(users.business_id) LIKE '%李四%' ESCAPE '\\'" in sql


def test_id_prefix_uses_a_prefix_like_not_a_regex():
    sql = _compile(users_repo._list_by_id_prefix_stmt("2026"))
    assert "regexp_like" not in sql.lower()
    assert "lower(users.business_id) LIKE '2026%' ESCAPE '\\'" in sql


def test_name_or_id_lookup_is_literal_like():
    sql = _compile(users_repo._list_by_name_or_id_stmt("a.b*"))
    assert "regexp_like" not in sql.lower()
    assert "'%a.b*%'" in sql


def test_members_activity_page_is_literal_like():
    sql = _compile(members_repo._list_for_activity_stmt("x", "李四", 1, 10))
    assert "regexp_like" not in sql.lower()
    assert "lower(users.name) LIKE '%李四%' ESCAPE '\\'" in sql


def test_members_user_activity_page_is_literal_like():
    sql = _compile(members_repo._list_for_user_activities_query(USER_OID, "100%"))
    assert "regexp_like" not in sql.lower()
    assert "'%100\\%%'" in sql


def test_groups_class_list_is_literal_like():
    sql = _compile(groups_repo._list_classes_stmt("李四", 1, 10))
    assert "regexp_like" not in sql.lower()
    assert "lower(groups.name) LIKE '%李四%' ESCAPE '\\'" in sql


def test_groups_members_page_is_literal_like():
    sql = _compile(groups_repo._members_page_stmt(CLASS_OID, "李四", 1, 10))
    assert "regexp_like" not in sql.lower()
    assert "lower(users.name) LIKE '%李四%' ESCAPE '\\'" in sql


def test_groups_activity_page_is_literal_like():
    sql = _compile(groups_repo._group_activities_stmt(CLASS_OID, "李四", "", False, 1, 10))
    assert "regexp_like" not in sql.lower()
    assert "lower(activities.name) LIKE '%李四%' ESCAPE '\\'" in sql


def test_groups_permission_filter_is_literal_like():
    sql = _conds_sql(groups_repo._permission_filter("李四", None))
    assert "regexp_like" not in sql.lower()
    assert "lower(groups.name) LIKE '%李四%' ESCAPE '\\'" in sql


def test_activities_filter_is_literal_like():
    sql = _conds_sql(activities_repo._activity_filter("a.b*", None, None))
    assert "regexp_like" not in sql.lower()
    assert "lower(activities.name) LIKE '%a.b*%' ESCAPE '\\'" in sql


def test_activities_by_oids_still_filters_on_name():
    stmt = activities_repo._list_by_oids_stmt([CLASS_OID], "李四", -1, 10)
    sql = _compile(stmt)
    assert "regexp_like" not in sql.lower()
    assert "lower(activities.name) LIKE '%李四%' ESCAPE '\\'" in sql


def test_violations_page_filter_is_literal_like():
    sql = _conds_sql(violations_repo._page_filter("李四"))
    assert "regexp_like" not in sql.lower()
    assert "lower(users.name) LIKE '%李四%' ESCAPE '\\'" in sql


def test_ban_records_page_filter_is_literal_like():
    sql = _conds_sql(ban_records_repo._page_filter("李四"))
    assert "regexp_like" not in sql.lower()
    assert "lower(users.name) LIKE '%李四%' ESCAPE '\\'" in sql


def test_app_logs_search_is_literal_like():
    sql = _compile(
        app_logs_repo._list_stmt("李四", "", None, None, None, None, 1, 10)
    )
    assert "regexp_like" not in sql.lower()
    assert "lower(app_logs.url) LIKE '%李四%' ESCAPE '\\'" in sql


def test_times_class_search_is_literal_like():
    sql = _conds_sql(times_repo._class_conds(CLASS_OID, "李四"))
    assert "regexp_like" not in sql.lower()
    assert "lower(users.name) LIKE '%李四%' ESCAPE '\\'" in sql
