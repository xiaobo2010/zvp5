"""智谱 OCR 适配器的出站契约：真 httpx 组包，MockTransport 收口。

与 Groq 的每一处差异都来自官方文档或实测，逐条钉在这里防止被「对齐」掉：

* **只有** ``response_format={"type":"json_object"}``：官方无 ``json_schema``/
  ``strict``，且明说「不能保证返回数据始终合法」→ 软约束，解析后必须校验形状。
* ``box`` 恒 ``None``：官方无 grounding / 行级坐标契约。模型自己塞的坐标一律丢弃。
* ``temperature`` 官方区间 (0,1)：传 0 不合法；默认 0.6 对 OCR 偏发散，用 0.1。
* 429 业务码 1302（账户并发）/ 1305（平台过载），错误体无 ``Retry-After``。

不出网、不写真实凭证：api_key 是本文件里的常量。
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

import settings  # noqa: F401  (services.ai.config 读它，先导入保证顺序一致)
from services.ai import ocr_engines

API_KEY = "zhipu-api-key-0123456789abcdef-never-print"
MODEL = "glm-4.6v-flash"
SECRET = {"api_key": API_KEY}
PNG = b"\x89PNG\r\n\x1a\n zhipu-adapter-offshelf-bytes"

DEFAULT_LINES = [{"text": "第一行"}, {"text": "second line"}]


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def _completion(lines=None, *, model=MODEL) -> dict:
    payload = {"lines": DEFAULT_LINES if lines is None else lines}
    return {
        "id": "chatcmpl-test",
        "request_id": "req-test",
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


def _body(request: httpx.Request) -> dict:
    return json.loads(request.content)


def _recognize(config=None):
    return _run(
        ocr_engines.ZhipuOcrEngine().recognize(PNG, "image/png", SECRET, config or {})
    )


# ---------------------------------------------------------------------------
# 端点 / 鉴权 / 模型
# ---------------------------------------------------------------------------


def test_posts_to_chat_completions_with_bearer_auth(monkeypatch) -> None:
    calls = _install(monkeypatch)
    _recognize()

    request = calls[0]
    assert request.method == "POST"
    assert str(request.url) == f"{ocr_engines.ZHIPU_DEFAULT_BASE_URL}/chat/completions"
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    assert request.headers["content-type"].startswith("application/json")
    # 非流式：流式下 usage 只在最后一个 chunk，且错误会藏在 chunk 里。
    assert _body(request)["stream"] is False


def test_model_comes_from_config_not_hardcoded(monkeypatch) -> None:
    # 智谱的免费视觉型号换代很快（4V → 4.1V → 4.6V），写死等于埋定时 400。
    assert ocr_engines.ZHIPU_DEFAULT_MODEL == "glm-4.6v-flash"
    calls = _install(monkeypatch)

    _recognize(config={})
    _recognize(config={"model": "glm-9v-flash"})

    assert _body(calls[0])["model"] == ocr_engines.ZHIPU_DEFAULT_MODEL
    assert _body(calls[1])["model"] == "glm-9v-flash"


def test_glm_4_7_flash_is_not_the_default_because_it_is_text_only() -> None:
    """``glm-4.7-flash`` 存在但是纯文本模型，传图进去不会被识别。

    这条不是凑数：默认值一旦被改成「最顺眼的 flash」，整条通道会静默输出
    与图片无关的文字，比报错难查得多。
    """
    assert ocr_engines.ZHIPU_DEFAULT_MODEL != "glm-4.7-flash"
    assert "v" in ocr_engines.ZHIPU_DEFAULT_MODEL.split("-")[1]


# ---------------------------------------------------------------------------
# 结构化输出：只有 json_object，没有 json_schema
# ---------------------------------------------------------------------------


def test_response_format_is_json_object_only(monkeypatch) -> None:
    calls = _install(monkeypatch)
    _recognize()

    response_format = _body(calls[0])["response_format"]
    assert response_format == {"type": "json_object"}
    # 官方没有 json_schema/strict；硬塞会 400，或者被静默忽略后失去约束。
    assert "json_schema" not in response_format
    assert "strict" not in response_format


def test_json_mode_can_be_disabled(monkeypatch) -> None:
    """万一某个型号不收 response_format，配置里关掉就能退回纯 prompt 约束。"""
    calls = _install(monkeypatch)
    _recognize(config={"json_mode": False})

    assert "response_format" not in _body(calls[0])


# ---------------------------------------------------------------------------
# 图片 / 参数
# ---------------------------------------------------------------------------


def test_image_is_sent_as_a_base64_data_url(monkeypatch) -> None:
    calls = _install(monkeypatch)
    _recognize()

    content = _body(calls[0])["messages"][0]["content"]
    assert content[0]["type"] == "text"
    assert content[0]["text"] == ocr_engines.ZHIPU_PROMPT
    image = content[1]
    assert image["type"] == "image_url"
    expected = "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert image["image_url"]["url"] == expected


def test_prompt_asks_for_no_coordinates() -> None:
    """提示词里明确「不要输出坐标」：要了也只会得到猜的数字。"""
    assert "不要输出任何坐标字段" in ocr_engines.ZHIPU_PROMPT
    assert "box" not in ocr_engines.ZHIPU_PROMPT


def test_temperature_defaults_to_point_one(monkeypatch) -> None:
    calls = _install(monkeypatch)
    _recognize()

    assert _body(calls[0])["temperature"] == pytest.approx(
        ocr_engines.ZHIPU_DEFAULT_TEMPERATURE
    )
    assert ocr_engines.ZHIPU_DEFAULT_TEMPERATURE == pytest.approx(0.1)


@pytest.mark.parametrize("bad", [0, 1, 1.5, -0.2, "abc"])
def test_invalid_temperature_is_rejected_before_any_request(monkeypatch, bad) -> None:
    calls = _install(monkeypatch)

    with pytest.raises(ocr_engines.OcrEngineError):
        _recognize(config={"temperature": bad})

    assert calls == []


def test_max_tokens_defaults_and_can_be_overridden(monkeypatch) -> None:
    calls = _install(monkeypatch)

    _recognize()
    _recognize(config={"max_tokens": 1024})

    assert ocr_engines.ZHIPU_DEFAULT_MAX_TOKENS == 4096
    assert _body(calls[0])["max_tokens"] == 4096
    assert _body(calls[1])["max_tokens"] == 1024


@pytest.mark.parametrize("bad", [0, -1, "abc"])
def test_invalid_max_tokens_is_rejected_before_any_request(monkeypatch, bad) -> None:
    calls = _install(monkeypatch)

    with pytest.raises(ocr_engines.OcrEngineError):
        _recognize(config={"max_tokens": bad})

    assert calls == []


def test_thinking_is_disabled_by_default_and_omittable(monkeypatch) -> None:
    calls = _install(monkeypatch)

    _recognize()
    _recognize(config={"thinking": "enabled"})
    _recognize(config={"thinking": ""})

    assert _body(calls[0])["thinking"] == {"type": "disabled"}
    assert _body(calls[1])["thinking"] == {"type": "enabled"}
    # 强制思考的型号（如 GLM-5.3）收到这个字段会报错，空串 = 整个字段省略。
    assert "thinking" not in _body(calls[2])


# ---------------------------------------------------------------------------
# 归一化：box 恒 None
# ---------------------------------------------------------------------------


def test_box_is_discarded_even_if_the_model_returns_one(monkeypatch) -> None:
    """智谱没有坐标契约：模型自报的 box 一律丢弃。

    编出来的坐标会让前端把高亮画到错位置——比「没有坐标」更误导。
    """
    _install(
        monkeypatch,
        content=json.dumps(
            {
                "lines": [
                    {"text": "第一行", "box": [10, 20, 40, 32]},
                    {"text": "第二行"},
                ]
            },
            ensure_ascii=False,
        ),
    )

    result = _recognize()

    assert [line["text"] for line in result["lines"]] == ["第一行", "第二行"]
    assert all(line["box"] is None for line in result["lines"])
    # confidence 同样恒 None：没有逐行置信度的契约。
    assert all(line["confidence"] is None for line in result["lines"])


def test_empty_lines_array_is_a_success(monkeypatch) -> None:
    _install(monkeypatch, content=json.dumps({"lines": []}))

    result = _recognize()

    assert result["lines"] == []
    assert result["engine"] == ocr_engines.KIND_ZHIPU


def test_unparsable_content_is_an_engine_error(monkeypatch) -> None:
    _install(monkeypatch, content="这张图我看不清")

    with pytest.raises(ocr_engines.OcrEngineError) as excinfo:
        _recognize()

    assert "unparsable" in str(excinfo.value)
    assert API_KEY not in str(excinfo.value)


# ---------------------------------------------------------------------------
# 截断 / 限流
# ---------------------------------------------------------------------------


def test_finish_reason_length_is_an_engine_error(monkeypatch) -> None:
    """软约束下的截断更隐蔽：JSON 可能仍然合法，只是行数少了一截。"""
    _install(monkeypatch, finish_reason="length")

    with pytest.raises(ocr_engines.OcrEngineError) as excinfo:
        _recognize()

    message = str(excinfo.value)
    assert "truncated" in message
    assert "finish_reason=length" in message
    assert API_KEY not in message


def test_429_carries_the_business_code(monkeypatch) -> None:
    """1302（账户并发）与 1305（平台过载）处置不同，必须能从日志里分辨。"""
    _install(
        monkeypatch,
        status=429,
        response={
            "error": {
                "code": "1302",
                "message": "您的账户已达到速率限制，请您控制请求频率",
            }
        },
    )

    with pytest.raises(ocr_engines.OcrEngineRateLimited) as excinfo:
        _recognize()

    message = str(excinfo.value)
    assert "code=1302" in message
    # 官方错误体没有 Retry-After → 老实返回 None，由调用方按固定退避处理。
    assert excinfo.value.retry_after is None
    assert API_KEY not in message


def test_429_uses_the_retry_after_header_when_present(monkeypatch) -> None:
    _install(
        monkeypatch,
        status=429,
        response={"error": {"code": "1305", "message": "平台过载"}},
        headers={"retry-after": "3"},
    )

    with pytest.raises(ocr_engines.OcrEngineRateLimited) as excinfo:
        _recognize()

    assert excinfo.value.retry_after == pytest.approx(3.0)
