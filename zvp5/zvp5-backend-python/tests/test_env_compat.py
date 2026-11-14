"""``util.env`` 的双名回落：新名优先、旧名兜底、显式空值不回落。"""

from __future__ import annotations

from util.env import getenv, getenv_bool, legacy_name


def test_legacy_name_maps_only_new_prefix():
    assert legacy_name("ZVP5_PORT") == "ZVMS_PORT"
    assert legacy_name("ZVP5_SSH_TARGETS") == "ZVMS_SSH_TARGETS"
    assert legacy_name("PORT") is None
    assert legacy_name("ZVMS_PORT") is None


def test_new_name_wins_over_legacy():
    env = {"ZVP5_PORT": "9000", "ZVMS_PORT": "8018"}
    assert getenv("ZVP5_PORT", "1", environ=env) == "9000"


def test_legacy_name_is_used_when_new_name_missing():
    assert getenv("ZVP5_PORT", "8018", environ={"ZVMS_PORT": "9000"}) == "9000"


def test_default_applies_when_neither_is_set():
    assert getenv("ZVP5_PORT", "8018", environ={}) == "8018"
    assert getenv("ZVP5_PORT", environ={}) is None


def test_explicit_empty_new_value_does_not_fall_back():
    # 空字符串是「显式关掉」，不属于未设置，不能退回旧名。
    env = {"ZVP5_CORS_ALLOWED_ORIGINS": "", "ZVMS_CORS_ALLOWED_ORIGINS": "https://a.example"}
    assert getenv("ZVP5_CORS_ALLOWED_ORIGINS", "", environ=env) == ""


def test_bool_reader_falls_back_to_legacy_name():
    assert getenv_bool("ZVP5_DB_PRE_PING", False, environ={"ZVMS_DB_PRE_PING": "true"}) is True
    assert getenv_bool("ZVP5_DB_PRE_PING", False, environ={"ZVMS_DB_PRE_PING": "0"}) is False
    assert getenv_bool("ZVP5_DB_PRE_PING", True, environ={}) is True
