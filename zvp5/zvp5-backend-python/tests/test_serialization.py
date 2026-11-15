"""``util.serialization`` / ``util.oid`` / ``db.repos.pagination`` 的收敛断言。

这三个模块是把散落在 routers/services/db 里的重复实现收拢出来的，所以测试的
重点是「逐字等价」而不是「好看」：每个新函数都和它替换掉的旧内联表达式逐一对拍
（旧表达式原样抄在 ``LEGACY`` 表里），参数表覆盖 naive/aware/微秒/None/字符串/
非时间类型这些真实出现过的形状。
"""

from __future__ import annotations

from datetime import date, datetime, timezone, timedelta

import pytest
from fastapi import HTTPException

from db.ids import InvalidOid, normalize_oid
from db.repos.pagination import page_args, page_bounds
from util import oid as oid_module
from util import object_id as object_id_module
from util import serialization as ser

# 旧实现里出现过的每一种「值」形状。``date`` 也放进来：``isinstance(d, datetime)``
# 对 ``date`` 为假，多数调用点因此回 ``None``，这条差异必须被钉住。
PROBES = [
    datetime(2026, 1, 2, 3, 4, 5, 678901),
    datetime(2026, 1, 2, 3, 4, 5),
    datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=8))),
    date(2026, 1, 2),
    None,
    "",
    "2026-01-02 03:04:05",
    "2026-01-02T03:04:05.123456",
    123,
    0,
]


def _legacy_iso(value):
    """``routers/ai_schedule_router._iso``（替换前）。"""
    return value.isoformat() if isinstance(value, datetime) else None


def _legacy_iso_seconds(value):
    """``ban_router`` / ``ban_violations_router`` / ``notifications_router`` /
    ``users_v3_router._iso_or_none`` 的 ``_iso``（替换前）。"""
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return None


def _legacy_iso_space_seconds(value):
    """``tools_governance._iso`` 的序列化那一步（替换前）。"""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return None


def _legacy_iso_text(value):
    """``attachments_router._iso`` / ``db_monitor._timestamp``（替换前）。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _legacy_iso_str(value):
    """``services/ai/conversations._iso`` / ``run_queue._iso``（替换前）。"""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return value if isinstance(value, str) else None


def _legacy_iso_str_nonempty(value):
    """``tools_ops_extra._iso``（替换前）。"""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, str) and value:
        return value
    return None


def _legacy_iso_utc(value):
    """``db_monitor._utc_timestamp``（替换前）。"""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return _legacy_iso_text(value)


@pytest.mark.parametrize(
    "helper, legacy",
    [
        (ser.iso, _legacy_iso),
        (ser.iso_seconds, _legacy_iso_seconds),
        (ser.iso_space_seconds, _legacy_iso_space_seconds),
        (ser.iso_text, _legacy_iso_text),
        (ser.iso_str, _legacy_iso_str),
        (ser.iso_str_nonempty, _legacy_iso_str_nonempty),
        (ser.iso_utc, _legacy_iso_utc),
    ],
    ids=[
        "iso",
        "iso_seconds",
        "iso_space_seconds",
        "iso_text",
        "iso_str",
        "iso_str_nonempty",
        "iso_utc",
    ],
)
@pytest.mark.parametrize("value", PROBES, ids=[repr(v) for v in PROBES])
def test_iso_helpers_match_legacy_expressions(helper, legacy, value) -> None:
    """新函数与旧内联表达式对同一个值必须产出**同一个字符串**（含 ``None``）。"""
    assert helper(value) == legacy(value)


@pytest.mark.parametrize(
    "ts",
    [0, 1_700_000_000, 1_700_000_000.5],
)
def test_iso_epoch_utc_matches_legacy_expression(ts) -> None:
    """``perf_sampler._iso``（替换前）逐字等价。"""
    legacy = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    assert ser.iso_epoch_utc(ts) == legacy


def test_iso_utc_labels_naive_values_but_keeps_aware_offsets() -> None:
    naive = datetime(2026, 1, 2, 3, 4, 5)
    aware = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone(timedelta(hours=8)))
    assert ser.iso_utc(naive) == "2026-01-02T03:04:05+00:00"
    assert ser.iso_utc(aware) == "2026-01-02T03:04:05+08:00"


# ---------------------------------------------------------------------------
# util.oid：唯一一份 oid 校验
# ---------------------------------------------------------------------------


def test_validate_object_id_is_the_single_implementation() -> None:
    """``util.object_id`` 只是 re-export，不存在第二份实现。"""
    assert object_id_module.validate_object_id is oid_module.validate_object_id


def test_validate_object_id_lowercases_and_keeps_the_400_contract() -> None:
    assert oid_module.validate_object_id("ABCDEFABCDEFABCDEFABCDEF") == (
        "abcdefabcdefabcdefabcdef"
    )
    with pytest.raises(HTTPException) as excinfo:
        oid_module.validate_object_id("not-an-oid")
    assert excinfo.value.status_code == 400
    # 文案是前端契约（旧 utils.py 的原文），不能变成全局处理器的中文。
    assert excinfo.value.detail == "Invalid Object ID"


@pytest.mark.parametrize(
    "value, length, expected",
    [
        ("abcdefabcdefabcdefabcdef", 24, "abcdefabcdefabcdefabcdef"),
        ("ABCDEFABCDEFABCDEFABCDEF", 24, "abcdefabcdefabcdefabcdef"),
        ("  abcdefabcdefabcdefabcdef  ", 24, "abcdefabcdefabcdefabcdef"),
        ("abcdefabcdefabcdefabcdef", 32, None),
        ("a" * 32, 32, "a" * 32),
        ("A" * 32, 32, "a" * 32),
        ("a" * 31, 32, None),
        ("g" * 24, 24, None),
        ("a" * 24 + "\n", 24, "a" * 24),  # strip 之后仍合法
        (None, 24, None),
        (None, 32, None),
    ],
)
def test_hex_id_or_none(value, length, expected) -> None:
    assert oid_module.hex_id_or_none(value, length=length) == expected


def test_invalid_oid_is_still_a_value_error() -> None:
    """``InvalidOid`` 必须继续是 ``ValueError``（error_handlers 的 400 依赖它），
    而 HTTP 封装层仍把它翻成 ``HTTPException``（异常类型也是契约）。"""
    with pytest.raises(ValueError):
        normalize_oid("nope")
    assert issubclass(InvalidOid, ValueError)
    with pytest.raises(HTTPException):
        oid_module.validate_object_id("nope")


# ---------------------------------------------------------------------------
# db.repos.pagination
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "page, size, kwargs, expected",
    [
        (1, 10, {}, (0, 10)),
        (3, 20, {}, (40, 20)),
        ("2", "5", {}, (5, 5)),
        (0, 10, {}, (0, 10)),
        (-3, 10, {}, (0, 10)),
        (1, 0, {}, (0, 1)),
        (1, -5, {}, (0, 1)),
        (None, None, {}, (0, 1)),
        (None, None, {"default_size": 20}, (0, 20)),
        ("x", "y", {"default_size": 20, "max_size": 50}, (0, 20)),
        (1, 10_000, {"max_size": 100}, (0, 100)),
        (2, 10_000, {"max_size": 100}, (100, 100)),
        (2, 100, {"max_size": 50}, (50, 50)),
    ],
)
def test_page_args_preserves_defaults_and_ceilings(page, size, kwargs, expected) -> None:
    """钳制只有「至少 1」与调用点自己给的 ``max_size``，没有全局一刀切。"""
    assert page_args(page, size, **kwargs) == expected
    # offset/limit 与 page_bounds 必须自洽（同一个归一结果）。
    page_no, size_no = page_bounds(page, size, **kwargs)
    assert (page_no - 1) * size_no == expected[0]
    assert size_no == expected[1]


def test_endpoint_defaults_are_unchanged() -> None:
    """各端点的默认值/上限仍由调用点自己决定，这里只钉住两个有代表性的口径。"""
    from db.repos.oracle import app_logs
    from services.ai import quota_requests

    assert app_logs.PAGE_SIZE_MAX == 100
    assert quota_requests._page_args("bad", "bad") == (1, 20)
    assert quota_requests._page_args(1, 10_000) == (1, 50)
    assert quota_requests.MAX_PAGE_SIZE == 50


def test_shared_helpers_are_imported_not_redefined() -> None:
    """收敛后的调用点必须指向同一个函数对象（不是各自又抄了一份）。"""
    from routers import ai_schedule_router, ban_router, notifications_router
    from services import db_monitor, perf_sampler
    from services.agent import run_queue, tools_ops_extra

    assert ban_router.iso_seconds is ser.iso_seconds
    assert notifications_router.iso_seconds is ser.iso_seconds
    assert ai_schedule_router.iso is ser.iso
    assert run_queue.iso_str is ser.iso_str
    assert tools_ops_extra.iso_str_nonempty is ser.iso_str_nonempty
    assert db_monitor.iso_text is ser.iso_text
    assert db_monitor.iso_utc is ser.iso_utc
    assert perf_sampler.iso_epoch_utc is ser.iso_epoch_utc
