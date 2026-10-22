#!/usr/bin/env python3
"""一次性清理 ``USER_DEVICE_BINDINGS`` 存量行的运维脚本（V014 上线配套）。

为什么需要它：``USER_DEVICE_BINDINGS`` 是早期「学海 ID 身份追溯 MVP」的观测表，
记录的是**历史上登录过的每一台设备**。V014 起「主学海设备」由
``USERS.PRIMARY_XUEHAI_ID`` 单列表达，这张表的旧行容易让管理员误以为账号还绑着
一堆设备，所以按需清一次。

**这不再是「全表清空」脚本**（2026-10 修订）：表仍在被活代码读写——
``routers/users_router.py`` 登录时自动 upsert、``routers/users_v3_router.py``
管理员手工 upsert/revoke、管理端列表直接读。全表 DELETE 会连「绑定不符的证据」
和「管理员手工 revoke 的记录」一起抹掉。因此删除必须带明确的时间下界
（``--before``），只清某一天之前的老数据。

**主设备真相只在** ``USERS.PRIMARY_XUEHAI_ID``：这张表永远不是「用户现在绑着
哪台设备」的答案，清掉的只是历史观测行；不要指望删完能顺带改变登录校验。

为什么不放进迁移：迁移会在每个环境/每次重建时自动执行，而删数据是**不可逆**的
决定。放进迁移意味着一次误重跑就抹掉审计线索；放进脚本则要求人显式
``--apply``，且 ``--apply`` 必须同时给出 ``--before``。

默认 dry-run：只打印待删行数 + 前 10 行（标识符做掩码），不写任何东西。
``--apply`` 才真正 ``DELETE``，且删前先按**同一谓词**把待删行全列导出成 JSON
备份；导出失败即中止，绝不带着「没有备份的删除」继续。

用法::

    # 预演某天之前的数据（不删）
    .venv/bin/python scripts/purge_legacy_device_bindings.py --before 2026-09-01
    # 真正删除（必须先确认备份文件已生成）
    .venv/bin/python scripts/purge_legacy_device_bindings.py --before 2026-09-01 --apply
    # 指定备份目录
    .venv/bin/python scripts/purge_legacy_device_bindings.py --before 2026-09-01 \
        --apply --backup-dir /data/backups

连接方式与运行时完全一致：``db.oracle.session.session_scope``（配置来自
``settings``），不自己拼 DSN、不读环境变量。
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

from sqlalchemy import delete, func, select, text  # noqa: E402

from db.models import user_device_bindings as bindings_tbl  # noqa: E402
from db.oracle.session import session_scope  # noqa: E402

PREVIEW_LIMIT = 10
DEFAULT_BACKUP_DIR = Path("/tmp/opencode/backups")
TABLE_NAME = "USER_DEVICE_BINDINGS"


def _mask(value: object) -> str:
    """只留末 4 位：屏幕上足够区分，又不把设备号/账号 id 落到终端历史里。"""
    text = "" if value is None else str(value)
    if len(text) <= 4:
        return "*" * len(text)
    return "*" * (len(text) - 4) + text[-4:]


def _parse_before(text: str) -> datetime:
    """``YYYY-MM-DD`` → 当天 ``00:00:00``（naive，与库里的 ``TIMESTAMP`` 同口径）。

    用「某天的零点」而不是「此刻减 N 天」：运维口径要可复述、可复核，
    ``created_at < 2026-09-01 00:00:00`` 读出来就是一个明确边界。
    """
    try:
        day = date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--before 必须是 YYYY-MM-DD（收到 {text!r}）"
        ) from exc
    return datetime.combine(day, datetime.min.time())


def _predicate(before: datetime | None) -> list:
    """count / preview / delete / 备份导出**共用**的唯一删除谓词。"""
    if before is None:
        return []
    return [bindings_tbl.c.created_at < before]


def _preview_stmt(where: list):
    return (
        select(
            bindings_tbl.c.oid,
            bindings_tbl.c.user_oid,
            bindings_tbl.c.xuehai_id,
            bindings_tbl.c.status,
            bindings_tbl.c.created_at,
        )
        .where(*where)
        .order_by(bindings_tbl.c.created_at.asc(), bindings_tbl.c.oid.asc())
        .limit(PREVIEW_LIMIT)
    )


def _count_stmt(where: list):
    return select(func.count()).select_from(bindings_tbl).where(*where)


def _export_stmt(where: list):
    """待删行的**全列**快照，顺序固定，便于和备份文件逐行对照。"""
    return (
        select(*bindings_tbl.c)
        .where(*where)
        .order_by(bindings_tbl.c.created_at.asc(), bindings_tbl.c.oid.asc())
    )


def _delete_stmt(where: list):
    return delete(bindings_tbl).where(*where)


def _table_exists_stmt():
    """按 Oracle 数据字典判表存在，避免直接查询时抛 ORA-00942。

    与 ``db/repos/oracle/users.py::_existing_tables`` 同一手法：生产 schema
    可能与仓库 DDL 漂移（实测缺过 ``user_device_bindings``）。
    """
    return text(
        "SELECT COUNT(*) FROM user_tables WHERE table_name = :name"
    ).bindparams(name=TABLE_NAME)


def _json_default(value: object) -> object:
    """``TIMESTAMP`` / ``Decimal`` 等列值转成 JSON 可表达的类型。"""
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _backup_path(backup_dir: Path, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return backup_dir / f"purge_device_bindings_{stamp}.json"


def _write_backup(path: Path, where: list, before: datetime | None, rows: list) -> None:
    """把待删行全列写进 JSON；抛异常由调用方处理（失败必须中止删除）。"""
    payload = {
        "table": TABLE_NAME,
        "predicate": "created_at < %s" % before.isoformat() if before else "ALL",
        "before": before.isoformat() if before else None,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "row_count": len(rows),
        "rows": [dict(row._mapping) for row in rows],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )


async def _table_exists(session) -> bool:
    return bool(int((await session.execute(_table_exists_stmt())).scalar_one()))


async def _run(
    apply: bool,
    before: datetime | None = None,
    backup_dir: Path = DEFAULT_BACKUP_DIR,
) -> int:
    async with session_scope() as session:
        if not await _table_exists(session):
            print(f"{TABLE_NAME} 不在当前 schema 里，没有可清理的数据（未做任何写）。")
            return 0

        where = _predicate(before)
        total = int((await session.execute(_count_stmt(where))).scalar_one())
        rows = (await session.execute(_preview_stmt(where))).all()

        if before is None:
            print("未提供 --before，仅预览全表（--apply 时必须给出时间下界）。")
        else:
            print(f"删除条件：created_at < {before:%Y-%m-%d %H:%M:%S}")

        print(f"{TABLE_NAME}: 待删除 {total} 行")
        if total:
            print(f"前 {min(total, PREVIEW_LIMIT)} 行（标识符已掩码）:")
            for row in rows:
                m = row._mapping
                print(
                    "  oid={oid} user={user} xuehai={xuehai} "
                    "status={status} created_at={created}".format(
                        oid=_mask(m["oid"]),
                        user=_mask(m["user_oid"]),
                        xuehai=_mask(m["xuehai_id"]),
                        status=m["status"],
                        created=m["created_at"],
                    )
                )

        if not apply:
            print("dry-run：未做任何删除。加 --apply 才会 DELETE。")
            return 0

        # 双保险：main() 已拦过一次，_run 被直接调用时同样不允许无下界删除。
        if before is None:
            print("拒绝执行：--apply 必须同时提供 --before <YYYY-MM-DD>。")
            return 2

        if total == 0:
            print("没有可删除的行。")
            return 0

        # 备份用同一个谓词、同一批行：先落盘再删，导出失败就不删。
        backup_rows = (await session.execute(_export_stmt(where))).all()
        path = _backup_path(backup_dir)
        print(f"备份待删行到：{path}")
        try:
            _write_backup(path, where, before, backup_rows)
        except Exception as exc:  # noqa: BLE001 - 任何写盘失败都必须中止删除
            print(f"备份失败，已中止删除（未执行 DELETE）：{exc}", file=sys.stderr)
            return 1

        result = await session.execute(_delete_stmt(where))
        await session.commit()
        print(f"已删除 {int(result.rowcount)} 行。备份：{path}")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="清理 USER_DEVICE_BINDINGS 的存量观测记录（默认 dry-run）。",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正执行 DELETE；不加则只打印待删行数与预览。",
    )
    parser.add_argument(
        "--before",
        type=_parse_before,
        default=None,
        metavar="YYYY-MM-DD",
        help="只删除 created_at 早于该日 00:00:00 的行；--apply 时必填。",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        default=DEFAULT_BACKUP_DIR,
        help=f"--apply 前的 JSON 备份目录（默认 {DEFAULT_BACKUP_DIR}）。",
    )
    args = parser.parse_args(argv)
    if args.apply and args.before is None:
        # 没有时间下界的删除等于「全表清空」，这张表还在被活代码写入，拒绝。
        parser.error(
            "--apply 必须同时提供 --before <YYYY-MM-DD>："
            "本脚本只清某天之前的历史行，不再支持全表清空。"
        )
    return asyncio.run(_run(args.apply, args.before, args.backup_dir))


if __name__ == "__main__":
    raise SystemExit(main())
