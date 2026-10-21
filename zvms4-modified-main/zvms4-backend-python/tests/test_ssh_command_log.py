"""Web SSH 命令审计：行缓冲的状态机边界（纯离线）。

终端输入是按键流，不是命令行。审计要能回答「这次会话执行过什么」，就必须
把按键流重建成命令行——这里的每条用例都是重建规则的一个坑：

* 方向键是 ANSI 转义序列，吞不掉就会被记成 ``[A`` 这种假命令；
* 退格不处理，改错重打会把两版拼成一条不存在的命令；
* 粘贴多行必须逐行产出，而不是合成一条；
* 超长粘贴必须截断，否则一帧 8KB × 若干帧能直接把日志表灌爆。
"""

from __future__ import annotations

import pytest

from services.ssh_command_log import (
    MAX_COMMAND_CHARS,
    TRUNCATED_MARK,
    CommandLineBuffer,
)


def test_plain_line_is_emitted_on_carriage_return():
    buffer = CommandLineBuffer()

    assert buffer.feed("ls -la\r") == ["ls -la"]
    assert buffer.feed("pwd\n") == ["pwd"]


def test_crlf_counts_as_one_line_break():
    buffer = CommandLineBuffer()

    assert buffer.feed("whoami\r\n") == ["whoami"]
    # 跨帧拆开的 \r 与 \n 也不能产出空行。
    assert buffer.feed("id\r") == ["id"]
    assert buffer.feed("\n") == []


def test_partial_line_is_kept_until_enter():
    buffer = CommandLineBuffer()

    assert buffer.feed("echo hel") == []
    assert buffer.pending == "echo hel"
    assert buffer.feed("lo\r") == ["echo hello"]


def test_backspace_deletes_one_character():
    buffer = CommandLineBuffer()

    assert buffer.feed("sl\x7f\x7fls\r") == ["ls"]
    # \b 与 \x7f 等价（部分终端发前者）。
    assert buffer.feed("cat\x08\r") == ["ca"]


def test_backspace_on_empty_line_is_ignored():
    buffer = CommandLineBuffer()

    assert buffer.feed("\x7f\x7fls\r") == ["ls"]


def test_chinese_text_is_preserved():
    buffer = CommandLineBuffer()

    assert buffer.feed("echo 你好\r") == ["echo 你好"]


def test_ansi_csi_sequences_are_skipped():
    """方向键/删除键都是 CSI，不能被当成命令行内容。"""
    buffer = CommandLineBuffer()

    assert buffer.feed("ls\x1b[A\x1b[B\r") == ["ls"]


def test_ansi_csi_split_across_frames_stays_skipped():
    buffer = CommandLineBuffer()

    assert buffer.feed("ls\x1b") == []
    assert buffer.feed("[") == []
    assert buffer.feed("A\r") == ["ls"]


def test_ansi_osc_title_sequence_is_skipped():
    """OSC 以 BEL 结束（另见 ST 形式）。"""
    buffer = CommandLineBuffer()

    assert buffer.feed("\x1b]0;root@host\x07ls\r") == ["ls"]


def test_ansi_osc_st_terminator_is_skipped():
    buffer = CommandLineBuffer()

    assert buffer.feed("\x1b]2;title\x1b\\pwd\r") == ["pwd"]


def test_multi_line_paste_yields_every_line():
    buffer = CommandLineBuffer()

    lines = buffer.feed("cd /tmp\nls\nrm -rf ./x\r")

    assert lines == ["cd /tmp", "ls", "rm -rf ./x"]


def test_ctrl_c_marks_the_interrupted_line_and_clears_it():
    buffer = CommandLineBuffer()

    assert buffer.feed("sleep 10\x03") == ["sleep 10^C"]
    # 清空后不会把上一条被中断的命令再带进下一条。
    assert buffer.feed("ls\r") == ["ls"]


def test_ctrl_c_on_empty_line_records_nothing():
    buffer = CommandLineBuffer()

    assert buffer.feed("\x03") == []
    assert buffer.pending == ""


def test_blank_lines_are_not_recorded():
    buffer = CommandLineBuffer()

    assert buffer.feed("\r\n   \n\t\n") == []
    assert buffer.feed("ls\r") == ["ls"]


def test_overlong_line_is_truncated_and_marked():
    buffer = CommandLineBuffer()

    payload = "a" * (MAX_COMMAND_CHARS + 50)
    (line,) = buffer.feed(f"{payload}\r")

    assert line == "a" * MAX_COMMAND_CHARS + TRUNCATED_MARK
    assert len(line) < len(payload)


def test_truncation_is_sticky_for_backspaces():
    """截断后回删删不到已经丢弃的内容，所以标记必须留着。"""
    buffer = CommandLineBuffer()

    buffer.feed("a" * (MAX_COMMAND_CHARS + 10))
    (line,) = buffer.feed("\x7f\r")

    assert line.endswith(TRUNCATED_MARK)
    assert line.count("a") == MAX_COMMAND_CHARS


def test_truncated_ctrl_c_line_keeps_both_marks():
    buffer = CommandLineBuffer()

    buffer.feed("a" * (MAX_COMMAND_CHARS + 10))
    (line,) = buffer.feed("\x03")

    assert line == "a" * MAX_COMMAND_CHARS + TRUNCATED_MARK + "^C"


def test_tab_and_other_control_chars_are_ignored():
    buffer = CommandLineBuffer()

    assert buffer.feed("ls\t-la\x01\r") == ["ls-la"]


@pytest.mark.parametrize("chunk", ["\x1b", "\x1b[", "\x1b]", "\x1b]0;x"])
def test_dangling_escape_never_leaks_into_next_line(chunk):
    buffer = CommandLineBuffer()

    buffer.feed(chunk)
    buffer.feed("ls\r")

    produced = buffer.feed("") or []
    assert all("\x1b" not in line for line in produced)
    assert "\x1b" not in buffer.pending


def test_unterminated_escape_cannot_swallow_the_session_forever():
    """未闭合的 OSC 不能变成审计盲区：超长后整行作废并重同步。"""
    buffer = CommandLineBuffer()

    buffer.feed("\x1b]" + "x" * 500)
    # 污染行连同换行一起丢掉，不编造一条拼接命令。
    assert buffer.feed("bogus\r") == []
    # 重同步之后恢复正常记录。
    assert buffer.feed("ls\r") == ["ls"]

