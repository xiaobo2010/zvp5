"""APScheduler 装配与应用生命周期（startup / shutdown）。

14 个周期任务的**顺序、id、cron/interval 参数**都是运维契约（见各段注释里的时段
避让说明），从 ``main`` 搬过来时逐字保留。``main`` 只 re-export ``scheduler`` /
``startup_event`` / ``shutdown_event``：运维工具（``services/agent/tools_ops*``）
与测试都按 ``main.scheduler`` 取同一个实例。
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from db import backends
from services import perf_sampler, settings_runtime
from services.agent import run_queue
from services.ai import scheduled_runner
from services.roles import policy as role_policy
from tasks.compute_time import generate_reports, rebuild_statistics
from tasks.files_retention import purge_expired_attachments
from tasks.ocr_upload_gc import run_ocr_upload_gc
from tasks.retention import purge_expired_records
from tasks.user_trash_gc import purge_expired_trash

_logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def mark_all_tasks_failed():
    await backends.get_tasks_repo().fail_pending("Program interrupted unexpectedly")


async def startup_event():
    """Startup event handler to initialize the application."""
    _logger.info("Starting up...")
    scheduler.start()

    # Rebuild the derived volunteer-time statistics daily at 01:00 HKT (UTC+8).
    scheduler.add_job(
        rebuild_statistics,
        "cron",
        hour=1,
        minute=0,
        second=0,
        timezone="Asia/Hong_Kong",
        id="daily_compute_time",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # Schedule compute_time to run daily at 00:00 HKT (UTC+8)
    scheduler.add_job(
        generate_reports,
        "cron",
        hour=4,
        minute=0,
        second=0,
        timezone="Asia/Hong_Kong",
        id="daily_generate_reports",
        # 报表生成要写几千个文件再打包，跑一次几分钟：错过触发窗口不该重跑
        # （coalesce），也不该和上一次重叠（max_instances）。
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # Clear the bytes of soft-deleted attachments older than the retention
    # window daily at 03:00 HKT (UTC+8). Tombstone metadata is kept.
    scheduler.add_job(
        purge_expired_attachments,
        "cron",
        hour=3,
        minute=0,
        second=0,
        timezone="Asia/Hong_Kong",
        id="daily_purge_files",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # OCR 上传图（义工登记表）保留 3 天：到点硬删原图 + 缩略图 + OCR 行 + 事件，
    # 活动绑定的义工单不在此列（entity_type='activity' 是证据）。05:00 避开
    # 03:00 的附件墓碑清理与 03:30 的留存清理，三件事不同表但都要 ADB 写额度。
    scheduler.add_job(
        run_ocr_upload_gc,
        "cron",
        hour=5,
        minute=0,
        second=0,
        timezone="Asia/Hong_Kong",
        id="daily_ocr_upload_gc",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # Delete expired app_logs / export_tasks rows daily at 03:30 HKT (UTC+8).
    # minute=30: hour=3 is taken by daily_purge_files above.
    scheduler.add_job(
        purge_expired_records,
        "cron",
        hour=3,
        minute=30,
        second=0,
        timezone="Asia/Hong_Kong",
        id="daily_retention_purge",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # Delete user_trash rows whose 30-day recovery window has passed, daily at
    # 02:00 HKT (UTC+8). 02:00 is the only free slot: 01:00 compute_time,
    # 03:00 files, 03:30 retention, 04:00 reports, 04:30 perf retention.
    # Retention window: USER_TRASH_RETENTION_DAYS (default 30) in
    # tasks/user_trash_gc.py.
    scheduler.add_job(
        purge_expired_trash,
        "cron",
        hour=2,
        minute=0,
        second=0,
        timezone="Asia/Hong_Kong",
        id="daily_user_trash_gc",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # 性能采样三档 + 每天一次的采样清理（设计 §4）。间隔全部可配
    # （PERF_HOST_ORACLE... 见 services/perf_sampler.py），单 worker 下进程内采样可靠。
    # 30s 档纯本地零 DB 往返；120s 档为 1 条合并语句；600s 档为 3 条慢语句。
    scheduler.add_job(
        perf_sampler.sample_host,
        "interval",
        seconds=perf_sampler.host_interval_seconds(),
        id="perf_sample_host",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=30,
    )
    scheduler.add_job(
        perf_sampler.sample_oracle,
        "interval",
        seconds=perf_sampler.oracle_interval_seconds(),
        id="perf_sample_oracle",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=60,
    )
    scheduler.add_job(
        perf_sampler.sample_slow,
        "interval",
        seconds=perf_sampler.slow_interval_seconds(),
        id="perf_sample_slow",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    # 采样表保留期清理（PERF_RETENTION_DAYS，默认 14 天）。04:30 HKT：03:00
    # 附件、03:30 业务日志、04:00 报表均已占用。
    scheduler.add_job(
        perf_sampler.purge_expired,
        "cron",
        hour=4,
        minute=30,
        second=0,
        timezone="Asia/Hong_Kong",
        id="daily_perf_retention_purge",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # 在线设置中心：每 5 秒把 SYSTEM_SETTINGS / USER_QUOTA_OVERRIDES 读进进程内
    # 快照。同步读取点（AI 配置视图、上下文窗口、上传配额）直接读内存，调用方
    # 签名不变；``PUT /api/system/settings`` 会立刻改写快照，本任务只负责把**别的
    # worker** 的修改在 5 秒内带过来。读库失败由 refresh() 内部吞掉并回落默认值。
    scheduler.add_job(
        settings_runtime.refresh,
        "interval",
        seconds=settings_runtime.REFRESH_SECONDS,
        id="system_settings_refresh",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=2,
    )

    # 角色功能开关与额度（V043）：每 5 秒把 ROLE_FEATURES / ROLE_QUOTAS 读进快照。
    # 功能闸（util.permission.features.has_feature）与额度预检都是同步判定，不刷
    # 就会一直按代码默认值判；管理页保存后也只对处理该请求的 worker 生效。
    scheduler.add_job(
        role_policy.refresh,
        "interval",
        seconds=role_policy.REFRESH_SECONDS,
        id="role_policy_refresh",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=2,
    )

    # AI 会话的后台 run 队列（设计 §4.3）：先把上次进程遗留的 running 收敛成
    # interrupted（单 worker 下「刚启动」等价于「没有合法在跑的任务」），再起泵
    # 去领 queued 的。收敛失败不该挡住启动——watchdog 每 30s 还会再试一次。
    try:
        recovered = await run_queue.recover_on_startup()
        if recovered:
            _logger.info("ai run queue: %s interrupted run(s) recovered", recovered)
    except Exception:  # noqa: BLE001 - 启动阶段的库不可用由 health 报告
        _logger.exception("ai run queue startup recovery failed")

    # run 心跳 watchdog：心跳早于 AI_RUN_STALE_SECONDS 的 running 判僵尸并掐掉
    # 对应协程（阈值下限 max(360, run_timeout+60)，见 run_queue.stale_seconds）。
    scheduler.add_job(
        run_queue.watchdog_tick,
        "interval",
        seconds=30,
        id="ai_run_watchdog",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=10,
    )

    # AI 定时任务：每分钟扫一次到期任务。claim 是 CAS（见 scheduled_runner），
    # 所以这里的 max_instances=1 只是省重复扫描，不是正确性依赖。
    scheduler.add_job(
        scheduled_runner.tick,
        "interval",
        seconds=60,
        id="ai_schedule_tick",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=30,
    )

    try:
        await run_queue.kick()
    except Exception:  # noqa: BLE001 - 起泵失败只影响后台执行，接口仍可用
        _logger.exception("ai run queue kick failed")

    _logger.info("Application started.")


async def shutdown_event():
    """Shutdown event handler to clean up resources."""
    _logger.info("Shutting down...")
    await mark_all_tasks_failed()
    scheduler.shutdown()
    try:
        from db import cache as db_cache

        await db_cache.aclose()
    except Exception:  # noqa: BLE001 - 缓存连接关不掉不影响进程退出
        pass
    try:
        from services.ai import ocr_engines as ocr_engines_module

        await ocr_engines_module.aclose()
    except Exception:  # noqa: BLE001 - OCR 连接池关不掉不影响进程退出
        pass
    _logger.info("Application shut down.")
