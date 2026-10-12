"""MongoDB implementation of :class:`db.repos.protocols.ActivitiesRepo`.

A mechanical lift of the live ``motor`` queries against ``zvms_new.activities``.
The row shape is the Mongo document shape itself, with ``_id`` normalised to a
lowercase hex string.

Sources lifted verbatim
-----------------------
* ``routers/activities_v3_router.py`` — ``list_page`` (``$match`` on type/status
  + case-insensitive ``re.escape``d keyword, ``$sort {_id: -1}``,
  ``$skip``/``$limit``) and ``create`` (``status="pending"``, ``approver=""``,
  ``createdAt``/``updatedAt`` = now).
* ``routers/activities_v2_router.py`` — ``get`` (``_id`` lookup), ``delete``,
  and the ``$set`` partial updates (``update``).
* ``routers/mcp_router.py`` / ``routers/activities_router.py`` — the paginated
  activity reads sharing the same filter/sort/skip/limit semantics.
* ``routers/activity_statistics_router.py`` — reads ``activity_members`` only,
  so it contributes nothing here.

No DB I/O happens at import time.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from bson import ObjectId

from database import db
from db.ids import normalize_oid

Row = dict[str, Any]

_COLLECTION = "activities"


def _activity_doc(doc: Row) -> Row:
    """Canonical activity row."""
    return {
        "_id": str(doc["_id"]),
        "name": doc.get("name"),
        "description": doc.get("description"),
        "type": doc.get("type"),
        "creator": doc.get("creator"),
        "status": doc.get("status"),
        "date": doc.get("date"),
        "createdAt": doc.get("createdAt"),
        "updatedAt": doc.get("updatedAt"),
        "appointee": doc.get("appointee"),
        "approver": doc.get("approver"),
        "place": doc.get("place"),
        "origin": doc.get("origin"),
    }


class MongoActivitiesRepo:
    """``ActivitiesRepo`` over MongoDB (``zvms_new.activities``)."""

    async def get(self, oid: str) -> Row | None:
        doc = await db.zvms_new.get_collection(_COLLECTION).find_one(
            {"_id": ObjectId(normalize_oid(oid))}
        )
        if doc is None:
            return None
        return _activity_doc(doc)

    async def list_page(
        self,
        query: str,
        atypes: list[str] | None,
        status: str | None,
        page: int,
        size: int,
    ) -> tuple[list[Row], int]:
        match: Row = {}
        if query:
            match["name"] = {"$regex": re.escape(query), "$options": "i"}
        if atypes:
            match["type"] = {"$in": list(atypes)}
        if status:
            match["status"] = status

        pipeline = [
            {"$match": match},
            {"$sort": {"_id": -1}},
            {"$skip": (page - 1) * size},
            {"$limit": size},
        ]

        collection = db.zvms_new.get_collection(_COLLECTION)
        total = await collection.count_documents(match)
        docs = await collection.aggregate(pipeline).to_list(None)
        return [_activity_doc(doc) for doc in docs], int(total)

    async def create(
        self,
        name: str,
        description: str,
        atype: str,
        creator_oid: str,
        appointee_oid: str,
        origin: str,
        activity_date: Any,
    ) -> str:
        oid = ObjectId()
        now = datetime.now()
        document: Row = {
            "_id": oid,
            "name": name,
            "description": description,
            "type": atype,
            "creator": normalize_oid(creator_oid),
            "status": "pending",
            "date": activity_date,
            "createdAt": now,
            "updatedAt": now,
            "appointee": normalize_oid(appointee_oid),
            "approver": "",
            "place": "",
            "origin": origin,
        }
        await db.zvms_new.get_collection(_COLLECTION).insert_one(document)
        return str(oid)

    async def update(self, oid: str, fields: Row) -> None:
        await db.zvms_new.get_collection(_COLLECTION).update_one(
            {"_id": ObjectId(normalize_oid(oid))},
            {"$set": dict(fields)},
        )

    async def delete(self, oid: str) -> None:
        await db.zvms_new.get_collection(_COLLECTION).delete_one(
            {"_id": ObjectId(normalize_oid(oid))}
        )


__all__ = ["MongoActivitiesRepo"]
