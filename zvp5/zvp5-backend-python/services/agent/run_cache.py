"""run 详情的进程内快照（轮询去重）。

前端在 run 进行中按秒级节奏拉 ``GET /runs/{id}``。同一秒里重复的那几次读，数据
几乎不会变，却每次都要回源 ``AI_RUNS`` + ``AI_AGENT_EVENTS``（后者还带两个 CLOB）。
这里按 run_id 存一份 1 秒快照，命中就不再打 DB；``after_round`` 的增量在内存里切。

与 :mod:`services.agent.run_queue` 同一前提：**单 worker**（``config/supervisor.conf``
没有 ``--workers``），缓存只对当前进程可见。也正因如此边界很窄：

* 过期是惰性的（读的时候判），不另起定时器；
* 最多 64 条，按 **LRU** 淘汰（命中即刷新位置）——轮询只关心「眼前这几个 run」，
  没有长期留存价值；
* 只缓存**展示层**已经组装好的东西（run 视图的原始行、事件视图、提案视图），
  不缓存 SQL 里的 CLOB。

读侧返回的是**条目内的只读引用**（不再逐条深拷贝）：命中一拍的代价从「O(事件数)
的 dict()」降到一次查表。契约是「调用方不得原地修改」——``run_detail`` 只做切片
与序列化，正是按这个契约写的。

这个模块刻意不 import ``run_queue``（run_queue 依赖它，反向依赖会成环），只依赖
stdlib。
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Iterable, Mapping

#: 活跃 run 的快照寿命：比前端轮询间隔（2s）短，跨拍一定回源。
DEFAULT_TTL = 1.0

#: 终态 run 的快照寿命：历史回看不必每次回源（结果不会再变）。
TERMINAL_TTL = 30.0

#: 进程内条目上限。轮询窗口里同时关注的 run 只有个位数，64 是给并发留的余量。
MAX_ENTRIES = 64

#: 顺序 = LRU 顺序：队首最旧（下一个淘汰），队尾最新（刚写入或刚命中）。
_entries: OrderedDict[str, dict] = OrderedDict()
_hits = 0
_misses = 0
_evictions = 0


def _now() -> float:
    """单调时钟（测试替换这一个点即可控制过期，不必碰全局 ``time``）。"""
    return time.monotonic()


def put(
    run_id: str,
    *,
    row: Mapping[str, Any],
    events: Iterable[Mapping[str, Any]],
    proposals: Iterable[Mapping[str, Any]],
    ttl: float = DEFAULT_TTL,
) -> None:
    """写入（或覆盖）一次 run 的快照；覆盖时把它挪到最新位置（LRU 队尾）。"""
    key = str(run_id)
    entry = {
        "row": dict(row),
        "events": [dict(event) for event in events],
        "proposals": [dict(item) for item in proposals],
        "expires_at": _now() + max(0.0, float(ttl)),
    }
    # 先删再插：写入 / 覆盖都算「刚刚用过」，不该保留旧年龄（也就不会先被淘汰）。
    _entries.pop(key, None)
    _entries[key] = entry
    global _evictions
    while len(_entries) > MAX_ENTRIES:
        _entries.popitem(last=False)
        _evictions += 1


def get(run_id: str) -> dict | None:
    """取快照；过期或不存在返回 ``None``（过期项顺手删掉）。

    命中会把这个 key 挪到 LRU 队尾（「刚被用过」）。返回的是**条目内的只读引用**，
    **调用方不得原地修改** ``row`` / ``events`` / ``proposals``：那会污染后续所有
    命中者。需要改写就自己拷一份。
    """
    global _hits, _misses
    key = str(run_id)
    entry = _entries.get(key)
    if entry is None:
        _misses += 1
        return None
    if entry["expires_at"] <= _now():
        _entries.pop(key, None)
        _misses += 1
        return None
    _hits += 1
    _entries.move_to_end(key)
    return {
        "row": entry["row"],
        "events": entry["events"],
        "proposals": entry["proposals"],
    }


def invalidate(run_id: str) -> None:
    """丢掉一个 run 的快照（取消、收尾之后必须调用）。"""
    _entries.pop(str(run_id), None)


#: 事件展示视图的字段集，与 :func:`services.agent.proposal.run_events` 的输出逐字
#: 对齐。写入方（executor 的 ``_write_event``）只负责取值，这里保证形状不缺列——
#: 缓存里存的就是前端即将序列化的那份东西，缺键会渲染成 ``undefined``。
EVENT_KEYS = (
    "oid",
    "round_no",
    "tool_name",
    "tool_kind",
    "args_summary",
    "result_summary",
    "status",
    "created_at",
)


def append_event(run_id: str, event: Mapping[str, Any]) -> None:
    """把刚落库的事件追加到已缓存的时间线；未缓存（或已过期）时 no-op。

    由执行器在 ``_write_event`` 落库成功后调用，让「工具跑完」与「时间线多一行」
    在同一拍发生，不必等 1 秒 TTL 到期回源。事件按 :data:`EVENT_KEYS` 归一：
    调用方少给一列也不会让前端拿到两种字段集。
    """
    key = str(run_id)
    entry = _entries.get(key)
    if entry is None:
        return
    if entry["expires_at"] <= _now():
        _entries.pop(key, None)
        return
    entry["events"].append({field: event.get(field) for field in EVENT_KEYS})


def reset() -> None:
    """清空全部状态（**只给测试**）。"""
    global _hits, _misses, _evictions
    _entries.clear()
    _hits = 0
    _misses = 0
    _evictions = 0


def stats() -> dict:
    """当前快照数与命中计数（诊断/测试；不参与业务判定）。"""
    return {
        "entries": len(_entries),
        "max_entries": MAX_ENTRIES,
        "hits": _hits,
        "misses": _misses,
        "evictions": _evictions,
    }


__all__ = [
    "DEFAULT_TTL",
    "EVENT_KEYS",
    "MAX_ENTRIES",
    "TERMINAL_TTL",
    "append_event",
    "get",
    "invalidate",
    "put",
    "reset",
    "stats",
]
