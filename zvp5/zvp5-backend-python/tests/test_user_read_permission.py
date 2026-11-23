"""``validate_read_user_permission`` 的班级边界（离线，不连 ADB）。

旧实现无条件放行：任何登录学生都能拿 OID 读全校同学的义工时长与参与记录。
这里钉住收紧后的口径：本人 / admin / department / 同班可读，跨班普通学生 403。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from db import backends
from util.permission.user import validate_read_user_permission

ACTOR = "000000000000000000000001"
CLASSMATE = "000000000000000000000002"
OUTSIDER = "000000000000000000000003"
CLASS_A = "000000000000000000000101"
CLASS_B = "000000000000000000000102"


class _UsersRepo:
    async def get_by_oid(self, oid: str):
        return {
            CLASSMATE: {"_id": CLASSMATE, "class_id": CLASS_A},
            OUTSIDER: {"_id": OUTSIDER, "class_id": CLASS_B},
        }.get(oid)


@pytest.fixture(autouse=True)
def users_repo(monkeypatch):
    monkeypatch.setattr(backends, "get_users_repo", lambda *a, **k: _UsersRepo())


def _actor(per: list[str]) -> dict:
    return {"id": ACTOR, "per": per, "class_id": CLASS_A}


@pytest.mark.asyncio
async def test_self_is_always_readable() -> None:
    assert await validate_read_user_permission(_actor(["student"]), ACTOR) is True


@pytest.mark.asyncio
async def test_admin_and_department_keep_school_wide_reads() -> None:
    assert await validate_read_user_permission(_actor(["admin"]), OUTSIDER) is True
    assert await validate_read_user_permission(_actor(["department"]), OUTSIDER) is True


@pytest.mark.asyncio
async def test_classmate_is_readable() -> None:
    assert await validate_read_user_permission(_actor(["student"]), CLASSMATE) is True


@pytest.mark.asyncio
async def test_other_class_student_is_rejected() -> None:
    with pytest.raises(HTTPException) as failure:
        await validate_read_user_permission(_actor(["student"]), OUTSIDER)

    assert failure.value.status_code == 403
    assert failure.value.detail == "权限不足"


@pytest.mark.asyncio
async def test_malformed_target_is_rejected_not_500() -> None:
    with pytest.raises(HTTPException) as failure:
        await validate_read_user_permission(_actor(["student"]), "20770115")

    assert failure.value.status_code == 403
