"""附件签名密钥的取源契约：绝对路径、缺失即失败、不再退化成公开可算的空键。

为什么单独锁这一条：签名 URL 的 HMAC 键派生自 ``aes_key.txt``。旧实现用相对
cwd 的 ``open("aes_key.txt")`` 并吞掉 ``OSError``，一旦工作目录不是后端根，键就
变成 ``sha256(b"filesign")``——任何拿到源码的人都算得出，签名链接可任意伪造。

``tests/test_aes_key_permissions.py`` 覆盖的是 ``util.cert`` 的权限自检（文件
权限收紧），与附件模块的取源路径是两回事；本文件补的是「取哪个文件 + 取不到
怎么办」。

跑法：``.venv/bin/python -m pytest tests/test_attachment_sign_key.py -q``
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from pathlib import Path

import pytest

from util import attachment

REPO_ROOT = Path(__file__).resolve().parents[1]  # tests/ -> 后端根


def test_key_path_is_anchored_to_the_backend_root():
    assert attachment._AES_KEY_PATH == REPO_ROOT / "aes_key.txt"
    assert attachment._AES_KEY_PATH.is_file()


def test_sign_key_is_not_the_public_empty_key_fallback():
    legacy = hmac.new(b"", b"filesign", hashlib.sha256).digest()

    assert attachment._SIGN_KEY != legacy
    assert attachment._aes_key_bytes.strip() != b""


def test_missing_key_file_fails_fast(tmp_path):
    with pytest.raises(RuntimeError, match="不可用"):
        attachment._load_sign_secret(tmp_path / "aes_key.txt")


def test_empty_key_file_fails_fast(tmp_path):
    empty = tmp_path / "aes_key.txt"
    empty.write_text("  \n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="为空"):
        attachment._load_sign_secret(empty)


def test_signature_computed_from_the_legacy_empty_key_is_rejected():
    oid = "0" * 24
    exp = int(time.time()) + 60
    legacy_key = hmac.new(b"", b"filesign", hashlib.sha256).digest()
    message = f"{oid}|{exp}|attachment".encode("utf-8")
    forged = (
        base64.urlsafe_b64encode(hmac.new(legacy_key, message, hashlib.sha256).digest())
        .rstrip(b"=")
        .decode("ascii")
    )

    assert attachment.verify_signed(oid, exp, "attachment", forged) is False
    genuine = attachment._compute_sig(oid, exp, "attachment")
    assert attachment.verify_signed(oid, exp, "attachment", genuine) is True
