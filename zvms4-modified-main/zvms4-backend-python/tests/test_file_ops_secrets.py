"""全盘文件管理的凭据文件黑名单离线用例。

覆盖：``.env`` 及其前缀变体、常见凭据文件名被读/写/stat 拒绝；目录列举不受影响
（否则管理员连「这里有这几个文件」都看不到）；非点文件的 ``config.env`` 与普通
文本照常可读写。

2026-11 补丁：生产上真实存在的是**带后缀的备份**（``settings.py.bak.<时间戳>``、
``id_rsa.bak``）、sshd 默认密钥 ``ssh_key``，以及 ``.ssh`` / ``.git`` 目录。
精确名字匹配挡不住它们，现在按「前缀 + 扩展名 + 路径段」四层判定，本文件
逐类锁住语义，并确认普通用户文件不被误伤。

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
        "settings.py",
        "aes_key.txt",
        "rsa_private_key.pem",
        "id_ed25519",
    ],
)
def test_credential_names_are_protected(name: str) -> None:
    assert file_ops._is_protected_secret(f"/srv/app/{name}") is True


@pytest.mark.parametrize(
    "path",
    [
        # 生产 LA 机器上真实存在的备份：精确集合永远列不全时间戳。
        "/www/zvms4/backend/settings.py.bak.20261013121615",
        "/www/zvms4/backend/settings.py.bak",
        # sshd 默认私钥与授权名单（services/ssh_bridge.py 的默认路径）。
        "/www/zvms4/backend/ssh_key",
        "/www/zvms4/backend/ssh_key.pub",
        "/home/u/authorized_keys",
        "/home/u/authorized_keys2",
        "/home/u/id_rsa.bak",
        "/home/u/.ssh/id_rsa",
        "/home/u/.SSH/id_ed25519",
        # Oracle 钱包/属性文件的备份与任意 .pem/.key。
        "/home/u/ewallet.pem",
        "/www/zvms4/backend/foo.key",
        "/www/zvms4/backend/cwallet.sso.bak",
        "/www/zvms4/backend/tnsnames.ora.old",
        # 仓库自身：.git/config 里有远端地址与可能的凭据。
        "/www/zvms4/.git/config",
        "/home/u/project/.git",
    ],
)
def test_backup_and_directory_variants_are_protected(path: str) -> None:
    assert file_ops._is_protected_secret(path) is True


def test_every_exact_basename_still_matches() -> None:
    """精确名单一项都不能被前缀/后缀重构顺手删掉。"""
    for name in file_ops.PROTECTED_BASENAMES:
        assert file_ops._is_protected_secret(f"/home/u/{name}") is True, name


@pytest.mark.parametrize(
    "name",
    ["config.env", "notes.txt", "environment.md", "settings_backup.txt"],
)
def test_ordinary_names_are_not_protected(name: str) -> None:
    assert file_ops._is_protected_secret(f"/srv/app/{name}") is False


def test_credentials_prefix_is_deliberately_broad() -> None:
    """``credentials`` 前缀按设计挡掉 ``credentials.txt`` 之类的变体。

    收紧是有意的：文件名以 credentials 开头就按凭据处理，不再猜「这看起来像
    普通文本」。普通用户文件的误伤面由下一组用例锁住。
    """
    assert file_ops._is_protected_secret("/home/u/credentials.txt") is True
    assert file_ops._is_protected_secret("/home/u/credentials_backup.csv") is True


@pytest.mark.parametrize(
    "path",
    [
        "/home/u/photo.jpg",
        "/home/u/notes.txt",
        "/home/u/.config/app/config.yaml",
        "/www/zvms4/frontend-dist/index.html",
        "/www/zvms4/backend/app.py",
    ],
)
def test_user_files_are_not_blocked(path: str) -> None:
    assert file_ops._is_protected_secret(path) is False


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
