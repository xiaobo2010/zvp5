"""活动文本列的宽度/字符校验与成员时长范围（离线单测）。

`activities` 表的列宽（``db/oracle/ddl/V001__initial_schema.sql:116-125``）：
``name``/``place`` 各 ``VARCHAR2(256 CHAR)``、``approver`` ``VARCHAR2(24 CHAR)``。
超宽直写会撞 ORA-12899（500），所以三个入口（v2 建活动、v2 改信息、v3 建活动、
Excel 导入）共用一个校验器，越界统一 400、绝不静默截断；NUL 也拒绝（Oracle 会
把它当字符串终止符吞掉，落库值就与提交值不一致）。

成员时长同理：``0``/负数/NaN/Infinity 会污染聚合（NaN 进缓存后所有求和都变
NaN），入口按 ``Field(gt=0, allow_inf_nan=False)`` 拦成 422。

    cd <repo root> && .venv/bin/python -m pytest tests/test_activity_validation.py -q
"""

from __future__ import annotations

import math

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from routers import activities_v2_router as v2
from routers import activities_v3_router as v3
from util.validation import (
    ACTIVITY_APPROVER_MAX_LENGTH,
    ACTIVITY_NAME_MAX_LENGTH,
    ACTIVITY_PLACE_MAX_LENGTH,
    validate_activity_approver,
    validate_activity_name,
    validate_activity_place,
)

ADMIN = {"id": "000000000000000000000001", "per": ["admin"]}


def _activity(**overrides) -> v2.Activity:
    payload = {
        "_id": "000000000000000000000010",
        "type": "on-campus",
        "name": "支教活动",
        "description": "说明",
        "date": "2026-01-01T00:00:00",
        "createdAt": "2026-01-01T00:00:00",
        "updatedAt": "2026-01-01T00:00:00",
        "appointee": ADMIN["id"],
        "approver": "member",
        "creator": ADMIN["id"],
        "status": "pending",
        "place": "学校",
        "origin": "labor",
    }
    payload.update(overrides)
    return v2.Activity.model_validate(payload)


# --- 校验器本身 -----------------------------------------------------------


def test_limits_match_the_ddl() -> None:
    assert (ACTIVITY_NAME_MAX_LENGTH, ACTIVITY_PLACE_MAX_LENGTH) == (256, 256)
    assert ACTIVITY_APPROVER_MAX_LENGTH == 24


def test_name_accepts_exactly_the_column_width() -> None:
    assert validate_activity_name("名" * ACTIVITY_NAME_MAX_LENGTH) == (True, "")


def test_name_rejects_one_character_over_the_width() -> None:
    valid, reason = validate_activity_name("名" * (ACTIVITY_NAME_MAX_LENGTH + 1))
    assert valid is False
    assert reason == "Name should be at most 256 characters"


@pytest.mark.parametrize(
    ("validator", "limit", "label"),
    [
        (validate_activity_name, ACTIVITY_NAME_MAX_LENGTH, "Name"),
        (validate_activity_place, ACTIVITY_PLACE_MAX_LENGTH, "Place"),
        (validate_activity_approver, ACTIVITY_APPROVER_MAX_LENGTH, "Approver"),
    ],
)
def test_nul_is_rejected_everywhere(validator, limit, label) -> None:
    valid, reason = validator("a\x00b")

    assert valid is False
    assert reason == f"{label} should not contain NUL characters"


def test_place_and_approver_allow_null() -> None:
    assert validate_activity_place(None) == (True, "")
    assert validate_activity_approver(None) == (True, "")


def test_approver_width_is_24_not_256() -> None:
    assert validate_activity_approver("a" * 24) == (True, "")
    valid, reason = validate_activity_approver("a" * 25)
    assert valid is False
    assert reason == "Approver should be at most 24 characters"


# --- v2 建活动 ------------------------------------------------------------


@pytest.fixture
def no_db(monkeypatch):
    """校验失败必须发生在任何仓库/事务之前：这里让它们一碰就炸。"""

    def boom(*args, **kwargs):
        raise AssertionError("校验失败时不应触达仓库")

    monkeypatch.setattr(v2.backends, "get_activities_repo", boom)
    monkeypatch.setattr(v2, "session_scope", boom)
    return True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "detail"),
    [
        ({"name": "名" * 257}, "Name should be at most 256 characters"),
        ({"place": "地" * 257}, "Place should be at most 256 characters"),
        ({"approver": "a" * 25}, "Approver should be at most 24 characters"),
        ({"name": "a\x00b"}, "Name should not contain NUL characters"),
    ],
)
async def test_v2_create_rejects_overlong_columns(no_db, overrides, detail) -> None:
    with pytest.raises(HTTPException) as failure:
        await v2.create_activity_v2(_activity(**overrides), ADMIN, log=None)

    assert failure.value.status_code == 400
    assert failure.value.detail == detail


@pytest.mark.asyncio
async def test_v3_create_rejects_an_overlong_name(monkeypatch) -> None:
    def boom(*args, **kwargs):
        raise AssertionError("校验失败时不应触达仓库")

    monkeypatch.setattr(v3.backends, "get_activities_repo", boom)
    payload = v3.CreateActivityRequest(
        name="名" * 257, description="说明", type="on-campus"
    )

    with pytest.raises(HTTPException) as failure:
        await v3.create_activity_v3(payload, ADMIN, log=None)

    assert failure.value.status_code == 400
    assert failure.value.detail == "Name should be at most 256 characters"


@pytest.mark.asyncio
async def test_v3_create_accepts_an_in_range_name(monkeypatch) -> None:
    created: list[tuple] = []

    class _Repo:
        async def create(self, *args):
            created.append(args)
            return "0000000000000000000000aa"

    class _Log:
        def with_text(self, *args, **kwargs):
            return self

        async def insert_log(self):
            return None

    monkeypatch.setattr(v3.backends, "get_activities_repo", lambda *a, **k: _Repo())
    monkeypatch.setattr(v3, "get_user_display_name", _fake_name)

    result = await v3.create_activity_v3(
        v3.CreateActivityRequest(
            name="名" * 256, description="说明", type="on-campus"
        ),
        ADMIN,
        log=_Log(),
    )

    assert result == {"id": "0000000000000000000000aa"}
    assert created and created[0][0] == "名" * 256


async def _fake_name(oid):
    return "管理员"


# --- 成员时长 --------------------------------------------------------------


@pytest.mark.parametrize("duration", [0, -1, -0.5, math.nan, math.inf, -math.inf])
def test_member_record_duration_must_be_finite_and_positive(duration) -> None:
    with pytest.raises(ValidationError):
        v2.UpdateUserRecord(duration=duration, mode="on-campus")


def test_member_record_duration_accepts_a_positive_value() -> None:
    record = v2.UpdateUserRecord(duration=0.5, mode="social-practice")

    assert record.duration == 0.5
    assert record.mode == "social-practice"
