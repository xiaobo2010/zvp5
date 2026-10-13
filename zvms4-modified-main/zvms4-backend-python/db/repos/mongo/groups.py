"""MongoDB-backed GroupsRepo (``zvms_new`` database, class side).

Mechanical lift of the existing motor queries:

* class CRUD + members aggregation from ``routers/groups_v3_router.py``,
* the class-only listing block from ``routers/groups_v2_router.py``
  (the legacy ``type:"permission"`` paths are intentionally skipped).

Row shapes must match ``db/repos/oracle/groups.py`` exactly: ``_id`` is a
lowercase-hex string, never a ``bson.ObjectId``. No DB I/O happens at import
time; the motor handles are read from :mod:`database` on each call.
"""
from __future__ import annotations

import re
from typing import Any

from bson import ObjectId

from database import db
from db.ids import normalize_oid

Row = dict[str, Any]

_UNKNOWN_CLASS = "未知班级"


def _name_match(query: str) -> dict[str, Any]:
    """Case-insensitive literal ``contains`` on ``name`` (empty -> no filter)."""
    if not query:
        return {}
    return {"name": {"$regex": re.escape(query), "$options": "i"}}


def _member_match(group_oid: ObjectId, query: str) -> dict[str, Any]:
    """``groups_v3`` members ``$match``: class_id filter + optional name/id contains."""
    match: dict[str, Any] = {"class_id": group_oid}
    if query:
        escaped = re.escape(query)
        match["$or"] = [
            {"name": {"$regex": escaped, "$options": "i"}},
            {"id": {"$regex": escaped, "$options": "i"}},
        ]
    return match


def _class_row(doc: dict[str, Any]) -> Row:
    return {"_id": str(doc["_id"]), "name": doc.get("name")}


def _member_row(doc: dict[str, Any]) -> Row:
    """Members row shape (matches the ``groups_v3`` members aggregation)."""
    return {
        "_id": doc["_id"],
        "id": doc["id"],
        "name": doc["name"],
        "roles": doc.get("roles") or [],
        "class_id": doc.get("class_id") or "",
        "class_name": doc.get("class_name") or _UNKNOWN_CLASS,
        "password": doc.get("password"),
    }


def _members_pipeline(group_oid: ObjectId, query: str, page: int, size: int):
    """``groups_v3`` members aggregation: filter/sort/paginate then join class name."""
    return [
        {"$match": _member_match(group_oid, query)},
        {"$sort": {"id": 1, "_id": 1}},
        {"$skip": (page - 1) * size},
        {"$limit": size},
        {
            "$lookup": {
                "from": "groups",
                "localField": "class_id",
                "foreignField": "_id",
                "as": "class_obj",
            }
        },
        {"$unwind": {"path": "$class_obj", "preserveNullAndEmptyArrays": True}},
        {"$set": {"class_obj": {"$ifNull": ["$class_obj", {"name": _UNKNOWN_CLASS}]}}},
        {
            "$project": {
                "_id": {"$toString": "$_id"},
                "id": "$id",
                "name": "$name",
                "roles": "$roles",
                "class_id": {
                    "$convert": {
                        "input": "$class_id",
                        "to": "string",
                        "onError": "",
                        "onNull": "",
                    }
                },
                "class_name": "$class_obj.name",
                "password": "$password",
            }
        },
    ]


class MongoGroupsRepo:
    """``GroupsRepo`` over Mongo (``zvms_new.groups`` / ``zvms_new.users``)."""

    async def get(self, oid: str) -> Row | None:
        doc = await db.zvms_new.groups.find_one({"_id": ObjectId(normalize_oid(oid))})
        if doc is None:
            return None
        return _class_row(doc)

    async def list_classes(
        self, query: str, page: int, size: int
    ) -> tuple[list[Row], int]:
        match = _name_match(query)
        pipeline = [
            {"$match": match},
            {"$sort": {"_id": 1}},
            {"$skip": (page - 1) * size},
            {"$limit": size},
            {"$project": {"_id": {"$toString": "$_id"}, "name": "$name"}},
        ]
        total = await db.zvms_new.groups.count_documents(match)
        docs = await db.zvms_new.groups.aggregate(pipeline).to_list(None)
        rows = [
            {"_id": doc["_id"], "name": doc.get("name")} for doc in docs
        ]
        return rows, int(total)

    async def create(self, name: str) -> str:
        result = await db.zvms_new.groups.insert_one({"name": name})
        return str(result.inserted_id)

    async def rename(self, oid: str, name: str) -> None:
        await db.zvms_new.groups.update_one(
            {"_id": ObjectId(normalize_oid(oid))}, {"$set": {"name": name}}
        )

    async def delete(self, oid: str) -> None:
        await db.zvms_new.groups.delete_one({"_id": ObjectId(normalize_oid(oid))})

    async def members_page(
        self, group_oid: str, query: str, page: int, size: int
    ) -> tuple[list[Row], int]:
        goid = ObjectId(normalize_oid(group_oid))
        match = _member_match(goid, query)
        total = await db.zvms_new.users.count_documents(match)
        docs = (
            await db.zvms_new.users.aggregate(
                _members_pipeline(goid, query, page, size)
            ).to_list(None)
        )
        return [_member_row(doc) for doc in docs], int(total)

    async def list_all(self) -> list[Row]:
        docs = await db.zvms_new.groups.find({}).to_list(None)
        return [{"_id": str(doc["_id"]), "name": doc.get("name")} for doc in docs]


__all__ = ["MongoGroupsRepo"]
