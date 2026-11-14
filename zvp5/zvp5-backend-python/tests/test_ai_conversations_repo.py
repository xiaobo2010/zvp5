"""会话/消息/run 数据层的离线 SQL 契约（不连库、不建引擎）。

三张新表的「写错了也不报错、只在生产上静默出错」的点各有几处，这里把它们钉成
SQL 形状断言（编译时内联绑定值，断言实际会发给 Oracle 的文本）：

* **列表页不得投影 ``summary``**：CLOB 进了列表就是每次翻页读几 MB。
* **seq 只有一个来源**：会话行锁内的 ``MAX(seq)+1``；锁语句必须真带 ``FOR UPDATE``。
* **一轮的消息与会话累加同一事务**：``append_batch`` 里「分配 seq → 插入 →
  累加」的顺序与方法体里只有一个提交点是承重的（评审 P1-3 / P1-4）。
* **活跃 run 的唯一性靠数据库**：``insert_queued`` 幂等命中要按唯一键等值查，
  撞活跃索引要抛 :class:`ActiveRunConflict` 而不是写第二条。
* **状态迁移是 CAS**：``finish`` / ``mark_cancelled_if_queued`` 的 ``WHERE`` 必须
  带预期状态，否则终态会被覆盖。
* **批删用 ROWNUM 分段**，不用 ``IN (...)`` 大列表（Oracle 1000 项上限）。
"""

from __future__ import annotations

import inspect
from collections.abc import Iterator, Mapping
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.dialects import oracle
from sqlalchemy.exc import NoSuchColumnError

from db.repos.oracle import ai_conversations as conversations_repo
from db.repos.oracle import ai_runs as runs_repo

DIALECT = oracle.dialect()
OWNER = "000000000000000000000002"
CONV = "c" * 32
RUN = "r" * 32
TS = datetime(2026, 10, 23, 1, 2, 3)


def _sql(stmt) -> str:
    """Compile with bound values inlined; whitespace collapsed for matching."""
    return " ".join(
        str(stmt.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True})).split()
    )


def _source(func) -> str:
    """方法的源码，用来钉住「顺序」与「提交次数」这类只能看代码的事实。"""
    return inspect.getsource(func)


def _list_projection_values() -> dict[str, Any]:
    """按真实列表投影的列集合构造一行。

    列名从 ``_list_columns()`` 反推而不是手抄：投影增删列时这里先炸，避免用例
    自己漂移成一个「假投影」。
    """
    values: dict[str, Any] = {
        "oid": CONV,
        "owner_oid": OWNER,
        "mode": "chat",
        "title": "学期总结",
        "model": "gpt-x",
        "thinking": "medium",
        "scene": None,
        "status": "active",
        "summary_upto_seq": 3,
        "tokens_prompt": 10,
        "tokens_completion": 20,
        "tokens_total": 30,
        "context_tokens": 40,
        "message_count": 7,
        "compaction_degraded_at": None,
        "created_at": TS,
        "updated_at": TS,
    }
    keys = {column.key for column in conversations_repo._list_columns()}
    assert keys == set(values), keys ^ set(values)
    return values


class _ProjectedMapping(Mapping[str, Any]):
    """复刻 ``RowMapping`` 的失败语义：缺列时抛 ``NoSuchColumnError``。

    这个复刻是必须的：普通 dict 缺键抛 ``KeyError``，而 SQLAlchemy 的
    ``RowMapping`` 抛 ``NoSuchColumnError``。用 dict 测这个漏洞会「看起来通过」，
    线上却照旧 500。
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> Any:
        try:
            return self._data[key]
        except KeyError:
            raise NoSuchColumnError(
                f"Could not locate column in row for column '{key}'"
            ) from None

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


class _FakeResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _FakeSession:
    """只够 ``list_page`` 用：``execute()`` 直接回放给定的行。"""

    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    async def execute(self, _stmt: Any) -> _FakeResult:
        return _FakeResult(self._rows)


# ---------------------------------------------------------------------------
# AI_CONVERSATIONS：列表 / 计数 / 批删
# ---------------------------------------------------------------------------


def test_list_projection_never_selects_the_summary_clob() -> None:
    listed = _sql(
        conversations_repo._list_page_stmt(OWNER, status=None, cursor=None, limit=50)
    )
    detailed = _sql(conversations_repo._get_stmt(CONV, OWNER))

    assert "ai_conversations.summary " not in listed.replace("summary_upto_seq", "")
    assert "ai_conversations.summary," not in listed
    # 详情投影要有它：上下文组装得读摘要。
    assert "ai_conversations.summary" in detailed
    # 列表里该有的元数据一个都不能少（少一列 = 左栏少一个字段）。
    for column in (
        "title",
        "status",
        "tokens_total",
        "context_tokens",
        "message_count",
        "updated_at",
    ):
        assert f"ai_conversations.{column}" in listed, column


def test_list_projection_row_maps_without_the_summary_column() -> None:
    """列表投影 → 行映射这一步必须不再依赖 ``summary``。

    历史漏洞：``_list_columns()`` 剔了 ``summary``，但列表页用的是读
    ``mapping["summary"]`` 的全列映射，于是只要列表里有会话就抛
    ``NoSuchColumnError`` → ``GET /api/ai/conversations`` 100% 500（会话数为 0
    时因为不进映射反而 200）。
    """
    mapping = _ProjectedMapping(_list_projection_values())

    # 先证明这个双精度复刻确实能复现原漏洞：全列映射喂列表投影会炸。
    with pytest.raises(NoSuchColumnError):
        conversations_repo._conversation_row(mapping)

    row = conversations_repo._list_row(mapping)
    assert row["summary"] is None
    assert row["_id"] == CONV
    assert row["message_count"] == 7
    assert row["tokens_total"] == 30
    assert row["updated_at"] == TS


@pytest.mark.asyncio
async def test_list_page_maps_projection_rows_end_to_end() -> None:
    """``list_page`` 的真实调用点：投影行进列表映射，不抛 ``NoSuchColumnError``。"""
    mapping = _ProjectedMapping(_list_projection_values())
    repo = conversations_repo.OracleAIConversationsRepo(
        session=_FakeSession([SimpleNamespace(_mapping=mapping)])
    )

    items, cursor = await repo.list_page(OWNER, limit=10)

    assert len(items) == 1
    assert items[0]["summary"] is None
    assert items[0]["_id"] == CONV
    # 不满一页 → 没有下一页游标。
    assert cursor is None


def test_list_page_is_keyset_paginated_not_offset() -> None:
    sql = _sql(
        conversations_repo._list_page_stmt(
            OWNER, status="active", cursor=(TS, "a" * 32), limit=10
        )
    )

    # 键集分页：严格递减的 (updated_at, oid)，没有 OFFSET。
    assert "ai_conversations.updated_at < TO_DATE(" in sql
    assert "ai_conversations.updated_at = TO_DATE(" in sql
    assert f"ai_conversations.oid < '{'a' * 32}'" in sql
    assert "OFFSET" not in sql.upper()
    assert "ORDER BY ai_conversations.updated_at DESC" in sql
    assert "ai_conversations.oid DESC" in sql
    assert "FETCH FIRST 10 ROWS ONLY" in sql
    # owner 过滤必须在 SQL 里，不能交给调用方。
    assert f"ai_conversations.owner_oid = '{OWNER}'" in sql


def test_list_page_first_page_has_no_cursor_predicate() -> None:
    sql = _sql(
        conversations_repo._list_page_stmt(OWNER, status=None, cursor=None, limit=50)
    )
    assert "updated_at <" not in sql
    assert "ai_conversations.oid <" not in sql
    assert "FETCH FIRST 50 ROWS ONLY" in sql


def test_list_page_limit_is_clamped_on_both_ends() -> None:
    def _fetch(limit: int) -> str:
        return _sql(
            conversations_repo._list_page_stmt(
                OWNER, status=None, cursor=None, limit=limit
            )
        )

    # 上限：写错一层也不会把整张表拉回来。
    assert f"FETCH FIRST {conversations_repo.LIST_LIMIT_MAX} ROWS ONLY" in _fetch(5000)
    # 下限：FETCH FIRST 0 在 Oracle 是合法但无意义的语句。
    assert "FETCH FIRST 1 ROWS ONLY" in _fetch(0)


def test_add_tokens_increments_in_sql_and_only_overwrites_context_when_given() -> None:
    sql = _sql(
        conversations_repo._add_tokens_stmt(
            CONV,
            prompt_tokens=5,
            completion_tokens=6,
            total_tokens=11,
            context_tokens=None,
        )
    )

    assert "SET tokens_prompt=(ai_conversations.tokens_prompt + 5)" in sql
    assert "tokens_completion=(ai_conversations.tokens_completion + 6)" in sql
    assert "tokens_total=(ai_conversations.tokens_total + 11)" in sql
    # 水位是覆盖语义；没给就不碰（否则「上游没报」会被写成 0）。
    assert "context_tokens" not in sql.split("SET ", 1)[1]
    assert "SELECT" not in sql.upper()

    with_context = _sql(
        conversations_repo._add_tokens_stmt(
            CONV,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            context_tokens=900,
        )
    )
    assert "context_tokens=900" in with_context
    # 覆盖而不是累加：不能出现 context_tokens + 900。
    assert "context_tokens + " not in with_context


def test_count_messages_reads_the_redundant_counter() -> None:
    sql = _sql(conversations_repo._count_messages_stmt(CONV)).upper()

    assert sql.startswith("SELECT AI_CONVERSATIONS.MESSAGE_COUNT")
    # 为一个数字去 COUNT 消息表（CLOB）不值得。
    assert "COUNT(" not in sql
    assert "AI_MESSAGES" not in sql


def test_delete_batched_uses_rownum_and_the_owner_predicate() -> None:
    sql = _sql(conversations_repo._delete_batched_stmt(CONV, OWNER, 200))

    assert sql.upper().startswith("DELETE FROM AI_CONVERSATIONS")
    assert f"ai_conversations.oid = '{CONV}'" in sql
    assert f"ai_conversations.owner_oid = '{OWNER}'" in sql
    # 分段删：ROWNUM 而不是 FETCH FIRST（DELETE 不接受后者）。
    assert "ROWNUM <= 200" in sql
    assert "FETCH FIRST" not in sql.upper()
    assert " IN (" not in sql.upper()


def test_update_values_skip_absent_fields_and_bump_updated_at() -> None:
    at = TS
    assert conversations_repo._update_values(
        title=None, status=None, thinking=None, model=None, at=at
    ) == {}
    values = conversations_repo._update_values(
        title="新标题", status="archived", thinking=None, model=None, at=at
    )
    assert values == {"title": "新标题", "status": "archived", "updated_at": at}
    # thinking=None 表示「不改」：显式清空走 clear_thinking 专用开关。
    assert "thinking" not in values

    thinking = conversations_repo._update_values(
        title=None, status=None, thinking="high", model=None, at=at
    )
    assert thinking == {"thinking": "high", "updated_at": at}

    # 空会话改模型：准入由服务层判（message_count == 0），仓储只负责写。
    model = conversations_repo._update_values(
        title=None, status=None, thinking=None, model="gpt-y", at=at
    )
    assert model == {"model": "gpt-y", "updated_at": at}


# ---------------------------------------------------------------------------
# AI_MESSAGES：seq 分配 / 增量回放 / 原子批次
# ---------------------------------------------------------------------------


def test_next_seq_locks_the_conversation_row_first() -> None:
    lock_sql = _sql(conversations_repo._lock_conversation_stmt(CONV))
    max_sql = _sql(conversations_repo._max_seq_stmt(CONV))

    assert lock_sql.endswith("FOR UPDATE")
    assert f"ai_conversations.oid = '{CONV}'" in lock_sql
    assert "max(ai_messages.seq)" in max_sql.lower()
    # 锁的是会话行，不是消息表：Oracle 没有间隙锁，锁消息表挡不住「读 MAX 时
    # 还没有下一行」的并发。
    assert "ai_messages" not in lock_sql

    source = _source(conversations_repo.OracleAIMessagesRepo.append_batch)
    assert source.index("_allocate_seq") < source.index("_insert_message_values")
    assert "_allocate_seq" not in _source(
        conversations_repo.OracleAIConversationsRepo.count_messages
    )


def test_list_after_is_an_incremental_ascending_scan() -> None:
    sql = _sql(conversations_repo._list_after_stmt(CONV, 7, 50))

    assert f"ai_messages.conversation_oid = '{CONV}'" in sql
    assert "ai_messages.seq > 7" in sql
    assert "ORDER BY ai_messages.seq ASC" in sql
    assert "FETCH FIRST 50 ROWS ONLY" in sql


def test_message_insert_binds_usage_and_tool_identity_explicitly() -> None:
    sql = _sql(
        conversations_repo._insert_message_stmt(
            seq=3,
            created_at=TS,
            oid="a" * 32,
            conversation_oid=CONV,
            role="tool",
            content="全文",
            tool_call_id="call-1",
            tool_name="update_activity",
            round_no=2,
            request_id=RUN,
        )
    )

    assert sql.upper().startswith("INSERT INTO AI_MESSAGES ")
    # 工具身份三件套缺一件，回放组装就无法把 tool 结果配对回 assistant.tool_calls。
    assert "'call-1', 'update_activity', 2" in sql
    assert f"'{RUN}'" in sql
    # 该轮的 token 列没给 → NULL（上游没报 ≠ 0）。
    assert "'tool', '全文', NULL" in sql


def test_message_insert_binds_reasoning_on_the_assistant_row() -> None:
    """思考过程与 content 同列类型（CLOB）：落库、投影、回传三处都必须有它。"""
    sql = _sql(
        conversations_repo._insert_message_stmt(
            seq=4,
            created_at=TS,
            oid="b" * 32,
            conversation_oid=CONV,
            role="assistant",
            content="答案",
            reasoning="先读日志再定位",
            round_no=2,
            request_id=RUN,
        )
    )

    assert "'先读日志再定位'" in sql
    # 没给的轮次写 NULL（上游没返回 ≠ 空串）。
    tool_sql = _sql(
        conversations_repo._insert_message_stmt(
            seq=5,
            created_at=TS,
            oid="c" * 32,
            conversation_oid=CONV,
            role="tool",
            content="{}",
            round_no=2,
            request_id=RUN,
        )
    )
    assert "'tool', '{}', NULL" in tool_sql
    # 投影列与行映射都带上它，否则 message_view 永远拿不到。
    assert "ai_messages.reasoning" in _sql(conversations_repo._list_after_stmt(CONV, 0, 10))


def test_append_batch_bumps_the_conversation_in_the_same_transaction() -> None:
    source = _source(conversations_repo.OracleAIMessagesRepo.append_batch)

    # 一轮的 assistant + 全部 tool 一次落库，会话累加在同一个方法体内。
    assert "_insert_message_values" in source
    assert "_bump_conversation_stmt" in source
    assert source.index("_allocate_seq") < source.index("_insert_message_values")
    assert source.index("_insert_message_values") < source.index(
        "_bump_conversation_stmt"
    )
    # **一次**数组绑定 INSERT：整批取值交一个 ``session.execute(insert(...), values)``，
    # 逐行 execute 在 ADB 上就是每条一次跨洋往返（它们本来就在同一事务里）。
    assert "session.execute(insert(ai_messages_tbl), values)" in source
    # 只有一个提交点：多一处 commit 就等于把「同事务」拆成两段。
    assert source.count("session.commit()") == 1
    # 空批次不碰数据库。
    assert "if not rows" in source


def test_append_batch_empty_batch_returns_without_a_statement() -> None:
    import asyncio

    repo = conversations_repo.OracleAIMessagesRepo()
    assert asyncio.run(repo.append_batch(CONV, [])) == []


def test_message_count_is_scoped_to_one_conversation() -> None:
    sql = _sql(conversations_repo._count_messages_of_stmt(CONV)).upper()
    assert sql.startswith("SELECT COUNT(*)")
    assert f"AI_MESSAGES.CONVERSATION_OID = '{CONV.upper()}'" in sql


def test_purge_messages_is_batched_by_rownum() -> None:
    sql = _sql(conversations_repo._purge_messages_stmt(CONV, 500))

    assert sql.upper().startswith("DELETE FROM AI_MESSAGES")
    assert f"ai_messages.conversation_oid = '{CONV}'" in sql
    assert "ROWNUM <= 500" in sql
    assert " IN (" not in sql.upper()


# ---------------------------------------------------------------------------
# AI_RUNS：领取 / CAS / 心跳 / 批删
# ---------------------------------------------------------------------------


def test_claim_next_skips_cancelled_and_leaves_locking_to_cas() -> None:
    sql = _sql(runs_repo._claim_candidate_stmt())

    assert "ai_runs.status = 'queued'" in sql
    assert "ai_runs.cancel_requested = 0" in sql
    assert "ORDER BY ai_runs.created_at ASC" in sql
    assert "FETCH FIRST 1 ROWS ONLY" in sql
    # Oracle 不允许 FETCH FIRST 与 FOR UPDATE 同层（ORA-02014），
    # 抢占原子性改由紧随其后的 CAS 承担，这里断言两者不会同时出现。
    assert "FOR UPDATE" not in sql.upper()
    assert "SKIP LOCKED" not in sql.upper()
    # 领取只取 oid：整行在 CAS 之后再读一次，避免「锁到的是旧快照」。
    assert sql.upper().startswith("SELECT AI_RUNS.OID ")


def test_claim_is_a_cas_from_queued_to_running() -> None:
    sql = _sql(runs_repo._claim_cas_stmt(RUN, TS))

    assert "SET status='running'" in sql
    assert "started_at=TO_DATE('2026-10-23 01:02:03'" in sql
    assert "heartbeat_at=TO_DATE('2026-10-23 01:02:03'" in sql
    assert "ai_runs.status = 'queued'" in sql


def test_finish_only_accepts_terminal_statuses() -> None:
    import asyncio

    import pytest

    repo = runs_repo.OracleAIRunsRepo()
    with pytest.raises(ValueError):
        asyncio.run(repo.finish(RUN, status="running"))
    with pytest.raises(ValueError):
        asyncio.run(repo.finish(RUN, status="queued"))


def test_finish_is_cas_on_running() -> None:
    sql = _sql(
        runs_repo._finish_stmt(
            RUN,
            runs_repo._finish_values(
                status="failed",
                stop_reason="error",
                rounds=3,
                error_code="AI_AGENT_UPSTREAM",
                error_message="上游 500",
                result_message_oid=None,
                prompt_tokens=10,
                completion_tokens=None,
                total_tokens=10,
                finished_at=TS,
            ),
        )
    )

    assert "ai_runs.status = 'running'" in sql
    assert "SET status='failed'" in sql
    assert "rounds=3" in sql
    assert "error_code='AI_AGENT_UPSTREAM'" in sql
    # None 的字段不写：不会把已有的 result_message_oid 抹成 NULL。
    assert "result_message_oid" not in sql
    assert "completion_tokens" not in sql


def test_heartbeat_only_touches_heartbeat_at_on_running_rows() -> None:
    sql = _sql(runs_repo._heartbeat_stmt(RUN, TS)).lower()

    assert sql.startswith("update ai_runs set heartbeat_at=")
    assert "ai_runs.status = 'running'" in sql
    # 心跳不能顺手改状态：那是 watchdog 与执行方各说各话的起点。
    assert "status=" not in sql.split("set ", 1)[1]


def test_request_cancel_is_scoped_to_the_owner_and_active_rows() -> None:
    sql = _sql(runs_repo._request_cancel_stmt(RUN, OWNER))

    assert "SET cancel_requested=1" in sql
    assert f"ai_runs.owner_oid = '{OWNER}'" in sql
    assert "ai_runs.status IN ('queued', 'running')" in sql
    # 取消不是状态迁移：不能在这里写 status。
    assert "SET cancel_requested=1" in sql
    assert "status=" not in sql.split("SET ", 1)[1]


def test_cancel_if_queued_is_a_cas() -> None:
    sql = _sql(runs_repo._cancel_if_queued_stmt(RUN, TS))

    assert "SET status='cancelled'" in sql
    assert "finished_at=TO_DATE(" in sql
    assert "ai_runs.status = 'queued'" in sql


def test_list_active_returns_queued_and_running_only() -> None:
    sql = _sql(runs_repo._list_active_stmt(OWNER))

    assert f"ai_runs.owner_oid = '{OWNER}'" in sql
    assert "ai_runs.status IN ('queued', 'running')" in sql
    assert "ORDER BY ai_runs.created_at ASC" in sql


def test_interrupt_running_targets_every_running_row() -> None:
    values = runs_repo._interrupt_values(
        error_code="AI_RUN_INTERRUPTED", message="执行进程失去心跳", at=TS
    )
    sql = _sql(runs_repo._interrupt_running_stmt(values))

    assert "SET status='interrupted'" in sql
    assert "error_code='AI_RUN_INTERRUPTED'" in sql
    assert "ai_runs.status = 'running'" in sql
    # 启动收敛不看心跳：进程刚起来时，任何 running 都是上次崩溃的残留。
    assert "heartbeat_at" not in sql


def test_interrupt_stale_requires_the_heartbeat_cutoff() -> None:
    values = runs_repo._interrupt_values(
        error_code="AI_RUN_INTERRUPTED", message="执行进程失去心跳", at=TS
    )
    sql = _sql(runs_repo._interrupt_stale_stmt(TS, values))

    assert "ai_runs.status = 'running'" in sql
    assert "ai_runs.heartbeat_at < TO_DATE('2026-10-23 01:02:03'" in sql
    assert "SET status='interrupted'" in sql


# ---------------------------------------------------------------------------
# AI_RUNS：入队的原子性与幂等
# ---------------------------------------------------------------------------


def test_insert_queued_writes_message_then_run_in_one_transaction() -> None:
    source = _source(runs_repo.OracleAIRunsRepo.insert_queued)

    assert "session.begin_nested()" in source
    # 顺序：分配 seq → 用户消息 → run → 会话累加。
    assert source.index("_allocate_seq") < source.index("_insert_message_stmt")
    assert source.index("_insert_message_stmt") < source.index("_insert_run_stmt")
    assert source.index("_insert_run_stmt") < source.index("_bump_conversation_stmt")
    # 唯一提交点：任何中间提交都会让「冲突回滚」失去意义。
    assert source.count("session.commit()") == 1


def test_insert_queued_raises_active_run_conflict_for_the_active_index() -> None:
    source = _source(runs_repo.OracleAIRunsRepo.insert_queued)

    assert "raise ActiveRunConflict(" in source
    # 先按幂等键回查，再判定冲突：两个唯一索引可能同时被撞，顺序无关地收敛。
    assert source.index("_by_client_msg_stmt") < source.index("raise ActiveRunConflict(")


def test_client_msg_dedupe_lookup_hits_the_unique_key() -> None:
    sql = _sql(runs_repo._by_client_msg_stmt(CONV, "msg-1"))

    assert f"ai_runs.conversation_oid = '{CONV}'" in sql
    assert "ai_runs.client_msg_id = 'msg-1'" in sql
    # 等值命中唯一索引；LIKE 会让索引失效并把子串当命中。
    assert " LIKE " not in sql.upper()


def test_deduped_reply_reuses_the_existing_user_message() -> None:
    sql = _sql(runs_repo._user_message_oid_stmt(CONV, RUN))

    assert f"ai_messages.conversation_oid = '{CONV}'" in sql
    assert f"ai_messages.request_id = '{RUN}'" in sql
    assert "ai_messages.role = 'user'" in sql
    assert "ORDER BY ai_messages.seq ASC" in sql


def test_insert_run_is_queued_with_no_started_at() -> None:
    sql = _sql(
        runs_repo._insert_run_stmt(
            run_id=RUN,
            conversation_oid=CONV,
            owner_oid=OWNER,
            client_msg_id=None,
            model="gpt-x",
            created_at=TS,
        )
    )

    assert sql.upper().startswith("INSERT INTO AI_RUNS ")
    assert "'queued'" in sql
    # 未开跑就没有开始时间与心跳：拿 created_at 顶替会让 watchdog 无据可判。
    assert "'queued', 'gpt-x', NULL, NULL, NULL, NULL" in sql
    assert "NULL, 0, NULL" in sql  # result_message_oid / 三个 token 之后是 cancel_requested
    assert "started_at" in sql


def test_run_row_normalises_numbers_and_the_cancel_flag() -> None:
    from decimal import Decimal

    mapping = {
        "oid": RUN,
        "conversation_oid": CONV,
        "owner_oid": OWNER,
        "client_msg_id": None,
        "status": "running",
        "model": None,
        "stop_reason": None,
        "rounds": Decimal(2),
        "error_code": None,
        "error_message": None,
        "result_message_oid": None,
        "prompt_tokens": Decimal(11),
        "completion_tokens": None,
        "total_tokens": Decimal(11),
        "cancel_requested": Decimal(1),
        "heartbeat_at": TS,
        "created_at": TS,
        "started_at": TS,
        "finished_at": None,
    }
    row = runs_repo._run_row(mapping)

    assert row["_id"] == RUN
    assert row["rounds"] == 2
    assert row["total_tokens"] == 11
    # NULL 不能变成 0：0 是真实 token 数，会把「上游没报」伪装成「没有」。
    assert row["completion_tokens"] is None
    assert row["cancel_requested"] is True


def test_purge_runs_is_batched_by_rownum() -> None:
    sql = _sql(runs_repo._purge_stmt(CONV, 500))

    assert sql.upper().startswith("DELETE FROM AI_RUNS")
    assert f"ai_runs.conversation_oid = '{CONV}'" in sql
    assert "ROWNUM <= 500" in sql
    assert " IN (" not in sql.upper()


def test_caller_cannot_choose_seq_or_created_at() -> None:
    """``seq`` / ``created_at`` 必须来自锁内事实，调用方给的会被丢掉。"""
    fields = conversations_repo._message_fields(
        {
            "oid": "a" * 32,
            "role": "assistant",
            "content": "答",
            "seq": 99,
            "created_at": TS,
            "conversation_oid": "x" * 32,
            "round_no": 1,
        }
    )

    assert "seq" not in fields
    assert "created_at" not in fields
    assert "conversation_oid" not in fields
    assert fields["round_no"] == 1
    assert fields["content"] == "答"


def test_usage_accumulation_is_in_sql_not_read_back() -> None:
    """轮收尾的累加必须是自增语句：读后写会在 chat + agent 并发时丢一次。"""
    sql = _sql(
        conversations_repo._bump_conversation_stmt(
            CONV,
            added=2,
            usage={
                "prompt_tokens": 7,
                "completion_tokens": 3,
                "total_tokens": 10,
                "context_tokens": 1200,
            },
            at=TS,
        )
    )

    assert "message_count=(ai_conversations.message_count + 2)" in sql
    assert "tokens_total=(ai_conversations.tokens_total + 10)" in sql
    assert "context_tokens=1200" in sql
    assert "SELECT" not in sql.upper()


# ---------------------------------------------------------------------------
# append_batch：一次数组绑定 INSERT（A-M2）
# ---------------------------------------------------------------------------

class _BatchResult:
    def __init__(self, rowcount: int = 1, scalar=None) -> None:
        self.rowcount = rowcount
        self._scalar = scalar

    def scalar(self):
        return self._scalar


class _RecordingSession:
    """记录 (语句, 参数) 的假 session：注入时仓储不许自己提交。"""

    def __init__(self, *, max_seq: int = 0) -> None:
        self.calls: list[tuple] = []
        self.commits = 0
        self.max_seq = max_seq

    async def execute(self, stmt, params=None):
        self.calls.append((stmt, params))
        if "max(ai_messages.seq)" in str(stmt).lower():
            return _BatchResult(scalar=self.max_seq)
        return _BatchResult()

    async def commit(self) -> None:
        self.commits += 1


def _message_row(oid: str, role: str = "tool", **extra: Any) -> dict:
    row = {
        "oid": oid,
        "role": role,
        "content": "正文",
        "tool_call_id": f"call-{oid}",
        "tool_name": "search_activities",
        "round_no": 1,
        "request_id": RUN,
    }
    row.update(extra)
    return row


def test_append_batch_sends_one_array_bound_insert() -> None:
    """一轮 N 条消息只发**一次**插入（外加锁、MAX(seq)、会话累加）。"""
    import asyncio

    session = _RecordingSession(max_seq=4)
    repo = conversations_repo.OracleAIMessagesRepo(session)

    inserted = asyncio.run(
        repo.append_batch(
            CONV,
            [_message_row("m1", "assistant"), _message_row("m2"), _message_row("m3")],
        )
    )

    # 语句总数固定为 4：锁会话行、MAX(seq)、**一次**批量插入、会话累加。
    # 原来是 3 + N（N = 消息条数）。
    assert len(session.calls) == 4
    inserts = [
        call for call in session.calls if "INSERT INTO AI_MESSAGES" in str(call[0]).upper()
    ]
    assert len(inserts) == 1, "逐行 execute 等于每条一次跨洋往返"
    _stmt, params = inserts[0]
    assert isinstance(params, list) and len(params) == 3
    # seq 连续且从锁内 MAX(seq)+1 起算。
    assert [item["seq"] for item in params] == [5, 6, 7]
    assert [item["oid"] for item in params] == ["m1", "m2", "m3"]
    # 工具身份三件套照旧带上：批量不能变成「只写正文」。
    assert params[1]["tool_call_id"] == "call-m2"
    assert params[1]["tool_name"] == "search_activities"
    # 返回值顺序与字段与原实现一致。
    assert [row["_id"] for row in inserted] == ["m1", "m2", "m3"]
    assert [row["seq"] for row in inserted] == [5, 6, 7]
    # 会话累加仍在同一方法体里，且注入 session 不提交。
    assert any(
        "UPDATE AI_CONVERSATIONS" in str(call[0]).upper() for call in session.calls
    )
    assert session.commits == 0


def test_append_batch_commits_once_when_it_owns_the_session(monkeypatch) -> None:
    import asyncio
    from contextlib import asynccontextmanager

    session = _RecordingSession(max_seq=0)

    @asynccontextmanager
    async def scope():
        yield session

    monkeypatch.setattr(conversations_repo, "session_scope", scope)
    repo = conversations_repo.OracleAIMessagesRepo()

    asyncio.run(repo.append_batch(CONV, [_message_row("m1"), _message_row("m2")]))

    # 消息 + 会话累加同一事务，只有边缘这一次提交。
    assert session.commits == 1


def test_append_batch_usage_goes_into_the_same_statement_set() -> None:
    import asyncio

    session = _RecordingSession(max_seq=1)
    repo = conversations_repo.OracleAIMessagesRepo(session)

    asyncio.run(
        repo.append_batch(
            CONV,
            [_message_row("m1", "assistant")],
            usage={
                "prompt_tokens": 7,
                "completion_tokens": 3,
                "total_tokens": 10,
                "context_tokens": 1200,
            },
        )
    )

    update_sql = next(
        " ".join(
            str(stmt.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True})).split()
        )
        for stmt, _params in session.calls
        if "UPDATE AI_CONVERSATIONS" in str(stmt).upper()
    )
    assert "message_count=(ai_conversations.message_count + 1)" in update_sql
    assert "tokens_total=(ai_conversations.tokens_total + 10)" in update_sql
    assert "context_tokens=1200" in update_sql
