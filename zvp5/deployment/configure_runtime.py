"""Create a production-shaped settings.py for the ADB2 deployment.

The Oracle password is read from ZVP5_ORACLE_PASSWORD (legacy ZVMS_ORACLE_PASSWORD
still accepted) so it is not stored in this repository or placed in a shell history file.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path


def replace_required(source: str, old: str, new: str) -> str:
    if old not in source:
        raise SystemExit(f"Expected settings template line not found: {old}")
    return source.replace(old, new, 1)


def main() -> None:
    base = Path(__file__).resolve().parent
    root = base if (base / "settings.example.py").exists() else base.parent / "zvp5-backend-python"
    template = root / "settings.example.py"
    target = root / "settings.py"
    password = os.environ.get("ZVP5_ORACLE_PASSWORD") or os.environ.get(
        "ZVMS_ORACLE_PASSWORD", ""
    )
    if not password:
        raise SystemExit("ZVP5_ORACLE_PASSWORD (or legacy ZVMS_ORACLE_PASSWORD) is required")
    source = target.read_text(encoding="utf-8") if target.exists() else template.read_text(encoding="utf-8")
    source = source.replace('ORACLE_ACTIVE = "prod"', 'ORACLE_ACTIVE = "test"', 1)
    replacements = {
        'SECRET_KEY = ""': f"SECRET_KEY = {secrets.token_urlsafe(64)!r}",
        'ORACLE_USER = ""': 'ORACLE_USER = "ADMIN"',
        'ORACLE_PASSWORD = ""': f"ORACLE_PASSWORD = {password!r}",
        'ORACLE_TEST_DSN = ""': 'ORACLE_TEST_DSN = "zvp5adb2_tp"',
        'ORACLE_TEST_WALLET_DIR = ""': (
            'ORACLE_TEST_WALLET_DIR = "/www/zvp5/adb-wallet-zvp5adb2"'
        ),
        'ORACLE_TEST_WALLET_PASSWORD = ""': (
            f"ORACLE_TEST_WALLET_PASSWORD = {password!r}"
        ),
    }
    for old, new in replacements.items():
        source = replace_required(source, old, new)

    temporary = target.with_suffix(".py.tmp")
    temporary.write_text(source, encoding="utf-8", newline="\n")
    temporary.chmod(0o600)
    temporary.replace(target)
    target.chmod(0o600)
    print(f"Wrote {target} for ADB test instance (no DDL executed)")


if __name__ == "__main__":
    main()
