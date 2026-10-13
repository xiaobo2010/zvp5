"""
One-off, idempotent migration: copy the legacy-only records the application
still needs from the `zvms` database into `zvms_new`.

Copied:
  * groups  - every legacy group with type == 'permission'
              (团支书/secretary, 学生会/department, 管理员/admin), preserving _id
  * imports - all legacy import records
  * tasks   - all legacy export-task records

Not copied (decided 2026-10-12):
  * logs  - the 97,510 legacy runtime logs stay frozen in `zvms`; the
            application logs into `zvms_new.app_logs` from scratch
  * users - every legacy user id already exists in `zvms_new` (superset),
            and role values follow `zvms_new` authority (no backfill)

Usage:
    python misc/migrate_legacy_to_new.py [--dry-run]

Idempotent: every write is an upsert keyed on _id, so re-running is safe.
"""

import asyncio
import os
import sys

from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import settings

DRY_RUN = "--dry-run" in sys.argv


async def copy_permission_groups(legacy, target):
    groups = await legacy.groups.find({"type": "permission"}).to_list(None)
    for group in groups:
        doc = {
            "name": group.get("name"),
            "type": "permission",
            "permissions": group.get("permissions", []),
        }
        if DRY_RUN:
            print(f"  [dry-run] groups upsert _id={group['_id']} {doc['name']} {doc['permissions']}")
        else:
            await target.groups.update_one({"_id": group["_id"]}, {"$set": doc}, upsert=True)
    return len(groups)


async def copy_collection(legacy_coll, target_coll, label):
    docs = await legacy_coll.find({}).to_list(None)
    for doc in docs:
        payload = {key: value for key, value in doc.items() if key != "_id"}
        if DRY_RUN:
            print(f"  [dry-run] {label} upsert _id={doc['_id']}")
        else:
            await target_coll.update_one({"_id": doc["_id"]}, {"$set": payload}, upsert=True)
    return len(docs)


async def run():
    client = AsyncIOMotorClient(settings.MONGODB_URI, uuidRepresentation="standard")
    legacy = client["zvms"]
    target = client["zvms_new"]
    try:
        print(f"migrate legacy zvms -> zvms_new{' (DRY RUN)' if DRY_RUN else ''}")

        groups = await copy_permission_groups(legacy, target)
        imports = await copy_collection(legacy.imports, target.imports, "imports")
        tasks = await copy_collection(legacy.tasks, target.tasks, "tasks")

        legacy_ids = set(await legacy.users.distinct("id"))
        new_ids = set(await target.users.distinct("id"))
        missing = legacy_ids - new_ids

        print("summary:")
        print(f"  permission groups copied: {groups}")
        print(f"  imports copied: {imports}")
        print(f"  tasks copied: {tasks}")
        print(f"  legacy user ids: {len(legacy_ids)}, missing from zvms_new: {len(missing)}")
        if missing:
            print(f"  WARNING missing user ids: {sorted(missing)[:10]}")
        print(f"  zvms_new.groups total: {await target.groups.count_documents({})}")
        print(
            "  zvms_new.groups permission: "
            f"{await target.groups.count_documents({'type': 'permission'})}"
        )
    finally:
        client.close()


if __name__ == "__main__":
    asyncio.run(run())
