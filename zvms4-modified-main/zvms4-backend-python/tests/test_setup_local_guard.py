"""``tools/setup_local.py`` 不得在生产机上凭空生成密钥。

2026-10-30 的线上事故：新主机部署时 ``aes_key.txt`` / ``rsa_*.pem`` 没有随包迁移，
有人在生产目录跑了这个「本地开发引导」脚本，脚本照常生成了新密钥。结果库里所有
``AI_CREDENTIALS.api_key_cipher`` 与 ``AI_OCR_ENGINES.secret_cipher`` 全部解不开，
AI 网关对每个请求回 ``AI_CREDENTIAL_UNREADABLE``。这里把「生产机缺密钥必须报错」
钉成回归用例：宁可部署失败，也不能让数据在无人察觉时变成不可读。
"""

from __future__ import annotations

import pytest
from Crypto.PublicKey import RSA

from tools import setup_local


def _write_prod_settings(root) -> None:
    (root / "settings.py").write_text(
        'ORACLE_ACTIVE = "prod"\n', encoding="utf-8"
    )


def test_production_host_refuses_to_generate_missing_keys(tmp_path):
    _write_prod_settings(tmp_path)

    with pytest.raises(SystemExit) as failure:
        setup_local.main(tmp_path)

    message = str(failure.value)
    assert "ORACLE_ACTIVE" in message
    assert "AI_CREDENTIAL_UNREADABLE" in message
    # 拒绝必须是「什么都没写」：半套密钥比没有密钥更难排查。
    assert not (tmp_path / "aes_key.txt").exists()
    assert not (tmp_path / "rsa_private_key.pem").exists()


def test_production_host_is_allowed_when_keys_already_exist(tmp_path):
    _write_prod_settings(tmp_path)
    (tmp_path / "aes_key.txt").write_text("a" * 64, encoding="utf-8")
    key = RSA.generate(1024)
    (tmp_path / "rsa_private_key.pem").write_bytes(key.export_key())
    (tmp_path / "rsa_public_key.pem").write_bytes(key.publickey().export_key())

    # 三个文件齐全时脚本本来就不会生成任何东西，生产标记不该拦住空跑。
    setup_local.main(tmp_path)

    assert (tmp_path / "aes_key.txt").read_text(encoding="utf-8") == "a" * 64


def test_development_host_still_bootstraps_keys(tmp_path, monkeypatch):
    monkeypatch.delenv(setup_local._ALLOW_BOOTSTRAP_ENV, raising=False)
    (tmp_path / "settings.py").write_text(
        'ORACLE_ACTIVE = "test"\n', encoding="utf-8"
    )

    setup_local.main(tmp_path)

    assert len((tmp_path / "aes_key.txt").read_text(encoding="utf-8")) == 64
    assert (tmp_path / "rsa_private_key.pem").exists()
    assert (tmp_path / "rsa_public_key.pem").exists()


def test_explicit_bootstrap_override_allows_rotation(tmp_path, monkeypatch):
    _write_prod_settings(tmp_path)
    monkeypatch.setenv(setup_local._ALLOW_BOOTSTRAP_ENV, "1")

    setup_local.main(tmp_path)

    assert (tmp_path / "aes_key.txt").exists()
    assert (tmp_path / "rsa_private_key.pem").exists()
