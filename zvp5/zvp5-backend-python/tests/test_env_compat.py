"""``util.env`` 的读取语义：新名优先、显式空值不回落默认、布尔真值集合。"""

from __future__ import annotations

from util.env import getenv, getenv_bool


def test_reads_value():
    assert getenv("ZVP5_PORT", "1", environ={"ZVP5_PORT": "9000"}) == "9000"


def test_default_applies_when_unset():
    assert getenv("ZVP5_PORT", "8018", environ={}) == "8018"
    assert getenv("ZVP5_PORT", environ={}) is None


def test_explicit_empty_value_is_not_replaced_by_default():
    # 空字符串是「显式关掉」，不属于未设置。
    assert getenv("ZVP5_CORS_ALLOWED_ORIGINS", "https://a.example", environ={"ZVP5_CORS_ALLOWED_ORIGINS": ""}) == ""


def test_legacy_prefix_is_no_longer_honoured():
    """回落层已删除：只认 ZVP5_*，旧名不再生效。"""
    env = {"ZVMS_PORT": "9000"}
    assert getenv("ZVP5_PORT", "8018", environ=env) == "8018"


def test_bool_reader():
    assert getenv_bool("ZVP5_DB_PRE_PING", False, environ={"ZVP5_DB_PRE_PING": "true"}) is True
    assert getenv_bool("ZVP5_DB_PRE_PING", False, environ={"ZVP5_DB_PRE_PING": "0"}) is False
    assert getenv_bool("ZVP5_DB_PRE_PING", True, environ={}) is True
