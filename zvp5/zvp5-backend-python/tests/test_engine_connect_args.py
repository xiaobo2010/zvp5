"""连接参数必须是驱动真正接受的参数。

2026-11-07 生产事故：``call_timeout`` 被当成 connect 关键字传给
``oracledb.connect_async()``，而 python-oracledb 26.x 只把它暴露成**连接属性**，
服务启动即 ``TypeError: connect_async() got an unexpected keyword argument
'call_timeout'``，``/api/health`` 直接 503。

本地 venv 里没有真实 ADB，测试永远不会真连库，所以驱动签名必须被当成硬约束
钉在用例里——否则「本地绿、生产炸」还会再来一次。
"""

from __future__ import annotations

import inspect

import pytest

from db.oracle import config as oracle_config
from db.oracle.engine import _call_timeout_ms, _connect_args


def _cfg() -> oracle_config.OracleConfig:
    return oracle_config.OracleConfig(
        active="prod",
        user="u",
        password="p",
        dsn="dsn",
        wallet_dir="",
        wallet_password="",
    )


def test_connect_args_are_accepted_by_the_driver() -> None:
    """``connect_args`` 里的每个键都必须出现在驱动的 connect 签名里。"""
    oracledb = pytest.importorskip("oracledb")
    accepted = set(inspect.signature(oracledb.connect_async).parameters)
    args = _connect_args(_cfg())
    unknown = sorted(set(args) - accepted)
    assert not unknown, (
        f"connect_args 含驱动不接受的参数 {unknown}；"
        f"驱动版本 {oracledb.__version__} 会直接抛 TypeError"
    )


def test_call_timeout_is_not_a_connect_keyword() -> None:
    """``call_timeout`` 只能作为连接属性设置，不能进 ``connect_args``。"""
    oracledb = pytest.importorskip("oracledb")
    assert "call_timeout" not in inspect.signature(oracledb.connect_async).parameters
    assert "call_timeout" not in _connect_args(_cfg())
    assert hasattr(oracledb.AsyncConnection, "call_timeout")


def test_call_timeout_ms_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("ZVP5_DB_CALL_TIMEOUT", "12.5")
    assert _call_timeout_ms() == 12500
    monkeypatch.setenv("ZVP5_DB_CALL_TIMEOUT", "0")
    assert _call_timeout_ms() == 0
