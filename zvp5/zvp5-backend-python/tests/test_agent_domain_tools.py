"""用户/义工管理专用工具 + 通用数据库写抑制（离线）。

这一组用例守三条线：

1. **能力的唯一入口**：活动创建/修改/审核有专用工具，身份字段（创建人、负责人、
   审核人）不在 schema 里，只能由服务端从当前用户派生；账号供给/提权刻意不提供。
2. **通用 DB 写不能再碰账号/义工表**：``db_insert`` / ``db_update`` /
   ``db_delete`` / ``db_ddl`` 在 **prepare 与 impl 两处**都拒绝——后者保证本次
   修改之前就已存在的 pending 提案、以及重放路径同样失效。
3. **审核 CAS 的缓存失效**：事务内写不提前失效，提交之后才删 key（与
   ``db.cached_repos.CachedActivitiesRepo.cas_status`` 的分工对齐）。
"""

from __future__ import annotations

import pytest

from services.agent import ops_tools, policy, tool_impl, tools, tools_domain
from services.agent.tool_impl import AgentError

ADMIN = {"id": "000000000000000000000001", "per": ["admin"]}
DEPARTMENT = {"id": "000000000000000000000002", "per": ["department"]}
SECRETARY = {"id": "000000000000000000000003", "per": ["secretary"]}
STUDENT = {"id": "000000000000000000000004", "per": ["student"]}

NEW_TOOLS = {
    "user_search",
    "user_set_class",
    "activity_create",
    "activity_update",
    "activity_review",
}

BLOCKED = (
    "USERS",
    "ACTIVITIES",
    "ACTIVITY_MEMBERS",
    "USER_TIME",
    "TIME_WITH_ORIGIN",
    "GROUPS",
    "ROLE_FEATURES",
    "ROLE_QUOTAS",
    "USER_QUOTA_OVERRIDES",
    "AI_QUOTA",
    "AI_API_KEYS",
    "AI_CONVERSATIONS",
    "AI_MESSAGES",
    "AI_RUNS",
    "AI_AGENT_EVENTS",
    "AI_SCHEDULED_TASKS",
    "APP_LOGS",
    "FILES",
    "FILE_EVENTS",
    " activities ",
    '"USERS"',
)


# ---------------------------------------------------------------------------
# 注册表 / schema
# ---------------------------------------------------------------------------


def test_domain_tools_are_registered_with_frozen_kinds() -> None:
    assert NEW_TOOLS <= set(tools.TOOLS)
    assert tools.TOOLS["user_search"].kind == tools.READ
    for name in NEW_TOOLS - {"user_search"}:
        assert tools.TOOLS[name].kind == tools.WRITE, name

    # 账号供给/提权/凭据重置刻意留在人工流程（密码再认证 + 双人授权），
    # agent 不应出现这些工具名。
    forbidden = {
        "user_create",
        "user_set_roles",
        "user_roles_set",
        "user_password_reset",
        "activity_member_add",
    }
    assert not forbidden & set(tools.TOOLS)


def test_department_sees_volunteer_management_tools() -> None:
    names = {spec["function"]["name"] for spec in tools.tool_specs(DEPARTMENT)}
    assert NEW_TOOLS <= names
    # 账号级写工具只对 admin 声明。
    assert not {"user_create", "user_set_roles"} & names


@pytest.mark.parametrize("name", sorted(NEW_TOOLS))
def test_schemas_are_closed_and_described(name: str) -> None:
    params = tools.TOOLS[name].params
    assert params["additionalProperties"] is False
    for key, spec in params["properties"].items():
        assert spec.get("description"), f"{name}.{key}"


def test_activity_create_schema_has_no_identity_fields() -> None:
    props = set(tools.TOOLS["activity_create"].params["properties"])
    assert not {
        "creator",
        "appointee",
        "approver",
        "status",
        "origin",
        "owner",
    } & props


def test_activity_review_status_is_closed_enum() -> None:
    spec = tools.TOOLS["activity_review"].params["properties"]["status"]
    assert spec["enum"] == ["effective", "refused"]


@pytest.mark.parametrize(
    ("name", "arguments", "field"),
    [
        ("activity_create", {"name": "x", "creator": "evil"}, "creator"),
        ("activity_review", {"status": "pending"}, "status"),
        ("activity_update", {"status": "effective"}, "status"),
        ("user_search", {"role": "root"}, "role"),
        ("user_set_class", {"target": "20260001", "roles": ["admin"]}, "roles"),
    ],
)
def test_strict_arguments_are_enforced(name: str, arguments: dict, field: str) -> None:
    """未知字段 / 枚举外值在 ``prepare_arguments`` 就拒（确认/重放同一道闸）。"""
    tool = tools.TOOLS[name]
    with pytest.raises(AgentError) as caught:
        tools.prepare_arguments(tool, arguments)
    assert field in caught.value.message


# ---------------------------------------------------------------------------
# 表级策略
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("table", BLOCKED)
def test_owner_bearing_tables_are_generic_write_blocked(table: str) -> None:
    assert policy.is_generic_write_blocked(table)
    assert policy.generic_write_blocked_reason(table)


@pytest.mark.parametrize(
    "table", ["NOTIFICATIONS", "SYSTEM_SETTINGS", "BAN_RULES", "", None, "ACTIVITIES_LOG"]
)
def test_other_tables_are_not_in_the_block_list(table) -> None:
    assert not policy.is_generic_write_blocked(table)
    assert policy.generic_write_blocked_reason(table) is None


# ---------------------------------------------------------------------------
# 通用 DB 写：prepare 与「旧提案重放」的 impl 都必须拒绝
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_update_prepare_refuses_users() -> None:
    with pytest.raises(AgentError) as caught:
        await tools.TOOLS["db_update"].prepare(
            ADMIN, table="USERS", key={"OID": "x"}, values={"ROLES": "admin"}
        )
    assert caught.value.code == policy.GENERIC_WRITE_BLOCKED_CODE
    assert caught.value.status_code == 403


@pytest.mark.asyncio
async def test_db_insert_prepare_refuses_activity_owner_forgery() -> None:
    with pytest.raises(AgentError) as caught:
        await tools.TOOLS["db_insert"].prepare(
            ADMIN,
            table="ACTIVITIES",
            values={"NAME": "x", "CREATOR_OID": STUDENT["id"]},
        )
    assert caught.value.code == policy.GENERIC_WRITE_BLOCKED_CODE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "kwargs"),
    [
        ("db_insert", {"table": "USERS", "values": {"ROLES": "admin"}}),
        (
            "db_update",
            {"table": "ACTIVITIES", "key": {"OID": "x"}, "values": {"CREATOR_OID": "y"}},
        ),
        (
            "db_update",
            {"table": "ACTIVITY_MEMBERS", "key": {"OID": "x"}, "values": {"DURATION": 99}},
        ),
        ("db_delete", {"table": "USER_TIME", "key": {"USER_OID": "x"}}),
    ],
)
async def test_legacy_dml_proposal_replay_is_refused_before_any_session(
    monkeypatch, name: str, kwargs: dict
) -> None:
    """旧 pending 提案/重放直接走 impl：必须在打开会话之前就拒。"""

    def _no_session():
        raise AssertionError("blocked table must not open a DB session")

    monkeypatch.setattr(ops_tools, "session_scope", _no_session)

    with pytest.raises(AgentError) as caught:
        await tools.TOOLS[name].impl(ADMIN, **kwargs)

    assert caught.value.code == policy.GENERIC_WRITE_BLOCKED_CODE


@pytest.mark.asyncio
async def test_db_ddl_prepare_and_replay_refuse_blocked_tables() -> None:
    with pytest.raises(AgentError) as prepare_exc:
        await tools.TOOLS["db_ddl"].prepare(
            ADMIN, sql="UPDATE USERS SET ROLES = 'admin'", confirm="USERS"
        )
    assert prepare_exc.value.code == policy.GENERIC_WRITE_BLOCKED_CODE

    with pytest.raises(AgentError) as replay_exc:
        await tools.TOOLS["db_ddl"].impl(
            ADMIN, sql="DELETE FROM ACTIVITIES WHERE OID = 'x'", confirm="ACTIVITIES"
        )
    assert replay_exc.value.code == policy.GENERIC_WRITE_BLOCKED_CODE


def test_ddl_scan_ignores_table_names_inside_string_literals() -> None:
    """``SET CONTENT='USERS'`` 不是写 USERS 表，不能误杀。"""
    ops_tools._ensure_generic_ddl_allowed(
        "UPDATE NOTIFICATIONS SET CONTENT = 'USERS' WHERE OID = 'x'"
    )


# ---------------------------------------------------------------------------
# 活动工具：服务端派生 + 严格校验 + CAS
# ---------------------------------------------------------------------------


class _FakeSession:
    def __init__(self) -> None:
        self.committed = False

    def begin(self):
        session = self

        class _Tx:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *exc):
                session.committed = True
                return False

        return _Tx()


def _patch_session(monkeypatch, events: list | None = None) -> _FakeSession:
    import contextlib

    session = _FakeSession()

    @contextlib.asynccontextmanager
    async def _scope():
        if events is not None:
            events.append("enter")
        yield session
        if events is not None:
            events.append("scope_exit")

    monkeypatch.setattr(tool_impl, "session_scope", _scope)
    return session


@pytest.mark.asyncio
async def test_activity_create_derives_creator_from_actor(monkeypatch) -> None:
    _patch_session(monkeypatch)
    captured: dict = {}

    class _Repo:
        async def create(self, *args):
            captured["create"] = args
            return "a" * 24

        async def update(self, oid, fields):
            captured["update"] = (oid, fields)

    async def _no_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        tool_impl.backends, "get_activities_repo", lambda session=None: _Repo()
    )
    monkeypatch.setattr(tool_impl, "_audit_member_write", _no_audit)

    result = await tool_impl.create_activity(
        ADMIN,
        name="图书馆整理",
        description="整理书架",
        activity_type="on-campus",
        date_value="2026-01-31T12:00:00",
        place="图书馆",
    )

    name, description, atype, creator, appointee, origin, when = captured["create"]
    assert (name, description, atype) == ("图书馆整理", "整理书架", "on-campus")
    assert creator == ADMIN["id"]
    assert appointee == ADMIN["id"]
    assert origin == "activities"
    assert when.tzinfo is None
    assert result["creator"] == ADMIN["id"]
    assert result["appointee"] == ADMIN["id"]
    assert result["status"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"name": "", "description": "", "activity_type": "on-campus"}, "empty"),
        ({"name": "x", "description": "", "activity_type": "nope"}, "type"),
        ({"name": "x", "description": "", "activity_type": "on-campus"}, "date"),
    ],
)
async def test_activity_create_strict_validation(monkeypatch, kwargs, message) -> None:
    _patch_session(monkeypatch)
    monkeypatch.setattr(
        tool_impl.backends,
        "get_activities_repo",
        lambda session=None: pytest.fail("must validate before touching the repo"),
    )
    payload = {"date_value": "not-a-date", **kwargs}
    with pytest.raises(AgentError) as caught:
        await tool_impl.create_activity(ADMIN, **payload)
    assert message in caught.value.message


class _LiveUsersRepo:
    """事务内的操作者行（live role）：默认是 admin。"""

    def __init__(self, roles: list[str] | None = None) -> None:
        self.roles = roles or ["admin"]

    async def get_by_oid(self, oid):
        return {"_id": ADMIN["id"], "roles": self.roles}


@pytest.mark.asyncio
async def test_review_activity_cas_and_cache_invalidation_after_commit(
    monkeypatch,
) -> None:
    events: list = []
    session = _patch_session(monkeypatch, events)

    class _Repo:
        async def get_for_update(self, oid):
            events.append("read")
            return {"status": "pending"}

        async def cas_status(self, oid, expected, fields):
            events.append(("cas", expected, dict(fields)))
            return True

    async def _invalidate(oid):
        events.append(("invalidate", oid, session.committed))

    async def _no_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        tool_impl.backends, "get_activities_repo", lambda session=None: _Repo()
    )
    monkeypatch.setattr(
        tool_impl.backends, "get_users_repo", lambda session=None: _LiveUsersRepo()
    )
    monkeypatch.setattr(tool_impl, "_invalidate_activity_cache", _invalidate)
    monkeypatch.setattr(
        tool_impl,
        "_schedule_activity_time_refresh",
        lambda oid: events.append(("refresh", oid)),
    )
    monkeypatch.setattr(tool_impl, "_audit_member_write", _no_audit)

    oid = "b" * 24
    result = await tool_impl.review_activity(
        ADMIN, activity_id=oid, status="effective"
    )

    assert result == {"id": oid, "status": "effective", "label": "生效"}
    cas = next(item for item in events if isinstance(item, tuple) and item[0] == "cas")
    assert cas[1] == "pending"
    assert cas[2]["status"] == "effective"
    assert cas[2]["approver"] == ADMIN["id"]
    # 失效与时长重算都发生在事务提交之后（commit 由 session.begin().__aexit__ 触发）。
    invalidate = next(
        item for item in events if isinstance(item, tuple) and item[0] == "invalidate"
    )
    assert invalidate[1] == oid
    assert invalidate[2] is True
    assert ("refresh", oid) in events
    assert events.index(cas) < events.index(invalidate)


@pytest.mark.asyncio
async def test_review_activity_conflict_is_409_and_not_invalidated(monkeypatch) -> None:
    _patch_session(monkeypatch)
    invalidated: list = []

    class _Repo:
        async def get_for_update(self, oid):
            return {"status": "pending"}

        async def cas_status(self, oid, expected, fields):
            return False

    async def _invalidate(oid):
        invalidated.append(oid)

    monkeypatch.setattr(
        tool_impl.backends, "get_activities_repo", lambda session=None: _Repo()
    )
    monkeypatch.setattr(
        tool_impl.backends, "get_users_repo", lambda session=None: _LiveUsersRepo()
    )
    monkeypatch.setattr(tool_impl, "_invalidate_activity_cache", _invalidate)

    with pytest.raises(AgentError) as caught:
        await tool_impl.review_activity(
            ADMIN, activity_id="c" * 24, status="refused"
        )

    assert caught.value.status_code == 409
    assert invalidated == []


@pytest.mark.asyncio
async def test_review_activity_rejects_non_pending_even_for_admin(monkeypatch) -> None:
    """Agent 不推翻已生效审核：旧提案不能把 effective 改成别的状态。"""
    _patch_session(monkeypatch)

    class _Repo:
        async def get_for_update(self, oid):
            return {"status": "effective"}

    monkeypatch.setattr(
        tool_impl.backends, "get_activities_repo", lambda session=None: _Repo()
    )
    monkeypatch.setattr(
        tool_impl.backends, "get_users_repo", lambda session=None: _LiveUsersRepo()
    )

    with pytest.raises(AgentError) as caught:
        await tool_impl.review_activity(
            ADMIN, activity_id="d" * 24, status="refused"
        )
    assert caught.value.status_code == 409
    assert "only pending" in caught.value.message


@pytest.mark.asyncio
async def test_review_activity_uses_live_role_not_token_snapshot(monkeypatch) -> None:
    """token 里写着 department、库里已降级为 student → 403（现查库）。"""
    _patch_session(monkeypatch)

    class _Repo:
        async def get_for_update(self, oid):
            return {"status": "pending"}

    monkeypatch.setattr(
        tool_impl.backends, "get_activities_repo", lambda session=None: _Repo()
    )
    monkeypatch.setattr(
        tool_impl.backends,
        "get_users_repo",
        lambda session=None: _LiveUsersRepo(roles=["student"]),
    )

    with pytest.raises(AgentError) as caught:
        await tool_impl.review_activity(
            DEPARTMENT, activity_id="e" * 24, status="effective"
        )
    assert caught.value.status_code == 403


# ---------------------------------------------------------------------------
# 权限闸：live 角色 / 封禁
# ---------------------------------------------------------------------------


def _patch_live_user(monkeypatch, row: dict | None, perms: list[str] | None = None):
    import contextlib

    class _Repo:
        async def get_by_oid(self, oid):
            return row

    @contextlib.asynccontextmanager
    async def _scope():
        yield _FakeSession()

    async def _perms(_row):
        return perms if perms is not None else list((row or {}).get("roles") or [])

    monkeypatch.setattr(tools_domain, "session_scope", _scope)
    monkeypatch.setattr(
        tools_domain.backends, "get_users_repo", lambda session=None: _Repo()
    )
    monkeypatch.setattr(tools_domain, "get_user_permissions", _perms)


@pytest.mark.asyncio
async def test_ensure_volunteer_manager_accepts_live_admin(monkeypatch) -> None:
    _patch_live_user(monkeypatch, {"_id": ADMIN["id"], "roles": ["admin"]})
    await tools_domain.ensure_volunteer_manager(ADMIN)


@pytest.mark.asyncio
async def test_ensure_volunteer_manager_rejects_demoted_or_missing(monkeypatch) -> None:
    _patch_live_user(monkeypatch, {"_id": STUDENT["id"], "roles": ["student"]})
    with pytest.raises(AgentError) as caught:
        await tools_domain.ensure_volunteer_manager(ADMIN)
    assert caught.value.status_code == 403

    _patch_live_user(monkeypatch, None)
    with pytest.raises(AgentError) as missing:
        await tools_domain.ensure_volunteer_manager(ADMIN)
    assert missing.value.status_code == 403


@pytest.mark.asyncio
async def test_ensure_volunteer_manager_rejects_banned_admin(monkeypatch) -> None:
    _patch_live_user(
        monkeypatch,
        {"_id": ADMIN["id"], "roles": ["admin"], "banned": 1, "ban_until": None},
    )
    with pytest.raises(AgentError) as caught:
        await tools_domain.ensure_volunteer_manager(ADMIN)
    assert caught.value.code == "USER_BANNED"


# ---------------------------------------------------------------------------
# 用户检索：白名单投影 + 角色过滤权限
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_search_projects_out_credentials(monkeypatch) -> None:
    row = {
        "_id": STUDENT["id"],
        "id": "20260001",
        "name": "张三",
        "roles": ["student"],
        "class_id": "e" * 24,
        "password": "hash",
        "session_id": "sid",
        "has_password": True,
    }

    class _Repo:
        async def list_page(self, query, role, page, size):
            assert (query, role, page, size) == ("张三", "", 1, 10)
            return [row], 1

    monkeypatch.setattr(
        tool_impl.backends, "get_users_repo", lambda session=None: _Repo()
    )

    result = await tool_impl.search_users(
        DEPARTMENT, query="张三", page=1, size=10
    )

    assert result["pagination"] == {"page": 1, "size": 10, "total": 1}
    (user,) = result["users"]
    assert user["_id"] == STUDENT["id"]
    assert user["name"] == "张三"
    assert "password" not in user
    assert "session_id" not in user
    assert "has_password" not in user


@pytest.mark.asyncio
async def test_user_search_role_filter_is_admin_only() -> None:
    with pytest.raises(AgentError) as caught:
        await tool_impl.search_users(DEPARTMENT, role="admin")
    assert caught.value.status_code == 403


# ---------------------------------------------------------------------------
# 唯一新增的账号写能力：班级归属
# ---------------------------------------------------------------------------


def _patch_class_repos(monkeypatch, *, target_class: dict | None):
    calls: dict = {}
    user_row = {
        "_id": STUDENT["id"],
        "id": "20260001",
        "name": "张三",
        "roles": ["student"],
        "class_id": "f" * 24,
    }

    class _Users:
        async def find_by_business_id(self, business_id):
            return user_row if business_id == "20260001" else None

        async def set_class(self, oid, class_oid):
            calls["set_class"] = (oid, class_oid)

    class _Groups:
        async def get_permission_group(self, oid):
            return None

        async def get(self, oid):
            return target_class

    async def _no_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(
        tool_impl.backends, "get_users_repo", lambda session=None: _Users()
    )
    monkeypatch.setattr(
        tool_impl.backends, "get_groups_repo", lambda session=None: _Groups()
    )
    monkeypatch.setattr(tool_impl, "_audit_member_write", _no_audit)
    return calls


@pytest.mark.asyncio
async def test_user_set_class_moves_user(monkeypatch) -> None:
    class_oid = "1" * 24
    calls = _patch_class_repos(
        monkeypatch, target_class={"_id": class_oid, "name": "一班"}
    )

    result = await tool_impl.set_user_class(
        DEPARTMENT, target="20260001", class_id=class_oid
    )

    assert calls["set_class"] == (STUDENT["id"], class_oid)
    assert result["from_class"] == "f" * 24
    assert result["to_class"] == class_oid
    assert result["class_name"] == "一班"
    assert "roles" not in result
    assert "password" not in result


@pytest.mark.asyncio
async def test_user_set_class_empty_clears_class(monkeypatch) -> None:
    calls = _patch_class_repos(monkeypatch, target_class=None)

    result = await tool_impl.set_user_class(
        DEPARTMENT, target="20260001", class_id=""
    )

    assert calls["set_class"] == (STUDENT["id"], None)
    assert result["to_class"] is None


@pytest.mark.asyncio
async def test_user_set_class_rejects_missing_class(monkeypatch) -> None:
    _patch_class_repos(monkeypatch, target_class=None)

    with pytest.raises(AgentError) as caught:
        await tool_impl.set_user_class(
            DEPARTMENT, target="20260001", class_id="2" * 24
        )
    assert caught.value.status_code == 404
