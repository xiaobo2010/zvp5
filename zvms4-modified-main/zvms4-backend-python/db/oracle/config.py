"""Oracle connection configuration.

Reads Oracle settings from the existing ``settings`` module (see
``settings.example.py``) and resolves them for a single active instance.

Two logical instances are supported:

* ``prod`` - uses ``ORACLE_DSN`` / ``ORACLE_WALLET_DIR``
* ``test`` - uses ``ORACLE_TEST_DSN`` / ``ORACLE_TEST_WALLET_DIR``

Wallet passwords (``ORACLE_WALLET_PASSWORD`` / ``ORACLE_TEST_WALLET_PASSWORD``)
are the OCI *wallet* passwords that decrypt ``ewallet.pem``; they are distinct
from ``ORACLE_PASSWORD`` (the DB user password).

The active instance is selected with ``ORACLE_ACTIVE`` ("prod" or "test",
default "prod"). Nothing in this module performs any database I/O; it only
reads Python attributes and builds strings.
"""

from __future__ import annotations

from typing import NamedTuple, Optional
from urllib.parse import quote_plus

import settings

VALID_ACTIVE = ("prod", "test")


class OracleConfig(NamedTuple):
    """Fully resolved configuration for one Oracle instance."""

    enabled: bool
    active: str
    user: str
    password: str
    dsn: str
    wallet_dir: str
    wallet_password: str


def _get(name: str, default):
    """Read a settings attribute, falling back to ``default`` when absent."""
    return getattr(settings, name, default)


def get_active() -> str:
    """Return the normalized active instance name ("prod" or "test")."""
    active = str(_get("ORACLE_ACTIVE", "prod") or "prod").strip().lower()
    return active if active in VALID_ACTIVE else "prod"


def load_config(active: Optional[str] = None) -> OracleConfig:
    """Resolve a :class:`OracleConfig` for the active (or given) instance."""
    instance = (active or get_active())
    instance = str(instance).strip().lower()
    if instance not in VALID_ACTIVE:
        instance = "prod"

    if instance == "test":
        dsn = _get("ORACLE_TEST_DSN", "")
        wallet_dir = _get("ORACLE_TEST_WALLET_DIR", "")
        wallet_password = _get("ORACLE_TEST_WALLET_PASSWORD", "")
    else:
        dsn = _get("ORACLE_DSN", "")
        wallet_dir = _get("ORACLE_WALLET_DIR", "")
        wallet_password = _get("ORACLE_WALLET_PASSWORD", "")

    return OracleConfig(
        enabled=bool(_get("ORACLE_ENABLED", False)),
        active=instance,
        user=str(_get("ORACLE_USER", "") or ""),
        password=str(_get("ORACLE_PASSWORD", "") or ""),
        dsn=str(dsn or ""),
        wallet_dir=str(wallet_dir or ""),
        wallet_password=str(wallet_password or ""),
    )


def get_config() -> OracleConfig:
    """Alias for :func:`load_config` using the active instance."""
    return load_config()


def build_async_url(config: Optional[OracleConfig] = None) -> str:
    """Build the SQLAlchemy async URL for python-oracledb.

    Format: ``oracle+oracledb_async://USER:PASSWORD@DSN``. The user and
    password are URL-encoded so special characters are safe to embed.
    """
    config = config or load_config()
    user = quote_plus(config.user)
    password = quote_plus(config.password)
    return f"oracle+oracledb_async://{user}:{password}@{config.dsn}"
