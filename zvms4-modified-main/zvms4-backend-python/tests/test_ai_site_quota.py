"""站点级 OCR 额度的默认口径。

背景（2026-10-24 上线实测）：站点额度曾经回落到 ``DEFAULT_QUOTA["ocr"]``（100），
于是 tencent/rapidocr 的月额度一建就是 100 —— 几十个用户一天就能打满，之后全站
OCR 一起 503。站点额度是「平台愿意为全站垫多少上游调用」，与「单个用户免费几次」
不是一个口径，必须分开。
"""

from __future__ import annotations

import settings  # noqa: F401  (services.ai.config 读它，先导入保证顺序一致)
from services.ai import config as ai_config
from services.ai import ocr_engines, quota


def test_site_kind_uses_the_site_default_not_the_user_quota() -> None:
    user_quota = ai_config.DEFAULT_QUOTA["ocr"]

    assert quota.default_limit_for(None, "ocr_tencent") == 1000
    # 关键断言：站点额度不能等于用户额度（回落就是那个 bug）。
    assert quota.default_limit_for(None, "ocr_tencent") != user_quota


def test_every_engine_kind_gets_a_site_default() -> None:
    for kind in ocr_engines.ENGINE_KINDS:
        site_kind = quota.site_kind(kind)
        assert site_kind in quota.SITE_KINDS
        assert quota.default_limit_for(None, site_kind) > 0


def test_site_quota_can_be_overridden_per_kind(monkeypatch) -> None:
    monkeypatch.setattr(
        settings,
        "AI_SITE_QUOTA",
        {"ocr_groq": 1500, "ocr_rapidocr": 1_000_000},
        raising=False,
    )

    assert quota.default_limit_for(None, "ocr_groq") == 1500
    assert quota.default_limit_for(None, "ocr_rapidocr") == 1_000_000
    # 没被覆盖的站点 kind 仍走兜底值。
    assert quota.default_limit_for(None, "ocr_tencent") == 1000


def test_site_default_can_be_configured(monkeypatch) -> None:
    monkeypatch.setattr(
        settings, "AI_DEFAULT_SITE_OCR_MONTHLY_UNITS", 2000, raising=False
    )

    assert quota.default_limit_for(None, "ocr_zhipu") == 2000


def test_invalid_override_falls_back_to_the_default(monkeypatch) -> None:
    """写错一个条目不该把额度变成 0，把整条链路掐死。"""
    monkeypatch.setattr(
        settings,
        "AI_SITE_QUOTA",
        {"ocr_tencent": -5, "ocr_zhipu": "abc"},
        raising=False,
    )

    assert quota.default_limit_for(None, "ocr_tencent") == 1000
    assert quota.default_limit_for(None, "ocr_zhipu") == 1000


def test_user_level_kinds_still_use_the_user_default() -> None:
    """用户级 kind 不受站点口径影响。"""
    assert quota.default_limit_for(None, "ocr") == ai_config.DEFAULT_QUOTA["ocr"]
    assert quota.default_limit_for(None, "chat") == ai_config.DEFAULT_QUOTA["chat"]
