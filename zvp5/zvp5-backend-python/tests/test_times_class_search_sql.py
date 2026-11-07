"""班级时长列表的关键字检索必须是**字面量** LIKE（离线，不连库）。

``_class_conds`` 曾把用户输入原样交给 ``REGEXP_LIKE``：每个查询都是用户可控的
正则（灾难性回溯），且无法用普通索引。这里把替换后的契约钉死：

* 编译出的 SQL 里不再出现 ``regexp_like``，两支都走 ``LOWER(col) LIKE ... ESCAPE``；
* ``%`` / ``_`` / ``\\`` 被转义，搜什么就只匹配什么（不是通配、不是正则）；
* 关键词长度上限 :data:`db.repos.oracle.times._SEARCH_QUERY_MAX`，超长直接判空。

语句只编译不执行（Oracle 方言 + 字面量绑定），从仓库根运行::

    ./.venv/bin/python -m pytest tests/test_times_class_search_sql.py -q
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects import oracle

from db.models import users as users_tbl
from db.repos.oracle import times as times_repo

CLASS_OID = "000000000000000000000001"
DIALECT = oracle.dialect()


def _sql(conds) -> str:
    stmt = select(func.count()).select_from(users_tbl).where(*conds)
    return " ".join(
        str(stmt.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True})).split()
    )


def test_class_search_uses_literal_like_not_regexp() -> None:
    sql = _sql(times_repo._class_conds(CLASS_OID, "李四"))

    assert "regexp_like" not in sql.lower()
    assert "lower(users.name) LIKE '%李四%'" in sql
    assert "lower(users.business_id) LIKE '%李四%'" in sql
    assert sql.count("ESCAPE '\\'") == 2
    assert f"users.class_oid = '{CLASS_OID}'" in sql


def test_like_wildcards_in_the_keyword_stay_literal() -> None:
    sql = _sql(times_repo._class_conds(CLASS_OID, "100%_a\\b"))

    assert "'%100\\%\\_a\\\\b%'" in sql
    assert "%100%" not in sql


def test_keyword_is_lower_cased_for_the_lowered_column() -> None:
    sql = _sql(times_repo._class_conds(CLASS_OID, "LiSi"))

    assert "'%lisi%'" in sql


def test_query_at_the_length_limit_still_searches() -> None:
    query = "a" * times_repo._SEARCH_QUERY_MAX

    sql = _sql(times_repo._class_conds(CLASS_OID, query))

    assert "LIKE" in sql
    assert "0 = 1" not in sql


def test_overlong_query_matches_nothing() -> None:
    query = "a" * (times_repo._SEARCH_QUERY_MAX + 1)

    sql = _sql(times_repo._class_conds(CLASS_OID, query))

    assert "0 = 1" in sql
    assert "LIKE" not in sql


def test_empty_query_only_filters_by_class() -> None:
    sql = _sql(times_repo._class_conds(CLASS_OID, ""))

    assert "LIKE" not in sql
    assert f"users.class_oid = '{CLASS_OID}'" in sql
