"""Oracle 仓储层共享的关键字匹配工具（``LIKE``，不是 ``REGEXP_LIKE``）。

关键字检索一律编译成::

    LOWER(col) LIKE :pattern ESCAPE '\\'

理由：``REGEXP_LIKE`` 会在**每一行**上编译一次正则，用户可控的关键词还能构造
灾难性回溯，并且正则谓词用不上普通索引——跨洋 ADB 上就是实打实的全表扫描。
``LIKE`` 是普通字符串比较，配合函数索引（``LOWER(col)``）还能走索引。

:func:`like_pattern` 只做三件事：转义 LIKE 通配符（``%`` / ``_`` / ``\\``）、
小写（配合 ``LOWER(col)``）、两头加 ``%``。列侧的小写由调用方负责，且必须带上
``escape="\\\\"``，否则转义字符就成了字面反斜杠。

本模块之前有四个副本（``times`` / ``app_logs`` / ``device_bans`` 各自一份），
语义完全一致，这里收敛成唯一实现。
"""

from __future__ import annotations

#: 关键词长度上限：超过就判空（不查库、不报错），避免异常长的入参进 SQL。
SEARCH_QUERY_MAX = 64

#: ``LIKE`` 的转义字符，与 SQL 里的 ``ESCAPE '\\'`` 必须一致。
LIKE_ESCAPE = "\\"


def _escape_like(value: str) -> str:
    """转义 ``LIKE`` 读作通配符的三个字符（反斜杠必须先转）。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def like_pattern(query: str) -> str:
    """``%query%``：转义 LIKE 通配符并小写，配合 ``LOWER(col)`` 使用。"""
    return f"%{_escape_like(query).lower()}%"


def like_prefix_pattern(prefix: str) -> str:
    """``query%``：前缀匹配（``^prefix`` 正则的 ``LIKE`` 等价物）。"""
    return f"{_escape_like(prefix).lower()}%"
