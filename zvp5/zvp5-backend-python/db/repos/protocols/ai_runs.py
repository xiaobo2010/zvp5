"""Agent conversations, messages and runs (``AI_CONVERSATIONS`` /
``AI_MESSAGES`` / ``AI_RUNS`` / ``AI_SCHEDULED_TASKS``).

One module: the tables are read and written as one unit (a run appends
messages, a conversation owns both), and ``ActiveRunConflict`` is raised
by the run protocol and handled by the conversation one."""

from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from datetime import datetime

from .common import Row


# ---------------------------------------------------------------------------
# AI conversations / messages / runs (V035)
# ---------------------------------------------------------------------------


class ActiveRunConflict(Exception):
    """The owner (or the conversation) already has a ``queued|running`` run.

    Raised from :meth:`AIRunsRepo.insert_queued` when the insert loses on
    ``uq_ai_runs_active_conv`` / ``uq_ai_runs_active_owner``: the transaction has
    already been rolled back, so **no orphan user message survives**. The router
    maps it to ``409 AI_RUN_CONFLICT``.

    与 :class:`DuplicateApiKey` 同形（应用层异常、协议里声明）：数据库那道闸是
    唯一的裁决者，仓储层只负责把 ORA-00001 翻译成调用方能懂的名字。
    """


class AIConversationsRepo(Protocol):
    """Data access for ``AI_CONVERSATIONS`` (持久会话, V035).

    Row shape::

        {"_id": <32 hex>, "owner_oid": str, "mode": "chat" | "agent",
         "title": str | None, "model": str | None, "scene": str | None,
         "status": "active" | "archived", "summary": str | None,
         "summary_upto_seq": int, "tokens_prompt": int,
         "tokens_completion": int, "tokens_total": int, "context_tokens": int,
         "message_count": int, "compaction_degraded_at": datetime | None,
         "created_at": datetime, "updated_at": datetime}

    ``_id`` is a 32-char ``uuid4().hex`` (an opaque handle, not a business oid).
    ``summary`` + ``summary_upto_seq`` are the compaction watermark: messages with
    ``seq <= summary_upto_seq`` go into the summary only, never into the model
    context. Every counter is an accumulator that starts at ``0`` (0 = "nothing
    yet"), which is why the columns are ``NOT NULL DEFAULT 0``: NULL would force
    a null-check on every increment.
    """

    async def create(
        self,
        *,
        oid: str,
        owner_oid: str,
        mode: str,
        title: str | None = None,
        model: str | None = None,
        scene: str | None = None,
    ) -> Row:
        """Insert a conversation and return the row (``status='active'``).

        ``oid`` 由调用方给：``POST /conversations`` 要在同一个响应里返回句柄，
        等数据库生成就得多一次查询。``scene`` 非空 = 只读场景会话
        （``terminal`` / ``file_edit``），``None`` = 普通会话。
        """
        ...

    async def get(self, oid: str, owner_oid: str) -> Row | None:
        """Return one conversation of *this* owner, or ``None``.

        ``owner_oid`` 是必填的第二个键而不是可选过滤：跨用户读取本期不做，
        把「忘了过滤」变成编译期错误比事后审计便宜。
        """
        ...

    async def list_page(
        self,
        owner_oid: str,
        *,
        status: str | None = None,
        cursor: tuple[datetime, str] | None = None,
        limit: int = 50,
    ) -> tuple[list[Row], tuple[datetime, str] | None]:
        """One page of the owner's conversations, newest ``updated_at`` first.

        ``cursor`` 是上一页最后一行解出来的 ``(updated_at, oid)``（游标编解码在
        HTTP 层，本层只认结构）：键集分页而不是 OFFSET，翻页时新消息不会把行
        从上一页挤到下一页。返回 ``(rows, next_cursor)``，``next_cursor=None``
        表示没有更多。

        **Rows here never carry ``summary``**（CLOB）：列表只要元数据，单会话 200
        轮 ≈1.6MB，把 CLOB 拉进列表等于每次翻页读几 MB。

        **只返回普通会话**（``scene IS NULL``）：场景会话有自己的入口，混进左栏
        列表等于让用户看到自己没建过的会话。
        """
        ...

    async def update(
        self,
        oid: str,
        owner_oid: str,
        *,
        title: str | None = None,
        status: str | None = None,
        model: str | None = None,
        clear_model: bool = False,
    ) -> bool:
        """Renames / archives / re-models a conversation; ``False`` = not found.

        ``mode`` 不在可改字段里：它决定执行模型（同步 SSE vs 后台任务），改它
        等于把一段会话的历史变成另一种语义。``None`` = 不改；**清空模型**（回到
        服务端默认）用 ``clear_model=True``——否则「不改」与「改成 NULL」在
        ``model=None`` 上撞车，PATCH 想清空模型的人只会得到一次静默 no-op。
        """
        ...

    async def touch(self, oid: str, *, at: datetime | None = None) -> None:
        """Bump ``updated_at`` (list ordering is by it)."""
        ...

    async def add_tokens(
        self,
        oid: str,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        context_tokens: int | None = None,
    ) -> None:
        """Accumulate usage in SQL (``tokens_total = tokens_total + :n``).

        ``context_tokens`` 是**覆盖**而不是累加：它是最近一次请求的 prompt token
        （上下文水位），累加会把它变成第二个总量。``None`` = 本次不改水位。
        """
        ...

    async def set_summary(self, oid: str, summary: str | None, upto_seq: int) -> None:
        """Store the compaction summary and its watermark in one statement."""
        ...

    async def set_context_tokens(self, oid: str, tokens: int | None) -> None:
        """Overwrite the context watermark (``None`` = 上游没报，别拿 0 顶替)."""
        ...

    async def mark_compaction_degraded(
        self, oid: str, *, at: datetime | None = None
    ) -> None:
        """Record "the latest compaction fell back to hard truncation".

        写时间点而不是布尔开关：``compaction_degraded_at`` 的事实是「最近一次降级
        发生在什么时候」，清不清零由展示层决定（``NULL`` = 从未降级）。
        """
        ...

    async def count_messages(self, oid: str) -> int:
        """Read the redundant ``message_count`` column — **not** a COUNT(*).

        删除回执与列表都用它：为了一个数字去扫 CLOB 表不值得。真实条数以
        :meth:`AIMessagesRepo.count` 为准（对账时用）。
        """
        ...

    async def latest_by_scene(self, owner_oid: str, scene: str) -> Row | None:
        """The owner's newest conversation of ``scene``; ``None`` = 还没建过。"""
        ...

    async def list_stale_scenes(
        self, cutoff: datetime, *, limit: int = 500
    ) -> list[Row]:
        """Scene conversations with ``updated_at < cutoff`` (oldest first)."""
        ...

    async def count_stale_scenes(self, cutoff: datetime) -> int:
        """How many scene conversations are past the retention window."""
        ...

    async def delete_batched(
        self, oid: str, owner_oid: str, *, batch_size: int = 500
    ) -> int:
        """Delete the conversation row in bounded batches; returns rows deleted.

        只删会话行：消息与 run 必须由调用方**先**批删（``messages`` → ``runs`` →
        ``conversation``），Oracle 没有 ``ON DELETE CASCADE``，而一条 ``DELETE``
        级联掉十几万行 CLOB 会长时间持锁并撑爆 undo。
        """
        ...


class AIMessagesRepo(Protocol):
    """Data access for ``AI_MESSAGES`` (会话内消息, V035).

    Row shape::

        {"_id": <32 hex>, "conversation_oid": str, "seq": int,
         "role": "user" | "assistant" | "tool", "content": str | None,
         "tool_calls": str | None, "tool_call_id": str | None,
         "tool_name": str | None, "round_no": int | None,
         "finish_reason": str | None, "request_id": str | None,
         "prompt_tokens": int | None, "completion_tokens": int | None,
         "total_tokens": int | None, "cache_read_tokens": int | None,
         "cache_write_tokens": int | None, "created_at": datetime}

    ``seq`` is per-conversation and starts at 1. Token columns are ``None`` when
    the upstream did not report usage (same rule as ``ai_call_logs``: 0 is a real
    count, NULL is "unknown"). There is **no** ``summary`` role — the summary is a
    conversation-level watermark, not a message.
    """

    async def append_batch(
        self,
        conversation_oid: str,
        rows: Sequence[Mapping[str, Any]],
        *,
        usage: Mapping[str, int | None] | None = None,
    ) -> list[Row]:
        """Append assistant + **all** of its tool messages in one transaction.

        一轮的 ``assistant(tool_calls)`` 与它对应的**全部** ``tool`` 消息必须原子
        落库（评审 P1-3）：中断时留下「半条工具链」，下一次回放组装会被上游 400。
        所以这里一次收一个批次，锁会话行、连续分配 ``seq``、逐行插入，并在**同一
        事务**里把 ``usage`` 累加到会话行与 ``message_count``。

        ``seq`` 不接受调用方指定：会话行锁内的 ``MAX(seq)+1`` 是唯一的序号来源，
        客户端算出来的号在重试时会撞 ``uq_ai_messages_conv_seq``。

        每个 ``rows`` 元素是 ``{"oid", "role", "content"?, "tool_calls"?,
        "tool_call_id"?, "tool_name"?, "round_no"?, "finish_reason"?,
        "request_id"?, "prompt_tokens"?, ..., "cache_write_tokens"?}``：
        ``seq`` / ``created_at`` / ``conversation_oid`` 由本方法填写，传了也会被
        覆盖——它们必须来自锁内的事实，不是调用方的猜测。
        """
        ...

    async def next_seq(self, conversation_oid: str) -> int:
        """Return the next free ``seq`` after locking the conversation row.

        ``SELECT ... FOR UPDATE`` 必须落在**调用方的事务**里才有意义（提交即释放
        锁），因此这个方法只在注入 session 时用于「先占号、后写消息」的场景；
        :meth:`append_batch` 内部自己完成同样的事，不需要先调用它。
        """
        ...

    async def list_after(
        self, conversation_oid: str, after_seq: int = 0, *, limit: int = 50
    ) -> list[Row]:
        """Messages with ``seq > after_seq`` in ascending order (增量回放)."""
        ...

    async def count(self, conversation_oid: str) -> int:
        """Real row count of one conversation (对账用；列表读冗余列即可)."""
        ...

    async def purge_by_conversation(
        self, conversation_oid: str, *, batch_size: int = 500
    ) -> int:
        """Delete up to ``batch_size`` messages of one conversation.

        返回本批删除数：调用方循环到「不足一批」为止（沿用
        :mod:`tasks.retention` 的 ``_drain`` 范式）。``ROWNUM`` 而不是 ``IN``
        大列表：后者会撞上 Oracle 的 1000 项上限，也会让一条语句锁太多行。
        """
        ...


class AIRunsRepo(Protocol):
    """Data access for ``AI_RUNS`` (后台执行的智能体 run, V035).

    Row shape::

        {"_id": <32 hex = run_id>, "conversation_oid": str, "owner_oid": str,
         "client_msg_id": str | None,
         "status": "queued" | "running" | "succeeded" | "failed" | "cancelled"
                   | "interrupted",
         "model": str | None, "stop_reason": str | None, "rounds": int | None,
         "error_code": str | None, "error_message": str | None,
         "result_message_oid": str | None, "prompt_tokens": int | None,
         "completion_tokens": int | None, "total_tokens": int | None,
         "cancel_requested": bool, "heartbeat_at": datetime | None,
         "created_at": datetime, "started_at": datetime | None,
         "finished_at": datetime | None}

    ``_id`` doubles as ``ai_call_logs.request_id`` and
    ``ai_agent_events.request_id``. State moves by CAS (``WHERE status = ...``),
    so a terminal row can never be rewritten — 覆盖终态等于让「已经扣过的费、
    已经执行过的工具」在审计里消失。
    """

    async def insert_queued(
        self,
        *,
        run_id: str,
        conversation_oid: str,
        owner_oid: str,
        message_oid: str,
        content: str,
        client_msg_id: str | None = None,
        model: str | None = None,
    ) -> tuple[Row, bool]:
        """Persist the user message + a ``queued`` run atomically.

        返回 ``(run_row, created)``；``run_row["message_oid"]`` 是这条用户消息的
        oid（既是本次新建的，也是去重命中的旧 run 的那条）。

        一个事务内按序：锁会话行 → 分配 ``seq`` → 写用户消息 → 写 run → 累加
        会话计数。**这是 run 进入 ``running`` 前唯一的持久化**（评审 P1-4 幂等
        边界），所以崩溃的 running 不会重跑扣费，queued 重跑也零重复。

        ``client_msg_id`` 撞 ``uq_ai_runs_client_msg`` → 回滚本次插入并返回**已
        存在**的 run（``created=False``）：不重复写消息、不重复计费。撞活跃唯一
        索引（同会话/同用户已有 queued|running）→ 抛 :class:`ActiveRunConflict`，
        同样不留孤儿消息。
        """
        ...

    async def claim_next(self) -> Row | None:
        """Claim the oldest claimable ``queued`` run, or ``None`` when idle.

        ``SELECT ... FOR UPDATE SKIP LOCKED`` + 同事务 CAS 到 ``running``（写
        ``started_at`` / ``heartbeat_at``）。跳过 ``cancel_requested=1`` 的行：
        已被取消的排队任务不该占一次上游调用。单 worker 下它就是队列，多 worker
        时 ``SKIP LOCKED`` 让两个泵各领一行而不是互相等锁。
        """
        ...

    async def get(self, run_id: str, owner_oid: str) -> Row | None:
        """Return one run of *this* owner, or ``None`` (foreign runs are 404)."""
        ...

    async def heartbeat(self, run_id: str, *, at: datetime | None = None) -> bool:
        """Refresh ``heartbeat_at`` while the run is still ``running``.

        只写这一列、且只对 running 行生效：终态行上的心跳是噪声，而「心跳还在
        跳但 run 已经 interrupted」会让 watchdog 与执行方各说各话。
        """
        ...

    async def finish(
        self,
        run_id: str,
        *,
        status: str,
        stop_reason: str | None = None,
        rounds: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        result_message_oid: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        finished_at: datetime | None = None,
    ) -> bool:
        """CAS ``running → <terminal>``; ``False`` = it was already terminal.

        调用方拿到 ``False`` 必须**丢弃本轮**（不再写消息、不再扣额度）：这正是
        「watchdog 已判僵尸、执行方还在写」的收敛点（评审 P0-2）。
        """
        ...

    async def request_cancel(self, run_id: str, owner_oid: str) -> bool:
        """Set ``cancel_requested=1`` on a non-terminal run of this owner.

        ``False`` = 不存在、不是本人、或已是终态（→ ``AI_RUN_NOT_CANCELLABLE``）。
        取消不是状态：已执行的工具不能回滚，worker 在轮边界与工具执行前读它。
        """
        ...

    async def mark_cancelled_if_queued(self, run_id: str) -> bool:
        """CAS ``queued → cancelled`` (还没开跑就不必等 worker)。"""
        ...

    async def cancel_queued_requested(self, *, at: datetime | None = None) -> int:
        """Converge every ``queued`` run that already has ``cancel_requested=1``.

        返回被写进 ``cancelled`` 的行数。这不是正常的取消路径（那条由
        :meth:`mark_cancelled_if_queued` 一次 CAS 完成），而是它的**故障兜底**：
        置标志与写终态之间进程退出/抛错时，这行既不会被 :meth:`claim_next` 领取
        （它跳过已取消的行），也不会被 watchdog 看见（只看 ``running``），于是
        永久占住活跃唯一索引。启动收敛与 watchdog 各调一次。
        """
        ...

    async def list_active(self, owner_oid: str) -> list[Row]:
        """Every ``queued|running`` run of this owner, oldest first.

        重连横幅（``GET /runs?active=1``）用它恢复在飞任务；同一次查询的
        ``len()`` 也是「该用户活跃 run 数」——``AI_RUN_MAX_PER_USER`` 的闸门在
        :meth:`insert_queued` 的事务里判，不在这里。
        """
        ...

    async def list_recent(self, owner_oid: str, limit: int) -> list[Row]:
        """The newest ``limit`` runs of this owner, any status, ``created_at`` desc.

        个人首页（``GET /runs?active=0&limit=N``）用：只按 ``owner_oid`` 过滤，
        别人的 run 不可能出现在结果里。``limit`` 由服务层夹到 1..10，仓储不再
        做第二套边界判断（它只是 SQL 的 ``FETCH FIRST``）。
        """
        ...

    async def interrupt_running(
        self,
        *,
        error_code: str = "AI_RUN_INTERRUPTED",
        message: str = "执行进程失去心跳",
    ) -> int:
        """Startup convergence: every ``running`` row becomes ``interrupted``.

        单 worker 下「进程刚启动」等价于「没有任何合法在跑的任务」，残留的
        running 只能是上次崩溃留下的。``queued`` **不**动：pump 起来后会领取。
        """
        ...

    async def interrupt_stale(
        self,
        cutoff: datetime,
        *,
        error_code: str = "AI_RUN_INTERRUPTED",
        message: str = "执行进程失去心跳",
    ) -> int:
        """Watchdog: ``running`` with ``heartbeat_at < cutoff`` → ``interrupted``.

        ``cutoff`` 由调用方按 ``max(360, run_timeout + 60)`` 算好（默认 360s）：
        阈值必须大于单次上游调用的最坏耗时，否则正常的长轮次会被误杀。
        """
        ...

    async def purge_by_conversation(
        self, conversation_oid: str, *, batch_size: int = 500
    ) -> int:
        """Delete up to ``batch_size`` runs of one conversation (批删, ROWNUM)."""
        ...


class AiScheduledTasksRepo(Protocol):
    """Data access for ``AI_SCHEDULED_TASKS`` (AI 定时任务, V040).

    Row shape (``_id`` 是 24 位 hex 的 ``OID``)::

        {"_id": str, "owner_oid": str, "name": str, "prompt_text": str,
         "review_status": "draft" | "pending" | "approved" | "rejected",
         "reviewed_by_oid": str | None, "reviewed_at": datetime | None,
         "review_note": str | None,
         "schedule_kind": "interval" | "cron",
         "interval_minutes": int, "cron_expr": str | None,
         "enabled": bool, "model": str | None,
         "last_run_at": datetime | None, "next_run_at": datetime | None,
         "run_count": int, "last_status": str | None,
         "last_error": str | None,
         "created_at": datetime, "updated_at": datetime}

    ``review_status`` 与 ``enabled`` 是两个独立事实：改 ``prompt_text`` 必须重新
    过审（服务层把状态打回 ``draft``），但排程与启用状态不受影响。
    """

    async def create(
        self,
        *,
        owner_oid: str,
        name: str,
        prompt_text: str,
        schedule_kind: str,
        interval_minutes: int,
        cron_expr: str | None,
        next_run_at: datetime | None,
        run_at: datetime | None = None,
        review_status: str = "draft",
        model: str | None = None,
    ) -> Row:
        """Insert one task and return the full row (created 视图直接用它)."""
        ...

    async def get(self, oid: str) -> Row | None:
        """Return one task regardless of owner, or ``None``."""
        ...

    async def list_for_owner(
        self, owner_oid: str, *, limit: int = 200
    ) -> list[Row]:
        """This owner's tasks, newest first."""
        ...

    async def list_all(
        self,
        *,
        owner_oid: str | None = None,
        review_status: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Row]:
        """Admin view: optionally filtered by owner and review status, newest first."""
        ...

    async def update(self, oid: str, values: Mapping[str, Any]) -> bool:
        """Partial update (column whitelist); ``False`` = row absent."""
        ...

    async def delete(self, oid: str) -> bool:
        """Delete one task; ``False`` = row absent."""
        ...

    async def count_active_for_owner(self, owner_oid: str) -> int:
        """配额口径：``review_status != 'rejected'`` 的行数（驳回不占额度）。"""
        ...

    async def claim_due(
        self, now: datetime, *, limit: int = 50
    ) -> list[Row]:
        """Snapshot due tasks, then CAS each one; losers are not returned.

        ``claim`` 的期望值是快照里的 ``last_run_at``：并发的两个 tick 只有一个能
        命中，而崩溃后的下一次 tick 用新快照自然重试，不会把任务卡死。
        """
        ...

    async def claim(
        self, oid: str, now: datetime, *, expected: datetime | None
    ) -> bool:
        """CAS one task: ``next_run_at <= now`` **and** ``last_run_at = expected``."""
        ...

    async def mark_result(
        self,
        oid: str,
        *,
        last_status: str,
        next_run_at: datetime,
        last_error: str | None = None,
        counted: bool = False,
    ) -> bool:
        """Write the scheduling verdict and advance ``next_run_at``.

        执行、跳过、失败都必须调用：``next_run_at`` 不推进会让坏任务被每个 tick
        重试一次。``counted=True`` 才 ``run_count + 1``（跳过不加计数）。
        """
        ...

    async def list_runs(
        self, task_oid: str, *, page: int = 1, size: int = 20
    ) -> tuple[list[Row], int]:
        """``(runs, total)`` for one task, newest first.

        run 行形状：``{"_id","status","model","stop_reason","error_code",
        "error_message","created_at","started_at","finished_at"}``。
        """
        ...

    async def last_conversation_oid(self, task_oid: str) -> str | None:
        """该任务最近一次 run 的会话（复用它，别每次执行都新建会话）。"""
        ...

    async def attach_run_task(self, run_id: str, task_oid: str) -> bool:
        """归因一次已入队的 run（``insert_queued`` 不认 ``task_oid``）。"""
        ...
