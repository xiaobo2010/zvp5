"""全盘文件管理的凭据文件黑名单离线用例。

覆盖：``.env`` 及其前缀变体、常见凭据文件名被读/写/stat 拒绝；目录列举不受影响
（否则管理员连「这里有这几个文件」都看不到）；非点文件的 ``config.env`` 与普通
文本照常可读写。

跑法：``.venv/bin/python -m pytest tests/test_file_ops_secrets.py -q``
"""

from __future__ import annotations

import pytest

from services import file_ops


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        ".env.local",
        ".env.production",
        ".ENV",
        ".git-credentials",
        ".npmrc",
        ".pypirc",
        ".pgpass",
        ".my.cnf",
        "credentials",
        "credentials.json",
    ],
)
def test_credential_names_are_protected(name: str) -> None:
    assert file_ops._is_protected_secret(f"/srv/app/{name}") is True


@pytest.mark.parametrize("name", ["config.env", "notes.txt", "environment.md", "credentials.txt"])
def test_ordinary_names_are_not_protected(name: str) -> None:
    assert file_ops._is_protected_secret(f"/srv/app/{name}") is False


@pytest.mark.parametrize("name", [".env", ".env.production", ".git-credentials"])
def test_read_write_and_stat_are_rejected(tmp_path, name: str) -> None:
    target = tmp_path / name
    target.write_text("SECRET=1\n", encoding="utf-8")

    with pytest.raises(file_ops.FileOpError) as preview:
        file_ops.preview_file(str(target))
    assert preview.value.status_code == 403

    with pytest.raises(file_ops.FileOpError) as written:
        file_ops.write_text(str(target), "SECRET=2\n", "utf-8")
    assert written.value.status_code == 403

    with pytest.raises(file_ops.FileOpError) as stat:
        file_ops.stat_entry(str(target))
    assert stat.value.status_code == 403


def test_listing_still_shows_the_entries(tmp_path) -> None:
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("hi\n", encoding="utf-8")

    page = file_ops.list_dir(str(tmp_path))
    names = {entry["name"] for entry in page["entries"]}

    assert names == {".env", "notes.txt"}


def test_ordinary_files_are_untouched(tmp_path) -> None:
    config = tmp_path / "config.env"
    config.write_text("DEBUG=1\n", encoding="utf-8")

    assert file_ops.preview_file(str(config))["content"] == "DEBUG=1\n"
    assert file_ops.write_text(str(config), "DEBUG=0\n", "utf-8")["size"] == 8

    notes = tmp_path / "notes.txt"
    notes.write_text("hello\n", encoding="utf-8")
    assert file_ops.preview_file(str(notes))["content"] == "hello\n"
