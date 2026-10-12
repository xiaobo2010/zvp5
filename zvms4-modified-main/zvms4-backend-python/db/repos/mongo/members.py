"""MongoDB implementation of :class:`db.repos.protocols.MembersRepo`.

A mechanical lift of the live ``motor`` queries against
``zvms_new.activity_members``:

* ``routers/activities_v3_router.py`` — ``get_activity_members_v3``: the
  ``$match`` on ``activity``, the ``$convert`` of ``member`` to an ObjectId,
  the ``$lookup`` into ``users`` backing the ``query`` filter, the ``$sort`` on
  ``_id`` and the ``$skip``/``$limit`` pagination (count pipeline = the first
  five stages + ``$count``).
* ``routers/users_v2_router.py`` — the per-user memberships pipeline: the
  ``$toObjectId`` cast on ``activity``, the ``$lookup`` into ``activities``,
  the ``$sort`` and the ``$skip``/``$limit`` pagination.
* ``routers/activities_v2_router.py`` / ``util/calculate.py`` /
  ``util/statement.py`` — the ``{"member": ...}`` equality, the optional
  ``status`` filter and the ``{"activity": ...}`` equality.
* ``routers/activity_statistics_router.py`` / ``routers/groups_v2_router.py``
  — the ``find({"activity": ...})`` / ``find({"member": ...})`` reads.

Row shape: plain ``dict`` rows
keyed by the **original Mongo field names**, ``_id`` normalised to a lowercase
hex string::

    {"_id": <hex>, "activity": <hex>, "member": <hex>,
     "status": ..., "mode": ..., "duration": ...}

``activity``/``member`` hold the raw 24-char hex ids the memberships were stored
with. No DB I/O happens at import time; the motor handle is read from
:mod:`database` on each call.
"""

from __future__ import annotations

import re
from asyncio import gather
from typing import Any

from bson import ObjectId

from database import db
from db.ids import normalize_oid

Row = dict[str, Any]

_COLLECTION = "activity_members"

# The shared ``$project`` tail: exactly the MembersRepo row shape (Mongo key
# names, ``_id`` as a lowercase hex string). Deliberately closes over the
# ``activities_v3`` projection minus the aggregation-only ``member_name``.
_PROJECTION = {
    "_id": {"$toString": "$_id"},
    "activity": "$activity",
    "member": "$member",
    "status": "$status",
    "mode": "$mode",
    "duration": "$duration",
}


def _activity_members_pipeline(
    activity_oid: str, query: str, page: int, size: int
) -> list[Row]:
    """``activities_v3`` members aggregation, cf. ``get_activity_members_v3``.

    The first five stages are byte-for-byte the live pipeline (the count query
    is built from ``pipeline[:5]``); only the ``$project`` is reduced to the
    ``MembersRepo`` row shape.
    """
    escaped = re.escape(query)
    name_match = (
        {
            "$and": [
                {"member_obj": {"$ne": None}},
                {"member_obj.name": {"$regex": escaped, "$options": "i"}},
            ]
        }
        if query
        else {}
    )
    return [
        {"$match": {"activity": activity_oid}},
        {
            "$addFields": {
                "member_id": {
                    "$convert": {
                        "input": "$member",
                        "to": "objectId",
                        "onError": None,
                        "onNull": None,
                    }
                }
            }
        },
        {
            "$lookup": {
                "from": "users",
                "localField": "member_id",
                "foreignField": "_id",
                "as": "member_obj",
            }
        },
        {"$unwind": {"path": "$member_obj", "preserveNullAndEmptyArrays": True}},
        {"$match": name_match},
        {"$sort": {"_id": 1}},
        {"$skip": (page - 1) * size},
        {"$limit": size},
        {"$project": dict(_PROJECTION)},
    ]


def _user_members_pipeline(
    user_oid: str, status: str | None, page: int, size: int
) -> list[Row]:
    """``users_v2`` per-user memberships pipeline (``$toObjectId`` + join).

    Mirrors the live pipeline: filter by ``member`` (+ optional ``status``),
    cast ``activity`` to an ObjectId, join ``activities`` (non-preserving
    ``$unwind``, so orphan memberships are dropped like the original), sort on
    ``_id`` and paginate.
    """
    match: Row = {"member": user_oid}
    if status:
        match["status"] = status
    return [
        {"$match": match},
        {"$addFields": {"activity_id_obj": {"$toObjectId": "$activity"}}},
        {
            "$lookup": {
                "from": "activities",
                "localField": "activity_id_obj",
                "foreignField": "_id",
                "as": "activity_obj",
            }
        },
        {"$unwind": "$activity_obj"},
        {"$sort": {"_id": 1}},
        {"$skip": (page - 1) * size},
        {"$limit": size},
        {"$project": dict(_PROJECTION)},
    ]


class MongoMembersRepo:
    """``MembersRepo`` over MongoDB (``zvms_new.activity_members``)."""

    async def list_for_activity(
        self, activity_oid: str, query: str, page: int, size: int
    ) -> tuple[list[Row], int]:
        activity = normalize_oid(activity_oid)
        coll = db.zvms_new.get_collection(_COLLECTION)
        page_pipeline = _activity_members_pipeline(activity, query, page, size)
        count_pipeline = page_pipeline[:5] + [{"$count": "total"}]
        total_docs, docs = await gather(
            coll.aggregate(count_pipeline).to_list(length=1),
            coll.aggregate(page_pipeline).to_list(None),
        )
        total = total_docs[0]["total"] if total_docs else 0
        return list(docs), int(total)

    async def list_for_user(
        self, user_oid: str, status: str | None, page: int, size: int
    ) -> tuple[list[Row], int]:
        member = normalize_oid(user_oid)
        coll = db.zvms_new.get_collection(_COLLECTION)
        match: Row = {"member": member}
        if status:
            match["status"] = status
        page_pipeline = _user_members_pipeline(member, status, page, size)
        total, docs = await gather(
            coll.count_documents(match),
            coll.aggregate(page_pipeline).to_list(None),
        )
        return list(docs), int(total)

    async def add(
        self,
        activity_oid: str,
        member_oid: str,
        mode: str,
        duration: float,
        status: str,
    ) -> str:
        oid = ObjectId()
        document: Row = {
            "_id": oid,
            "activity": normalize_oid(activity_oid),
            "member": normalize_oid(member_oid),
            "status": status,
            "mode": mode,
            "duration": duration,
        }
        await db.zvms_new.get_collection(_COLLECTION).insert_one(document)
        return str(oid)

    async def remove(self, oid: str) -> None:
        await db.zvms_new.get_collection(_COLLECTION).delete_one(
            {"_id": ObjectId(normalize_oid(oid))}
        )

    async def set_status(self, oid: str, status: str) -> None:
        await db.zvms_new.get_collection(_COLLECTION).update_one(
            {"_id": ObjectId(normalize_oid(oid))},
            {"$set": {"status": status}},
        )

    async def set_mode(self, oid: str, mode: str) -> None:
        await db.zvms_new.get_collection(_COLLECTION).update_one(
            {"_id": ObjectId(normalize_oid(oid))},
            {"$set": {"mode": mode}},
        )


__all__ = ["MongoMembersRepo"]
