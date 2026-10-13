"""MongoDB implementation of :class:`db.repos.protocols.TimeRepo`.

A mechanical lift of the live ``motor`` queries against ``zvms_new.time`` /
``zvms_new.time_academic_year`` / ``zvms_new.time_with_origin``:

* ``util/calculate.py`` — ``calculate_user_time`` looks a user's totals up via
  ``{"user": <hex>}`` and, on a cache write, performs one **whole-row replace**
  (``delete_many({"user": ...})`` then ``insert_one``).
* ``util/calculate.py`` — ``calculate_user_time_with_origin`` does the same
  whole-row replace against ``time_with_origin`` with a ``data`` payload.
* ``tasks/compute_time.py`` — ``compute_ay_time`` writes one
  ``time_academic_year`` row per user (``{"user": <hex>}``).

Rows are the Mongo document shape (``_id`` normalised to a lowercase hex
string), matching ``db/repos/oracle/times.py`` field for field, so the two
backends are drop-in compatible. No DB I/O happens at import time; the motor
handle is read from :mod:`database` on each call.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from bson import ObjectId

from database import db
from db.ids import normalize_oid

Row = dict[str, Any]

_TIME = "time"
_TIME_ACADEMIC_YEAR = "time_academic_year"
_TIME_WITH_ORIGIN = "time_with_origin"
_TIME_WITH_ORIGIN_STAT = "time_with_origin_stat"


def _time_doc(doc: Row) -> Row:
    """Canonical ``time`` row (matches ``oracle/times._time_row``)."""
    return {
        "_id": str(doc["_id"]),
        "user": doc.get("user"),
        "on_campus_raw": doc.get("on_campus_raw"),
        "off_campus_raw": doc.get("off_campus_raw"),
        "social_practice": doc.get("social_practice"),
        "updated_at": doc.get("updated_at"),
    }


def _academic_year_doc(doc: Row) -> Row:
    """Canonical ``time_academic_year`` row (hyphenated Mongo keys kept)."""
    return {
        "_id": str(doc["_id"]),
        "user": doc.get("user"),
        "entry": doc.get("entry"),
        "academic_year": doc.get("academic_year"),
        "start_of_year": doc.get("start_of_year"),
        "end_of_year": doc.get("end_of_year"),
        "on-campus": doc.get("on-campus"),
        "off-campus": doc.get("off-campus"),
        "social-practice": doc.get("social-practice"),
    }


def _with_origin_doc(doc: Row) -> Row:
    """Canonical ``time_with_origin`` row (matches ``oracle/times``)."""
    return {
        "_id": str(doc["_id"]),
        "user": doc.get("user"),
        "data": doc.get("data"),
        "updated_at": doc.get("updated_at"),
    }


def _with_origin_stat_doc(doc: Row) -> Row:
    """Canonical ``time_with_origin_stat`` row."""
    return {
        "_id": str(doc["_id"]),
        "id": doc.get("id"),
        "scope": doc.get("scope"),
        "scope_id": doc.get("scope_id"),
        "require_percentage": doc.get("require_percentage"),
        "data": doc.get("data"),
        "updated_at": doc.get("updated_at"),
    }


class MongoTimeRepo:
    """``TimeRepo`` over MongoDB (``zvms_new`` time collections)."""

    async def get_for_user(self, user_oid: str) -> Row | None:
        user = normalize_oid(user_oid)
        doc = await db.zvms_new.get_collection(_TIME).find_one({"user": user})
        if doc is None:
            return None
        return _time_doc(doc)

    async def upsert_for_user(
        self,
        user_oid: str,
        on_campus_raw: float,
        off_campus_raw: float,
        social_practice: float,
    ) -> None:
        user = normalize_oid(user_oid)
        collection = db.zvms_new.get_collection(_TIME)
        await collection.delete_many({"user": user})
        await collection.insert_one(
            {
                "_id": ObjectId(),
                "user": user,
                "on_campus_raw": on_campus_raw,
                "off_campus_raw": off_campus_raw,
                "social_practice": social_practice,
                "updated_at": datetime.now(),
            }
        )

    async def list_academic_years(self, user_oid: str) -> list[Row]:
        user = normalize_oid(user_oid)
        docs = (
            await db.zvms_new.get_collection(_TIME_ACADEMIC_YEAR)
            .find({"user": user})
            .sort([("academic_year", 1)])
            .to_list(None)
        )
        return [_academic_year_doc(doc) for doc in docs]

    async def get_with_origin(self, user_oid: str) -> Row | None:
        user = normalize_oid(user_oid)
        doc = await db.zvms_new.get_collection(_TIME_WITH_ORIGIN).find_one(
            {"user": user}
        )
        if doc is None:
            return None
        return _with_origin_doc(doc)

    async def upsert_with_origin(self, user_oid: str, data: Row) -> None:
        user = normalize_oid(user_oid)
        collection = db.zvms_new.get_collection(_TIME_WITH_ORIGIN)
        await collection.delete_many({"user": user})
        await collection.insert_one(
            {
                "_id": ObjectId(),
                "user": user,
                "data": data,
                "updated_at": datetime.now(),
            }
        )

    async def list_by_users(
        self,
        user_oids: list[str],
        sort_field: str | None = None,
        descending: bool = False,
        page: int | None = None,
        size: int | None = None,
    ) -> list[Row]:
        if not user_oids:
            return []
        users = [normalize_oid(oid) for oid in user_oids]
        cursor = db.zvms_new.get_collection(_TIME).find({"user": {"$in": users}})
        if sort_field:
            cursor = cursor.sort([(sort_field, -1 if descending else 1)])
        if page is not None:
            cursor = cursor.skip((page - 1) * size).limit(size)
        docs = await cursor.to_list(None)
        return [_time_doc(doc) for doc in docs]

    async def list_with_origin(self, user_oids: list[str]) -> list[Row]:
        if not user_oids:
            return []
        users = [normalize_oid(oid) for oid in user_oids]
        docs = (
            await db.zvms_new.get_collection(_TIME_WITH_ORIGIN)
            .find({"user": {"$in": users}})
            .to_list(None)
        )
        return [_with_origin_doc(doc) for doc in docs]

    async def upsert_with_origin_stat(
        self,
        stat_key: str,
        scope: str,
        scope_id: str,
        require_percentage: int,
        data: Row,
    ) -> None:
        collection = db.zvms_new.get_collection(_TIME_WITH_ORIGIN_STAT)
        await collection.delete_many({"id": stat_key})
        await collection.insert_one(
            {
                "_id": ObjectId(),
                "id": stat_key,
                "scope": scope,
                "scope_id": scope_id,
                "require_percentage": require_percentage,
                "data": data,
                "updated_at": datetime.now(),
            }
        )

    async def clear_for_all(self) -> None:
        await db.zvms_new.get_collection(_TIME).delete_many({})

    async def upsert_academic_year(
        self,
        user_oid: str,
        entry: str,
        academic_year: int,
        start_of_year: Any,
        end_of_year: Any,
        on_campus: float,
        off_campus: float,
        social_practice: float,
    ) -> None:
        user = normalize_oid(user_oid)
        collection = db.zvms_new.get_collection(_TIME_ACADEMIC_YEAR)
        await collection.delete_many({"user": user})
        await collection.insert_one(
            {
                "_id": ObjectId(),
                "user": user,
                "entry": entry,
                "academic_year": academic_year,
                "start_of_year": start_of_year,
                "end_of_year": end_of_year,
                "on-campus": on_campus,
                "off-campus": off_campus,
                "social-practice": social_practice,
            }
        )

    async def clear_academic_years(self) -> None:
        await db.zvms_new.get_collection(_TIME_ACADEMIC_YEAR).delete_many({})

    async def clear_with_origin(self) -> None:
        await db.zvms_new.get_collection(_TIME_WITH_ORIGIN).delete_many({})

    async def clear_with_origin_stats(self) -> None:
        await db.zvms_new.get_collection(_TIME_WITH_ORIGIN_STAT).delete_many({})


__all__ = ["MongoTimeRepo"]
