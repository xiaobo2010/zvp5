"""AI 面的角色功能开关（V043 ``ROLE_FEATURES``）闸门。

口径与 ``test_role_feature_gates_admin.py`` 一致：**只加不删**。既有的角色白名单
（``AI_ALLOWED_ROLES``）、密码再认证、双人码、解锁门、安全闸全部保留；功能开关
只在它们**之后**再收一次。因此每个 key 都钉两件事：

* 关掉 → 403 且**没有副作用**（不调上游、不扣额度、不建任务、不写 ``AI_CALL_LOGS``）；
* 开着（``department`` 默认全开）→ 行为与改造前逐条一致。

另外钉住三件容易静默漂移的事：``admin`` 在策略层恒真（关掉所有开关也不影响管理
员）；未知/拼错的 key 恒 False（拼错不放行）；两个 OpenAI 兼容面端点被拒时必须
回 ``{"error": {...}}`` 而不是仓库的 ``detail``（SDK 只认前者）。

离线：目录/上游/额度/审计/仓储全部内存桩，不连 ADB、不出网。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import settings  # noqa: F401  (导入顺序与其它 AI 用例一致)
from db import backends
from routers import ai_agent_router, ai_api_keys_router, ai_router, ai_schedule_router
from services.agent import proposal
from services.ai import (
    api_keys,
    credentials,
    ocr as ai_ocr,
    providers,
    quota,
    rate_limit,
    routing,
    safety_guard,
    scheduled_runner as runner,
)
from services.roles import policy
from typings.log import inject_log
from util import agent_unlock
from util.object_id import get_current_user
from util.permission.features import has_feature

FEATURE_DENIED = "当前角色无权使用该功能"

ADMIN = "000000000000000000000001"
DEPT = "000000000000000000000002"
SECRETARY = "000000000000000000000003"
ATTACHMENT = "f" * 24
MODEL = "gpt-x"
SECRET = "sk-unit-test-0123456789abcdef"

AI_KEYS = ("ai.chat", "ai.ocr", "ai.agent", "ai.schedule", "ai.keys", "quota.manage")

ADMIN_USER = {"id": ADMIN, "per": ["admin"]}
DEPT_USER = {"id": DEPT, "per": ["department"]}
SECRETARY_USER = {"id": SECRETARY, "per": ["secretary"]}


# ---------------------------------------------------------------------------
# 桩
# ---------------------------------------------------------------------------


class StubLog:
    def with_text(self, *args, **kwargs):
        return self

    async def insert_log(self):
        return None


class Recorder:
    """记录调用参数的异步桩。"""

    def __init__(self, result=None) -> None:
        self.calls: list[tuple] = []
        self.result = result

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.result


class FakeAuditRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(self, **fields) -> str:
        self.rows.append(fields)
        return "a" * 24


class _NoRepo:
    """任何仓储访问都算「闸门没挡住」。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, *args, **kwargs):
        self.calls.append("getter")
        return self

    def __getattr__(self, name: str):
        def _boom(*_args, **_kwargs):
            self.calls.append(name)
            raise AssertionError(f"闸门没挡住就调了仓储方法 {name}")

        return _boom


def _deny(monkeypatch, key: str) -> None:
    """把某个开关关掉（等价于库里那一格 ``enabled=0``），其余 key 不受影响。"""
    real = policy.feature_enabled

    def fake(roles, feature_key):
        if feature_key == key:
            return False
        return real(roles, feature_key)

    monkeypatch.setattr(policy, "feature_enabled", fake)


def _app(router, prefix: str, user: dict, *, log: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(router.router, prefix=prefix)
    app.dependency_overrides[get_current_user] = lambda: user
    if log:
        app.dependency_overrides[inject_log] = lambda: StubLog()
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, quiet_ai_governance):
    """频控/熔断/信号量都是进程级状态：每个用例从干净起点开始。"""
    credentials._cache.clear()
    routing.reset_state()
    rate_limit.reset_state()
    routing._semaphore = None
    routing._semaphore_size = 0
    monkeypatch.setattr(settings, "AI_PROVIDERS", {}, raising=False)
    yield
    credentials._cache.clear()
    routing.reset_state()
    rate_limit.reset_state()
    routing._semaphore = None
    routing._semaphore_size = 0


def _audit_repo(monkeypatch) -> FakeAuditRepo:
    fake = FakeAuditRepo()
    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: fake)
    return fake


# ---------------------------------------------------------------------------
# 默认值 / admin 短路 / 未知 key
# ---------------------------------------------------------------------------


def test_defaults_are_the_pre_change_behaviour_for_the_ai_keys():
    department = policy.DEFAULTS["department"]["features"]
    for key in ("ai.chat", "ai.ocr", "ai.agent", "ai.schedule", "ai.keys"):
        assert key in policy.FEATURE_KEYS
        assert department[key] is True, key
    # 配额管理在改造前只有 admin：department 默认关，靠策略层的 admin 短路保管理员。
    assert "quota.manage" in policy.FEATURE_KEYS
    assert department["quota.manage"] is False

    secretary = policy.DEFAULTS["secretary"]["features"]
    assert {key for key in AI_KEYS if secretary[key]} == {"ai.ocr"}
    assert all(policy.DEFAULTS["student"]["features"][key] is False for key in AI_KEYS)


def test_admin_short_circuits_every_switch_and_unknown_keys_are_denied(monkeypatch):
    for role in policy.EDITABLE_ROLES:
        for key in policy.FEATURE_KEYS:
            monkeypatch.setitem(policy._features, (role, key), False)

    for key in policy.FEATURE_KEYS:
        assert has_feature(ADMIN_USER, key) is True, key
    # 拼错/未知 key 一律 False：即使数据里塞了一行也不放行。
    assert has_feature({"per": ["admin"]}, "ai.chta") is True  # admin 仍短路
    assert has_feature(DEPT_USER, "ai.chta") is False
    assert has_feature(DEPT_USER, "ai.chat") is False  # 被上面的 setitem 关掉了


@pytest.fixture
def gateway(monkeypatch):
    """chat / models 的最小环境：目录、上游、额度、审计全部内存桩。"""
    async def _catalog(user_oid: str) -> list[dict]:
        return [
            {
                "key": "shared:acme",
                "scope": "shared",
                "owner_oid": credentials.SHARED_OWNER,
                "name": "acme",
                "kind": "openai_compat",
                "base_url": "https://acme.example.com/v1",
                "models": [MODEL],
                "api_key_cipher": "cipher-acme",
                "api_key": None,
                "key_hint": "cdef",
                "enabled": True,
                "oid": "b" * 24,
                "created_at": None,
                "updated_at": None,
            }
        ]

    async def _resolve(entry):
        return SECRET

    async def _allowed(*_args, **_kwargs):
        return safety_guard.ALLOWED

    monkeypatch.setattr(credentials, "catalog", _catalog)
    monkeypatch.setattr(credentials, "resolve_api_key", _resolve)
    # 本文件不验证安全闸（那是 test_safety_guard* 的事）：固定放行，保证计数只
    # 反映真正的上游调用。
    monkeypatch.setattr(safety_guard, "screen", _allowed)

    upstream = Recorder({"id": "ok", "usage": {"total_tokens": 1}})
    monkeypatch.setattr(providers, "chat_completion", upstream)
    peek = Recorder((True, 0, 100))
    consume = Recorder((True, 1, 100))
    monkeypatch.setattr(quota, "peek", peek)
    monkeypatch.setattr(quota, "consume", consume)
    audit = _audit_repo(monkeypatch)

    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/ai")
    state = {"user": DEPT_USER}
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    app.dependency_overrides[ai_router.get_ai_client] = lambda: state["user"]
    app.dependency_overrides[inject_log] = lambda: StubLog()
    return SimpleNamespace(
        client=TestClient(app),
        state=state,
        upstream=upstream,
        peek=peek,
        consume=consume,
        audit=audit,
    )


def test_unknown_key_does_not_disturb_an_existing_endpoint(gateway, monkeypatch):
    """策略表里出现一个代码不认识的 key 时，既有端点照旧（不误拒、不误放）。"""
    monkeypatch.setitem(policy._features, ("department", "ai.chta"), True)
    assert has_feature(DEPT_USER, "ai.chta") is False

    response = gateway.client.get("/api/ai/v1/models")

    assert response.status_code == 200
    assert [row["id"] for row in response.json()["data"]] == [MODEL]


# ---------------------------------------------------------------------------
# ai.chat：/v1/chat/completions + /v1/models
# ---------------------------------------------------------------------------


def test_chat_switch_off_blocks_before_upstream_charge_or_audit(gateway, monkeypatch):
    _deny(monkeypatch, "ai.chat")

    response = gateway.client.post(
        "/api/ai/v1/chat/completions",
        json={"model": MODEL, "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 403
    body = response.json()
    # OpenAI 兼容面必须是 error 壳，不能混进 detail（SDK 只认前者）。
    assert "detail" not in body
    assert body["error"]["code"] == "AI_FORBIDDEN"
    assert body["error"]["message"] == FEATURE_DENIED
    assert gateway.upstream.calls == []
    assert gateway.peek.calls == []
    assert gateway.consume.calls == []
    assert gateway.audit.rows == []


def test_chat_switch_on_keeps_the_department_behaviour(gateway):
    response = gateway.client.post(
        "/api/ai/v1/chat/completions",
        json={"model": MODEL, "messages": [{"role": "user", "content": "hi"}]},
    )

    assert response.status_code == 200
    assert len(gateway.upstream.calls) == 1
    assert gateway.consume.calls[0][0][:3] == (DEPT, "chat", 1)
    assert [(row["status"], row["http_status"]) for row in gateway.audit.rows] == [
        ("success", 200)
    ]


def test_models_switch_off_uses_the_openai_error_envelope(gateway, monkeypatch):
    _deny(monkeypatch, "ai.chat")

    response = gateway.client.get("/api/ai/v1/models")

    assert response.status_code == 403
    body = response.json()
    assert "detail" not in body
    assert body["error"]["code"] == "AI_FORBIDDEN"
    assert body["error"]["message"] == FEATURE_DENIED


def test_models_switch_on_lists_the_same_models(gateway):
    response = gateway.client.get("/api/ai/v1/models")

    assert response.status_code == 200
    assert [row["id"] for row in response.json()["data"]] == [MODEL]


# ---------------------------------------------------------------------------
# ai.ocr：/ai/ocr + /ai/volunteer-ocr
# ---------------------------------------------------------------------------


@pytest.fixture
def ocr_env(monkeypatch):
    state = {"user": DEPT_USER, "password_ok": True}
    enqueue = Recorder(
        {"status": "processing", "engine": "primary", "engine_kind": "tencent"}
    )
    monkeypatch.setattr(ai_ocr, "enqueue", enqueue)

    async def _verify(token, user, **kwargs):
        if not state["password_ok"]:
            raise HTTPException(
                status_code=403, detail="AI_PASSWORD_VERIFICATION_REQUIRED"
            )
        return True

    # ``ai_router`` 是 ``from ... import require_password_verification`` 的绑定，
    # 所以要打在路由模块的命名空间上。
    monkeypatch.setattr(ai_router, "require_password_verification", _verify)
    audit = _audit_repo(monkeypatch)

    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/ai")
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    app.dependency_overrides[inject_log] = lambda: StubLog()
    return SimpleNamespace(
        client=TestClient(app), state=state, enqueue=enqueue, audit=audit
    )


def test_ocr_switch_off_blocks_both_entries_without_enqueueing(ocr_env, monkeypatch):
    _deny(monkeypatch, "ai.ocr")

    plain = ocr_env.client.post("/api/ai/ocr", json={"attachment_oid": ATTACHMENT})
    volunteer = ocr_env.client.post(
        "/api/ai/volunteer-ocr",
        json={"attachment_oid": ATTACHMENT, "verification_token": "token"},
    )

    for response in (plain, volunteer):
        assert response.status_code == 403
        assert response.json()["detail"] == FEATURE_DENIED
    assert ocr_env.enqueue.calls == []
    assert ocr_env.audit.rows == []


def test_ocr_switch_on_keeps_both_entries_working(ocr_env):
    plain = ocr_env.client.post("/api/ai/ocr", json={"attachment_oid": ATTACHMENT})
    volunteer = ocr_env.client.post(
        "/api/ai/volunteer-ocr",
        json={"attachment_oid": ATTACHMENT, "verification_token": "token"},
    )

    assert plain.status_code == 200  # enqueue 桩回 processing → 幂等分支
    assert volunteer.status_code == 200
    assert len(ocr_env.enqueue.calls) == 2


def test_ocr_keeps_the_password_and_role_gates_in_front(ocr_env, monkeypatch):
    """开关不能把既有的密码再认证与角色白名单顶掉。"""
    # 1) 密码没通过：仍是密码错误，不是功能开关的 403。
    ocr_env.state["password_ok"] = False
    bad_password = ocr_env.client.post(
        "/api/ai/volunteer-ocr",
        json={"attachment_oid": ATTACHMENT, "verification_token": "stale"},
    )
    assert bad_password.status_code == 403
    assert bad_password.json()["detail"] == "AI_PASSWORD_VERIFICATION_REQUIRED"

    # 2) 角色白名单仍在前：secretary 默认有 ai.ocr，却过不了 AI_ALLOWED_ROLES。
    ocr_env.state["password_ok"] = True
    ocr_env.state["user"] = SECRETARY_USER
    assert has_feature(SECRETARY_USER, "ai.ocr") is True
    denied = ocr_env.client.post("/api/ai/ocr", json={"attachment_oid": ATTACHMENT})
    assert denied.status_code == 403
    assert denied.json()["detail"] == "AI_FORBIDDEN"
    assert ocr_env.enqueue.calls == []


# ---------------------------------------------------------------------------
# ai.agent：解锁 + 全部读写端点
# ---------------------------------------------------------------------------


@pytest.fixture
def agent_env(monkeypatch):
    agent_unlock.clear_state()
    state = {"user": DEPT_USER}

    class _UsersRepo:
        async def get_by_oid(self, oid):
            return {"_id": oid, "id": oid, "password": "hash"}

    async def _verify(password, hashed):
        return True

    monkeypatch.setattr(backends, "get_users_repo", lambda session=None: _UsersRepo())
    monkeypatch.setattr(ai_agent_router, "verify_password_async", _verify)
    audit = _audit_repo(monkeypatch)

    app = FastAPI()
    app.include_router(ai_agent_router.router, prefix="/api/ai/agent")
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    yield SimpleNamespace(client=TestClient(app), state=state, audit=audit)
    agent_unlock.clear_state()


def test_agent_switch_off_blocks_unlock_and_does_not_authorise(agent_env, monkeypatch):
    _deny(monkeypatch, "ai.agent")

    response = agent_env.client.post(
        "/api/ai/agent/unlock", json={"password": "whatever"}
    )

    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == "AI_FORBIDDEN"
    assert body["error"]["message"] == FEATURE_DENIED
    # 解锁没发生：不给「解锁了却不能用」留下半截状态。
    assert agent_unlock.is_unlocked(DEPT) is False
    assert agent_env.audit.rows == []


def test_agent_switch_off_blocks_all_proposal_endpoints(agent_env, monkeypatch):
    _deny(monkeypatch, "ai.agent")
    monkeypatch.setattr(proposal, "confirm", _no_call)
    monkeypatch.setattr(proposal, "reject", _no_call)
    monkeypatch.setattr(proposal, "list_mine", _no_call)

    responses = [
        agent_env.client.post(f"/api/ai/agent/proposals/{'a' * 32}/confirm"),
        agent_env.client.post(f"/api/ai/agent/proposals/{'a' * 32}/reject"),
        agent_env.client.get("/api/ai/agent/proposals"),
    ]

    for response in responses:
        assert response.status_code == 403
        assert response.json()["error"]["message"] == FEATURE_DENIED
    # 开关拒绝不写 AI_CALL_LOGS（那是角色白名单拒绝的口径）。
    assert agent_env.audit.rows == []


async def _no_call(*_args, **_kwargs):
    raise AssertionError("功能开关关着却调到了服务层")


def test_agent_switch_on_keeps_unlock_and_the_unlock_gate(agent_env, monkeypatch):
    listed = Recorder([{"oid": "a" * 32, "status": "proposed"}])
    monkeypatch.setattr(proposal, "list_mine", listed)
    monkeypatch.setattr(proposal, "confirm", _no_call)

    # 未解锁：仍是既有的 422，不是开关的 403。
    locked = agent_env.client.post(f"/api/ai/agent/proposals/{'a' * 32}/confirm")
    assert locked.status_code == 422
    assert locked.json()["error"]["code"] == "AI_AGENT_UNLOCK_REQUIRED"

    unlocked = agent_env.client.post("/api/ai/agent/unlock", json={"password": "pw"})
    assert unlocked.status_code == 200
    assert agent_unlock.is_unlocked(DEPT) is True

    ok = agent_env.client.get("/api/ai/agent/proposals")
    assert ok.status_code == 200
    assert ok.json()["proposals"] == [{"oid": "a" * 32, "status": "proposed"}]
    assert len(listed.calls) == 1


# ---------------------------------------------------------------------------
# ai.schedule：/ai/scheduled-tasks*
# ---------------------------------------------------------------------------


class _ScheduleRepo:
    """``ai_scheduled_tasks`` 的最小内存版。"""

    def __init__(self, rows: dict | None = None) -> None:
        self.rows = {oid: dict(row) for oid, row in (rows or {}).items()}
        self.created: list[dict] = []
        self.deleted: list[str] = []
        self.updated: list[tuple] = []
        self._seq = 0

    async def count_active_for_owner(self, owner_oid: str) -> int:
        return sum(1 for row in self.rows.values() if row["owner_oid"] == owner_oid)

    async def create(self, **fields):
        self._seq += 1
        oid = f"{self._seq + 9:024x}"
        row = {**fields, "_id": oid, "oid": oid, "enabled": 1, "run_count": 0}
        self.rows[oid] = row
        self.created.append(dict(row))
        return dict(row)

    async def get(self, oid: str):
        row = self.rows.get(oid)
        return dict(row) if row is not None else None

    async def list_for_owner(self, owner_oid: str):
        return [dict(row) for row in self.rows.values() if row["owner_oid"] == owner_oid]

    async def update(self, oid: str, values: dict):
        self.updated.append((oid, dict(values)))
        self.rows[oid].update(values)

    async def delete(self, oid: str):
        self.deleted.append(oid)
        self.rows.pop(oid, None)

    async def list_runs(self, oid: str, *, page: int, size: int):
        return [], 0

    async def mark_result(self, oid: str, **kwargs):
        self.updated.append((oid, dict(kwargs)))


TASK = "a" * 24


def _task_row(owner: str = DEPT) -> dict:
    return {
        "_id": TASK,
        "oid": TASK,
        "owner_oid": owner,
        "name": "每日值日总结",
        "prompt_text": "总结今天的值日情况",
        "review_status": "approved",
        "schedule_kind": "interval",
        "interval_minutes": 60,
        "cron_expr": None,
        "enabled": 1,
        "model": None,
        "next_run_at": None,
        "run_count": 0,
    }


@pytest.fixture
def schedule_env(monkeypatch):
    state = {"user": DEPT_USER}
    repo = _ScheduleRepo({TASK: _task_row()})
    monkeypatch.setattr(runner, "_repo", lambda: repo)

    app = FastAPI()
    app.include_router(ai_schedule_router.router, prefix="/api/ai/scheduled-tasks")
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    return SimpleNamespace(client=TestClient(app), state=state, repo=repo)


def test_schedule_switch_off_blocks_list_and_create_before_any_write(
    schedule_env, monkeypatch
):
    _deny(monkeypatch, "ai.schedule")

    listed = schedule_env.client.get("/api/ai/scheduled-tasks")
    created = schedule_env.client.post(
        "/api/ai/scheduled-tasks",
        json={
            "name": "x",
            "prompt_text": "y",
            "schedule_kind": "interval",
            "interval_minutes": 60,
        },
    )

    for response in (listed, created):
        assert response.status_code == 403
        assert response.json()["detail"] == FEATURE_DENIED
    assert schedule_env.repo.created == []


def test_schedule_switch_off_blocks_owner_scoped_and_review_endpoints(
    schedule_env, monkeypatch
):
    _deny(monkeypatch, "ai.schedule")

    responses = [
        schedule_env.client.patch(
            f"/api/ai/scheduled-tasks/{TASK}", json={"name": "改名"}
        ),
        schedule_env.client.delete(f"/api/ai/scheduled-tasks/{TASK}"),
        schedule_env.client.post(f"/api/ai/scheduled-tasks/{TASK}/run-now"),
        schedule_env.client.get(f"/api/ai/scheduled-tasks/{TASK}/runs"),
    ]

    for response in responses:
        assert response.status_code == 403
        assert response.json()["detail"] == FEATURE_DENIED

    schedule_env.state["user"] = ADMIN_USER
    reviewed = schedule_env.client.post(
        f"/api/ai/scheduled-tasks/{TASK}/review", json={"decision": "approved"}
    )
    assert reviewed.status_code == 403
    assert reviewed.json()["detail"] == FEATURE_DENIED

    assert schedule_env.repo.updated == []
    assert schedule_env.repo.deleted == []
    assert schedule_env.repo.rows[TASK]["name"] == "每日值日总结"


def test_schedule_switch_on_keeps_department_create(schedule_env):
    # 清掉夹具里那条已存在的任务：department 的角色级条数上限是 1，留着会先吃 409。
    schedule_env.repo.rows.clear()
    response = schedule_env.client.post(
        "/api/ai/scheduled-tasks",
        json={
            "name": "每日值日总结",
            "prompt_text": "总结今天的值日情况",
            "schedule_kind": "interval",
            "interval_minutes": 60,
        },
    )

    assert response.status_code == 201
    assert len(schedule_env.repo.created) == 1
    assert response.json()["data"]["review_status"] == "pending"


# ---------------------------------------------------------------------------
# ai.keys：签发 + 修改（查询/删除保持原样）
# ---------------------------------------------------------------------------


@pytest.fixture
def keys_env(monkeypatch):
    created = Recorder(
        {"oid": "c" * 24, "name": "ci", "key_hint": "abcd", "token": "zvk_unit"}
    )
    updated = Recorder({"oid": "c" * 24, "name": "ci-2", "key_hint": "abcd"})
    deleted = Recorder(None)
    listed = Recorder([{"oid": "c" * 24, "name": "ci"}])
    monkeypatch.setattr(api_keys, "create", created)
    monkeypatch.setattr(api_keys, "update", updated)
    monkeypatch.setattr(api_keys, "delete", deleted)
    monkeypatch.setattr(api_keys, "list_for", listed)

    app = FastAPI()
    app.include_router(ai_api_keys_router.router, prefix="/api/ai/api-keys")
    app.dependency_overrides[get_current_user] = lambda: DEPT_USER
    app.dependency_overrides[inject_log] = lambda: StubLog()
    return SimpleNamespace(
        client=TestClient(app),
        created=created,
        updated=updated,
        deleted=deleted,
        listed=listed,
    )


def test_api_keys_switch_off_blocks_issue_and_patch_only(keys_env, monkeypatch):
    _deny(monkeypatch, "ai.keys")

    issued = keys_env.client.post("/api/ai/api-keys", json={"name": "ci"})
    patched = keys_env.client.patch(
        f"/api/ai/api-keys/{'c' * 24}", json={"enabled": False}
    )

    for response in (issued, patched):
        assert response.status_code == 403
        assert response.json()["detail"] == FEATURE_DENIED
    assert keys_env.created.calls == []
    assert keys_env.updated.calls == []

    # 查询与吊销保持原样：收回「签发权」不等于让人看不见/吊销不了已发出的 key。
    listed = keys_env.client.get("/api/ai/api-keys")
    revoked = keys_env.client.delete(f"/api/ai/api-keys/{'c' * 24}")
    assert listed.status_code == 200
    assert listed.json()["data"] == [{"oid": "c" * 24, "name": "ci"}]
    assert revoked.status_code == 204
    assert len(keys_env.deleted.calls) == 1


def test_api_keys_switch_on_keeps_issue_and_patch(keys_env):
    issued = keys_env.client.post("/api/ai/api-keys", json={"name": "ci"})
    patched = keys_env.client.patch(
        f"/api/ai/api-keys/{'c' * 24}", json={"enabled": False}
    )

    assert issued.status_code == 201
    assert issued.json()["data"]["token"] == "zvk_unit"
    assert patched.status_code == 200
    assert len(keys_env.created.calls) == 1
    assert len(keys_env.updated.calls) == 1


# ---------------------------------------------------------------------------
# quota.manage：admin 的三个额度管理端点
# ---------------------------------------------------------------------------


@pytest.fixture
def quota_env(monkeypatch):
    snapshot = Recorder({"kinds": []})
    set_limit = Recorder({"kind": "chat", "limit": 5})
    reset = Recorder({"kind": "chat", "limit": 5})
    monkeypatch.setattr(quota, "admin_snapshot", snapshot)
    monkeypatch.setattr(quota, "set_user_limit", set_limit)
    monkeypatch.setattr(quota, "reset_used", reset)

    users = _NoRepo()
    monkeypatch.setattr(backends, "get_users_repo", users)
    audit = _audit_repo(monkeypatch)

    app = FastAPI()
    app.include_router(ai_router.router, prefix="/api/ai")
    app.dependency_overrides[get_current_user] = lambda: ADMIN_USER
    app.dependency_overrides[inject_log] = lambda: StubLog()
    return SimpleNamespace(
        client=TestClient(app),
        users=users,
        snapshot=snapshot,
        set_limit=set_limit,
        reset=reset,
        audit=audit,
    )


def test_quota_manage_switch_off_blocks_all_three_admin_endpoints(
    quota_env, monkeypatch
):
    _deny(monkeypatch, "quota.manage")
    target = "000000000000000000000009"

    responses = [
        quota_env.client.get(f"/api/ai/quota/{target}"),
        quota_env.client.put(
            f"/api/ai/quota/{target}", json={"kind": "chat", "limit_count": 5}
        ),
        quota_env.client.post(f"/api/ai/quota/{target}/reset", json={"kind": "chat"}),
    ]

    for response in responses:
        assert response.status_code == 403
        assert response.json()["detail"] == FEATURE_DENIED

    # 连目标用户的角色都没查：闸门在仓库访问之前。
    assert quota_env.users.calls == []
    assert quota_env.snapshot.calls == []
    assert quota_env.set_limit.calls == []
    assert quota_env.reset.calls == []
    assert quota_env.audit.rows == []


def test_quota_manage_switch_on_keeps_admin_endpoints(quota_env, monkeypatch):
    class _UsersRepo:
        async def get_for_auth(self, oid):
            return {"_id": oid, "id": oid, "roles": ["department"]}

    monkeypatch.setattr(backends, "get_users_repo", lambda session=None: _UsersRepo())
    target = "000000000000000000000009"

    read = quota_env.client.get(f"/api/ai/quota/{target}")
    written = quota_env.client.put(
        f"/api/ai/quota/{target}", json={"kind": "chat", "limit_count": 5}
    )
    reset = quota_env.client.post(
        f"/api/ai/quota/{target}/reset", json={"kind": "chat"}
    )

    assert read.status_code == 200
    assert written.status_code == 200
    assert reset.status_code == 200
    assert len(quota_env.snapshot.calls) == 1
    assert len(quota_env.set_limit.calls) == 1
    assert len(quota_env.reset.calls) == 1
