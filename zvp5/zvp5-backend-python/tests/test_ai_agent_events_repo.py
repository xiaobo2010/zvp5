"""``AI_AGENT_EVENTS`` 的轮读口：``after_round`` 过滤与轻量投影。

不连真库：SQL 编译后断言形状。两件事在这里钉死——

* ``after_round > 0`` 时轮次过滤下推到 SQL（默认 0 的旧行为逐字不变）；
* 轮询用的投影**不选** ``args_payload`` / ``args_hash``（前者是 CLOB），
  但返回的行与全量版本同形状（两个键补 ``None``）。
"""

from __future__ import annotations

from sqlalchemy.dialects import oracle

from db.repos.oracle import ai_agent_events as repo

REQUEST = "b" * 32


def _sql(stmt) -> str:
    return " ".join(
        str(
            stmt.compile(
                dialect=oracle.dialect(), compile_kwargs={"literal_binds": True}
            )
        ).split()
    )


# ---------------------------------------------------------------------------
# list_for_request：轮次过滤
# ---------------------------------------------------------------------------

def test_list_for_request_default_keeps_the_whole_timeline() -> None:
    sql = _sql(repo._list_for_request_stmt(REQUEST))

    assert f"ai_agent_events.request_id = '{REQUEST}'" in sql
    # 默认 0 = 旧行为：不带轮次谓词。
    assert "ai_agent_events.round_no >" not in sql
    assert "ORDER BY ai_agent_events.created_at, ai_agent_events.oid" in sql


def test_list_for_request_after_round_filters_in_sql() -> None:
    sql = _sql(repo._list_for_request_stmt(REQUEST, after_round=2))

    assert "ai_agent_events.round_no > 2" in sql
    # 过滤只加谓词，顺序不变（时间线按写入顺序）。
    assert "ORDER BY ai_agent_events.created_at, ai_agent_events.oid" in sql


def test_negative_after_round_is_treated_as_no_filter() -> None:
    sql = _sql(repo._list_for_request_stmt(REQUEST, after_round=-1))

    assert "ai_agent_events.round_no >" not in sql


# ---------------------------------------------------------------------------
# list_display_for_request：轻量投影
# ---------------------------------------------------------------------------

def test_display_projection_skips_the_clob_columns() -> None:
    sql = _sql(repo._list_display_stmt(REQUEST))

    assert "ai_agent_events.args_payload" not in sql
    assert "ai_agent_events.args_hash" not in sql


def test_display_projection_keeps_the_rendered_columns() -> None:
    sql = _sql(repo._list_display_stmt(REQUEST))

    for column in (
        "oid",
        "request_id",
        "round_no",
        "tool_name",
        "tool_kind",
        "args_summary",
        "result_summary",
        "status",
        "actor_oid",
        "preview",
        "created_at",
    ):
        assert f"ai_agent_events.{column}" in sql
    assert "ORDER BY ai_agent_events.created_at, ai_agent_events.oid" in sql


def test_display_projection_honours_after_round() -> None:
    sql = _sql(repo._list_display_stmt(REQUEST, after_round=3))

    assert "ai_agent_events.round_no > 3" in sql
    assert "ai_agent_events.args_payload" not in sql


def test_display_row_keeps_the_full_row_shape() -> None:
    row = repo._display_row(
        {
            "oid": "e1",
            "request_id": REQUEST,
            "round_no": 2,
            "tool_name": "fs_read",
            "tool_kind": "read",
            "args_summary": "{}",
            "result_summary": "ok",
            "status": "executed",
            "actor_oid": "0" * 24,
            "preview": None,
            "created_at": None,
        }
    )

    # 同形状：缺的两列补 None，而不是缺键（上层只读 args_summary/preview）。
    assert row["args_payload"] is None
    assert row["args_hash"] is None
    assert row["_id"] == "e1"
    assert row["round_no"] == 2
    assert row["tool_name"] == "fs_read"
    assert row["result_summary"] == "ok"
