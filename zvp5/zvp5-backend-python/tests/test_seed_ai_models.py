"""``misc/seed_ai_models.py``：模型清单 + AF 倍率的落库计划（全离线）。

这个脚本能造成的最大事故是「把线上手调的倍率悄悄改掉」——账面上不会报错，只是
每一次 AI 调用都按错的价扣钱。所以这里钉的是**边界**：

* 换算口径 ``rate = round(score / 56.16, 4)``，4 位小数；
* 基准模型 ``THUDM/GLM-Z1-9B-0414`` 恒 1.0000（表被改坏时启动即失败）；
* 任务型模型 rate=1.0 占位 + ``task-priced:`` notes；
* 已存在的行默认 **不**覆盖倍率（skip），``--force-rate`` 才覆盖；
* 清单是「凭据 ∪ 白名单 ∪ 内嵌表键」的并集；
* ``--dry-run`` 一行不写库。

仓储用假实现注入（``backends.get_ai_models_repo`` / ``get_ai_credentials_repo``）：
用例不连库、不出网。
"""

from __future__ import annotations

import asyncio

import pytest

import settings
from db import backends
from misc import seed_ai_models as seed
from services.ai import model_rates

BASE = seed.BASE_MODEL
WHITELIST = ["Qwen/Qwen3-8B", "THUDM/GLM-4.1V-9B-Thinking"]


# ---------------------------------------------------------------------------
# 假仓储
# ---------------------------------------------------------------------------


class FakeModelsRepo:
    """``AIModelsRepo`` 的内存实现：行形状与 Oracle 版一致。"""

    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows: dict[str, dict] = {}
        for row in rows or []:
            self.seed_row(row["model"], **row)
        self.writes: list[tuple[str, dict, str | None]] = []

    def seed_row(self, model: str, **fields) -> None:
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
        row.update({k: v for k, v in fields.items() if k != "model"})
        self.rows[model] = row

    async def list_all(self) -> list[dict]:
        return [dict(self.rows[model]) for model in sorted(self.rows)]

    async def upsert(self, model: str, *, fields: dict, actor: str | None = None) -> dict:
        self.writes.append((model, dict(fields), actor))
        row = self.rows.get(model)
        if row is None:
            # 与 Oracle 版同口径：新行从默认值起步，只覆盖调用方明确给的字段。
            self.seed_row(model)
            row = self.rows[model]
        row.update(fields)
        row["updated_by"] = actor
        return dict(row)


class FakeCredentialsRepo:
    def __init__(self, rows: list[dict] | None = None) -> None:
        self.rows = list(rows or [])

    async def list_all(self) -> list[dict]:
        return [dict(row) for row in self.rows]


def _credential(name: str, models: list, scope: str = "shared") -> dict:
    return {"name": name, "scope": scope, "models": models}


@pytest.fixture
def models_repo(monkeypatch) -> FakeModelsRepo:
    repo = FakeModelsRepo()
    monkeypatch.setattr(backends, "get_ai_models_repo", lambda session=None: repo)
    return repo


@pytest.fixture
def credentials_repo(monkeypatch) -> FakeCredentialsRepo:
    repo = FakeCredentialsRepo()
    monkeypatch.setattr(backends, "get_ai_credentials_repo", lambda session=None: repo)
    return repo


@pytest.fixture(autouse=True)
def _no_whitelist(monkeypatch):
    """默认环境里没有免费白名单：要测并集的用例自己设。"""
    monkeypatch.setattr(settings, "AI_SILICONFLOW_FREE_MODELS", [], raising=False)



# ---------------------------------------------------------------------------
# 换算
# ---------------------------------------------------------------------------


def test_rate_from_score_matches_contract():
    assert seed.rate_from_score(56.16) == 1.0
    assert seed.rate_from_score(68.35) == 1.2171
    assert seed.rate_from_score(33.44) == 0.5954
    assert seed.rate_from_score(88.00) == 1.5670


def test_rate_is_rounded_to_four_decimals():
    rate = seed.rate_from_score(70.0)
    assert rate == round(70.0 / 56.16, 4)
    assert len(str(rate).split(".")[-1]) <= 4


def test_rate_rejects_bad_scores():
    for bad in (0, -1, True, "56.16", None):
        with pytest.raises(seed.SeedError):
            seed.rate_from_score(bad)


def test_whole_score_table_converts_within_rate_max():
    for model, (score, _source) in seed.SCORE_TABLE.items():
        rate = seed.rate_from_score(score)
        assert 0 < rate <= seed.RATE_MAX, model


# ---------------------------------------------------------------------------
# 基准模型
# ---------------------------------------------------------------------------


def test_base_model_rate_is_exactly_one():
    assert seed.assert_base_rate() is None
    target = seed.target_for(BASE)
    assert target is not None
    assert target.rate == 1.0
    assert target.score == seed.BASE_SCORE


def test_base_score_corruption_is_fatal(monkeypatch):
    monkeypatch.setitem(seed.SCORE_TABLE, BASE, (60.0, "model-card"))
    with pytest.raises(seed.SeedError):
        seed.assert_base_rate()


def test_base_model_missing_is_fatal(monkeypatch):
    monkeypatch.delitem(seed.SCORE_TABLE, BASE)
    with pytest.raises(seed.SeedError):
        seed.assert_base_rate()


def test_main_exits_2_when_base_row_broken(monkeypatch, models_repo, credentials_repo, capsys):
    monkeypatch.setitem(seed.SCORE_TABLE, BASE, (60.0, "model-card"))
    assert seed.main(["--dry-run"]) == 2
    assert "基准" in capsys.readouterr().err
    assert models_repo.writes == []


# ---------------------------------------------------------------------------
# 任务型模型
# ---------------------------------------------------------------------------


def test_task_models_are_placeholder_priced():
    for model, unit in seed.TASK_TABLE.items():
        target = seed.target_for(model)
        assert target is not None, model
        assert target.rate == 1.0
        assert target.score is None
        assert target.notes == f"task-priced: {unit}"
        assert target.billing == f"task-priced: {unit}"


def test_task_models_create_with_rate_one_enabled():
    plan = seed.build_plan(list(seed.TASK_TABLE), None)
    for row in plan:
        assert row.action == seed.CREATE
        assert row.fields["rate"] == 1.0
        assert row.fields["enabled"] == 1
        assert str(row.fields["notes"]).startswith("task-priced:")


# ---------------------------------------------------------------------------
# 幂等：默认不覆盖管理员的倍率
# ---------------------------------------------------------------------------


def test_existing_manual_rate_is_skipped(models_repo, credentials_repo):
    models_repo.seed_row("Qwen/Qwen3.5-4B", rate=2.5, notes="hand tuned")
    assert seed.main([]) == 0
    assert models_repo.rows["Qwen/Qwen3.5-4B"]["rate"] == 2.5
    assert models_repo.rows["Qwen/Qwen3.5-4B"]["notes"] == "hand tuned"


def test_existing_admin_rate_one_is_not_treated_as_missing(models_repo, credentials_repo):
    """管理员把倍率设成 1.0 是决定：notes 不是 seed 标记就不该被改写。"""
    models_repo.seed_row("Qwen/Qwen3.5-4B", rate=1.0, notes="cheap on purpose")
    assert seed.main([]) == 0
    assert models_repo.rows["Qwen/Qwen3.5-4B"]["rate"] == 1.0


def test_seed_marked_placeholder_row_gets_rate_filled(models_repo, credentials_repo):
    """上次跑到一半（rate 还是占位 1.0、notes 已是 seed 标记）→ 补齐。"""
    models_repo.seed_row(
        "Qwen/Qwen3.5-4B", rate=1.0, notes=seed.score_note("Qwen/Qwen3.5-4B")
    )
    assert seed.main([]) == 0
    assert models_repo.rows["Qwen/Qwen3.5-4B"]["rate"] == 1.2171


def test_already_seeded_row_is_skipped_on_rerun(models_repo, credentials_repo):
    assert seed.main([]) == 0
    first = len(models_repo.writes)
    assert first > 0
    assert seed.main([]) == 0
    assert len(models_repo.writes) == first  # 第二次一行都不写


def test_force_rate_overrides_existing(models_repo, credentials_repo):
    models_repo.seed_row("Qwen/Qwen3.5-4B", rate=2.5, notes="hand tuned")
    assert seed.main(["--force-rate"]) == 0
    assert models_repo.rows["Qwen/Qwen3.5-4B"]["rate"] == 1.2171


def test_force_rate_keeps_admin_notes(models_repo, credentials_repo):
    models_repo.seed_row("Qwen/Qwen3.5-4B", rate=2.5, notes="hand tuned")
    assert seed.main(["--force-rate"]) == 0
    assert models_repo.rows["Qwen/Qwen3.5-4B"]["notes"] == "hand tuned"


def test_update_never_touches_display_name(models_repo, credentials_repo):
    models_repo.seed_row(
        "Qwen/Qwen3.5-4B", rate=1.0, notes=seed.score_note("Qwen/Qwen3.5-4B"),
        display_name="Qwen 3.5 (fast)",
    )
    assert seed.main([]) == 0
    assert models_repo.rows["Qwen/Qwen3.5-4B"]["display_name"] == "Qwen 3.5 (fast)"
    assert "display_name" not in models_repo.writes[-1][1]


def test_provider_is_filled_only_when_empty(models_repo, credentials_repo):
    credentials_repo.rows = [_credential("siliconflow", ["Qwen/Qwen3.5-4B"])]
    models_repo.seed_row("Qwen/Qwen3.5-4B", rate=1.2171, notes=seed.score_note("Qwen/Qwen3.5-4B"))
    assert seed.main([]) == 0
    assert models_repo.rows["Qwen/Qwen3.5-4B"]["provider"] == "siliconflow"

    models_repo.seed_row("Qwen/Qwen3.5-4B", provider="manual-provider")
    models_repo.writes.clear()
    assert seed.main([]) == 0
    assert models_repo.writes == []


# ---------------------------------------------------------------------------
# 清单并集
# ---------------------------------------------------------------------------


def test_catalog_is_union_of_credentials_whitelist_and_table(monkeypatch):
    monkeypatch.setattr(
        settings, "AI_SILICONFLOW_FREE_MODELS", WHITELIST, raising=False
    )
    rows = [
        _credential("siliconflow", ["Qwen/Qwen3-8B", {"id": "gpt-5.4", "context": 400000}]),
        _credential("other", ["private/model"], scope="personal"),
    ]
    models, hints = seed.catalog_models(rows, seed.ai_config.siliconflow_free_models())

    assert set(models) >= {
        "Qwen/Qwen3-8B",          # 凭据 + 白名单交集
        "gpt-5.4",                # 凭据（对象形态）
        "private/model",          # personal scope 也要算进来
        "THUDM/GLM-4.1V-9B-Thinking",  # 只在白名单里
        "Qwen/Qwen3.5-4B",        # 只在分数表里
        "tencent/Hunyuan-MT-7B",  # 只在任务表里
    }
    assert models == sorted(set(models), key=lambda name: (name.lower(), name))
    assert hints["gpt-5.4"] == "siliconflow"
    # 未定价的模型即使能推出渠道，也不会建行（见 plan_row 的 no-price 分支）。
    assert seed.plan_row(None, None, provider_hint=hints.get("private/model")).action == seed.SKIP


def test_ambiguous_provider_is_left_empty():
    rows = [
        _credential("a", ["Qwen/Qwen3-8B"]),
        _credential("b", ["Qwen/Qwen3-8B"]),
    ]
    _models, hints = seed.catalog_models(rows, [])
    assert "Qwen/Qwen3-8B" not in hints


def test_unknown_catalog_model_is_skipped_without_row(models_repo, credentials_repo):
    credentials_repo.rows = [_credential("siliconflow", ["private/model"])]
    assert seed.main([]) == 0
    assert "private/model" not in models_repo.rows


def test_collect_returns_union_plan(models_repo, credentials_repo):
    credentials_repo.rows = [_credential("siliconflow", ["private/model"])]
    models, hints, existing = asyncio.run(seed.collect())
    assert "private/model" in models
    assert BASE in models
    assert hints["private/model"] == "siliconflow"
    assert existing == []


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------


def test_dry_run_writes_nothing(models_repo, credentials_repo, capsys):
    assert seed.main(["--dry-run"]) == 0
    assert models_repo.writes == []
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "create=" in out
    assert seed.SCORE_TABLE and "1.2171" in out


def test_dry_run_prints_one_line_per_model(models_repo, credentials_repo, capsys):
    assert seed.main(["--dry-run"]) == 0
    out = capsys.readouterr().out.splitlines()
    body = [line for line in out if " | " in line][1:]  # 去掉表头
    assert len(body) == len(seed.SCORE_TABLE) + len(seed.TASK_TABLE)


def test_run_reports_summary_and_actor(models_repo, credentials_repo, capsys):
    assert seed.main([]) == 0
    out = capsys.readouterr().out
    assert "create=" in out and "update=" in out and "skip=" in out
    actor = models_repo.writes[0][2]
    assert actor == str(seed.credentials.SHARED_OWNER)


def test_invalidate_is_called_after_write(monkeypatch, models_repo, credentials_repo):
    calls: list[bool] = []
    monkeypatch.setattr(model_rates, "invalidate", lambda: calls.append(True))
    assert seed.main([]) == 0
    assert calls == [True]


def test_dry_run_does_not_invalidate(monkeypatch, models_repo, credentials_repo):
    calls: list[bool] = []
    monkeypatch.setattr(model_rates, "invalidate", lambda: calls.append(True))
    assert seed.main(["--dry-run"]) == 0
    assert calls == []


# ---------------------------------------------------------------------------
# 参数与失败路径
# ---------------------------------------------------------------------------


def test_parse_args_defaults():
    args = seed.parse_args([])
    assert args.dry_run is False
    assert args.force_rate is False


def test_parse_args_flags():
    args = seed.parse_args(["--dry-run", "--force-rate"])
    assert args.dry_run is True
    assert args.force_rate is True


def test_unknown_flag_exits_2():
    with pytest.raises(SystemExit) as excinfo:
        seed.parse_args(["--nope"])
    assert excinfo.value.code == 2


def test_read_failure_exits_2(monkeypatch, models_repo, capsys):
    class Broken:
        async def list_all(self):
            raise RuntimeError("db down")

    monkeypatch.setattr(backends, "get_ai_credentials_repo", lambda session=None: Broken())
    assert seed.main(["--dry-run"]) == 2
    assert "AI_CREDENTIALS" in capsys.readouterr().err
    assert models_repo.writes == []
