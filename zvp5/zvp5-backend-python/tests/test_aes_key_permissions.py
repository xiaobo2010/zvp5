"""``aes_key.txt`` 的权限自检：组/其他可读就收紧成 0600 并告警。

密钥是 JWT 与 TOTP 的派生源：同机任何账号读得到它就能签出合法 token。自检
必须是**不中断启动**的——读不到密钥本来就起不来，权限只是能被顺手修掉的问题。
"""

from __future__ import annotations

import logging
import os
import stat

from util import cert


def _key_file(tmp_path, mode: int):
    path = tmp_path / "aes_key.txt"
    path.write_text("platform-secret", encoding="utf-8")
    os.chmod(path, mode)
    return path


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_world_readable_key_is_tightened_and_reported(tmp_path, caplog) -> None:
    path = _key_file(tmp_path, 0o644)

    with caplog.at_level(logging.WARNING, logger="util.cert"):
        assert cert.read_platform_key(str(path)) == "platform-secret"

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert any("已收紧为 0600" in message for message in _warnings(caplog))


def test_group_readable_key_is_tightened(tmp_path, caplog) -> None:
    path = _key_file(tmp_path, 0o640)

    with caplog.at_level(logging.WARNING, logger="util.cert"):
        cert.read_platform_key(str(path))

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_private_key_is_left_untouched_without_noise(tmp_path, caplog) -> None:
    path = _key_file(tmp_path, 0o600)

    with caplog.at_level(logging.WARNING, logger="util.cert"):
        assert cert.read_platform_key(str(path)) == "platform-secret"

    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert _warnings(caplog) == []


def test_chmod_failure_warns_but_still_reads(tmp_path, caplog, monkeypatch) -> None:
    path = _key_file(tmp_path, 0o644)

    def _denied(*args, **kwargs):
        raise PermissionError("read-only filesystem")

    monkeypatch.setattr(cert.os, "chmod", _denied)

    with caplog.at_level(logging.WARNING, logger="util.cert"):
        assert cert.read_platform_key(str(path)) == "platform-secret"

    assert any("无法收紧" in message for message in _warnings(caplog))
