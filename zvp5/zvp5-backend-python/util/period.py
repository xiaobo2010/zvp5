"""自然月窗口（``YYYYMM`` 文本）的唯一实现。

为什么是 ``YYYYMM`` 文本而不是「月初时间戳 + 区间比较」：月度口径要能当**唯一
键**（``USER_WARNINGS`` / ``AI_QUOTA``）。文本相等让「这个月有没有记录」退化成
一次索引查找，不需要任何区间谓词，也没有「按哪个时区截断月初」的隐式规则。

为什么复制而不是从 :mod:`services.ai.quota` 引：额度是 AI 网关的能力，警告是
封禁体系的能力。让封禁模块依赖 AI 模块会绕出错误的依赖方向——AI 被关掉时
违规判定不该 import 不到东西。
"""

from __future__ import annotations

from datetime import datetime


def period_now(now: datetime | None = None) -> str:
    """当前自然月的 ``YYYYMM``。"""
    return (now or datetime.now()).strftime("%Y%m")


__all__ = ["period_now"]
