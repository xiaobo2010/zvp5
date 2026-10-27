"""CHANGELOG → SYSTEM 版本公告的同步服务（V038）。

**为什么公告由后端生成，而不是让管理员手抄一遍**：一条 CHANGELOG 行就是一次
发版的事实，公告只是它的另一种排版。手抄会漂移（改了 CHANGELOG 忘了改公告），
而客户端「已读」是按通知 oid 记的，两边各写一条就变成两条互不相干的未读。

**为什么用 ``SOURCE_REF`` 而不是版本号做关联**：版本号是 ``VARCHAR2(32)``、没有
唯一约束，管理员可以把它改成任何字符串（甚至与历史行重复）；日期同理。只有
``CHANGELOG.OID`` 在整条记录的生命周期里不变，所以它才是幂等重跑与
``get_by_source_ref`` 的唯一稳定键。

**为什么编辑 CHANGELOG 不改 ``active``**：公告的显隐是管理员的独立决定（停用
一条公告是「这条别再弹了」，不是「这条 CHANGELOG 不存在了」）。编辑只覆盖标题
与正文，``version`` 由仓储自增，客户端据此把这条重新标记为未读。停用/启用走
``active``，删除 CHANGELOG 只把公告置为 inactive，不硬删——发布过是既成事实。

**为什么所有对外函数都吞异常**：调用点在 CHANGELOG 的写路由上。公告同步失败
只能少一条通知，绝不能把「管理员保存版本记录」这个主流程打成 500，也不能让
``GET /api/version`` 依赖的基础表写入失败。失败一律落 ``warning`` 日志（带
``exc_info``），事后可追。

**为什么截断按条目边界**：公告正文是给人看的 Markdown 列表。按字符硬切会切出
半个汉字、半条版本说明；停在最后一条完整的 ``- item`` 之前再补一行 ``- ……``，
读者能看出「还有更多，去 CHANGELOG 看」，而不会看到一条断句。
"""

from __future__ import annotations

import logging
from typing import Any

from db import backends

_logger = logging.getLogger(__name__)

#: 系统发送者：24 位小写 hex 全零。``NOTIFICATIONS.AUTHOR_OID`` 是
#: ``VARCHAR2(24)`` + 无外键，管理员不会拥有这个 oid（``new_oid`` 前 4 字节是
#: 秒级时间戳，2020 年以后必然非零），所以不会与真实账号混淆。
SYSTEM_AUTHOR_OID = "000000000000000000000000"
SYSTEM_AUTHOR_NAME = "SYSTEM"

#: ``NOTIFICATIONS.TITLE`` 的列宽（200 CHAR）。
TITLE_MAX = 200
#: ``NOTIFICATIONS.CONTENT`` 是 ``VARCHAR2(4000 CHAR)``，但 Oracle 同时限制
#: 4000 **字节**——中文按 UTF-8 算 3 字节/字，只按字符数校验会撞 ORA-12899。
CONTENT_MAX_CHARS = 4000
CONTENT_MAX_BYTES = 4000

#: 截断标记：与条目行同形（``- `` 开头），保证 Markdown 列表不被破坏。
TRUNCATED_MARKER = "- ……"


def _clip(text: str, max_chars: int, max_bytes: int) -> str:
    """按字符上限截断，同时保证 UTF-8 字节不超限（不切断半个字符）。"""
    if len(text) <= max_chars and len(text.encode("utf-8")) <= max_bytes:
        return text
    clipped = text[:max_chars]
    while clipped and len(clipped.encode("utf-8")) > max_bytes:
        clipped = clipped[:-1]
    return clipped


def _entry_oid(entry: dict) -> str:
    """读 CHANGELOG 行的 oid（仓储行用 ``_id``，HTTP 序列化用 ``id``）。"""
    raw = entry.get("_id") or entry.get("id")
    return str(raw).strip() if raw else ""


def _entry_version(entry: dict) -> str:
    value = entry.get("version")
    return "" if value is None else str(value).strip()


def _too_long(text: str) -> bool:
    return (
        len(text) > CONTENT_MAX_CHARS
        or len(text.encode("utf-8")) > CONTENT_MAX_BYTES
    )


def build_notification_fields(entry: dict[str, Any]) -> tuple[str, str]:
    """把一条 CHANGELOG 行渲染成 ``(公告标题, 公告正文)``。

    标题：``{version} 更新公告``（超过 200 字符按字符截断；version 列宽 32，实际
    不会触发）。正文：``## {version}（{date}）`` + 空行 + 每条 ``items`` 一行的
    ``- item`` 列表。

    正文按**条目边界**截断：遇到第一条会超出 4000 字符或 4000 UTF-8 字节的条目
    就停在它之前，并补一行 ``- ……``。宁缺一条也不留半条。
    """
    version = _entry_version(entry)
    date = "" if entry.get("date") is None else str(entry.get("date")).strip()
    raw_items = entry.get("items") or []
    items = [str(item).strip() for item in raw_items if str(item).strip()]

    title = f"{version} 更新公告" if version else "更新公告"
    title = title[:TITLE_MAX]

    if version and date:
        header = f"## {version}（{date}）"
    elif version:
        header = f"## {version}"
    elif date:
        header = f"## {date}"
    else:
        header = "## 更新"

    lines = [f"- {item}" for item in items]
    if not lines:
        return title, _clip(header, CONTENT_MAX_CHARS, CONTENT_MAX_BYTES)

    kept: list[str] = []
    for line in lines:
        candidate = header + "\n\n" + "\n".join([*kept, line])
        if _too_long(candidate):
            break
        kept.append(line)

    if len(kept) < len(lines):
        # 标记本身也占额度：从尾部回退，直到「最后一条 + 标记」放得下。
        while kept and _too_long(
            header + "\n\n" + "\n".join([*kept, TRUNCATED_MARKER])
        ):
            kept.pop()
        body = "\n".join([*kept, TRUNCATED_MARKER])
    else:
        body = "\n".join(kept)

    return title, _clip(
        header + "\n\n" + body, CONTENT_MAX_CHARS, CONTENT_MAX_BYTES
    )


async def sync_changelog_notification(entry: dict[str, Any]) -> dict[str, Any] | None:
    """把一条 CHANGELOG 行同步成 SYSTEM 公告；返回公告行（失败返回 ``None``）。

    幂等：先按 ``source_ref = entry oid`` 查。没有就创建（active，发布人
    SYSTEM），有就只更新标题/正文——仓储会自增单条 ``version`` 并 bump 全局
    版本号，客户端因此把这条重新视为未读。

    永不抛出：调用点在 CHANGELOG 写路由上，公告失败不能影响主流程。
    """
    oid = ""
    try:
        oid = _entry_oid(entry)
        if not oid:
            _logger.warning("CHANGELOG 条目缺少 oid，跳过版本公告同步")
            return None
        title, content = build_notification_fields(entry)
        repo = backends.get_notifications_repo()
        existing = await repo.get_by_source_ref(oid)
        if existing is None:
            row, _version = await repo.create(
                title=title,
                content=content,
                author_oid=SYSTEM_AUTHOR_OID,
                author_name=SYSTEM_AUTHOR_NAME,
                source_ref=oid,
            )
            return row
        row, _version = await repo.update(
            existing["_id"], title=title, content=content
        )
        return row
    except Exception:  # noqa: BLE001 - 公告是旁路，任何失败都只能降级
        _logger.warning(
            "同步 CHANGELOG %s 的版本公告失败", oid or "?", exc_info=True
        )
        return None


async def deactivate_changelog_notification(changelog_oid: str) -> bool:
    """把某条 CHANGELOG 对应的公告置为 ``active = 0``。

    返回是否真的命中并停用（没有对应公告时 ``False``，不算错误：CHANGELOG 可能
    是在本功能上线前建的）。不硬删公告：发布过是事实，客户端本地「已读」也按
    通知 oid 记，硬删会让同一 oid 复活时状态错乱。
    """
    oid = ""
    try:
        oid = str(changelog_oid or "").strip()
        if not oid:
            return False
        repo = backends.get_notifications_repo()
        existing = await repo.get_by_source_ref(oid)
        if existing is None:
            return False
        row, _version = await repo.update(existing["_id"], active=False)
        return row is not None
    except Exception:  # noqa: BLE001 - 同上，删除 CHANGELOG 不能被公告拖垮
        _logger.warning(
            "停用 CHANGELOG %s 的版本公告失败", oid or "?", exc_info=True
        )
        return False


async def backfill_changelog_notifications(
    activate_version: str | None = None,
) -> dict[str, int]:
    """为历史 CHANGELOG 补齐公告；返回 ``{created, activated, skipped}``。

    默认全部建为 **inactive**：历史版本一次性全弹出来只会把通知列表刷满，用户
    看到的应该是「从现在起的新版本」。``activate_version`` 用于首次上线/补发：
    只有该版本（字符串精确比较，``v4.3.0-6`` 与 ``4.3.0-6`` 是两条记录）对应的
    公告是 active。

    幂等：已有的条目不再创建、不再改内容；只有「目标版本但当前不是 active」才
    多一次启用。整体失败（列表读不出来）落 warning 并返回已完成的统计；单条
    失败跳过该条，不影响其余条目。
    """
    stats = {"created": 0, "activated": 0, "skipped": 0}
    target = "" if activate_version is None else str(activate_version).strip()
    try:
        entries = await backends.get_changelog_repo().list_all()
    except Exception:  # noqa: BLE001 - 回填是运维旁路，失败只记日志
        _logger.warning("读取 CHANGELOG 失败，版本公告回填中止", exc_info=True)
        return stats

    repo = backends.get_notifications_repo()
    for entry in entries:
        oid = _entry_oid(entry)
        if not oid:
            stats["skipped"] += 1
            continue
        want_active = bool(target) and _entry_version(entry) == target
        try:
            title, content = build_notification_fields(entry)
            existing = await repo.get_by_source_ref(oid)
            if existing is None:
                row, _version = await repo.create(
                    title=title,
                    content=content,
                    author_oid=SYSTEM_AUTHOR_OID,
                    author_name=SYSTEM_AUTHOR_NAME,
                    source_ref=oid,
                )
                stats["created"] += 1
                if want_active:
                    stats["activated"] += 1
                elif row is not None:
                    # create 恒为 active（手写通知的默认值），历史条目立即收起来。
                    await repo.update(row["_id"], active=False)
                continue
            if want_active and not existing["active"]:
                await repo.update(existing["_id"], active=True)
                stats["activated"] += 1
            else:
                stats["skipped"] += 1
        except Exception:  # noqa: BLE001 - 一条坏数据不该中断整轮回填
            _logger.warning(
                "回填 CHANGELOG %s 的版本公告失败", oid, exc_info=True
            )
            stats["skipped"] += 1
    return stats


__all__ = [
    "SYSTEM_AUTHOR_OID",
    "SYSTEM_AUTHOR_NAME",
    "TITLE_MAX",
    "CONTENT_MAX_CHARS",
    "CONTENT_MAX_BYTES",
    "TRUNCATED_MARKER",
    "build_notification_fields",
    "sync_changelog_notification",
    "deactivate_changelog_notification",
    "backfill_changelog_notifications",
]
