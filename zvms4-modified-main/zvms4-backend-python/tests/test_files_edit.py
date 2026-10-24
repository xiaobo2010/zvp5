"""文本编辑端点（``POST /api/system/files/edit``）的离线用例。

独立成文件而不是并入 ``test_v4242_files.py``：那边是 Stage 2 契约（路径安全 /
双人码边界 / 增删改）的回归集，这边是「整篇写回」新能力的判定表——编码白名单、
权限位保持、原子替换与大小上限，值得单独成篇。

与 ``test_files_preview.py`` 同一套 harness：依赖覆盖 + 替身 users repo，不起
真实数据库，也不对系统目录做任何写操作（只有 ``/proc`` 拒绝用例碰系统路径）。
"""

from __future__ import annotations

import os
import stat as stat_module
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
    """管理员客户端；审计换成记录器，管理员身份可现场改。"""
    state = {"per": ["admin"], "roles": ["admin"]}
    audits: list[dict] = []

    async def get_by_oid(oid):
        return {"_id": oid, "roles": list(state["roles"]), "banned": False}

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda session=None: SimpleNamespace(get_by_oid=get_by_oid),
    )

    async def fake_audit(
        operator_oid,
        *,
        action,
        detail="",
        log_type="system_op",
        level="warning",
    ):
        audits.append(
            {
                "operator": operator_oid,
                "action": action,
                "detail": detail,
                "log_type": log_type,
                "level": level,
            }
        )

    monkeypatch.setattr(system_files_router, "audit_system_op", fake_audit)

    app = _make_client()
    app.dependency_overrides[get_current_user] = lambda: {
        "id": OPERATOR,
        "per": state["per"],
    }
    return SimpleNamespace(client=TestClient(app), audits=audits, state=state)


# ---------------------------------------------------------------------------
# 服务层：编码与写入
# ---------------------------------------------------------------------------


def test_utf8_write_keeps_mode(tmp_path):
    target = tmp_path / "conf.txt"
    target.write_bytes(b"old")
    os.chmod(target, 0o640)

    data = file_ops.write_text(str(target), "新内容\nsecond line\n", "utf-8")

    assert target.read_text(encoding="utf-8") == "新内容\nsecond line\n"
    # mkstemp 默认 0600，原子替换前必须恢复原模式，否则编辑会把配置改成私有。
    assert stat_module.S_IMODE(target.stat().st_mode) == 0o640
    assert data["path"] == str(target)
    assert data["encoding"] == "utf-8"
    assert data["size"] == len("新内容\nsecond line\n".encode("utf-8"))


def test_gbk_write_round_trips(tmp_path):
    target = tmp_path / "legacy.log"
    target.write_bytes(b"initial")

    file_ops.write_text(str(target), "中文日志内容", "gbk")

    assert target.read_bytes() == "中文日志内容".encode("gbk")
    assert target.read_text(encoding="gbk") == "中文日志内容"


@pytest.mark.parametrize("encoding", ["latin-1", "utf-16", "ascii", "no-such-codec"])
def test_unsupported_encoding_is_400_and_file_untouched(tmp_path, encoding):
    target = tmp_path / "keep.txt"
    target.write_bytes("原样".encode("utf-8"))

    with pytest.raises(file_ops.FileOpError) as excinfo:
        file_ops.write_text(str(target), "别的内容", encoding)

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "不支持的编码：只允许 utf-8 或 gbk"
    assert target.read_bytes() == "原样".encode("utf-8")


def test_missing_target_is_404(tmp_path):
    with pytest.raises(file_ops.FileOpError) as excinfo:
        file_ops.write_text(str(tmp_path / "ghost.txt"), "x", "utf-8")

    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == "路径不存在"


def test_directory_target_is_400(tmp_path):
    with pytest.raises(file_ops.FileOpError) as excinfo:
        file_ops.write_text(str(tmp_path), "x", "utf-8")

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "目录不支持编辑"


def test_unencodable_content_is_400_and_file_untouched(tmp_path):
    target = tmp_path / "keep.txt"
    target.write_bytes(b"keep")

    with pytest.raises(file_ops.FileOpError) as excinfo:
        file_ops.write_text(str(target), "表情 🙂 不在 GBK 里", "gbk")

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "内容无法按 gbk 编码保存"
    assert target.read_bytes() == b"keep"


def test_oversize_content_is_413_and_file_untouched(tmp_path):
    target = tmp_path / "big.txt"
    target.write_bytes(b"small")

    with pytest.raises(file_ops.FileOpError) as excinfo:
        file_ops.write_text(str(target), "a" * (file_ops.MAX_EDIT_BYTES + 1), "utf-8")

    assert excinfo.value.status_code == 413
    assert excinfo.value.detail == "文件超过 512KB 编辑上限"
    assert target.read_bytes() == b"small"
    # 超限在落盘前就被拒，目录里不应留下 .zvms-edit- 残片。
    assert [name for name in os.listdir(tmp_path) if name.startswith(".zvms-edit-")] == []


def test_virtual_fs_target_is_403(tmp_path):
    with pytest.raises(file_ops.FileOpError) as excinfo:
        file_ops.write_text("/proc/self/environ", "x", "utf-8")

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "虚拟文件系统不可访问"


def test_symlink_is_followed_to_its_target(tmp_path):
    # 文档化的行为：resolve_path 展开链接，编辑链接写的是目标文件本身。
    real = tmp_path / "real.txt"
    real.write_bytes(b"before")
    link = tmp_path / "link.txt"
    link.symlink_to(real)

    data = file_ops.write_text(str(link), "after", "utf-8")

    assert data["path"] == str(real)
    assert real.read_bytes() == b"after"
    assert link.is_symlink()


def test_failed_write_leaves_no_temp_file(tmp_path, monkeypatch):
    target = tmp_path / "ok.txt"
    target.write_bytes(b"stable")

    def broken_replace(source, destination):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(file_ops.os, "replace", broken_replace)

    with pytest.raises(file_ops.FileOpError) as excinfo:
        file_ops.write_text(str(target), "new", "utf-8")

    assert excinfo.value.status_code == 400
    assert target.read_bytes() == b"stable"
    assert [name for name in os.listdir(tmp_path) if name.startswith(".zvms-edit-")] == []


# ---------------------------------------------------------------------------
# 端点层
# ---------------------------------------------------------------------------


def test_edit_endpoint_needs_no_totp_code(harness, tmp_path):
    target = tmp_path / "app.conf"
    target.write_bytes(b"port=1\n")

    response = harness.client.post(
        f"{PREFIX}/edit", json={"path": str(target), "content": "port=8080\n"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok" and body["code"] == 200
    assert body["data"]["encoding"] == "utf-8"
    assert body["data"]["size"] == len(b"port=8080\n")
    assert target.read_bytes() == b"port=8080\n"

    # 审计照写：只有动作、路径与字节数，不含内容。
    assert [entry["action"] for entry in harness.audits] == ["fs_admin"]
    detail = harness.audits[0]["detail"]
    assert detail == f"edit {target} {len(b'port=8080\n')} bytes"
    assert "port=8080" not in detail
    assert "approver=" not in detail


def test_edit_endpoint_accepts_gbk(harness, tmp_path):
    target = tmp_path / "gbk.txt"
    target.write_bytes(b"x")

    response = harness.client.post(
        f"{PREFIX}/edit",
        json={"path": str(target), "content": "中文", "encoding": "gbk"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["encoding"] == "gbk"
    assert target.read_bytes() == "中文".encode("gbk")


def test_edit_endpoint_maps_errors(harness, tmp_path):
    unsupported = harness.client.post(
        f"{PREFIX}/edit",
        json={"path": str(tmp_path / "a.txt"), "content": "x", "encoding": "latin-1"},
    )
    assert unsupported.status_code == 400
    assert unsupported.json()["detail"] == "不支持的编码：只允许 utf-8 或 gbk"

    virtual = harness.client.post(
        f"{PREFIX}/edit", json={"path": "/proc/self/environ", "content": "x"}
    )
    assert virtual.status_code == 403
    assert harness.audits == []


def test_delete_endpoint_needs_no_totp_code_but_still_audits(harness, tmp_path):
    victim = tmp_path / "scratch.txt"
    victim.write_bytes(b"temp-body")

    response = harness.client.post(f"{PREFIX}/delete", json={"path": str(victim)})

    assert response.status_code == 200, response.text
    assert response.json()["data"]["deleted"] == 1
    assert not victim.exists()

    assert [entry["action"] for entry in harness.audits] == ["fs_admin"]
    entry = harness.audits[0]
    assert entry["log_type"] == "system_file"
    assert entry["detail"] == f"delete {victim}"
    assert "approver=" not in entry["detail"]
    assert "temp-body" not in entry["detail"]


def test_edit_endpoint_rejects_non_admin(harness, tmp_path):
    harness.state["per"] = ["student"]
    harness.state["roles"] = ["student"]
    target = tmp_path / "a.txt"
    target.write_bytes(b"x")

    response = harness.client.post(
        f"{PREFIX}/edit", json={"path": str(target), "content": "y"}
    )

    assert response.status_code == 403
    assert target.read_bytes() == b"x"
    assert harness.audits == []
