from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db import backends
from util.object_id import get_current_user
from utils import validate_object_id

router = APIRouter()


class ChangelogEntryRequest(BaseModel):
    version: str
    date: str
    items: list[str]


def _require_admin(user: dict) -> None:
    if "admin" not in (user.get("per") or []):
        raise HTTPException(status_code=403, detail="权限不足")


def _serialize(entry: dict) -> dict:
    return {
        "id": entry["_id"],
        "version": entry["version"],
        "date": entry["date"],
        "items": entry.get("items") or [],
    }


@router.get("")
async def read_changelog():
    entries = await backends.get_changelog_repo().list_all()
    return {"status": "ok", "data": [_serialize(entry) for entry in entries]}


@router.post("", status_code=201)
async def create_changelog(
    payload: ChangelogEntryRequest,
    user=Depends(get_current_user),
):
    _require_admin(user)

    repo = backends.get_changelog_repo()
    new_id = await repo.create(payload.version, payload.date, list(payload.items))
    entry = await repo.get_by_oid(new_id)
    if entry is None:
        entry = {
            "_id": new_id,
            "version": payload.version,
            "date": payload.date,
            "items": list(payload.items),
        }
    return _serialize(entry)


@router.put("/{oid}", status_code=204)
async def update_changelog(
    oid: str,
    payload: ChangelogEntryRequest,
    user=Depends(get_current_user),
):
    _require_admin(user)
    validate_object_id(oid)
    await backends.get_changelog_repo().update(
        oid, payload.version, payload.date, list(payload.items)
    )
    return


@router.delete("/{oid}", status_code=204)
async def delete_changelog(
    oid: str,
    user=Depends(get_current_user),
):
    _require_admin(user)
    validate_object_id(oid)
    await backends.get_changelog_repo().delete(oid)
    return
