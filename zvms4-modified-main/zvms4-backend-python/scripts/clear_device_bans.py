#!/usr/bin/env python3
"""一次性清理「显式设备封禁」存量行的运维脚本。

为什么需要它：早期规则（临时密码连错）把一批设备写进了 ``BANNED_DEVICES``，
其中可能包含管理员主学海设备与误伤的平板。本脚本把这些**显式行**清掉，
同时清空 ``DEVICE_BAN_COUNTERS`` 的梯度计数——只删封禁行会留下「解封了但
下一次再违规直接跳高档位」的隐藏状态。

**不在本脚本范围**：账号推导型受限（``USERS.PRIMARY_XUEHAI_ID`` = 本机 且
账号 ``banned=1``）不是 ``BANNED_DEVICES`` 的行，只能靠解封账号或清空该账号
的主设备来解除；本脚本不会写 ``USERS``，因此这类受限清完后仍然存在。

安全约束：

* 默认 dry-run，只打印统计与最多 ``PREVIEW_LIMIT`` 条样例，不写任何东西；
* ``--apply`` 必须同时给出 ``--before YYYY-MM-DD-HHMMSS``：这是执行授权戳
  （会被写进备份文件），不是行过滤条件——本脚本按契约清的是**全表**；
* 删前先把两张表的全部行导出成 JSON 备份，写盘失败立即中止，绝不带着
  「没有备份的删除」继续；
* 会话统一走 ``db.oracle.session.session_scope``，与运行时同源配置。

用法::

    # 预演（默认，不写库）
    .venv/bin/python scripts/clear_device_bans.py
    # 真正执行（--before 是授权时间戳，格式 YYYY-MM-DD-HHMMSS）
    .venv/bin/python scripts/clear_device_bans.py --apply --before 2026-10-29-120000
    # 指定备份目录
    .venv/bin/python scripts/clear_device_bans.py --apply \
        --before 2026-10-29-120000 --backup-dir /data/backups
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

# 允许以 `python scripts/xxx.py` 直接运行：脚本目录是 sys.path[0]，仓库根不在
# 里面，`import db` 会失败。先补上仓库根，再导入项目模块。
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import delete, func, or_, select, text  # noqa: E402

from db.models import banned_devices as banned_tbl  # noqa: E402
from db.models import device_ban_counters as counters_tbl  # noqa: E402
from db.oracle.session import session_scope  # noqa: E402

PREVIEW_LIMIT = 20
DEFAULT_BACKUP_DIR = Path("/tmp/opencode/backups")
BANNED_TABLE = "BANNED_DEVICES"
COUNTERS_TABLE = "DEVICE_BAN_COUNTERS"
# ``--before`` 的契约格式：沿用既有脚本「日期-时分秒」的可复述口径。
BEFORE_FORMAT = "%Y-%m-%d-%H%M%S"


def _parse_before(raw: str) -> datetime:
    try:
        return datetime.strptime(raw, BEFORE_FORMAT)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--before 必须是 YYYY-MM-DD-HHMMSS（收到 {raw!r}）"
        ) from exc


def _active_filter(now: datetime):
    """「此刻仍生效」：``until`` 为空=永久，或尚未到期。"""
    return or_(
        banned_tbl.c.until.is_(None),
        banned_tbl.c.until > now,
    )


def _preview_stmt(now: datetime):
    return (
        select(
            banned_tbl.c.xuehai_id,
            banned_tbl.c.source,
            banned_tbl.c.rule_code,
            banned_tbl.c.until,
            banned_tbl.c.created_at,
        )
        .order_by(banned_tbl.c.created_at.desc(), banned_tbl.c.xuehai_id.asc())
        .limit(PREVIEW_LIMIT)
    )


def _export_bans_stmt():
    return select(*banned_tbl.c).order_by(banned_tbl.c.xuehai_id.asc())


def _export_counters_stmt():
    return select(*counters_tbl.c).order_by(counters_tbl.c.xuehai_id.asc())


def _table_exists_stmt(name: str):
    """按 Oracle 数据字典判表存在，避免直接查询时抛 ORA-00942。

    与 ``scripts/purge_legacy_device_bindings.py`` 同一手法：生产 schema
    可能与仓库 DDL 漂移，缺表时给出可读提示而不是堆栈。
    """
    return text(
        "SELECT COUNT(*) FROM user_tables WHERE table_name = :name"
    ).bindparams(name=name)


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _backup_path(backup_dir: Path, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return backup_dir / f"clear_device_bans_{stamp}.json"


def _write_backup(
    path: Path,
    authorization: datetime,
    bans: list,
    counters: list,
) -> None:
    payload = {
        "tables": [BANNED_TABLE, COUNTERS_TABLE],
        "delete_all": True,
        "authorized_before": authorization.isoformat(sep=" ", timespec="seconds"),
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "banned_devices": {
            "row_count": len(bans),
            "rows": [dict(row._mapping) for row in bans],
        },
        "device_ban_counters": {
            "row_count": len(counters),
            "rows": [dict(row._mapping) for row in counters],
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )


async def _table_exists(session, name: str) -> bool:
    return bool(int((await session.execute(_table_exists_stmt(name))).scalar_one()))


async def _run(
    apply: bool,
    before: datetime | None = None,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
) -> int:
    async with session_scope() as session:
        for name in (BANNED_TABLE, COUNTERS_TABLE):
            if not await _table_exists(session, name):
                print(f"{name} 不在当前 schema 里，没有可清理的数据（未做任何写）。")
                return 0

        now = datetime.now()
        total_bans = int(
            (await session.execute(
                select(func.count()).select_from(banned_tbl)
            )).scalar_one()
        )
        active_bans = int(
            (await session.execute(
                select(func.count()).select_from(banned_tbl).where(_active_filter(now))
            )).scalar_one()
        )
        expired_bans = total_bans - active_bans
        total_counters = int(
            (await session.execute(
                select(func.count()).select_from(counters_tbl)
            )).scalar_one()
        )
        preview = (await session.execute(_preview_stmt(now))).all()

        print(f"{BANNED_TABLE} 总行数：{total_bans}")
        print(f"  生效中：{active_bans}")
        print(f"  已过期：{expired_bans}")
        print(f"{COUNTERS_TABLE} 行数：{total_counters}")
        if not apply:
            print("清理范围说明：账号推导型受限不在本脚本范围"
                  "（只能靠解封账号或清主设备）。")
        if preview:
            print(f"样例（最多 {PREVIEW_LIMIT} 条）:")
            for row in preview:
                m = row._mapping
                print(
                    "  xuehai_id={xuehai} source={source} rule_code={rule} "
                    "until={until}".format(
                        xuehai=m["xuehai_id"],
                        source=m["source"],
                        rule=m["rule_code"] or "-",
                        until=m["until"] or "永久",
                    )
                )

        if not apply:
            print("dry-run：未做任何删除。加 --apply --before <戳> 才会执行。")
            return 0

        # 双保险：main() 已拦一次，_run 被直接调用时同样拒绝无授权戳的执行。
        if before is None:
            print("拒绝执行：--apply 必须同时提供 --before <YYYY-MM-DD-HHMMSS>。")
            return 2

        if total_bans == 0 and total_counters == 0:
            print("没有可删除的行。")
            return 0

        backup_bans = (await session.execute(_export_bans_stmt())).all()
        backup_counters = (await session.execute(_export_counters_stmt())).all()
        path = _backup_path(backup_dir)
        print(f"备份待删行到：{path}")
        try:
            _write_backup(path, before, backup_bans, backup_counters)
        except Exception as exc:  # noqa: BLE001 - 任何写盘失败都必须中止删除
            print(f"备份失败，已中止删除（未执行 DELETE）：{exc}", file=sys.stderr)
            return 1

        removed_bans = await session.execute(delete(banned_tbl))
        removed_counters = await session.execute(delete(counters_tbl))
        await session.commit()
        print(
            f"已删除 {BANNED_TABLE} {int(removed_bans.rowcount)} 行、"
            f"{COUNTERS_TABLE} {int(removed_counters.rowcount)} 行。"
            f"备份：{path}"
        )
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "清空 BANNED_DEVICES 显式设备封禁与 DEVICE_BAN_COUNTERS 梯度计数"
            "（默认 dry-run；账号推导型受限不在范围内）。"
        ),
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正执行 DELETE；不加则只打印统计与样例。",
    )
    parser.add_argument(
        "--before",
        type=_parse_before,
        default=None,
        metavar="YYYY-MM-DD-HHMMSS",
        help="执行授权时间戳（写入备份文件）；--apply 时必填。",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=DEFAULT_BACKUP_DIR,
        help=f"--apply 前的 JSON 备份目录（默认 {DEFAULT_BACKUP_DIR}）。",
    )
    args = parser.parse_args(argv)
    if args.apply and args.before is None:
        parser.error(
            "--apply 必须同时提供 --before <YYYY-MM-DD-HHMMSS>："
            "无授权戳的全表清空容易被误触发。"
        )
    return asyncio.run(_run(args.apply, args.before, args.backup_dir))


if __name__ == "__main__":
    raise SystemExit(main())
