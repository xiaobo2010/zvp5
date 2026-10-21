"""Stage 2 共享双人授权守卫（``util/dual_auth.py``）的离线用例。

守卫是三块高危系统能力（Web SSH / 全盘文件管理 / 数据库内容管理）的唯一鉴权
入口，所以这里把边界一次钉死：成功路径、错码、用自己的码、没有第二位管理员、
重放、限流、格式非法、审计不留明文。

不连真库：管理员名单与使用记录都由替身 repo 提供；只有「使用时序表结构」和
「语句可编译」两条用例碰 SQLAlchemy 的 DDL/AST（离线，无 I/O）。
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest
from sqlalchemy.dialects import oracle
from sqlalchemy.exc import IntegrityError

from db import backends
from db.ids import is_valid_oid
from db.models import dual_auth_uses as dual_auth_uses_tbl
from db.repos.oracle.users import OracleUsersRepo, _list_admin_oids_stmt
from util import dual_auth
from util.cert import OTP_STEP, _totp_code, _totp_secret, get_current_totp
from util.dual_auth import DualAuthError, audit_system_op, require_dual_auth

OPERATOR = "000000000000000000000001"
APPROVER = "000000000000000000000002"
THIRD = "000000000000000000000003"


def _code_from_window(oid: str, delta: int) -> str:
    """取 ``delta`` 个窗口之前的码（用于构造必然不命中的错误码）。"""
    counter = int(time.time() // OTP_STEP) - delta
    return _totp_code(_totp_secret(oid), counter)


def _wrong_code(oid: str = APPROVER) -> str:
    # 远离当前窗口，避开 counter / counter-1 的合法区间。
    return _code_from_window(oid, 100)


class StubLog:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def insert_log(self):
        return None


class StubRepo:
    def __init__(self, admin_oids: list[str], conflict: bool = False) -> None:
        self.admin_oids = list(admin_oids)
        self.conflict = conflict
        self.used: set[tuple[str, int]] = set()
        self.insert_calls: list[tuple[str, int, str, str]] = []
        self.name_calls: list[list[str]] = []
        self.list_calls = 0

    async def get_names(self, oids: list[str]) -> dict[str, str]:
        self.name_calls.append(list(oids))
        return {oid: f"姓名-{oid[-1]}" for oid in oids if oid in self.admin_oids}

    async def list_admin_oids(self) -> list[str]:
        self.list_calls += 1
        return list(self.admin_oids)

    async def insert_dual_auth_use(
        self, approver_oid: str, otp_counter: int, action: str, operator_oid: str
    ) -> str:
        self.insert_calls.append((approver_oid, otp_counter, action, operator_oid))
        if self.conflict or (approver_oid, otp_counter) in self.used:
            # 生产里这是 SQLAlchemy 对 oracledb ORA-00001 的包装形态。
            raise IntegrityError(
                "INSERT INTO dual_auth_uses ...",
                {},
                Exception(
                    "ORA-00001: unique constraint "
                    "(ZVMS_NEW.UQ_DUAL_APPROVER_COUNTER) violated"
                ),
            )
        self.used.add((approver_oid, otp_counter))
        return "f" * 24


@pytest.fixture(autouse=True)
def _reset_limiter():
    dual_auth._failures.clear()
    yield
    dual_auth._failures.clear()


@pytest.fixture
def audits(monkeypatch):
    entries: list[StubLog] = []

    def factory(**kwargs):
        entry = StubLog(**kwargs)
        entries.append(entry)
        return entry

    monkeypatch.setattr(dual_auth, "ZVMSLog", factory)
    return entries


@pytest.fixture
def install(monkeypatch):
    def _install(admin_oids: list[str], conflict: bool = False) -> StubRepo:
        repo = StubRepo(admin_oids, conflict)
        monkeypatch.setattr(backends, "get_users_repo", lambda session=None: repo)
        return repo

    return _install


def _call(operator, code, action: str = "web_ssh") -> str:
    return asyncio.run(require_dual_auth(operator, code, action=action))


def _reject(operator, code, action: str = "web_ssh") -> DualAuthError:
    with pytest.raises(DualAuthError) as excinfo:
        _call(operator, code, action)
    return excinfo.value


def test_success_returns_approver_and_audits(install, audits):
    repo = install([OPERATOR, APPROVER])
    code, _ = get_current_totp(APPROVER)

    assert _call({"id": OPERATOR}, code) == APPROVER

    assert len(repo.insert_calls) == 1
    approver_oid, counter, action, operator_oid = repo.insert_calls[0]
    assert (approver_oid, action, operator_oid) == (APPROVER, "web_ssh", OPERATOR)
    assert counter == int(time.time() // OTP_STEP)

    assert len(audits) == 1
    log = audits[0].kwargs
    assert log["type"] == "system_dual_auth"
    assert log["level"] == "warning"
    assert log["user"] == OPERATOR
    assert json.loads(log["data"]) == {
        "action": "web_ssh",
        "operator": OPERATOR,
        "operator_name": "姓名-1",
        "approver": APPROVER,
        "approver_name": "姓名-2",
    }
    # 一次审计只查一次姓名：批量解析，不按人 N+1。
    assert repo.name_calls == [[OPERATOR, APPROVER]]


def test_accepts_underscore_id_shape(install, audits):
    install([OPERATOR, APPROVER])
    code, _ = get_current_totp(APPROVER)
    assert _call({"_id": OPERATOR}, code) == APPROVER


def test_wrong_code_is_rejected_without_leaking_approver(install, audits):
    repo = install([OPERATOR, APPROVER])

    error = _reject({"id": OPERATOR}, _wrong_code())

    assert error.status_code == 403
    assert error.detail == "动态码无效或已过期"
    assert repo.insert_calls == []
    payload = json.loads(audits[0].kwargs["data"])
    assert payload["reason"] == "动态码无效或已过期"
    assert "approver" not in payload


def test_operator_cannot_use_own_code(install, audits):
    repo = install([OPERATOR, APPROVER])
    code, _ = get_current_totp(OPERATOR)

    error = _reject({"id": OPERATOR}, code)

    assert error.status_code == 403
    assert error.detail == "动态码无效或已过期"
    assert repo.insert_calls == []


def test_single_admin_is_not_enough(install, audits):
    repo = install([OPERATOR])
    code, _ = get_current_totp(OPERATOR)

    error = _reject({"id": OPERATOR}, code)

    assert error.status_code == 403
    assert error.detail == "动态码无效或已过期"
    assert repo.insert_calls == []
    assert json.loads(audits[0].kwargs["data"])["reason"] == "无其他管理员可审批"


def test_replayed_code_is_rejected_with_409(install, audits):
    repo = install([OPERATOR, APPROVER])
    code, _ = get_current_totp(APPROVER)

    assert _call({"id": OPERATOR}, code) == APPROVER
    error = _reject({"id": OPERATOR}, code)

    assert error.status_code == 409
    assert error.detail == "该动态码已被使用"
    assert len(repo.insert_calls) == 2
    assert json.loads(audits[-1].kwargs["data"])["reason"] == "动态码已被使用"


def test_conflicting_insert_is_translated_to_409(install, audits):
    install([OPERATOR, APPROVER], conflict=True)
    code, _ = get_current_totp(APPROVER)

    error = _reject({"id": OPERATOR}, code)

    assert error.status_code == 409
    assert error.detail == "该动态码已被使用"


def test_five_failures_then_429(install, audits):
    repo = install([OPERATOR, APPROVER])
    code = _wrong_code()

    for _ in range(5):
        assert _reject({"id": OPERATOR}, code).status_code == 403
    assert repo.list_calls == 5

    error = _reject({"id": OPERATOR}, code)
    assert error.status_code == 429
    assert error.detail == "尝试过于频繁，请稍后再试"
    # 超限后不再查库（不再消耗二次校验开销）
    assert repo.list_calls == 5


def test_rate_limit_is_per_operator(install, audits):
    install([OPERATOR, APPROVER, THIRD])
    code = _wrong_code()
    for _ in range(5):
        assert _reject({"id": OPERATOR}, code).status_code == 403

    # 另一个操作者不受影响，仍走正常校验（这里仍用错码，所以是 403 而非 429）
    assert _reject({"id": THIRD}, code).status_code == 403


@pytest.mark.parametrize("code", ["12345", "1234567", "", "abcdef", "12345a", None])
def test_malformed_code_is_422(install, audits, code):
    repo = install([OPERATOR, APPROVER])

    error = _reject({"id": OPERATOR}, code)

    assert error.status_code == 422
    assert error.detail == "动态码格式错误：需为 6 位数字"
    assert repo.list_calls == 0


def test_audit_never_contains_the_plain_code(install, audits):
    install([OPERATOR, APPROVER])
    code, _ = get_current_totp(APPROVER)
    wrong = _wrong_code()

    assert _call({"id": OPERATOR}, code) == APPROVER
    assert _reject({"id": OPERATOR}, wrong).status_code == 403

    assert len(audits) == 2
    for entry in audits:
        data = entry.kwargs["data"]
        assert code not in data
        assert wrong not in data


def test_duplicate_admin_rows_do_not_block_success(install, audits):
    repo = install([OPERATOR, OPERATOR, APPROVER])
    code, _ = get_current_totp(APPROVER)

    assert _call({"id": OPERATOR}, code) == APPROVER
    assert repo.insert_calls[0][0] == APPROVER


def test_audit_system_op_writes_detail(install, audits):
    install([OPERATOR])
    asyncio.run(
        audit_system_op(OPERATOR, action="db_ddl", detail="DROP TABLE demo", log_type="system_op")
    )

    assert len(audits) == 1
    log = audits[0].kwargs
    assert log["type"] == "system_op"
    assert log["user"] == OPERATOR
    payload = json.loads(log["data"])
    assert payload["detail"] == "DROP TABLE demo"
    assert payload["operator"] == OPERATOR
    # 没有姓名就没有人能读懂这行日志；oid 仍保留用于溯源。
    assert payload["operator_name"] == "姓名-1"


def test_audit_system_op_includes_operator_name(install, audits):
    install([OPERATOR, APPROVER])

    asyncio.run(audit_system_op(OPERATOR, action="web_ssh", detail="open"))

    payload = json.loads(audits[-1].kwargs["data"])
    assert payload["operator"] == OPERATOR
    assert payload["operator_name"] == "姓名-1"
    assert payload["detail"] == "open"


def test_name_lookup_failure_still_writes_the_audit(monkeypatch, audits):
    """取名失败不能丢审计：降级成空姓名，oid 仍在。"""

    class BrokenNamesRepo:
        async def get_names(self, oids):
            raise RuntimeError("users table unavailable")

    monkeypatch.setattr(
        backends, "get_users_repo", lambda session=None: BrokenNamesRepo()
    )

    asyncio.run(audit_system_op(OPERATOR, action="web_ssh", detail="open"))

    assert len(audits) == 1
    payload = json.loads(audits[0].kwargs["data"])
    assert payload["operator"] == OPERATOR
    assert payload["operator_name"] == ""


def test_failed_dual_auth_audit_carries_operator_name(install, audits):
    install([OPERATOR, APPROVER])

    _reject({"id": OPERATOR}, _wrong_code())

    payload = json.loads(audits[0].kwargs["data"])
    assert payload["operator_name"] == "姓名-1"
    # 被拒时审批人未知：不能凭空写一个 approver_name。
    assert "approver_name" not in payload



# ---------------------------------------------------------------------------
# 结构/语句层：防重放靠唯一约束，管理员查询走 JSON 精确匹配
# ---------------------------------------------------------------------------


def test_unique_constraint_is_the_replay_guard():
    names = {constraint.name for constraint in dual_auth_uses_tbl.constraints}
    assert "uq_dual_approver_counter" in names


def test_repo_insert_uses_otp_counter_column():
    class FakeSession:
        def __init__(self) -> None:
            self.statements = []

        async def execute(self, stmt):
            self.statements.append(stmt)
            return None

    session = FakeSession()
    repo = OracleUsersRepo(session=session)
    oid = asyncio.run(
        repo.insert_dual_auth_use(APPROVER, 12345, "db_ddl", OPERATOR)
    )

    assert is_valid_oid(oid)
    assert len(session.statements) == 1
    compiled = session.statements[0].compile(dialect=oracle.dialect())
    assert "insert into dual_auth_uses" in str(compiled).lower()
    assert compiled.params["otp_counter"] == 12345
    assert compiled.params["approver_oid"] == APPROVER
    assert compiled.params["action"] == "db_ddl"


def test_admin_query_matches_role_exactly():
    compiled = str(_list_admin_oids_stmt().compile(dialect=oracle.dialect()))

    assert "JSON_EXISTS" in compiled.upper()
    assert "users.roles" in compiled
