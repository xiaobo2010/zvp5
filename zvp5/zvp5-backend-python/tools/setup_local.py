"""Create missing local development configuration without touching existing keys.

Run from the backend directory with its virtualenv Python. No DB connection is
attempted. Generated secrets and settings are ignored by Git.

``aes_key.txt`` / ``rsa_*.pem`` 是**数据的一部分**，不是可以随手重建的本地缓存：
库里所有 AI 凭据（``AI_CREDENTIALS.api_key_cipher`` / ``AI_OCR_ENGINES.secret_cipher``）
都是用 ``aes_key.txt`` 解开的，换一把 key 等于让既有密文全部变成
``AI_CREDENTIAL_UNREADABLE``；RSA 私钥则决定已签发 token 与 TOTP 派生。生产机缺
密钥时应从备份/上一台机器迁移，绝不能让本脚本「顺手生成一把新的」——2026-10-30
的事故就是这么发生的（见 ``docs/ISSUE_2026-11-03_AI_CREDENTIAL_UNREADABLE.md``）。
"""

from __future__ import annotations

from pathlib import Path
import os
import re
import secrets

from Crypto.PublicKey import RSA

#: 生产库里指向真实数据的标记；``settings.py`` 是部署产物，读不到就按开发机处理。
_PROD_MARKER = re.compile(
    r"""^\s*ORACLE_ACTIVE\s*=\s*(['"])prod\1""", re.MULTILINE
)
#: 显式轮换/首次搭建生产机的逃生门：设了它才允许生成新密钥。
_ALLOW_BOOTSTRAP_ENV = "ZVP5_ALLOW_KEY_BOOTSTRAP"


def _would_create_keys(root: Path) -> bool:
    """本脚本是否真的会写出**新的**密钥材料。

    只有 ``aes_key.txt`` 缺失、或 RSA 私钥缺失（意味着要重新生成一对）才算。
    私钥在、只缺公钥时脚本只是从私钥导出公钥，不产生新秘密，不该被生产护栏挡住。
    """
    return not (root / "aes_key.txt").exists() or not (
        root / "rsa_private_key.pem"
    ).exists()


def _guard_production(root: Path) -> None:
    """生产机缺密钥时拒绝「就地生成」，把问题留在部署阶段而不是数据层。"""
    if os.environ.get(_ALLOW_BOOTSTRAP_ENV) == "1":
        return
    if not _would_create_keys(root):
        return
    try:
        settings_text = (root / "settings.py").read_text(
            encoding="utf-8", errors="ignore"
        )
    except OSError:
        return
    if not _PROD_MARKER.search(settings_text):
        return
    raise SystemExit(
        "Refusing to generate new keys on a production host "
        "(settings.py has ORACLE_ACTIVE = \"prod\").\n"
        "Missing key files would make every stored ciphertext unreadable "
        "(AI_CREDENTIAL_UNREADABLE) and invalidate TOTP/tokens.\n"
        "Restore aes_key.txt / rsa_*.pem from the deployment backup instead.\n"
        "If this is an intentional rotation, re-encrypt the stored secrets first, "
        f"then rerun with {_ALLOW_BOOTSTRAP_ENV}=1."
    )


def main(root: Path | None = None) -> None:
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1]
    _guard_production(root)
    private_path = root / "rsa_private_key.pem"
    public_path = root / "rsa_public_key.pem"
    if private_path.exists():
        key = RSA.import_key(private_path.read_bytes())
        if not public_path.exists():
            public_path.write_bytes(key.publickey().export_key())
    elif public_path.exists():
        raise SystemExit("Public key exists without private key; restore the matching private key.")
    else:
        key = RSA.generate(2048)
        with private_path.open("xb") as output:
            output.write(key.export_key())
        with public_path.open("xb") as output:
            output.write(key.publickey().export_key())

    secret_path = root / "aes_key.txt"
    if not secret_path.exists():
        with secret_path.open("x", encoding="utf-8") as output:
            output.write(secrets.token_hex(32))

    settings_path = root / "settings.py"
    if not settings_path.exists():
        source = (root / "settings.example.py").read_text(encoding="utf-8")
        source = source.replace('ORACLE_ACTIVE = "prod"', 'ORACLE_ACTIVE = "test"')
        with settings_path.open("x", encoding="utf-8") as output:
            output.write(source)
    print("Local keys/settings ready. Oracle credentials remain unconfigured; offline tests can run.")


if __name__ == "__main__":
    main()
