"""CORS contract for the split content/API deployment.

The browser entry point is ``zvp.xiaobopisces.top`` while API requests target
``zvp.albwxy.fun``. Requests carry Authorization and device headers, so the
preflight must explicitly allow the main-site origin and credentials.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.mark.parametrize(
    "origin",
    [
        "https://zvp.xiaobopisces.top",
        "https://zvp.albwxy.fun",
    ],
)
def test_split_site_origins_pass_preflight(origin: str) -> None:
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
