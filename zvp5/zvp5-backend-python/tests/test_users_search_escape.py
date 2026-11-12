"""/api/users 的关键字搜索必须是**字面量** LIKE，不能把原始输入送进正则引擎。

该入口没有 ``regex`` 开关，``query`` 就是用户键入的关键字：正则引擎会在每行上
编译模式（``a.b*`` 这类输入既可能触发回溯，也会让搜索结果偏离字面量语义），
而 ``LOWER(col) LIKE`` 是普通字符串比较，还能吃到函数索引。``%``/``_``/``\\``
转义后保持字面量，多个关键词仍是 AND 关系。
"""

from sqlalchemy.dialects import oracle

from db.repos.oracle.users import _search_filter


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
