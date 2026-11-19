"""/api/users 的关键字搜索必须是**字面量** LIKE，不能把原始输入送进正则引擎。

该入口没有 ``regex`` 开关，``query`` 就是用户键入的关键字：正则引擎会在每行上
编译模式（``a.b*`` 这类输入既可能触发回溯，也会让搜索结果偏离字面量语义），
而 ``LOWER(col) LIKE`` 是普通字符串比较，还能吃到函数索引。``%``/``_``/``\\``
转义后保持字面量，多个关键词仍是 AND 关系。
"""

from sqlalchemy.dialects import oracle

from db.repos.oracle.users import _search_filter, _search_page_stmt


def _sql(query: str, logged_in: bool = True) -> str:
    dialect = oracle.dialect()
    return " ".join(
        str(cond.compile(dialect=dialect, compile_kwargs={"literal_binds": True}))
        for cond in _search_filter(query, logged_in)
    )


def test_regex_metacharacters_are_plain_literals():
    sql = _sql("a.b*")

    assert "regexp_like" not in sql.lower()
    assert "lower(users.name) LIKE '%a.b*%' ESCAPE '\\'" in sql
    assert "lower(users.business_id) LIKE '%a.b*%' ESCAPE '\\'" in sql


def test_like_wildcards_in_the_keyword_are_escaped():
    sql = _sql("100%_a\\b")

    assert "'%100\\%\\_a\\\\b%'" in sql
    assert "'%100%'" not in sql


def test_plain_keyword_keeps_the_same_sql_shape():
    sql = _sql("2026")

    assert "lower(users.name) LIKE '%2026%' ESCAPE '\\'" in sql
    assert "regexp_like" not in sql.lower()


def test_past_array_branch_is_literal_too():
    sql = _sql("张三")

    assert "LOWER(JSON_SERIALIZE(users.past)) LIKE '%张三%' ESCAPE '\\'" in sql


def test_unlogged_in_search_stays_an_exact_match():
    sql = _sql("a.b*", logged_in=False)

    assert "users.business_id = 'a.b*'" in sql
    assert "regexp_like" not in sql.lower()
    assert "like" not in sql.lower()


def _page_sql(query: str, logged_in: bool = True) -> str:
    stmt = _search_page_stmt(query, logged_in, 10, 20)
    return str(
        stmt.compile(dialect=oracle.dialect(), compile_kwargs={"literal_binds": True})
    )


def test_empty_query_has_no_predicate_at_all():
    """空关键字 = 无谓词：不拼 REGEXP_LIKE，更不碰 past 这个 CLOB。"""
    assert _sql("", logged_in=True) == ""

    sql = _page_sql("")
    assert "regexp_like" not in sql.lower()
    assert "json_serialize" not in sql.lower()
    assert "users.past" not in sql
    assert "where" not in sql.lower()


def test_whitespace_only_query_is_treated_as_empty():
    assert _sql("   ", logged_in=True) == ""
    assert "regexp_like" not in _page_sql("   ").lower()


def test_unlogged_in_empty_query_keeps_the_exact_match_shape():
    """匿名空关键字仍是「学号 = 空串」，不会退化成全表可见。"""
    sql = _sql("", logged_in=False)

    assert "regexp_like" not in sql.lower()
    assert "json_serialize" not in sql.lower()
    assert "business_id" in sql
