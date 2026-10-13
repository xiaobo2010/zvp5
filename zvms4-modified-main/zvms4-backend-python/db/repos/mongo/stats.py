"""MongoDB implementation of :class:`db.repos.protocols.StatsCacheRepo`.

A mechanical lift of the derived-cache reads/writes against
``zvms_new.indicators`` (global, single row) and
``zvms_new.group_indicators`` (one row per group):

* ``tasks/compute_time.py`` — ``compute_indicators`` / ``compute_group_indicators``
  write via one **whole-row replace** (``delete_many`` then ``insert_one``).
* ``routers/users_v2_router.py`` — ``get_user_time_statistics_v2`` reads the
  single global row (``indicators.find({})``) and one group row
  (``group_indicators.find_one({"group": <hex>})``).
* ``routers/activity_statistics_router.py`` — the per-grade percentiles /
  indicators reads against ``indicators``.

Rows are the Mongo document shape (``_id`` normalised to a lowercase hex
string), matching ``db/repos/oracle/stats.py`` field for field. No DB I/O
happens at import time; the motor handle is read from :mod:`database` on each
call.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bson import ObjectId

from database import db

Row = dict[str, Any]

_INDICATORS = "indicators"
_GROUP_INDICATORS = "group_indicators"


def _indicators_doc(doc: Row) -> Row:
    """Canonical global ``indicators`` row (matches ``oracle/stats``)."""
    return {
        "_id": str(doc["_id"]),
        "percentiles": doc.get("percentiles"),
        "indicators": doc.get("indicators"),
        "updated_at": doc.get("updated_at"),
    }


def _group_indicators_doc(doc: Row) -> Row:
    """Canonical ``group_indicators`` row (matches ``oracle/stats``)."""
    return {
        "_id": str(doc["_id"]),
        "group": doc.get("group"),
        "percentiles": doc.get("percentiles"),
        "indicators": doc.get("indicators"),
        "updated_at": doc.get("updated_at"),
    }


class MongoStatsCacheRepo:
    """``StatsCacheRepo`` over MongoDB (``indicators``/``group_indicators``)."""

    async def get_indicators(self) -> Row | None:
        doc = await db.zvms_new.get_collection(_INDICATORS).find_one({})
        if doc is None:
            return None
        return _indicators_doc(doc)

    async def upsert_indicators(self, percentiles: Row, indicators: Row) -> None:
        collection = db.zvms_new.get_collection(_INDICATORS)
        await collection.delete_many({})
        await collection.insert_one(
            {
                "_id": ObjectId(),
                "percentiles": percentiles,
                "indicators": indicators,
                "updated_at": datetime.now(),
            }
        )

    async def list_group_indicators(self) -> list[Row]:
        docs = (
            await db.zvms_new.get_collection(_GROUP_INDICATORS)
            .find({})
            .sort([("group", 1)])
            .to_list(None)
        )
        return [_group_indicators_doc(doc) for doc in docs]

    async def upsert_group_indicators(
        self, group_oid: str, percentiles: Row, indicators: Row
    ) -> None:
        group = str(group_oid)
        collection = db.zvms_new.get_collection(_GROUP_INDICATORS)
        await collection.delete_many({"group": group})
        await collection.insert_one(
            {
                "_id": ObjectId(),
                "group": group,
                "percentiles": percentiles,
                "indicators": indicators,
                "updated_at": datetime.now(),
            }
        )


__all__ = ["MongoStatsCacheRepo"]
