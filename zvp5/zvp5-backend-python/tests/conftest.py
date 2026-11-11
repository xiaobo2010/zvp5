"""全局测试隔离：默认把「维护状态库」指到临时目录，绝不写进仓库。

为什么必须这么做：``tasks.retention.purge_expired_records()`` / ``purge_now()``
现在会让服务器**进入维护态**，也就是写 ``db/system_state.py`` 的
``system_state.db``（后端根目录）。用例若不隔离 ``ZVMS_SYSTEM_DB``，测试就会在
仓库目录里生成 ``system_state.db``：既污染工作区，又可能被 rsync 部署同步到
生产、**覆盖线上维护状态**。

作用域与覆盖方式：function 级 ``autouse``。按 pytest 规则，同作用域下 autouse
fixture 先于用例自己的 fixture 实例化，所以这里给的是**默认值**：用例（或模块内
fixture）之后 ``monkeypatch.setenv("ZVMS_SYSTEM_DB", ...)`` 会覆盖它，monkeypatch
按写入顺序回滚，两者互不干扰 —— 需要跨用例共享同一个状态库的用例照旧自建。

顺带把仓库根放进 ``sys.path``，这样即使从别处调用 pytest（不走 ``python -m``）、
conftest 需要 import ``db`` 时也不会因找不到包而崩。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]  # tests/conftest.py -> 后端根
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from db import system_state  # noqa: E402  (sys.path 引导之后再导入)
from db import cache as _db_cache  # noqa: E402


@pytest.fixture(autouse=True)
def _redis_isolated() -> None:
    """每个用例前后都清掉测试注入的 Redis 替身，避免跨用例串味。"""
    _db_cache._reset_for_tests()
    yield
    _db_cache._reset_for_tests()


@pytest.fixture
def quiet_ai_governance(monkeypatch):
    """AI 请求的治理旁路（prompt 留存 / 突发风控）默认不连库。

    ``routers.ai_router`` 与 ``services.agent.executor`` 在请求入口/完成处各挂了一个
    **旁路**：受控 prompt 留存（``AI_PROMPT_AUDIT``）与 ``ai_chat_burst`` 风控。
    两者都用真实仓储，而单元测试的仓储是逐文件打桩的——漏桩就会真的去连 ADB
    （有 DSN 的环境甚至会写库）。这个 fixture 把两处**明确的治理副作用**一起
    屏蔽，需要验证它们的用例（``test_ai_prompt_capture`` / ``test_ai_chat_burst``）
    自己直接调服务层，不走这个 fixture。
    """
    from services import risk_monitor
    from services.ai import prompt_audit

    async def _no_capture(**_kwargs):
        return None

    async def _no_evaluate(*_args, **_kwargs):
        return None

    monkeypatch.setattr(prompt_audit, "capture", _no_capture)
    monkeypatch.setattr(risk_monitor, "evaluate", _no_evaluate)


@pytest.fixture(autouse=True)
def _isolated_state_db(tmp_path_factory, monkeypatch) -> Path:
    """每个用例拿到自己的临时状态库；想覆盖就在用例里再 setenv 一次。"""
    path = tmp_path_factory.mktemp("system_state") / "system_state.db"
    monkeypatch.setenv("ZVMS_SYSTEM_DB", str(path))
    # 缓存按库路径区分，正常不会串味；这里再显式失效一次，保证「换库即重读」。
    system_state.invalidate_cache()
    return path


@pytest.fixture(autouse=True)
def _files_jail_allows_tmp(monkeypatch) -> None:
    """文件根目录 jail 的测试豁免：用例都在 pytest 的 tmp 目录里造文件。

    生产白名单仍是 ``services.file_ops.ALLOWED_ROOTS``（站点根 + 家目录）；这里只
    把系统临时目录并进来，否则所有 ``tmp_path`` 用例都会撞 403
    ``FILES_ROOT_FORBIDDEN``。jail 的边界用例（/etc、/root、/home2、软链逃逸）
    不受影响：它们断言的是白名单**之外**的路径。
    """
    import tempfile

    from services import file_ops

    monkeypatch.setattr(
        file_ops,
        "ALLOWED_ROOTS",
        (*file_ops.ALLOWED_ROOTS, os.path.realpath(tempfile.gettempdir())),
    )


@pytest.fixture(autouse=True)
def _fresh_login_throttle():
    """登录频控的窗口是进程内的：不清就会让用例之间互相把额度用光。"""
    from util import login_throttle

    login_throttle.reset_state()
    yield
    login_throttle.reset_state()


@pytest.fixture(autouse=True)
def _fresh_read_budget():
    """重查询读预算同样是进程内窗口，用例之间不该互相扣除额度。"""
    from util import read_throttle

    read_throttle.reset_state()
    yield
    read_throttle.reset_state()


@pytest.fixture(autouse=True)
def _fresh_credential_replay():
    """凭据一次性消费表也是进程内状态：跨用例残留会把新凭据误判成重放。"""
    from util import credential_replay

    credential_replay.reset_state()
    yield
    credential_replay.reset_state()
