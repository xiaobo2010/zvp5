"""回收站（``user_trash``）保留期清理任务（v4.2.4-2 契约 §3.3A-5）。

``DELETE /api/v3/users/{user_id}`` 是硬删除：被删用户的行 + 全部关联行先在同一个
事务里整包归档进 ``user_trash``（见 ``db/repos/oracle/users.py``
``archive_and_delete_user``）。归档**不是永久备份**——保留期 30 天（可配），到期
由本模块物理删除回收站行。

清理走 ``OracleUsersRepo.purge_trash_batch`` / ``count_expired_trash``（语句由
``user_trash_tbl`` 生成，见 ``db/repos/oracle/users.py``），按
:func:`db.repos.oracle.users._purge_trash_stmt` 的 ``deleted_at < cutoff`` +
``ROWNUM <= n`` 分批删，最多 ``TRASH_GC_MAX_BATCHES`` 批（与
``tasks/retention.py`` 的 ``_drain`` 同一套「短批即停 + 封顶」纪律：一次运行绝不
变成无界删除）。每批一次 commit，长清理不会在 ADB 上挂一个大事务。

保留期读法**防御式**（照抄 ``tasks/retention.py`` / ``tasks/files_retention.py``）：
``config.py`` 是部署侧文件，仓库里新增的键在旧主机上可能不存在，所以用
``import config`` + ``getattr`` + 代码内默认值，绝不 ``from config import ...``。

审计：ltype ``user_trash_gc`` / level ``warning``。为什么是 ``warning``——``warning``
是 ``app_logs`` 清理保护集（``db/repos/oracle/app_logs.py::PROTECTED_LEVEL``）的
判据，回收站的清理记录属于「不可逆删除的证据」，必须不会被 90 天日志清理顺手删掉；
``user_trash_gc`` 同时进 ``PROTECTED_LTYPES`` 的同类语义（该元组本身在
``app_logs.py`` 里，L3A 白名单不允许改，故只靠 level 保证不被清理——见报告「未决项」）。
``user`` 归属 ``scheduler``：定时触发没有登录主体，与
``tasks/retention.py::SCHEDULER_USER`` 一致。

调度接入：**尚未接线**。``main.py`` 在本阶段被冻结（L3A 白名单不含它），需要主会话/
部署方在 ``startup_event`` 里补一条 ``scheduler.add_job``（仿 ``daily_retention_purge``）::

    from tasks.user_trash_gc import purge_expired_trash

    scheduler.add_job(
        purge_expired_trash,
        "cron",
        hour=4,          # 03:00 附件 / 03:30 业务日志 / 04:00 报表 / 04:30 采样清理
        minute=0,
        second=0,
        timezone="Asia/Hong_Kong",
        id="daily_user_trash_gc",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

在接线之前，本模块只被测试导入（``tests/test_v4242_user_admin.py``），不会在生产
被自动触发——不会有半成品定时任务先跑起来。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

import config
from db import backends

# 契约 §3.3A-5 的保留期：30 天。
DEFAULT_TRASH_RETENTION_DAYS = 30
# 一批删多少行 / 一次最多跑多少批。
DEFAULT_TRASH_GC_BATCH_SIZE = 500
DEFAULT_TRASH_GC_MAX_BATCHES = 20

# 审计行的 type/level（见模块 docstring）。
AUDIT_TYPE = "user_trash_gc"
AUDIT_LEVEL = "warning"

# 定时触发没有登录主体；手动触发（若将来加端点）应传发起管理员的 oid。
SCHEDULER_USER = "scheduler"


def _positive_int(name: str, default: int) -> int:
    """Read a positive integer from ``config``, never raising on a bad value.

    Missing key, non-integer value and non-positive values all fall back to
    ``default`` — a broken deployment config must not take the app down.
    """
    raw = getattr(config, name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def trash_retention_days() -> int:
    """回收站保留期（天），默认 30，可被 ``USER_TRASH_RETENTION_DAYS`` 覆盖。"""
    return _positive_int("USER_TRASH_RETENTION_DAYS", DEFAULT_TRASH_RETENTION_DAYS)


def trash_gc_batch_size() -> int:
    return _positive_int("TRASH_GC_BATCH_SIZE", DEFAULT_TRASH_GC_BATCH_SIZE)


def trash_gc_max_batches() -> int:
    return _positive_int("TRASH_GC_MAX_BATCHES", DEFAULT_TRASH_GC_MAX_BATCHES)


def retention_cutoff(now: datetime, days: int) -> datetime:
    """Pure helper: 回收站行必须早于这个时刻才可被物理删除。"""
    return now - timedelta(days=days)


async def drain_trash(
    purge: Callable[[Any, int], Awaitable[int]],
    cutoff: datetime,
    batch_size: int,
    max_batches: int,
    on_batch: Callable[[int], None] | None = None,
) -> int:
    """把 ``purge`` 跑到底；返回删除总行数。

    一批短于 ``batch_size`` ⇒ 已无过期行，停。无论如何都受 ``max_batches`` 封顶。
    ``on_batch(total)`` 在每批提交后回调（纯记账）。
    """
    total = 0
    for _ in range(max_batches):
        deleted = int(await purge(cutoff, batch_size))
        total += deleted
        if on_batch is not None:
            on_batch(total)
        if deleted < batch_size:
            break
    return total


async def write_audit_row(deleted: int, remaining: int, user: str, cutoff: datetime) -> None:
    """写一条 ``user_trash_gc`` / ``warning`` 审计行（直接走 repo，无请求上下文）。"""
    await backends.get_applogs_repo().insert(
        url="",
        user=user,
        data=(
            f"回收站清理：物理删除 {deleted} 行（剩余 {remaining} 行），"
            f"保留期 {trash_retention_days()} 天，截止 {cutoff.isoformat()}"
        ),
        timestamp=datetime.now().timestamp(),
        ltype=AUDIT_TYPE,
        level=AUDIT_LEVEL,
        xuehai_id="",
    )


async def run_trash_gc(actor: str = SCHEDULER_USER) -> dict:
    """执行一轮回收站清理并返回计数（不吞异常；供测试与将来的手动入口调用）。

    返回 ``{"deleted", "remaining", "cutoff", "retention_days"}``。
    """
    days = trash_retention_days()
    cutoff = retention_cutoff(datetime.now(), days)
    repo = backends.get_users_repo()

    deleted = await drain_trash(
        repo.purge_trash_batch,
        cutoff,
        trash_gc_batch_size(),
        trash_gc_max_batches(),
    )
    remaining = int(await repo.count_expired_trash(cutoff))
    await write_audit_row(deleted, remaining, actor, cutoff)
    return {
        "deleted": deleted,
        "remaining": remaining,
        "cutoff": cutoff.isoformat(),
        "retention_days": days,
    }


async def purge_expired_trash() -> int:
    """定时入口（``daily_user_trash_gc``）；返回物理删除的行数。

    失败一律**打印不抛**：坏行或瞬时数据库错误不能把调度器/进程带崩（与
    ``tasks/retention.py::purge_expired_records``、
    ``tasks/files_retention.py::purge_expired_attachments`` 同一约定）。
    """
    try:
        result = await run_trash_gc(SCHEDULER_USER)
    except Exception as exc:
        print(f"purge_expired_trash failed: {exc!r}")
        return 0

    print(
        f"purge_expired_trash: deleted {result['deleted']} row(s); "
        f"remaining={result['remaining']}; retention={result['retention_days']}d; "
        f"cutoff={result['cutoff']}"
    )
    return int(result["deleted"])
