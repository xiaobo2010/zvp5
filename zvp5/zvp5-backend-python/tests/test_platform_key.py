"""平台主密钥解析顺序（systemd LoadCredential → 显式路径 → 旧文件）。

这个模块是 JWT/TOTP/附件签名/AI 凭据四处共用的读取点，测试要锁住三件事：

1. 优先级：显式路径 > ``CREDENTIALS_DIRECTORY`` > 旧 ``aes_key.txt``；
2. ``CREDENTIALS_DIRECTORY`` 存在但文件缺失时必须**报错**，不能静默退回旧文件；
3. 返回原始文本、不 strip——末尾换行参与 TOTP 与附件签名的 HMAC，去掉会改变
   既有派生结果。
"""

from __future__ import annotations

import pytest

from util import platform_key


def _clear_env(monkeypatch) -> None:
    monkeypatch.delenv("ZVP5_PLATFORM_KEY_FILE", raising=False)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)


def test_explicit_path_beats_credential_directory(tmp_path, monkeypatch):
    credential_dir = tmp_path / "credentials"
    credential_dir.mkdir()
    (credential_dir / platform_key.CREDENTIAL_ID).write_text("cred\n", encoding="utf-8")
    explicit = tmp_path / "explicit-key"
    explicit.write_text("explicit\n", encoding="utf-8")

    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credential_dir))
    monkeypatch.setenv("ZVP5_PLATFORM_KEY_FILE", str(explicit))

    assert platform_key.resolve_path() == explicit
    assert platform_key.read_text() == "explicit\n"


def test_credential_directory_beats_legacy_file(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    (tmp_path / platform_key.CREDENTIAL_ID).write_text("from-systemd\n", encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))

    assert platform_key.read_text() == "from-systemd\n"


def test_legacy_credential_id_is_no_longer_read(tmp_path, monkeypatch):
    """回落层已删除：只认 zvp5_ai_key，旧 id 即使存在也当没挂凭据。"""
    _clear_env(monkeypatch)
    (tmp_path / "zvms_ai_key").write_text("legacy\n", encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))

    with pytest.raises(platform_key.KeyMaterialError):
        platform_key.read_text()


def test_missing_credential_file_does_not_fall_back(tmp_path, monkeypatch):
    """挂载配置错了就启动失败；退回旧文件会把事故藏起来。"""
    _clear_env(monkeypatch)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))

    with pytest.raises(platform_key.KeyMaterialError):
        platform_key.read_text()


def test_raw_text_is_preserved(tmp_path, monkeypatch):
    """末尾换行必须保留：TOTP / 附件签名的派生输入是原始文本。"""
    path = tmp_path / "key"
    path.write_text("a" * 64 + "\n", encoding="utf-8")
    monkeypatch.setenv("ZVP5_PLATFORM_KEY_FILE", str(path))

    assert platform_key.read_text() == "a" * 64 + "\n"


def test_empty_key_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "empty"
    path.write_text(" \n", encoding="utf-8")
    monkeypatch.setenv("ZVP5_PLATFORM_KEY_FILE", str(path))

    with pytest.raises(platform_key.KeyMaterialError):
        platform_key.read_text()


def test_credential_path_detection(tmp_path, monkeypatch):
    credential_dir = tmp_path / "credentials"
    credential_dir.mkdir()
    credential = credential_dir / platform_key.CREDENTIAL_ID
    credential.write_text("from-systemd\n", encoding="utf-8")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(credential_dir))

    assert platform_key.is_credential_path(credential)
    assert not platform_key.is_credential_path(tmp_path / "other-key")


def test_legacy_file_is_the_default(monkeypatch):
    _clear_env(monkeypatch)
    if not platform_key.legacy_path().exists():
        pytest.skip("legacy aes_key.txt is created by tools/setup_local.py")

    assert platform_key.resolve_path() == platform_key.legacy_path()
    assert platform_key.read_text().strip()
