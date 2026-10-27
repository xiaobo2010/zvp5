"""OCR 上传图的短保留期清理任务（默认 3 天）。

背景：义工登记上传的图片（``purpose='volunteer_order'``）只是 OCR 的一次性
输入——识别完就再没有业务价值，却会永久占着 ``FILES`` 的 BLOB 和缩略图。
真正需要留证的是**绑定到活动**的图（``entity_type='activity'``）和**管理员已
裁决**的图（``status IN ('verified','rejected')``），这两类永远不归本任务管。

删除口径只有一处定义：``db/repos/oracle/files.py`` 的
``_ocr_upload_candidates_stmt``。本模块只负责「什么时候跑、跑几批、出错别炸」，
SQL 条件不在第二个地方重复，避免审计时两个文件给出不同的答案。

纪律（照抄 ``tasks/user_trash_gc.py`` / ``tasks/retention.py``）：

* 每批独立事务（``purge_ocr_uploads`` 自己 commit），长清理不在 ADB 上挂一个
  大事务；
* 短批即停：一批删出来的原图少于批大小 ⇒ 没有更多过期行了；
* 批数封顶（默认 10 批），一次运行绝不变成无界删除；
* 异常吞掉只打印——坏行或瞬时数据库错误不能把调度器/进程带崩。

保留期读法**防御式**（与 ``tasks/files_retention.py`` 同一理由）：``config.py``
是部署侧文件，仓库里新增的键在旧主机上可能不存在，所以 ``import config`` +
``getattr`` + 代码内默认值，绝不 ``from config import ...``。

调度接入：**尚未接线**（``main.py`` 在本次改动里被冻结）。接线时在
``startup_event`` 里补一条 ``scheduler.add_job``，仿 ``daily_purge_files``::

    from tasks.ocr_upload_gc import run_ocr_upload_gc

    scheduler.add_job(
        run_ocr_upload_gc,
        "cron",
        hour=5,  # 03:00 附件墓碑 / 03:30 业务日志 / 04:00 报表 / 04:30 采样
        minute=0,
        second=0,
        timezone="Asia/Hong_Kong",
        id="daily_ocr_upload_gc",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

在接线之前，本模块只被测试导入（``tests/test_ocr_upload_gc.py``），不会在生产
被自动触发——不会有半成品定时任务先跑起来。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import config
from db import backends

# 义工登记图只用于 OCR，3 天足够回溯一次「图传错了/识别错了」的排障。
DEFAULT_OCR_UPLOAD_RETENTION_DAYS = 3
# 一批多少个原图（仓储内部再夹到 ≤200：Oracle 的 IN 列表要留余量）。
DEFAULT_OCR_UPLOAD_GC_BATCH_SIZE = 200
DEFAULT_OCR_UPLOAD_GC_MAX_BATCHES = 10

# 计数键必须与 ``OracleFilesRepo.purge_ocr_uploads`` 的返回一致。
_COUNT_KEYS = ("originals", "thumbs", "ocr_rows", "events")


def _positive_int(name: str, default: int) -> int:
    """Read a positive integer from ``config``, never raising on a bad value.

    Missing key, non-integer value and non-positive values all fall back to
    ``default``: a broken deployment config must not take the app (or the
    scheduler job) down.
    """
    raw = getattr(config, name, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def retention_days() -> int:
    """保留天数，默认 3，可被 ``OCR_UPLOAD_RETENTION_DAYS`` 覆盖。"""
    return _positive_int(
        "OCR_UPLOAD_RETENTION_DAYS", DEFAULT_OCR_UPLOAD_RETENTION_DAYS
    )


def batch_size() -> int:
    return _positive_int("OCR_UPLOAD_GC_BATCH_SIZE", DEFAULT_OCR_UPLOAD_GC_BATCH_SIZE)


def max_batches() -> int:
    return _positive_int(
        "OCR_UPLOAD_GC_MAX_BATCHES", DEFAULT_OCR_UPLOAD_GC_MAX_BATCHES
    )


def retention_cutoff(now: datetime, days: int) -> datetime:
    """Pure helper: 上传行必须早于这个时刻才可被物理删除。"""
    return now - timedelta(days=days)


def _empty_stats() -> dict[str, Any]:
    stats: dict[str, Any] = {key: 0 for key in _COUNT_KEYS}
    stats["batches"] = 0
    return stats


async def run_ocr_upload_gc() -> dict[str, Any]:
    """执行一轮清理并返回统计（永不抛异常）。

    返回 ``{"originals", "thumbs", "ocr_rows", "events", "batches", "cutoff",
    "retention_days"}``；出错时额外带 ``"error"``（已删除的行照常计数——它们
    是真删掉了的，不该因为后面的批次失败而报 0）。
    """
    days = retention_days()
    cutoff = retention_cutoff(datetime.now(), days)
    stats = _empty_stats()
    size = batch_size()

    try:
        repo = backends.get_files_repo()
        for _ in range(max_batches()):
            result = await repo.purge_ocr_uploads(cutoff, size)
            stats["batches"] += 1
            for key in _COUNT_KEYS:
                stats[key] += int(result.get(key) or 0)
            # 短批即停：这一批没凑满，说明过期行已经清完。
            if int(result.get("originals") or 0) < size:
                break
    except Exception as exc:  # 定时任务绝不能把调度器带崩
        stats["error"] = repr(exc)
        print(f"run_ocr_upload_gc failed: {exc!r}")

    stats["cutoff"] = cutoff.isoformat()
    stats["retention_days"] = days
    print(
        f"run_ocr_upload_gc: originals={stats['originals']} "
        f"thumbs={stats['thumbs']} ocr_rows={stats['ocr_rows']} "
        f"events={stats['events']} batches={stats['batches']} "
        f"retention={days}d cutoff={stats['cutoff']}"
    )
    return stats


__all__ = [
    "run_ocr_upload_gc",
    "retention_days",
    "batch_size",
    "max_batches",
    "retention_cutoff",
    "DEFAULT_OCR_UPLOAD_RETENTION_DAYS",
]
