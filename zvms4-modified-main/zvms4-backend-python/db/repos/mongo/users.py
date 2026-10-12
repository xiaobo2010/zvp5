"""MongoDB implementation of :class:`db.repos.protocols.UsersRepo`.

A mechanical lift of the live ``motor`` queries against ``zvms_new.users``.
The row shapes are translated to exactly match ``db/repos/oracle/users.py``
(``_page_row`` / ``_user_doc``): ``_id`` becomes a lowercase hex string, the
aggregation's ``未知班级`` / ``""`` sentinels are preserved, and optional fields
are omitted when absent so ``"class_id" in user`` keeps its meaning.

Sources lifted verbatim
-----------------------
* ``util/cert.py``            — ``validate_by_cert`` / ``checkpwd`` lookups
                                (``_id`` / ``id`` → full doc).
* ``util/user.py``            — ``get_user_display_names`` (``get_names``, the
                                ``zvms_new`` side only; legacy fallback stays
                                with the caller).
* ``util/permission/user.py`` — ``get_zvms_new_user`` (``_id`` lookup).
* ``routers/users_v2_router.py`` — the ``list_page`` aggregation (role/query
                                match, ``$sort`` id, ``$skip``/``$limit``,
                                ``$lookup`` to groups).
* ``routers/users_router.py`` — ``create``/``read_users`` document shapes.
* ``routers/users_v3_router.py`` — ``create`` / ``set_roles`` / ``set_password``
                                writes.

No DB I/O happens at import time.
"""

from __future__ import annotations

import re
from typing import Any

from bson import ObjectId

from database import db
from db.ids import normalize_oid

Row = dict[str, Any]

# The aggregation sets this literal when the group lookup misses (users_v2).
_UNKNOWN_CLASS = "未知班级"


def _hex(value: Any) -> str:
    """Return ``value`` as its lowercase hex string form."""
    return str(value)


def _user_doc(doc: Row) -> Row:
    """Full document for ``get_by_oid`` / ``find_by_business_id``.

    ``password`` is always present (even when ``None``) because callers index it
    directly (``user["password"]``). Optional fields are only added when
    non-NULL so ``"eligibility" in user`` keeps its Mongo meaning.
    """
    out: Row = {
        "_id": _hex(doc["_id"]),
        "id": doc["id"],
        "name": doc["name"],
        "password": doc.get("password"),
        "roles": doc.get("roles") if doc.get("roles") is not None else [],
    }
    if doc.get("class_id") is not None:
        out["class_id"] = _hex(doc["class_id"])
    if doc.get("session_id") is not None:
        out["session_id"] = doc["session_id"]
    if doc.get("sex") is not None:
        out["sex"] = doc["sex"]
    if doc.get("eligibility") is not None:
        out["eligibility"] = doc["eligibility"]
    if doc.get("past") is not None:
        out["past"] = doc["past"]
    return out


def _page_row(doc: Row) -> Row:
    """Aggregation-shaped row for ``list_page`` (matches the users_v2 pipeline)."""
    roles = doc.get("roles")
    class_id = doc.get("class_id")
    class_name = doc.get("class_name")
    return {
        "_id": _hex(doc["_id"]),
        "id": doc["id"],
        "name": doc["name"],
        "roles": roles if roles is not None else [],
        "class_id": class_id if class_id is not None else "",
        "class_name": class_name if class_name is not None else _UNKNOWN_CLASS,
    }


class MongoUsersRepo:
    """``UsersRepo`` over MongoDB (``zvms_new.users``)."""

    async def get_by_oid(self, oid: str) -> Row | None:
        doc = await db.zvms_new.users.find_one(
            {"_id": ObjectId(normalize_oid(oid))}
        )
        if doc is None:
            return None
        return _user_doc(doc)

    async def find_by_business_id(self, business_id: str) -> Row | None:
        doc = await db.zvms_new.users.find_one({"id": business_id})
        if doc is None:
            return None
        return _user_doc(doc)

    async def list_page(
        self, query: str, role: str, page: int, size: int
    ) -> tuple[list[Row], int]:
        match: Row = {}
        if role:
            match["roles"] = role
        if query:
            escaped = re.escape(query)
            match["$or"] = [
                {"name": {"$regex": escaped, "$options": "i"}},
                {"id": {"$regex": escaped, "$options": "i"}},
            ]

        pipeline = [
            {"$match": match},
            {"$sort": {"id": 1}},
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
            {
                "$set": {
                    "class_obj": {"$ifNull": ["$class_obj", {"name": _UNKNOWN_CLASS}]}
                }
            },
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
                            "onNull": "",
                            "onError": "",
                        }
                    },
                    "class_name": "$class_obj.name",
                }
            },
        ]

        total = await db.zvms_new.users.count_documents(match)
        docs = await db.zvms_new.users.aggregate(pipeline).to_list(None)
        return [_page_row(doc) for doc in docs], int(total)

    async def create(
        self,
        business_id: str,
        name: str,
        roles: list[str],
        password_hash: str | None,
        class_oid: str | None,
    ) -> str:
        oid = ObjectId()
        document: Row = {
            "_id": oid,
            "id": business_id,
            "name": name,
            "password": password_hash,
            "roles": list(roles),
        }
        if class_oid is not None:
            document["class_id"] = ObjectId(normalize_oid(class_oid))
        await db.zvms_new.users.insert_one(document)
        return str(oid)

    async def set_password(self, oid: str, password_hash: str | None) -> None:
        await db.zvms_new.users.update_one(
            {"_id": ObjectId(normalize_oid(oid))},
            {"$set": {"password": password_hash}},
        )

    async def set_roles(self, oid: str, roles: list[str]) -> None:
        await db.zvms_new.users.update_one(
            {"_id": ObjectId(normalize_oid(oid))},
            {"$set": {"roles": list(roles)}},
        )

    async def set_class(self, user_oid: str, class_oid: str | None) -> None:
        class_value = (
            ObjectId(normalize_oid(class_oid)) if class_oid is not None else None
        )
        await db.zvms_new.users.update_one(
            {"_id": ObjectId(normalize_oid(user_oid))},
            {"$set": {"class_id": class_value}},
        )

    async def detach_class(self, class_oid: str) -> int:
        result = await db.zvms_new.users.update_many(
            {"class_id": ObjectId(normalize_oid(class_oid))},
            {"$set": {"class_id": None}},
        )
        return int(result.modified_count)

    async def get_names(self, oids: list[str]) -> dict[str, str]:
        unique = list(dict.fromkeys(normalize_oid(oid) for oid in oids))
        if not unique:
            return {}
        docs = await db.zvms_new.users.find(
            {"_id": {"$in": [ObjectId(oid) for oid in unique]}}, {"name": 1}
        ).to_list(None)
        return {str(doc["_id"]): doc.get("name") for doc in docs}


__all__ = ["MongoUsersRepo"]
