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

from typing import Any

__all__ = ["page_args", "page_bounds"]


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
