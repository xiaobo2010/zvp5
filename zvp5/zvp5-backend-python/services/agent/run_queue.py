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
from db.repos.oracle.users import UserGoneError
from db.repos.protocols import ActiveRunConflict
from services import violation_guard
from services.agent import executor, live, proposal as proposal_service, run_cache
from services.agent.tools import WRITE
from services.agent.tool_impl import AgentError
from services.ai import (
    ban_state,
    conversations,
    prompt_audit,
    safety_guard,
)
from typings.log import ZVMSLog
from util import agent_unlock
from util.ban import ban_is_active
from util.serialization import iso_str

_logger = logging.getLogger(__name__)

# 默认值全部写在这里：部署差异走 ``settings``，实现差异才改代码。
DEFAULT_CONCURRENCY = 2
DEFAULT_MAX_RUNS_PER_USER = 1
DEFAULT_HEARTBEAT_SECONDS = 30
DEFAULT_IDLE_SECONDS = 1.0
# 空闲退避倍率（相对于 ``DEFAULT_IDLE_SECONDS``）：1s → 5s → 30s。
# 队列常年是空的，空闲时每秒一次 ``claim_next``（一次 SELECT + 一次 CAS UPDATE）
# 纯属白烧连接与 ADB 往返；但「有活」时必须立刻回到 1s，否则提交后要等半分钟
# 才开跑——所以 kick / 线程回收唤醒会把退避清零。
IDLE_BACKOFF = (1.0, 5.0, 30.0)
DEFAULT_STALE_SECONDS = 360
STALE_MARGIN_SECONDS = 60
# 单次 run 从库里回放的历史消息上限：超长会话该由压缩截断（§5），这里只是兜住
# 「一次把上万条 CLOB 拉进内存」的病态情形。
DEFAULT_HISTORY_MESSAGES = 400
HISTORY_PAGE = 100

# 个人首页的「最近 run」：默认 5 条，最多 10 条。上限写死在这里而不是交给查询串：
# 首页是一条横幅，没有翻页需求，放开只会让同一份数据被无限次拉取。
RECENT_LIMIT_DEFAULT = 5
RECENT_LIMIT_MAX = 10

# 工具结果落库长度与模型看到的同长（executor 用 head+tail 截断，错误结果放宽到
# 16000；这里用同一个上限再做一次 head+tail，避免普通 ``_clip`` 把尾巴/标记切掉）。
MAX_TOOL_CONTENT_CHARS = executor.MAX_TOOL_ERROR_CHARS
# ``ai_runs.error_message`` 是 VARCHAR2(500)，超了是 ORA-12899。
MAX_ERROR_MESSAGE_CHARS = 500

TERMINAL_STOP_REASONS = frozenset(
    {"completed", "max_iterations", "token_budget", "timeout", "cancelled", "error"}
)
ACTIVE_STATUSES = ("queued", "running")
#: 终态：run 详情可以缓存久一点——结果不会再变，前端回看历史不必每次回源。
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "interrupted"})
RUN_NOT_FOUND = "AI_RUN_NOT_FOUND"
RUN_NOT_CANCELLABLE = "AI_RUN_NOT_CANCELLABLE"
RUN_CONFLICT = conversations.RUN_CONFLICT
INTERRUPTED_CODE = "AI_RUN_INTERRUPTED"
INTERRUPTED_MESSAGE = "执行进程失去心跳"
# 执行期封禁与 ``util.ban`` 的 detail 同字面量（前端按它分流到「账号封禁」页）；
# 消息进 ``AI_RUNS.error_message``（VARCHAR2(500)），所以只写一句人话。
USER_BANNED_CODE = "USER_BANNED"
USER_BANNED_MESSAGE = "该账号当前处于封禁状态，本次任务已中止。"

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
        "created_at": iso_str(row.get("created_at")),
        "started_at": iso_str(row.get("started_at")),
        "finished_at": iso_str(row.get("finished_at")),
    }


# ---------------------------------------------------------------------------
# 入队（§2.3 提交事务的调用侧）
# ---------------------------------------------------------------------------

def is_admin(user: Mapping[str, Any] | dict) -> bool:
    """管理员天然旁路内容审核（不再有「无限制模式」开关）。"""
    per = (user or {}).get("per")
    if not isinstance(per, (list, tuple, set, frozenset)):
        return False
    return "admin" in per


async def _reject_unsafe(
    user: Mapping[str, Any],
    *,
    text: str,
    xuehai_id: str,
    category: str,
    unavailable: bool = False,
    audit_oid: str | None = None,
) -> None:
    """内容审核拒绝：判定成立时先记违规+警告+首档封号，再抛 422。

    对外只有一个错误码与一句固定文案：不区分警告/封禁，也不解释处罚梯度。
    ``unavailable`` 是「判定没跑起来」——只拒绝本次回答，不写任何证据。

    ``audit_oid`` 是留存行（``AI_PROMPT_AUDIT``）的句柄：被拒的问题同样要标
    ``REFUSED=1``，发了警告再标 ``WARNED=1``——否则「他被拒过几次」只能靠猜
    错误码去对时间。
    """
    warned = False
    if not unavailable:
        try:
            outcome = await violation_guard.record_agent_jailbreak(
                user,
                xuehai_id=xuehai_id,
                rule=str(category or ""),
                detail=text,
            )
            warned = bool((outcome or {}).get("warned"))
            if (outcome or {}).get("banned"):
                # 刚落的封禁必须立刻对执行期可见（should_stop 的缓存不能等 TTL）。
                ban_state.invalidate(str(user.get("id") or ""))
        except Exception:  # noqa: BLE001 - 证据入库失败不该把拒绝变成 500
            _logger.exception("AI 安全认证违规入库失败（本次仍然拒绝）")
    await prompt_audit.record_outcome(
        audit_oid, refused=True, warned=True if warned else None
    )
    raise AgentError(
        safety_guard.REJECTED_CODE, safety_guard.REJECTED_MESSAGE, 422
    )


async def submit(
    *,
    user: Mapping[str, Any],
    conversation_oid: str,
    content: str,
    model: str | None = None,
    client_msg_id: str | None = None,
    xuehai_id: str = "",
    conversation: Mapping[str, Any] | None = None,
    reference: str | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> dict:
    """把一条用户消息 + 一个 ``queued`` run 原子落库。

    调用方负责紧跟着 ``await kick()``——这里刻意不 kick：提交是纯数据操作，起泵
    是编排决定（也让「只入队不执行」的测试不必收拾一个跑着的协程）。

    幂等：带 ``client_msg_id`` 时**不做**活跃 run 预检，直接交给仓储。预检会把
    「客户端重试同一个键」也判成冲突，而 ``insert_queued`` 能区分这两种冲突
    （撞幂等键 → 返回已有 run；撞活跃唯一索引 → :class:`ActiveRunConflict`）。

    ``model`` 只是**记录**（写进 ``AI_RUNS.model`` 备查）：实际执行用的模型一律
    取 ``conversation.model``（R2）。``conversation`` 是调用方刚读过的那一行——
    传进来就省掉一次同为「会话 + 归属」的查询，不传则自己查（语义完全一样）。

    ``reference`` 是只读场景的参考内容（已脱敏）：**不落消息表**，只在进程内随
    run_id 交给执行器（单 worker，与队列本身同一个前提），并由执行器随 prompt
    留存一起留档。

    ``overrides`` 是只读场景的**按次覆盖**（``model`` / ``thinking``）：同样只在
    进程内随 run_id 传递，执行器用它覆盖会话行上的固定值。登记放在插入之前——
    行一旦可见，泵随时可能领走它，晚一步就会先按会话值跑一轮。
    """
    owner_oid = str(user.get("id") or "")
    if conversation is None:
        conversation = await conversations.require_row(
            user=user, oid=str(conversation_oid)
        )
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

    # 密码门在审核之前：没有解锁的人连「被审核的机会」都不该有——否则审核命中
    # 会把一次未授权的探测写进违规证据里，处罚依据就不干净了。
    # **只读场景豁免**（oracle 条件）：只读场景（终端 / 文件编辑 / 义工分析 /
    # 活动分析）只声明读工具，风险面与「能改数据」的普通 agent 不同；再要一次
    # 密码只是让「看一眼日志」多一步，挡不住任何真实风险。
    #
    # 豁免判据用 :func:`services.ai.conversations.scene_allows` 而不是写死
    # ``is_admin``：场景的准入角色是 ``SCENE_MIN_ROLES`` 一个地方的事，密码门跟着
    # 同一张表走，「谁进得了场景」与「谁免密码」才不会漂移——写死 admin 时
    # ``activity`` 对 ``department`` 放开，部门提交照样 422，路由层放行变成空话。
    # 判据仍然是「**只读场景** 且 **该场景允许的角色**」：非只读场景一律要解锁，
    # 未知场景 ``scene_allows`` fail-closed，拼错不会变成跳过门禁。
    # 条件写死在这里，不开放成参数——那等于给调用方一个「跳过门禁」的开关。
    scene = conversation.get("scene")
    if not (
        conversations.is_readonly_scene(scene)
        and conversations.scene_allows(user, scene)
    ):
        if not await agent_unlock.is_unlocked(owner_oid):
            raise AgentError(
                "AI_AGENT_UNLOCK_REQUIRED",
                "agent access requires an unlock with your account password",
                422,
            )

    # 管理员天然旁路审核（不再是「无限制模式」开关）：审核分支根本不进入，也就
    # 没有「跳过了审核」这条记录——正常的管理员 run 只留 executor 每轮的审计行
    # （``roles_snapshot`` 带 admin，事后仍能区分是谁提的）。
    #
    # 内容安全**只有一道判据**：:func:`services.ai.safety_guard.screen`——用当前
    # 会话的模型在入队前判一次「是否属于平台事务」，无关即拒绝并走首档封号。规则
    # 词表不再参与门禁（词表本体保留在 :mod:`services.ai.moderation` 备查）。
    if not is_admin(user):
        verdict = await safety_guard.screen(
            user,
            text,
            model_hint=(str(model).strip() or None) if model else conversation.get("model"),
        )
        if not verdict.allowed:
            # 被拒原文不留就永远查不到「他到底说了什么」；留存仍受
            # ``ai.prompt_capture_roles`` 白名单约束（capture 内部判断）。
            audit_oid = await prompt_audit.capture(
                user=dict(user),
                purpose=executor.PURPOSE_AGENT,
                messages=[{"role": "user", "content": text}],
            )
            await _reject_unsafe(
                user,
                text=text,
                xuehai_id=xuehai_id,
                category=verdict.category,
                unavailable=verdict.unavailable,
                audit_oid=audit_oid,
            )
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
    # 覆盖先登记、后插入：行可见之后泵随时可能 `claim_next` 到它。
    _remember_override(
        run_id,
        model=(overrides or {}).get("model"),
        thinking=(overrides or {}).get("thinking"),
    )
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
        # 没入队就没有 run 能消费这份覆盖：留着会污染同一会话的下一次 run。
        _OVERRIDES.pop(run_id, None)
        raise AgentError(
            RUN_CONFLICT, "an active run already exists", 409
        ) from exc
    except UserGoneError as exc:
        # 会话在 require_row 之后、插入之前被删：仓储拒绝写入（不落孤儿消息/run）。
        # 对调用方与「一开始就读不到」是同一件事，回同一个 404。
        _OVERRIDES.pop(run_id, None)
        raise AgentError(
            conversations.NOT_FOUND, "conversation not found", 404
        ) from exc
    except BaseException:
        _OVERRIDES.pop(run_id, None)
        raise

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
        # 命中的是**已有**的 run：覆盖登记在本次新生成的 run_id 上，没人会消费它。
        _OVERRIDES.pop(run_id, None)
    else:
        _remember_reference(payload["run_id"], reference)
    return payload


# ---------------------------------------------------------------------------
# 只读场景（终端 / 文件编辑）
#
# 参考内容（用户在终端里选中的输出 / 正在看的文件片段）**不落 AI_MESSAGES**：
# 它不是用户说的话，写进消息表会让历史回放把它当对话。它随 run_id 在进程内交给
# 执行器，再由执行器随 ``AI_PROMPT_AUDIT`` 一起留档。
#
# 进程内传递的前提与队列本身相同：单 worker（见模块 docstring）。真丢了（进程
# 重启后 queued 的 run 继续跑）也只是「这一轮没有参考内容」，不会丢消息、不会
# 影响计费——所以不值得为它加一列 CLOB。
# ---------------------------------------------------------------------------
_REFERENCES: dict[str, str] = {}
REFERENCES_MAX = 64


def _remember_reference(run_id: str, reference: str | None) -> None:
    text = reference.strip() if isinstance(reference, str) else ""
    if not text:
        return
    while len(_REFERENCES) >= REFERENCES_MAX:
        _REFERENCES.pop(next(iter(_REFERENCES)))
    _REFERENCES[str(run_id)] = text


def _take_reference(run_id: str) -> str | None:
    return _REFERENCES.pop(str(run_id), None)


#: 按次覆盖的模型 / 思考强度（只读场景）：同样随 run_id 在进程内传递。
#:
#: 为什么不写进 ``AI_RUNS``：该表只有 ``MODEL`` 一列，``THINKING`` 没有列，而 DDL
#: 本期不动。与参考内容同一前提（单 worker），进程重启后 queued 的 run 续跑时
#: 覆盖丢失 → 退回会话现有值（不会丢消息、不会改变计费口径）。
_OVERRIDES: dict[str, dict[str, str]] = {}
OVERRIDES_MAX = 64


def _remember_override(
    run_id: str, model: str | None = None, thinking: str | None = None
) -> None:
    values = {
        key: value
        for key, value in (("model", model), ("thinking", thinking))
        if isinstance(value, str) and value.strip()
    }
    if not values:
        return
    while len(_OVERRIDES) >= OVERRIDES_MAX:
        _OVERRIDES.pop(next(iter(_OVERRIDES)))
    _OVERRIDES[str(run_id)] = values


def _take_override(run_id: str) -> dict[str, str]:
    return _OVERRIDES.pop(str(run_id), {})


async def submit_scene(
    *,
    user: Mapping[str, Any],
    scene: str,
    content: str,
    context: str | None = None,
    xuehai_id: str = "",
    model: str | None = None,
    thinking: str | None = None,
) -> dict:
    """只读场景的一次提问：复用该用户的场景会话，入队并返回 ``{run_id, conversation_oid}``。

    会话复用、参考内容脱敏都在这一层决定：路由层只做协议翻译，不自己拼 prompt。
    ``context`` 先脱敏再交给 ``submit``（脱敏必须在入库/入 prompt 之前，见
    :func:`services.ai.conversations.scene_reference`）。

    ``model`` / ``thinking`` 是**按次覆盖**：只作用于这一次 run，不改写场景会话行上的
    固定值（不传 = 与今天完全一致，沿用会话值）。覆盖在入队前登记，所以在泵领到
    这一行之前就已经就位——不存在「先跑一轮默认档再用覆盖档」的中间态。
    """
    conversation = await conversations.ensure_scene(user=user, scene=scene)
    reference = conversations.scene_reference(context)
    conversation_oid = str(conversation.get("_id") or "")
    data = await submit(
        user=user,
        conversation_oid=conversation_oid,
        content=content,
        xuehai_id=xuehai_id,
        conversation=conversation,
        reference=reference,
        # ``AI_RUNS.model`` 记「这一次请求指定的模型」（可空）：实际执行用它，审计里
        # 落的是解析后的合格 ID（``services.agent.executor._audit_round``）。
        model=model,
        overrides={"model": model, "thinking": thinking},
    )
    return {"run_id": data["run_id"], "conversation_oid": conversation_oid}


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

    # 状态刚变过：下一次轮询必须回源，否则前端会再看到 1 秒「仍在跑」。
    run_cache.invalidate(str(run_id))
    live.publish_resync(str(run_id))
    latest = await _runs().get(str(run_id), owner_oid) or row
    return {"status": str(latest.get("status"))}


async def compact_conversation(*, user: Mapping[str, Any], conversation_oid: str) -> dict:
    """手动压缩一次会话（``POST /conversations/{oid}/compact`` 的服务层）。

    顺序固定，每一步都省不得：

    1. **归属**：``require_row`` 只认 ``owner_oid``，别人的 oid 一律 404（不做探针）；
    2. **活跃 run**：同会话还有 ``queued|running`` 就 409 ``AI_RUN_CONFLICT``。并发
       压缩会与 worker 的 ``set_summary`` / ``context_tokens`` 打架，而 worker 手里的
       上下文是压缩前那一份——两边都赢不了；
    3. **回放历史**：与 run 启动同一条路径（摘要水位之后、带 ``seq``）。空会话直接
       回 ``nothing_to_compact``，不当错误；
    4. **压缩**：``executor.compact_messages(force=True)``——绕过阈值，与自动路径共用
       同一份实现；
    5. **落库**：摘要 + ``summary_upto_seq``，然后 ``set_context_tokens`` 把水位拉下来
       （前端占用条据此回落），降级时补 ``compaction_degraded_at``。

    额度不足时 :func:`services.agent.executor.compact_messages` 抛
    ``insufficient_quota``（429），由路由层的 ``_dispatch`` 翻成既有错误体——手动
    压缩不能像自动路径那样悄悄硬截断用户的上下文。
    """
    owner_oid = str(user.get("id") or "")
    oid = str(conversation_oid)
    conversation = await conversations.require_row(user=dict(user), oid=oid)

    for row in await _runs().list_active(owner_oid):
        if str(row.get("conversation_oid")) == oid:
            raise AgentError(
                RUN_CONFLICT, "this conversation already has an active run", 409
            )

    history = await _history_rows(oid, _int(conversation.get("summary_upto_seq")))
    messages = _replay(history)
    if not messages:
        return {
            "compacted": False,
            "upto_seq": None,
            "tokens_before": 0,
            "tokens_after": 0,
            "degraded": False,
            "reason": "nothing_to_compact",
        }

    summary = conversation.get("summary")
    text = summary.strip() if isinstance(summary, str) else ""
    rebuilt, result = await executor.compact_messages(
        user=dict(user),
        messages=messages,
        summary=text or None,
        model=conversation.get("model"),
        request_id=uuid4().hex,
        prompt_tokens=_optional_int(conversation.get("context_tokens")),
        scene=conversations.scene_of(conversation),
        force=True,
    )
    del rebuilt  # 只用来算 tokens_after；会话原文不动，回放靠 summary 水位跳过

    if result.get("compacted"):
        upto = _optional_int(result.get("upto_seq"))
        if result.get("summary") and upto:
            await _conversations().set_summary(
                oid, str(result["summary"]), int(upto)
            )
        after = _optional_int(result.get("tokens_after"))
        if after is not None:
            await _conversations().set_context_tokens(oid, after)
        if result.get("degraded"):
            await _conversations().mark_compaction_degraded(oid)

    return {
        key: result.get(key)
        for key in (
            "compacted",
            "upto_seq",
            "tokens_before",
            "tokens_after",
            "degraded",
            "reason",
        )
    }


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
    idle = 0  # 连续空转次数：只影响「下一次空转等多久」，不影响领取顺序
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
            if not await _idle(wake, idle):
                idle += 1
            else:
                idle = 0  # 被 kick 叫醒 = 有新活，退避立刻回到最短
            continue
        idle = 0
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


def _idle_timeout(attempt: int) -> float:
    """第 ``attempt`` 次空等多久：``DEFAULT_IDLE_SECONDS`` × 1 / 5 / 30。

    以 ``DEFAULT_IDLE_SECONDS`` 为基准而不是写死 1/5/30 秒：测试把它调到 0.01
    时整套退避同比缩小，用例不必真的等 30 秒。
    """
    factor = IDLE_BACKOFF[min(max(attempt, 0), len(IDLE_BACKOFF) - 1)]
    return DEFAULT_IDLE_SECONDS * factor


async def _idle(wake: asyncio.Event, attempt: int = 0) -> bool:
    """空闲等待；返回 ``True`` 表示被 kick 叫醒（有新活），``False`` 表示超时。"""
    try:
        await asyncio.wait_for(wake.wait(), timeout=_idle_timeout(attempt))
    except (asyncio.TimeoutError, TimeoutError):
        return False
    wake.clear()
    return True


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
    # run 级进程内状态一并清掉：测试里上一个用例的覆盖不能带进下一个用例。
    _OVERRIDES.clear()
    # 轮询快照同理：上一条用例缓存的 run 行不该出现在下一条里。
    run_cache.reset()
    # 封禁态缓存同属进程内状态：上一条用例造出来的「已封」不能跟着 TTL 漏进来。
    ban_state.invalidate()
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
    # 参考内容随 run 一次性取走：即使这次执行在读到会话前就失败，也不会在进程里
    # 留一份没人认领的终端/文件片段。
    reference = _take_reference(run_id)
    # 按次覆盖同样一次性取走：它只属于这一次 run，取不到就退回会话行上的值。
    overrides = _take_override(run_id)
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
        # 提交到执行之间可能已经落封（提交时审核命中、管理员手工封、并发的另一
        # 条请求触发梯度）。已封就不发起上游调用：一次 run 能跑几十轮工具，让
        # 它跑完等于给被封账号继续执行写操作的机会。
        if user.get("banned"):
            await _log_banned_run(run_id, owner_oid)
            await _finish(
                run_id,
                status="failed",
                stop_reason="error",
                error_code=USER_BANNED_CODE,
                error_message=USER_BANNED_MESSAGE,
            )
            return
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
        scene = conversations.scene_of(conversation)

        def _on_delta(round_no: int, text: str) -> None:
            if not text or not live.has_subscribers(run_id):
                return
            live.publish(
                run_id,
                {
                    "type": "assistant",
                    "round_no": round_no,
                    # 客户端用 ``run_id:round_no`` 归并同一个回答的累计文本。
                    "stream_key": f"{run_id}:{round_no}",
                    "content": text,
                },
            )

        def _on_tool(frame: dict) -> None:
            if not live.has_subscribers(run_id):
                return
            live.publish(run_id, frame)

        result = await executor.run(
            user=user,
            messages=messages,
            # 模型与思考强度默认取**会话**（R2/R3）：``AI_RUNS.model`` 只是提交时的
            # 记录，不是执行依据。只读场景可以**按次覆盖**（终端/文件编辑卡片上的
            # 模型与档位选择），覆盖只活这一次 run，不改写会话行。
            model=overrides.get("model") or conversation.get("model"),
            thinking=overrides.get("thinking") or conversation.get("thinking"),
            request_id=run_id,
            # 摘要**不走 messages**（executor 只允许 user/assistant/tool，客户端
            # 塞 system 一律 400）：它由 executor 按 §5.1 拼成第二条 system。
            summary=summary,
            on_round=_on_round(run_id, conversation_oid, owner_oid, state),
            should_stop=_should_stop(run_id, owner_oid, state),
            on_summary=_on_summary(conversation, conversation_oid, state),
            on_compaction=_on_compaction(conversation_oid, state),
            # 上一个 run 的实测 prompt 规模：executor 用它做「请求前预检」，避免新
            # run 先把全量历史发上去再压缩。
            context_tokens=_optional_int(conversation.get("context_tokens")),
            # 场景决定工具声明的裁剪与 system 追加句；参考内容只在本次 run 内有效。
            scene=scene,
            reference=reference,
            on_delta=_on_delta,
            on_tool=_on_tool,
        )
        status, stop_reason = _outcome(result, state)
        # 执行期被封：终态与错误码以封禁为准（``_outcome`` 只会说「cancelled」，
        # 而「用户自己点的取消」与「账号被封」对运维与前端是两件事）。
        banned = bool(state.get("banned"))
        await _finish(
            run_id,
            status="failed" if banned else status,
            stop_reason="error" if banned else stop_reason,
            rounds=state["rounds"] or _int((result or {}).get("rounds")),
            result_message_oid=state["result_message_oid"],
            usage=state["usage"],
            error_code=(
                USER_BANNED_CODE
                if banned
                else INTERRUPTED_CODE if status == "interrupted" else None
            ),
            error_message=(
                USER_BANNED_MESSAGE
                if banned
                else "执行已被中止" if status == "interrupted" else None
            ),
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

    ``banned`` 一并留下来（提交与执行之间可能已经落封）；判定走
    ``ban_is_active``——``banned`` 列到期不会自动归零，只看列会把刑满用户也拦下。
    """
    row = await _users().get_for_auth(owner_oid)
    roles = list(row.get("roles") or []) if isinstance(row, Mapping) else []
    banned = ban_is_active(row) if isinstance(row, Mapping) else False
    return {"id": owner_oid, "per": roles, "banned": banned}


async def _log_banned_run(run_id: str, owner_oid: str) -> None:
    """已封账号被拒执行：留一行审计（与违规证据同一张日志表）。

    写日志失败不影响终态：run 已经写了 ``failed`` + ``USER_BANNED``，那才是事实。
    """
    try:
        await ZVMSLog(
            "",
            owner_oid,
            f"AI run 因账号封禁被拒执行（run {run_id}）",
            datetime.now().timestamp(),
            type="ai_run_banned",
            level="warning",
        ).insert_log()
    except Exception:  # noqa: BLE001 - 审计是旁路
        _logger.exception("写入 ai_run_banned 日志失败（run 已按失败收尾）")


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
    else:
        # 终态刚落：快照里还是 running，留着会让前端继续画「执行中」。
        run_cache.invalidate(str(run_id))
        # SSE 订阅者需要一次最终快照 + terminal 收尾；没有订阅者时这是 no-op。
        live.publish_resync(str(run_id))
        live.cleanup(str(run_id), drop_seq=True)
    return bool(written)


async def _still_running(run_id: str, owner_oid: str) -> Mapping[str, Any] | None:
    """写任何东西之前的最后一道校验：run 还必须是 ``running``。

    与 ``finish`` 的 CAS 配对使用（评审 P0-2）：watchdog 把行判成 ``interrupted``
    之后，这一轮的执行结果必须被丢弃，而不是写在终态行上面。

    返回整行而不是 bool：调用方（``_on_round``）紧接着还要判取消标志，把这一行
    存进 ``state['run_check']`` 就能省掉下一轮边界的同一次读取。
    """
    try:
        row = await _runs().get(run_id, owner_oid)
    except Exception:  # noqa: BLE001 - 读不到就当作还能写：一次查询抖动不该丢一轮
        _logger.warning("ai run %s status check failed", run_id)
        return {"status": "running"}  # 保守：当作还能写
    if row is None or str(row.get("status")) != "running":
        return None
    return row


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
    """executor 的闸：轮边界 + 每次工具执行前调用（**必须 await**）。

    查库**每轮一次**：同一轮里的第 2..N 个工具复用这一行，轮收尾（``on_round``）
    把缓存清掉，于是下一轮的第一次检查必然重新读库——取消/中断仍然「每轮可见」，
    只是不再「每个工具一次」（单轮 5 个工具 = 从 5 次查询降到 1 次）。

    封禁**不共用这份缓存**：它是别处写入的事实（违规梯度/管理员），所以在每次
    调用时都问一遍 ``ban_state``（自带 1 秒 TTL，频率有上界）。命中就把 run 标成
    ``banned``，终态按 ``USER_BANNED`` 收尾——「被封之后还在继续跑轮次」正是要修
    的那个缺陷。
    """

    async def should_stop() -> bool:
        if state.get("cancelled") or state.get("interrupted"):
            return True
        if await ban_state.is_banned(owner_oid):
            state["banned"] = True
            return True
        row = state.get("run_check")
        if row is None:
            try:
                row = await _runs().get(run_id, owner_oid)
            except Exception:  # noqa: BLE001 - 读不到就继续：多跑一轮好过丢掉整次执行
                _logger.warning("ai run %s cancel check failed", run_id)
                return False
            # ``None`` 也要缓存（存成空 dict）：否则「行不见了」会变成每个工具
            # 都去查一次，而结论永远是同一个。
            row = row or {}
            state["run_check"] = row
        if not row or str(row.get("status")) != "running":
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
        # 一轮结束 = 下一轮该重新读一次 run 状态（取消检查的可见性边界）。
        state.pop("run_check", None)
        # 用户取消（cancel_requested）不是「终态行」：此刻行仍是 running，已执行
        # 的工具与事件必须照常落库。真正的终态（watchdog 判 interrupted）由下面
        # 的 ``_still_running`` 挡住——那条路径不允许往终态行上补消息。
        running = await _still_running(run_id, owner_oid)
        if running is None:
            state["interrupted"] = True
            return

        rows = _round_rows(
            run_id,
            round_no,
            assistant=payload["assistant"],
            tools=payload["tools"],
            tokens=payload["tokens"],
        )
        events = payload["events"]
        if not rows and not events:
            return
        # 一个 ``append_batch`` = 一个事务：锁会话行、连续分配 seq、插入本体、
        # 累加会话 token/context/count。半条工具链在回放时会被上游 400。
        # 本轮的工具事件搭同一趟车（executor 在工具完成时攒好，带各自的时间戳）：
        # 它们原来是每条一次提交，ADB 上单次 commit 0.5~0.95s，一轮 2~3 个工具
        # 就能把 run 拖慢好几秒，而这些事件并没有「必须先于消息可见」的语义。
        committed_events: list[dict] = []
        inserted = await conversations.append_round(
            conversation_oid,
            rows,
            usage=_usage_block(payload["tokens"]),
            events=events,
            committed_events=committed_events,
        )

        # 只有 savepoint 真正提交成功的事件才进轮询快照：事件写失败时消息照常
        # 落库，但快照里不能出现一行「已持久化」的幽灵审计。
        for event in committed_events:
            run_cache.append_event(run_id, _cache_event(event))
        if rows:
            state["rounds"] = max(int(state.get("rounds") or 0), round_no)
            _accumulate(state.setdefault("usage", {}), payload["tokens"])
            if not payload["assistant"].get("tool_calls"):
                # 最后一轮没有工具调用 = 这次 run 的最终回答（§2.3 的
                # ``result_message_oid``）。
                state["result_message_oid"] = inserted[0]["oid"] if inserted else None
        # 本轮已持久化：清掉该轮的 ephemeral 状态，再让订阅者拉新快照。
        live.clear_round(run_id, round_no)
        live.publish_resync(run_id)
        # 不复用这一行给下一轮边界：``_should_stop`` 必须重新读一次，才能看到
        # 提交期间到达的 cancel_requested（取消可见性优先于省一次查询）。

    return on_round


def _on_summary(conversation: dict, conversation_oid: str, state: dict):
    """压缩结果落库（契约 §5.3 的在飞重建）。

    executor 已经把摘要重建进它自己的对话列表了，这里只负责**持久化**并同步内存
    里的会话行：下一次 run 组装历史时才会跳过 ``seq <= upto_seq`` 的原文。写失败
    只记日志——压缩本身已经完成，丢掉这条摘要的代价是「下一轮再压一次」，而不是
    这次 run 的结果不对。

    水位只许**向前**：一次 run 现在可以压多次（长 run 会再次越过阈值），但落库只有
    一个 ``summary`` + ``summary_upto_seq``。``seq`` 不退让是硬条件——把水位往回写
    会让已经摘要过的原文重新进入上下文（重复计费），也可能让摘要与水位错位。
    """

    async def on_summary(summary: object, upto_seq: object) -> None:
        text = str(summary or "").strip()
        seq = _optional_int(upto_seq)
        if not text or not seq:
            return
        previous = _optional_int(state.get("summary_upto"))
        if previous is None:
            previous = _optional_int(conversation.get("summary_upto_seq")) or 0
        if seq <= previous:
            # 同一个水位/更早的水位：重复写入只会把水位往后推，而摘要还是那份摘要。
            return
        try:
            await _conversations().set_summary(conversation_oid, text, seq)
        except Exception:  # noqa: BLE001 - 压缩是旁路，不能因落库失败打挂 run
            _logger.exception("ai conversation summary persist failed")
            return
        conversation["summary"] = text
        conversation["summary_upto_seq"] = seq
        state["summary_upto"] = seq
        state["compacted"] = True

    return on_summary


def _on_compaction(conversation_oid: str, state: dict):
    """压缩完成后的会话水位刷新（自动路径）。

    两件事必须落库，否则前端的占用条会一直停在压缩前的水位、运维也看不到降级：

    * ``context_tokens`` 用**压缩后**的估算值覆盖（仓储里 ``set_context_tokens``
      一直没人调用，这里接上它）。下一次 ``_on_round`` 会用实测 prompt 再覆盖一次，
      所以这里只是「在 compression 的那一刻」把条拉下来；
    * 降级（硬截断）记一行 ``compaction_degraded_at``：那是事实而不是开关，不清零。

    两个写都是旁路：失败只记日志，压缩已经完成，run 不该因此失败。
    """

    async def on_compaction(result: dict) -> None:
        if not isinstance(result, dict) or not result.get("compacted"):
            return
        state["compaction"] = {
            key: result.get(key)
            for key in (
                "upto_seq",
                "tokens_before",
                "tokens_after",
                "degraded",
                "reason",
            )
        }
        after = _optional_int(result.get("tokens_after"))
        if after is not None:
            try:
                await _conversations().set_context_tokens(conversation_oid, after)
            except Exception:  # noqa: BLE001 - 水位刷新是旁路
                _logger.exception("ai conversation context tokens refresh failed")
        if result.get("degraded"):
            try:
                await _conversations().mark_compaction_degraded(conversation_oid)
            except Exception:  # noqa: BLE001 - 降级标记同样不该打挂 run
                _logger.exception("ai conversation compaction degraded mark failed")

    return on_compaction


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
    """``on_round(...)`` 实参 → ``{round_no, assistant, tools, tokens, events}``。"""
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
    raw_events = payload.get("events")
    return {
        "round_no": _int(payload.get("round_no")),
        "assistant": dict(assistant) if isinstance(assistant, Mapping) else {},
        "tools": [dict(item) for item in raw_tools if isinstance(item, Mapping)]
        if isinstance(raw_tools, (list, tuple))
        else [],
        "tokens": _tokens_of(payload.get("usage")),
        # 缺省空元组：手写 payload 的测试替身与旧调用方没有这个键，不该因此 500。
        "events": [dict(item) for item in raw_events if isinstance(item, Mapping)]
        if isinstance(raw_events, (list, tuple))
        else [],
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
        # 旧形状没有事件缓冲（那个年代每个事件自己写一行）：空列表 = 走快路径。
        "events": [],
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


def _cache_event(event: Mapping[str, Any]) -> dict:
    """落库事件行 → ``run_cache.EVENT_KEYS`` 形状（``created_at`` 转展示串）。"""
    created = event.get("created_at")
    if isinstance(created, datetime):
        created_text = created.isoformat(sep=" ", timespec="seconds")
    else:
        created_text = created
    return {
        "oid": event.get("oid"),
        "round_no": event.get("round_no"),
        "tool_name": event.get("tool_name"),
        "tool_kind": event.get("tool_kind"),
        "args_summary": event.get("args_summary"),
        "result_summary": event.get("result_summary"),
        "status": event.get("status"),
        "created_at": created_text,
    }


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
    raw_reasoning = assistant.get("reasoning")
    reasoning = raw_reasoning if isinstance(raw_reasoning, str) and raw_reasoning else None
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
            # 思考过程只属于 assistant 行：它不参与回放（见 V049 的说明），
            # 落库只为「刷新页面后仍能看到这一轮想了什么」。
            "reasoning": reasoning,
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
                "content": executor._truncate(  # noqa: SLF001 - 同包同一份截断实现
                    str(item.get("content") or ""), MAX_TOOL_CONTENT_CHARS
                ),
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


def recent_limit(limit: object) -> int:
    """把调用方给的 ``limit`` 夹进 1..10（协议层已校验，这里兜服务层其它入口）。"""
    try:
        parsed = int(limit)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return RECENT_LIMIT_DEFAULT
    return max(1, min(RECENT_LIMIT_MAX, parsed))


async def list_recent(owner_oid: str, *, limit: int = RECENT_LIMIT_DEFAULT) -> list[dict]:
    """本人最近的 run（任意状态，``created_at`` 倒序，最多 10 条）。

    个人首页用；``owner_oid`` 是唯一的过滤条件，越权读取在这一层没有入口。
    """
    return await _runs().list_recent(str(owner_oid), recent_limit(limit))


async def run_detail(
    *,
    user: Mapping[str, Any],
    run_id: str,
    after_round: int = 0,
    since_msg_seq: int | None = None,
) -> dict:
    """``GET /runs/{id}``：状态 + 增量事件 + 本次 run 的提案（+ 可选增量消息）。

    ``after_round`` 只回 ``round_no > after_round`` 的事件：轮询载荷随轮次线性
    增长，而不是平方（每次把整条时间线重发一遍）。

    **1 秒内重复轮询不回源**：run 行、事件、提案都从进程内快照取，``after_round``
    在内存里切片；只有消息增量（``since_msg_seq``）每次都查库——它是唯一「必须
    实时」的部分（用户发消息、别的 run 落轮次）。快照由 :mod:`run_cache` 管，
    单 worker 前提与队列本身相同；取消/收尾会主动失效。

    ``since_msg_seq`` 是**轮级**增量：给了就额外回 ``seq > since_msg_seq`` 的消息，
    与 ``GET /conversations/{oid}/messages`` **同一形状、同一顺序**（升序、同一份
    view）。为什么不复用它：一次轮询里客户端要的是「run 进度 + 这轮新增了哪些
    消息」，两条请求会让「run 已结束、消息还没拉」变成一个可观测的中间态。
    不传则不返回该键——老客户端拿到的响应逐字不变。
    """
    owner_oid = str(user.get("id") or "")
    key = str(run_id)
    floor = max(0, int(after_round or 0))

    snapshot = run_cache.get(key)
    if snapshot is not None and str(snapshot["row"].get("owner_oid") or "") != owner_oid:
        # 缓存键只有 run_id：别人的 run 不能被一次命中绕过归属检查。
        snapshot = None
    if snapshot is None:
        row = await _runs().get(key, owner_oid)
        if row is None:
            raise AgentError(RUN_NOT_FOUND, "run not found", 404)
        # 事件与提案是**同一张表**的两种视图：一次查询喂两处，别再各查一遍。
        rows = await _event_rows(key)
        events = await proposal_service.run_events(
            request_id=key, user=dict(user), rows=rows
        )
        proposals = _run_proposals(rows, owner_oid)
        run_cache.put(
            key,
            row=row,
            events=events,
            proposals=proposals,
            ttl=_detail_ttl(row),
        )
    else:
        row = snapshot["row"]
        events = snapshot["events"]
        proposals = snapshot["proposals"]

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

    detail = {
        "run": run_view(row),
        "events": incremental,
        "proposals": proposals,
    }
    if since_msg_seq is not None:
        detail["messages"] = await conversations.messages_since(
            str(row.get("conversation_oid") or ""),
            max(0, int(since_msg_seq)),
            limit=conversations.MESSAGE_LIMIT_MAX,
        )
    return detail


def _detail_ttl(row: Mapping[str, Any]) -> float:
    """快照寿命：终态 30s（结果不会再变），活跃 1s（下一拍必须看得见进展）。"""
    if str(row.get("status") or "") in TERMINAL_STATUSES:
        return run_cache.TERMINAL_TTL
    return run_cache.DEFAULT_TTL


async def _event_rows(run_id: str) -> list[dict]:
    """轮询取事件：优先轻量投影（不拉两个 CLOB 列）。

    内存桩/旧实现没有 ``list_display_for_request`` 时回落到全量查询——形状一致，
    只是多背两列。
    """
    repo = _events()
    lister = getattr(repo, "list_display_for_request", None)
    if lister is None:
        return await repo.list_for_request(str(run_id))
    return await lister(str(run_id))


def _run_proposals(rows: Sequence[Mapping[str, Any]], owner_oid: str) -> list[dict]:
    """一次 run 的写工具提案（调用方已经取过这张表）。

    视图形状直接借 ``services.agent.proposal._list_item``：提案卡片的字段（preview
    的解析、双人码标记、过期时间）只该有一份实现，复制一遍就会和确认路径漂移。
    """
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
    "RECENT_LIMIT_DEFAULT",
    "RECENT_LIMIT_MAX",
    "cancel",
    "compact_conversation",
    "concurrency",
    "heartbeat_seconds",
    "kick",
    "list_active",
    "list_recent",
    "max_runs_per_user",
    "recent_limit",
    "recover_on_startup",
    "reset",
    "run_detail",
    "run_view",
    "stale_seconds",
    "submit",
    "watchdog_tick",
]
