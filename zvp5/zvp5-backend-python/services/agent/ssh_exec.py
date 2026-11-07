"""单次 SSH 命令：给 agent 的 ``ssh_exec`` 工具用（契约 §4.2）。

与 ``ssh_bridge.SshSession`` 的区别是**没有 PTY、没有会话状态**：一条命令一个
连接，跑完就关。这样「模型能连哪台机」仍然只由 ``ssh_bridge.load_target()``
决定，也避免了把交互式终端的并发配额借给自动化调用。

三条硬约束：

* ``check=False``：非 0 退出码是**结果**而不是异常（模型要看 stderr 才能纠偏）；
* 输出按 stdout / stderr **分别**截断到 8KB，各自标 ``truncated``；
* 命令长度与超时在上游就拒绝，不把「跑 10 分钟的命令」变成一次挂死的 run。
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any

from services import ssh_bridge

from .tool_impl import AgentError

_logger = logging.getLogger(__name__)

# 契约 §4.2：command ≤1024，timeout ≤60；输出 stdout/stderr 各 8KB。
MAX_COMMAND_CHARS = 1024
MAX_TIMEOUT_SECONDS = 60.0
DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_STREAM_CHARS = 8192


def _bad_argument(message: str) -> AgentError:
    return AgentError("AI_AGENT_BAD_ARGUMENT", message, 400)


def _decode(raw: Any) -> str:
    """asyncssh 在 ``encoding=None`` 时给 bytes；替身/别的实现可能直接给 str。"""
    if raw is None:
        return ""
    if isinstance(raw, bytes):
        return raw.decode("utf-8", "replace")
    return str(raw)


def _clip(text: str) -> tuple[str, bool]:
    if len(text) <= MAX_STREAM_CHARS:
        return text, False
    return text[:MAX_STREAM_CHARS], True


async def _close_quietly(connection: Any) -> None:
    """关闭失败不影响结果：对端可能已经先断了。``close()`` 可能是协程。"""
    try:
        closed = connection.close()
    except Exception:  # noqa: BLE001 - 清理路径不抛
        return
    if inspect.isawaitable(closed):
        try:
            await closed
        except Exception:  # noqa: BLE001 - 清理路径不抛
            return


async def run_once(command: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict:
    """跑一条命令并返回 ``{exit_status, stdout, stderr, truncated, target}``。"""
    if not isinstance(command, str) or not command.strip():
        raise _bad_argument("command must be a non-empty string")
    if len(command) > MAX_COMMAND_CHARS:
        raise _bad_argument(f"command must be <= {MAX_COMMAND_CHARS} characters")

    try:
        limit = float(timeout)
    except (TypeError, ValueError):
        raise _bad_argument("timeout must be a number") from None
    if limit != limit or limit <= 0:  # NaN / 非正数
        raise _bad_argument("timeout must be > 0")
    if limit > MAX_TIMEOUT_SECONDS:
        raise _bad_argument(f"timeout must be <= {MAX_TIMEOUT_SECONDS:g} seconds")

    target = ssh_bridge.load_target()
    connection = await ssh_bridge.connect()
    try:
        try:
            result = await connection.run(command, check=False, timeout=limit)
        except asyncio.TimeoutError:
            raise AgentError(
                "AI_AGENT_SSH_TIMEOUT",
                f"ssh command timed out after {limit:g}s",
                504,
            ) from None
        except AgentError:
            raise
        except Exception as exc:  # noqa: BLE001 - 连接/通道故障统一翻译
            # 上游异常原文可能带 known_hosts / 私钥路径，模型不需要也不该看到；
            # 只把类型名交出去，细节留在服务端日志（同 system_ssh_router 的姿势）。
            _logger.exception("ssh command failed: %s", type(exc).__name__)
            raise AgentError(
                "AI_AGENT_SSH_FAILED",
                f"ssh command failed: {type(exc).__name__}",
                502,
            ) from None

        stdout, stdout_truncated = _clip(_decode(getattr(result, "stdout", b"")))
        stderr, stderr_truncated = _clip(_decode(getattr(result, "stderr", b"")))
        status = getattr(result, "exit_status", None)
        try:
            exit_status = -1 if status is None else int(status)
        except (TypeError, ValueError):
            exit_status = -1

        return {
            "exit_status": exit_status,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": {"stdout": stdout_truncated, "stderr": stderr_truncated},
            "target": f"{target.user}@{target.host}:{target.port}",
        }
    finally:
        await _close_quietly(connection)


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_COMMAND_CHARS",
    "MAX_STREAM_CHARS",
    "MAX_TIMEOUT_SECONDS",
    "run_once",
]
