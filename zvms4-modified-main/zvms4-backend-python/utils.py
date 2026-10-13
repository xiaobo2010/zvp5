from fastapi import HTTPException

from db.ids import normalize_oid


def validate_object_id(id: str):
    try:
        return normalize_oid(id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid Object ID")
