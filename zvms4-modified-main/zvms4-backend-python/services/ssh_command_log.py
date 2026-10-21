"""把 Web SSH 的键盘字节流还原成「执行过的命令行」。

终端里没有「一条命令」这种东西：服务端拿到的是一串按键（可打印字符、退格、
回车、方向键的 ANSI 转义序列、Ctrl-C、粘贴进来的多行文本）。要在审计里留下
「谁执行了什么」，只能自己维护一个行缓冲，按**发送给 PTY 的原始输入**重建。

为什么是重建而不是「从 shell 要历史」：PTY 里的目标是远端真实服务器，可能
只给了普通用户，读 ``~/.bash_history`` 既不可靠（非交互/不同 shell/被清空）
又超出本服务的管理边界。审计的定位是「操作留痕」，能还原到命令行级别就够。

边界规则（每条都有单测钉住）：

* 可打印字符（含中文等多字节字符，``input.data`` 是 ``str``）追加进缓冲。
* ``\\x7f`` / ``\\b`` 退格删一个字符 —— 否则改错重打会把两版混成一条假命令。
* ``\\r`` / ``\\n`` 结束一行；``\\r\\n`` 只算一次（xterm 两种都发）。
* ANSI CSI/OSC 转义序列整体跳过：方向键 ``\\x1b[A`` 是「从历史里调命令」，
  把它记成命令行纯属噪声。转义序列可能被切成两帧，状态跨 ``feed`` 保留。
* ``\\x03``（Ctrl-C）：当前行有内容时补 ``^C`` 记一条，让「敲了一半被打断」
  也留痕；空行直接丢弃。
* 单行上限 :data:`MAX_COMMAND_CHARS`，超出部分丢弃并标记 ``…(truncated)``：
  误解是把 ``cat`` 大文件的内容粘进终端，不设上限会按 8KB/帧灌进日志库。
* 未闭合的转义序列超过 :data:`MAX_ESCAPE_CHARS` 就整行作废、丢到下一个换行
  重同步：否则一个始终不结束的 OSC 能把之后所有输入都吞掉，变成审计盲区。
* 纯空白行不记。
* 一次 ``feed`` 可以产出多行（粘贴多行脚本）。

已知取舍：Tab 只触发远端补全，不进入缓冲；补全后 shell 实际执行的命令可能
与缓冲不同（补全结果只在终端输出里）。这里不猜补全结果，宁可少记也不记错。
"""

from __future__ import annotations

MAX_COMMAND_CHARS = 512
TRUNCATED_MARK = "…(truncated)"
INTERRUPT_MARK = "^C"
# 未闭合转义序列的容忍长度。正常的 CSI/OSC 都在几十字节内结束；一直不结束
# 只能是坏客户端，不能让它把后续输入无限吞掉（那等于开一条审计盲区）。
MAX_ESCAPE_CHARS = 64

_CSI_FINAL_MIN = "@"
_CSI_FINAL_MAX = "~"

_BACKSPACE = ("\x7f", "\b")


class CommandLineBuffer:
    """输入字节流 → 完整命令行列表。纯状态机，无 I/O，可单测。

    ``feed`` 返回本次输入**结束掉**的行；未结束的尾巴留在缓冲里，等下一条
    输入（或 Ctrl-C）再产出。会话结束时要不要冲刷尾巴由调用方决定——半行
    没按回车，远端并没有执行，本模块默认不把它当成命令。
    """

    def __init__(self, limit: int = MAX_COMMAND_CHARS) -> None:
        self._limit = limit
        self._chars: list[str] = []
        self._truncated = False
        # "text" | "esc" | "csi" | "osc" | "osc_esc"
        self._state = "text"
        self._escape_len = 0
        self._swallow_lf = False

    # -- 对外 ---------------------------------------------------------------

    def feed(self, data: str) -> list[str]:
        lines: list[str] = []
        for char in data:
            self._consume(char, lines)
        return lines

    @property
    def pending(self) -> str:
        """当前未结束的行（截断标记已含）。供调试/测试用。"""
        return self._take()

    def flush_interrupt(self) -> list[str]:
        """把当前行以 ``^C`` 收尾；空行不产出。"""
        if not self._chars:
            return []
        line = self._take()
        self._reset()
        return [f"{line}{INTERRUPT_MARK}"]

    # -- 内部 ---------------------------------------------------------------

    def _consume(self, char: str, lines: list[str]) -> None:
        if self._state == "discard":
            # 已污染的输入行：丢到下一个换行为止，再回到正常解析。记一条
            # 拼接出来的假命令比不记更糟——远端到底执行了什么无从得知。
            if char in ("\r", "\n"):
                self._swallow_lf = char == "\r"
                self._state = "text"
            return
        if self._state != "text":
            self._escape_len += 1
            if self._escape_len > MAX_ESCAPE_CHARS:
                # 坏客户端/恶意输入：未闭合序列不能一直吞输入，整行作废并重同步。
                self._state = "discard"
                self._escape_len = 0
                self._reset()
                return
            self._consume_escape(char)
            if self._state == "text":
                self._escape_len = 0
            return

        if self._swallow_lf:
            # 上一个字符是 \r，紧跟的 \n 属于同一个回车。
            self._swallow_lf = False
            if char == "\n":
                return

        if char == "\x1b":
            self._state = "esc"
            self._escape_len = 0
            return
        if char in ("\r", "\n"):
            self._swallow_lf = char == "\r"
            self._end_line(lines)
            return
        if char in _BACKSPACE:
            if self._chars:
                self._chars.pop()
            return
        if char == "\x03":
            lines.extend(self.flush_interrupt())
            return
        if char < " " or char == "\x7f":
            # 其余 C0 控制符（Tab/方向键之外的 Ctrl-X 组合）不构成命令内容。
            return
        self._append(char)

    def _consume_escape(self, char: str) -> None:
        if self._state == "esc":
            if char == "[":
                self._state = "csi"
            elif char == "]":
                self._state = "osc"
            elif char == "\x1b":
                # 连续 ESC 是新一轮序列的开头，状态不前进也不退出。
                self._state = "esc"
            else:
                # 两字符转义（如 ESC 7 / ESC 8）：第二个字符吃掉即可。
                self._state = "text"
            return
        if self._state == "csi":
            # CSI 的最后一个字节落在 0x40-0x7E，参数与中间字节在此之前。
            if _CSI_FINAL_MIN <= char <= _CSI_FINAL_MAX:
                self._state = "text"
            return
        if self._state == "osc":
            if char == "\x07":
                self._state = "text"
            elif char == "\x1b":
                self._state = "osc_esc"
            return
        # ``osc_esc``：OSC 的结束符是 ST（ESC \），ESC 后不是反斜杠说明还在 OSC 里。
        self._state = "text" if char == "\\" else "osc"

    def _append(self, char: str) -> None:
        if len(self._chars) >= self._limit:
            # 截断是粘性的：后面的退格删不到已经丢弃的内容。
            self._truncated = True
            return
        self._chars.append(char)

    def _take(self) -> str:
        text = "".join(self._chars)
        return f"{text}{TRUNCATED_MARK}" if self._truncated else text

    def _reset(self) -> None:
        self._chars = []
        self._truncated = False

    def _end_line(self, lines: list[str]) -> None:
        text = self._take()
        self._reset()
        if text.strip():
            lines.append(text)


__all__ = ["CommandLineBuffer", "MAX_COMMAND_CHARS", "TRUNCATED_MARK"]
