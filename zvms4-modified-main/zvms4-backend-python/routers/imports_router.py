import asyncio
import math
from datetime import datetime
from io import BytesIO
from zipfile import BadZipFile, ZipFile

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from db import backends
from db.ids import normalize_oid
from db.oracle.session import session_scope
from typings.log import inject_log
from util.object_id import get_current_user
from util.validation import validate_activity_description, validate_activity_name

router = APIRouter()

_MAX_BYTES = 10 * 1024 * 1024
_MAX_ROWS = 10_000
_COLUMNS = ["_id", "Name", "ID", "Group", "On Campus", "Off Campus", "Social Practice"]
_MODES = {"On Campus": "on-campus", "Off Campus": "off-campus", "Social Practice": "social-practice"}


def _parse_import(contents: bytes) -> dict[tuple[str, str], float]:
    if contents.startswith(b"PK"):
        with ZipFile(BytesIO(contents)) as archive:
            entries = archive.infolist()
            if len(entries) > 2000 or sum(item.file_size for item in entries) > 100 * 1024 * 1024:
                raise ValueError("Expanded workbook too large")
    records = {}
    total_rows = 0
    with pd.ExcelFile(BytesIO(contents)) as workbook:
        if not 1 <= len(workbook.sheet_names) <= 20:
            raise ValueError("Too many sheets")
        for sheet in workbook.sheet_names:
            frame = pd.read_excel(workbook, sheet_name=sheet, nrows=_MAX_ROWS + 1,
                                  dtype={"_id": str}, keep_default_na=False)
            frame.rename(columns={"Class": "Group"}, inplace=True)
            if frame.columns.to_list() != _COLUMNS:
                raise ValueError("Invalid columns")
            total_rows += len(frame)
            if total_rows > _MAX_ROWS:
                raise ValueError("Too many rows")
            for row in frame.to_dict("records"):
                oid = normalize_oid(row["_id"])
                for column, mode in _MODES.items():
                    raw = row[column]
                    if raw is None or raw == "":
                        continue
                    if isinstance(raw, bool):
                        raise ValueError("Invalid duration")
                    duration = float(raw)
                    if not math.isfinite(duration) or duration < 0:
                        raise ValueError("Invalid duration")
                    if duration:
                        key = (oid, mode)
                        records[key] = records.get(key, 0.0) + duration
                        if not math.isfinite(records[key]):
                            raise ValueError("Duration overflow")
    if not records:
        raise ValueError("No nonzero durations")
    return records


@router.post("/activities")
async def upload_activity_excel(
    name: str,
    desc: str,
    payload: UploadFile = File(...),
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    if "admin" not in (user.get("per") or []):
        raise HTTPException(status_code=403, detail="权限不足")
    for validator, value in ((validate_activity_name, name), (validate_activity_description, desc)):
        valid, reason = validator(value)
        if not valid:
            raise HTTPException(status_code=400, detail=reason)
    if payload.content_type not in (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.ms-excel"
    ):
        raise HTTPException(status_code=400, detail="Invalid Excel file format")
    try:
        contents = await payload.read(_MAX_BYTES + 1)
    finally:
        await payload.close()
    if len(contents) > _MAX_BYTES:
        raise HTTPException(status_code=413, detail="Excel file exceeds 10 MiB")
    try:
        records = await asyncio.to_thread(_parse_import, contents)
    except (ValueError, TypeError, KeyError, BadZipFile, OSError):
        raise HTTPException(status_code=400, detail="Invalid workbook: check columns, user IDs and finite nonnegative durations")

    # Every validation and write uses one transaction. An invalid user or a
    # later insert failure rolls back the uploaded file, activity and members.
    async with session_scope() as session:
        async with session.begin():
            users_repo = backends.get_users_repo(session)
            for oid in sorted({key[0] for key in records}):
                if await users_repo.get_by_oid(oid) is None:
                    raise HTTPException(status_code=400, detail="Workbook contains an unknown user")
            now = datetime.now()
            await backends.get_imports_repo(session).create(
                name, (payload.filename or "import.xlsx").encode("utf-8")[:256].decode("utf-8", "ignore"),
                desc, user["id"], now.isoformat(), contents
            )
            activities_repo = backends.get_activities_repo(session)
            activity_id = await activities_repo.create(
                name, desc, "hybrid", user["id"], user["id"], "import", now
            )
            await activities_repo.update(activity_id, {
                "status": "effective", "approver": "authority", "place": "", "updatedAt": now
            })
            members_repo = backends.get_members_repo(session)
            for (oid, mode), duration in records.items():
                await members_repo.add(str(activity_id), oid, mode, duration, "effective")
            # 同一条审计要能直接读：操作者写名字，活动写文件名里的活动名。
            performer = (
                await users_repo.get_names([str(user["id"])])
            ).get(str(user["id"])) or "未知用户"
            log.with_text(
                f"用户 {performer} 导入义工活动《{name}》Excel，记录 {len(records)} 条",
                type="import_excel",
            )
            await log.insert_log(session)

    return {"status": "ok", "code": 201}
