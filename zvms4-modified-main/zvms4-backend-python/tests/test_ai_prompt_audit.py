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

import pytest
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


# ---------------------------------------------------------------------------
# R5：回答侧四字段（detail 投影 / capture 扩签名 / 补写）
# ---------------------------------------------------------------------------


def test_detail_projection_carries_the_answer_side_columns() -> None:
    """审阅端点透出 ANSWER_TEXT / TOOL_CALLS / REFUSED / WARNED（R5）。"""
    row = prompt_repo._detail_row(
        {
            "oid": "c" * 24,
            "request_id": "req-1",
            "user_oid": USER_OID,
            "purpose": "agent",
            "roles_snapshot": "department",
            "prompt_text": "帮我查成绩",
            "answer_text": "已为你查到",
            "tool_calls": '[{"name": "search_activities"}]',
            "refused": 1,
            "warned": 0,
            "created_at": START,
        }
    )

    assert row["answer_text"] == "已为你查到"
    assert row["tool_calls"] == '[{"name": "search_activities"}]'
    # NUMBER(1) → bool：前端按布尔分支，不该再去比 0/1。
    assert row["refused"] is True
    assert row["warned"] is False


def test_answer_side_columns_are_in_the_detail_select() -> None:
    from sqlalchemy.dialects import oracle

    sql = " ".join(
        str(
            __import__("sqlalchemy").select(*prompt_repo._detail_columns()).compile(
                dialect=oracle.dialect()
            )
        ).split()
    )
    for column in ("ANSWER_TEXT", "TOOL_CALLS", "REFUSED", "WARNED"):
        assert column in sql.upper()


def test_meta_projection_stays_without_the_answer_body() -> None:
    """列表仍然只搬元数据：回答正文只走单条审阅（会写 APP_LOGS 的那条路）。"""
    selected = _page().split(" FROM ", 1)[0].upper()

    assert "ANSWER_TEXT" not in selected
    assert "TOOL_CALLS" not in selected


# ---------------------------------------------------------------------------
# R5：服务层（capture 扩签名 / record_outcome / 摘要形状）
# ---------------------------------------------------------------------------

class FakeAuditRepo:
    def __init__(self) -> None:
        self.inserts: list[dict] = []
        self.updates: list[dict] = []

    async def insert(self, **fields) -> str:
        self.inserts.append(fields)
        return "d" * 24

    async def update_outcome(self, oid, **fields) -> bool:
        self.updates.append({"oid": str(oid), **fields})
        return True


@pytest.fixture
def audit_repo(monkeypatch):
    from db import backends
    from services.ai import prompt_audit

    fake = FakeAuditRepo()
    monkeypatch.setattr(
        backends, "get_ai_prompt_audit_repo", lambda session=None: fake
    )
    monkeypatch.setattr(
        prompt_audit.ai_config,
        "prompt_capture_roles",
        lambda: frozenset({"department"}),
    )
    return fake


@pytest.mark.asyncio
async def test_capture_writes_the_answer_side_fields(audit_repo) -> None:
    from services.ai import prompt_audit

    oid = await prompt_audit.capture(
        user={"id": USER_OID, "per": ["department"]},
        purpose="markdown-assistant",
        messages=[{"role": "user", "content": "帮我查 13812345678 的成绩"}],
        request_id="req-9",
        answer_text="已为 13812345678 查到成绩",
        tool_calls=[{"name": "list_groups", "args_summary": "{}", "status": "executed"}],
        refused=True,
        warned=True,
    )

    assert oid == "d" * 24
    row = audit_repo.inserts[0]
    # 提问与回答都过同一套脱敏。
    assert "13812345678" not in row["prompt_text"]
    assert "13812345678" not in row["answer_text"]
    assert row["refused"] is True
    assert row["warned"] is True
    assert '"name": "list_groups"' in row["tool_calls"]


@pytest.mark.asyncio
async def test_record_outcome_updates_the_same_row(audit_repo) -> None:
    from services.ai import prompt_audit

    await prompt_audit.record_outcome(
        "e" * 24,
        answer_text="结论",
        tool_calls=[{"tool_name": "fs_read", "status": "executed"}],
        refused=False,
        warned=None,
    )

    assert audit_repo.updates == [
        {
            "oid": "e" * 24,
            "answer_text": "结论",
            "tool_calls": '[{"name": "fs_read", "args_summary": null, '
            '"result_summary": null, "status": "executed"}]',
            "refused": False,
            "warned": None,
        }
    ]


@pytest.mark.asyncio
async def test_record_outcome_without_an_oid_is_a_noop(audit_repo) -> None:
    """没有留存行（角色不在白名单）时不该产生任何写操作。"""
    from services.ai import prompt_audit

    await prompt_audit.record_outcome(None, answer_text="x", refused=True)

    assert audit_repo.updates == []


def test_tool_calls_payload_keeps_only_the_documented_shape() -> None:
    from services.ai import prompt_audit

    payload = prompt_audit.tool_calls_payload(
        [
            {
                "name": "fs_read",
                "args_summary": "{}",
                "result_summary": "ok",
                "status": "executed",
                "round_no": 1,
                "tool_kind": "read",
            },
            "垃圾",
        ]
    )

    import json

    assert json.loads(payload) == [
        {
            "name": "fs_read",
            "args_summary": "{}",
            "result_summary": "ok",
            "status": "executed",
        }
    ]
    assert prompt_audit.tool_calls_payload([]) is None
    assert prompt_audit.tool_calls_payload(None) is None
