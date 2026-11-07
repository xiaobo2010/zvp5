"""平台主密钥的唯一读取点。

同一把 ``aes_key.txt`` 被四处使用，但各自硬编码路径：

* ``util/cert.py`` —— JWT / TOTP 的派生源（**原始文本**，含末尾换行）；
* ``util/attachment.py`` —— 附件签名 URL 的 HMAC 源（**原始字节**）；
* ``services/ai/crypto.py`` —— AI 凭据落库加密（``.strip()`` 后使用）；
* ``services/file_ops.py`` —— 文件管理器黑名单（只按文件名挡）。

2026-10-30 的事故根因是密钥文件放在部署树里：rsync/重建把 ``aes_key.txt`` 换掉
后，AI 密文全部解不开。把读取点收敛到这里之后，生产可以改走 systemd
``LoadCredential``（推荐），密钥不再进入 ``/www/zvms4/backend``。

解析顺序（先命中先返回，**不做静默回退**）：

1. ``ZVMS_PLATFORM_KEY_FILE``：显式路径，给非 systemd 部署与本地调试；
2. ``$CREDENTIALS_DIRECTORY/zvms_ai_key``：systemd ``LoadCredential`` 注入；
3. 后端根 ``aes_key.txt``：兼容回退，迁移完成后可删除。

注意：``read_text()`` 原样返回文件内容，**不 strip**。``util/cert.py`` 与
``util/attachment.py`` 用的是原始文本/字节，统一 strip 会改变既有 TOTP 与附件
签名；需要去空白的调用方（``crypto``）自己处理。
"""

from __future__ import annotations

import os
from pathlib import Path

#: systemd ``LoadCredential=zvms_ai_key:<source>`` 里的 id。
CREDENTIAL_ID = "zvms_ai_key"
#: 非 systemd 部署的显式路径。
_PATH_ENV = "ZVMS_PLATFORM_KEY_FILE"
#: 旧位置：与 backend 根同层（部署树内）。
_LEGACY_FILE = Path(__file__).resolve().parents[1] / "aes_key.txt"


class KeyMaterialError(RuntimeError):
    """密钥缺失/为空/不可读；调用方必须让启动或请求失败，不能降级成空密钥。"""


def legacy_path() -> Path:
    return _LEGACY_FILE


def credential_path() -> Path | None:
    root = os.environ.get("CREDENTIALS_DIRECTORY")
    return Path(root) / CREDENTIAL_ID if root else None


def is_credential_path(path: str | Path) -> bool:
    """该路径是不是 systemd 注入的凭据副本。

    ``/run/credentials`` 是只读挂载、权限由 systemd 管理（0440 + ACL），不要对它
    做 chmod 自检——否则每次启动都会留下「权限过宽且无法收紧」的假告警。
    """
    credential = credential_path()
    if credential is None:
        return False
    try:
        return Path(path).resolve() == credential.resolve()
    except OSError:
        return False


def resolve_path() -> Path | None:
    """返回当前应读取的密钥路径；None 表示三个来源都不存在。

    ``CREDENTIALS_DIRECTORY`` 一旦存在就只认它（即使文件缺失）——这是 systemd
    部署的契约，静默退到旧文件会把「凭据没挂上」伪装成正常启动。
    """
    explicit = os.environ.get(_PATH_ENV)
    if explicit:
        return Path(explicit)
    credential = credential_path()
    if credential is not None:
        return credential
    if _LEGACY_FILE.exists():
        return _LEGACY_FILE
    return None


def read_text() -> str:
    """读原始文本（含末尾换行）；缺失/为空抛 :class:`KeyMaterialError`。"""
    path = resolve_path()
    if path is None:
        raise KeyMaterialError(
            "platform key is unavailable: configure systemd LoadCredential "
            f"({CREDENTIAL_ID}) or {_PATH_ENV}"
        )
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise KeyMaterialError(f"platform key is unreadable: {path}") from exc
    if not text.strip():
        raise KeyMaterialError(f"platform key is empty: {path}")
    return text


__all__ = [
    "CREDENTIAL_ID",
    "KeyMaterialError",
    "credential_path",
    "is_credential_path",
    "legacy_path",
    "read_text",
    "resolve_path",
]
