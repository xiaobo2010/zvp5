"""One-off, idempotent seed for ``zvms_new.changelog``.

Inserts the initial release-note entries below into MongoDB. For each entry the
insert happens **only when its ``version`` is not already present**, so re-running
is safe and never duplicates a row or overwrites an admin's edits.

Usage:
    python misc/seed_changelog.py [--dry-run]
"""

import asyncio
import os
import sys
from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import settings

DRY_RUN = "--dry-run" in sys.argv

ENTRIES = [
    {
        "version": "v4.2.1-1",
        "date": "2026-10-12",
        "items": [
            "「更新日志」与「关于」页面无需登录即可查看，并新增返回按钮",
            "新版本发布后应用自动更新，无需重装或手动刷新",
            "网络不稳定时，常用页面数据自动改用本地缓存显示",
            "个人主页与成员详情的身份标识升级为彩色图标：管理员（红色）、团支书、学生会，与群组管理页一致",
        ],
    },
    {
        "version": "v4.2.0-3",
        "date": "2026-10-12",
        "items": [
            "后端安全加固与稳定性修复：异常请求直接拦截，运行缺口补齐",
            "「身份」栏不再空白：学生用户显示班级徽标或「学生」标签；团支书/学生会/管理员角色的用户同时显示角色标签",
            "修复用户详情显示「班级不存在」的问题：用户与班级信息读取改为新旧数据双库兼容解析，此前无法查看的约 785 个新用户也恢复正常",
            "修复添加义工成员、选择用户时，高一2026级等新用户被误报「无此用户」的问题",
            "管理页「群组」补全：团支书、学生会、管理员权限组恢复显示，并可查看成员名单",
            "系统日志中未登录的请求现在显示「未登录」，不再误标为「无此用户」",
            "删除义工活动后自动返回活动列表",
            "修复部分学海平板上旧版应用无法使用、报 404 的问题：新版将自动替换旧缓存",
        ],
    },
    {
        "version": "v4.2.0-2",
        "date": "2026-10-11",
        "items": [
            "平台迁移至全新部署，全部用户数据完整保留",
            "平台图标全套更新",
            "修复：导出用户列表时，无班级用户导致导出失败的问题",
            "修复：删除用户按钮不可用、班级分组类型过滤、自助修改密码返回提示等问题",
            "前端构建类型错误清零，页面更稳定",
        ],
    },
]


async def seed(target) -> int:
    inserted = 0
    for entry in ENTRIES:
        existing = await target.changelog.find_one({"version": entry["version"]})
        if existing is not None:
            print(f"  skip {entry['version']} (already present)")
            continue
        if DRY_RUN:
            print(f"  [dry-run] insert {entry['version']} ({len(entry['items'])} items)")
        else:
            await target.changelog.insert_one(
                {
                    "version": entry["version"],
                    "date": entry["date"],
                    "items": list(entry["items"]),
                    "updated_at": datetime.now(),
                }
            )
        inserted += 1
    return inserted


async def run():
    client = AsyncIOMotorClient(settings.MONGODB_URI, uuidRepresentation="standard")
    target = client["zvms_new"]
    try:
        print(f"seed zvms_new.changelog{' (DRY RUN)' if DRY_RUN else ''}")
        inserted = await seed(target)
        print("summary:")
        print(f"  entries inserted: {inserted}")
        print(f"  zvms_new.changelog total: {await target.changelog.count_documents({})}")
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(run())
