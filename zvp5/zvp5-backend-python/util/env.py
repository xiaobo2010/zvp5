"""环境变量读取的唯一入口。

项目已由 zvms 更名为 zvp5，所有配置变量统一叫 ``ZVP5_*``。本模块存在的理由：

* 名字规则集中在一处 —— 以后要再换前缀，改这里而不是翻 20 个文件；
* ``getenv`` 的语义固定为「未设置才用默认值」，显式空字符串算「关掉」，
  不会悄悄退回默认值（CORS 白名单、DB 池大小这类配置上这点很关键）；
* 布尔值的真值集合统一，避免每个调用点自己写一遍。

2026-11-14 之前这里还有一层 ``ZVMS_*`` 回落。两台生产机的
``/etc/zvp5/*.env`` 与 ``zvp5.service.d/*.conf`` 已全部换名并验证，回落层
随之删除；现在只认新名。
"""

from __future__ import annotations

import os
from typing import Mapping

PREFIX = "ZVP5_"
TRUTHY = frozenset({"1", "true", "yes", "on"})


def getenv(
    name: str,
    default: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """读 ``name``；未设置（含没有该键）时返回 ``default``。"""
    source = os.environ if environ is None else environ
    value = source.get(name)
    return default if value is None else value


def getenv_bool(
    name: str,
    default: bool = False,
    *,
    environ: Mapping[str, str] | None = None,
    truthy: frozenset[str] = TRUTHY,
) -> bool:
    """把环境变量读成布尔值；未设置时返回 ``default``。"""
    raw = getenv(name, None, environ=environ)
    if raw is None:
        return default
    return str(raw).strip().lower() in truthy
