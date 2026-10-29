"""Agent Fuel（AF）计费：公式、倍率解析、五类扣费点、个人 key 豁免。

守的是三件事：

1. **只有一个量纲**：``AF = max(1, ceil(total_tokens / 1000 × rate))``，实测与
   估算同源（改造前估算返回 token 个数，比实测大 1000 倍）；
2. **倍率只在一处解析**：``AI_MODELS`` 覆盖行 → 完整 ID 优先、裸名兜底、默认 1，
   进程内 60 秒缓存，管理端写成功后立刻失效；
3. **个人 key 不扣 AF**（钱是管理员自己出的），但流水照记。
"""

from __future__ import annotations

import pytest

from db import backends
from routers import ai_models_router
from services.agent import executor
from services.ai import credentials, model_rates, providers, rate_limit, routing, usage

USER = "000000000000000000000003"
ADMIN = {"id": USER, "per": ["admin"]}
MODEL = "gpt-x"
QUALIFIED = f"lab/{MODEL}"


# ---------------------------------------------------------------------------
# 纯函数：公式与估算
# ---------------------------------------------------------------------------


def test_af_formula_is_thousand_tokens_times_rate_with_a_floor_of_one() -> None:
    assert usage.af_units(1000, 1.0) == 1
    assert usage.af_units(1001, 1.0) == 2
    assert usage.af_units(1200, 2.0) == 3
    assert usage.af_units(None, 2.0) == 1
    assert usage.af_units(0, 1000.0) == 1


def test_estimate_af_shares_the_measured_formula() -> None:
    """估算不再差 1000 倍：8000 字符 ≈ 2000 token ≈ 2 AF（倍率 1）。"""
    assert usage.estimate_tokens_from_chars(4000, 4000) == 2000
    assert usage.estimate_af(4000, 4000) == usage.af_units(2000, 1.0) == 2
    assert usage.estimate_af(4000, 4000, 2.0) == 4


# ---------------------------------------------------------------------------
# 倍率解析
# ---------------------------------------------------------------------------


class FakeModelsRepo:
    def __init__(self, rows: list[dict] | None = None, *, fail: bool = False) -> None:
        self.rows = list(rows or [])
        self.fail = fail
        self.calls = 0

    async def list_all(self) -> list[dict]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("adb is down")
        return [dict(row) for row in self.rows]

    async def get(self, model: str):
        for row in self.rows:
            if row["model"].lower() == model.lower():
                return dict(row)
        return None

    async def upsert(self, model: str, *, fields, actor=None):
        row = {
            "model": model,
            "provider": fields.get("provider"),
            "rate": float(fields.get("rate") or 1.0),
            "enabled": bool(fields.get("enabled", True)),
            "display_name": fields.get("display_name"),
            "context_length": fields.get("context_length"),
            "notes": fields.get("notes"),
            "updated_at": None,
        }
        self.rows = [item for item in self.rows if item["model"] != model] + [row]
        return row

    async def delete(self, model: str) -> bool:
        before = len(self.rows)
        self.rows = [item for item in self.rows if item["model"] != model]
        return len(self.rows) != before


@pytest.fixture(autouse=True)
def _fresh_rate_cache():
    model_rates.invalidate()
    yield
    model_rates.invalidate()


@pytest.fixture
def rate_models(monkeypatch):
    repo = FakeModelsRepo()
    monkeypatch.setattr(backends, "get_ai_models_repo", lambda session=None: repo)
    return repo


@pytest.mark.asyncio
async def test_rate_resolution_prefers_full_id_then_bare_name(rate_models) -> None:
    rate_models.rows = [
        {"model": "gpt-x", "provider": "lab", "rate": 2.5, "enabled": True},
        {"model": "gpt-y", "provider": None, "rate": 0.5, "enabled": True},
    ]

    assert await model_rates.resolve_rate(QUALIFIED) == 2.5  # 完整 ID
    assert await model_rates.resolve_rate("LAB/GPT-X") == 2.5  # 大小写不敏感
    assert await model_rates.resolve_rate("gpt-y") == 0.5  # 裸名
    assert await model_rates.resolve_rate("other/gpt-y") == 0.5  # 完整 ID 未命中 → 裸名
    assert await model_rates.resolve_rate("gpt-z") == 1.0  # 完全没配过
    assert await model_rates.resolve_rate(None) == 1.0
    assert await model_rates.resolve_rate("") == 1.0


@pytest.mark.asyncio
async def test_ttl_cache_reads_the_table_once(rate_models) -> None:
    rate_models.rows = [{"model": "gpt-x", "provider": "lab", "rate": 3, "enabled": True}]

    for _ in range(5):
        assert await model_rates.resolve_rate(QUALIFIED) == 3
    assert rate_models.calls == 1

    model_rates.invalidate()
    assert await model_rates.resolve_rate(QUALIFIED) == 3
    assert rate_models.calls == 2


@pytest.mark.asyncio
async def test_reload_failure_degrades_to_base_rate_instead_of_raising(
    rate_models,
) -> None:
    rate_models.rows = [{"model": "gpt-x", "provider": "lab", "rate": 7, "enabled": True}]
    assert await model_rates.resolve_rate(QUALIFIED) == 7

    model_rates.invalidate()
    rate_models.fail = True

    # 读库失败不抛：计费不能因为目录查询失败把已经生成的答案丢掉。
    assert await model_rates.resolve_rate(QUALIFIED) == 1.0
    # 失败在一个 TTL 内不重试（否则每个请求都撞一次挂掉的库）。
    assert await model_rates.resolve_rate(QUALIFIED) == 1.0
    assert rate_models.calls == 2


def test_bad_rates_fall_back_to_one(rate_models) -> None:
    table = model_rates._build(
        [
            {"model": "a", "rate": 0},
            {"model": "b", "rate": -2},
            {"model": "c", "rate": None},
            {"model": "d", "rate": 1.5},
        ]
    )
    assert table["a"] == 1.0
    assert table["b"] == 1.0
    assert table["c"] == 1.0
    assert table["d"] == 1.5


@pytest.mark.asyncio
async def test_admin_write_invalidates_the_rate_cache(monkeypatch, rate_models) -> None:
    """PUT / DELETE 成功后缓存立刻失效（否则新价最多晚 60 秒）。"""
    rate_models.rows = [{"model": MODEL, "provider": "lab", "rate": 2, "enabled": True}]
    assert await model_rates.resolve_rate(QUALIFIED) == 2

    async def _no_catalog(_user):
        return {}, {}

    monkeypatch.setattr(ai_models_router, "_catalog", _no_catalog)
    monkeypatch.setattr(ai_models_router, "_repo", lambda: rate_models)

    await ai_models_router.delete_model(MODEL, user=ADMIN)
    # 缓存清空后立刻回默认倍率；下一次计费会重新读表。
    assert model_rates.rate_for(QUALIFIED) == 1.0
    assert model_rates._rates == {}

    # PUT 同样要失效：改完倍率，下一个请求就按新价计费。
    assert await model_rates.resolve_rate(QUALIFIED) == 1.0  # 表里那行已被删
    await ai_models_router.update_model(
        MODEL,
        ai_models_router.ModelUpdateRequest(rate=4.0, provider="lab"),
        user=ADMIN,
    )
    assert model_rates._rates == {}
    assert await model_rates.resolve_rate(QUALIFIED) == 4.0


# ---------------------------------------------------------------------------
# 扣费点（agent 轮 / 压缩轮）—— 用一个最小执行器环境跑真实 run 循环
# ---------------------------------------------------------------------------


class FakeAuditRepo:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    async def insert(self, **fields) -> str:
        self.rows.append(fields)
        return "a" * 24


class FakeQuotaRepo:
    def __init__(self) -> None:
        self.consumed: list[tuple] = []
        self.reads: list[tuple] = []

    async def get(self, user_oid, period, kind):
        self.reads.append((user_oid, kind))
        return None

    async def consume(self, user_oid, period, kind, amount, default_limit):
        self.consumed.append((user_oid, kind, amount))
        return True, amount, default_limit


def _entry(scope: str = "shared") -> dict:
    return {
        "key": f"{scope}:64b7f0c2e1a3b4c5d6e7fc01",
        "scope": scope,
        "owner_oid": credentials.SHARED_OWNER,
        "name": "lab",
        "kind": "openai_compat",
        "base_url": "https://lab.example.com/v1",
        "models": [MODEL],
        "api_key_cipher": "cipher",
        "api_key": None,
        "key_hint": "cdef",
        "enabled": True,
        "oid": "64b7f0c2e1a3b4c5d6e7fc01",
        "created_at": None,
        "updated_at": None,
        "expires_at": None,
    }


def _completion(total_tokens: int | None, content: str = "ok") -> dict:
    body: dict = {
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}]
    }
    if total_tokens is not None:
        body["usage"] = {
            "prompt_tokens": total_tokens - 20,
            "completion_tokens": 20,
            "total_tokens": total_tokens,
        }
    return body


@pytest.fixture
def env(monkeypatch, quiet_ai_governance, rate_models):
    audit = FakeAuditRepo()
    quota_repo = FakeQuotaRepo()

    await_return: dict = {}

    async def catalog(_user_oid):
        return [await_return["entry"]]

    async def resolve_key(_entry):
        return "sk-unit-test-0123456789abcdef"

    monkeypatch.setattr(backends, "get_ai_call_logs_repo", lambda session=None: audit)
    monkeypatch.setattr(backends, "get_ai_quota_repo", lambda session=None: quota_repo)
    monkeypatch.setattr(credentials, "catalog", catalog)
    monkeypatch.setattr(credentials, "resolve_api_key", resolve_key)

    credentials._cache.clear()
    routing.reset_state()
    rate_limit.reset_state()
    routing._semaphore = None
    routing._semaphore_size = 0
    await_return["entry"] = _entry()

    class _Env:
        pass

    box = _Env()
    box.audit = audit
    box.quota = quota_repo
    box.models = rate_models
    box.entry = await_return

    yield box

    credentials._cache.clear()
    routing.reset_state()
    rate_limit.reset_state()
    routing._semaphore = None
    routing._semaphore_size = 0


@pytest.mark.asyncio
async def test_agent_round_is_charged_in_af_with_the_model_rate(monkeypatch, env) -> None:
    """1200 token × 倍率 2 = 2.4 → 3 AF（ceil，不是 1 也不是 2）。"""
    monkeypatch.setattr(
        providers, "chat_completion", _scripted([_completion(1200)])
    )
    env.models.rows = [
        {"model": MODEL, "provider": "lab", "rate": 2.0, "enabled": True}
    ]

    await executor.run(user=ADMIN, messages=[{"role": "user", "content": "hi"}],
                       model=MODEL)

    assert env.quota.consumed == [(USER, "chat", 3)]
    assert env.audit.rows[0]["units"] == 3


@pytest.mark.asyncio
async def test_agent_round_defaults_to_base_rate_when_the_model_is_unpriced(
    monkeypatch, env
) -> None:
    monkeypatch.setattr(
        providers, "chat_completion", _scripted([_completion(1200)])
    )

    await executor.run(user=ADMIN, messages=[{"role": "user", "content": "hi"}],
                       model=MODEL)

    assert env.quota.consumed == [(USER, "chat", 2)]


@pytest.mark.asyncio
async def test_agent_round_without_usage_estimates_in_af(monkeypatch, env) -> None:
    """上游不回 usage：按字符估算后**仍走 AF 公式**（不是恒定 1）。"""
    monkeypatch.setattr(
        providers,
        "chat_completion",
        _scripted([_completion(None, content="x" * 8000)]),
    )

    await executor.run(user=ADMIN, messages=[{"role": "user", "content": "hi"}],
                       model=MODEL)

    charged = env.quota.consumed[0][2]
    assert charged >= 2  # 8000 字符 ≈ 2000 token ≈ 2 AF
    assert env.audit.rows[0]["units"] == charged


@pytest.mark.asyncio
async def test_personal_key_is_never_charged(monkeypatch, env) -> None:
    """个人 key：peek 与 consume 都不发生，流水照记 AF（对账要能看出这次多大）。"""
    env.entry["entry"] = _entry("personal")
    env.models.rows = [
        {"model": MODEL, "provider": "lab", "rate": 2.0, "enabled": True}
    ]
    monkeypatch.setattr(
        providers, "chat_completion", _scripted([_completion(1200)])
    )

    await executor.run(user=ADMIN, messages=[{"role": "user", "content": "hi"}],
                       model=MODEL)

    assert env.quota.consumed == []
    assert env.quota.reads == []
    assert env.audit.rows[0]["key_scope"] == "personal"
    assert env.audit.rows[0]["units"] == 3


def _scripted(responses: list[dict]):
    queue = list(responses)

    async def _call(payload, entry, api_key):
        if not queue:
            raise AssertionError("upstream called more times than scripted")
        return queue.pop(0)

    return _call
