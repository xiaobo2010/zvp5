"""``util.permission.user.credential_change_forbidden`` 的离线单测。

纯判定函数、不查库：直接喂 ``(actor_oid, target_per, target_oid)`` 三维矩阵，
覆盖任务书规定的 F1 口径——本人放行；他人改 admin 级账号 → 拦；其余等级维持
现状（放行）。不连数据库、不发 HTTP。
"""

from __future__ import annotations

from util.permission.user import credential_change_forbidden

ADMIN_OID = "64b7f0c2e1a3b4c5d6e7f801"
OTHER_ADMIN_OID = "64b7f0c2e1a3b4c5d6e7f802"
DEPT_OID = "64b7f0c2e1a3b4c5d6e7f803"
STUDENT_OID = "64b7f0c2e1a3b4c5d6e7f804"


class _OidLike:
    """模拟上层传入的 ObjectId——语义上与 str 相同，str() 后应判为同一人。"""

    def __init__(self, value: str) -> None:
        self.value = value

    def __str__(self) -> str:
        return self.value


def test_self_change_on_an_admin_target_is_allowed() -> None:
    assert credential_change_forbidden(ADMIN_OID, ["admin"], ADMIN_OID) is False


def test_identity_is_compared_as_string() -> None:
    assert (
        credential_change_forbidden(_OidLike(ADMIN_OID), ["admin"], ADMIN_OID)
        is False
    )


def test_another_actor_on_an_admin_target_is_forbidden() -> None:
    assert credential_change_forbidden(OTHER_ADMIN_OID, ["admin"], ADMIN_OID) is True


def test_another_actor_on_an_admin_target_with_mixed_roles_is_forbidden() -> None:
    assert (
        credential_change_forbidden(OTHER_ADMIN_OID, ["student", "admin"], ADMIN_OID)
        is True
    )


def test_another_actor_on_a_department_target_is_allowed() -> None:
    assert credential_change_forbidden(ADMIN_OID, ["department"], DEPT_OID) is False


def test_another_actor_on_a_student_target_is_allowed() -> None:
    assert credential_change_forbidden(ADMIN_OID, ["student"], STUDENT_OID) is False


def test_another_actor_on_a_roleless_target_is_allowed() -> None:
    assert credential_change_forbidden(ADMIN_OID, [], STUDENT_OID) is False
    assert credential_change_forbidden(ADMIN_OID, None, STUDENT_OID) is False
