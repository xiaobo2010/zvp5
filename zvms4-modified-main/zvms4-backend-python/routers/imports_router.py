from io import BytesIO
from typing import Literal, cast

from fastapi import APIRouter, File, HTTPException, Depends, UploadFile
from typings.log import inject_log
from util.user import get_user_display_name
from util.object_id import get_current_user
from datetime import datetime
from db import backends
import pandas as pd

from util.validation import validate_activity_description, validate_activity_name

router = APIRouter()


@router.post("/activities")
async def upload_activity_excel(
    name: str,
    desc: str,
    payload: UploadFile = File(...),
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """
    Upload activity excel
    """

    validated, reason = validate_activity_name(name)
    if not validated:
        raise HTTPException(status_code=400, detail=reason)

    validated, reason = validate_activity_description(desc)
    if not validated:
        raise HTTPException(status_code=400, detail=reason)

    if (
        payload.content_type
        != "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        and payload.content_type != "application/vnd.ms-excel"
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid file format. You should upload an excel file",
        )

    expected_columns = [
        "_id",
        "Name",
        "ID",
        "Group",
        "On Campus",
        "Off Campus",
        "Social Practice",
    ]

    try:
        contents = await payload.read()

        await backends.get_imports_repo().create(
            name,
            payload.filename,
            desc,
            user["id"],
            datetime.now().isoformat(),
            contents,
        )

        filename = BytesIO(contents)

        sheet_names = pd.ExcelFile(filename).sheet_names

        # Read all sheets into a list of DataFrames
        dfs = [pd.read_excel(filename, sheet_name=sheet) for sheet in sheet_names]

        # Concatenate all DataFrames into one
        df = pd.concat(dfs, ignore_index=True)

        if df.columns.to_list() != expected_columns:
            raise HTTPException(status_code=400, detail="Invalid excel format")

        df.fillna(0.0)
        accepted_modes = ["On Campus", "Off Campus", "Social Practice"]

        log.with_text(
            f"用户 {await get_user_display_name(user['id'])} 导入了义工活动 Excel",
            type="import_excel",
        )
        await log.insert_log()

        activities_repo = backends.get_activities_repo()
        activity_id = await activities_repo.create(
            name,
            desc,
            "hybrid",
            user["id"],
            user["id"],
            "import",
            datetime.now(),
        )
        await activities_repo.update(
            activity_id,
            {
                "status": "effective",
                "approver": "authority",
                "place": "",
                "updatedAt": datetime.now(),
            },
        )

        users_repo = backends.get_users_repo()
        members_repo = backends.get_members_repo()

        for mode in accepted_modes:
            for idx, row in df.iterrows():
                if row[mode] != 0.0 and not pd.isna(row[mode]):
                    users = await users_repo.get_by_oid(row["_id"])
                    record_mode = mode.lower().replace(" ", "-")
                    if record_mode not in [
                        "on-campus",
                        "off-campus",
                        "social-practice",
                    ]:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Invalid mode: {mode}. Expected one of {accepted_modes}.",
                        )
                    record_mode = cast(
                        Literal["on-campus", "off-campus", "social-practice"],
                        record_mode,
                    )
                    if users is not None:
                        await members_repo.add(
                            str(activity_id),
                            str(users["_id"]),
                            record_mode,
                            row[mode],
                            "effective",
                        )

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "ok", "code": 201}
