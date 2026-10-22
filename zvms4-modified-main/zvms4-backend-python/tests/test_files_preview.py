"""文件预览端点（``GET /api/system/files/preview``）的离线用例。

独立成文件而不是并入 ``test_v4242_files.py``：那边是 Stage 2 契约的回归集，
这边是读内容的新能力，判定表（文本/图片/二进制/超限）与编码回退值得单独成篇。

全部数据落在 ``tmp_path``；只有 ``/proc`` 拒绝用例碰系统路径，且仅发出被拒请求。
"""

from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import backends
from routers import system_files_router
from services import file_ops
from util.object_id import get_current_user

PREFIX = "/api/system/files"
OPERATOR = "000000000000000000000001"


def _make_client():
    app = FastAPI()
    app.include_router(system_files_router.router, prefix=PREFIX)
    return app


@pytest.fixture
def harness(monkeypatch):
    state = {"per": ["admin"], "roles": ["admin"]}

    async def get_by_oid(oid):
        return {"_id": oid, "roles": list(state["roles"]), "banned": False}

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda session=None: SimpleNamespace(get_by_oid=get_by_oid),
    )

    app = _make_client()
    app.dependency_overrides[get_current_user] = lambda: {
        "id": OPERATOR,
        "per": state["per"],
    }
    return SimpleNamespace(client=TestClient(app), state=state)


def _preview(client, path):
    response = client.get(f"{PREFIX}/preview", params={"path": str(path)})
    assert response.status_code == 200, response.text
    return response.json()["data"]


# ---------------------------------------------------------------------------
# 文本
# ---------------------------------------------------------------------------


def test_text_utf8(harness, tmp_path):
    source = tmp_path / "demo.py"
    source.write_text("print('你好')\n", encoding="utf-8")

    data = _preview(harness.client, source)

    assert data["kind"] == "text"
    assert data["mime"] == "text/x-python"
    assert data["size"] == len("print('你好')\n".encode("utf-8"))
    assert data["content"] == "print('你好')\n"
    assert data["encoding"] == "utf-8"
    assert data["truncated"] is False


def test_text_gbk_fallback(harness, tmp_path):
    source = tmp_path / "legacy.txt"
    source.write_bytes("中文日志".encode("gbk"))

    data = _preview(harness.client, source)

    assert data["kind"] == "text"
    assert data["encoding"] == "gbk"
    assert data["content"] == "中文日志"


def test_text_truncated_at_limit(harness, tmp_path):
    source = tmp_path / "big.log"
    payload = b"a" * (file_ops.PREVIEW_TEXT_BYTES + 100)
    source.write_bytes(payload)

    data = _preview(harness.client, source)

    assert data["kind"] == "text"
    assert data["truncated"] is True
    assert data["size"] == len(payload)
    assert len(data["content"]) == file_ops.PREVIEW_TEXT_BYTES


def test_text_without_extension_is_sniffed(harness, tmp_path):
    source = tmp_path / "notes-2026"
    source.write_text("纯文本兜底\n", encoding="utf-8")

    data = _preview(harness.client, source)

    assert data["kind"] == "text"
    assert data["content"] == "纯文本兜底\n"
    assert data["mime"] == "text/plain"


@pytest.mark.parametrize("name", [".gitignore", ".env", "Dockerfile"])
def test_dotfiles_and_bare_names_are_text(harness, tmp_path, name):
    source = tmp_path / name
    source.write_text("VALUE=1\n", encoding="utf-8")

    data = _preview(harness.client, source)

    assert data["kind"] == "text"
    assert data["content"] == "VALUE=1\n"


# ---------------------------------------------------------------------------
# 图片 / 二进制 / 超限
# ---------------------------------------------------------------------------


def test_image_is_base64_with_mime(harness, tmp_path):
    raw = b"\x89PNG\r\n\x1a\n" + b"payload"
    source = tmp_path / "logo.png"
    source.write_bytes(raw)

    data = _preview(harness.client, source)

    assert data["kind"] == "image"
    assert data["mime"] == "image/png"
    assert data["size"] == len(raw)
    assert data["content"] == base64.b64encode(raw).decode("ascii")
    assert not data["content"].startswith("data:")


def test_oversize_image_is_too_large(harness, tmp_path, monkeypatch):
    monkeypatch.setattr(file_ops, "PREVIEW_IMAGE_BYTES", 8)
    source = tmp_path / "huge.png"
    source.write_bytes(b"\x89PNG" + b"x" * 8)

    data = _preview(harness.client, source)

    assert data["kind"] == "too_large"
    assert data["mime"] == "image/png"
    assert data["content"] is None


def test_binary_is_detected_by_nul(harness, tmp_path):
    source = tmp_path / "blob.bin"
    source.write_bytes(b"\x7fELF\x00\x01\x02binary")

    data = _preview(harness.client, source)

    assert data["kind"] == "binary"
    assert data["mime"] == "application/octet-stream"
    assert data["content"] is None


# ---------------------------------------------------------------------------
# 异常映射
# ---------------------------------------------------------------------------


def test_directory_is_400(harness, tmp_path):
    response = harness.client.get(f"{PREFIX}/preview", params={"path": str(tmp_path)})
    assert response.status_code == 400
    assert response.json()["detail"] == "目录不支持预览"


def test_missing_is_404(harness, tmp_path):
    response = harness.client.get(
        f"{PREFIX}/preview", params={"path": str(tmp_path / "ghost.txt")}
    )
    assert response.status_code == 404


def test_virtual_fs_is_403(harness):
    response = harness.client.get(
        f"{PREFIX}/preview", params={"path": "/proc/self/environ"}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "虚拟文件系统不可访问"


def test_symlink_to_virtual_fs_is_403(harness, tmp_path):
    link = tmp_path / "environ.txt"
    link.symlink_to("/proc/self/environ")

    response = harness.client.get(f"{PREFIX}/preview", params={"path": str(link)})
    assert response.status_code == 403


def test_non_admin_is_403(harness, tmp_path):
    harness.state["per"] = ["student"]
    source = tmp_path / "a.txt"
    source.write_text("x", encoding="utf-8")

    response = harness.client.get(f"{PREFIX}/preview", params={"path": str(source)})
    assert response.status_code == 403


def test_requires_login(tmp_path):
    client = TestClient(_make_client())
    response = client.get(
        f"{PREFIX}/preview", params={"path": str(tmp_path / "a.txt")}
    )
    assert response.status_code == 401


def test_relative_path_is_400(harness):
    response = harness.client.get(
        f"{PREFIX}/preview", params={"path": "etc/passwd"}
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "仅接受绝对路径"
