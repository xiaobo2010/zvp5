"""AI 凭据加解密（AES-256-GCM）的离线契约。

这里钉的是「密钥换了/密文坏了会怎样」这类只在生产上才会发生的事：

* 随机 nonce：同一明文两次加密必须是两段不同密文，否则库里两行同 key 会互相
  印证（也说明 nonce 被复用了，GCM 的安全性直接作废）；
* 篡改任何一个字节都必须解密失败，且错误信息里**不能**出现明文或密文——这句
  字符串会一路进日志；
* ``key_hint`` 的 12 位门槛：8 位 key 露 4 位等于泄掉一半熵。

主密钥由环境变量注入并在用例结束后清掉，不读仓库根的 ``aes_key.txt``。
"""

from __future__ import annotations

import base64

import pytest

from services.ai import crypto

SECRET = "sk-unit-test-0123456789abcdef"


@pytest.fixture(autouse=True)
def _isolated_master_key(monkeypatch):
    """固定主密钥并清掉进程内派生缓存：否则结果取决于本机有没有 aes_key.txt。"""
    monkeypatch.setenv(crypto._SECRET_ENV, "unit-test-master-secret")
    monkeypatch.setattr(crypto, "_master_key", None)
    yield
    crypto._master_key = None


def _flip(token: str, index: int) -> str:
    raw = bytearray(base64.urlsafe_b64decode(token.encode("ascii")))
    raw[index] ^= 0x01
    return base64.urlsafe_b64encode(bytes(raw)).decode("ascii")


def test_roundtrip_preserves_plaintext() -> None:
    for plaintext in (SECRET, "中文密钥-测试", "x" * 4096):
        assert crypto.decrypt_secret(crypto.encrypt_secret(plaintext)) == plaintext


def test_ciphertext_is_randomized_per_call() -> None:
    first = crypto.encrypt_secret(SECRET)
    second = crypto.encrypt_secret(SECRET)

    assert first != second  # nonce 随机；相等就意味着 nonce 复用了
    assert crypto.decrypt_secret(first) == crypto.decrypt_secret(second) == SECRET


@pytest.mark.parametrize("index", [0, 11, 12, -17, -1])
def test_tampering_any_byte_fails_without_leaking_secrets(index: int) -> None:
    """nonce / 密文 / tag 各改一个字节都必须失败，且异常串里没有秘密。"""
    token = crypto.encrypt_secret(SECRET)
    bad = _flip(token, index)

    with pytest.raises(ValueError) as failure:
        crypto.decrypt_secret(bad)

    message = str(failure.value)
    assert SECRET not in message
    assert bad not in message
    assert token not in message


def test_garbage_tokens_raise_value_error() -> None:
    for token in ("", "not-base64!!", base64.urlsafe_b64encode(b"short").decode()):
        with pytest.raises(ValueError):
            crypto.decrypt_secret(token)


def test_rotating_the_master_key_makes_old_ciphertext_unreadable(monkeypatch) -> None:
    token = crypto.encrypt_secret(SECRET)

    monkeypatch.setenv(crypto._SECRET_ENV, "a-completely-different-master")
    monkeypatch.setattr(crypto, "_master_key", None)

    with pytest.raises(ValueError):
        crypto.decrypt_secret(token)


@pytest.mark.parametrize(
    "plaintext,expected",
    [
        ("", "****"),
        ("short", "****"),
        ("01234567890", "****"),  # 11 位：差一位也不给
        ("012345678901", "8901"),  # 12 位是门槛，刚好开始露末四位
        ("sk-abcdefghijklmn", "klmn"),
        ("  padded-key-123456  ", "3456"),
    ],
)


def test_key_hint_threshold(plaintext: str, expected: str) -> None:
    assert crypto.key_hint(plaintext) == expected
