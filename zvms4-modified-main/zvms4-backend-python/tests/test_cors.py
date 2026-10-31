"""CORS contract for a split content/API deployment."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import app, cors_allowed_origins


@pytest.mark.parametrize("origin", ["http://localhost:4173", "http://localhost:5173"])
def test_local_origins_pass_preflight(origin: str) -> None:
    response = TestClient(app).options(
        "/api/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": (
                "authorization,content-type,xuehai-id,x-device-unlock"
            ),
            "User-Agent": "Mozilla/5.0",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "authorization" in response.headers["access-control-allow-headers"].lower()
    assert "xuehai-id" in response.headers["access-control-allow-headers"].lower()
    assert "x-device-unlock" in response.headers["access-control-allow-headers"].lower()


def test_deployment_origins_come_from_environment(monkeypatch) -> None:
    monkeypatch.setenv(
        "ZVMS_CORS_ALLOWED_ORIGINS",
        "https://content.example.test, https://api.example.test/",
    )

    assert cors_allowed_origins()[-2:] == [
        "https://content.example.test",
        "https://api.example.test",
    ]


def test_wildcard_origin_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("ZVMS_CORS_ALLOWED_ORIGINS", "*")

    with pytest.raises(RuntimeError, match="http\\(s\\) origins"):
        cors_allowed_origins()
