import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, List, Optional, Literal
from pydantic import BaseModel, Field

from db import backends
from db.ids import is_valid_oid
from typings.time import UserActivityTime
from util.calculate import (
    calculate_user_time,
    describe_memberships,
)
from util.get_class import get_user_classname
from util.oid import validate_object_id

# Language type definition
Language = Literal["zh", "en"]

# Translation dictionaries
TRANSLATIONS = {
    "zh": {
        "volunteer_statement": "义工明细",
        "signature": "镇海中学义工管理平台 | 学生会",
        "name": "姓名",
        "student_id": "学号",
        "class": "班级",
        "database_id": "数据库 ID",
        "export_time": "导出时间",
        "export_period": "导出周期",
        "volunteer_statistics": "义工时长统计",
        "volunteer_statistics_this_year": "本学年义工时长统计",
        "category": "统计项目",
        "total_hours": "总时长",
        "on_campus": "校内",
        "off_campus": "校外",
        "social_practice": "社会实践",
        "effective_hours": "有效时长",
        "volunteer_hours": "义工时长",
        "required_hours": "要求最低时长",
        "reached_target": "已达标",
        "not_reached_target": "未达标",
        "target_status": "达标状态",
        "target": "根据平台数据，该同学义工时长已达到要求",
        "volunteer_hours_this_year": "本学年义工时长",
        "hours": "小时",
        "volunteer_service_records": "义工记录明细",
        "activity_name": "义工名称",
        "date": "日期",
        "recording_mode": "记入模式",
        "duration": "计入时长",
        "person_in_charge": "负责人",
        "sgn": "镇海中学团委 学生会",
        "stmt": "本明细由平台数据导出自动生成，经团委审核、盖章后生效。未经审核的明细不具备证明效力。",
        "spreadsheet": {
            "_id": "数据库 ID",
            "name": "姓名",
            "id": "学号",
            "group": "班级",
            "on-campus": "校内",
            "off-campus": "校外",
            "social-practice": "社会实践",
            "ay-on-campus": "本学年校内",
            "ay-off-campus": "本学年校外",
            "ay-social-practice": "本学年社会实践",
            "description": "明细",
            "filename": "义工明细表",
        },
    },
    "en": {
        "volunteer_statement": "Volunteer Details",
        "signature": "Zhenhai High School Volunteer Management System | Student Union",
        "name": "Name",
        "student_id": "Student ID",
        "class": "Class",
        "database_id": "Database ID",
        "export_time": "Export Time",
        "export_period": "Export Period",
        "volunteer_statistics": "Volunteer Statistics",
        "volunteer_statistics_this_year": "Volunteer Statistics this Year",
        "category": "Category",
        "total_hours": "Total Hours",
        "on_campus": "On-Campus",
        "off_campus": "Off-Campus",
        "social_practice": "Social Practice",
        "effective_hours": "Effective Hours",
        "volunteer_hours": "Volunteer Hours",
        "required_hours": "Required Minimum Hours",
        "reached_target": "Target Reached",
        "not_reached_target": "Target Not Reached",
        "target_status": "Target Status",
        "volunteer_hours_this_year": "Volunteer Hours this Year",
        "hours": "Hours",
        "volunteer_service_records": "Volunteer Service Records",
        "activity_name": "Activity Name",
        "date": "Date",
        "recording_mode": "Recording Mode",
        "duration": "Duration",
        "person_in_charge": "Person in Charge",
        "sgn": "Zhenhai High School Youth League Committee & Student Union",
        "stmt": "This statement is automatically generated from platform data and is valid after review and stamping by the Youth League Committee. Unreviewed statements do not have proof of validity.",
        "spreadsheet": {
            "_id": "Database ID",
            "name": "Name",
            "id": "Student ID",
            "group": "Class",
            "on-campus": "On-Campus",
            "off-campus": "Off-Campus",
            "social-practice": "Social Practice",
            "ay-on-campus": "On-Campus this Year",
            "ay-off-campus": "Off-Campus this Year",
            "ay-social-practice": "Social Practice this Year",
            "description": "Details",
            "filename": "Volunteer Details Sheet",
        },
    },
}


# Pydantic models for data validation
class UserData(BaseModel):
    name: str = Field(..., description="User's full name")
    student_id: str = Field(..., description="Student ID number")
    class_name: str = Field(..., description="Class name")
    database_id: Optional[str] = Field(None, description="Database ID")
    export_time: str = Field(..., description="Export timestamp")
    export_period: Optional[str] = Field(None, description="Export period range")


class StatsData(BaseModel):
    on_campus_hours: float = Field(0, description="Total on-campus volunteer hours")
    off_campus_hours: float = Field(0, description="Total off-campus volunteer hours")
    social_practice_hours: float = Field(0, description="Total social practice hours")

    @property
    def total_hours(self) -> float:
        """Calculate total volunteer hours"""
        return self.on_campus_hours + self.off_campus_hours + self.social_practice_hours


class StatementRecord(BaseModel):
    name: str = Field(..., description="Activity name")
    date: str = Field(..., description="Activity date")
    recording_mode: str = Field(..., description="Recording mode: on, off, or social")
    duration: float = Field(..., description="Service duration in hours")
    person_in_charge_name: str = Field(..., description="Person in charge name")
    person_in_charge_id: str = Field(..., description="Person in charge ID")


class VolunteerStatementData(BaseModel):
    user_data: UserData
    stats_data: StatsData
    stats_data_raw: StatsData
    stats_data_this_year: StatsData
    statement_data: List[StatementRecord] = Field(default_factory=list)


def assemble_statement(
    *,
    oid: str,
    user: Mapping[str, Any],
    class_name: str,
    period: tuple[datetime, datetime],
    language: Language,
    stats: Mapping[str, Any],
    stats_this_year: Mapping[str, Any],
    memberships: Sequence[Mapping[str, Any]],
    activities: Sequence[Mapping[str, Any] | None],
    approvers: Mapping[str, Mapping[str, Any] | None],
) -> tuple[VolunteerStatementData, dict]:
    """Assemble one statement from data the caller has already loaded.

    Pure Python: no database access, no ``await``. This is the single
    implementation behind both :func:`build_statement_data` (which fills the
    arguments with per-user queries) and the batched report/export paths (which
    fill them from school-wide maps), so the two can never drift apart.

    Args:
        oid: User oid (24-hex).
        user: Full user document (``_id`` / ``id`` / ``name`` are read).
        class_name: Class display name (already resolved by the caller).
        period: Start and end dates for the statement header.
        language: ``"zh"`` or ``"en"``.
        stats: ``calculate_user_time`` result for all effective memberships.
        stats_this_year: Same, restricted to the current academic year.
        memberships: Effective membership rows for this user, in display order.
        activities: Activity documents **aligned with** ``memberships`` (``None``
            where the activity is missing); non-``effective`` rows are skipped.
        approvers: ``approver_oid -> user document`` (``None`` for a dangling
            approver). Non-oid approvers fall back to the built-in authority.

    Returns:
        A tuple of the assembled ``VolunteerStatementData`` and the per-user
        ``spreadsheet_data`` dict (``filename`` is added by the caller).
    """

    user_name = user.get("name", "Unknown User")
    student_id = user.get("id", "Unknown ID")

    # Create Pydantic models from kernel data
    user_data = UserData(
        name=user_name,
        student_id=student_id,
        class_name=class_name,
        database_id=str(user["_id"]),
        export_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        export_period=f"{period[0].strftime('%Y年%m月%d日')}——{period[1].strftime('%Y年%m月%d日')}"
        if language == "zh"
        else f"{period[0].strftime('%B %d, %Y')} – {period[1].strftime('%B %d, %Y')}",
    )

    on_campus_hours = stats.get("on-campus", 0.0)
    off_campus_hours = stats.get("off-campus", 0.0)
    social_practice_hours = stats.get("social-practice", 0.0)
    stat_obj = UserActivityTime(
        _id=oid,
        user=user["id"],
        on_campus_raw=on_campus_hours,
        off_campus_raw=off_campus_hours,
        social_practice=social_practice_hours,
        updated_at=datetime.now(),
    )

    stats_data = StatsData(
        on_campus_hours=round(stat_obj.on_campus, 1),
        off_campus_hours=round(stat_obj.off_campus, 1),
        social_practice_hours=stat_obj.social_practice,
    )

    raw_data = StatsData(
        on_campus_hours=stat_obj.on_campus_raw,
        off_campus_hours=stat_obj.off_campus_raw,
        social_practice_hours=stat_obj.social_practice,
    )

    stats_data_this_year = StatsData(
        on_campus_hours=stats_this_year.get("on-campus", 0.0),
        off_campus_hours=stats_this_year.get("off-campus", 0.0),
        social_practice_hours=stats_this_year.get("social-practice", 0.0),
    )

    statement_records = []

    type_map = {"on-campus": "on", "off-campus": "off", "social-practice": "social"}
    for item, activity in zip(memberships, activities):
        if not activity or activity.get("status") != "effective":
            continue
        assignee = activity.get("approver", "Unknown")
        if is_valid_oid(assignee):
            assignee_data = approvers.get(assignee)
        else:
            assignee_data = {
                "name": "学校团委" if language == "zh" else "Authority",
                "id": "AUTHORITY",  # Default authority ID
            }
        statement_records.append(
            StatementRecord(
                name=activity["name"],
                date=activity["date"].strftime("%Y-%m-%d"),
                recording_mode=type_map[item["mode"]],
                duration=item["duration"],
                person_in_charge_name=assignee_data.get("name", "Unknown")
                if assignee_data
                else "学校团委"
                if language == "zh"
                else "Authority",
                person_in_charge_id=assignee_data.get(
                    "id", "AUTHORITY" if language == "zh" else "Authority"
                )
                if assignee_data
                else "AUTHORITY"
                if language == "zh"
                else "Authority",
            )
        )

    # Create complete data structure
    volunteer_data = VolunteerStatementData(
        user_data=user_data,
        stats_data=stats_data,
        stats_data_raw=raw_data,
        stats_data_this_year=stats_data_this_year,
        statement_data=statement_records,
    )

    description = describe_memberships(memberships, activities)

    spreadsheet_data = {
        "_id": user_data.database_id,
        "name": user_data.name,
        "id": user_data.student_id,
        "group": user_data.class_name,
        "on-campus": stats_data.on_campus_hours,
        "off-campus": stats_data.off_campus_hours,
        "social-practice": stats_data.social_practice_hours,
        "ay-on-campus": stats_data_this_year.on_campus_hours,
        "ay-off-campus": stats_data_this_year.off_campus_hours,
        "ay-social-practice": stats_data_this_year.social_practice_hours,
        "description": description,
    }

    return volunteer_data, spreadsheet_data


# Function to build statement data from your kernel data
async def build_statement_data(
    id: str,
    period: tuple[datetime, datetime],
    language: Language = "zh",
    *,
    user: Optional[Mapping[str, Any]] = None,
    class_name: Optional[str] = None,
    groups: Optional[Mapping[str, Mapping[str, Any]]] = None,
    activities: Optional[Mapping[str, Mapping[str, Any]]] = None,
    memberships: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
    approvers: Optional[Mapping[str, Mapping[str, Any]]] = None,
    stats: Optional[Mapping[str, Any]] = None,
    stats_this_year: Optional[Mapping[str, Any]] = None,
) -> tuple[VolunteerStatementData, dict]:
    """
    Build the volunteer statement model and spreadsheet row from kernel data

    Args:
        id: Unique identifier for the user (e.g., student ID)
        period: Tuple containing start and end dates for the statement period
        language: Language for the statement ("zh" for Chinese, "en" for English)
        user: Pre-loaded user document (``get_by_oid`` result).
        class_name: Pre-resolved class name; resolution is skipped when given.
        groups: ``group_oid -> group document`` map, used to resolve the class
            name from ``user["class_id"]``. Falls back to ``get_user_classname``
            when the oid is absent so odd data keeps the historical behaviour.
        activities: ``activity_oid -> activity document`` map.
        memberships: ``user_oid -> effective membership rows`` map.
        approvers: ``approver_oid -> user document`` map.
        stats: Pre-computed ``calculate_user_time`` result (all memberships).
        stats_this_year: Pre-computed result for the current academic year.

    Every pre-load argument is optional and defaults to the historical per-user
    query, so existing callers keep their exact behaviour. A batched caller
    passes them all and this function performs no database access at all.

    Returns:
        A tuple of the assembled ``VolunteerStatementData`` and the per-user
        ``spreadsheet_data`` dict (``filename`` is added by the caller).
    """

    validate_object_id(id)
    if user is None:
        user = await backends.get_users_repo().get_by_oid(id)

    if class_name is None:
        class_id = user.get("class_id")
        if groups is not None and class_id is not None:
            group = groups.get(str(class_id))
            if group is not None:
                class_name = group["name"]
        if class_name is None:
            class_name = await get_user_classname(id)

    if stats is None:
        stats = await calculate_user_time(id, allow_cache=False)

    now = datetime.now()
    ay = now.year if now.month >= 9 else now.year - 1
    soy = datetime.now().replace(
        month=8, day=1, year=ay, hour=0, minute=0, second=0, microsecond=0
    )
    eoy = datetime.now().replace(
        month=7, day=31, year=ay + 1, hour=23, minute=59, second=59, microsecond=999999
    )
    if stats_this_year is None:
        stats_this_year = await calculate_user_time(
            id, date_start=soy, date_end=eoy, allow_cache=False
        )

    if memberships is None:
        items = list(
            await backends.get_members_repo().list_for_member(
                id, status="effective"
            )
        )
    else:
        items = list(memberships.get(str(id), ()))

    if activities is None:
        resolved = [
            await backends.get_activities_repo().get(item["activity"])
            for item in items
        ]
    else:
        resolved = [activities.get(str(item["activity"])) for item in items]

    if approvers is None:
        approver_oids = {
            activity["approver"]
            for activity in resolved
            if activity and is_valid_oid(activity.get("approver", ""))
        }
        approvers = {
            approver: await backends.get_users_repo().get_by_oid(approver)
            for approver in approver_oids
        }

    return assemble_statement(
        oid=id,
        user=user,
        class_name=class_name,
        period=period,
        language=language,
        stats=stats,
        stats_this_year=stats_this_year,
        memberships=items,
        activities=resolved,
        approvers=approvers,
    )


def _stats_payload(stats: StatsData) -> dict:
    """Dump a ``StatsData`` and append the derived ``total_hours`` property."""
    payload = stats.model_dump()
    payload["total_hours"] = stats.total_hours
    return payload


def statement_data_to_json(
    data: VolunteerStatementData, spreadsheet: dict, filename: str
) -> str:
    """
    Serialise a volunteer statement into a UTF-8 friendly JSON string

    Args:
        data: Assembled volunteer statement model
        spreadsheet: Per-user spreadsheet row dict
        filename: Output filename to record in the payload

    Returns:
        An indented JSON string with non-ASCII characters left unescaped.
    """

    payload = {
        "user_data": data.user_data.model_dump(),
        "stats_data": _stats_payload(data.stats_data),
        "stats_data_raw": _stats_payload(data.stats_data_raw),
        "stats_data_this_year": _stats_payload(data.stats_data_this_year),
        "statement_data": [record.model_dump() for record in data.statement_data],
        "spreadsheet": spreadsheet,
        "filename": filename,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def write_statement_json(
    data: VolunteerStatementData, spreadsheet: dict, filename: str
) -> None:
    """
    Write ``statement_data_to_json`` output to ``filename`` as UTF-8

    Args:
        data: Assembled volunteer statement model
        spreadsheet: Per-user spreadsheet row dict
        filename: Destination path for the JSON file
    """

    with open(filename, "w", encoding="utf-8") as handle:
        handle.write(statement_data_to_json(data, spreadsheet, filename))
