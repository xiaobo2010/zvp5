"""角色策略（V043 ``ROLE_FEATURES`` / ``ROLE_QUOTAS``）的契约。

四条不变量：

* :data:`services.roles.policy.DEFAULTS` 覆盖三个可编辑角色、每个角色覆盖全部
  键，且 ``admin`` 是代码短路（全开 + 无限额），不落库；
* 功能开关按**多角色并集**判定，额度按 :data:`QUOTA_ROLE_PRIORITY` 取**第一个
  命中**的角色（不叠加、不取最大值）；
* ``PUT /api/system/role-policies/{role}`` 是**全量替换**：请求里没出现的 key
  回落到代码默认（稀疏覆盖层），非法 role / 未知 key / 越界额度一律 400；
* ``GET /me`` 只回本人，不泄露其他角色的策略；``services.ai.quota`` 优先读角色
  策略表，表里没有意见时才回落到 ``ai.role_quota`` / 全局默认。

全部离线：仓储是内存桩，端点走真实 router + 真实守卫（只替掉「查库」与
「双人码」两处外部依赖）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends, ddl_check
from routers import system_role_policies_router as role_router
from routers import system_router
from services.ai import quota as quota_service
from services.ai.config import DEFAULT_QUOTA
from services.roles import policy
from util.dual_auth import DualAuthError
from util.object_id import get_current_user

ADMIN_OID = "64b7f0c2e1a3b4c5d6e7fa01"
STUDENT_OID = "64b7f0c2e1a3b4c5d6e7fa02"
APPROVER_OID = "64b7f0c2e1a3b4c5d6e7fa03"

ADMIN = {"_id": ADMIN_OID, "id": ADMIN_OID, "per": ["admin"]}
STUDENT = {"_id": STUDENT_OID, "id": STUDENT_OID, "per": ["student"]}
SECRETARY = {"_id": STUDENT_OID, "id": STUDENT_OID, "per": ["secretary"]}

STAMP = datetime(2026, 10, 1, 12, 0, 0)


# --------------------------------------------------------------------------- #
# 内存桩：角色策略仓储 / 用户仓储 / 双人码 / 审计
# --------------------------------------------------------------------------- #


class FakePolicyRepo:
    """``OracleRolePoliciesRepo`` 的内存版，语义与 SQL 版一致（DELETE + INSERT）。"""

    def __init__(self) -> None:
        self.features: dict[tuple[str, str], bool] = {}
        self.quotas: dict[tuple[str, str], int] = {}
        self.actors: dict[str, str | None] = {}
        self.upserts: list[dict] = []
        self.fail = False

    async def list_all(self) -> dict:
        if self.fail:
            raise RuntimeError("adb down")
        return {
            "features": [
                {
                    "role": role,
                    "feature_key": key,
                    "enabled": enabled,
                    "updated_at": STAMP,
                    "updated_by": self.actors.get(role),
                }
                for (role, key), enabled in self.features.items()
            ],
            "quotas": [
                {
                    "role": role,
                    "kind": kind,
                    "monthly_limit": limit,
                    "updated_at": STAMP,
                    "updated_by": self.actors.get(role),
                }
                for (role, kind), limit in self.quotas.items()
            ],
        }

    async def get_role(self, role: str) -> dict:
        features = {
            key: value
            for (item_role, key), value in self.features.items()
            if item_role == role
        }
        quotas = {
            kind: value
            for (item_role, kind), value in self.quotas.items()
            if item_role == role
        }
        touched = bool(features or quotas)
        return {
            "role": role,
            "features": features,
            "quotas": quotas,
            "updated_at": STAMP if touched else None,
            "updated_by": self.actors.get(role),
        }

    async def upsert_role(
        self,
        role: str,
        features: dict[str, bool],
        quotas: dict[str, int],
        actor: str | None = None,
    ) -> dict:
        self.upserts.append(
            {
                "role": role,
                "features": dict(features),
                "quotas": dict(quotas),
                "actor": actor,
            }
        )
        # 全量替换：先清掉这个角色的所有行，再写请求里那一份。
        self.features = {
            (item_role, key): value
            for (item_role, key), value in self.features.items()
            if item_role != role
        }
        self.quotas = {
            (item_role, kind): value
            for (item_role, kind), value in self.quotas.items()
            if item_role != role
        }
        self.features.update({(role, key): value for key, value in features.items()})
        # 与 SQL 版一致：``None`` 额度**不落行**（= 用系统默认）。
        self.quotas.update(
            {(role, kind): value for kind, value in quotas.items() if value is not None}
        )
        self.actors[role] = actor
        return await self.get_role(role)


class FakeUsersRepo:
    """``_require_system_admin`` 需要的那一个方法（``banned`` 为假即可放行）。"""

    async def get_by_oid(self, oid: str) -> dict | None:
        if not oid:
            return None
        return {"_id": oid, "banned": 0}


class RecordingSession:
    """只记录语句的 ``AsyncSession`` 替身（仓储层「全量替换」的顺序断言用）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []
        self.commits = 0

    async def execute(self, statement, params=None):
        self.calls.append((statement, params))
        return _EmptyResult()

    async def commit(self) -> None:
        self.commits += 1


class _EmptyResult:
    def all(self) -> list:
        return []


@pytest.fixture(autouse=True)
def _fresh_snapshot() -> None:
    """快照是模块级全局：每个用例从「库里没有任何覆盖」开始。"""
    policy.reset_cache()
    yield
    policy.reset_cache()


@pytest.fixture
def repo(monkeypatch) -> FakePolicyRepo:
    """内存桩 + 把策略层的唯一接缝换成它（配额用例因此也不碰真库）。"""
    fake = FakePolicyRepo()
    monkeypatch.setattr(policy, "_repo", lambda: fake)
    return fake


@pytest.fixture
def audits() -> list[dict]:
    return []


@pytest.fixture
def app(repo, audits, monkeypatch) -> FastAPI:
    """真实 router + 真实 admin 守卫，只替掉查库与双人码。"""

    async def _no_dual_auth(operator, code, *, action):
        return APPROVER_OID

    async def _record_audit(operator_oid, *, action, detail, log_type):
        audits.append(
            {
                "operator": operator_oid,
                "action": action,
                "detail": detail,
                "log_type": log_type,
            }
        )

    async def _all_perms(_doc):
        return ["admin"]

    monkeypatch.setattr(backends, "get_users_repo", lambda session=None: FakeUsersRepo())
    monkeypatch.setattr(system_router, "get_user_permissions", _all_perms)
    monkeypatch.setattr(role_router, "require_dual_auth", _no_dual_auth)
    monkeypatch.setattr(role_router, "audit_system_op", _record_audit)

    application = FastAPI()
    application.include_router(
        role_router.router, prefix="/api/system/role-policies"
    )
    return application


def _client(app: FastAPI, user: dict) -> TestClient:
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


# --------------------------------------------------------------------------- #
# DEFAULTS / 短路 / 并集 / 优先级
# --------------------------------------------------------------------------- #


def test_defaults_cover_exactly_the_editable_roles() -> None:
    assert set(policy.DEFAULTS) == set(policy.EDITABLE_ROLES)
    assert "admin" not in policy.DEFAULTS
    for role, entry in policy.DEFAULTS.items():
        assert set(entry["features"]) == set(policy.FEATURE_KEYS), role
        assert set(entry["quotas"]) == set(policy.QUOTA_KINDS), role


def test_department_defaults_open_only_what_it_could_do_before_v043() -> None:
    """department 默认关着的，正是改造前只有 admin 能进的六项。

    这六项的端点已改成「admin 或该角色开关已开」，默认关 = 线上行为不变；
    一旦这里多开一个，等于把该项**默认**下放给学生会，必须是一次显式决定。
    """
    department = policy.DEFAULTS["department"]["features"]
    assert {key for key, value in department.items() if not value} == set(
        policy.ADMIN_ONLY_FEATURE_KEYS
    )
    assert set(policy.ADMIN_ONLY_FEATURE_KEYS) == {
        "export.time",
        "import.activity",
        "logs.read",
        "notify.publish",
        "ban.read",
        "quota.manage",
    }
    assert len(department) - len(policy.ADMIN_ONLY_FEATURE_KEYS) == 7


def test_admin_short_circuits_features_and_quotas() -> None:
    """管理员永远全开、无限额：这是代码短路，不是数据行。"""
    assert policy.feature_enabled(["admin"], "logs.read") is True
    assert policy.feature_enabled(["admin"], "no.such.key") is True
    assert policy.monthly_limit(["admin"], "chat") is None
    assert policy.monthly_limit(["admin"], "no_such_kind") is None


def test_features_are_a_union_across_roles() -> None:
    assert policy.feature_enabled(["secretary"], "activity.stats") is True
    assert policy.feature_enabled(["secretary"], "ai.chat") is False
    assert policy.feature_enabled(["student"], "ai.chat") is False
    # 并集：任一角色给了就放行。
    assert policy.feature_enabled(["student", "department"], "ai.chat") is True
    assert policy.feature_enabled(["student", "secretary"], "ai.chat") is False


def test_monthly_limit_takes_the_first_hit_role() -> None:
    assert policy.monthly_limit(["department"], "chat") == 200_000
    assert policy.monthly_limit(["student"], "chat") == 0
    assert policy.monthly_limit(["secretary"], "ocr") == 100
    assert policy.monthly_limit(["student"], "schedule") == 0
    # 多角色取第一个命中（department 优先于 student），不叠加、不取最大值。
    assert policy.monthly_limit(["student", "department"], "chat") == 200_000
    assert policy.monthly_limit(None, "chat") is None
    # 站点级/未知 kind 没有角色维度：交回调用方。
    assert policy.monthly_limit(["department"], "ocr_tencent") is None


def test_snapshot_falls_back_to_defaults_when_the_table_is_empty(repo) -> None:
    view = asyncio.run(policy.all_policies(force=True))

    assert [row["role"] for row in view["roles"]] == list(policy.EDITABLE_ROLES)
    assert view["editable_roles"] == list(policy.EDITABLE_ROLES)
    assert view["feature_keys"] == list(policy.FEATURE_KEYS)

    department = view["roles"][0]
    assert department["features"]["ai.chat"] is True
    assert department["quotas"] == {"chat": 200_000, "ocr": 100, "schedule": 1}
    assert view["roles"][2]["features"]["ai.chat"] is False
    # 空表 = 从没配过：没有「最后修改人」可报。
    assert department["updated_at"] is None
    assert department["updated_by"] is None


def test_rows_for_unknown_roles_or_keys_are_ignored(repo) -> None:
    """手工改库能塞进代码不认识的组合：忽略而不是崩。"""
    repo.features[("teacher", "ai.chat")] = True
    repo.features[("student", "no.such.key")] = True
    repo.quotas[("student", "image")] = 10

    asyncio.run(policy.refresh())

    assert policy.feature_enabled(["teacher"], "ai.chat") is False
    assert policy.feature_enabled(["student"], "no.such.key") is False
    assert policy.monthly_limit(["student"], "chat") == 0


def test_repo_failure_falls_back_to_defaults_instead_of_raising(repo) -> None:
    repo.fail = True

    asyncio.run(policy.refresh())

    assert policy.feature_enabled(["department"], "ai.chat") is True
    assert policy.monthly_limit(["department"], "chat") == 200_000


# --------------------------------------------------------------------------- #
# 仓储：全量替换 = 两张表各 DELETE 一次 + INSERT 请求里那一份
# --------------------------------------------------------------------------- #


def _repo_with_recorder(monkeypatch):
    from db.repos.oracle.role_policies import OracleRolePoliciesRepo

    session = RecordingSession()
    repository = OracleRolePoliciesRepo(session=session)

    async def _get_role(role: str) -> dict:
        return {"role": role, "features": {}, "quotas": {}}

    monkeypatch.setattr(repository, "get_role", _get_role)
    return repository, session


def test_upsert_role_replaces_whole_matrix_in_one_transaction(monkeypatch) -> None:
    repository, session = _repo_with_recorder(monkeypatch)

    asyncio.run(
        repository.upsert_role(
            "student", {"ai.ocr": True}, {"chat": 7, "ocr": 5, "schedule": 0}, "actor"
        )
    )

    statements = [statement for statement, _params in session.calls]
    # 先删两张表（同一个角色的全部行），再写请求给的那一份。
    assert [(item.is_delete, item.table.name) for item in statements] == [
        (True, "role_features"),
        (True, "role_quotas"),
        (False, "role_features"),
        (False, "role_quotas"),
    ]
    assert all(item.is_insert for item in statements[2:])
    # 注入 session 时提交权属于调用方（路由/服务层），仓储不自己 commit。
    assert session.commits == 0

    feature_params = session.calls[2][1]
    quota_params = session.calls[3][1]
    assert [row["feature_key"] for row in feature_params] == ["ai.ocr"]
    assert [row["kind"] for row in quota_params] == ["chat", "ocr", "schedule"]


def test_upsert_role_with_empty_maps_wipes_the_role(monkeypatch) -> None:
    """空矩阵 = 整角色回落代码默认（只有 DELETE，没有 INSERT）。"""
    repository, session = _repo_with_recorder(monkeypatch)

    asyncio.run(repository.upsert_role("student", {}, {}))

    assert all(statement.is_delete for statement, _params in session.calls)
    assert len(session.calls) == 2


# --------------------------------------------------------------------------- #
# 端点：管理视图
# --------------------------------------------------------------------------- #


def test_get_requires_admin(app) -> None:
    response = _client(app, STUDENT).get("/api/system/role-policies")

    assert response.status_code == 403
    assert response.json()["detail"] == "权限不足"


def test_get_returns_matrix_and_catalog(app, repo) -> None:
    repo.features[("student", "ai.ocr")] = True
    repo.actors["student"] = ADMIN_OID

    response = _client(app, ADMIN).get("/api/system/role-policies")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["editable_roles"] == list(policy.EDITABLE_ROLES)
    assert data["feature_keys"] == list(policy.FEATURE_KEYS)
    student = next(row for row in data["roles"] if row["role"] == "student")
    assert student["features"]["ai.ocr"] is True
    # 稀疏覆盖：没覆盖的键照旧是默认值。
    assert student["features"]["ai.chat"] is False
    assert student["updated_by"] == ADMIN_OID


def test_put_rejects_non_admin(app, repo, audits) -> None:
    response = _client(app, STUDENT).put(
        "/api/system/role-policies/student",
        json={"features": {}, "quotas": {"chat": 0, "ocr": 0, "schedule": 0}},
    )

    assert response.status_code == 403
    assert repo.upserts == []
    assert audits == []


@pytest.mark.parametrize("role", ["admin", "teacher", ""])
def test_put_rejects_role_outside_the_editable_set(role, app, repo) -> None:
    response = _client(app, ADMIN).put(
        f"/api/system/role-policies/{role}",
        json={"features": {}, "quotas": {"chat": 0, "ocr": 0, "schedule": 0}},
    )

    assert response.status_code in (400, 307, 404), response.text
    assert repo.upserts == []


def test_put_rejects_unknown_feature_key(app, repo) -> None:
    response = _client(app, ADMIN).put(
        "/api/system/role-policies/student",
        json={
            "features": {"ai.chat": True, "ai.teleport": True},
            "quotas": {"chat": 0, "ocr": 0, "schedule": 0},
        },
    )

    assert response.status_code == 400
    assert "ai.teleport" in response.json()["detail"]
    assert repo.upserts == []


def test_put_rejects_non_boolean_feature(app, repo) -> None:
    response = _client(app, ADMIN).put(
        "/api/system/role-policies/student",
        json={"features": {"ai.chat": 1}, "quotas": {"chat": 0, "ocr": 0, "schedule": 0}},
    )

    assert response.status_code == 400
    assert repo.upserts == []


def test_put_rejects_partial_quota_map(app, repo) -> None:
    """额度是整表提交：缺一个 kind 会被当作 0 还是「不变」说不清，直接拒。"""
    response = _client(app, ADMIN).put(
        "/api/system/role-policies/student",
        json={"features": {}, "quotas": {"chat": 10}},
    )

    assert response.status_code == 400
    assert "ocr" in response.json()["detail"]
    assert repo.upserts == []


def test_put_treats_a_null_quota_as_no_override(app, repo) -> None:
    """``null`` = 用系统默认（不落行）、``0`` = 该角色不开放：两者不能折叠。

    前端的数字输入框清空后就是 ``null``（见 ``manage.ts`` / ``ManageRoles.vue``
    的 ``setQuota``），所以这条必须一直是 200 而不是「必须是整数」的 400。
    """
    response = _client(app, ADMIN).put(
        "/api/system/role-policies/student",
        json={
            "features": {"ai.ocr": True},
            "quotas": {"chat": None, "ocr": 5, "schedule": None},
            "totp_code": "123456",
        },
    )

    assert response.status_code == 200, response.text
    assert repo.quotas == {("student", "ocr"): 5}
    # chat / schedule 没有覆盖行 → 显示 student 的默认值 0。
    assert response.json()["data"]["quotas"] == {"chat": 0, "ocr": 5, "schedule": 0}


@pytest.mark.parametrize("limit", [-1, 100_000_001, True, "10"])
def test_put_rejects_out_of_range_or_mistyped_quota(limit, app, repo) -> None:
    response = _client(app, ADMIN).put(
        "/api/system/role-policies/student",
        json={
            "features": {},
            "quotas": {"chat": limit, "ocr": 0, "schedule": 0},
        },
    )

    assert response.status_code == 400, response.text
    assert repo.upserts == []


def test_put_requires_dual_auth(app, repo, audits, monkeypatch) -> None:
    async def _denied(operator, code, *, action):
        raise DualAuthError(403, "需要另一位管理员的动态码")

    monkeypatch.setattr(role_router, "require_dual_auth", _denied)

    response = _client(app, ADMIN).put(
        "/api/system/role-policies/student",
        json={
            "features": {"ai.ocr": True},
            "quotas": {"chat": 0, "ocr": 5, "schedule": 0},
            "totp_code": "000000",
        },
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "需要另一位管理员的动态码"
    # 鉴权全部通过之前不碰数据库、不写审计。
    assert repo.upserts == []
    assert audits == []


def test_put_replaces_matrix_and_audits(app, repo, audits) -> None:
    response = _client(app, ADMIN).put(
        "/api/system/role-policies/student",
        json={
            "features": {"ai.ocr": True},
            "quotas": {"chat": 7, "ocr": 5, "schedule": 0},
            "totp_code": "123456",
        },
    )

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["role"] == "student"
    assert data["quotas"] == {"chat": 7, "ocr": 5, "schedule": 0}
    # 回的是**生效矩阵**（13 个键全在），不是请求里那份稀疏覆盖：前端
    # ``normalizeFeatures`` 把缺省键当 true，回半张表会让保存后的界面全亮。
    assert set(data["features"]) == set(policy.FEATURE_KEYS)
    assert data["features"]["ai.ocr"] is True
    assert data["features"]["ai.chat"] is False
    assert data["updated_by"] == ADMIN_OID

    assert repo.upserts == [
        {
            "role": "student",
            "features": {"ai.ocr": True},
            "quotas": {"chat": 7, "ocr": 5, "schedule": 0},
            "actor": ADMIN_OID,
        }
    ]
    # 写后立刻生效（本进程下一次 guard 判断就是新值）。
    assert policy.feature_enabled(["student"], "ai.ocr") is True
    assert policy.feature_enabled(["student"], "ai.chat") is False
    assert policy.monthly_limit(["student"], "chat") == 7

    assert len(audits) == 1
    audit = audits[0]
    assert audit["operator"] == ADMIN_OID
    assert audit["action"] == "role_policy"
    # 沿用既有 ltype，不新造只有这里会写的日志类型。
    assert audit["log_type"] == "system_settings_update"
    assert "role=student" in audit["detail"]
    assert "ai.ocr" in audit["detail"]
    assert APPROVER_OID in audit["detail"]


# --------------------------------------------------------------------------- #
# 端点：自助视图
# --------------------------------------------------------------------------- #


def test_me_returns_only_my_policy(app, repo) -> None:
    # 库里给 department 配了全开，student 没配：/me 不该看到别人的格子。
    repo.features[("department", "logs.read")] = True
    repo.quotas[("department", "chat")] = 999

    response = _client(app, STUDENT).get("/api/system/role-policies/me")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert set(data) == {"features", "quotas", "unlimited"}
    assert data["unlimited"] is False
    assert data["features"]["logs.read"] is False
    assert data["quotas"] == {"chat": 0, "ocr": 0, "schedule": 0}


def test_me_for_admin_is_unlimited(app) -> None:
    response = _client(app, ADMIN).get("/api/system/role-policies/me")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["unlimited"] is True
    assert data["quotas"] == {"chat": None, "ocr": None, "schedule": None}
    assert all(data["features"].values())


def test_me_reflects_the_role_table(app, repo) -> None:
    repo.features[("secretary", "export.time")] = True
    repo.quotas[("secretary", "chat")] = 42

    response = _client(app, SECRETARY).get("/api/system/role-policies/me")

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["features"]["export.time"] is True
    assert data["quotas"]["chat"] == 42


# --------------------------------------------------------------------------- #
# quota 接入
# --------------------------------------------------------------------------- #


def test_quota_prefers_the_role_policy_table(repo) -> None:
    repo.quotas[("department", "chat")] = 7

    asyncio.run(policy.refresh())

    assert quota_service.default_limit_for(["department"], "chat") == 7


def test_quota_falls_back_to_global_defaults_when_policy_is_silent(repo) -> None:
    asyncio.run(policy.refresh())

    assert quota_service.default_limit_for(None, "chat") == DEFAULT_QUOTA["chat"]
    assert quota_service.default_limit_for([], "chat") == DEFAULT_QUOTA["chat"]


def test_quota_keeps_the_legacy_table_for_admin(repo) -> None:
    """``admin`` 在策略层是 None（无限），额度层继续用既有的管理员口径。"""
    asyncio.run(policy.refresh())

    assert quota_service.default_limit_for(["admin"], "chat") == 1_000_000


# --------------------------------------------------------------------------- #
# schema 镜像
# --------------------------------------------------------------------------- #


def test_ddl_check_has_no_drift() -> None:
    assert ddl_check.main() == 0
