"""AI 定时任务的调度执行器（``tick``）与排程工具函数。

一次 tick 的形状：

    claim_due(CAS) → 逐条 _handle → 一律 mark_result(推进 next_run_at)

三条不能省的性质：

* **失败不能卡住调度**。执行、跳过、失败都写 ``last_status`` 并推进
  ``next_run_at``；漏掉的后果是一条坏任务被每个 tick 重试一次，日志和额度一起
  被打爆。单条任务抛异常只影响它自己（tick 里逐条 try/except）。
* **同一任务同一时刻最多 1 个 run**。靠两点：tick 内的 claim CAS（同一条任务
  只被一个 tick 领走），以及「owner 已有 queued|running 就跳过本周期」。后者是
  必须的——两次 tick 之间隔了几十秒，前一次的 run 可能还在跑。
* **必须先解析 owner 自己的 key 再提交**。key 只对本人可见（个人 key 不共享），
  没有可用 key 的任务在提交时必然 503；提前判掉能让 ``last_status`` 说清原因，
  而不是留下一堆 ``failed``。

为什么不用 ``run_queue.submit``（**故意偏离**，改之前先读）：

``run_queue.submit`` 是**交互式**提交的入口，它有三道只对交互有意义的闸：
密码解锁（``agent_unlock.is_unlocked``，30 分钟就过期）、越狱审核证据链、以及
「首条消息定会话标题」。定时任务在凌晨执行时解锁窗口早已过期，走 submit 等于
让每一次计划执行都 ``skipped_locked``。所以这里直接复用同一套仓储原语
（``ai_runs.insert_queued``：用户消息 + run 的同一事务、幂等键、活跃唯一索引），
只在 ``kick()`` 上回到 run_queue——那是编排（起泵），不是校验。

代价是：定时任务的 prompt 不走 ``moderation.check`` 的**违规证据**入库（不写
``ban_violations``）。这里是另一种取舍：审核在创建/审核阶段已经由管理员做过，
运行期再过一遍违规记录等于让一条文案在半夜把任务 owner 拉进封禁梯度。运行期只
做拦截（``skipped_flagged``），不产生处罚证据。

**LLM 安全认证是例外**：判定成立时任务内容确实是 owner 写的，责任归 owner——
记违规、发警告、按首档封号，然后跳过本周期；判定跑不起来（``unavailable``）
只跳过 + ``ai_safety_guard_error``，不碰违规表。
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Mapping
from uuid import uuid4

from apscheduler.triggers.cron import CronTrigger

from db import backends
from db.repos.oracle.ai_scheduled_tasks import OracleAiScheduledTasksRepo  # noqa: F401 - 测试按符号打桩
from db.repos.protocols import ActiveRunConflict
from services import violation_guard
from services.agent import run_queue
from services.ai import conversations, credentials, moderation, quota, safety_guard
from typings.log import ZVMSLog
from util.ban import ban_is_active

_logger = logging.getLogger(__name__)

# 排程时区固定上海：cron 表达式是人写的（"0 3 * * *" = 每天凌晨 3 点），跟服务器
# 的 TZ 环境变量走会让同一条配置在两台机器上跑在不同时刻。
SCHEDULE_TZ_STR = "Asia/Shanghai"

MIN_INTERVAL_MINUTES = 15
DEFAULT_INTERVAL_MINUTES = 1440

# ``last_status`` 是 VARCHAR2(16)：这里的每个常量都必须 ≤16 个字符。
STATUS_QUEUED = "queued"
STATUS_FAILED = "failed"
STATUS_SKIPPED_REVIEW = "skipped_review"
STATUS_SKIPPED_BUSY = "skipped_busy"
STATUS_SKIPPED_NO_KEY = "skipped_no_key"
STATUS_SKIPPED_QUOTA = "skipped_quota"
STATUS_SKIPPED_OWNER = "skipped_owner"
STATUS_SKIPPED_FLAGGED = "skipped_flagged"
# LLM 安全认证拒绝：prompt 已被管理员审核过，这里只跳过，不计违规、不发警告。
STATUS_SKIPPED_SAFETY = "skipped_safety"

LAST_STATUS_MAX = 16
LAST_ERROR_MAX = 512
NAME_MAX = 128

# 定时任务的额度口径：与交互式对话同一个 AF 池（agent 不单列）。
QUOTA_KIND = "af"

_TZ = None


def _tz():
    """``ZoneInfo`` 延迟构造：导入期不做时区库 I/O。"""
    global _TZ
    if _TZ is None:
        from zoneinfo import ZoneInfo

        _TZ = ZoneInfo(SCHEDULE_TZ_STR)
    return _TZ


# ---------------------------------------------------------------------------
# 错误
# ---------------------------------------------------------------------------

class ScheduledError(Exception):
    """可预期的业务错误，由路由层翻译成 ``HTTPException(detail=code)``。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


class SafetySkipped(ScheduledError):
    """LLM 安全认证拒绝：本周期不提交 run，任务行已写 ``skipped_safety``。"""

    def __init__(self, message: str) -> None:
        super().__init__("AI_SCHEDULE_SAFETY_REJECTED", message, 422)


def _clip(value: object, limit: int) -> str | None:
    """截断到列宽。超长直接写库是 ORA-12899（500），截断至少保住结论。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# 仓储 / 依赖（测试逐文件打桩，与 conversations._repo 同一范式）
# ---------------------------------------------------------------------------

def _repo() -> Any:
    return backends.get_ai_scheduled_tasks_repo()


def repo() -> Any:
    """当前任务仓储（路由层用；测试打桩 :func:`_repo` 即可连带生效）。"""
    return _repo()


def _runs() -> Any:
    return backends.get_ai_runs_repo()


def _users() -> Any:
    return backends.get_users_repo()


# ---------------------------------------------------------------------------
# 排程
# ---------------------------------------------------------------------------

def cron_trigger(expr: object) -> CronTrigger:
    """5 段式 crontab → ``CronTrigger``（固定上海时区）。

    非法表达式在这里就抛 ``ValueError``/``KeyError``；调用方（创建/PATCH）翻成
    422，调度侧则永远只会看到已经解析成功的表达式。
    """
    text = str(expr or "").strip()
    if not text:
        raise ValueError("cron expression is empty")
    return CronTrigger.from_crontab(text, timezone=_tz())


def validate_cron(expr: object) -> str:
    """校验并规范化 cron 表达式；非法时抛 :class:`ScheduledError`（422）。"""
    text = str(expr or "").strip()
    if not text:
        raise ScheduledError("AI_SCHEDULE_BAD_CRON", "cron expression is required", 422)
    if len(text) > 64:
        raise ScheduledError("AI_SCHEDULE_BAD_CRON", "cron expression is too long", 422)
    try:
        cron_trigger(text)
    except (ValueError, KeyError, TypeError):
        raise ScheduledError(
            "AI_SCHEDULE_BAD_CRON", "cron expression is not a valid 5-field crontab", 422
        ) from None
    return text


def validate_interval(minutes: object) -> int:
    """``interval_minutes`` 必须是 ≥15 的整数。

    15 分钟不是拍脑袋：定时任务每次都真实消耗额度，配成 1 分钟一定是误填。
    """
    if isinstance(minutes, bool) or not isinstance(minutes, int):
        raise ScheduledError(
            "AI_SCHEDULE_BAD_INTERVAL", "interval_minutes must be an integer", 422
        )
    if minutes < MIN_INTERVAL_MINUTES:
        raise ScheduledError(
            "AI_SCHEDULE_BAD_INTERVAL",
            f"interval_minutes must be >= {MIN_INTERVAL_MINUTES}",
            422,
        )
    return minutes


def next_run_at(task: Mapping[str, Any], *, after: datetime) -> datetime:
    """下一次该跑的时刻（naive 本地时间，与 TIMESTAMP 列口径一致）。

    ``after`` 用「本次 tick 的 now」而不是「库里原来的 next_run_at」：任务已经
    迟到很久（停机一天）时，按原排程补齐会连跑十几次，那不是用户想要的。
    """
    kind = str(task.get("schedule_kind") or "interval")
    if kind == "cron":
        trigger = cron_trigger(task.get("cron_expr"))
        nxt = trigger.get_next_fire_time(None, after.replace(tzinfo=_tz()))
        if nxt is None:  # 合法 crontab 理论上不会没有下一次
            return after + timedelta(minutes=DEFAULT_INTERVAL_MINUTES)
        return nxt.replace(tzinfo=None)

    try:
        minutes = int(task.get("interval_minutes") or DEFAULT_INTERVAL_MINUTES)
    except (TypeError, ValueError):
        minutes = DEFAULT_INTERVAL_MINUTES
    return after + timedelta(minutes=max(minutes, MIN_INTERVAL_MINUTES))


def first_run_at(
    *, schedule_kind: str, interval_minutes: int, cron_expr: str | None,
    now: datetime | None = None,
) -> datetime:
    """创建 / 改排程时的首次触发时刻：从「现在」往后算一个周期。"""
    moment = now or datetime.now()
    return next_run_at(
        {
            "schedule_kind": schedule_kind,
            "interval_minutes": interval_minutes,
            "cron_expr": cron_expr,
        },
        after=moment,
    )


# ---------------------------------------------------------------------------
# 提交
# ---------------------------------------------------------------------------

async def _conversation_for(task: Mapping[str, Any]) -> str:
    """复用该任务上一次的会话；没有就建一个新的 agent 会话。

    复用而不是每次新建：一次任务跑几百次之后，左栏应该只有一条「定时任务 · X」，
    而不是几百条孤立会话。用完即弃的会话也会把会话表撑成日志表。
    """
    previous = await _repo().last_conversation_oid(task["_id"])
    if previous:
        return str(previous)
    created = await conversations.create(
        user={"id": task["owner_oid"]},
        mode="agent",
        title=_clip(f"定时任务 · {task.get('name') or ''}", NAME_MAX),
    )
    return str(created.get("oid") or "")


def _owner_identity(owner_oid: str, owner: Mapping[str, Any] | None) -> dict:
    """把用户文档归一成 ``safety_guard`` 认得的形状（``per`` = 原始角色）。

    仓储给的是 ``roles``，JWT 给的是 ``per``；两者是同一份东西的两种字段名。
    ``per`` 优先保留 E2 权限映射（如果将来用户文档真的带了它）。
    """
    raw = (owner or {}).get("per")
    if not isinstance(raw, (list, tuple, set, frozenset)):
        raw = (owner or {}).get("roles")
    roles = [role for role in (raw or []) if isinstance(role, str)]
    return {"id": owner_oid, "per": roles}


async def _log_safety_skip(owner_oid: str, task_oid: str, reason: str) -> None:
    """定时任务被安全认证跳过的审计（旁路：写失败不改变「不跑」的结论）。"""
    try:
        await ZVMSLog(
            "",
            owner_oid,
            f"定时任务 {task_oid} 被 LLM 安全认证跳过：{reason}",
            type="ai_safety_guard_scheduled_skip",
            level="warning",
        ).insert_log()
    except Exception:  # noqa: BLE001 - 审计是旁路
        _logger.exception("写入 ai_safety_guard_scheduled_skip 日志失败")


async def _record_safety_skip(
    task: Mapping[str, Any], verdict: Any, *, now: datetime
) -> str | None:
    """写 ``skipped_safety`` 并推进 ``next_run_at``；返回给用户的错误摘要。

    只在任务**本来该跑**（``next_run_at <= now``）时改排程：``POST /{oid}/run-now``
    可能在周期的中途手动触发，那种情况下拒绝一次不该把正常排程往后推。
    """
    task_oid = str(task["_id"])
    owner_oid = str(task.get("owner_oid") or "")
    category = str(getattr(verdict, "category", "") or "destructive_request")
    reason = f"prompt rejected by AI safety guard ({category})"

    await _log_safety_skip(owner_oid, task_oid, reason)

    due = task.get("next_run_at")
    if isinstance(due, datetime) and due > now:
        return reason

    try:
        upcoming = next_run_at(task, after=now)
    except Exception:  # noqa: BLE001 - 非法排程也要推进，否则每个 tick 重试
        upcoming = now + timedelta(minutes=DEFAULT_INTERVAL_MINUTES)
    try:
        await _repo().mark_result(
            task_oid,
            last_status=STATUS_SKIPPED_SAFETY,
            last_error=_clip(reason, LAST_ERROR_MAX),
            next_run_at=upcoming,
            counted=False,
        )
    except Exception:  # noqa: BLE001 - 写结论失败不改变「本次不提交」
        _logger.exception("ai scheduled task: mark_result(skipped_safety) failed")
    return reason


async def _canonical_model(owner_oid: str, model: object) -> str | None:
    """定时任务里存的模型 → 合格 ID（``provider/model``）。

    任务行是**用户写的**（可能还是老数据里的裸名），新产生的 run 行要统一口径，
    所以提交时解析一次。解析不了（未知模型 / 有歧义 / 目录读失败）就原样保留：
    这里不是拒绝用户任务的地方，执行期遇到问题会给出 ``model_ambiguous`` /
    ``model_not_found``，比在凌晨的 tick 里静默改选一个渠道好排障。
    """
    text = str(model).strip() if model else ""
    if not text:
        return None
    try:
        entries = await credentials.catalog(owner_oid)
        resolved = credentials.resolve_model(entries, text)
    except credentials.ModelAmbiguous:
        return text
    except Exception:  # noqa: BLE001 - 目录读失败不该让任务不跑
        _logger.warning("ai scheduled task model resolve failed", exc_info=True)
        return text
    return resolved.qualified if resolved is not None else text


async def submit_task_run(task: Mapping[str, Any], *, now: datetime) -> str:
    """把任务提交成一次 ``queued`` run；返回 run_id。

    ``client_msg_id`` 用「任务 + 分钟」做幂等键：同一个调度槽重复提交（tick 重入
    或进程重启后立刻再跑）只会命中已有 run，不会重复写消息、重复计费。
    """
    task_oid = str(task["_id"])
    owner_oid = str(task["owner_oid"])
    stored_model = await _canonical_model(owner_oid, task.get("model"))

    # 灰区闸门：department owner 的 prompt 在提交前过一遍 LLM 安全认证。
    # 任务内容是 owner 自己写的，判定成立就由 owner 承担：记违规 + 警告 + 首档
    # 封号；判定跑不起来（无凭据/超时/坏 JSON）只跳过本周期，不产生证据。
    owner = await _users().get_by_oid(owner_oid)
    identity = _owner_identity(owner_oid, owner)
    if safety_guard.is_department(identity):
        verdict = await safety_guard.screen(
            identity,
            str(task.get("prompt_text") or ""),
            model_hint=stored_model,
        )
        if not verdict.allowed:
            if not verdict.unavailable:
                try:
                    await violation_guard.record_agent_jailbreak(
                        identity,
                        rule=str(verdict.category or ""),
                        detail=str(task.get("prompt_text") or ""),
                    )
                except Exception:  # noqa: BLE001 - 入库失败不改变「本周期不跑」
                    _logger.exception("定时任务安全认证违规入库失败（本次仍然跳过）")
            reason = await _record_safety_skip(task, verdict, now=now)
            raise SafetySkipped(reason or "prompt rejected by AI safety guard")

    conversation_oid = await _conversation_for(task)
    if not conversation_oid:
        raise ScheduledError(
            "AI_SCHEDULE_CONVERSATION_FAILED", "cannot create task conversation", 500
        )

    run_id = uuid4().hex
    row, _created = await _runs().insert_queued(
        run_id=run_id,
        conversation_oid=conversation_oid,
        owner_oid=owner_oid,
        message_oid=uuid4().hex,
        content=str(task.get("prompt_text") or ""),
        client_msg_id=f"sched:{task_oid}:{now.strftime('%Y%m%d%H%M')}",
        model=stored_model,
    )
    run_id = str(row.get("_id") or run_id)
    # 归因必须在 kick 之前：泵一起来就可能开跑，晚写会出现 task_oid 为空的窗口。
    await _repo().attach_run_task(run_id, task_oid)
    await run_queue.kick()
    return run_id


# ---------------------------------------------------------------------------
# 前置检查
# ---------------------------------------------------------------------------

def _has_personal_key(entries: list[dict]) -> bool:
    """生效目录里是否含个人凭据（``catalog`` 已滤掉 disabled/过期项）。"""
    return any(
        str(entry.get("scope") or "") == credentials.SCOPE_PERSONAL
        for entry in entries
    )


async def _preflight(task: Mapping[str, Any]) -> tuple[str | None, str | None]:
    """返回 ``(跳过状态, 原因)``；``(None, None)`` 表示可以提交。

    顺序是刻意的：审核 → 在跑 → owner 存在 → key → 额度 → 内容审核。越靠前的
    判断越便宜、越不会误报。
    """
    owner_oid = str(task.get("owner_oid") or "")

    if str(task.get("review_status") or "") != "approved":
        return STATUS_SKIPPED_REVIEW, "prompt has not been approved"

    active = await _runs().list_active(owner_oid)
    if active:
        return STATUS_SKIPPED_BUSY, f"owner already has {len(active)} active run(s)"

    owner = await _users().get_by_oid(owner_oid)
    if owner is None:
        return STATUS_SKIPPED_OWNER, "task owner no longer exists"
    if ban_is_active(owner):
        # 封禁账号不该在后台继续花自己的额度、用自己的 key 跑任务——否则「封禁」
        # 只是关掉了登录界面。
        return STATUS_SKIPPED_OWNER, "task owner is banned"

    entries = await credentials.catalog(owner_oid)
    if not entries:
        return STATUS_SKIPPED_NO_KEY, "owner has no usable AI credential"

    roles = list(owner.get("roles") or [])
    # 个人 key 不占平台 AF 额度（与 ``executor._billable`` 同一口径）：执行期对
    # personal 凭据 peek/consume 双侧豁免，预检若按平台额度挡下，就成了「用自己的
    # key 却被配额拦住」。判定对象是凭据 scope，不是 owner 角色。
    if not _has_personal_key(entries):
        allowed, used, limit = await quota.peek(
            owner_oid, QUOTA_KIND, 1, roles=roles
        )
        if not allowed:
            return (
                STATUS_SKIPPED_QUOTA,
                f"monthly {QUOTA_KIND} quota exhausted ({used}/{limit})",
            )

    verdict = moderation.check(str(task.get("prompt_text") or ""))
    if verdict.hit:
        return STATUS_SKIPPED_FLAGGED, f"prompt blocked by rule {verdict.rule}"

    return None, None


# ---------------------------------------------------------------------------
# tick
# ---------------------------------------------------------------------------

async def _handle(repo: Any, task: Mapping[str, Any], now: datetime) -> tuple[str, str | None, bool]:
    """处理一条已领取的任务，返回 ``(last_status, last_error, counted)``。"""
    status, reason = await _preflight(task)
    if status is not None:
        return status, reason, False

    try:
        await submit_task_run(task, now=now)
    except SafetySkipped as exc:
        # 本周期不跑：tick 会在下游统一 mark_result（与 submit 里写的一致）。
        return STATUS_SKIPPED_SAFETY, exc.message, False
    except ActiveRunConflict:
        return STATUS_SKIPPED_BUSY, "an active run already exists", False
    except ScheduledError as exc:
        return STATUS_FAILED, exc.message, False
    return STATUS_QUEUED, None, True


async def tick(now: datetime | None = None) -> dict[str, int]:
    """调度一轮：领取到期任务，逐条执行/跳过，并推进下一次触发时刻。

    逐条 try/except 是硬要求：一条任务的字段异常（比如库里存了非法 cron，只能
    由 DBA 改出来）不该让同一批里的其它任务都不跑。
    """
    moment = now or datetime.now()
    repo = _repo()
    summary = {
        "claimed": 0,
        "queued": 0,
        "skipped": 0,
        "failed": 0,
        "errors": 0,
    }

    try:
        claimed = await repo.claim_due(moment)
    except Exception:  # noqa: BLE001 - 调度器是旁路，不能把异常抛回 APScheduler
        _logger.exception("ai scheduled tasks: claim failed")
        return summary

    summary["claimed"] = len(claimed)
    for task in claimed:
        status = STATUS_FAILED
        error: str | None = None
        counted = False
        try:
            status, error, counted = await _handle(repo, task, moment)
        except Exception as exc:  # noqa: BLE001 - 单条失败不影响同批其它任务
            _logger.exception("ai scheduled task failed: %s", task.get("_id"))
            error = f"{type(exc).__name__}: {exc}"

        try:
            upcoming = next_run_at(task, after=moment)
        except Exception:  # noqa: BLE001 - 非法 cron 也要推进，否则每分钟重试
            _logger.warning(
                "ai scheduled task has an unusable schedule, falling back to 24h: %s",
                task.get("_id"),
            )
            upcoming = moment + timedelta(minutes=DEFAULT_INTERVAL_MINUTES)

        try:
            await repo.mark_result(
                task["_id"],
                last_status=_clip(status, LAST_STATUS_MAX) or STATUS_FAILED,
                last_error=_clip(error, LAST_ERROR_MAX),
                next_run_at=upcoming,
                counted=counted,
            )
        except Exception:  # noqa: BLE001 - 写结论失败也要继续跑完这一批
            _logger.exception(
                "ai scheduled task: mark_result failed: %s", task.get("_id")
            )
            summary["errors"] += 1
            continue

        if status == STATUS_QUEUED:
            summary["queued"] += 1
        elif status == STATUS_FAILED:
            summary["failed"] += 1
        else:
            summary["skipped"] += 1

    return summary


async def run_now(task: Mapping[str, Any], *, now: datetime | None = None) -> str:
    """手动立即执行一次（``POST /{oid}/run-now``）。

    与 tick 共用前置检查，所以「未审核 / 已在跑 / 无 key / 额度不足」在这里是
    **错误**而不是跳过——用户点了按钮，必须知道为什么没跑。
    """
    moment = now or datetime.now()
    status, reason = await _preflight(task)
    if status == STATUS_SKIPPED_REVIEW:
        raise ScheduledError(
            "AI_SCHEDULE_NOT_APPROVED", "task prompt has not been approved", 409
        )
    if status == STATUS_SKIPPED_BUSY:
        raise ScheduledError(
            "AI_SCHEDULE_BUSY", reason or "an active run already exists", 409
        )
    if status == STATUS_SKIPPED_OWNER:
        raise ScheduledError("AI_SCHEDULE_NO_OWNER", reason or "owner not found", 409)
    if status == STATUS_SKIPPED_NO_KEY:
        raise ScheduledError(
            "AI_SCHEDULE_NO_PROVIDER", reason or "no usable AI credential", 409
        )
    if status == STATUS_SKIPPED_QUOTA:
        raise ScheduledError(
            "AI_SCHEDULE_QUOTA_EXHAUSTED", reason or "monthly quota exhausted", 429
        )
    if status == STATUS_SKIPPED_FLAGGED:
        raise ScheduledError(
            "AI_SCHEDULE_MODERATION_REJECTED", reason or "prompt was blocked", 422
        )
    return await submit_task_run(task, now=moment)
