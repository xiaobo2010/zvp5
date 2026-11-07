"""安全响应头：每个经过中间件栈的响应都带四条基线头 + 一条保守 CSP。

注册位置在 ``shield_middleware`` **内侧**（add_middleware 后注册者更靠外层），
所以正常的业务响应（含维护 503、404）都会带上，而盾牌的 418 仍是最外层、
先于本层返回——用它自己的响应体，不参与本用例。

离线：只打一个不存在的路径，命中 Starlette 的 404，不触任何业务/数据库。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

import main

MOZ = {"User-Agent": "Mozilla/5.0 (headers harness)"}
PROBE_URL = "/api/__security_headers_probe__"

EXPECTED = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "strict-transport-security": "max-age=31536000",
    "content-security-policy": (
        "default-src 'self'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "font-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'none'"
    ),
}


def _client() -> TestClient:
    return TestClient(main.app)


def test_error_responses_carry_the_baseline_headers() -> None:
    client = _client()
    try:
        response = client.get(PROBE_URL, headers=MOZ)
    finally:
        client.close()

    assert response.status_code == 404
    for name, value in EXPECTED.items():
        assert response.headers.get(name) == value


def test_maintenance_503_carries_the_baseline_headers(monkeypatch) -> None:
    async def _enabled(*args, **kwargs):
        return True

    monkeypatch.setattr(main, "maintenance_enabled", _enabled)
    monkeypatch.setattr(main, "maintenance_enabled_fresh", _enabled)

    client = _client()
    try:
        response = client.get("/api/__security_headers_probe__2__", headers=MOZ)
    finally:
        client.close()

    assert response.status_code == 503
    assert response.json()["maintenance"] is True
    for name, value in EXPECTED.items():
        assert response.headers.get(name) == value


def test_shield_418_is_still_the_outermost_layer() -> None:
    client = _client()
    try:
        blocked = client.get(PROBE_URL, headers={"User-Agent": "curl/8.5.0"})
    finally:
        client.close()

    assert blocked.status_code == 418
    assert blocked.json() == main.SHIELD_BLOCKED_MESSAGE



def test_csp_is_part_of_the_shared_header_table() -> None:
    assert "Content-Security-Policy" in main.SECURITY_RESPONSE_HEADERS
    assert "unsafe-eval" not in main.SECURITY_RESPONSE_HEADERS["Content-Security-Policy"]
