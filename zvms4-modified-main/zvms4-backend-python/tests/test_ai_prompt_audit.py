"""``AI_PROMPT_AUDIT`` 仓储的过滤条件与 SQL 形状（离线，不连库）。

审阅列表这几条新增过滤有两个「单测能过、生产静默错」的点：

* ``keyword`` 必须落在 **WHERE**（``DBMS_LOB.INSTR``），不能把正文拉进内存再筛：
  后者会让列表重新变成「翻一页读几十段对话」。
* ``days`` 与显式时间范围同时在时，范围说了算；仓储口径上 ``since``/``time_start``
  取更紧的一侧、``until``/``time_end`` 取更紧的一侧（交集），不会因为多传一个
  参数反而放宽。

编译时把绑定值内联（``literal_binds``），断言的是实际会发给 Oracle 的文本。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.dialects import oracle

from db.repos.oracle import ai_prompt_audit as prompt_repo

DIALECT = oracle.dialect()
USER_OID = "000000000000000000000002"
START = datetime(2026, 10, 1, 0, 0, 0)
END = datetime(2026, 10, 2, 0, 0, 0)


def _sql(stmt) -> str:
    """Compile with bound values inlined; whitespace collapsed for matching."""
    return " ".join(
        str(
            stmt.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True})
        ).split()
    )


def _page(**filters) -> str:
    return _sql(prompt_repo._page_stmt(prompt_repo._filters(**filters), 1, 20))


def _count(**filters) -> str:
    return _sql(prompt_repo._count_stmt(prompt_repo._filters(**filters)))


def _where(sql: str) -> str:
    """只取 WHERE 段（去掉 ORDER BY / 分页），便于逐条断言过滤条件。"""
    if " WHERE " not in sql:
        return ""
    return sql.split(" WHERE ", 1)[1].split(" ORDER BY ", 1)[0]


# ---------------------------------------------------------------------------
# 投影：列表永远不含正文
# ---------------------------------------------------------------------------


def test_page_projection_counts_chars_but_does_not_select_the_body() -> None:
    selected = _page().split(" FROM ", 1)[0]

    assert "LENGTH(AI_PROMPT_AUDIT.PROMPT_TEXT)" in selected.upper()
    # 除 LENGTH(...) 之外，正文列不得出现在 SELECT 列表里。
    stripped = selected.upper().replace(
        "LENGTH(AI_PROMPT_AUDIT.PROMPT_TEXT)", ""
    )
    assert "PROMPT_TEXT" not in stripped


def test_meta_row_shape_has_no_body() -> None:
    row = prompt_repo._meta_row(
        {
            "oid": "a" * 24,
            "user_oid": USER_OID,
            "purpose": "agent",
            "roles_snapshot": "admin",
            "created_at": START,
            "prompt_chars": 12,
        }
    )

    assert "prompt_text" not in row
    assert row["purpose"] == "agent"
    assert row["prompt_chars"] == 12


# ---------------------------------------------------------------------------
# 过滤条件进 WHERE
# ---------------------------------------------------------------------------


def test_purpose_is_an_exact_predicate() -> None:
    where = _where(_page(purpose="agent"))

    assert "ai_prompt_audit.purpose = 'agent'" in where


def test_purpose_does_not_use_like() -> None:
    """场景是封闭枚举值，模糊匹配会把 ``chat`` 和 ``chat-x`` 混在一起。"""
    assert "LIKE" not in _where(_page(purpose="chat")).upper()


def test_time_bounds_are_closed_interval() -> None:
    where = _where(_page(since=START, until=END))

    # Oracle 方言把 datetime 渲染成 TO_DATE(...)（仍是裸列比较）。
    assert (
        "ai_prompt_audit.created_at >= TO_DATE('2026-10-01 00:00:00'" in where
    )
    assert (
        "ai_prompt_audit.created_at <= TO_DATE('2026-10-02 00:00:00'" in where
    )


def test_time_start_and_since_merge_to_the_tighter_bound() -> None:
    later = datetime(2026, 10, 1, 12, 0, 0)

    # 同一语义的两个名字同时给出：取更晚的下界（交集），不是任意一个。
    assert ">= TO_DATE('2026-10-01 12:00:00'" in _where(
        _page(since=START, time_start=later)
    )
    assert ">= TO_DATE('2026-10-01 12:00:00'" in _where(
        _page(since=later, time_start=START)
    )
    # 上界相反：取更早的那一个。
    earlier = datetime(2026, 10, 1, 18, 0, 0)
    assert "<= TO_DATE('2026-10-01 18:00:00'" in _where(
        _page(until=END, time_end=earlier)
    )


def test_time_start_and_time_end_are_supported_directly() -> None:
    where = _where(_page(time_start=START, time_end=END))

    assert ">= TO_DATE('2026-10-01 00:00:00'" in where
    assert "<= TO_DATE('2026-10-02 00:00:00'" in where


def test_keyword_filters_in_sql_with_instr_on_clob() -> None:
    where = _where(_page(keyword="活动通知"))

    assert "DBMS_LOB.INSTR(ai_prompt_audit.prompt_text, '活动通知') > 0" in where
    # 正文不进 SELECT：过滤在库里完成，列表只拿 LENGTH。
    selected = _page(keyword="活动通知").split(" FROM ", 1)[0]
    assert "DBMS_LOB" not in selected


def test_keyword_is_literal_not_a_like_pattern() -> None:
    """``%`` / ``_`` 是普通字符：管理端搜「50%」不该变成通配。"""
    where = _where(_page(keyword="50%_x"))

    assert "'50%_x'" in where
    assert "LIKE" not in where.upper()


def test_empty_keyword_is_not_a_filter() -> None:
    assert not prompt_repo._filters(keyword="")
    assert not prompt_repo._filters(keyword=None)


def test_filters_combine_into_one_where_clause() -> None:
    where = _where(
        _page(
            user_oid=USER_OID,
            purpose="agent",
            keyword="运动会",
            since=START,
            until=END,
        )
    )

    assert f"ai_prompt_audit.user_oid = '{USER_OID}'" in where
    assert "ai_prompt_audit.purpose = 'agent'" in where
    assert "DBMS_LOB.INSTR(ai_prompt_audit.prompt_text, '运动会') > 0" in where
    # Oracle 方言把 datetime 渲染成 TO_DATE(...)（仍是裸列比较）。
    assert (
        "ai_prompt_audit.created_at >= TO_DATE('2026-10-01 00:00:00'" in where
    )
    assert (
        "ai_prompt_audit.created_at <= TO_DATE('2026-10-02 00:00:00'" in where
    )


# ---------------------------------------------------------------------------
# COUNT 与分页同条件 / 排序
# ---------------------------------------------------------------------------


def test_count_uses_the_same_conditions_as_the_page() -> None:
    filters = {
        "user_oid": USER_OID,
        "purpose": "agent",
        "keyword": "运动会",
        "since": START,
        "until": END,
    }

    count_where = _where(_count(**filters))
    page_where = _where(_page(**filters))

    assert count_where == page_where
    assert _count(**filters).upper().startswith("SELECT COUNT(*)")


def test_page_orders_newest_first_and_paginates() -> None:
    sql = _page()

    assert (
        "ORDER BY ai_prompt_audit.created_at DESC, ai_prompt_audit.oid DESC" in sql
    )
    # 第二页：跳过 20 行、取 20 行（Oracle 12c 的 OFFSET/FETCH 口径）。
    assert "OFFSET 20 ROWS FETCH FIRST 20 ROWS ONLY" in _sql(
        prompt_repo._page_stmt(prompt_repo._filters(), 2, 20)
    )


def test_created_at_range_is_index_friendly() -> None:
    """``created_at >= / <=`` 是裸列比较（没有包函数），可走 ``(created_at)`` 索引。"""
    where = _where(_page(since=START, until=END))

    assert "TO_CHAR" not in where.upper()
    assert "TRUNC(" not in where.upper()
    assert ">= TO_DATE('2026-10-01 00:00:00'" in where
