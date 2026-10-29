"""上游 ``usage`` 的解析与折算（``AI_CALL_LOGS`` 的 token 列）。

两件事必须只有一份实现：

* **把 usage 读成三个 token 数**：非流式从响应体的 ``usage`` 读，流式从最后一
  帧（``stream_options.include_usage``）里读。两处口径不同（dict / SSE 字节流），
  但落到库里的语义必须一样，否则同一张报表里两种行没法比。
* **token → Agent Fuel（AF）的折算**：``UNITS`` 是扣额度用的整数，语义是「本次
  消耗的 AF」，``AF = max(1, ceil(total_tokens / 1000 × 模型倍率))``。倍率来自
  ``AI_MODELS.RATE``（默认 1）；换算规则一变，历史与新增的行就不能直接相加了
  （本版由「每 1000 token 1 unit」改为乘倍率，**不做历史换算**）。

不变式（写在这里是因为它同时是读侧的解释规则）：

    token 列全为 NULL  ⟺  该行的 UNITS 是**估算值**

流式上游没回 usage 帧时，token 列留 NULL、UNITS 按「4 字符 ≈ 1 token」估算；
回了 usage，则 token 列与 UNITS 都来自 usage。不要用 0 代替 NULL：0 是一个真实
的 token 数，会把「不知道」伪装成观测值。

缓存列（``cache_read_tokens`` / ``cache_write_tokens``）沿用同一条 NULL 语义：
NULL = 上游没报缓存用量（或这个上游压根没有缓存计费），不是「0 个缓存 token」。
同一行的 ``prompt_tokens`` **已经包含**命中部分（OpenAI 与 DeepSeek 都是这个口径），
所以缓存命中率 = ``cache_read_tokens / prompt_tokens``，不要再改成
``cache_read / (prompt_tokens + cache_read)``。

Anthropic 形状是唯一需要**还原**分母的一家：它的 ``input_tokens`` 只是「最后一个
断点之后的未缓存部分」，总输入 = ``input_tokens + cache_read_input_tokens +
cache_creation_input_tokens``。所以 ``prompt_tokens`` 缺失时按这个口径还原（仅当
``input_tokens`` 存在时），``total_tokens`` 仍然只认上游原值——补的是命中率的分母，
不是替上游算合计。

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

# 计费口径的三个核心列：判「这次到底有没有拿到 usage」只看这三个。
_CORE_TOKEN_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")

# 缓存列：附加信息，不参与上面那个判定（理由见 ``has_tokens``）。
_CACHE_TOKEN_KEYS = ("cache_read_tokens", "cache_write_tokens")


def _to_int(value: Any) -> int | None:
    """上游的数值可能是 int / float / 数字字符串；认不出就是「没报」。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _cache_tokens(usage: dict[str, Any]) -> tuple[int | None, int | None]:
    """``usage`` → ``(cache_read_tokens, cache_write_tokens)``，各自缺失为 ``None``。

    三家上游的字段名互不相同，优先级固定成「先看 OpenAI 的嵌套形状，再看
    Anthropic 的平铺字段，最后看 DeepSeek 的命中字段」——同一个响应里不该出现多
    套，但网关聚合层（OpenRouter / LiteLLM）会把它们拼在一起，所以按顺序取第一个
    非 None 的，而不是相加（相加会把同一份缓存读算两次）。
    """
    details = usage.get("prompt_tokens_details")
    if not isinstance(details, dict):
        details = {}
    creation = usage.get("cache_creation")
    if not isinstance(creation, dict):
        creation = {}

    read = _to_int(details.get("cached_tokens"))
    if read is None:
        read = _to_int(usage.get("cache_read_input_tokens"))
    if read is None:
        read = _to_int(usage.get("prompt_cache_hit_tokens"))

    write = _to_int(details.get("cache_write_tokens"))
    if write is None:
        write = _to_int(usage.get("cache_creation_input_tokens"))
    if write is None:
        # Anthropic 的 5 分钟 / 1 小时缓存分开计价，但审计表只有一列「写入量」。
        # 两个都在就相加（同一次调用写入的总量），只有一个就取那一个。
        short = _to_int(creation.get("ephemeral_5m_input_tokens"))
        long = _to_int(creation.get("ephemeral_1h_input_tokens"))
        if short is not None and long is not None:
            write = short + long
        elif short is not None:
            write = short
        elif long is not None:
            write = long

    return read, write


def token_fields(usage: Any) -> dict[str, int | None]:
    """``usage`` dict → 五个 token 列（缺哪个留 ``None``，不是 0）。

    返回的键与 ``AI_CALL_LOGS`` 的列同名。``total_tokens`` 缺失时**不**用
    prompt+completion 推算：上游给的合计可能含缓存/推理 token，自己加出来的数
    会与供应商账单对不上，而这一列存在的意义正是对账。

    唯一的例外是 ``prompt_tokens`` 的 Anthropic 还原：缺 ``prompt_tokens`` 而有
    ``input_tokens`` 时按「input + 命中 + 写入」补出总输入。那是官方定义的还原，
    不是估算；没有 ``input_tokens`` 就依旧留 None。
    """
    fields: dict[str, int | None] = {
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
    }
    if not isinstance(usage, dict):
        return fields

    # 缓存两列先算：``prompt_tokens`` 的 Anthropic 还原要用到它们（见下）。
    read, write = _cache_tokens(usage)
    fields["cache_read_tokens"] = read
    fields["cache_write_tokens"] = write

    prompt_tokens = _to_int(usage.get("prompt_tokens"))
    if prompt_tokens is None:
        # Anthropic 形状的 usage 没有 ``prompt_tokens``，只有 ``input_tokens``——
        # 而按官方定义它只是「最后一个 cache 断点之后的**未缓存**部分」，总输入
        # = ``input_tokens + cache_read_input_tokens + cache_creation_input_tokens``。
        # 不还原的话「命中列有值、分母 NULL」，命中率恒为 None，缓存这两列白记。
        #
        # 这**不是**在推算 ``total_tokens``：那一列仍然只认上游原值，缺了就是
        # None（自己加出来的合计会与供应商账单对不上）。这里补的只是命中率的分母。
        base = _to_int(usage.get("input_tokens"))
        if base is not None:
            prompt_tokens = base + (read or 0) + (write or 0)
    fields["prompt_tokens"] = prompt_tokens

    fields["completion_tokens"] = _to_int(usage.get("completion_tokens"))
    fields["total_tokens"] = _to_int(usage.get("total_tokens"))
    return fields


def has_tokens(fields: dict[str, int | None]) -> bool:
    """是否真的拿到了 token 数（三个**核心**列全空 = 没报）。

    只认 ``_CORE_TOKEN_KEYS``，缓存列不参与。理由：调用方用这个判断决定「token
    列落值还是留 NULL、units 走实测还是估算」。若一帧只带
    ``prompt_tokens_details.cached_tokens`` 而三个核心列全空，把它算成「拿到
    usage」会让 units 从「按字符估算」退化成 ``units_from_total_tokens(None)`` 的
    恒定 1，额度被系统性低估；同时该行出现「有 token 值但 units 是估算值」的
    混合态，读侧的不变式就失效了。
    """
    return any(fields.get(key) is not None for key in _CORE_TOKEN_KEYS)


def _clean_rate(rate: object) -> float:
    """倍率兜底：非数字 / 非有限值 / ``<= 0`` 一律按 1（基准价）算。

    ``rate`` 来自 ``AI_MODELS.RATE``（管理员可编辑），坏值不该让一次已经成功的
    上游调用变成 500——更不该算出 0 AF（等于白送）。
    """
    if isinstance(rate, bool) or not isinstance(rate, (int, float)):
        return 1.0
    value = float(rate)
    if not math.isfinite(value) or value <= 0:
        return 1.0
    return value


def af_units(total_tokens: int | None, rate: float = 1.0) -> int:
    """token 总数 × 模型倍率 → **Agent Fuel（AF）**，至少 1。

    公式（唯一口径，实测与估算共用）::

        AF = max(1, ceil(total_tokens / 1000 × rate))

    ``rate`` 是模型的价格倍率（``AI_MODELS.RATE``，默认 1 = 基准价）：1 AF 即
    1000 token 的基准成本。``rate <= 0`` / 非有限值 / 非数字都按 1 处理。

    至少 1 是刻意的：一次调用要留下「用过」的痕迹，0 AF 会让额度表看起来什么
    都没发生。``total_tokens=None``（上游没报 usage）同样给 1——**调用方在能
    估算时应先估算再进来**（见 :func:`estimate_af`），这里的 1 只是最后兜底。

    结尾的 ``round(..., 9)`` 是防浮点尘：``2.4 × 1.0`` 之类本应整除的组合在
    二进制浮点下可能算成 ``3.0000000000000004``，直接 ``ceil`` 会多收 1 AF。
    """
    total = max(0, int(total_tokens or 0))
    return max(1, math.ceil(round(total / 1000 * _clean_rate(rate), 9)))


def units_from_total_tokens(total_tokens: int | None) -> int:
    """**legacy 别名**：等价于 ``af_units(total_tokens, 1.0)``。

    ``UNITS`` 列的语义自本版起统一为 AF（见 :func:`af_units`），这个函数保留是
    为了不打断既有调用点与历史文档；新代码请直接用 :func:`af_units` 并显式传
    模型倍率，否则贵模型会被按基准价计费。
    """
    return af_units(total_tokens, 1.0)


def estimate_tokens_from_chars(prompt_chars: int, completion_chars: int) -> int:
    """字符数 → token 估算（4 字符 ≈ 1 token，向上取整）。

    ``0 字符`` 就是 ``0 token``：这里不抬到 1，抬下限是 :func:`af_units` 的职责。
    """
    chars = max(0, int(prompt_chars)) + max(0, int(completion_chars))
    return math.ceil(chars / CHARS_PER_TOKEN)


def estimate_af(prompt_chars: int, completion_chars: int, rate: float = 1.0) -> int:
    """没有 usage 时的 AF 估算：先按字符估 token，再走**同一个** AF 公式。

    这是本版修掉的口径分裂：旧 ``estimate_units`` 返回的是 token 量级（少了
    ``/1000``），实测路径返回的是千 token 量级，两者差了 1000 倍却写进同一列。
    """
    return af_units(estimate_tokens_from_chars(prompt_chars, completion_chars), rate)


def estimate_units(
    prompt_chars: int, completion_chars: int, rate: float = 1.0
) -> int:
    """**AF 口径**的估算（与 :func:`estimate_af` 完全等价，保留旧函数名）。

    注意返回值变了：改造前是 ``ceil(p/4 + c/4)``（token 个数），现在是 AF——
    与实测路径同量纲，两者相加才有意义。
    """
    return estimate_af(prompt_chars, completion_chars, rate)


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
            "cache_read_tokens": None,
            "cache_write_tokens": None,
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
    def cache_read_tokens(self) -> int | None:
        return self._fields["cache_read_tokens"]

    @property
    def cache_write_tokens(self) -> int | None:
        return self._fields["cache_write_tokens"]

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
    "af_units",
    "estimate_af",
    "estimate_tokens_from_chars",
    "estimate_units",
    "has_tokens",
    "prompt_chars",
    "token_fields",
    "units_from_total_tokens",
]
