#!/usr/bin/env python3
"""一次性清理 ``USER_DEVICE_BINDINGS`` 的运维脚本（V014 上线配套）。

为什么需要它：``USER_DEVICE_BINDINGS`` 是早期「学海 ID 身份追溯 MVP」的观测表，
记录的是**历史上登录过的每一台设备**。V014 起「主学海设备」由
``USERS.PRIMARY_XUEHAI_ID`` 单列表达，这张表的存量行只会让管理员以为账号还绑着
一堆设备，所以上线后按需清空一次。

为什么不放进迁移：迁移会在每个环境/每次重建时自动执行，而删数据是**不可逆**的
决定。放进迁移意味着一次误重跑就抹掉审计线索；放进脚本则要求人显式
``--apply``。

默认 dry-run：只打印总行数 + 前 10 行（标识符做掩码），不写任何东西。
``--apply`` 才真正 ``DELETE``。

用法::

    .venv/bin/python scripts/purge_legacy_device_bindings.py          # 预演
    .venv/bin/python scripts/purge_legacy_device_bindings.py --apply  # 执行

连接方式与运行时完全一致：``db.oracle.session.session_scope``（配置来自
``settings``），不自己拼 DSN、不读环境变量。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# 允许以 `python scripts/xxx.py` 直接运行：脚本目录是 sys.path[0]，仓库根不在
# 里面，`import db` 会失败。先补上仓库根，再导入项目模块。
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import delete, func, select  # noqa: E402

from db.models import user_device_bindings as bindings_tbl  # noqa: E402
from db.oracle.session import session_scope  # noqa: E402

PREVIEW_LIMIT = 10


def _mask(value: object) -> str:
    """只留末 4 位：屏幕上足够区分，又不把设备号/账号 id 落到终端历史里。"""
    text = "" if value is None else str(value)
    if len(text) <= 4:
        return "*" * len(text)
    return "*" * (len(text) - 4) + text[-4:]


def _preview_stmt():
    return (
        select(
            bindings_tbl.c.oid,
            bindings_tbl.c.user_oid,
            bindings_tbl.c.xuehai_id,
            bindings_tbl.c.status,
            bindings_tbl.c.created_at,
        )
        .order_by(bindings_tbl.c.created_at.asc(), bindings_tbl.c.oid.asc())
        .limit(PREVIEW_LIMIT)
    )


def _count_stmt():
    return select(func.count()).select_from(bindings_tbl)


def _delete_stmt():
    return delete(bindings_tbl)


async def _run(apply: bool) -> int:
    async with session_scope() as session:
        total = int((await session.execute(_count_stmt())).scalar_one())
        rows = (await session.execute(_preview_stmt())).all()

        print(f"user_device_bindings: {total} 行")
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

        if total == 0:
            print("没有可删除的行。")
            return 0

        result = await session.execute(_delete_stmt())
        await session.commit()
        print(f"已删除 {int(result.rowcount)} 行。")
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="清理 USER_DEVICE_BINDINGS 的存量观测记录（默认 dry-run）。",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正执行 DELETE；不加则只打印行数与预览。",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
