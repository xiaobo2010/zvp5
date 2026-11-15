"""P0-5 / P2-33：``/api/cert`` 不得因文件读失败 500，OpenAPI 不得暴露。

契约：

1. ``/api/cert`` 是维护豁免的公网端点，公钥按 ``__file__`` 绝对定位（不依赖
   cwd），首次读入内存后复用；
2. 文件缺失 / 不可读 → **503** + 固定中文文案（而不是 500，也不是空 200）；
3. ``openapi_url`` 同样置空：``docs_url=None`` 只关掉 UI，``/openapi.json``
   仍可访问，之前只靠 shield 的 SEC_PATTERN 兜着。

离线：公钥路径由用例替换成 ``tmp_path`` 里的假文件，不碰真实密钥。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import main

MOZ = {"User-Agent": "Mozilla/5.0 (p0-5 harness)"}
CERT_URL = "/api/cert"
FAKE_PEM = "-----BEGIN PUBLIC KEY-----\nfake\n-----END PUBLIC KEY-----\n"


@pytest.fixture(autouse=True)
def _reset_cert_cache(monkeypatch):
    monkeypatch.setattr(main, "_cert_pem", None)
    yield


def test_cert_is_served_from_the_backend_directory():
    response = TestClient(main.app).get(CERT_URL, headers=MOZ)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok" and body["code"] == 200
    assert "BEGIN PUBLIC KEY" in body["data"]


def test_cert_reads_the_file_once(monkeypatch, tmp_path):
    pem = tmp_path / "pub.pem"
    pem.write_text(FAKE_PEM, encoding="utf-8")
    monkeypatch.setattr(main, "CERT_PATH", str(pem))

    http = TestClient(main.app)
    assert http.get(CERT_URL, headers=MOZ).json()["data"] == FAKE_PEM

    # 缓存后不再碰磁盘：文件被删也照样 200。
    pem.unlink()
    assert http.get(CERT_URL, headers=MOZ).json()["data"] == FAKE_PEM


def test_missing_cert_file_is_503_not_500(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "CERT_PATH", str(tmp_path / "missing.pem"))

    response = TestClient(main.app).get(CERT_URL, headers=MOZ)

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "error" and body["code"] == 503
    assert body["data"] == "证书暂不可用"


def test_unreadable_cert_file_is_503_too(monkeypatch, tmp_path):
    broken = tmp_path / "broken.pem"
    broken.write_bytes(b"\xff\xfe\x00not-a-pem")
    monkeypatch.setattr(main, "CERT_PATH", str(broken))

    response = TestClient(main.app).get(CERT_URL, headers=MOZ)

    assert response.status_code == 503
    assert response.json()["data"] == "证书暂不可用"


def test_openapi_schema_is_not_exposed():
    # 只关 docs_url 不够：openapi_url 非空时 /openapi.json 仍是可达的路由。
    assert main.app.openapi_url is None
    assert main.app.docs_url is None
