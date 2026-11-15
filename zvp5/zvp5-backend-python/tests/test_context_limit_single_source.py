"""A-L1：上下文上限只有一个来源（``services.agent.context``）。

以前 ``services.ai.conversations.context_limit`` 抄了一份实现，抄丢了
``AI_MODEL_CONTEXT_LIMITS`` 的 ``"*"`` 通配规则。后果很具体：**视图上的分母**
（会话页的占用条）与**压缩的水位**（``compact_history`` 真正用的数）能给出两个
不同答案——进度条永远差一截，或者反过来早早就变红。

这些用例钉的是「两个调用方对同一组配置给出同一个数」，而不是某个具体默认值：

* ``"*"`` 通配：两条路径都认；
* 裸模型名 / 合格 ID（``provider/model``）：都按裸名命中；
* 运行期覆盖 ``ai.context_limit_default``：都认；
* 模型级上下文（凭据目录）：都优先；
* 非字符串模型：都按「没有模型信息」处理（老的坏行不该让会话页 500）。
"""

from __future__ import annotations

import pytest

import settings
from services import settings_runtime
from services.agent import context as agent_context
from services.ai import conversations
from services.ai import credentials


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """把三处输入都钉住：模型级目录、配置表、运行期覆盖。"""
    credentials._cache.clear()
    monkeypatch.setattr(credentials, "model_context", lambda _model: None)
    monkeypatch.setattr(settings_runtime, "get_override", lambda _key: None)
    monkeypatch.setattr(settings, "AI_MODEL_CONTEXT_LIMITS", {}, raising=False)
    monkeypatch.setattr(settings, "AI_DEFAULT_CONTEXT_LIMIT", 32000, raising=False)
    yield
    credentials._cache.clear()


def _both(model):
    """两条路径对同一个模型的结论：必须相等。"""
    view = conversations.context_limit(model)
    ceiling = agent_context.context_limit(model)
    assert view == ceiling, (model, view, ceiling)
    return view


def test_wildcard_applies_on_both_paths(monkeypatch) -> None:
    """``"*"`` 是配置表的后备键：抄漏它就会在「没登记的模型」上分叉。"""
    monkeypatch.setattr(
        settings, "AI_MODEL_CONTEXT_LIMITS", {"*": 97000}, raising=False
    )

    assert _both("unlisted-model") == 97000
    # 没配通配时两边都回默认值。
    monkeypatch.setattr(settings, "AI_MODEL_CONTEXT_LIMITS", {}, raising=False)
    assert _both("unlisted-model") == 32000


def test_exact_bare_name_beats_the_wildcard(monkeypatch) -> None:
    monkeypatch.setattr(
        settings,
        "AI_MODEL_CONTEXT_LIMITS",
        {"gpt-x": 128000, "*": 8000},
        raising=False,
    )

    assert _both("gpt-x") == 128000
    assert _both("lab/unlisted") == 8000


def test_qualified_id_falls_back_to_the_bare_name(monkeypatch) -> None:
    """会话里存的是合格 ID，配置表配的是裸名：两边都要按裸名再查一次。"""
    monkeypatch.setattr(
        settings, "AI_MODEL_CONTEXT_LIMITS", {"gpt-x": 64000}, raising=False
    )

    assert _both("lab/gpt-x") == 64000


def test_runtime_override_feeds_both_paths(monkeypatch) -> None:
    """``ai.context_limit_default`` 在线改完即时生效——两份实现都得认它。"""
    monkeypatch.setattr(
        settings_runtime,
        "get_override",
        lambda key: 125000 if key == "ai.context_limit_default" else None,
    )

    assert _both("unlisted-model") == 125000


def test_model_level_context_wins_on_both_paths(monkeypatch) -> None:
    """凭据目录里的 ``context`` 是上游事实，优先级高于配置表。"""
    monkeypatch.setattr(credentials, "model_context", lambda model: 200000 if model else None)
    monkeypatch.setattr(
        settings, "AI_MODEL_CONTEXT_LIMITS", {"gpt-x": 32000}, raising=False
    )

    assert _both("gpt-x") == 200000


@pytest.mark.parametrize("bad", [None, "", 123, [], {}])
def test_non_string_models_are_handled_identically(bad) -> None:
    """非字符串（老坏行 / 测试替身）：按「没有模型信息」处理，不抛异常。"""
    assert conversations.context_limit(bad) == agent_context.context_limit(None)


def test_illegal_values_fall_through_on_both_paths(monkeypatch) -> None:
    """``0`` / 负数 / 布尔 / 字符串不是合法上限：两边都要继续往下找。"""
    monkeypatch.setattr(
        settings,
        "AI_MODEL_CONTEXT_LIMITS",
        {"gpt-x": 0, "*": True, "other": "128000"},
        raising=False,
    )

    assert _both("gpt-x") == 32000
    assert _both("other") == 32000


def test_public_constant_is_the_same_number() -> None:
    """``CONTEXT_LIMIT_DEFAULT`` 仍在 ``__all__`` 里（视图与测试引用它）。"""
    assert conversations.CONTEXT_LIMIT_DEFAULT == agent_context.DEFAULT_CONTEXT_LIMIT
    assert "CONTEXT_LIMIT_DEFAULT" in conversations.__all__
