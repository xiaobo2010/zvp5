"""``quota.consume`` 的 ``want_state`` 开关（A-M3a）。

钉两件事：

* ``want_state=False`` 的**成功分支只发一条 UPDATE**：扣完不再回读 ``used/limit``
  （每轮对话少一次跨洋 ``SELECT``），返回的 used/limit 恒为 0；
* 首次使用（行还不存在）仍然要建行——成败取决于存在性查询，这条查询不能因为
  「不看展示值」就省掉，否则账号会永远扣不上费。

另有一条源码形状断言：四个**丢弃返回值**的调用点必须传 ``want_state=False``，
而 OCR（429 回包要用 used/limit）必须保持默认。
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import oracle

from db import backends
from db.repos.oracle import ai_quota as ai_quota_repo
from routers.ai import common as ai_common
from services.agent import executor
from services.ai import ocr, textgen
from services.ai import quota as quota_service

DIALECT = oracle.dialect()
USER_OID = "000000000000000000000002"
PERIOD = "202610"
TS = datetime(2026, 10, 23, 1, 2, 3)


class _Result:
    """只够 ``_consume_in_session`` 用：``rowcount`` 与 ``first()``。"""

    def __init__(self, *, rowcount: int = 0, row: dict | None = None) -> None:
        self.rowcount = rowcount
        self._row = row

    def first(self):
        if self._row is None:
            return None
        return SimpleNamespace(_mapping=self._row)


class _NullSavepoint:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _ScriptedSession:
    """按顺序回放结果，并记录实际会发给 Oracle 的语句。"""

    def __init__(self, results: list[_Result]) -> None:
        self._results = list(results)
        self.statements: list[str] = []

    async def execute(self, stmt):
        self.statements.append(
            " ".join(
                str(
                    stmt.compile(dialect=DIALECT, compile_kwargs={"literal_binds": True})
                ).split()
            )
        )
        return self._results.pop(0)

    def begin_nested(self):
        return _NullSavepoint()


def _verbs(statements: list[str]) -> list[str]:
    return [text.split(" ", 1)[0].upper() for text in statements]


@pytest.mark.asyncio
async def test_want_state_false_sends_only_the_update() -> None:
    session = _ScriptedSession([_Result(rowcount=1)])

    result = await ai_quota_repo._consume_in_session(  # noqa: SLF001
        session, USER_OID, PERIOD, "af", 2, 100, TS, want_state=False
    )

    assert result == (True, 0, 0)
    # 一条语句、且不是 SELECT：成功分支的回读被省掉了。
    assert _verbs(session.statements) == ["UPDATE"]
    assert "SELECT" not in session.statements[0].upper()


@pytest.mark.asyncio
async def test_default_still_reads_back_used_and_limit() -> None:
    session = _ScriptedSession(
        [_Result(rowcount=1), _Result(row={"used": 3, "limit_count": 100})]
    )

    result = await ai_quota_repo._consume_in_session(  # noqa: SLF001
        session, USER_OID, PERIOD, "af", 2, 100, TS
    )

    assert result == (True, 3, 100)
    assert _verbs(session.statements) == ["UPDATE", "SELECT"]


@pytest.mark.asyncio
async def test_want_state_false_reports_a_refusal_without_the_state() -> None:
    """额度不足：allowed=False，但 used/limit 不再有意义（恒 0）。"""
    session = _ScriptedSession(
        [_Result(rowcount=0), _Result(row={"used": 100, "limit_count": 100})]
    )

    result = await ai_quota_repo._consume_in_session(  # noqa: SLF001
        session, USER_OID, PERIOD, "af", 2, 100, TS, want_state=False
    )

    assert result == (False, 0, 0)


@pytest.mark.asyncio
async def test_want_state_false_still_creates_the_row_on_first_use() -> None:
    """存在性查询是**建行分支的判据**，不能因为不回读就省掉。

    少了 INSERT，这个账号之后每一次扣减都会落进「行不存在」的同一个分支，
    等于永远不扣费——而调用方丢掉了返回值，根本看不出来。
    """
    session = _ScriptedSession(
        [
            _Result(rowcount=0),  # 第一次扣减：行还不存在
            _Result(row=None),  # 存在性查询：确实没有
            _Result(rowcount=1),  # INSERT 建行
            _Result(rowcount=1),  # 重试扣减：成功
        ]
    )

    result = await ai_quota_repo._consume_in_session(  # noqa: SLF001
        session, USER_OID, PERIOD, "af", 2, 100, TS, want_state=False
    )

    assert result == (True, 0, 0)
    assert _verbs(session.statements) == ["UPDATE", "SELECT", "INSERT", "UPDATE"]


def test_discarding_call_sites_opt_out_and_ocr_keeps_the_default() -> None:
    """调用点的选择是契约的一部分：不看返回值的路径才允许省掉回读。"""
    for func in (
        executor._summarize_history,  # noqa: SLF001
        executor.run,
        ai_common._charge,
        textgen.suggest_ssh_command,
    ):
        source = inspect.getsource(func)
        assert "want_state=False" in source, func.__qualname__

    for func in (ocr.enqueue, ocr.recognize_bytes):
        assert "want_state" not in inspect.getsource(func), func.__qualname__


def test_service_forwards_want_state_to_the_repo(monkeypatch) -> None:
    calls: list[bool] = []

    class _Repo:
        async def consume(
            self, user_oid, period, kind, amount, default_limit, want_state=True
        ):
            calls.append(want_state)
            return True, 7, 100

    monkeypatch.setattr(backends, "get_ai_quota_repo", lambda session=None: _Repo())

    async def run() -> None:
        assert await quota_service.consume(USER_OID, "af", 1) == (True, 7, 100)
        assert await quota_service.consume(
            USER_OID, "af", 1, want_state=False
        ) == (True, 7, 100)

    asyncio.run(run())
    # 默认值一路透传到仓储：OCR 那条路径（默认 True）仍然拿得到 used/limit。
    assert calls == [True, False]
