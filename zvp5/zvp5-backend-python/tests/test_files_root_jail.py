"""文件根目录 jail（``services.file_ops.ALLOWED_ROOTS``）的边界。

jail 是「文件管理端点」与「agent ``fs_*`` 工具」共同依赖的唯一判定点，所以这里的
每一条都钉在**路径段**上而不是字符串前缀：

* 白名单内：站点根及其子路径、根自身（``/home`` 分支已随 ProtectHome=true
  的部署现实删除，见 ``services/file_ops.py`` 注释）；
* 白名单外：``/etc`` / ``/root`` / ``/home`` / ``/www/zvms4-old``（同名前缀伪装）；
* 软链逃逸：白名单内的链接指向 ``/etc``，``realpath`` 之后仍被拒。

``tests/conftest.py`` 给所有用例把系统临时目录并进了白名单（用例都在 ``tmp_path``
里造文件），所以本文件先把它换回生产口径。
"""

from __future__ import annotations

import os

import pytest

from services import file_ops

PRODUCTION_ROOTS = ("/www/zvms4",)


@pytest.fixture(autouse=True)
def _production_roots(monkeypatch) -> None:
    monkeypatch.setattr(file_ops, "ALLOWED_ROOTS", PRODUCTION_ROOTS)


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "/etc",
        "/root",
        "/root/.bashrc",
        "/usr/bin/python3",
        "/var/lib/docker/x",
        "/tmp/scratch.txt",
        "/home/xiaobo2010/notes.txt",
        "/www/zvms4-old/x",
        "/www/zvms40",
    ],
)
def test_paths_outside_the_roots_are_forbidden(path: str) -> None:
    with pytest.raises(file_ops.FileOpError) as caught:
        file_ops.resolve_path(path)
    assert caught.value.status_code == 403
    assert caught.value.code == "FILES_ROOT_FORBIDDEN"
    assert caught.value.detail == "路径不在允许的根目录范围内"


@pytest.mark.parametrize(
    "path",
    [
        "/www/zvms4",
        "/www/zvms4/app/main.py",
        "/www/zvms4/app/../app/main.py",
    ],
)
def test_paths_inside_the_roots_are_allowed(path: str) -> None:
    assert file_ops.resolve_path(path) == os.path.realpath(path)


def test_root_itself_is_not_an_entry() -> None:
    with pytest.raises(file_ops.FileOpError) as caught:
        file_ops.resolve_entry_path("/")
    assert caught.value.code == "FILES_ROOT_FORBIDDEN"


@pytest.mark.parametrize(
    "path",
    ["/www/zvms4/app/main.py", "/www/zvms4/data/backups"],
)
def test_entry_paths_inside_the_roots_are_allowed(path: str) -> None:
    entry, target = file_ops.resolve_entry_path(path)
    assert entry == path
    assert target == os.path.realpath(path)


@pytest.mark.parametrize("path", ["/etc/passwd", "/home2/x.txt", "/www/zvms4-old/x"])
def test_entry_paths_outside_the_roots_are_forbidden(path: str) -> None:
    with pytest.raises(file_ops.FileOpError) as caught:
        file_ops.resolve_entry_path(path)
    assert caught.value.code == "FILES_ROOT_FORBIDDEN"


def test_symlink_escape_is_resolved_before_the_jail(tmp_path, monkeypatch) -> None:
    """白名单内的软链指向 ``/etc``：``realpath`` 先展开，jail 再按真实落点拒。"""
    root = tmp_path / "root"
    root.mkdir()
    inside = root / "ok.txt"
    inside.write_text("ok", encoding="utf-8")
    escape = root / "escape"
    escape.symlink_to("/etc")
    monkeypatch.setattr(file_ops, "ALLOWED_ROOTS", (os.path.realpath(str(root)),))

    assert file_ops.resolve_path(str(inside)) == os.path.realpath(str(inside))

    with pytest.raises(file_ops.FileOpError) as caught:
        file_ops.resolve_path(str(escape / "passwd"))
    assert caught.value.code == "FILES_ROOT_FORBIDDEN"
    assert os.path.realpath(str(escape / "passwd")) == "/etc/passwd"

    with pytest.raises(file_ops.FileOpError) as entry:
        file_ops.resolve_entry_path(str(escape / "passwd"))
    assert entry.value.code == "FILES_ROOT_FORBIDDEN"


def test_dot_dot_hop_stays_inside_the_jail() -> None:
    """``..`` 只会在 realpath 里展开，不能借它从根内跳到根外。"""
    assert file_ops.resolve_path("/www/zvms4/app/../app/main.py") == (
        "/www/zvms4/app/main.py"
    )
    with pytest.raises(file_ops.FileOpError) as caught:
        file_ops.resolve_path("/www/zvms4/../etc/passwd")
    assert caught.value.code == "FILES_ROOT_FORBIDDEN"


def test_agent_tools_keep_the_same_error_shape() -> None:
    """agent 侧只翻译异常，不改口径：403 + 中文一句话（``AI_AGENT_TOOL_FAILED``）。"""
    from services.agent import ops_tools

    translated = ops_tools._service_error(
        file_ops.FileOpError(403, file_ops.ROOT_FORBIDDEN_DETAIL)
    )
    assert translated.code == "AI_AGENT_TOOL_FAILED"
    assert translated.status_code == 403
    assert translated.message == "路径不在允许的根目录范围内"
