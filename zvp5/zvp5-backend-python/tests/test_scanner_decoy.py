"""未登录目录扫描诱饵：路径归一化、字典命中、canary 边界与中间件实测。

这些用例走真实的 ``main.app``，但不连 ADB：418 分支的审计写库用空日志类
打桩；诱饵分支本身不写库。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main
from services import scanner_decoy
from util.cert import jwt_encode

MOZ = {"User-Agent": "Mozilla/5.0 (scanner-decoy harness)"}
DIRSEARCH = {"User-Agent": "dirsearch/0.4.3"}


class _NullLog:
    def __init__(self, **_kwargs) -> None:
        pass

    async def insert_log(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _stub_audit(monkeypatch):
    monkeypatch.setattr(main, "ZVMSLog", _NullLog)


@pytest.fixture
def client():
    http = TestClient(main.app)
    try:
        yield http
    finally:
        http.close()


def _enable(monkeypatch, body: str = "CUSTOM\nBODY") -> None:
    monkeypatch.setattr(scanner_decoy, "_enabled", lambda: True)
    monkeypatch.setattr(scanner_decoy, "_body", lambda: body)


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------

def test_normalize_path_decodes_and_collapses() -> None:
    assert scanner_decoy.normalize_path("/%2eenv") == "/.env"
    assert scanner_decoy.normalize_path("//wp-admin///") == "/wp-admin"
    assert scanner_decoy.normalize_path("/foo/../.git/config") == "/.git/config"
    assert scanner_decoy.normalize_path("/WP-Admin?x=1#frag") == "/wp-admin"


def test_dictionary_is_not_empty() -> None:
    assert len(scanner_decoy.PATHS) > 100


def test_high_signal_paths_match() -> None:
    for path in (
        "/.env",
        "/.git/config",
        "/wp-admin/",
        "/phpmyadmin",
        "/actuator/env",
        "/server-status?auto",
        "/api/.env",
        "/api/wp-login.php",
    ):
        assert scanner_decoy.is_scanner_path(path), path


def test_public_paths_are_allowlisted() -> None:
    for path in (
        "/",
        "/assets/index-abc.js",
        "/themes.json",
        "/theme-switcher.js",
        "/api/health",
        "/api/version",
        "/api/cert",
        "/api/v1/themes/server-time",
        "/api/ai/v1/models",
        "/api/logs",
        "/api/users",
        "/api/system",
        "/themes/canary-pro/.env",
    ):
        assert not scanner_decoy.is_scanner_path(path), path


def test_body_and_switch_read_from_settings(monkeypatch) -> None:
    values = {
        "security.scanner_decoy_enabled": False,
        "security.scanner_decoy_body": "第一行\n第二行",
    }
    monkeypatch.setattr(
        scanner_decoy.settings_runtime,
        "get_sync",
        lambda key: values[key],
    )

    assert scanner_decoy._enabled() is False
    assert scanner_decoy._body() == "第一行\n第二行"


# ---------------------------------------------------------------------------
# 中间件实测
# ---------------------------------------------------------------------------

def test_default_body_is_neutral_and_switch_is_on(client) -> None:
    response = client.get("/.env", headers=DIRSEARCH)

    assert response.status_code == 200
    assert response.text == "Not Found"


def test_anonymous_scanner_gets_200_and_editable_body(monkeypatch, client) -> None:
    _enable(monkeypatch)

    response = client.get("/.env", headers=DIRSEARCH)

    assert response.status_code == 200
    assert response.text == "CUSTOM\nBODY"
    assert response.headers["content-type"].startswith("text/plain")
    assert response.headers["cache-control"] == "no-store"


def test_api_prefixed_scanner_path_matches(monkeypatch, client) -> None:
    _enable(monkeypatch, "api decoy")

    response = client.get("/api/.env", headers=DIRSEARCH)

    assert response.status_code == 200
    assert response.text == "api decoy"


def test_pro_referer_is_not_decoyed(monkeypatch, client) -> None:
    _enable(monkeypatch)

    response = client.get(
        "/.env",
        headers={**MOZ, "Referer": "https://zvp.example/themes/canary-pro/"},
    )

    assert response.status_code == 418


def test_themes_path_is_not_decoyed(monkeypatch, client) -> None:
    _enable(monkeypatch)

    response = client.get("/themes/canary-pro/.env", headers=MOZ)

    assert response.status_code == 418


def test_authenticated_user_is_not_decoyed(monkeypatch, client) -> None:
    _enable(monkeypatch)
    token = jwt_encode("0" * 24, [], ["student"], type="long")

    response = client.get(
        "/.env",
        headers={**MOZ, "Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 418


def test_post_is_not_decoyed(monkeypatch, client) -> None:
    _enable(monkeypatch)

    response = client.post("/wp-admin", headers=MOZ)

    assert response.status_code == 404


def test_disabled_switch_restores_old_behaviour(monkeypatch, client) -> None:
    monkeypatch.setattr(scanner_decoy, "_enabled", lambda: False)
    monkeypatch.setattr(scanner_decoy, "_body", lambda: "unused")

    response = client.get("/.env", headers=MOZ)

    assert response.status_code == 418
