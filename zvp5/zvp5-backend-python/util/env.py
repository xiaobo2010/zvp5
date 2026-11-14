"""环境变量读取：新名 ``ZVP5_*`` 优先，回落历史 ``ZVMS_*``。

项目已由 zvms 更名为 zvp5，但两台生产机的 unit / env 文件仍在导出 ``ZVMS_*``。
所有读取点统一走这里，改名就能分两步走完：

1. 现在：代码只写新名，:func:`getenv` 在旧名还有值时自动回落 —— 不改生产配置
   也能跑，改到哪台机器哪台就生效。
2. 收口：两台机器的 ``/etc/zvp5/*.env`` 与 drop-in 全部换成 ``ZVP5_*`` 后，把
   ``LEGACY_PREFIX`` 置空，回落逻辑消失，旧名从此无效。

两边都设置时以新名为准（新名空字符串也算「设置了」，便于显式关闭某个开关）。
"""

from __future__ import annotations

import os
from typing import Mapping

PREFIX = "ZVP5_"
LEGACY_PREFIX = "ZVMS_"
TRUTHY = frozenset({"1", "true", "yes", "on"})


def legacy_name(name: str) -> str | None:
    """``ZVP5_FOO`` -> ``ZVMS_FOO``；非新名前缀返回 ``None``。"""
    if not LEGACY_PREFIX or not name.startswith(PREFIX):
        return None
    return LEGACY_PREFIX + name[len(PREFIX) :]


def getenv(
    name: str,
    default: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """读 ``name``；未设置时按前缀回落到历史变量名。"""
    source = os.environ if environ is None else environ
    value = source.get(name)
    if value is None:
        old = legacy_name(name)
        if old is not None:
            value = source.get(old)
    return default if value is None else value


def getenv_bool(
    name: str,
    default: bool = False,
    *,
    environ: Mapping[str, str] | None = None,
    truthy: frozenset[str] = TRUTHY,
) -> bool:
    """把环境变量读成布尔值；未设置（新旧名都没有）时返回 ``default``。"""
    raw = getenv(name, None, environ=environ)
    if raw is None:
        return default
    return str(raw).strip().lower() in truthy
