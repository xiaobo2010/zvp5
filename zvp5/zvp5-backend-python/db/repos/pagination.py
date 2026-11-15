"""分页参数归一：``page``/``size`` → ``(offset, limit)``。

各端点的默认值与上限**并不相同**（管理端日志一页 10 条、上限 100；封禁/流水
列表一页 20；配额管理 50……），所以这里不放全局常量：调用点自己传
``default_size`` / ``max_size``，本模块只保证三件事：

* ``page`` 至少 1；
* ``size`` 至少 1，``None``/非数字回 ``default_size``；
* 给了 ``max_size`` 就封顶（没给表示该端点本来就不设上限）。

不改变任何端点的既有默认值与上限，也不做「全部统一成 50」这种契约变更。
"""

from __future__ import annotations

import logging
from typing import Any

__all__ = ["LIST_PAGE_SIZE_MAX", "capped_size", "page_args", "page_bounds"]

_logger = logging.getLogger(__name__)

# 列表接口（活动列表 / 用户列表）的单页硬上限。端点上的 ``le=1000`` 不动
# （老客户端传大页不该 422），超出的部分在这里夹掉并留一条 warning。
LIST_PAGE_SIZE_MAX = 200


def capped_size(
    size: Any,
    max_size: int = LIST_PAGE_SIZE_MAX,
    *,
    where: str = "",
) -> int:
    """``size`` 归一到 ``1..max_size``；真的夹了就打一条 warning。

    夹取是**静默降级**：客户端要 1000 行只拿到 ``max_size`` 行，总数仍由
    ``COUNT(*) OVER()`` 如实回报。warning 不带请求体，只带端点名与两个数字，
    便于发现还有谁在用超大页。
    """
    try:
        value = int(size)
    except (TypeError, ValueError):
        value = 1
    value = max(1, value)
    if value > max_size:
        _logger.warning(
            "%s size=%s 超过单页上限 %s，已夹取", where or "列表接口", value, max_size
        )
        return max_size
    return value


def page_bounds(
    page: Any,
    size: Any,
    *,
    default_size: int | None = None,
    max_size: int | None = None,
) -> tuple[int, int]:
    """归一成 ``(page, size)``：都至少 1，``size`` 可选封顶/回落默认值。"""
    try:
        page_no = int(page)
    except (TypeError, ValueError):
        page_no = 1
    try:
        size_no = int(size)
    except (TypeError, ValueError):
        # 解析不出来才用默认值；调用方本来就是 ``int`` 时这条分支不会走到。
        size_no = default_size if default_size is not None else 1
    size_no = max(1, size_no)
    if max_size is not None:
        size_no = min(size_no, max_size)
    return max(1, page_no), size_no


def page_args(
    page: Any,
    size: Any,
    *,
    default_size: int | None = None,
    max_size: int | None = None,
) -> tuple[int, int]:
    """归一成 ``(offset, limit)``，直接喂给 ``OFFSET/FETCH`` 或列表切片。"""
    page_no, size_no = page_bounds(
        page, size, default_size=default_size, max_size=max_size
    )
    return (page_no - 1) * size_no, size_no
