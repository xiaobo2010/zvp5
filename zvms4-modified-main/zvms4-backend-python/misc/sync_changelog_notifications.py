"""为历史 CHANGELOG 补齐 SYSTEM 版本公告（V038 上线/补发工具）。

应用启动时**不会**自动回填：公告列表是用户可见的推送，批量创建属于运维动作，
必须由人决定「哪些历史版本现在弹出来」。本工具就是那个决定入口。

用法（在服务器上的后端根目录）::

    # 1) 上线第一步：把历史版本都建成公告，但全部 inactive（不打扰用户）
    .venv/bin/python misc/sync_changelog_notifications.py

    # 2) 只读预览：连库读 CHANGELOG 与已有公告，不写任何东西
    .venv/bin/python misc/sync_changelog_notifications.py --dry-run

    # 3) 首发版本：回填的同时把 v4.3.0-6 那条公告置为 active
    .venv/bin/python misc/sync_changelog_notifications.py --activate v4.3.0-6

``--activate`` 的取值按**字符串精确匹配** ``CHANGELOG.VERSION``（``v4.3.0-6`` 与
``4.3.0-6`` 是两条记录，写错只会得到一条都不激活的 inactive 回填）。新建的公告
在回填阶段一律先建后停用，所以重复执行不会产生重复公告，也不会把已 active 的
历史版本重新弹出来。

退出码：0 = 成功（含「无事可做」）；1 = 读 CHANGELOG 失败（此时没有任何写入）。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 以 ``python misc/sync_changelog_notifications.py`` 运行时 ``sys.path[0]`` 是
# ``misc/``，``db`` / ``services`` / ``settings`` 都不可见；把后端根放进来，
# 脚本才能复用运行时的仓储与会话工厂（与 misc/apply_ddl.py 同一手法）。
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def _fmt(stats: dict[str, int]) -> str:
    return (
        f"created={stats['created']} activated={stats['activated']} "
        f"skipped={stats['skipped']}"
    )


async def _preview(activate_version: str | None) -> dict[str, int]:
    """只读预演：统计「会创建 / 会启用 / 会跳过」的条数，不写库。"""
    from db import backends

    stats = {"created": 0, "activated": 0, "skipped": 0}
    target = "" if activate_version is None else str(activate_version).strip()
    entries = await backends.get_changelog_repo().list_all()
    repo = backends.get_notifications_repo()
    for entry in entries:
        oid = entry.get("_id") or entry.get("id")
        want_active = bool(target) and str(entry.get("version", "")).strip() == target
        existing = (
            await repo.get_by_source_ref(str(oid).strip()) if oid else None
        )
        if not oid:
            stats["skipped"] += 1
        elif existing is None:
            stats["created"] += 1
            if want_active:
                stats["activated"] += 1
        elif want_active and not existing["active"]:
            stats["activated"] += 1
        else:
            stats["skipped"] += 1
    return stats


async def _run(activate_version: str | None, dry_run: bool) -> int:
    from services import changelog_notifications

    if dry_run:
        try:
            stats = await _preview(activate_version)
        except Exception as exc:  # noqa: BLE001 - 只读预演失败直接报出原因
            print(f"FAILED 读取 CHANGELOG：{type(exc).__name__}: {exc}")
            return 1
        print(f"changelog notifications (dry-run): {_fmt(stats)}")
        return 0

    stats = await changelog_notifications.backfill_changelog_notifications(
        activate_version
    )
    print(f"changelog notifications: {_fmt(stats)}")
    # 服务层吞异常是刻意的（不能拖垮写路由），但 CLI 需要能区分「真的成功」与
    # 「一条都没读到」：CHANGELOG 为空时也打印同样一行，这里额外说明。
    if stats == {"created": 0, "activated": 0, "skipped": 0}:
        print("  (CHANGELOG 为空或读取失败，详见 warning 日志)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--activate",
        default=None,
        metavar="VERSION",
        help="同一次运行里把该版本（精确匹配 CHANGELOG.VERSION）的公告置为 active",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只读预演：连库统计，不写任何数据",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.activate, args.dry_run))


if __name__ == "__main__":
    sys.exit(main())
