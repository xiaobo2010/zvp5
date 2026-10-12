"""MongoDB implementation of :class:`db.repos.protocols.ChangelogRepo`.

Operates on ``zvms_new.changelog``: each document stores ``version`` (a release
tag such as ``v4.2.1-1``), ``date`` (an ISO ``YYYY-MM-DD`` string), ``items``
(the release notes, a list of Chinese sentences) and ``updated_at``. ``_id`` is
returned as a lowercase hex string so callers never see a raw ``ObjectId``.

No DB I/O happens at import time.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bson import ObjectId

from database import db
from db.ids import normalize_oid

Row = dict[str, Any]


def _entry_doc(doc: Row) -> Row:
    """Normalise a raw changelog document to the shared ``Row`` shape."""
    return {
        "_id": str(doc["_id"]),
        "version": doc.get("version", ""),
        "date": doc.get("date", ""),
        "items": doc.get("items") if doc.get("items") is not None else [],
        "updated_at": doc.get("updated_at"),
    }


class MongoChangelogRepo:
    """``ChangelogRepo`` over MongoDB (``zvms_new.changelog``)."""

    async def list_all(self) -> list[Row]:
        docs = (
            await db.zvms_new.changelog.find({})
            .sort([("date", -1), ("version", -1)])
            .to_list(None)
        )
        return [_entry_doc(doc) for doc in docs]

    async def get_by_oid(self, oid: str) -> Row | None:
        doc = await db.zvms_new.changelog.find_one(
            {"_id": ObjectId(normalize_oid(oid))}
        )
        if doc is None:
            return None
        return _entry_doc(doc)

    async def create(self, version: str, date: str, items: list[str]) -> str:
        oid = ObjectId()
        await db.zvms_new.changelog.insert_one(
            {
                "_id": oid,
                "version": version,
                "date": date,
                "items": list(items),
                "updated_at": datetime.now(),
            }
        )
        return str(oid)

    async def update(
        self, oid: str, version: str, date: str, items: list[str]
    ) -> int:
        result = await db.zvms_new.changelog.update_one(
            {"_id": ObjectId(normalize_oid(oid))},
            {
                "$set": {
                    "version": version,
                    "date": date,
                    "items": list(items),
                    "updated_at": datetime.now(),
                }
            },
        )
        return int(result.matched_count)

    async def delete(self, oid: str) -> int:
        result = await db.zvms_new.changelog.delete_one(
            {"_id": ObjectId(normalize_oid(oid))}
        )
        return int(result.deleted_count)


__all__ = ["MongoChangelogRepo"]
