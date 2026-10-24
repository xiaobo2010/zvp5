"""Stage 2 文件管理端点（``/api/system/files``）的离线用例。

全部落在 ``tmp_path`` 上，只读地碰系统路径（``/etc/passwd``、``/proc/...``）
用于验证虚拟文件系统保护，绝不对真实系统目录做写操作。

覆盖四类硬约束：

1. 路径安全：绝对路径、``..`` 展开后再判 ``/proc``/``/sys``、路径段匹配
   （``/procfs`` 不误杀）；``/etc`` 等原黑名单目录已放开。
2. 鉴权：管理员闸（per + 查库复核）、写操作的双人 TOTP 边界（mkdir/upload/
   rename/chmod 要码，edit/delete 免码）、失败时零副作用。
3. 文件动作：list 排序/分页、stat、mkdir/rename/delete、upload 原子落盘与上限、
   chmod 拒绝特殊位、download 流式与上限。
4. 审计：每次成功写入都留 ``type=system_file`` 且不含文件内容。
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
from util.dual_auth import DualAuthError
from util.object_id import get_current_user

PREFIX = "/api/system/files"
OPERATOR = "000000000000000000000001"
APPROVER = "000000000000000000000002"
CODE = "123456"


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


def _make_client():
    app = FastAPI()
    app.include_router(system_files_router.router, prefix=PREFIX)
    return app


@pytest.fixture
def harness(monkeypatch):
    """管理员客户端；守卫/审计替换为记录器，管理员身份可现场改。"""
    state = {"roles": ["admin"], "per": ["admin"], "banned": False, "reject": False}

    async def get_by_oid(oid):
        return {
            "_id": oid,
            "roles": list(state["roles"]),
            "banned": state["banned"],
        }

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda session=None: SimpleNamespace(get_by_oid=get_by_oid),
    )

    audits: list[dict] = []

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

    async def fake_dual_auth(operator, code, *, action):
        if state["reject"]:
            raise DualAuthError(403, "动态码无效或已过期")
        return APPROVER

    monkeypatch.setattr(system_files_router, "require_dual_auth", fake_dual_auth)

    app = _make_client()
    app.dependency_overrides[get_current_user] = lambda: {
        "id": OPERATOR,
        "per": state["per"],
    }
    return SimpleNamespace(client=TestClient(app), audits=audits, state=state)


def _upload(client, directory, name, content, **form):
    data = {"path": str(directory), "totp_code": CODE, **form}
    return client.post(
        f"{PREFIX}/upload", data=data, files={"file": (name, content)}
    )


def _snapshot(root) -> list[tuple[str, int, int]]:
    """目录树的 (相对路径, 大小, 权限位)；用来证明「失败无副作用」。"""
    items = []
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = os.path.join(dirpath, name)
            info = os.lstat(path)
            items.append((os.path.relpath(path, root), info.st_size, info.st_mode))
    return sorted(items)


# ---------------------------------------------------------------------------
# 鉴权
# ---------------------------------------------------------------------------


def test_endpoints_require_login():
    client = TestClient(_make_client())
    assert client.get(f"{PREFIX}/list", params={"path": "/"}).status_code == 401
    assert client.post(f"{PREFIX}/mkdir", json={"path": "/tmp/x"}).status_code == 401


def test_non_admin_is_forbidden(harness, tmp_path):
    harness.state["per"] = ["student"]
    harness.state["roles"] = ["student"]

    assert harness.client.get(f"{PREFIX}/list", params={"path": str(tmp_path)}).status_code == 403
    assert (
        harness.client.post(
            f"{PREFIX}/mkdir", json={"path": str(tmp_path / "x"), "totp_code": CODE}
        ).status_code
        == 403
    )
    assert not (tmp_path / "x").exists()


def test_banned_admin_is_forbidden(harness, tmp_path):
    harness.state["banned"] = True

    response = harness.client.get(f"{PREFIX}/list", params={"path": str(tmp_path)})
    assert response.status_code == 403
    # 统一封禁契约：detail 是机器可读标记，原因走响应头（util/ban.py）。
    assert response.json()["detail"] == "USER_BANNED"
    assert response.headers["X-Ban-Reason"]
    assert response.headers["X-Ban-Until"] == ""


def test_totp_rejection_has_no_side_effects(harness, tmp_path):
    source = tmp_path / "keep.txt"
    source.write_bytes(b"keep")
    directory = tmp_path / "dir"
    directory.mkdir()
    (directory / "inner.txt").write_bytes(b"inner")
    before = _snapshot(tmp_path)

    harness.state["reject"] = True

    mkdir = harness.client.post(
        f"{PREFIX}/mkdir", json={"path": str(tmp_path / "new"), "totp_code": "000000"}
    )
    upload = _upload(harness.client, tmp_path, "new.txt", b"nope", totp_code="000000")
    rename = harness.client.post(
        f"{PREFIX}/rename",
        json={"from": str(source), "to": str(tmp_path / "renamed.txt"), "totp_code": "000000"},
    )
    chmod = harness.client.post(
        f"{PREFIX}/chmod", json={"path": str(source), "mode": 0o600, "totp_code": "000000"}
    )

    for response in (mkdir, upload, rename, chmod):
        assert response.status_code == 403
        assert response.json()["detail"] == "动态码无效或已过期"

    # delete 已不属于双人码范畴（免审批），因此不进上面的拒绝清单；这里只证明它
    # 不会顺手删掉真实文件：目标给一个不存在的路径，得到 404，目录树原样不动。
    delete = harness.client.post(
        f"{PREFIX}/delete", json={"path": str(tmp_path / "ghost.txt")}
    )
    assert delete.status_code == 404

    assert _snapshot(tmp_path) == before
    assert source.read_bytes() == b"keep"
    assert (directory / "inner.txt").exists()


def test_write_endpoints_require_login():
    # 双人码之前还有登录闸；这里证明上传/重命名/删除/改权限没有被「放开路径」顺带削弱。
    client = TestClient(_make_client())
    responses = [
        client.post(f"{PREFIX}/mkdir", json={"path": "/tmp/unused"}),
        client.post(
            f"{PREFIX}/rename", json={"from": "/tmp/a", "to": "/tmp/b"}
        ),
        client.post(f"{PREFIX}/delete", json={"path": "/tmp/a"}),
        client.post(f"{PREFIX}/chmod", json={"path": "/tmp/a", "mode": 0o600}),
        client.post(
            f"{PREFIX}/upload",
            data={"path": "/tmp"},
            files={"file": ("a.txt", b"x")},
        ),
    ]
    assert [response.status_code for response in responses] == [401] * 5


def test_write_endpoints_still_require_dual_auth(monkeypatch, tmp_path):
    """用真实 ``require_dual_auth``（不 fake）验证写操作仍要求另一位管理员的码。"""
    from util import dual_auth as dual_auth_module

    async def get_by_oid(oid):
        return {"_id": oid, "roles": ["admin"], "banned": False}

    async def list_admin_oids():
        return []

    monkeypatch.setattr(
        backends,
        "get_users_repo",
        lambda session=None: SimpleNamespace(
            get_by_oid=get_by_oid, list_admin_oids=list_admin_oids
        ),
    )

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(dual_auth_module, "_write_audit", noop)
    dual_auth_module._failures.pop(OPERATOR, None)

    audits: list[dict] = []

    async def fake_audit(operator_oid, *, action, detail="", log_type="system_op", level="warning"):
        audits.append({"action": action, "detail": detail, "log_type": log_type})

    monkeypatch.setattr(system_files_router, "audit_system_op", fake_audit)

    app = _make_client()
    app.dependency_overrides[get_current_user] = lambda: {
        "id": OPERATOR,
        "per": ["admin"],
    }
    client = TestClient(app)

    wrong = "000000"
    responses = [
        client.post(f"{PREFIX}/mkdir", json={"path": str(tmp_path / "d"), "totp_code": wrong}),
        client.post(
            f"{PREFIX}/rename",
            json={"from": str(tmp_path / "a"), "to": str(tmp_path / "b"), "totp_code": wrong},
        ),
        client.post(
            f"{PREFIX}/chmod",
            json={"path": str(tmp_path / "a"), "mode": 0o600, "totp_code": wrong},
        ),
        client.post(
            f"{PREFIX}/upload",
            data={"path": str(tmp_path), "totp_code": wrong},
            files={"file": ("a.txt", b"x")},
        ),
    ]

    for response in responses:
        assert response.status_code == 403
        assert response.json()["detail"] == "动态码无效或已过期"
    assert not (tmp_path / "d").exists()
    assert not (tmp_path / "a.txt").exists()

    # delete 不需要双人码：同一个客户端（真实守卫、没有有效码）也能删成功，
    # 但审计必须照写，且 detail 里不出现 approver。
    victim = tmp_path / "scratch.txt"
    victim.write_bytes(b"x")
    deleted = client.post(f"{PREFIX}/delete", json={"path": str(victim)})

    assert deleted.status_code == 200
    assert not victim.exists()
    assert audits and audits[-1]["action"] == "fs_admin"
    assert audits[-1]["detail"] == f"delete {victim}"
    assert "approver=" not in audits[-1]["detail"]

    dual_auth_module._failures.pop(OPERATOR, None)


# ---------------------------------------------------------------------------
# 路径安全
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/proc",
        "/proc/self/environ",
        "/proc/self/cmdline",
        "/sys",
        "/sys/kernel",
        "/proc/../proc/self/cmdline",
        "/tmp/../../../proc/self/environ",
    ],
)
def test_virtual_fs_paths_are_forbidden_even_for_reads(harness, path):
    response = harness.client.get(f"{PREFIX}/stat", params={"path": path})
    assert response.status_code == 403
    assert response.json()["detail"] == "虚拟文件系统不可访问"


def test_virtual_fs_is_segment_matched():
    assert file_ops._is_virtual_fs("/proc") is True
    assert file_ops._is_virtual_fs("/proc/self/environ") is True
    assert file_ops._is_virtual_fs("/procfs") is False
    assert file_ops._is_virtual_fs("/procfs/passwd") is False
    assert file_ops._is_virtual_fs("/sys/kernel") is True
    assert file_ops._is_virtual_fs("/syslog") is False
    # 原黑名单目录不再被任何路径判断拦截。
    assert file_ops._is_virtual_fs("/etc") is False
    assert file_ops._is_virtual_fs("/etc/passwd") is False
    assert file_ops._is_virtual_fs("/var/lib/docker/x") is False


def test_segment_matching_holds_on_a_real_filesystem(harness, tmp_path, monkeypatch):
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "token").write_text("s")
    lookalike = tmp_path / "secret-lab"
    lookalike.mkdir()
    (lookalike / "open.txt").write_text("ok")

    monkeypatch.setattr(
        file_ops, "VIRTUAL_FS_PREFIXES", (os.path.realpath(str(secret)),)
    )

    blocked = harness.client.get(f"{PREFIX}/stat", params={"path": str(secret / "token")})
    allowed = harness.client.get(
        f"{PREFIX}/stat", params={"path": str(lookalike / "open.txt")}
    )

    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "虚拟文件系统不可访问"
    assert allowed.status_code == 200


def test_former_system_directories_are_no_longer_blocked(harness):
    # 回归：/etc/passwd 曾经被黑名单 403。现在只允许因 OS 权限/不存在返回别的码，
    # 不能再出现「该路径受保护」或虚拟文件系统的拒绝。
    assert file_ops.resolve_path("/etc/passwd") == "/etc/passwd"
    assert file_ops.resolve_entry_path("/etc/passwd")[0] == "/etc/passwd"

    response = harness.client.get(f"{PREFIX}/stat", params={"path": "/etc/passwd"})
    assert response.status_code in {200, 403, 404}
    detail = response.json().get("detail", "")
    assert detail != "该路径受保护，禁止访问"
    assert "虚拟文件系统" not in detail


@pytest.mark.parametrize("path", ["etc/passwd", "tmp/../etc/passwd", "./etc"])
def test_relative_paths_are_rejected(harness, path):
    response = harness.client.get(f"{PREFIX}/stat", params={"path": path})
    assert response.status_code == 400
    assert response.json()["detail"] == "仅接受绝对路径"


@pytest.mark.parametrize("path", ["/tmp/with\x00nul", "/tmp/with\nnewline"])
def test_control_characters_are_rejected(path):
    with pytest.raises(file_ops.FileOpError) as excinfo:
        file_ops.resolve_path(path)
    assert excinfo.value.status_code == 400


def test_virtual_fs_write_is_forbidden_before_any_syscall(harness):
    response = harness.client.post(
        f"{PREFIX}/chmod",
        json={"path": "/proc/self/environ", "mode": 0o777, "totp_code": CODE},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "虚拟文件系统不可访问"
    assert harness.audits == []


# ---------------------------------------------------------------------------
# list / stat / download
# ---------------------------------------------------------------------------


@pytest.fixture
def tree(tmp_path):
    (tmp_path / "zdir").mkdir()
    (tmp_path / "adir").mkdir()
    (tmp_path / "b.txt").write_bytes(b"bbb")
    (tmp_path / "a.txt").write_bytes(b"a")
    os.symlink(tmp_path / "b.txt", tmp_path / "link.txt")
    return tmp_path


def test_list_puts_dirs_first_and_paginates(harness, tree):
    first = harness.client.get(
        f"{PREFIX}/list", params={"path": str(tree), "offset": 0, "limit": 2}
    )
    assert first.status_code == 200
    body = first.json()
    assert body["status"] == "ok" and body["code"] == 200
    data = body["data"]
    assert data["total"] == 5
    assert [item["name"] for item in data["entries"]] == ["adir", "zdir"]
    assert {item["type"] for item in data["entries"]} == {"dir"}

    rest = harness.client.get(
        f"{PREFIX}/list", params={"path": str(tree), "offset": 2, "limit": 3}
    ).json()["data"]
    assert [item["name"] for item in rest["entries"]] == ["a.txt", "b.txt", "link.txt"]

    link = rest["entries"][2]
    assert link["type"] == "symlink"
    assert link["is_symlink"] is True
    assert link["target"] == str(tree / "b.txt")
    assert rest["entries"][0]["size"] == 1
    assert rest["entries"][0]["mode"] == 0o644
    assert isinstance(rest["entries"][0]["mtime"], int)


def test_list_limit_is_capped_by_contract(harness, tree):
    assert (
        harness.client.get(
            f"{PREFIX}/list", params={"path": str(tree), "limit": 5000}
        ).status_code
        == 422
    )
    assert (
        harness.client.get(
            f"{PREFIX}/list", params={"path": str(tree), "offset": -1}
        ).status_code
        == 422
    )
    # 服务层自身也夹紧，别处直接调用时不会越界。
    assert file_ops.list_dir(str(tree), limit=5000)["limit"] == file_ops.MAX_LIST_LIMIT


def test_list_dir_keeps_only_a_bounded_window(tmp_path, monkeypatch):
    # 大目录不再全量 list + sort：只保留 offset+limit 个候选；total 仍精确。
    for index in range(30):
        (tmp_path / f"{index:02}.txt").write_bytes(b"")
    monkeypatch.setattr(file_ops, "MAX_LIST_WINDOW", 10)

    page = file_ops.list_dir(str(tmp_path), offset=0, limit=5)
    assert page["total"] == 30
    assert [item["name"] for item in page["entries"]] == [
        "00.txt",
        "01.txt",
        "02.txt",
        "03.txt",
        "04.txt",
    ]

    deep = file_ops.list_dir(str(tmp_path), offset=15, limit=5)
    assert deep["total"] == 30
    assert deep["entries"] == []


def test_list_rejects_file_and_missing_dir(harness, tree):
    assert (
        harness.client.get(
            f"{PREFIX}/list", params={"path": str(tree / "a.txt")}
        ).status_code
        == 400
    )
    assert (
        harness.client.get(
            f"{PREFIX}/list", params={"path": str(tree / "nope")}
        ).status_code
        == 404
    )


def test_stat_returns_entry_metadata(harness, tree):
    response = harness.client.get(f"{PREFIX}/stat", params={"path": str(tree / "b.txt")})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["name"] == "b.txt"
    assert data["type"] == "file"
    assert data["size"] == 3
    assert data["mode"] == 0o644
    assert data["path"] == str(tree / "b.txt")
    assert "target" not in data


def test_stat_missing_path_is_404(harness, tmp_path):
    response = harness.client.get(f"{PREFIX}/stat", params={"path": str(tmp_path / "ghost")})
    assert response.status_code == 404


def test_download_streams_file_content(harness, tree):
    response = harness.client.get(f"{PREFIX}/download", params={"path": str(tree / "b.txt")})
    assert response.status_code == 200
    assert response.content == b"bbb"
    assert response.headers["content-type"] == "application/octet-stream"
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["content-length"] == "3"


def test_download_rejects_directory_and_oversize(harness, tree, monkeypatch):
    assert (
        harness.client.get(
            f"{PREFIX}/download", params={"path": str(tree)}
        ).status_code
        == 400
    )

    monkeypatch.setattr(file_ops, "MAX_DOWNLOAD_BYTES", 2)
    over = harness.client.get(f"{PREFIX}/download", params={"path": str(tree / "b.txt")})
    assert over.status_code == 413


# ---------------------------------------------------------------------------
# 写操作
# ---------------------------------------------------------------------------


def test_mkdir_creates_nested_directory(harness, tmp_path):
    target = tmp_path / "new" / "nested"
    response = harness.client.post(
        f"{PREFIX}/mkdir", json={"path": str(target), "totp_code": CODE}
    )
    assert response.status_code == 200
    assert response.json()["data"]["path"] == str(target)
    assert target.is_dir()

    again = harness.client.post(
        f"{PREFIX}/mkdir", json={"path": str(target), "totp_code": CODE}
    )
    assert again.status_code == 409


def test_rename_moves_file_and_refuses_existing_target(harness, tmp_path):
    source = tmp_path / "old.txt"
    source.write_bytes(b"payload")
    target = tmp_path / "new.txt"

    response = harness.client.post(
        f"{PREFIX}/rename",
        json={"from": str(source), "to": str(target), "totp_code": CODE},
    )
    assert response.status_code == 200
    assert not source.exists()
    assert target.read_bytes() == b"payload"

    other = tmp_path / "other.txt"
    other.write_bytes(b"x")
    conflict = harness.client.post(
        f"{PREFIX}/rename",
        json={"from": str(other), "to": str(target), "totp_code": CODE},
    )
    assert conflict.status_code == 409
    assert other.read_bytes() == b"x"

    missing = harness.client.post(
        f"{PREFIX}/rename",
        json={"from": str(tmp_path / "ghost"), "to": str(tmp_path / "ghost2"), "totp_code": CODE},
    )
    assert missing.status_code == 404


def test_delete_empty_dir_and_non_empty_conflict(harness, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert (
        harness.client.post(
            f"{PREFIX}/delete", json={"path": str(empty)}
        ).json()["data"]["deleted"]
        == 1
    )
    assert not empty.exists()

    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "f.txt").write_bytes(b"1")

    conflict = harness.client.post(
        f"{PREFIX}/delete", json={"path": str(occupied)}
    )
    assert conflict.status_code == 409
    assert (occupied / "f.txt").exists()


def test_recursive_delete_removes_tree(harness, tmp_path):
    root = tmp_path / "tree"
    (root / "deep").mkdir(parents=True)
    (root / "a.txt").write_bytes(b"a")
    (root / "deep" / "b.txt").write_bytes(b"b")

    response = harness.client.post(
        f"{PREFIX}/delete",
        json={"path": str(root), "recursive": True},
    )
    assert response.status_code == 200
    assert response.json()["data"]["deleted"] == 3
    assert not root.exists()


def test_recursive_delete_entry_limit(harness, tmp_path, monkeypatch):
    monkeypatch.setattr(file_ops, "MAX_RECURSIVE_ENTRIES", 2)
    root = tmp_path / "big"
    root.mkdir()
    for index in range(3):
        (root / f"{index}.txt").write_bytes(b"x")

    response = harness.client.post(
        f"{PREFIX}/delete",
        json={"path": str(root), "recursive": True},
    )
    assert response.status_code == 400
    assert root.exists()


def test_delete_symlink_keeps_target(harness, tree):
    response = harness.client.post(
        f"{PREFIX}/delete", json={"path": str(tree / "link.txt")}
    )
    assert response.status_code == 200
    assert not (tree / "link.txt").exists()
    assert (tree / "b.txt").read_bytes() == b"bbb"


def test_upload_writes_file_and_conflicts_without_overwrite(harness, tmp_path):
    response = _upload(harness.client, tmp_path, "hello.txt", b"hello")
    assert response.status_code == 200
    assert response.json()["data"]["size"] == 5
    assert (tmp_path / "hello.txt").read_bytes() == b"hello"

    conflict = _upload(harness.client, tmp_path, "hello.txt", b"nope")
    assert conflict.status_code == 409
    assert (tmp_path / "hello.txt").read_bytes() == b"hello"

    replaced = _upload(harness.client, tmp_path, "hello.txt", b"second", overwrite="true")
    assert replaced.status_code == 200
    assert replaced.json()["data"]["overwritten"] is True
    assert (tmp_path / "hello.txt").read_bytes() == b"second"


def test_upload_basename_strips_traversal(harness, tmp_path):
    directory = tmp_path / "uploads"
    directory.mkdir()

    response = _upload(harness.client, directory, "../../escaped.txt", b"x")
    assert response.status_code == 200
    assert (directory / "escaped.txt").read_bytes() == b"x"
    assert not (tmp_path / "escaped.txt").exists()

    windows = _upload(harness.client, directory, "..\\..\\win.txt", b"y")
    assert windows.status_code == 200
    assert (directory / "win.txt").exists()
    assert not (tmp_path / "win.txt").exists()


def test_upload_over_limit_leaves_no_partial_file(harness, tmp_path, monkeypatch):
    monkeypatch.setattr(file_ops, "MAX_UPLOAD_BYTES", 10)

    response = _upload(harness.client, tmp_path, "big.bin", b"x" * 64)

    assert response.status_code == 413
    assert list(tmp_path.iterdir()) == []
    assert not any(name.startswith(".zvms-upload-") for name in os.listdir(tmp_path))


def test_upload_rejects_illegal_filename(harness, tmp_path):
    for name in ("..", "."):
        response = _upload(harness.client, tmp_path, name, b"x")
        assert response.status_code == 400


def test_upload_requires_existing_directory(harness, tmp_path):
    response = _upload(harness.client, tmp_path / "ghost", "a.txt", b"x")
    assert response.status_code == 404


def test_chmod_accepts_plain_modes_only(harness, tmp_path):
    target = tmp_path / "mode.txt"
    target.write_bytes(b"x")

    ok = harness.client.post(
        f"{PREFIX}/chmod", json={"path": str(target), "mode": 0o640, "totp_code": CODE}
    )
    assert ok.status_code == 200
    assert stat_module.S_IMODE(target.stat().st_mode) == 0o640

    for mode in (0o4755, 0o2755, 0o1755, 0o1000, 0o4000, 0o7777):
        response = harness.client.post(
            f"{PREFIX}/chmod",
            json={"path": str(target), "mode": mode, "totp_code": CODE},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "权限位只允许 0o000-0o777（含特殊位的一律拒绝）"

    assert stat_module.S_IMODE(target.stat().st_mode) == 0o640


def test_chmod_rejects_non_integer_mode(harness, tmp_path):
    target = tmp_path / "mode.txt"
    target.write_bytes(b"x")
    response = harness.client.post(
        f"{PREFIX}/chmod", json={"path": str(target), "mode": "640", "totp_code": CODE}
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# 审计
# ---------------------------------------------------------------------------


def test_every_write_is_audited_without_file_content(harness, tmp_path):
    target = tmp_path / "audited.txt"

    _upload(harness.client, tmp_path, "audited.txt", b"secret-body")
    harness.client.post(
        f"{PREFIX}/chmod", json={"path": str(target), "mode": 0o600, "totp_code": CODE}
    )
    harness.client.post(f"{PREFIX}/delete", json={"path": str(target)})

    assert [entry["action"] for entry in harness.audits] == ["fs_admin"] * 3
    for entry in harness.audits:
        assert entry["operator"] == OPERATOR
        assert entry["log_type"] == "system_file"
        assert entry["level"] == "warning"
        assert "secret-body" not in entry["detail"]

    details = [entry["detail"] for entry in harness.audits]
    assert details[0].startswith(f"upload {target}")
    assert details[0].endswith(f"approver={APPROVER}")
    assert details[1].startswith(f"chmod {target}")
    assert details[1].endswith(f"approver={APPROVER}")
    # delete 免双人码，detail 只有动作与路径，不能凭空出现审批人。
    assert details[2] == f"delete {target}"
