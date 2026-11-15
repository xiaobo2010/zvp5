"""AI 凭据的对称加解密（AES-256-GCM）。

为什么加密而不是哈希：上游 api_key 必须能还原成明文去发请求，哈希做不到；所以
库里存的是密文，主密钥留在进程环境（``ZVP5_AI_KEY_SECRET``）或 ``aes_key.txt``
（路径解析见 :mod:`util.platform_key`，生产推荐 systemd ``LoadCredential``），
数据库备份泄露不等于凭据泄露。

为什么 GCM：自带完整性校验。密文被改一个字节，``decrypt_and_verify`` 会抛错，
我们就能把「库里数据坏了」与「换过主密钥」区分成同一种可观测结果（502），而不是
拿一段乱码去调上游。

主密钥是**派生**的而不是直接用文件内容：``aes_key.txt`` 是 65 字节的随机串，
长度本身不保证是 32；SHA-256 把任意长度收敛成 AES-256 要的 32 字节，且加了一段
域分隔前缀，避免这把密钥被同仓库里别的用途复用。
"""

from __future__ import annotations

import base64
import hashlib
import os

from Crypto.Cipher import AES

from util import platform_key

_SECRET_ENV = "ZVP5_AI_KEY_SECRET"
_KEY_SALT = b"zvms-ai-credential-v1:"
_NONCE_BYTES = 12  # GCM 的推荐长度；随机 nonce 复用会直接摧毁 GCM 的安全性
_TAG_BYTES = 16
_HINT_VISIBLE = 4
_MIN_HINTABLE = 12

_master_key: bytes | None = None


def _load_secret() -> str:
    """环境变量优先；否则走共享解析（显式路径 → LoadCredential → 旧文件）。

    这里必须 ``strip()``：历史实现读文件后就去空白，生产密文都是用去掉换行的
    64 字符派生的；改成保留换行会让所有已存密文变成不可读。
    """
    value = os.environ.get(_SECRET_ENV)
    if value:
        return value
    try:
        raw = platform_key.read_text()
    except platform_key.KeyMaterialError as exc:
        raise ValueError("credential key material is unavailable") from exc
    text = raw.strip()
    if not text:
        raise ValueError("credential key material is unavailable")
    return text


def _key() -> bytes:
    """懒加载 + 进程内缓存：派生一次 SHA-256 就够，不必每个请求重读文件。"""
    global _master_key
    if _master_key is None:
        _master_key = hashlib.sha256(_KEY_SALT + _load_secret().encode()).digest()
    return _master_key


def encrypt_secret(plaintext: str) -> str:
    """随机 nonce + 密文 + tag 打包成一段 urlsafe base64，便于塞进 VARCHAR2。"""
    if not isinstance(plaintext, str):
        raise TypeError("plaintext must be str")
    cipher = AES.new(_key(), AES.MODE_GCM, nonce=os.urandom(_NONCE_BYTES))
    ciphertext, tag = cipher.encrypt_and_digest(plaintext.encode("utf-8"))
    return base64.urlsafe_b64encode(cipher.nonce + ciphertext + tag).decode("ascii")


def decrypt_secret(token: str) -> str:
    """解密失败统一抛同一个 ``ValueError``。

    异常信息里不带密文也不带明文：这段字符串会一路飘到日志里，密文虽然解不开，
    但把它写进日志等于给攻击者送一份离线爆破素材。
    """
    key = _key()  # 主密钥缺失是运维问题，错误信息要能被区分出来
    try:
        raw = base64.urlsafe_b64decode(token.encode("ascii"))
        if len(raw) <= _NONCE_BYTES + _TAG_BYTES:
            raise ValueError("token too short")
        nonce = raw[:_NONCE_BYTES]
        ciphertext = raw[_NONCE_BYTES:-_TAG_BYTES]
        tag = raw[-_TAG_BYTES:]
        cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
        return cipher.decrypt_and_verify(ciphertext, tag).decode("utf-8")
    except Exception:
        raise ValueError("credential cannot be decrypted") from None


def key_hint(plaintext: str) -> str:
    """给管理界面认 key 用：只露末四位。

    短 key 连末四位也不给：8 位 key 露出 4 位等于泄了一半熵，门槛定在 12 位
    （主流上游 key 都远长于此），低于它一律 ``****``。
    """
    text = (plaintext or "").strip()
    if len(text) < _MIN_HINTABLE:
        return "****"
    return text[-_HINT_VISIBLE:]


__all__ = ["encrypt_secret", "decrypt_secret", "key_hint"]
