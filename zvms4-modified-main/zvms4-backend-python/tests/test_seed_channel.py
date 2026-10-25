"""``misc/seed_channel.py``：SiliconFlow 公开渠道的落库计划（全离线）。

这个脚本能造成的最大事故不是「渠道不可用」，而是「把付费模型写进 shared 凭据」——
平台替所有登录用户买单，而且不会有任何报错。所以这里钉的是**边界**：

* 白名单为空 → 在碰网络之前就失败（退出码 2），不回落「上游全部模型」；
* ``--models`` 只能收窄，越界即拒；
* ``--free-only`` 的交集只做过滤，不扩集；
* 去重与 20 上限（``credentials._MODELS_MAX``）；
* ``--dry-run`` 不碰写库路径。

上游 ``/models`` 用假函数注入，DNS 用假解析器替换：用例不出网、不连库。
"""

from __future__ import annotations

import ipaddress

import pytest

import settings
from db import backends
from misc import seed_channel
from services.ai import config as ai_config
from services.ai import credentials, crypto

SECRET = "sk-siliconflow-unit-test-0123456789"
WHITELIST = ["Qwen/Qwen3-8B", "THUDM/GLM-4.1V-9B-Thinking"]


# ---------------------------------------------------------------------------
# 假仓储（与 test_ai_credentials.py 同形状：只实现服务层用到的四个方法）
# ---------------------------------------------------------------------------


class DuplicateCredential(Exception):
    """与仓储同名，``credentials._is_duplicate`` 按类名判定。"""


class FakeRepo:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows: dict[str, dict] = {row["_id"]: dict(row) for row in rows or []}
        self.writes: list[str] = []

    async def list_visible(self, user_oid: str) -> list[dict]:
        return [dict(row) for row in self.rows.values()]

    async def list_all(self) -> list[dict]:
        return [dict(row) for row in self.rows.values()]

    async def get(self, oid: str) -> dict | None:
        row = self.rows.get(oid)
        return dict(row) if row is not None else None

    async def create(self, **fields) -> str:
        self.writes.append("create")
        for row in self.rows.values():
            if (
                row["scope"] == fields["scope"]
                and row["owner_oid"] == fields["owner_oid"]
                and row["name"] == fields["name"]
            ):
                raise DuplicateCredential(fields["name"])
        oid = f"{len(self.rows) + 100:024x}"
        self.rows[oid] = {
            "_id": oid,
            "scope": fields["scope"],
            "owner_oid": fields["owner_oid"],
            "name": fields["name"],
            "kind": fields["kind"],
            "base_url": fields["base_url"],
            "models": list(fields["models"]),
            "api_key_cipher": fields["api_key_cipher"],
            "key_hint": fields.get("key_hint"),
            "enabled": fields["enabled"],
            "created_at": None,
            "updated_at": None,
        }
        return oid

    async def update(self, oid: str, **fields) -> bool:
        self.writes.append("update")
        row = self.rows.get(oid)
        if row is None:
            return False
        row.update(fields)
        return True

    async def delete(self, oid: str) -> bool:
        self.writes.append("delete")
        return self.rows.pop(oid, None) is not None


class FakeFetch:
    """假的上游 ``/models``：记录调用次数，返回预置结果。"""

    def __init__(self, models: list[str] | None = None) -> None:
        self.models = models or []
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, base_url: str, api_key: str) -> list[str]:
        self.calls.append((base_url, api_key))
        return list(self.models)


def _shared_row(*, models: tuple[str, ...] = ("old-model",)) -> dict:
    return {
        "_id": "0000000000000000000000aa",
        "scope": "shared",
        "owner_oid": credentials.SHARED_OWNER,
        "name": "siliconflow",
        "kind": "openai_compat",
        "base_url": "https://api.siliconflow.cn/v1",
        "models": list(models),
        "api_key_cipher": crypto.encrypt_secret(SECRET),
        "key_hint": crypto.key_hint(SECRET),
        "enabled": True,
        "created_at": None,
        "updated_at": None,
    }


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    """主密钥固定 + DNS 不解析 + 凭据目录缓存清空。"""
    credentials._cache.clear()
    monkeypatch.setenv(crypto._SECRET_ENV, "unit-test-master-secret")
    monkeypatch.setattr(crypto, "_master_key", None)
    monkeypatch.setattr(settings, "AI_PROVIDERS", {}, raising=False)
    # base_url 校验会真的查 DNS（create_entry → _clean_base_url）；离线环境直接摊平。
    monkeypatch.setattr(
        credentials, "_resolve_host", lambda host: [ipaddress.ip_address("93.184.216.34")]
    )
    yield
    credentials._cache.clear()
    crypto._master_key = None


@pytest.fixture
def repo(monkeypatch) -> FakeRepo:
    fake = FakeRepo()
    monkeypatch.setattr(backends, "get_ai_credentials_repo", lambda session=None: fake)
    return fake


@pytest.fixture
def key_env(monkeypatch) -> str:
    monkeypatch.setenv(seed_channel.DEFAULT_KEY_ENV, SECRET)
    return seed_channel.DEFAULT_KEY_ENV


@pytest.fixture
def fetch(monkeypatch) -> FakeFetch:
    fake = FakeFetch(WHITELIST)
    monkeypatch.setattr(seed_channel, "_fetch_model_ids", fake)
    return fake


def _whitelist(monkeypatch, models: list[str]) -> None:
    monkeypatch.setattr(settings, "AI_SILICONFLOW_FREE_MODELS", models, raising=False)


def _row(scope: str = "shared", name: str = "siliconflow") -> dict:
    row = _shared_row()
    row["scope"] = scope
    row["name"] = name
    return row


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------


def test_parse_args_defaults():
    args = seed_channel.parse_args([])
    assert args.name == "siliconflow"
    assert args.base_url == "https://api.siliconflow.cn/v1"
    assert args.key_env == "SILICONFLOW_API_KEY"
    assert args.key_file is None
    assert args.free_only is False
    assert args.dry_run is False
    assert args.models == []


def test_parse_args_overrides():
    args = seed_channel.parse_args(
        [
            "--name",
            "sf-test",
            "--base-url",
            "https://example.com/v1",
            "--key-file",
            "/tmp/x.env",
            "--key-env",
            "OTHER_KEY",
            "--models",
            "a,b",
            "--models",
            "c",
            "--free-only",
            "--dry-run",
        ]
    )
    assert args.name == "sf-test"
    assert args.base_url == "https://example.com/v1"
    assert args.key_file == "/tmp/x.env"
    assert args.key_env == "OTHER_KEY"
    assert args.free_only is True
    assert args.dry_run is True
    assert seed_channel._split_models(args.models) == ["a", "b", "c"]


def test_unknown_flag_exits_2():
    with pytest.raises(SystemExit) as excinfo:
        seed_channel.parse_args(["--nope"])
    assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# 白名单为空
# ---------------------------------------------------------------------------


def test_empty_whitelist_exits_2_before_network(monkeypatch, repo, fetch, key_env, capsys):
    _whitelist(monkeypatch, [])
    code = seed_channel.main(["--free-only", "--dry-run"])

    assert code == 2
    assert fetch.calls == []  # 网络之前就停
    assert repo.writes == []
    assert "AI_SILICONFLOW_FREE_MODELS" in capsys.readouterr().err


def test_missing_whitelist_key_treated_as_empty(monkeypatch, repo, fetch, key_env, capsys):
    """老部署的 settings 里没有这个键：等价于「没配」，不是「全放开」。"""
    monkeypatch.delattr(settings, "AI_SILICONFLOW_FREE_MODELS", raising=False)
    code = seed_channel.main(["--free-only", "--dry-run"])

    assert code == 2
    assert fetch.calls == []
    assert "AI_SILICONFLOW_FREE_MODELS" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# 交集
# ---------------------------------------------------------------------------


def test_free_only_intersects_whitelist(monkeypatch, fetch):
    fetch.models = ["THUDM/GLM-4.1V-9B-Thinking", "paid/model", "extra/model"]
    models = seed_channel.select_models(
        WHITELIST, available=fetch.models
    )
    assert models == ["THUDM/GLM-4.1V-9B-Thinking"]


def test_free_only_never_widens_past_whitelist(monkeypatch, fetch):
    """上游能看到付费模型也不许进：交集只收窄。"""
    fetch.models = WHITELIST + ["deepseek-ai/DeepSeek-V3"]
    models = seed_channel.select_models(WHITELIST, available=fetch.models)
    assert models == WHITELIST
    assert "deepseek-ai/DeepSeek-V3" not in models


def test_no_intersection_exits_2(monkeypatch, repo, fetch, key_env, capsys):
    _whitelist(monkeypatch, WHITELIST)
    fetch.models = ["some/other-model"]

    code = seed_channel.main(["--free-only", "--dry-run"])

    assert code == 2
    assert repo.writes == []
    assert "无交集" in capsys.readouterr().err


def test_explicit_models_outside_whitelist_rejected(monkeypatch, repo, fetch, key_env, capsys):
    """任何情况下都不把付费模型塞进凭据。"""
    _whitelist(monkeypatch, WHITELIST)
    code = seed_channel.main(
        ["--models", "deepseek-ai/DeepSeek-V3", "--dry-run"]
    )

    assert code == 2
    assert fetch.calls == []  # 没开 --free-only，本来就不该出网
    assert repo.writes == []
    assert "免费白名单" in capsys.readouterr().err


def test_explicit_models_inside_whitelist_used(monkeypatch, repo, fetch, key_env, capsys):
    _whitelist(monkeypatch, WHITELIST)
    code = seed_channel.main(["--models", WHITELIST[1], "--dry-run"])

    assert code == 0
    assert "models=1" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# 去重与上限
# ---------------------------------------------------------------------------


def test_duplicates_collapse_and_order_is_kept(monkeypatch):
    models = seed_channel.select_models(["a", "b", "a", "c", "b"])
    assert models == ["a", "b", "c"]


def test_cap_at_models_max(monkeypatch):
    whitelist = [f"m{i}" for i in range(seed_channel.MAX_MODELS + 5)]
    models = seed_channel.select_models(whitelist)

    assert len(models) == seed_channel.MAX_MODELS
    assert models == whitelist[: seed_channel.MAX_MODELS]


def test_cap_matches_credentials_limit():
    """上限必须与凭据服务同口径，否则脚本产出的清单会被 create_entry 打回。"""
    assert seed_channel.MAX_MODELS == credentials._MODELS_MAX == 20


def test_dedupe_happens_before_cap(monkeypatch):
    """重复项先收敛再截断，否则 25 个里 10 个重复会白占配额。"""
    whitelist = ["a"] * 10 + [f"m{i}" for i in range(seed_channel.MAX_MODELS)]
    models = seed_channel.select_models(whitelist)

    assert len(models) == seed_channel.MAX_MODELS
    assert models[0] == "a"
    assert len(set(models)) == len(models)


# ---------------------------------------------------------------------------
# dry-run 不写库
# ---------------------------------------------------------------------------


def test_dry_run_prints_plan_and_skips_write(monkeypatch, repo, fetch, key_env, capsys):
    _whitelist(monkeypatch, WHITELIST)

    code = seed_channel.main(["--free-only", "--dry-run"])

    assert code == 0
    assert repo.writes == []
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "name=siliconflow" in out
    assert "base_url=https://api.siliconflow.cn/v1" in out
    assert f"models={len(WHITELIST)}" in out
    assert SECRET not in out


# ---------------------------------------------------------------------------
# 真实写库路径（假仓储）
# ---------------------------------------------------------------------------


def test_creates_shared_channel(monkeypatch, repo, fetch, key_env, capsys):
    _whitelist(monkeypatch, WHITELIST)

    code = seed_channel.main(["--free-only"])

    assert code == 0
    assert repo.writes == ["create"]
    row = next(iter(repo.rows.values()))
    assert row["scope"] == "shared"
    assert row["owner_oid"] == credentials.SHARED_OWNER
    assert row["models"] == WHITELIST
    assert row["base_url"] == "https://api.siliconflow.cn/v1"

    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err
    assert crypto.key_hint(SECRET) in captured.out


def test_update_rotates_existing_shared_channel(monkeypatch, repo, fetch, key_env, capsys):
    _whitelist(monkeypatch, WHITELIST)
    repo.rows = {_shared_row()["_id"]: _shared_row()}

    code = seed_channel.main(["--free-only"])

    assert code == 0
    assert repo.writes == ["update"]
    assert len(repo.rows) == 1
    row = next(iter(repo.rows.values()))
    assert row["models"] == WHITELIST
    assert "updated" in capsys.readouterr().out


def test_rerun_is_idempotent(monkeypatch, repo, fetch, key_env):
    _whitelist(monkeypatch, WHITELIST)

    assert seed_channel.main(["--free-only"]) == 0
    assert seed_channel.main(["--free-only"]) == 0

    assert repo.writes == ["create", "update"]
    assert len(repo.rows) == 1


def test_missing_key_exits_2(monkeypatch, repo, fetch, capsys):
    monkeypatch.delenv(seed_channel.DEFAULT_KEY_ENV, raising=False)
    _whitelist(monkeypatch, WHITELIST)

    code = seed_channel.main(["--free-only"])

    assert code == 2
    assert repo.writes == []
    assert seed_channel.DEFAULT_KEY_ENV in capsys.readouterr().err


def test_key_file_wins_over_env(monkeypatch, tmp_path, repo, fetch, key_env):
    _whitelist(monkeypatch, WHITELIST)
    dotenv = tmp_path / "siliconflow.env"
    dotenv.write_text(f"# comment\nexport {seed_channel.DEFAULT_KEY_ENV}='file-secret-0123'\n")
    monkeypatch.setenv(seed_channel.DEFAULT_KEY_ENV, "env-secret-0123456789")

    code = seed_channel.main(["--free-only", "--key-file", str(dotenv)])

    assert code == 0
    assert fetch.calls == [("https://api.siliconflow.cn/v1", "file-secret-0123")]


def test_bad_base_url_exits_2(monkeypatch, repo, fetch, key_env, capsys):
    _whitelist(monkeypatch, WHITELIST)

    code = seed_channel.main(["--base-url", "api.siliconflow.cn/v1", "--dry-run"])

    assert code == 2
    assert repo.writes == []
    assert "base_url" in capsys.readouterr().err


def test_free_models_readback_matches_settings(monkeypatch):
    """``config.siliconflow_free_models`` 是脚本与 settings 之间唯一的读口。"""
    monkeypatch.setattr(
        settings, "AI_SILICONFLOW_FREE_MODELS", [" a ", "", "a", 3, "b"], raising=False
    )
    assert ai_config.siliconflow_free_models() == ["a", "b"]
