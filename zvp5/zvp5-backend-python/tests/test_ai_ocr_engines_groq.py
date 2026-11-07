"""Groq OCR 适配器的出站契约：真 httpx 组包，MockTransport 收口。

Groq 的雷区都在「多传一个字段就 400」上：``logprobs`` 直接拒、``temperature=0``
被静默改成 ``1e-8``（等于没锁定），``strict`` 的 json_schema 少一个 required 也
400。这些都不会在本地报错，只会在第一次真调用时炸，所以逐条钉在这里。

不出网、不写真实凭证：api_key 是本文件里的常量。
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

import settings  # noqa: F401  (services.ai.config 读它，先导入保证顺序一致)
from services.ai import ocr_engines

API_KEY = "gsk-groq-0123456789abcdef-never-print"
MODEL = "qwen/qwen3.8-27b"
SECRET = {"api_key": API_KEY}
PNG = b"\x89PNG\r\n\x1a\n groq-adapter-offshelf-bytes"

DEFAULT_LINES = [
    {"text": "第一行", "box": [10, 20, 40, 32]},
    {"text": "second line", "box": [10, 40, 90, 52]},
]


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def _completion(lines=None, *, model=MODEL) -> dict:
    payload = {"lines": DEFAULT_LINES if lines is None else lines}
    return {
        "id": "chatcmpl-test",
        "model": model,
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _install(
    monkeypatch,
    *,
    response=None,
    status=200,
    content=None,
    finish_reason="stop",
    headers=None,
):
    """装上 MockTransport，返回按发生顺序记录的 ``httpx.Request`` 列表。

    ``content`` 覆盖模型输出的正文（用于非法 JSON 用例）；``response`` 覆盖整个
    响应体（用于空 choices / 429 错误体）；``finish_reason`` 覆盖停止原因（用于
    截断用例）；``headers`` 用于 ``retry-after`` 这类响应头分支。
    """
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if response is not None:
            return httpx.Response(status, json=response, headers=headers)
        body = _completion()
        if content is not None:
            body["choices"][0]["message"]["content"] = content
        if finish_reason != "stop":
            body["choices"][0]["finish_reason"] = finish_reason
        return httpx.Response(status, json=body, headers=headers)

    def factory(timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=timeout
        )

    monkeypatch.setattr(ocr_engines, "_async_http_client", factory)
    return calls


def _recognize(config=None):
    return _run(
        ocr_engines.GroqOcrEngine().recognize(
            PNG, "image/png", SECRET, config or {}
        )
    )


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content)


# ---------------------------------------------------------------------------
# 端点 / 鉴权 / 模型
# ---------------------------------------------------------------------------


def test_posts_to_chat_completions_with_bearer_auth(monkeypatch) -> None:
    calls = _install(monkeypatch)
    _recognize()

    request = calls[0]
    assert request.method == "POST"
    assert str(request.url) == (
        f"{ocr_engines.GROQ_DEFAULT_BASE_URL}/chat/completions"
    )
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["content-type"].startswith("application/json")


def test_model_comes_from_config_not_hardcoded(monkeypatch) -> None:
    # 常量存在是「有默认值」，但线上必须能靠 config 换模型（Groq 的视觉模型
    # 下线没有过渡期）。
    assert ocr_engines.GROQ_DEFAULT_MODEL
    calls = _install(monkeypatch)

    _recognize(config={"model": "custom/vision-model-9"})
    _recognize(config={})

    assert _body(calls[0])["model"] == "custom/vision-model-9"
    assert _body(calls[1])["model"] == ocr_engines.GROQ_DEFAULT_MODEL


# ---------------------------------------------------------------------------
# 请求体：不能出现的字段 / response_format / 图片
# ---------------------------------------------------------------------------


def test_request_omits_temperature_and_logprobs(monkeypatch) -> None:
    calls = _install(monkeypatch)
    _recognize()

    body = _body(calls[0])
    # Groq 传 logprobs 直接 400；temperature=0 会被静默改成 1e-8 —— 传了只是
    # 制造「已锁定确定性」的假象。
    assert "temperature" not in body
    assert "logprobs" not in body


def test_response_format_is_a_strict_complete_json_schema(monkeypatch) -> None:
    calls = _install(monkeypatch)
    _recognize()

    response_format = _body(calls[0])["response_format"]
    assert response_format["type"] == "json_schema"
    json_schema = response_format["json_schema"]
    assert json_schema["strict"] is True
    assert json_schema["schema"] == ocr_engines.GROQ_RESPONSE_SCHEMA

    def _assert_strict_objects(node) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                # strict: true 的硬要求：全字段 required + 禁止额外字段。
                assert node.get("additionalProperties") is False
                assert set(node.get("required") or ()) == set(
                    node.get("properties") or {}
                )
            for value in node.values():
                _assert_strict_objects(value)
        elif isinstance(node, list):
            for value in node:
                _assert_strict_objects(value)

    _assert_strict_objects(json_schema["schema"])


def test_image_is_sent_as_a_base64_data_url(monkeypatch) -> None:
    calls = _install(monkeypatch)
    _recognize()

    content = _body(calls[0])["messages"][0]["content"]
    text_parts = [part for part in content if part["type"] == "text"]
    image_parts = [part for part in content if part["type"] == "image_url"]
    assert text_parts and text_parts[0]["text"] == ocr_engines.GROQ_PROMPT
    assert len(image_parts) == 1
    assert image_parts[0]["image_url"]["url"] == (
        "data:image/png;base64," + base64.b64encode(PNG).decode("ascii")
    )


# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------


def test_confidence_is_none_and_box_is_kept_verbatim(monkeypatch) -> None:
    _install(monkeypatch)

    result = _recognize()

    assert result["engine"] == ocr_engines.KIND_GROQ
    assert result["text"] == "第一行\nsecond line"
    assert [line["text"] for line in result["lines"]] == ["第一行", "second line"]
    # 不传 logprobs 就拿不到分数：一律 None，不编造。
    assert all(line["confidence"] is None for line in result["lines"])
    assert [line["box"] for line in result["lines"]] == [
        [10, 20, 40, 32],
        [10, 40, 90, 52],
    ]
    assert result["raw"]["finish_reason"] == "stop"
    assert result["raw"]["usage"]["total_tokens"] == 15


def test_empty_lines_array_is_a_success(monkeypatch) -> None:
    _install(monkeypatch, response=_completion(lines=[]))

    result = _recognize()

    assert result["text"] == ""
    assert result["lines"] == []
    assert result["engine"] == ocr_engines.KIND_GROQ


# ---------------------------------------------------------------------------
# 失败路径
# ---------------------------------------------------------------------------


def test_unparsable_content_is_an_engine_error(monkeypatch) -> None:
    _install(monkeypatch, content="这不是 JSON，只是一段解释")

    with pytest.raises(ocr_engines.OcrEngineError) as excinfo:
        _recognize()

    assert "unparsable" in str(excinfo.value)


def test_empty_choices_is_an_engine_error(monkeypatch) -> None:
    _install(monkeypatch, response={"choices": []})

    with pytest.raises(ocr_engines.OcrEngineError) as excinfo:
        _recognize()

    assert "no choices" in str(excinfo.value)


def test_401_is_an_engine_error_without_echoing_the_api_key(monkeypatch) -> None:
    _install(
        monkeypatch,
        status=401,
        response={"error": {"message": "Invalid API Key", "type": "auth"}},
    )

    with pytest.raises(ocr_engines.OcrEngineError) as excinfo:
        _recognize()

    message = str(excinfo.value)
    assert "401" in message
    assert API_KEY not in message


# ---------------------------------------------------------------------------
# 可选参数
# ---------------------------------------------------------------------------


def test_reasoning_effort_can_be_overridden_and_empty_omits_it(monkeypatch) -> None:
    calls = _install(monkeypatch)

    _recognize(config={})
    _recognize(config={"reasoning_effort": "high"})
    _recognize(config={"reasoning_effort": ""})

    assert _body(calls[0])["reasoning_effort"] == (
        ocr_engines.GROQ_DEFAULT_REASONING_EFFORT
    )
    assert _body(calls[1])["reasoning_effort"] == "high"
    # 空串 = 整个字段省略：非推理模型收到未知字段会 400。
    assert "reasoning_effort" not in _body(calls[2])


# ---------------------------------------------------------------------------
# 真实额度约束：max_tokens / 截断 / 限流
#
# 以下三条全部来自 2026-10-24 的真机实测（免费层 on_demand，模型
# qwen/qwen3.8-27b），单测里复现不了，只能把结论钉在契约上：
#   1. 不传 max_tokens → 上游按 1164 估，超 OTPM 1000 直接 429
#   2. 密集页 max_tokens=1000 → 38 行只回 25 行，finish_reason=length，HTTP 200
#   3. 连发两次 → 第二次 429 "Used 990, Requested 1000, try again in 59.4s"
# ---------------------------------------------------------------------------


def test_max_tokens_defaults_to_the_free_tier_output_cap(monkeypatch) -> None:
    calls = _install(monkeypatch)
    _recognize()

    assert ocr_engines.GROQ_DEFAULT_MAX_TOKENS == 1000
    assert _body(calls[0])["max_tokens"] == ocr_engines.GROQ_DEFAULT_MAX_TOKENS


def test_max_tokens_can_be_overridden_from_config(monkeypatch) -> None:
    calls = _install(monkeypatch)
    _recognize(config={"max_tokens": 512})

    assert _body(calls[0])["max_tokens"] == 512


@pytest.mark.parametrize("bad", [0, -1, "abc"])
def test_invalid_max_tokens_is_rejected_before_any_request(monkeypatch, bad) -> None:
    calls = _install(monkeypatch)

    with pytest.raises(ocr_engines.OcrEngineError):
        _recognize(config={"max_tokens": bad})

    # 非法配置不该先发一次注定 429/400 的请求再报错。
    assert calls == []


def test_finish_reason_length_is_an_engine_error(monkeypatch) -> None:
    """截断页是 HTTP 200 + 残缺内容，必须硬失败而不是交出半页。"""
    _install(monkeypatch, finish_reason="length")

    with pytest.raises(ocr_engines.OcrEngineError) as excinfo:
        _recognize()

    message = str(excinfo.value)
    assert "truncated" in message
    assert "finish_reason=length" in message
    assert API_KEY not in message


def test_429_is_a_retryable_error_with_the_upstream_wait(monkeypatch) -> None:
    _install(
        monkeypatch,
        status=429,
        response={
            "error": {
                "message": (
                    "Rate limit reached ... on output tokens per minute (OTPM): "
                    "Limit 1000, Used 990, Requested 1000. "
                    "Please try again in 59.4s."
                ),
                "type": "tokens",
                "code": "rate_limit_exceeded",
            }
        },
    )

    with pytest.raises(ocr_engines.OcrEngineRateLimited) as excinfo:
        _recognize()

    assert excinfo.value.retry_after == pytest.approx(59.4)
    assert "retry_after=59.4s" in str(excinfo.value)
    assert API_KEY not in str(excinfo.value)


def test_429_prefers_the_retry_after_header(monkeypatch) -> None:
    _install(
        monkeypatch,
        status=429,
        response={"error": {"message": "slow down"}},
        headers={"retry-after": "12"},
    )

    with pytest.raises(ocr_engines.OcrEngineRateLimited) as excinfo:
        _recognize()

    assert excinfo.value.retry_after == pytest.approx(12.0)


def test_429_without_a_hint_has_no_retry_after(monkeypatch) -> None:
    _install(monkeypatch, status=429, response={"error": {"message": "slow down"}})

    with pytest.raises(ocr_engines.OcrEngineRateLimited) as excinfo:
        _recognize()

    # 上游没给等待时间就老实承认没有，不编一个数字。
    assert excinfo.value.retry_after is None
    assert "retry_after=unknown" in str(excinfo.value)


def test_rate_limited_is_a_subclass_of_engine_error(monkeypatch) -> None:
    """既有调用方只 catch OcrEngineError，新增子类不能把它们打断。"""
    assert issubclass(ocr_engines.OcrEngineRateLimited, ocr_engines.OcrEngineError)
