"""后台 run 队列：进程内单泵 + 每 run 独立 task（契约 §2.3 / §3.2 / §4）。

为什么是进程内：部署是单 worker（``config/supervisor.conf`` 没有 ``--workers``），
``asyncio`` 本身就是队列；多 worker 需要外部租约，本期明确不做（§0.3）。所以本
模块的全局状态（泵、信号量、在飞表）就是「当前队列」的唯一真相，与
``tasks/retention.py`` 的进程内作业同一范式。

三条不能省的机制：

* **领取是 CAS**：``claim_next`` 在同一个事务里 ``queued → running``（行锁 +
  ``SKIP LOCKED``），两个泵不会领到同一条。
* **心跳与上游等待解耦**：每 run 一个独立 ticker 刷 ``heartbeat_at``。只在轮边界
  刷的话，单轮最坏能占满整个 run deadline（``executor.run_timeout_seconds()``），
  watchdog 会把活着的任务判成僵尸（§4.3）。
* **终态只能 CAS 一次**：``finish`` 回 ``False`` 说明 watchdog 已经把它写成
  ``interrupted``；这时统计不许再写——「已扣的费」与 run 记录必须对得上。

`should_stop` 是 **async** 的：executor 侧必须 ``await``。直接 ``if should_stop()``
拿到的是协程对象（恒真），会让整次 run 在第二轮就停住。
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence
from uuid import uuid4

import settings

from db import backends
from db.repos.protocols import ActiveRunConflict
from services.agent import executor, proposal as proposal_service
from services.agent.tools import WRITE
from services.agent.tool_impl import AgentError
from services.ai import conversations

_logger = logging.getLogger(__name__)

# 默认值全部写在这里：部署差异走 ``settings``，实现差异才改代码。
DEFAULT_CONCURRENCY = 2
DEFAULT_MAX_RUNS_PER_USER = 1
DEFAULT_HEARTBEAT_SECONDS = 30
DEFAULT_IDLE_SECONDS = 1.0
DEFAULT_STALE_SECONDS = 360
STALE_MARGIN_SECONDS = 60
# 单次 run 从库里回放的历史消息上限：超长会话该由压缩截断（§5），这里只是兜住
# 「一次把上万条 CLOB 拉进内存」的病态情形。
DEFAULT_HISTORY_MESSAGES = 400
HISTORY_PAGE = 100

# 工具结果落库长度与模型看到的同长（executor 侧已按 8000 截断，这里再兜一次）。
MAX_TOOL_CONTENT_CHARS = 8000
# ``ai_runs.error_message`` 是 VARCHAR2(500)，超了是 ORA-12899。
MAX_ERROR_MESSAGE_CHARS = 500

TERMINAL_STOP_REASONS = frozenset(
    {"completed", "max_iterations", "token_budget", "timeout", "cancelled", "error"}
)
ACTIVE_STATUSES = ("queued", "running")
RUN_NOT_FOUND = "AI_RUN_NOT_FOUND"
RUN_NOT_CANCELLABLE = "AI_RUN_NOT_CANCELLABLE"
RUN_CONFLICT = conversations.RUN_CONFLICT
INTERRUPTED_CODE = "AI_RUN_INTERRUPTED"
INTERRUPTED_MESSAGE = "执行进程失去心跳"

# ---------------------------------------------------------------------------
# 进程内状态
#
# ``_pump_task`` 是唯一的泵；``_in_flight`` 给 watchdog 用（本地心跳时间戳是它
# 自己的事实，不读库：watchdog 要判「这个协程还活着吗」，而库里的 heartbeat_at
# 正是它自己刷的，读库只会得到「我刚写过」的答案）。
# ---------------------------------------------------------------------------
_pump_task: "asyncio.Task | None" = None
_semaphore: "asyncio.Semaphore | None" = None
_wake: "asyncio.Event | None" = None
_loop: "asyncio.AbstractEventLoop | None" = None
_in_flight: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# 配置读取（非法值一律回落默认，绝不把部署配置错误变成启动失败）
# ---------------------------------------------------------------------------

def _positive_int(name: str, default: int) -> int:
    raw = getattr(settings, name, default)
    if isinstance(raw, bool):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def concurrency() -> int:
    """同时在跑的 run 数（``AI_RUN_CONCURRENCY``，默认 2）。"""
    return _positive_int("AI_RUN_CONCURRENCY", DEFAULT_CONCURRENCY)


def max_runs_per_user() -> int:
    """单用户活跃 run 上限（``AI_RUN_MAX_PER_USER``，默认 1）。

    入队时的第一道闸；真正的裁决者是 ``uq_ai_runs_active_owner`` 这个函数唯一
    索引（活跃行不计 NULL），两者冲突时以数据库为准（ORA-00001 → 409）。
    """
    return _positive_int("AI_RUN_MAX_PER_USER", DEFAULT_MAX_RUNS_PER_USER)


def heartbeat_seconds() -> float:
    """心跳周期（``AI_RUN_HEARTBEAT_SECONDS``，默认 30s）。"""
    return float(_positive_int("AI_RUN_HEARTBEAT_SECONDS", DEFAULT_HEARTBEAT_SECONDS))


def stale_seconds() -> int:
    """判僵尸的阈值：``max(360, run_timeout + 60)`` 是**下限**。

    阈值必须大于单次上游调用的最坏耗时，否则正常的长轮次会被误杀；配得比下限还
    小的一律抬回下限（§4.3 的硬约束）。
    """
    floor = max(
        DEFAULT_STALE_SECONDS, executor.run_timeout_seconds() + STALE_MARGIN_SECONDS
    )
    return max(floor, _positive_int("AI_RUN_STALE_SECONDS", floor))


# ---------------------------------------------------------------------------
# 仓储入口（每次现取：仓储自己开 session，长事务不藏在模块状态里）
# ---------------------------------------------------------------------------

def _runs() -> Any:
    return backends.get_ai_runs_repo()


def _messages() -> Any:
    return backends.get_ai_messages_repo()


def _conversations() -> Any:
    """会话行（压缩水位只用 ``set_summary`` 这一个写入口）。"""
    return backends.get_ai_conversations_repo()


def _users() -> Any:
    return backends.get_users_repo()


def _events() -> Any:
    return backends.get_ai_agent_events_repo()


# ---------------------------------------------------------------------------
# 视图与错误
# ---------------------------------------------------------------------------

def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _iso(value: object) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return value if isinstance(value, str) else None


def _clip(text: object, limit: int) -> str | None:
    if text is None:
        return None
    value = str(text)
    return value if len(value) <= limit else value[: limit - 1] + "…"


def run_view(row: Mapping[str, Any]) -> dict:
    """run 视图（§3.2）。计数列 NULL 表示「还没有结果」，展示成 0 而不是 ``—``：
    前端拿它画进度，缺字段比 0 更容易让页面崩。"""
    return {
        "oid": str(row.get("_id") or ""),
        "conversation_oid": str(row.get("conversation_oid") or ""),
        "status": row.get("status") or "interrupted",
        "model": row.get("model"),
        "stop_reason": row.get("stop_reason"),
        "rounds": _int(row.get("rounds")),
        "error_code": row.get("error_code"),
        "error_message": row.get("error_message"),
        "result_message_oid": row.get("result_message_oid"),
        "prompt_tokens": _int(row.get("prompt_tokens")),
        "completion_tokens": _int(row.get("completion_tokens")),
        "total_tokens": _int(row.get("total_tokens")),
        "cancel_requested": bool(row.get("cancel_requested")),
        "created_at": _iso(row.get("created_at")),
        "started_at": _iso(row.get("started_at")),
        "finished_at": _iso(row.get("finished_at")),
    }


# ---------------------------------------------------------------------------
# 入队（§2.3 提交事务的调用侧）
# ---------------------------------------------------------------------------

async def submit(
    *,
    user: Mapping[str, Any],
    conversation_oid: str,
    content: str,
    model: str | None = None,
    client_msg_id: str | None = None,
) -> dict:
    """把一条用户消息 + 一个 ``queued`` run 原子落库。

    调用方负责紧跟着 ``await kick()``——这里刻意不 kick：提交是纯数据操作，起泵
    是编排决定（也让「只入队不执行」的测试不必收拾一个跑着的协程）。

    幂等：带 ``client_msg_id`` 时**不做**活跃 run 预检，直接交给仓储。预检会把
    「客户端重试同一个键」也判成冲突，而 ``insert_queued`` 能区分这两种冲突
    （撞幂等键 → 返回已有 run；撞活跃唯一索引 → :class:`ActiveRunConflict`）。
    """
    owner_oid = str(user.get("id") or "")
    conversation = await conversations.require_row(user=user, oid=str(conversation_oid))
    if str(conversation.get("mode")) != "agent":
        # 往 chat 会话提交后台任务等于把同步 SSE 的产物换成任务流：语义不同，
        # 必须拒绝（§3.4）。
        raise AgentError(
            conversations.MODE_MISMATCH,
            "agent runs require an agent conversation",
            409,
        )

    text = str(content or "").strip()
    if not text:
        raise AgentError("AI_AGENT_BAD_ARGUMENTS", "content must not be empty", 400)

    if client_msg_id is None:
        # 第一道闸：应用层先看着像冲突就回 409（省一次必然失败的插入）。第二道是
        # 数据库的函数唯一索引，两道都要有。
        active = await _runs().list_active(owner_oid)
        for row in active:
            if str(row.get("conversation_oid")) == str(conversation_oid):
                raise AgentError(
                    RUN_CONFLICT, "this conversation already has an active run", 409
                )
        if len(active) >= max_runs_per_user():
            raise AgentError(RUN_CONFLICT, "too many active runs", 409)

    run_id = uuid4().hex
    try:
        row, created = await _runs().insert_queued(
            run_id=run_id,
            conversation_oid=str(conversation_oid),
            owner_oid=owner_oid,
            message_oid=uuid4().hex,
            content=text,
            client_msg_id=(str(client_msg_id).strip() or None) if client_msg_id else None,
            model=(str(model).strip() or None) if model else None,
        )
    except ActiveRunConflict as exc:
        raise AgentError(
            RUN_CONFLICT, "an active run already exists", 409
        ) from exc

    # 首条用户消息定标题（§2.1）。只在「还没有标题」时写一次：之后改名是用户的
    # 事，重试或第二条消息都不该把标题改回去。
    if created and not str(conversation.get("title") or "").strip():
        try:
            await conversations.retitle(
                owner_oid=owner_oid, oid=str(conversation_oid), title=conversations.title_from(text)
            )
        except Exception:  # noqa: BLE001 - 标题是展示信息，写失败不该废掉这次提交
            _logger.warning("ai conversation title backfill failed")

    payload = {
        "run_id": str(row.get("_id") or run_id),
        "message_oid": str(row.get("message_oid") or ""),
        "status": "queued",
    }
    if not created:
        payload["deduped"] = True
    return payload


# ---------------------------------------------------------------------------
# 取消 / 启动收敛 / watchdog
# ---------------------------------------------------------------------------

async def cancel(*, user: Mapping[str, Any], run_id: str) -> dict:
    """请求中止一次 run。``queued`` 直接进终态，``running`` 只置标志。

    已执行的工具不能回滚，所以取消不是「撤销」而是「到此为止」：worker 在轮边界与
    每次工具执行前读这个标志，消息照常落库（§3.2）。
    """
    owner_oid = str(user.get("id") or "")
    row = await _runs().get(str(run_id), owner_oid)
    if row is None:
        raise AgentError(RUN_NOT_FOUND, "run not found", 404)
    if str(row.get("status")) not in ACTIVE_STATUSES:
        raise AgentError(RUN_NOT_CANCELLABLE, "run is already finished", 409)

    marked = await _runs().request_cancel(str(run_id), owner_oid)
    if not marked:
        latest = await _runs().get(str(run_id), owner_oid)
        if latest is None:
            raise AgentError(RUN_NOT_FOUND, "run not found", 404)
        if not latest.get("cancel_requested"):
            # CAS 抢不到：这一瞬间它已经进了终态。
            raise AgentError(RUN_NOT_CANCELLABLE, "run is already finished", 409)
        return {"status": str(latest.get("status"))}

    if str(row.get("status")) == "queued":
        # 还没开跑：不必等 worker 在轮边界看到标志（也免得它占一次上游调用）。
        await _runs().mark_cancelled_if_queued(str(run_id))

    latest = await _runs().get(str(run_id), owner_oid) or row
    return {"status": str(latest.get("status"))}


async def recover_on_startup() -> int:
    """启动收敛：``running → interrupted``，``queued`` 原样保留（§4.3）。

    单 worker 下「进程刚起来」等价于「没有任何合法在跑的任务」，残留的 running
    只可能是上次崩溃留下的；而 queued 是**还没开始**的任务，起泵后自然续跑。
    崩溃的 running 不重跑，是因为它唯一的持久化只有那条用户消息（§4.5）——重跑
    会把已经发生的计费再花一遍。
    """
    try:
        interrupted = await _runs().interrupt_running(
            error_code=INTERRUPTED_CODE, message="执行进程重启"
        )
    except Exception:  # noqa: BLE001 - 收敛失败不该挡住启动（下个 watchdog 还会试）
        _logger.exception("ai run startup recovery failed")
        return 0
    if interrupted:
        _logger.info("ai run queue: %s run(s) marked interrupted at startup", interrupted)
    return int(interrupted or 0)


async def watchdog_tick() -> int:
    """看门狗：心跳早于阈值的 ``running`` 判僵尸，并取消本进程里对应的协程。

    「命中后取消对应 task」是关键的一半：只改库不掐协程，执行方会在终态行上继续
    写消息、继续扣额度（评审 P0-2）。本地心跳时间戳用来判断「哪个协程该掐」——
    库里的 heartbeat_at 就是这个协程刷的，读库等于自问自答。
    """
    cutoff = datetime.now() - timedelta(seconds=stale_seconds())
    try:
        hit = await _runs().interrupt_stale(
            cutoff, error_code=INTERRUPTED_CODE, message=INTERRUPTED_MESSAGE
        )
    except Exception:  # noqa: BLE001 - watchdog 是旁路，失败不能拖垮调度器
        _logger.exception("ai run watchdog failed")
        return 0

    for run_id, entry in list(_in_flight.items()):
        if entry.get("heartbeat") is None or entry["heartbeat"] >= cutoff:
            continue
        task = entry.get("task")
        if task is None or task.done():
            continue
        _logger.warning("ai run %s lost its heartbeat, cancelling", run_id)
        task.cancel()
    return int(hit or 0)


# ---------------------------------------------------------------------------
# 泵
# ---------------------------------------------------------------------------

def _primitives() -> tuple[asyncio.Semaphore, asyncio.Event]:
    """按事件循环缓存原语。

    生产里只有一个循环，这里等价于「建一次」；但 ``asyncio`` 的原语会绑在创建它
    的循环上，跨循环复用直接抛 ``RuntimeError``（测试客户端每个请求一个新循环）。
    换循环时旧泵已经随旧循环死了，顺手把句柄丢掉，让 ``kick()`` 在新循环里重建。
    """
    global _semaphore, _wake, _loop, _pump_task
    loop = asyncio.get_running_loop()
    if _loop is not loop or _semaphore is None or _wake is None:
        _loop = loop
        _semaphore = asyncio.Semaphore(concurrency())
        _wake = asyncio.Event()
        if _pump_task is not None and not _pump_task.done():
            _pump_task.cancel()
        _pump_task = None
    return _semaphore, _wake


async def kick() -> None:
    """确保泵在跑（幂等）；已在跑就催一次，让它立刻去领一行。

    幂等是必须的：每个提交都会 kick 一次，若每次都起协程，队列里就有一堆泵在
    同一个 ``claim_next`` 上互抢（正确但白烧连接）。
    """
    global _pump_task
    semaphore, wake = _primitives()
    if _pump_task is not None and not _pump_task.done():
        wake.set()
        return
    _pump_task = asyncio.create_task(_pump(semaphore, wake), name="ai-run-pump")


async def _pump(semaphore: asyncio.Semaphore, wake: asyncio.Event) -> None:
    """领取循环：串行领、并发跑。异常一律就地吞掉——泵死了整个队列就停了。"""
    while True:
        await semaphore.acquire()
        run: dict | None = None
        try:
            run = await _runs().claim_next()
        except asyncio.CancelledError:
            semaphore.release()
            raise
        except Exception:  # noqa: BLE001 - 一次领取失败不该终止整个泵
            _logger.exception("ai run claim failed")
        if run is None:
            semaphore.release()
            await _idle(wake)
            continue
        _spawn(run, semaphore, wake)


def _spawn(run: Mapping[str, Any], semaphore: asyncio.Semaphore, wake: asyncio.Event) -> None:
    run_id = str(run.get("_id") or "")
    # 契约：claim 之后、开跑之前就是这个初值——若进程在 ticker 起第一拍之前被掐死，
    # watchdog 仍有可比较的时间戳。
    _in_flight[run_id] = {"task": None, "heartbeat": datetime.now()}
    task = asyncio.create_task(_execute(run, semaphore), name=f"ai-run-{run_id}")
    _in_flight[run_id]["task"] = task
    task.add_done_callback(lambda done, rid=run_id: _retire(rid, done, wake))


def _retire(run_id: str, task: asyncio.Task, wake: asyncio.Event) -> None:
    _in_flight.pop(run_id, None)
    wake.set()  # 空出一个并发额度：立刻去领下一行，不等下一个 tick
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:  # 消费异常，免得 asyncio 报 "never retrieved"
        _logger.error("ai run task %s crashed: %r", run_id, error)


async def _idle(wake: asyncio.Event) -> None:
    """空闲等待：有 kick 就立刻醒，没有就一秒看一次队列。"""
    try:
        await asyncio.wait_for(wake.wait(), timeout=DEFAULT_IDLE_SECONDS)
    except (asyncio.TimeoutError, TimeoutError):
        return
    wake.clear()


def reset() -> None:
    """清空进程内状态（**只给测试**）。

    新事件循环里旧 ``Event`` / ``Semaphore`` 绑的是上一个循环；不清掉的话第二个
    用例的 ``kick()`` 会复用一个已关闭循环的原语。
    """
    global _pump_task, _semaphore, _wake, _loop
    for entry in list(_in_flight.values()):
        task = entry.get("task")
        if task is not None and not task.done():
            task.cancel()
    _in_flight.clear()
    if _pump_task is not None and not _pump_task.done():
        _pump_task.cancel()
    _pump_task = None
    _semaphore = None
    _wake = None
    _loop = None


# ---------------------------------------------------------------------------
# 单次执行
# ---------------------------------------------------------------------------

async def _execute(run: Mapping[str, Any], semaphore: asyncio.Semaphore) -> None:
    run_id = str(run.get("_id") or "")
    conversation_oid = str(run.get("conversation_oid") or "")
    owner_oid = str(run.get("owner_oid") or "")
    state: dict[str, Any] = {
        "rounds": 0,
        "result_message_oid": None,
        "usage": {},
        "cancelled": False,
        "interrupted": False,
    }
    stop = asyncio.Event()
    ticker = asyncio.create_task(
        _heartbeat_loop(run_id, stop, state), name=f"ai-run-heartbeat-{run_id}"
    )
    try:
        user = await _owner_user(owner_oid)
        conversation = await conversations.fetch_row(owner_oid, conversation_oid)
        if conversation is None:
            await _finish(
                run_id,
                status="failed",
                error_code=conversations.NOT_FOUND,
                error_message="会话已被删除",
            )
            return
        messages, summary = await _executor_messages(conversation)
        result = await executor.run(
            user=user,
            messages=messages,
            model=run.get("model"),
            request_id=run_id,
            # 摘要**不走 messages**（executor 只允许 user/assistant/tool，客户端
            # 塞 system 一律 400）：它由 executor 按 §5.1 拼成第二条 system。
            summary=summary,
            on_round=_on_round(run_id, conversation_oid, owner_oid, state),
            should_stop=_should_stop(run_id, owner_oid, state),
            on_summary=_on_summary(conversation, conversation_oid, state),
        )
        status, stop_reason = _outcome(result, state)
        await _finish(
            run_id,
            status=status,
            stop_reason=stop_reason,
            rounds=state["rounds"] or _int((result or {}).get("rounds")),
            result_message_oid=state["result_message_oid"],
            usage=state["usage"],
            error_code=INTERRUPTED_CODE if status == "interrupted" else None,
            error_message="执行已被中止" if status == "interrupted" else None,
        )
    except asyncio.CancelledError:
        # watchdog 或进程退出：终态通常已经由 watchdog 写好，这里补一次 CAS
        # （写不上也无妨），然后让取消继续向上传播。
        await _finish(
            run_id,
            status="interrupted",
            error_code=INTERRUPTED_CODE,
            error_message="执行已被中止",
            rounds=state["rounds"],
            usage=state["usage"],
        )
        raise
    except AgentError as exc:
        await _finish(
            run_id,
            status="cancelled" if state["cancelled"] else "failed",
            stop_reason="cancelled" if state["cancelled"] else "error",
            error_code=exc.code,
            error_message=exc.message,
            rounds=state["rounds"],
            result_message_oid=state["result_message_oid"],
            usage=state["usage"],
        )
    except Exception as exc:  # noqa: BLE001 - 一次 run 的崩溃不能带走泵
        _logger.exception("ai run %s failed", run_id)
        await _finish(
            run_id,
            status="failed",
            stop_reason="error",
            error_code="AI_AGENT_INTERNAL_ERROR",
            # 只留类型名：异常正文可能带 DSN / 内部路径，而这一列会直接进响应。
            error_message=type(exc).__name__,
            rounds=state["rounds"],
            usage=state["usage"],
        )
    finally:
        stop.set()
        ticker.cancel()
        semaphore.release()


async def _owner_user(owner_oid: str) -> dict:
    """执行身份：**读执行时刻的角色**，不用提交时那份 token 快照。

    角色可能在这几秒里被改掉（提案/额度/频控都按角色算），拿旧快照跑完一整次
    run 等于用过期权限执行写工具。
    """
    row = await _users().get_for_auth(owner_oid)
    roles = list(row.get("roles") or []) if isinstance(row, Mapping) else []
    return {"id": owner_oid, "per": roles}


def _outcome(result: Mapping[str, Any] | None, state: Mapping[str, Any]) -> tuple[str, str | None]:
    """``executor`` 的返回 → ``(status, stop_reason)``。

    取消优先于一切：用户点了中止，结果里就算带了完整答案，这次 run 的语义也是
    「被用户停掉」（消息已经落库，正文不会丢）。
    """
    if state.get("cancelled"):
        return "cancelled", "cancelled"
    if state.get("interrupted"):
        return "interrupted", None
    reason = (result or {}).get("stop_reason")
    if reason is None:
        return "succeeded", "completed"
    text = str(reason)
    if text in TERMINAL_STOP_REASONS:
        return "succeeded", text
    # 未知的 stop_reason：列宽 24，能放就原样留（对诊断有价值），放不下才退化。
    return "succeeded", text[:24]


async def _finish(
    run_id: str,
    *,
    status: str,
    stop_reason: str | None = None,
    rounds: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    result_message_oid: str | None = None,
    usage: Mapping[str, Any] | None = None,
) -> bool:
    """收尾：CAS ``running → <terminal>``；``False`` = 已经被别人写进终态。"""
    totals = usage or {}
    try:
        written = await _runs().finish(
            run_id,
            status=status,
            stop_reason=stop_reason,
            rounds=int(rounds) if rounds is not None else None,
            error_code=error_code,
            error_message=_clip(error_message, MAX_ERROR_MESSAGE_CHARS),
            result_message_oid=result_message_oid,
            prompt_tokens=_optional_int(totals.get("prompt_tokens")),
            completion_tokens=_optional_int(totals.get("completion_tokens")),
            total_tokens=_optional_int(totals.get("total_tokens")),
        )
    except Exception:  # noqa: BLE001 - 收尾失败只能记日志：进程还要继续跑别的 run
        _logger.exception("ai run %s finish failed", run_id)
        return False
    if not written:
        _logger.info("ai run %s was already terminal, result dropped", run_id)
    return bool(written)


async def _still_running(run_id: str, owner_oid: str) -> bool:
    """写任何东西之前的最后一道校验：run 还必须是 ``running``。

    与 ``finish`` 的 CAS 配对使用（评审 P0-2）：watchdog 把行判成 ``interrupted``
    之后，这一轮的执行结果必须被丢弃，而不是写在终态行上面。
    """
    try:
        row = await _runs().get(run_id, owner_oid)
    except Exception:  # noqa: BLE001 - 读不到就当作还能写：一次查询抖动不该丢一轮
        _logger.warning("ai run %s status check failed", run_id)
        return True
    return row is not None and str(row.get("status")) == "running"


# ---------------------------------------------------------------------------
# 心跳
# ---------------------------------------------------------------------------

async def _heartbeat_loop(run_id: str, stop: asyncio.Event, state: dict) -> None:
    """30s 独立 ticker：与上游等待无关地刷 ``heartbeat_at``。

    第二职责：``heartbeat`` 返回 False 说明行已经不在 ``running``（watchdog 判了
    僵尸），把 ``interrupted`` 立起来让轮边界收手——比等 watchdog 掐协程更快。
    """
    interval = heartbeat_seconds()
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except (asyncio.TimeoutError, TimeoutError):
            pass
        try:
            alive = await _runs().heartbeat(run_id)
        except Exception:  # noqa: BLE001 - 心跳失败不等于任务失败，下一拍再试
            _logger.warning("ai run %s heartbeat failed", run_id)
            continue
        if not alive:
            state["interrupted"] = True
            return
        entry = _in_flight.get(run_id)
        if entry is not None:
            entry["heartbeat"] = datetime.now()


# ---------------------------------------------------------------------------
# executor 的回调
# ---------------------------------------------------------------------------

def _should_stop(run_id: str, owner_oid: str, state: dict):
    """executor 的闸：轮边界 + 每次工具执行前调用（**必须 await**）。"""

    async def should_stop() -> bool:
        if state.get("cancelled") or state.get("interrupted"):
            return True
        try:
            row = await _runs().get(run_id, owner_oid)
        except Exception:  # noqa: BLE001 - 读不到就继续：多跑一轮好过丢掉整次执行
            _logger.warning("ai run %s cancel check failed", run_id)
            return False
        if row is None or str(row.get("status")) != "running":
            state["interrupted"] = True
            return True
        if row.get("cancel_requested"):
            state["cancelled"] = True
            return True
        return False

    return should_stop


def _on_round(run_id: str, conversation_oid: str, owner_oid: str, state: dict):
    """每轮收尾的唯一入库点：assistant + 全部 tool 消息 + 会话用量，一个事务。

    executor 的实参形状是 ``on_round(payload)``（见 ``executor._round_payload``）。
    同时保留 ``(round_no, messages, usage)`` 旧形状的解析：一个"少一个参数"的
    ``TypeError`` 会发生在已经扣过费之后，看起来像执行失败，而真正的原因只是两边
    的调用拼法不同——所以入口先把两种形状归一到同一份内部结构。
    """

    async def on_round(*args: Any, **kwargs: Any) -> None:
        payload = _normalise_round(args, kwargs)
        round_no = int(payload["round_no"])
        if round_no <= 0:
            return
        if state.get("cancelled") or state.get("interrupted"):
            # 终态行上不再写消息（评审 P0-2）。
            return
        if not await _still_running(run_id, owner_oid):
            state["interrupted"] = True
            return

        rows = _round_rows(
            run_id,
            round_no,
            assistant=payload["assistant"],
            tools=payload["tools"],
            tokens=payload["tokens"],
        )
        if not rows:
            return
        # 一个 ``append_batch`` = 一个事务：锁会话行、连续分配 seq、插入本体、
        # 累加会话 token/context/count。半条工具链在回放时会被上游 400。
        await conversations.append_round(
            conversation_oid, rows, usage=_usage_block(payload["tokens"])
        )

        state["rounds"] = max(int(state.get("rounds") or 0), round_no)
        _accumulate(state.setdefault("usage", {}), payload["tokens"])
        if not payload["assistant"].get("tool_calls"):
            # 最后一轮没有工具调用 = 这次 run 的最终回答（§2.3 的
            # ``result_message_oid``）。
            state["result_message_oid"] = rows[0]["oid"]

    return on_round


def _on_summary(conversation: dict, conversation_oid: str, state: dict):
    """压缩结果落库（契约 §5.3 的在飞重建）。

    executor 已经把摘要重建进它自己的对话列表了，这里只负责**持久化**并同步内存
    里的会话行：下一次 run 组装历史时才会跳过 ``seq <= upto_seq`` 的原文。写失败
    只记日志——压缩本身已经完成，丢掉这条摘要的代价是「下一轮再压一次」，而不是
    这次 run 的结果不对。
    """

    async def on_summary(summary: object, upto_seq: object) -> None:
        text = str(summary or "").strip()
        seq = _optional_int(upto_seq)
        if not text or not seq:
            return
        if state.get("compacted"):
            # 一次 run 只压一次（§5.3 防抖动）：重复写入只会把水位往后推，而摘要
            # 还是那份摘要，下次组装会多丢一段原文。
            return
        try:
            await _conversations().set_summary(conversation_oid, text, seq)
        except Exception:  # noqa: BLE001 - 压缩是旁路，不能因落库失败打挂 run
            _logger.exception("ai conversation summary persist failed")
            return
        conversation["summary"] = text
        conversation["summary_upto_seq"] = seq
        state["compacted"] = True

    return on_summary


# executor 的 ``_round_payload`` 用短键（prompt/completion/total/cache_read/
# cache_write），旧形状用长键。列名只有一套，翻译只在这一个地方。
_USAGE_ALIASES: dict[str, tuple[str, ...]] = {
    "prompt_tokens": ("prompt_tokens", "prompt"),
    "completion_tokens": ("completion_tokens", "completion"),
    "total_tokens": ("total_tokens", "total"),
    "cache_read_tokens": ("cache_read_tokens", "cache_read"),
    "cache_write_tokens": ("cache_write_tokens", "cache_write"),
}


def _tokens_of(usage: object) -> dict[str, int | None]:
    """本轮 usage → 长键 tokens；缺的键是 ``None``（上游没报，**不是** 0）。"""
    tokens: dict[str, int | None] = {key: None for key in _USAGE_ALIASES}
    if not isinstance(usage, Mapping):
        return tokens
    for key, aliases in _USAGE_ALIASES.items():
        for alias in aliases:
            value = _optional_int(usage.get(alias))
            if value is not None:
                tokens[key] = value
                break
    return tokens


def _is_round_payload(value: Mapping[str, Any]) -> bool:
    """executor 的 payload 与「usage 字典」的区分点：它带本轮消息本体。"""
    return "assistant" in value or "tools" in value


def _normalise_round(args: Sequence[Any], kwargs: Mapping[str, Any]) -> dict:
    """``on_round(...)`` 实参 → ``{round_no, assistant, tools, tokens}``。"""
    payload = kwargs.get("payload")
    if not isinstance(payload, Mapping):
        for value in args:
            if isinstance(value, Mapping) and _is_round_payload(value):
                payload = value
                break
    if isinstance(payload, Mapping):
        return _round_payload_of(payload)
    return _legacy_round(args, kwargs)


def _round_payload_of(payload: Mapping[str, Any]) -> dict:
    """executor 的 ``_round_payload`` 形状：``assistant`` / ``tools`` / ``usage``。"""
    assistant = payload.get("assistant")
    raw_tools = payload.get("tools")
    return {
        "round_no": _int(payload.get("round_no")),
        "assistant": dict(assistant) if isinstance(assistant, Mapping) else {},
        "tools": [dict(item) for item in raw_tools if isinstance(item, Mapping)]
        if isinstance(raw_tools, (list, tuple))
        else [],
        "tokens": _tokens_of(payload.get("usage")),
    }


def _legacy_round(args: Sequence[Any], kwargs: Mapping[str, Any]) -> dict:
    """旧形状 ``(round_no, messages, usage)``：从 OpenAI 消息列表里还原。"""
    round_no = kwargs.get("round_no")
    messages = (
        kwargs.get("messages")
        or kwargs.get("tool_messages")
        or kwargs.get("round_messages")
    )
    usage = kwargs.get("usage") or kwargs.get("tokens")

    for value in args:
        if isinstance(value, bool):
            continue
        if isinstance(value, int) and round_no is None:
            round_no = value
        elif isinstance(value, Mapping) and "role" in value and messages is None:
            messages = [dict(value)]
        elif isinstance(value, Mapping) and usage is None:
            usage = value
        elif isinstance(value, (list, tuple)) and messages is None:
            messages = [item for item in value if isinstance(item, Mapping)]

    items = list(messages or [])
    assistant = _assistant_of(items)
    return {
        "round_no": _int(round_no),
        "assistant": assistant,
        "tools": _tools_of(items, assistant),
        "tokens": _tokens_of(usage),
    }


def _assistant_of(messages: Sequence[Mapping[str, Any]]) -> dict:
    """旧形状里的 assistant（可能只剩 content 与 tool_calls）。"""
    content = ""
    calls: list[dict] = []
    for item in messages:
        if str(item.get("role") or "") != "assistant":
            continue
        content = content or str(item.get("content") or "")
        raw = item.get("tool_calls")
        if isinstance(raw, list):
            calls.extend(call for call in raw if isinstance(call, Mapping))
    return {"content": content, "tool_calls": calls or None, "finish_reason": None}


def _tools_of(
    messages: Sequence[Mapping[str, Any]], assistant: Mapping[str, Any]
) -> list[dict]:
    """旧形状里的 tool 消息只有 ``tool_call_id``，工具名从 assistant 反查。"""
    names: dict[str, str] = {}
    for call in assistant.get("tool_calls") or []:
        if not isinstance(call, Mapping):
            continue
        call_id = str(call.get("id") or "")
        function = call.get("function")
        if call_id and isinstance(function, Mapping):
            names[call_id] = str(function.get("name") or "")
    tools: list[dict] = []
    for item in messages:
        if str(item.get("role") or "") != "tool":
            continue
        call_id = str(item.get("tool_call_id") or "")
        tools.append(
            {
                "tool_call_id": call_id,
                "tool_name": names.get(call_id),
                "content": item.get("content"),
            }
        )
    return tools


def _round_rows(
    run_id: str,
    round_no: int,
    *,
    assistant: Mapping[str, Any],
    tools: Sequence[Mapping[str, Any]],
    tokens: Mapping[str, int | None],
) -> list[dict]:
    """一轮的 payload → ``AI_MESSAGES`` 行：assistant 一行 + 全部 tool 各一行。"""
    raw_calls = assistant.get("tool_calls")
    calls = (
        [call for call in raw_calls if isinstance(call, Mapping)]
        if isinstance(raw_calls, (list, tuple))
        else []
    )
    content = str(assistant.get("content") or "")
    has_usage = any(value is not None for value in tokens.values())
    if not content and not calls and not tools and not has_usage:
        # 空轮（超时/超预算在工具前就截断，且没有任何产物）：不写一条空气泡；但
        # 只要有 usage 就必须落——少记一轮 token 会让会话累计比流水少一截。
        return []

    finish_reason = assistant.get("finish_reason")
    rows: list[dict] = [
        {
            "oid": uuid4().hex,
            "role": "assistant",
            "content": content,
            # 原文进库（§2.2）：回放时要原样交还上游，解析再序列化会改形状。
            "tool_calls": json.dumps(calls, ensure_ascii=False) if calls else None,
            # 有工具调用就记 ``tool_calls``：上游常把带工具的一轮也报成 ``stop``，
            # 照抄会让 UI 判不出"这轮要不要渲染工具块"。
            "finish_reason": "tool_calls"
            if calls
            else (str(finish_reason) if isinstance(finish_reason, str) and finish_reason else "stop"),
            "round_no": round_no,
            "request_id": run_id,
            "prompt_tokens": tokens.get("prompt_tokens"),
            "completion_tokens": tokens.get("completion_tokens"),
            "total_tokens": tokens.get("total_tokens"),
            "cache_read_tokens": tokens.get("cache_read_tokens"),
            "cache_write_tokens": tokens.get("cache_write_tokens"),
        }
    ]
    for item in tools:
        rows.append(
            {
                "oid": uuid4().hex,
                "role": "tool",
                # tool 行的身份三件套直接来自 payload：executor 侧已经有工具名，
                # 再反查一次 tool_calls 只会引入"名字对不上"的可能。
                "content": _clip(item.get("content"), MAX_TOOL_CONTENT_CHARS) or "",
                "tool_call_id": str(item.get("tool_call_id") or "") or None,
                "tool_name": str(item.get("tool_name") or "") or None,
                "round_no": round_no,
                "request_id": run_id,
            }
        )
    return rows


def _usage_block(tokens: Mapping[str, int | None]) -> dict | None:
    """本轮 tokens → 会话累加口径。``context_tokens`` 是**覆盖**语义（最近一次请求
    的 prompt token），所以只带 prompt，不带累计值。"""
    prompt = tokens.get("prompt_tokens")
    completion = tokens.get("completion_tokens")
    total = tokens.get("total_tokens")
    if prompt is None and completion is None and total is None:
        return None
    if total is None:
        total = (prompt or 0) + (completion or 0)
    return {
        "prompt_tokens": prompt or 0,
        "completion_tokens": completion or 0,
        "total_tokens": total,
        "context_tokens": prompt,
    }


def _accumulate(totals: dict, tokens: Mapping[str, int | None]) -> None:
    """run 级累计（写进 ``ai_runs`` 的那三个数）。

    仓储的 ``finish`` 是唯一的终态写入点，所以累计留在内存里、收尾时一次写；
    ``None`` 表示上游没报，不能被累加成 0。
    """
    if not isinstance(tokens, Mapping):
        return
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        value = _optional_int(tokens.get(key))
        if value is None:
            continue
        totals[key] = int(totals.get(key) or 0) + value


def _optional_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


# ---------------------------------------------------------------------------
# 读侧：run 详情（§3.2）
# ---------------------------------------------------------------------------

async def list_active(owner_oid: str) -> list[dict]:
    """本人的 ``queued|running`` 原始行（重连横幅；视图由调用方组装）。"""
    return await _runs().list_active(str(owner_oid))


async def run_detail(
    *, user: Mapping[str, Any], run_id: str, after_round: int = 0
) -> dict:
    """``GET /runs/{id}``：状态 + 增量事件 + 本次 run 的提案。

    ``after_round`` 只回 ``round_no > after_round`` 的事件：轮询载荷随轮次线性
    增长，而不是平方（每次把整条时间线重发一遍）。
    """
    owner_oid = str(user.get("id") or "")
    row = await _runs().get(str(run_id), owner_oid)
    if row is None:
        raise AgentError(RUN_NOT_FOUND, "run not found", 404)

    floor = max(0, int(after_round or 0))
    events = await proposal_service.run_events(request_id=str(run_id), user=dict(user))
    seen: set[str] = set()
    incremental: list[dict] = []
    for event in events:
        if _int(event.get("round_no")) <= floor:
            continue
        oid = str(event.get("oid") or "")
        if oid and oid in seen:
            # 同轮重发：按 oid 去重，客户端拿到的序列与轮次一一对应。
            continue
        if oid:
            seen.add(oid)
        incremental.append(event)

    return {
        "run": run_view(row),
        "events": incremental,
        "proposals": await _run_proposals(str(run_id), owner_oid),
    }


async def _run_proposals(run_id: str, owner_oid: str) -> list[dict]:
    """一次 run 的写工具提案，复用事件仓储的 ``request_id`` 查询。

    视图形状直接借 ``services.agent.proposal._list_item``：提案卡片的字段（preview
    的解析、双人码标记、过期时间）只该有一份实现，复制一遍就会和确认路径漂移。
    """
    rows = await _events().list_for_request(str(run_id))
    proposals: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        if str(row.get("tool_kind") or "") != WRITE:
            continue
        if str(row.get("actor_oid") or "") != str(owner_oid):
            continue
        oid = str(row.get("_id") or row.get("oid") or "")
        if oid and oid in seen:
            # 同一行被重复取出时不该出现两张一样的提案卡（确认接口按 oid 走，
            # 重复的卡片只会让人以为有两笔待办）。
            continue
        if oid:
            seen.add(oid)
        proposals.append(proposal_service._list_item(row))  # noqa: SLF001 - 同一份提案视图
    return proposals


# ---------------------------------------------------------------------------
# 历史回放（§5.1 的骨架：摘要 + 水位之后的消息）
# ---------------------------------------------------------------------------

async def _executor_messages(
    conversation: Mapping[str, Any],
) -> tuple[list[dict], str | None]:
    """会话历史 → ``(messages, summary)``。

    两件东西必须**分开**交给 executor：

    * ``messages`` 只放回放行（user / assistant / tool，各带 ``seq``）。摘要绝不能
      变成这里的一条 ``role="system"``：executor 只收 user/assistant/tool，
      客户端塞 system 一律 400；
    * ``summary`` 单独传。executor 会把它按 §5.1 拼成**第二条 system**（放尾部会
      让缓存前缀退化成全站同 key）。

    ``seq`` 必须保留：压缩的切点（``summary_upto_seq``）只能落在已落库的行上，
    丢了行号就算不出"压到第几条"。送上游前 ``executor._wire`` 会剥掉它。
    """
    conversation_oid = str(conversation.get("_id") or "")
    history = await _history_rows(
        conversation_oid, _int(conversation.get("summary_upto_seq"))
    )
    messages = _replay(history)
    if not messages:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENTS", "conversation has no replayable messages", 400
        )
    summary = conversation.get("summary")
    text = summary.strip() if isinstance(summary, str) else ""
    return messages, (text or None)


async def _history_rows(conversation_oid: str, after_seq: int) -> list[dict]:
    """按 ``seq`` 升序翻页取历史，受 ``DEFAULT_HISTORY_MESSAGES`` 限制。"""
    rows: list[dict] = []
    cursor = max(0, int(after_seq))
    while len(rows) < DEFAULT_HISTORY_MESSAGES:
        page = await _messages().list_after(
            conversation_oid, cursor, limit=HISTORY_PAGE
        )
        if not page:
            break
        rows.extend(page)
        cursor = int(page[-1].get("seq") or cursor)
        if len(page) < HISTORY_PAGE:
            break
    return rows[:DEFAULT_HISTORY_MESSAGES]


def _replay(rows: Sequence[Mapping[str, Any]]) -> list[dict]:
    """库里的行 → 回放形状（user / assistant / tool，每条都带 ``seq``）。

    防御（§2.2 硬不变量 2）：``assistant.tool_calls`` 存在但缺任一对应 ``tool``
    结果时，把这条 assistant 和它的残缺 tool 一起丢掉。上游对「tool_calls 没有
    配对结果」直接 400，而这类残行只可能来自历史里的一次异常中断。
    """
    out: list[dict] = []
    index = 0
    total = len(rows)
    while index < total:
        row = rows[index]
        role = str(row.get("role") or "")
        if role == "assistant":
            calls = _calls_of(row.get("tool_calls"))
            following: list[Mapping[str, Any]] = []
            if calls:
                cursor = index + 1
                while cursor < total and str(rows[cursor].get("role") or "") == "tool":
                    following.append(rows[cursor])
                    cursor += 1
                wanted = {str(call.get("id") or "") for call in calls}
                got = {str(item.get("tool_call_id") or "") for item in following}
                if not wanted <= got:
                    index = cursor  # 丢掉残缺的一条（连同它的 tool）
                    continue
            message: dict = {"role": "assistant", "content": str(row.get("content") or "")}
            if calls:
                # 工具调用必须原样带回：下一轮的 tool 消息要对得上它，否则 400。
                message["tool_calls"] = calls
            _with_seq(message, row)
            out.append(message)
            for item in following:
                tool = {
                    "role": "tool",
                    "tool_call_id": str(item.get("tool_call_id") or ""),
                    "content": str(item.get("content") or ""),
                }
                name = item.get("tool_name")
                if isinstance(name, str) and name:
                    # OpenAI 的 tool 消息允许带 name（部分兼容实现按它做日志/校验）。
                    tool["name"] = name
                _with_seq(tool, item)
                out.append(tool)
            index = index + 1 + len(following)
            continue
        if role == "tool":
            # 孤立 tool（没有对应 assistant）：上游 400，直接跳过。
            index += 1
            continue
        message = {"role": "user", "content": str(row.get("content") or "")}
        _with_seq(message, row)
        out.append(message)
        index += 1
    return out


def _with_seq(message: dict, row: Mapping[str, Any]) -> None:
    """把库里的 ``seq`` 挂到回放行上（压缩切点要用；``_wire`` 送上游前剥掉）。"""
    seq = _optional_int(row.get("seq"))
    if seq:
        message["seq"] = seq


def _calls_of(value: object) -> list[dict]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, list):
        return []
    return [call for call in value if isinstance(call, Mapping)]


__all__ = [
    "DEFAULT_CONCURRENCY",
    "DEFAULT_HEARTBEAT_SECONDS",
    "DEFAULT_MAX_RUNS_PER_USER",
    "DEFAULT_STALE_SECONDS",
    "cancel",
    "concurrency",
    "heartbeat_seconds",
    "kick",
    "list_active",
    "max_runs_per_user",
    "recover_on_startup",
    "reset",
    "run_detail",
    "run_view",
    "stale_seconds",
    "submit",
    "watchdog_tick",
]
