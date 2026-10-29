"""``/api/ai/models``：AI 模型目录（Agent Fuel 倍率 + 元数据）的 HTTP 契约。

只起一个挂 ``ai_models_router`` 的迷你 app：覆盖行仓储是内存桩，渠道目录
（``credentials.list_entries``）也是桩——不连库、不出网。

守的是四件事：

* ``GET`` 是**并集**（``AI_MODELS`` 覆盖行 ∪ 渠道里的模型），缺覆盖行时
  ``rate=1`` / ``enabled=true``，``in_catalog`` 按渠道判断；
* ``PUT`` 是**部分更新**：没传的字段保持原值，自定义模型（不在任何渠道里）也能建，
  响应 ``in_catalog=false``；
* 非法 rate / context_length 回 **400 + 中文文案**（不是 500，也不是 Pydantic 的
  422 结构错误）；
* 非 admin 一律 403；``DELETE`` 只删覆盖行、幂等回 204。

同一份文件里还钉住「凭据的 models 字段放宽」：``KeyCreateRequest`` /
``KeyUpdateRequest`` 必须收 ``{id, context}`` 对象（否则前端的「按模型填上下文」
会在 HTTP 层 422），而非法形状由 ``credentials._clean_models`` 回 400。
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from routers import ai_models_router, ai_router
from db import backends
from db.repos.oracle.ai_models import OracleAIModelsRepo
from services.ai import credentials
from services.ai.credentials import CredentialError
from util.object_id import get_current_user

PREFIX = "/api/ai/models"
ADMIN_OID = "64b7f0c2e1a3b4c5d6e7fa01"
STUDENT_OID = "64b7f0c2e1a3b4c5d6e7fa02"

ADMIN = {"id": ADMIN_OID, "per": ["admin"]}
STUDENT = {"id": STUDENT_OID, "per": ["student"]}

ROW_KEYS = {
    "model",
    "display_name",
    "provider",
    "providers",
    "context_length",
    "rate",
    "enabled",
    "in_catalog",
    "notes",
    "updated_at",
}


class _FakeModelsRepo:
    """``AIModelsRepo`` 的内存实现：行形状与 Oracle 版一致。"""

    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    def seed(self, model: str, **fields) -> None:
        row = {
            "model": model,
            "display_name": None,
            "provider": None,
            "context_length": None,
            "rate": 1.0,
            "enabled": True,
            "notes": None,
            "updated_at": None,
            "updated_by": None,
        }
        row.update(fields)
        self.rows[model] = row

    async def list_all(self) -> list[dict]:
        return [dict(self.rows[model]) for model in sorted(self.rows)]

    async def get(self, model: str) -> dict | None:
        row = self.rows.get(model)
        return dict(row) if row is not None else None

    async def upsert(self, model: str, *, fields, actor=None) -> dict:
        if model not in self.rows:
            self.seed(model)
        row = self.rows[model]
        row.update(fields)
        row["updated_at"] = datetime(2026, 10, 29, 12, 0, 0)
        row["updated_by"] = actor
        return dict(row)

    async def delete(self, model: str) -> bool:
        return self.rows.pop(model, None) is not None


def _credential(name: str, models, contexts=None, scope: str = "shared") -> dict:
    """``credentials.list_entries`` 的视图形状（只需要模型与渠道名）。"""
    return {
        "name": name,
        "scope": scope,
        "models": list(models),
        "model_contexts": dict(contexts or {}),
    }


@pytest.fixture
def api(monkeypatch):
    repo = _FakeModelsRepo()
    views = [
        _credential("team", ["gpt-x", "gpt-y"], {"gpt-y": 128000}),
        _credential("personal-one", ["gpt-x"], scope="personal"),
    ]

    async def _list_entries(_user):
        return [dict(view) for view in views]

    monkeypatch.setattr(backends, "get_ai_models_repo", lambda *a, **k: repo)
    monkeypatch.setattr(credentials, "list_entries", _list_entries)

    app = FastAPI()
    app.include_router(ai_models_router.router, prefix=PREFIX)
    app.dependency_overrides[get_current_user] = lambda: ADMIN
    return TestClient(app), repo


def _as(client: TestClient, user: dict) -> None:
    client.app.dependency_overrides[get_current_user] = lambda: user


# ---------------------------------------------------------------------------
# GET：并集 + 默认口径
# ---------------------------------------------------------------------------


def test_get_merges_catalog_and_override_rows(api) -> None:
    client, repo = api
    repo.seed(
        "gpt-x",
        display_name="GPT X",
        provider="openai",
        rate=2.5,
        enabled=False,
        notes="贵一倍",
    )
    repo.seed("custom-model", rate=4)

    body = client.get(PREFIX).json()

    assert body["status"] == "ok"
    assert body["code"] == 200
    models = {row["model"]: row for row in body["data"]["models"]}
    assert set(models) == {"gpt-x", "gpt-y", "custom-model"}

    gpt_x = models["gpt-x"]
    assert set(gpt_x) == ROW_KEYS
    assert gpt_x["display_name"] == "GPT X"
    assert gpt_x["provider"] == "openai"
    # providers 是渠道名集合，去重且排序；同名列在 personal 里只算一个渠道名。
    assert gpt_x["providers"] == ["personal-one", "team"]
    assert gpt_x["rate"] == 2.5
    assert gpt_x["enabled"] is False
    assert gpt_x["in_catalog"] is True


def test_get_defaults_when_the_override_row_is_missing(api) -> None:
    client, _repo = api

    models = {row["model"]: row for row in client.get(PREFIX).json()["data"]["models"]}

    # 没有覆盖行：rate=1 / enabled=true，但 in_catalog 仍按渠道判断。
    assert models["gpt-y"]["rate"] == 1.0
    assert models["gpt-y"]["enabled"] is True
    assert models["gpt-y"]["in_catalog"] is True
    # 上下文窗口回落到凭据里配的值（管理页要能看见渠道上填的窗口）。
    assert models["gpt-y"]["context_length"] == 128000


def test_get_lists_override_only_models_as_not_in_catalog(api) -> None:
    client, repo = api
    repo.seed("custom-model", rate=4)

    models = {row["model"]: row for row in client.get(PREFIX).json()["data"]["models"]}

    assert models["custom-model"]["providers"] == []
    assert models["custom-model"]["in_catalog"] is False
    assert models["custom-model"]["rate"] == 4.0


def test_get_is_admin_only(api) -> None:
    client, _repo = api
    _as(client, STUDENT)

    response = client.get(PREFIX)

    assert response.status_code == 403
    assert response.json()["detail"] == "Permission denied"


# ---------------------------------------------------------------------------
# PUT：upsert + 部分更新 + 校验
# ---------------------------------------------------------------------------


def test_put_creates_a_custom_model_row(api) -> None:
    client, repo = api

    response = client.put(
        f"{PREFIX}/custom-model",
        json={
            "display_name": "自建模型",
            "provider": "local",
            "context_length": 32000,
            "rate": 3.25,
            "notes": "内部推理服务",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200
    row = body["data"]
    assert set(row) == ROW_KEYS
    assert row["model"] == "custom-model"
    assert row["rate"] == 3.25
    assert row["context_length"] == 32000
    assert row["in_catalog"] is False  # 不在任何渠道里：这正是「自定义模型」
    assert repo.rows["custom-model"]["updated_by"] == ADMIN_OID


def test_put_partial_update_keeps_untouched_fields(api) -> None:
    client, repo = api
    repo.seed("gpt-x", rate=3, display_name="GPT X", enabled=False)

    body = client.put(f"{PREFIX}/gpt-x", json={"notes": "换了个说明"}).json()

    assert body["data"]["rate"] == 3.0
    assert body["data"]["display_name"] == "GPT X"
    assert body["data"]["enabled"] is False
    assert body["data"]["notes"] == "换了个说明"
    # 渠道事实仍然来自凭据，不被覆盖行改写。
    assert body["data"]["in_catalog"] is True
    assert body["data"]["providers"] == ["personal-one", "team"]
    assert repo.rows["gpt-x"]["rate"] == 3


def test_put_explicit_null_clears_optional_columns(api) -> None:
    client, repo = api
    repo.seed("gpt-x", display_name="GPT X", notes="旧说明", context_length=64000)

    body = client.put(
        f"{PREFIX}/gpt-x",
        json={"display_name": None, "notes": None, "context_length": None},
    ).json()

    assert body["data"]["display_name"] is None
    assert body["data"]["notes"] is None
    assert body["data"]["context_length"] is None
    assert repo.rows["gpt-x"]["display_name"] is None


@pytest.mark.parametrize(
    "payload",
    [
        {"rate": 0},
        {"rate": -1},
        {"rate": 1001},
        {"rate": None},
        {"context_length": 0},
        {"context_length": -5},
        {"display_name": "x" * 121},
        {"notes": "x" * 401},
        {"provider": "x" * 41},
        {"display_name": "带\n换行"},
    ],
)
def test_put_rejects_out_of_range_values_with_400(api, payload) -> None:
    client, repo = api

    response = client.put(f"{PREFIX}/gpt-x", json=payload)

    assert response.status_code == 400, payload
    assert response.json()["detail"]  # 中文文案（非空且不是英文 code）
    assert "gpt-x" not in repo.rows  # 校验失败不落任何行


def test_put_rejects_non_positive_rates_even_for_new_models(api) -> None:
    client, repo = api

    response = client.put(f"{PREFIX}/custom-model", json={"rate": 0})

    assert response.status_code == 400
    assert repo.rows == {}


def test_put_rejects_overlong_model_names(api) -> None:
    client, _repo = api

    response = client.put(f"{PREFIX}/{'m' * 81}", json={"rate": 1})

    assert response.status_code == 400


def test_put_is_admin_only(api) -> None:
    client, repo = api
    _as(client, STUDENT)

    response = client.put(f"{PREFIX}/gpt-x", json={"rate": 2})

    assert response.status_code == 403
    assert repo.rows == {}


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


def test_delete_removes_only_the_override_row(api) -> None:
    client, repo = api
    repo.seed("gpt-x", rate=2)

    response = client.delete(f"{PREFIX}/gpt-x")

    assert response.status_code == 204
    assert "gpt-x" not in repo.rows
    # 凭据里的模型仍在：删配置 = 回到默认倍率，不是把模型删掉。
    models = {row["model"]: row for row in client.get(PREFIX).json()["data"]["models"]}
    assert models["gpt-x"]["rate"] == 1.0
    assert models["gpt-x"]["in_catalog"] is True


def test_delete_is_idempotent(api) -> None:
    client, _repo = api

    assert client.delete(f"{PREFIX}/never-configured").status_code == 204


def test_delete_is_admin_only(api) -> None:
    client, repo = api
    repo.seed("gpt-x", rate=2)
    _as(client, STUDENT)

    response = client.delete(f"{PREFIX}/gpt-x")

    assert response.status_code == 403
    assert "gpt-x" in repo.rows


# ---------------------------------------------------------------------------
# 凭据 models 字段：对象形态不再 422，非法形状 400 + 中文
# ---------------------------------------------------------------------------


def test_key_schemas_accept_object_models() -> None:
    """``{id, context}`` 必须过 Pydantic 层——否则前端的按模型填上下文会 422。"""
    created = ai_router.KeyCreateRequest(
        scope="shared",
        name="team",
        base_url="https://api.example.com",
        models=["gpt-x", {"id": "gpt-y", "context": 128000}],
        api_key="sk-unit-test-0123456789abcdef",
    )
    assert created.models == ["gpt-x", {"id": "gpt-y", "context": 128000}]

    updated = ai_router.KeyUpdateRequest(
        models=[{"id": "gpt-y", "context": 64000}]
    )
    assert updated.models == [{"id": "gpt-y", "context": 64000}]


@pytest.mark.parametrize(
    "models",
    [
        [{"foo": "bar"}],
        [{"id": 123}],
        [{"id": "gpt-x", "context": 0}],
        [{"id": "gpt-x", "context": "big"}],
        [{"id": "gpt-x", "extra": 1}],
        [123],
    ],
)
def test_clean_models_rejects_bad_shapes_with_chinese_message(models) -> None:
    with pytest.raises(CredentialError) as failure:
        credentials._clean_models(models)

    assert failure.value.code == "AI_KEY_INVALID"
    assert failure.value.status_code == 400
    # 文案直接进管理端提示框：必须是中文（不是 "models must be ..."）。
    assert any("\u4e00" <= ch <= "\u9fff" for ch in failure.value.message)


def test_clean_models_keeps_valid_objects_and_dedupes() -> None:
    cleaned = credentials._clean_models(
        ["gpt-x", {"id": "gpt-y", "context": 128000}, "gpt-x"]
    )

    assert cleaned == ["gpt-x", {"id": "gpt-y", "context": 128000}]


# ---------------------------------------------------------------------------
# 接线：路由走 db.backends 工厂（与其它路由同一条注册路径）
# ---------------------------------------------------------------------------

def test_backends_factory_is_registered() -> None:
    """``get_ai_models_repo`` 必须和其它仓储工厂一样出现在 ``__all__`` 里。

    路由层只依赖工厂名；漏登记不会报错，只会让「统一收敛仓储注册」时找不到它。
    """
    assert "get_ai_models_repo" in backends.__all__

    repo = backends.get_ai_models_repo()
    assert isinstance(repo, OracleAIModelsRepo)
    # 工厂只做懒导入 + 构造：不传 session 时仓储自己管会话，构造期不连库。
    assert repo._session is None
