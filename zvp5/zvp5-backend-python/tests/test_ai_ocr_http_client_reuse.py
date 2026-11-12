"""百度/Groq/智谱的 ``httpx.AsyncClient`` 必须**复用**。

每次识别都新建 client 会重付一次 TCP + TLS（跨境时就是每页几百毫秒）。这里把
两件事钉死：

* 多次识别（含百度的换 token + OCR 两次请求）共用同一个 client 实例；
* :func:`services.ai.ocr_engines.aclose` 之后下一次调用会重新建一个，不会用到
  已关闭的 client。

传输层仍然只有 ``MockTransport``——不出网、不用真凭证。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

import settings  # noqa: F401  (services.ai.config 读它，先导入保证顺序一致)
from services.ai import ocr_engines

PNG = b"\x89PNG\r\n\x1a\n ocr-client-reuse-bytes"

BAIDU_SECRET = {"api_key": "baidu-key", "secret_key": "baidu-secret"}
GROQ_SECRET = {"api_key": "gsk-test"}


class _Factory:
    """记录每次真实建 client 的次数与共享的请求记录。"""

    def __init__(self, handler) -> None:
        self.handler = handler
        self.clients: list[httpx.AsyncClient] = []

    def __call__(self, timeout: float) -> httpx.AsyncClient:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(self.handler), timeout=timeout
        )
        self.clients.append(client)
        return client


@pytest.fixture(autouse=True)
def _fresh_http_client():
    """复用的 client 是进程级状态，用例之间必须丢干净。"""
    ocr_engines._reset_http_client()
    yield
    ocr_engines._reset_http_client()


def _install(monkeypatch, handler) -> _Factory:
    factory = _Factory(handler)
    monkeypatch.setattr(ocr_engines, "_async_http_client", factory)
    return factory


def _run(coro):
    return asyncio.run(coro)


def test_groq_reuses_one_client_across_calls(monkeypatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = {"lines": [{"text": "第一行", "box": None}]}
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(body, ensure_ascii=False)
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    factory = _install(monkeypatch, handler)
    engine = ocr_engines.GroqOcrEngine()

    async def run() -> None:
        for _ in range(3):
            await engine.recognize(PNG, "image/png", GROQ_SECRET, {})

    _run(run())

    assert len(factory.clients) == 1
    assert len(requests) == 3


def test_baidu_token_and_ocr_share_one_client(monkeypatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/oauth/2.0/token"):
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(
            200, json={"words_result": [{"words": "第一行"}], "words_result_num": 1}
        )

    ocr_engines._clear_baidu_tokens()
    factory = _install(monkeypatch, handler)

    _run(
        ocr_engines.BaiduOcrEngine().recognize(
            PNG, "image/png", BAIDU_SECRET, {}
        )
    )

    assert len(factory.clients) == 1
    assert len(requests) == 2  # token + OCR


def test_aclose_rebuilds_the_client(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps({"lines": [{"text": "x"}], "box": None})
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    factory = _install(monkeypatch, handler)
    engine = ocr_engines.GroqOcrEngine()

    async def run() -> None:
        await engine.recognize(PNG, "image/png", GROQ_SECRET, {})
        first = ocr_engines._shared_http_client()
        await ocr_engines.aclose()
        assert first.is_closed
        await engine.recognize(PNG, "image/png", GROQ_SECRET, {})

    _run(run())

    assert len(factory.clients) == 2
    # 重复 aclose 是幂等的（启动/退出各调一次也不炸）
    _run(ocr_engines.aclose())
