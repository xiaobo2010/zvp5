"""AI 网关里少量「写后必须立刻失效」的共享缓存。

目前只有一处：``routers/ai_router.py`` 的「上游不支持流式」负缓存
（``zvms:state:kv:ai:stream:<credential-key>:<model>``，TTL 1 小时）。它按
``(凭据 key, 模型)`` 记，键名里没有模型/凭据的可枚举前缀，而模型覆盖行或凭据
一改，这条负缓存就可能让新配置继续走降级路径一小时。

为什么不做精确失效：键是二维组合，SCAN 一次拿不到「只属于这个模型」的 key；
这类 CRUD 是低频操作，全清全部 ``ai:stream:`` 的成本可以忽略。清空只会让下一次
请求重新试一次流式，失败会重新写回负缓存——没有副作用。
"""

from __future__ import annotations

from db import state

_PREFIX = "ai:stream:"


async def clear_stream_unsupported() -> int:
    """清空「上游不支持流式」的共享负缓存；返回删除条数（Redis 不可用时 0）。"""
    keys = await state.kv_scan(_PREFIX)
    if not keys:
        return 0
    await state.kv_delete(*keys)
    return len(keys)


__all__ = ["clear_stream_unsupported"]
