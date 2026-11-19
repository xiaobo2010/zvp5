"""Small, dependency-free regression tests for the security hardening pass.

These deliberately avoid FastAPI/TestClient where possible so they can run on
the constrained dev box without the optional image/multipart dependencies.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import HTTPException, Response

from routers import users_router
from services import file_ops


@pytest.mark.parametrize(
    "name",
    [
        "aes_key.txt",
        "rsa_private_key.pem",
        "settings.py",
        "ewallet.pem",
        "cwallet.sso",
        "tnsnames.ora",
        "id_ed25519",
    ],
)
def test_secret_files_are_not_readable(tmp_path: Path, name: str) -> None:
    target = tmp_path / name
    target.write_text("secret", encoding="utf-8")

    with pytest.raises(file_ops.FileOpError) as exc:
        file_ops.resolve_path(str(target))
    assert exc.value.status_code == 403

    with pytest.raises(file_ops.FileOpError):
        file_ops.resolve_entry_path(str(target))


def test_wallet_directory_is_not_readable(tmp_path: Path) -> None:
    wallet = tmp_path / "adb-wallet"
    wallet.mkdir()
    with pytest.raises(file_ops.FileOpError) as exc:
        file_ops.resolve_path(str(wallet))
    assert exc.value.status_code == 403


def test_symlink_to_secret_is_not_readable(tmp_path: Path) -> None:
    secret = tmp_path / "aes_key.txt"
    secret.write_text("secret", encoding="utf-8")
    link = tmp_path / "harmless.txt"
    link.symlink_to(secret)

    with pytest.raises(file_ops.FileOpError) as exc:
        file_ops.resolve_path(str(link))
    assert exc.value.status_code == 403


def test_normal_file_is_still_readable(tmp_path: Path) -> None:
    target = tmp_path / "notes.txt"
    target.write_text("hello", encoding="utf-8")
    assert file_ops.resolve_path(str(target)) == str(target)


class _NoopLog:
    def with_text(self, *args, **kwargs):
        return self

    async def insert_log(self):
        return None


def test_create_user_cannot_grant_admin_directly() -> None:
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            users_router.create_user_v3(
                users_router.NewUserRequest(
                    id="20269999",
                    name="攻击者",
                    roles=["admin"],
                ),
                response=Response(),
                user={"id": "000000000000000000000001", "per": ["admin"]},
                log=_NoopLog(),
            )
        )
    assert exc.value.status_code == 403
