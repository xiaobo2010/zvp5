"""时间值 → 字符串的统一序列化。

仓库里此前有十几份 ``isoformat()`` 小函数，差别全在细节上：``T`` 还是空格
分隔、是否截到秒、``None`` 回 ``None`` 还是空串、非 ``datetime`` 是原样透传
还是 ``str()``。这些细节都是**响应体契约**的一部分，所以这里不做「一刀切」，
而是把每种语义各给一个名字——调用点按原语义挑一个，行为逐字不变。

命名规律：``iso*`` 系列只认 ``datetime``（+ 少量显式的透传），不做时区推断；
唯一会把 naive 当 UTC 的是 :func:`iso_utc`，那是给「ADB 会话时区 = UTC」的
运维面时间戳用的。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

__all__ = [
    "iso",
    "iso_seconds",
    "iso_space_seconds",
    "iso_text",
    "iso_str",
    "iso_str_nonempty",
    "iso_utc",
    "iso_epoch_utc",
]


def iso(value: Any) -> str | None:
    """``datetime`` → ``isoformat()``（`T` 分隔，保留精度/时区）；其它 → ``None``。"""
    return value.isoformat() if isinstance(value, datetime) else None


def iso_seconds(value: Any) -> str | None:
    """``datetime`` → ``isoformat(timespec="seconds")``；其它 → ``None``。"""
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return None


def iso_space_seconds(value: Any) -> str | None:
    """``datetime`` → ``isoformat(sep=" ", timespec="seconds")``；其它 → ``None``。

    空格分隔是 AI/运维面既有口径（TIMESTAMP 列的人读形状），不要拿来当
    HTTP 的时间标准格式。
    """
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return None


def iso_text(value: Any) -> str | None:
    """``None`` → ``None``；``datetime`` → ``iso``；其它 → ``str(value)``。

    用于「仓储已序列化、内存仓储给对象」的宽容投影：不丢值，也不猜类型。
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def iso_str(value: Any) -> str | None:
    """``datetime`` → 空格分隔秒级；``str`` 原样（含空串）；其它 → ``None``。"""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return value if isinstance(value, str) else None


def iso_str_nonempty(value: Any) -> str | None:
    """同 :func:`iso_str`，但空串视作缺失 → ``None``。"""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, str) and value:
        return value
    return None


def iso_utc(value: Any) -> str | None:
    """``datetime`` → ISO-8601，naive 值标注为 UTC；非 ``datetime`` 走 :func:`iso_text`。

    Oracle ``TIMESTAMP``/``DATE`` 不带时区，ADB 会话时区是 UTC（生产已核实），
    所以 naive 值必须显式补 ``+00:00``，否则浏览器会按本地时区解读。
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return iso_text(value)


def iso_epoch_utc(timestamp: float | int) -> str:
    """Unix 秒 → UTC ISO-8601 字符串（始终返回字符串）。"""
    return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).isoformat()
