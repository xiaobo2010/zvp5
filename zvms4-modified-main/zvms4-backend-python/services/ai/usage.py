"""上游 ``usage`` 的解析与折算（``AI_CALL_LOGS`` 的 token 列）。

两件事必须只有一份实现：

* **把 usage 读成三个 token 数**：非流式从响应体的 ``usage`` 读，流式从最后一
  帧（``stream_options.include_usage``）里读。两处口径不同（dict / SSE 字节流），
  但落到库里的语义必须一样，否则同一张报表里两种行没法比。
* **token → 额度单位（units）的折算**：``UNITS`` 是扣额度用的整数，而 token 是
  上游报的实数；折算规则一变，历史与新增的行就不能相加了。

不变式（写在这里是因为它同时是读侧的解释规则）：

    token 列全为 NULL  ⟺  该行的 UNITS 是**估算值**

流式上游没回 usage 帧时，token 列留 NULL、UNITS 按「4 字符 ≈ 1 token」估算；
回了 usage，则 token 列与 UNITS 都来自 usage。不要用 0 代替 NULL：0 是一个真实
的 token 数，会把「不知道」伪装成观测值。

本模块是纯函数 + 一个增量解析器：不读配置、不写库、不记日志，可以离线单测。
"""

from __future__ import annotations

import json
import math
from typing import Any

# 估算口径：4 字符 ≈ 1 token。刻意与「上游 usage 的实测值」分开两条路径——
# 估算值是**下界**（中英文混排时字符数/token 比通常低于 4），如实标注比调参重要。
CHARS_PER_TOKEN = 4

# 一帧 SSE 的文本理论上不会超过几十 KB（一个 delta 或一个 usage 帧）。缓冲区
# 超过这个量还没见到帧结束符，说明上游吐的不是 SSE（或帧没有空行收尾）：直接
# 丢弃，别让一个异常上游把内存吃穿。丢弃只会让 usage 缺失 → token 列为 NULL，
# 属于「降级为估算」而不是错误。
MAX_BUFFER_BYTES = 64 * 1024

_FRAME_SEPARATORS = (b"\r\n\r\n", b"\n\n")


def _to_int(value: Any) -> int | None:
    """上游的数值可能是 int / float / 数字字符串；认不出就是「没报」。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def token_fields(usage: Any) -> dict[str, int | None]:
    """``usage`` dict → 三个 token 列（缺哪个留 ``None``，不是 0）。

    返回的键与 ``AI_CALL_LOGS`` 的列同名。``total_tokens`` 缺失时**不**用
    prompt+completion 推算：上游给的合计可能含缓存/推理 token，自己加出来的数
    会与供应商账单对不上，而这一列存在的意义正是对账。
    """
    fields: dict[str, int | None] = {
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
    }
    if not isinstance(usage, dict):
        return fields
    fields["prompt_tokens"] = _to_int(usage.get("prompt_tokens"))
    fields["completion_tokens"] = _to_int(usage.get("completion_tokens"))
    fields["total_tokens"] = _to_int(usage.get("total_tokens"))
    return fields


def has_tokens(fields: dict[str, int | None]) -> bool:
    """是否真的拿到了 token 数（三个全空的 usage 等于没报）。"""
    return any(value is not None for value in fields.values())


def units_from_total_tokens(total_tokens: int | None) -> int:
    """token 总数 → 额度单位：每 1000 token 一个单位，至少 1。

    至少 1 是刻意的：一次调用要留下「用过」的痕迹，0 单位会让额度表看起来
    什么都没发生（历史行为，本轮不改语义）。
    """
    total = max(0, int(total_tokens or 0))
    return max(1, math.ceil(total / 1000))


def estimate_units(prompt_chars: int, completion_chars: int) -> int:
    """流式没有 usage 时的估算：按字符 / 4 折算，至少 1。

    与 :func:`units_from_total_tokens` 的关系是「同一把尺子的两个来源」：估算值
    与实测值都折算成单位数再写 ``UNITS``，所以两行历史数据可以相加。
    """
    prompt_tokens = max(0, int(prompt_chars)) / CHARS_PER_TOKEN
    completion_tokens = max(0, int(completion_chars)) / CHARS_PER_TOKEN
    return max(1, math.ceil(prompt_tokens + completion_tokens))


def prompt_chars(payload: dict[str, Any] | Any) -> int:
    """请求侧的字符数（只用来估算，不进库、不落正文）。

    直接 ``json.dumps`` 整个 ``messages``：它包含了 role / content / 多模态结构，
    与上游真正看到的内容最接近。落库的永远只是字符数，不是这段 JSON。
    """
    if not isinstance(payload, dict):
        return 0
    messages = payload.get("messages")
    try:
        return len(json.dumps(messages or [], ensure_ascii=False))
    except (TypeError, ValueError):
        return 0


def _frame_usage(frame: str) -> dict[str, int | None] | None:
    """从一帧 SSE 的 ``data:`` 载荷里取 usage；没有就返回 None。"""
    data = frame.strip()
    if not data.startswith("data:"):
        return None
    data = data[len("data:") :].strip()
    if not data or data == "[DONE]":
        return None
    try:
        payload = json.loads(data)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    fields = token_fields(payload.get("usage"))
    return fields if has_tokens(fields) else None


class StreamUsageTracker:
    """增量喂 SSE 字节，拿最后一帧里的 ``usage``。

    为什么是增量解析而不是「拼起来最后解析」：流式响应可能很长（几十万字符），
    攒全量等于把整段回答留在内存里，而 usage 只在**最后一帧**出现。这里按帧边界
    切，处理完就丢，缓冲区只保留「还没闭合的那一帧」。

    一帧可能被 TCP 切成多段、也可能多帧挤在一段里——这与前端
    ``parseSseChunk`` 面对的是同一个问题，但后端只需要 usage，不需要内容。

    usage 帧语义上只会出现一次（OpenAI 兼容面的最后一帧，``choices`` 为空、
    ``usage`` 有值）；后到的覆盖先到的，是为了容忍「上游逐帧都带累计 usage」的
    实现，取最后一份即最完整的一份。
    """

    def __init__(self) -> None:
        self._buffer = b""
        self._fields: dict[str, int | None] = {
            "prompt_tokens": None,
            "completion_tokens": None,
            "total_tokens": None,
        }

    @property
    def prompt_tokens(self) -> int | None:
        return self._fields["prompt_tokens"]

    @property
    def completion_tokens(self) -> int | None:
        return self._fields["completion_tokens"]

    @property
    def total_tokens(self) -> int | None:
        return self._fields["total_tokens"]

    @property
    def captured(self) -> bool:
        """是否拿到过 usage（决定 token 列落值还是留 NULL）。"""
        return has_tokens(self._fields)

    def feed(self, chunk: bytes) -> None:
        """喂一段上游字节；只有完整的帧会被解析。"""
        if not isinstance(chunk, (bytes, bytearray)) or not chunk:
            return
        self._buffer += bytes(chunk)
        self._drain(final=False)

    def close(self) -> None:
        """流结束：最后一帧可能没带空行收尾，补一次解析。"""
        self._drain(final=True)

    def _drain(self, *, final: bool) -> None:
        while True:
            cut = self._find_separator()
            if cut is None:
                break
            frame, self._buffer = self._buffer[: cut[0]], self._buffer[cut[1] :]
            self._consume_frame(frame)

        if self._buffer and (final or len(self._buffer) > MAX_BUFFER_BYTES):
            # 收尾时按最后一帧解析一次；超限时直接丢弃（不猜边界，避免把内容
            # 拼错成 usage）。
            if final:
                self._consume_frame(self._buffer)
            self._buffer = b""

    def _find_separator(self) -> tuple[int, int] | None:
        found: tuple[int, int] | None = None
        for sep in _FRAME_SEPARATORS:
            index = self._buffer.find(sep)
            if index < 0:
                continue
            if found is None or index < found[0]:
                found = (index, index + len(sep))
        return found

    def _consume_frame(self, frame: bytes) -> None:
        text = frame.decode("utf-8", "replace")
        for line in text.replace("\r\n", "\n").split("\n"):
            if not line.startswith("data:"):
                continue
            found = _frame_usage(line)
            if found is not None:
                self._fields = found


__all__ = [
    "CHARS_PER_TOKEN",
    "MAX_BUFFER_BYTES",
    "StreamUsageTracker",
    "estimate_units",
    "has_tokens",
    "prompt_chars",
    "token_fields",
    "units_from_total_tokens",
]
