"""百度 OCR 适配器的出站契约：真 httpx 组包，MockTransport 收口。

这里**只替换传输层**（``ocr_engines._async_http_client`` → 带
``httpx.MockTransport`` 的 ``AsyncClient``）：URL、query、header、表单编码全部
由 httpx 真正组装后交给断言，所以「实现里写成 JSON body」这种错误会在这里红，
而不是靠 mock 掉适配器方法假装通过。

不出网、不写真实凭证：所有 key/token 都是本文件里的常量。
"""

from __future__ import annotations

import base64
import json
import re
from urllib.parse import parse_qs

import httpx
import pytest

import settings  # noqa: F401  (services.ai.config 读它，先导入保证顺序一致)
from services.ai import ocr_engines

API_KEY = "baidu-api-key-0123456789abcdef"
SECRET_KEY = "baidu-secret-key-9876543210fedcba"
TOKEN = "24.fake-access-token-never-print-me.abc"
ENDPOINT = ocr_engines.BAIDU_DEFAULT_ENDPOINT
SECRET = {"api_key": API_KEY, "secret_key": SECRET_KEY}
PNG = b"\x89PNG\r\n\x1a\n baidu-adapter-offshelf-bytes"

DEFAULT_WORDS = {
    "words_result": [
        {
            "words": "第一行",
            "location": {"left": 10, "top": 20, "width": 30, "height": 12},
        },
        {
            "words": "second line",
            "location": {"left": 10, "top": 40, "width": 80, "height": 12},
        },
    ],
    "words_result_num": 2,
    "direction": 0,
}


def _run(coro):
    import asyncio

    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clean_token_cache():
    """token 缓存是进程级的共享状态，用例之间必须清干净。"""
    ocr_engines._clear_baidu_tokens()
    yield
    ocr_engines._clear_baidu_tokens()


def _install(
    monkeypatch,
    *,
    expires_in=2_592_000,
    token_status=200,
    token_body=None,
    ocr_status=200,
    ocr_body=None,
):
    """装上 MockTransport，返回按发生顺序记录的 ``httpx.Request`` 列表。"""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/oauth/2.0/token"):
            body = token_body
            if body is None:
                body = {"access_token": TOKEN, "expires_in": expires_in}
            return httpx.Response(token_status, json=body)
        body = DEFAULT_WORDS if ocr_body is None else ocr_body
        return httpx.Response(ocr_status, json=body)

    def factory(timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(handler), timeout=timeout
        )

    monkeypatch.setattr(ocr_engines, "_async_http_client", factory)
    return calls


def _recognize(secret=None, config=None):
    return _run(
        ocr_engines.BaiduOcrEngine().recognize(
            PNG, "image/png", SECRET if secret is None else secret, config or {}
        )
    )


def _token_requests(calls):
    return [r for r in calls if r.url.path.endswith("/oauth/2.0/token")]


def _ocr_requests(calls):
    return [r for r in calls if "/rest/2.0/ocr/v1/" in r.url.path]


# ---------------------------------------------------------------------------
# token 缓存
# ---------------------------------------------------------------------------


def test_first_call_fetches_token_and_second_reuses_it(monkeypatch) -> None:
    calls = _install(monkeypatch)

    first = _recognize()
    second = _recognize()

    # 首次：换 token + 识别；第二次：只有识别（缓存命中）。
    assert len(_token_requests(calls)) == 1
    assert len(_ocr_requests(calls)) == 2
    assert len(calls) == 3
    assert first["engine"] == ocr_engines.KIND_BAIDU
    assert second["engine"] == ocr_engines.KIND_BAIDU
    # 两次识别带的是同一枚 token（缓存真的返回了它，而不是又换了一枚）。
    assert [r.url.params["access_token"] for r in _ocr_requests(calls)] == [
        TOKEN,
        TOKEN,
    ]


def test_rotating_the_secret_key_invalidates_the_cached_token(monkeypatch) -> None:
    """轮换 secret_key 后旧 token 必须立刻作废。

    百度 access_token 有效期以月计：缓存键不带 secret 的话，「换了密钥」之后
    旧 secret 换出的 token 还能继续用，等于留了一条没撤销的通道。
    """
    calls = _install(monkeypatch)

    _recognize()
    assert len(_token_requests(calls)) == 1

    rotated = {"api_key": API_KEY, "secret_key": "rotated-secret-key-0000000000"}
    _recognize(secret=rotated)

    # 换了密钥 = 换了一把通道：必须重新换 token，而不是复用旧 secret 换来的那枚。
    assert len(_token_requests(calls)) == 2
    # 两把密钥各自持有自己的缓存条目，互不串用。
    assert ocr_engines._baidu_cached_token(ENDPOINT, API_KEY, SECRET_KEY) == TOKEN
    assert (
        ocr_engines._baidu_cached_token(ENDPOINT, API_KEY, rotated["secret_key"])
        == TOKEN
    )


def test_cache_key_never_stores_the_raw_secret_key() -> None:
    """缓存键里只能有 secret 的摘要，不能有原始密钥。"""
    key = ocr_engines._baidu_token_key(ENDPOINT, API_KEY, SECRET_KEY)
    assert SECRET_KEY not in key
    assert key[2] == ocr_engines._baidu_secret_digest(SECRET_KEY)
    # 摘要要稳定：同一个密钥每次算出同一个摘要，否则缓存永远命中不了。
    assert ocr_engines._baidu_secret_digest(SECRET_KEY) == ocr_engines._baidu_secret_digest(
        SECRET_KEY
    )
    assert ocr_engines._baidu_secret_digest(SECRET_KEY) != ocr_engines._baidu_secret_digest(
        "another-secret"
    )


def test_token_expires_three_hundred_seconds_before_expires_in(monkeypatch) -> None:
    class _Clock:
        now = 1_000.0

        @staticmethod
        def monotonic() -> float:
            return _Clock.now

    monkeypatch.setattr(ocr_engines, "time", _Clock)
    calls = _install(monkeypatch, expires_in=1_000)  # 实际寿命 700s

    _recognize()
    assert len(_token_requests(calls)) == 1

    # 差 1 秒到期：仍应命中缓存（提前量是 300s，不是 1s）。
    _Clock.now += 699
    _recognize()
    assert len(_token_requests(calls)) == 1

    # 跨过 1000-300 的边界：必须重新换 token，否则上游会回 110。
    _Clock.now += 2
    _recognize()
    assert len(_token_requests(calls)) == 2


# ---------------------------------------------------------------------------
# 出站请求形状
# ---------------------------------------------------------------------------


def test_token_request_puts_credentials_in_query(monkeypatch) -> None:
    calls = _install(monkeypatch)
    _recognize()

    request = _token_requests(calls)[0]
    assert request.method == "POST"
    assert request.url.host == "aip.baidubce.com"
    assert request.url.path == "/oauth/2.0/token"
    assert request.url.params["grant_type"] == "client_credentials"
    assert request.url.params["client_id"] == API_KEY
    assert request.url.params["client_secret"] == SECRET_KEY
    assert request.content == b""


def test_ocr_request_is_form_encoded_with_a_base64_image(monkeypatch) -> None:
    calls = _install(monkeypatch)
    _recognize()

    request = _ocr_requests(calls)[0]
    assert request.method == "POST"
    assert request.url.host == "aip.baidubce.com"
    assert request.url.path == "/rest/2.0/ocr/v1/accurate_basic"
    assert request.url.params["access_token"] == TOKEN
    # 百度 v1 接口要表单编码；JSON body 会被上游当成参数错误（216202）。
    assert request.headers["content-type"] == "application/x-www-form-urlencoded"
    with pytest.raises(ValueError):
        json.loads(request.content)

    form = parse_qs(request.content.decode("ascii"))
    assert form["image"] == [base64.b64encode(PNG).decode("ascii")]
    assert "data:" not in form["image"][0]
    assert form["detect_direction"] == ["true"]
    assert form["paragraph"] == ["false"]


# ---------------------------------------------------------------------------
# 归一化
# ---------------------------------------------------------------------------


def test_confidence_is_always_none_and_location_is_kept_verbatim(monkeypatch) -> None:
    _install(monkeypatch)

    result = _recognize()

    assert result["engine"] == ocr_engines.KIND_BAIDU
    assert result["text"] == "第一行\nsecond line"
    assert [line["text"] for line in result["lines"]] == ["第一行", "second line"]
    # 百度该接口不返回逐行置信度：有意的 None，不能编一个分数出来。
    assert all(line["confidence"] is None for line in result["lines"])
    # location 原样保留，不转成统一 box 形状。
    assert result["lines"][0]["box"] == {
        "left": 10,
        "top": 20,
        "width": 30,
        "height": 12,
    }
    assert result["lines"][1]["box"] == DEFAULT_WORDS["words_result"][1]["location"]
    assert result["raw"]["words_result_num"] == 2


def test_empty_words_result_is_a_success(monkeypatch) -> None:
    _install(monkeypatch, ocr_body={"words_result": [], "words_result_num": 0})

    result = _recognize()

    # 「图上没字」是成功，不是引擎坏了。
    assert result["text"] == ""
    assert result["lines"] == []
    assert result["engine"] == ocr_engines.KIND_BAIDU


# ---------------------------------------------------------------------------
# 失败路径
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "secret, missing",
    [
        ({"secret_key": SECRET_KEY}, "api_key"),
        ({"api_key": API_KEY}, "secret_key"),
    ],
)
def test_missing_credentials_raise_without_echoing_values(
    monkeypatch, secret, missing
) -> None:
    calls = _install(monkeypatch)

    with pytest.raises(ocr_engines.OcrEngineError) as excinfo:
        _recognize(secret=secret)

    message = str(excinfo.value)
    assert missing in message
    # 消息里只许出现缺失字段名（命中''引号里的标识符集合），不许带任何凭证值。
    assert set(re.findall(r"'([a-z_]+)'", message)) == {missing}
    assert API_KEY not in message
    assert SECRET_KEY not in message
    assert calls == []


@pytest.mark.parametrize("code", ["110", "111"])
def test_expired_token_error_drops_the_cache(monkeypatch, code: str) -> None:
    calls = _install(
        monkeypatch,
        ocr_body={"error_code": code, "error_msg": "access token invalid or expired"},
    )

    with pytest.raises(ocr_engines.OcrEngineError) as excinfo:
        _recognize()

    message = str(excinfo.value)
    assert f"error_code={code}" in message
    for secret in (TOKEN, API_KEY, SECRET_KEY):
        assert secret not in message
    # 110/111 = token 失效：缓存必须清掉，下一次自动换新的（否则会一直 110）。
    assert ocr_engines._baidu_cached_token(ENDPOINT, API_KEY, SECRET_KEY) is None
    with pytest.raises(ocr_engines.OcrEngineError):
        _recognize()
    assert len(_token_requests(calls)) == 2


def test_other_error_code_raises_but_keeps_the_token(monkeypatch) -> None:
    calls = _install(
        monkeypatch,
        ocr_body={"error_code": 17, "error_msg": "Open api daily request limit reached"},
    )

    with pytest.raises(ocr_engines.OcrEngineError) as excinfo:
        _recognize()

    message = str(excinfo.value)
    assert "error_code=17" in message
    assert "daily request limit" in message
    for secret in (TOKEN, API_KEY, SECRET_KEY):
        assert secret not in message
    # 不是 token 的问题：清缓存只会多一次无谓的 OAuth 往返。
    assert ocr_engines._baidu_cached_token(ENDPOINT, API_KEY, SECRET_KEY) == TOKEN
    assert len(_token_requests(calls)) == 1


def test_ocr_non_2xx_is_an_engine_error_without_secrets(monkeypatch) -> None:
    _install(monkeypatch, ocr_status=500, ocr_body={"message": "upstream exploded"})

    with pytest.raises(ocr_engines.OcrEngineError) as excinfo:
        _recognize()

    message = str(excinfo.value)
    assert "500" in message
    for secret in (TOKEN, API_KEY, SECRET_KEY):
        assert secret not in message


def test_token_non_2xx_is_an_engine_error_without_secrets(monkeypatch) -> None:
    _install(
        monkeypatch,
        token_status=401,
        token_body={"error": "invalid_client", "error_description": "bad credentials"},
    )

    with pytest.raises(ocr_engines.OcrEngineError) as excinfo:
        _recognize()

    message = str(excinfo.value)
    assert "401" in message
    for secret in (TOKEN, API_KEY, SECRET_KEY):
        assert secret not in message
