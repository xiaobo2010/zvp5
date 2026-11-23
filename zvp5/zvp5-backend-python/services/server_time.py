"""服务器权威时间。

一份实现同时喂给 ZVAgent 的 ``server_time`` 工具和面向前端的公开时间端点：
前端「Pro 配色跟随时间」必须按服务器墙钟选色，不能拿平板本地时区/被改过的
系统时间当基准。``local_naive`` 与封禁落库的 ``BAN_UNTIL`` 保持同一口径。
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any

_WEEKDAYS = (
    "星期一",
    "星期二",
    "星期三",
    "星期四",
    "星期五",
    "星期六",
    "星期日",
)


def _zone_name(aware: datetime) -> str:
    """进程当前时区名（``Asia/Shanghai`` 这种），拿不到就退 ``tzname()``。

    顺序：``TZ`` 环境变量 → ``/etc/timezone`` → ``/etc/localtime`` 软链 →
    ``tzname()``。前三个给的是 IANA 名，告诉模型「这是东八区」比 ``CST`` 这种
    可能被误读成北美中部的缩写可靠。
    """
    env = os.environ.get("TZ", "").strip().lstrip(":")
    if env:
        return env
    try:
        text = Path("/etc/timezone").read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    if text:
        return text
    try:
        target = str(Path("/etc/localtime").resolve())
    except OSError:
        target = ""
    if "zoneinfo/" in target:
        return target.split("zoneinfo/", 1)[1]
    return aware.tzname() or "UTC"


def server_time_payload() -> dict[str, Any]:
    """权威时间基准。**naive 侧走 ``device_guard._to_local_naive`` 同一口径**：
    封禁落库的 ``USERS.BAN_UNTIL`` 是本地墙钟 naive，直接喂 aware UTC 会差出一个
    时区偏移（东八区「封 24 小时」实际只封 16 小时）。
    """
    from services.device_guard import _to_local_naive

    aware = datetime.now().astimezone()
    naive = _to_local_naive(aware) or datetime.now()
    return {
        "kind": "server_time",
        "now_iso": aware.isoformat(timespec="seconds"),
        "timezone": _zone_name(aware),
        "epoch_seconds": int(aware.timestamp()),
        "weekday": _WEEKDAYS[naive.weekday()],
        # 前端按服务器墙钟选配色阶段；不要从 now_iso 再经浏览器时区换算。
        "hour": naive.hour,
        # 与封禁落库同口径的墙钟值：给「封到几点」时直接用它加时长。
        "local_naive": naive.isoformat(sep=" ", timespec="seconds"),
        "note": (
            "local_naive 是本地墙钟 naive（与 BAN_UNTIL 落库口径一致）；"
            "now_iso 带时区偏移。两者指向同一时刻。"
        ),
    }


__all__ = ["server_time_payload"]
