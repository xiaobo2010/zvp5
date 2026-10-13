"""Mongo-free id helpers for the Oracle ADB layer.

Mongo ``_id`` values are stored in Oracle as ``VARCHAR2(24)`` **lowercase hex**
(spec §1). These helpers are the single id-mapping boundary used by the
repository layer and the HTTP layer:

* :func:`new_oid` - generate a fresh 24-char lowercase hex id.
* :func:`normalize_oid` / :func:`validate_oid` - accept a 24-hex string (mixed
  case is fine) and return its lowercase form, raising :class:`ValueError`
  for anything else.
* :func:`is_valid_oid` - non-raising predicate for the same shape.

Semantics match ``utils.validate_object_id`` (which raises ``HTTPException``);
this module raises plain ``ValueError`` so it stays framework-agnostic and does
**no** database I/O. It has no third-party dependencies.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any, Union

OID_HEX_LENGTH = 24

_OID_HEX_RE = re.compile(r"[0-9a-fA-F]{24}")

__all__ = [
    "new_oid",
    "normalize_oid",
    "validate_oid",
    "is_valid_oid",
    "OID_HEX_LENGTH",
]


def new_oid() -> str:
    """Return a fresh 24-character lowercase-hex id, time-ordered.

    The first 4 bytes are the current Unix time (seconds, big-endian) so that
    lexicographic ordering matches creation order — preserving the ordering the
    Oracle repos rely on (``ORDER BY oid DESC`` = newest first). The remaining
    8 bytes are random.
    """
    timestamp = int(time.time()).to_bytes(4, "big", signed=False)
    return (timestamp + os.urandom(8)).hex()


def is_valid_oid(value: Any) -> bool:
    """Return ``True`` when ``value`` is a 24-character hex string."""
    return isinstance(value, str) and _OID_HEX_RE.fullmatch(value) is not None


def normalize_oid(value: Union[str, Any]) -> str:
    """Return ``value`` as a lowercase 24-char hex string.

    Accepts any 24-hex string (mixed case is fine). Raises :class:`ValueError`
    for anything else (this includes the ``activities.approver`` sentinels
    ``""`` and ``"authority"``, which are intentionally NOT valid ids).
    """
    if not is_valid_oid(value):
        raise ValueError(f"Invalid ObjectId: {value!r}")
    return value.lower()


def validate_oid(value: Union[str, Any]) -> str:
    """Return the lowercased 24-hex id, raising :class:`ValueError` otherwise."""
    return normalize_oid(value)
