"""平台帮助知识库（静态文档）的检索契约。

帮助内容本身就是产品口径：长度（一节塞进一次工具结果）、关键词命中（模型问法千奇
百怪，但「临时密码」「时长」「封禁」这类核心词必须一击即中）、未命中的兜底（返回速查
而不是空串）都是可回归的。
"""

from __future__ import annotations

import pytest

from services.agent import help_docs

REQUIRED_IDS = [
    "overview",
    "login",
    "temp-password",
    "signup",
    "hours",
    "batch",
    "upload",
    "ai-agent",
    "ban",
    "faq",
]


def test_ten_sections_in_frozen_order() -> None:
    assert [section["id"] for section in help_docs.HELP_SECTIONS] == REQUIRED_IDS


@pytest.mark.parametrize("section", help_docs.HELP_SECTIONS, ids=lambda s: s["id"])
def test_section_shape(section) -> None:
    """一节 = 一个工具结果的一部分：非空、不超预算、有标题和关键词。"""
    assert section["title"]
    assert section["keywords"]
    body = section["body"]
    assert body.strip()
    assert len(body) <= help_docs.MAX_BODY_CHARS
    assert body.lstrip().startswith("## ")


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("临时密码", "temp-password"),
        ("我的时长怎么还没加上", "hours"),
        ("被封禁了怎么办", "ban"),
        ("怎么报名活动", "signup"),
        ("批量登记怎么用", "batch"),
        ("平板传不了附件", "upload"),
        ("ZVAgent 怎么用", "ai-agent"),
        ("主设备换绑", "login"),
        ("这个平台是什么", "overview"),
    ],
)
def test_keyword_hits_return_the_matching_section(query, expected) -> None:
    result = help_docs.find_help(query)

    assert result["matched"] is True
    assert expected in result["sections"]
    assert help_docs.HELP_SECTIONS_BY_ID[expected]["body"] in result["answer"]


def test_multiple_hits_are_concatenated_in_document_order() -> None:
    """「批量登记」和「时长」同时出现：两节都要给，且顺序稳定（文档顺序）。"""
    result = help_docs.find_help("批量登记后时长怎么算")

    assert result["matched"] is True
    assert result["sections"] == sorted(
        result["sections"], key=REQUIRED_IDS.index
    )
    assert len(result["sections"]) >= 2
    assert "---" in result["answer"]


@pytest.mark.parametrize("query", ["", "   ", "量子纠缠", "zzzz", None])
def test_unknown_query_falls_back_to_the_quick_reference(query) -> None:
    result = help_docs.find_help(query)

    assert result["matched"] is False
    assert result["sections"] == [help_docs.QUICK_REFERENCE_ID]
    assert help_docs.HELP_SECTIONS_BY_ID["faq"]["body"] in result["answer"]
    # 兜底要给模型一个「换个说法再试」的抓手：全部标题都在索引里。
    for section in help_docs.HELP_SECTIONS:
        if section["id"] != help_docs.QUICK_REFERENCE_ID:
            assert section["title"] in result["answer"]
