from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import main


@pytest.mark.parametrize("result,expected", [(True, 200), (False, 503), (ConnectionError("secret DSN"), 503)])
def test_health_status_matches_connectivity(monkeypatch, result, expected):
    ping = AsyncMock(side_effect=result) if isinstance(result, Exception) else AsyncMock(return_value=result)
    monkeypatch.setattr(main, "oracle_ping", ping)
    response = TestClient(main.app).get("/api/health", headers={"User-Agent": "Mozilla/5.0"})
    assert response.status_code == expected
    assert "secret" not in response.text
