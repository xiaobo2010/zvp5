"""run 的进程内实时事件总汇（SSE）。

设计约束只有一条：**发布方永远不阻塞**。executor 正在跑工具/等上游，慢客户端
不能让它排队。所以每个订阅者是一个有界队列，满了就按事件类型丢弃/合并：

* ``assistant``：同一 ``stream_key`` 只保留**最新累计文本**（原地替换，不增长）；
* ``tool``：新的同 ``tool_call_id`` 覆盖旧的；队列满时丢最旧的 ephemeral 帧并
  置 ``resync_pending``，下一次读取先补一个 ``resync`` 让客户端重新拉快照；
* ``resync``：只保留一个（快照是幂等的，重复拉没有意义）；
* ``terminal`` 不会从发布方直接来：终态由 ``resync`` + 路由层读到的 run 状态
  推导，保证「最终 snapshot → terminal → [DONE]」的顺序。

跨进程说明：这是单 worker 的内存 hub。多 worker 部署下各进程的订阅互不可见，
所以路由层在 :func:`multi_worker` 为真时直接回 503，而不是假装流式可用。
"""

from __future__ import annotations

import asyncio
import json
import os
from collections import deque
from typing import Any, AsyncIterator, Mapping

#: 单个订阅者最多积压多少帧。64 够覆盖「一轮 5 个工具 + 若干文本增量」的毛刺。
MAX_PENDING = 64

#: SSE 心跳间隔（秒）。注释帧不参与 ``seq``，客户端解析器会忽略。
HEARTBEAT_SECONDS = 15.0

#: 与 ``run_queue`` 的终态集合同值。这里不 import 它（避免成环），只做一次性
#: 判断；真正的状态仍以 ``run_detail`` 返回的 run 行自己携带的值为准。
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "interrupted"})

_seq: dict[str, int] = {}
_subs: dict[str, set["Subscription"]] = {}
_live_assistant: dict[str, dict[str, dict]] = {}
_live_tools: dict[str, dict[str, dict]] = {}

_WORKER_ENV_KEYS = ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS")


def multi_worker() -> bool:
    """当前是否可能有多 worker（无法安全提供跨进程 SSE）。

    显式开关 ``AI_RUN_EVENTS_MULTI_WORKER=1`` 优先；否则读常见的 worker 环境
    变量。小于等于 1 一律按单 worker 处理。
    """
    forced = os.environ.get("AI_RUN_EVENTS_MULTI_WORKER", "").strip().lower()
    if forced in ("1", "true", "yes", "on"):
        return True
    for key in _WORKER_ENV_KEYS:
        raw = os.environ.get(key, "").strip()
        try:
            if int(raw) > 1:
                return True
        except ValueError:
            continue
    return False


def next_seq(run_id: str) -> int:
    """run 内单调递增的帧序号（从 1 开始，允许空洞）。"""
    key = str(run_id)
    value = _seq.get(key, 0) + 1
    _seq[key] = value
    return value


def current_seq(run_id: str) -> int:
    return _seq.get(str(run_id), 0)


class Subscription:
    """一个有界、非阻塞的订阅者队列。

    ``offer`` 是同步方法：发布方（executor 回调）调用它不会 await，慢客户端
    最多让内存里少几帧，不会让 run 停下来。
    """

    def __init__(self, run_id: str, *, max_pending: int = MAX_PENDING) -> None:
        self.run_id = str(run_id)
        self.max_pending = max(1, int(max_pending))
        self._items: deque[dict] = deque()
        self._wake = asyncio.Event()
        self._closed = False
        self.resync_pending = False
        self.resync_seq: int | None = None

    def offer(self, frame: Mapping[str, Any]) -> None:
        if self._closed:
            return
        kind = str(frame.get("type") or "")
        if kind in ("assistant", "resync"):
            for index, existing in enumerate(self._items):
                if existing.get("type") != kind:
                    continue
                if kind == "assistant" and existing.get("stream_key") != frame.get(
                    "stream_key"
                ):
                    continue
                del self._items[index]
                self._items.append(dict(frame))
                self._wake.set()
                return
        if len(self._items) >= self.max_pending:
            self.resync_pending = True
            self.resync_seq = int(frame.get("seq") or 0) or next_seq(self.run_id)
            self._items.clear()
            self._wake.set()
            return
        self._items.append(dict(frame))
        self._wake.set()

    async def get(self, timeout: float) -> dict | None:
        """取下一帧；``timeout`` 到期返回 ``None``（调用方据此发心跳）。"""
        if self._closed:
            return None
        if self.resync_pending:
            self.resync_pending = False
            seq = self.resync_seq if self.resync_seq is not None else next_seq(self.run_id)
            self.resync_seq = None
            return {"type": "resync", "seq": seq, "run_id": self.run_id}
        if self._items:
            frame = self._items.popleft()
            if self._items:
                self._wake.set()
            return frame
        self._wake.clear()
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=max(0.001, timeout))
        except (asyncio.TimeoutError, TimeoutError):
            return None
        if self._closed or not self._items:
            return None
        frame = self._items.popleft()
        if self._items:
            self._wake.set()
        return frame

    def close(self) -> None:
        self._closed = True
        self._items.clear()
        self._wake.set()


def subscribe(run_id: str, *, max_pending: int = MAX_PENDING) -> Subscription:
    key = str(run_id)
    sub = Subscription(key, max_pending=max_pending)
    _subs.setdefault(key, set()).add(sub)
    return sub


def unsubscribe(subscription: Subscription) -> None:
    key = subscription.run_id
    bucket = _subs.get(key)
    if bucket is not None:
        bucket.discard(subscription)
        if not bucket:
            _subs.pop(key, None)
    subscription.close()


def publish(run_id: str, event: Mapping[str, Any]) -> dict:
    """发布一帧：分配 seq、更新 live 状态、非阻塞写给所有订阅者。"""
    key = str(run_id)
    frame = {"type": str(event.get("type") or "event"), "seq": next_seq(key), "run_id": key}
    frame.update({str(k): v for k, v in event.items() if k != "seq"})
    kind = frame["type"]
    if kind == "assistant":
        stream_key = str(frame.get("stream_key") or "")
        if stream_key:
            _live_assistant.setdefault(key, {})[stream_key] = frame
    elif kind == "tool":
        call_id = str(frame.get("tool_call_id") or "")
        if call_id:
            _live_tools.setdefault(key, {})[call_id] = frame
    elif kind == "snapshot":
        # 快照是 DB 事实：用它覆盖 live 状态，避免断线重连后还挂着旧增量。
        return frame
    for subscription in tuple(_subs.get(key, ())):
        subscription.offer(frame)
    return frame


def publish_resync(run_id: str) -> None:
    """让订阅者重新拉一次快照（轮提交、状态变化、终态前都用它）。"""
    key = str(run_id)
    if not _subs.get(key):
        return
    publish(key, {"type": "resync"})


def has_subscribers(run_id: str) -> bool:
    """有没有人在看：没有订阅者时 executor 连 frame 都不必构造。"""
    return bool(_subs.get(str(run_id)))


def live_state(run_id: str) -> dict:
    """当前 live 状态的快照（首帧与 resync 帧带上，客户端不用等增量）。"""
    key = str(run_id)
    assistant = sorted(_live_assistant.get(key, {}).values(), key=lambda item: item.get("seq") or 0)
    tools = sorted(_live_tools.get(key, {}).values(), key=lambda item: item.get("seq") or 0)
    return {"assistant": assistant, "tools": tools}


def clear_live(run_id: str) -> None:
    key = str(run_id)
    _live_assistant.pop(key, None)
    _live_tools.pop(key, None)


def clear_round(run_id: str, round_no: int) -> None:
    """轮提交后丢掉该轮的 ephemeral 状态（快照已经有持久化版本）。"""
    key = str(run_id)
    stream_key = f"{key}:{int(round_no)}"
    _live_assistant.get(key, {}).pop(stream_key, None)
    bucket = _live_tools.get(key)
    if bucket:
        for call_id, frame in list(bucket.items()):
            if int(frame.get("round_no") or 0) == int(round_no):
                bucket.pop(call_id, None)


def cleanup(run_id: str, *, drop_seq: bool = False) -> None:
    """释放该 run 的 live 状态（没有订阅者时）；``drop_seq`` 仅在确认收尾后使用。

    断线重连时不能丢 ``seq``：客户端还带着上一段的最后序号，若从 0 重新开始，
    去重逻辑会把新帧当旧帧丢掉。
    """
    key = str(run_id)
    if _subs.get(key):
        return
    clear_live(key)
    if drop_seq:
        _seq.pop(key, None)


def reset() -> None:
    """清空全部状态（只给测试）。"""
    _seq.clear()
    _subs.clear()
    _live_assistant.clear()
    _live_tools.clear()


def sse(frame: Mapping[str, Any]) -> bytes:
    return (
        "data: " + json.dumps(dict(frame), ensure_ascii=False, separators=(",", ":")) + "\n\n"
    ).encode("utf-8")


DONE_FRAME = b"data: [DONE]\n\n"
HEARTBEAT_FRAME = b": ping\n\n"


def terminal_frame(run_id: str, detail: Mapping[str, Any]) -> dict:
    run = detail.get("run") if isinstance(detail, Mapping) else {}
    run = run if isinstance(run, Mapping) else {}
    return {
        "type": "terminal",
        "seq": next_seq(run_id),
        "run_id": str(run_id),
        "status": run.get("status"),
        "stop_reason": run.get("stop_reason"),
        "error_code": run.get("error_code"),
    }


async def event_frames(
    subscription: Subscription,
    *,
    run_id: str,
    initial: Mapping[str, Any],
    snapshot_seq: int,
    user: Mapping[str, Any],
    heartbeat: float = HEARTBEAT_SECONDS,
) -> AsyncIterator[bytes]:
    """SSE 生成器：首帧完整 snapshot，之后 live 增量，终态前补最终 snapshot。

    ``snapshot_seq`` 是**订阅之后、读库之前**捕获的 hub 序号：等价于「读到这份
    快照时，所有 seq <= snapshot_seq 的事件都已经在快照里」。客户端按 seq 去重，
    晚到的重复帧不会重复渲染。
    """
    from . import run_queue  # 局部导入：run_queue 在模块层 import live

    def snapshot_frame(detail: Mapping[str, Any], seq: int) -> dict:
        return {
            "type": "snapshot",
            "seq": seq,
            "run_id": str(run_id),
            "data": detail,
            "live": live_state(run_id),
        }

    terminal_seen = False
    try:
        detail = initial
        yield sse(snapshot_frame(detail, snapshot_seq))
        run = detail.get("run") if isinstance(detail, Mapping) else {}
        if str((run or {}).get("status") or "") in TERMINAL_STATUSES:
            terminal_seen = True
            yield sse(terminal_frame(run_id, detail))
            yield DONE_FRAME
            return

        while True:
            frame = await subscription.get(heartbeat)
            if frame is None:
                yield HEARTBEAT_FRAME
                continue
            kind = frame.get("type")
            if kind == "resync":
                try:
                    detail = await run_queue.run_detail(user=dict(user), run_id=str(run_id))
                except Exception as exc:  # noqa: BLE001 - 流里只能报错误帧
                    code = getattr(exc, "code", None) or "AI_RUN_EVENTS_FAILED"
                    yield sse(
                        {
                            "type": "error",
                            "seq": next_seq(run_id),
                            "run_id": str(run_id),
                            "code": code,
                        }
                    )
                    yield DONE_FRAME
                    return
                yield sse(snapshot_frame(detail, int(frame.get("seq") or next_seq(run_id))))
                run = detail.get("run") if isinstance(detail, Mapping) else {}
                if str((run or {}).get("status") or "") in TERMINAL_STATUSES:
                    terminal_seen = True
                    yield sse(terminal_frame(run_id, detail))
                    yield DONE_FRAME
                    return
                continue
            if kind == "terminal":
                terminal_seen = True
                yield sse(frame)
                yield DONE_FRAME
                return
            yield sse(frame)
    finally:
        unsubscribe(subscription)
        cleanup(run_id, drop_seq=terminal_seen)


__all__ = [
    "DONE_FRAME",
    "HEARTBEAT_FRAME",
    "HEARTBEAT_SECONDS",
    "MAX_PENDING",
    "Subscription",
    "TERMINAL_STATUSES",
    "clear_live",
    "clear_round",
    "cleanup",
    "current_seq",
    "event_frames",
    "has_subscribers",
    "live_state",
    "multi_worker",
    "next_seq",
    "publish",
    "publish_resync",
    "reset",
    "sse",
    "subscribe",
    "terminal_frame",
    "unsubscribe",
]
