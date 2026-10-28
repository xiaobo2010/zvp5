import os
import uuid
import asyncio
import logging
from fastapi import APIRouter, HTTPException, Depends, Query, Request


from config import (
    BASE_ON_CAMPUS,
    ON_TO_OFF_RATE,
    MAX_EXCEED_DISCOUNT,
    BASE_OFF_CAMPUS,
    OFF_TO_ON_RATE,
)
from typings.export import ExportFormat, ExportTask, ExportStatus, ExportVariant
from typings.log import inject_log, ZVMSLog, xuehai_id_from
from util.calculate import calculate_user_time
from util.user import get_user_display_name
from fastapi.responses import FileResponse, Response
from io import BytesIO
import base64
import json
from util.object_id import (
    get_current_user,
    validate_object_id,
    compulsory_temporary_token,
)
from util.permission.features import has_feature
from datetime import datetime, timedelta
from db import backends
from db.models import export_tasks as _tasks_tbl
from db.oracle.session import get_session
import pandas as pd
from fastapi import BackgroundTasks
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from services import risk_monitor

_logger = logging.getLogger(__name__)

_MAX_EXPORT_PAGE_SIZE = 100
_DEFAULT_EXPORT_WINDOW_DAYS = 30
_MAX_EXPORT_WINDOW_DAYS = 365


async def _run_risk_export(user_oid) -> None:
    """导出频率规则：评估失败不得影响导出任务已创建的响应。"""
    try:
        await risk_monitor.evaluate("export_burst", str(user_oid))
    except Exception:
        _logger.warning("风险规则 export_burst 评估失败", exc_info=True)


def _task_age():
    return func.coalesce(_tasks_tbl.c.task_end, _tasks_tbl.c.task_start)


def _task_page_stmt(cutoff: datetime, page: int, size: int):
    return (
        select(
            _tasks_tbl.c.oid,
            _tasks_tbl.c.task_uuid,
            _tasks_tbl.c.status,
            _tasks_tbl.c.variant,
            _tasks_tbl.c.export_format,
            _tasks_tbl.c.task_start,
            _tasks_tbl.c.task_end,
            _tasks_tbl.c.percentage,
        )
        .where(_task_age() >= cutoff)
        .order_by(_tasks_tbl.c.task_start.desc(), _tasks_tbl.c.oid.desc())
        .offset((page - 1) * size)
        .limit(size)
    )


def _task_count_stmt(cutoff: datetime):
    return select(func.count()).select_from(_tasks_tbl).where(_task_age() >= cutoff)


def _task_summary(row) -> dict:
    raw_uuid = row["task_uuid"]
    try:
        task_id = uuid.UUID(str(raw_uuid))
    except (ValueError, TypeError):
        task_id = raw_uuid
    return {
        "_id": str(row["oid"]),
        "id": task_id,
        "status": row["status"],
        "variant": row["variant"],
        "format": row["export_format"],
        "task_start": row["task_start"],
        "task_end": row["task_end"],
        "percentage": row["percentage"],
    }


class CreateExport(BaseModel):
    start: str = ""
    end: str = ""
    format: ExportFormat
    allow_cache: bool = False
    include_description: bool = False


def _task_uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid task ID")


def _export_date(value: str):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid export date")


tokens = []


router = APIRouter()


async def process_task(task_id: str):
    try:
        await _process_task(task_id)
    except Exception:
        logging.getLogger(__name__).exception("Export task %s failed", task_id)
        await backends.get_tasks_repo().update_fields(
            _task_uuid(task_id),
            {"status": ExportStatus.failed, "task_end": datetime.now(),
             "errmsg": "导出失败，请联系管理员查看服务日志"},
        )


async def _process_task(task_id: str):
    tasks_repo = backends.get_tasks_repo()
    task = await tasks_repo.get_by_uuid(_task_uuid(task_id))
    if not task:
        return
    await tasks_repo.update_fields(
        _task_uuid(task_id),
        {"status": ExportStatus.processing, "task_start": datetime.now()},
    )
    include_description = task.get("include_description", False)
    if task["variant"] == "time":
        result = []
        users = await backends.get_users_repo().list_all()
        activities = await backends.get_activities_repo().list_effective(
            task["export_start"], task["export_end"]
        )
        for idx, user in enumerate(users):
            if task["export_start"] is not None or task["export_end"] is not None:
                user_time = await calculate_user_time(
                    str(user["_id"]),
                    task["export_start"],
                    task["export_end"],
                    attach_description=include_description,
                    activities=activities,
                )
            else:
                user_time = await calculate_user_time(
                    str(user["_id"]),
                    allow_cache=task["allow_cache"],
                    attach_description=include_description,
                    activities=activities,
                )
            more_on_campus = min(
                round(
                    max(user_time["off-campus"] - BASE_OFF_CAMPUS, 1) * OFF_TO_ON_RATE,
                    0,
                ),
                MAX_EXCEED_DISCOUNT,
            )
            more_off_campus = min(
                round(
                    max(user_time["on-campus"] - BASE_ON_CAMPUS, 1) * ON_TO_OFF_RATE, 0
                ),
                MAX_EXCEED_DISCOUNT,
            )
            user_time["on-campus"] += more_on_campus
            user_time["off-campus"] += more_off_campus
            class_oid = user.get("class_id")
            group = (
                await backends.get_groups_repo().get(class_oid) if class_oid else None
            )
            if group is None:
                continue
            doc = {
                "_id": str(user["_id"]),
                "Name": user["name"],
                "ID": str(user["id"]),
                "Group": group["name"],
                "On Campus": user_time["on-campus"],
                "Off Campus": user_time["off-campus"],
                "Social Practice": user_time["social-practice"],
                "Description": ""
                if not include_description
                else user_time.get("description", ""),
            }
            result.append(doc)
            task["percentage"] = (idx + 1) / len(users) * 100
            if idx % 20 == 19:
                await tasks_repo.push_result(
                    _task_uuid(task_id),
                    result,
                    {"percentage": task["percentage"]},
                )
                result = []
                await asyncio.sleep(0.01)
        await tasks_repo.push_result(
            _task_uuid(task_id),
            result,
            {
                "percentage": 100,
                "status": ExportStatus.completed,
                "task_end": datetime.now(),
            },
        )
    elif task["variant"] == "users":
        result = []
        users = await backends.get_users_repo().list_all()
        for idx, user in enumerate(users):
            class_oid = user.get("class_id")
            group = (
                await backends.get_groups_repo().get(class_oid) if class_oid else None
            )
            doc = {
                "_id": str(user["_id"]),
                "Name": user["name"],
                "ID": str(user["id"]),
                "Group": group["name"] if group else "",
                "On Campus": None,
                "Off Campus": None,
                "Social Practice": None,
            }
            result.append(doc)
            task["percentage"] = (idx + 1) / len(users) * 100
            if idx % 20 == 19:
                await tasks_repo.push_result(
                    _task_uuid(task_id),
                    result,
                    {"percentage": task["percentage"]},
                )
                result = []
                await asyncio.sleep(0.01)
        await tasks_repo.push_result(
            _task_uuid(task_id),
            result,
            {
                "percentage": 100,
                "status": ExportStatus.completed,
                "task_end": datetime.now(),
            },
        )
    else:
        await tasks_repo.update_fields(
            _task_uuid(task_id),
            {
                "status": ExportStatus.failed,
                "task_end": datetime.now(),
                "errmsg": "Invalid variant",
            },
        )


@router.post("/users")
async def export_users(
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    if "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="Permission denied")
    task_id = uuid.uuid4()
    task = ExportTask(
        id=task_id,
        status=ExportStatus.pending,
        format=ExportFormat.excel,
        variant=ExportVariant.users,
        export_start=None,
        export_end=None,
        task_start=datetime.now(),
        task_end=None,
        result=[],
    )
    document = task.model_dump()
    document["variant"] = ExportVariant.users.value
    document["format"] = document["format"].value
    document["status"] = document["status"].value
    await backends.get_tasks_repo().create(document)
    log.with_text(
        f"用户 {await get_user_display_name(user['id'])} 发起了用户列表导出（任务 ID: {task_id}）",
        type="export",
    )
    await log.insert_log()
    await _run_risk_export(user["id"])
    background_tasks.add_task(process_task, str(task_id))
    return {"code": 201, "status": "ok", "data": str(task_id)}


@router.post("/time")
async def export_time(
    properties: CreateExport,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    if "admin" not in user["per"] and not has_feature(user, "export.time"):
        raise HTTPException(status_code=403, detail="Permission denied")
    start, end = _export_date(properties.start), _export_date(properties.end)
    if start is not None and end is not None:
        try:
            invalid_range = start > end
        except TypeError:
            raise HTTPException(status_code=400, detail="Inconsistent date timezones")
        if invalid_range:
            raise HTTPException(status_code=400, detail="Start date must precede end date")
    task_id = uuid.uuid4()
    task = ExportTask(
        id=task_id,
        status=ExportStatus.pending,
        format=properties.format,
        variant=ExportVariant.time,
        export_start=start,
        export_end=end,
        task_start=datetime.now(),
        task_end=None,
        result=[],
        allow_cache=properties.allow_cache,
        include_description=properties.include_description,
    )
    document = task.model_dump()
    document["variant"] = ExportVariant.time.value
    document["format"] = document["format"].value
    document["status"] = document["status"].value
    await backends.get_tasks_repo().create(document)
    log.with_text(
        f"用户 {await get_user_display_name(user['id'])} 发起了时长报表导出（任务 ID: {task_id}）",
        type="export",
    )
    await log.insert_log()
    await _run_risk_export(user["id"])
    background_tasks.add_task(process_task, str(task_id))
    return {"code": 201, "status": "ok", "data": str(task_id)}


@router.get("/{task_id}")
async def get_export(task_id: str, user=Depends(get_current_user)):
    if "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="权限不足")
    task = await backends.get_tasks_repo().get_by_uuid(_task_uuid(task_id))
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    del task["result"]
    task["_id"] = str(task["_id"])
    return {"code": 200, "status": "ok", "data": task}


@router.post("/reports")
async def request_download_reports(
    user=Depends(compulsory_temporary_token),
    log=Depends(inject_log),
):
    if "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="Permission denied")
    global tokens
    token_data = {
        "token": uuid.uuid4().hex,
        "granted_to": user["id"],
        "expires_at": datetime.now().timestamp() + 300,  # 5 minutes
    }
    token_data_encoded = base64.b64encode(json.dumps(token_data).encode()).decode()
    now = datetime.now().timestamp()
    tokens = [item for item in tokens if item["expires_at"] > now]
    tokens.append(token_data)
    log.with_text(
        f"用户 {await get_user_display_name(user['id'])} 请求了报告打包下载授权",
        type="export",
    )
    await log.insert_log()
    return token_data_encoded


@router.get("/reports/download")
async def download_reports(
    request: Request,
    token: str,
):
    global tokens
    try:
        token_data = json.loads(base64.b64decode(token).decode())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid token format")

    if not isinstance(token_data, dict) or not isinstance(token_data.get("token"), str):
        raise HTTPException(status_code=400, detail="Invalid token format")
    now = datetime.now().timestamp()
    # No await between lookup and removal: consume once within this process.
    # The client supplies only a lookup key; expiry and actor are authoritative
    # server-side fields, even if the base64 JSON was edited by the caller.
    stored = next((item for item in tokens if item["token"] == token_data["token"]), None)
    tokens = [item for item in tokens if item["expires_at"] > now and item is not stored]
    if stored is None or stored["expires_at"] <= now:
        raise HTTPException(status_code=403, detail="Invalid or expired token")
    token_data = stored

    log = ZVMSLog(
        str(request.url),
        str(token_data["granted_to"]),
        f"用户 {await get_user_display_name(token_data['granted_to'])} 下载了报告文件",
        datetime.timestamp(datetime.now()),
        type="export",
        xuehai_id=xuehai_id_from(request),
    )
    await log.insert_log()

    # response to `export.tar.gz`, which is a tar.gz file containing all reports and already exists in the server
    file_path = "./data/export.tar.gz"
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        file_path,
        media_type="application/gzip",
        filename="export.tar.gz",
        headers={"Content-Disposition": "attachment; filename=export.tar.gz"},
    )


@router.get("/{task_id}/file")
async def get_export_file(
    task_id: str,
    language: str = "en",
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    if "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="Permission denied")
    task = await backends.get_tasks_repo().get_by_uuid(_task_uuid(task_id))
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task["status"] != "completed":
        raise HTTPException(status_code=400, detail="Task not completed")
    export_format = ExportFormat(task["format"])

    def render():
        result = pd.DataFrame(task["result"] or [])
        if "_id" in result.columns:
            result.sort_values("_id", inplace=True)
        if language == "zh-CN":
            result.rename(columns={
                "_id": "数据库 ID", "Name": "姓名", "ID": "学号", "Group": "班级",
                "On Campus": "校内义工时长", "Off Campus": "校外义工时长",
                "Social Practice": "社会实践时长", "Description": "描述",
            }, inplace=True)
        if export_format == ExportFormat.excel:
            buffer = BytesIO()
            result.to_excel(buffer, index=False)
            return buffer.getvalue()
        if export_format == ExportFormat.csv:
            return result.to_csv(index=False).encode("utf-8-sig")
        if export_format == ExportFormat.json:
            return result.to_json().encode("utf-8")
        if export_format == ExportFormat.latex:
            return result.to_latex().encode("utf-8")
        return result.to_html().encode("utf-8")

    content = await asyncio.to_thread(render)
    log.with_text(
        f"用户 {await get_user_display_name(user['id'])} 下载了导出文件（任务 ID: {task_id}，格式 {task['format']}）",
        type="export",
    )
    await log.insert_log()
    return Response(content, media_type=export_format.mime(), headers={
        "Content-Disposition": f'attachment; filename="export.{export_format.suffix()}"'
    })


@router.get("")
async def get_export_list(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=_MAX_EXPORT_PAGE_SIZE),
    days: int = Query(_DEFAULT_EXPORT_WINDOW_DAYS, ge=1, le=_MAX_EXPORT_WINDOW_DAYS),
    user=Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    if "admin" not in user["per"]:
        raise HTTPException(status_code=403, detail="权限不足")
    cutoff = datetime.now() - timedelta(days=days)
    total = int((await session.execute(_task_count_stmt(cutoff))).scalar_one())
    rows = (await session.execute(_task_page_stmt(cutoff, page, size))).all()
    return {
        "code": 200,
        "status": "ok",
        "data": [_task_summary(row._mapping) for row in rows],
        "page": page,
        "size": size,
        "days": days,
        "total": total,
    }
