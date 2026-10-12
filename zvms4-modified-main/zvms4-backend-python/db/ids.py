"""ObjectId <-> lowercase hex helpers for the repository layer.

Repository rows expose ``_id`` as a **lowercase hex** string. These helpers are
the single conversion boundary used by the adapters:

* :func:`new_oid` - generate a fresh 24-char lowercase hex id.
* :func:`normalize_oid` - accept mixed-case hex (or a ``bson.ObjectId``) and
  return its lowercase hex form, raising :class:`ValueError` for anything that
  is not a valid ObjectId.

Semantics match ``utils.validate_object_id`` (which raises ``HTTPException``);
this module raises plain ``ValueError`` so it stays framework-agnostic and does
**no** database I/O.
"""

from __future__ import annotations

from typing import Any, Union

from bson import ObjectId

OID_HEX_LENGTH = 24

__all__ = ["new_oid", "normalize_oid", "OID_HEX_LENGTH"]


def new_oid() -> str:
    """Return a new 24-character lowercase-hex ObjectId string."""
    return str(ObjectId())


def normalize_oid(value: Union[str, ObjectId, Any]) -> str:
    """Return ``value`` as a lowercase 24-char hex string.

    Accepts a :class:`bson.ObjectId` or any string that ``ObjectId`` accepts
    (mixed case is fine). Raises :class:`ValueError` if it is not a valid
    ObjectId (this includes the ``activities.approver`` sentinels ``""`` and
    ``"authority"``, which are intentionally NOT valid ids).
    """
    if isinstance(value, ObjectId):
        return str(value)
    if not isinstance(value, str):
        raise ValueError(f"Invalid ObjectId: {value!r}")
    try:
        oid = ObjectId(value)
    except Exception as exc:  # bson raises InvalidId; normalize to ValueError
        raise ValueError(f"Invalid ObjectId: {value!r}") from exc
    return str(oid)
