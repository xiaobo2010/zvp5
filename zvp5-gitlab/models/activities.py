from typing import *
from annotated_types import *

from ..dtypes import (
    ObjectId,
    ActivityStatus,
    ActivityTime,
    UserRole,
)
from .shared import *
from .relations import (
    ActivityResTable,
)

__all__ = ['ActivityTemplate', 'Activity', 'ActivityMember']

class ActivityTemplate(db.Model):
    __tablename__ = 'templates'

    # columns #
    object_id: db.Mapped[str] = _primarycol()
    name: db.Mapped[str] = _col()
    school_id: db.Mapped[Optional[str]] = _foreigncol('schools')
    permission: db.Mapped[UserRole] = _foreigncol()
    max_time: db.Mapped[AcivityTime] = _actimecomp('mx')
    time_unit_hr: db.Mapped[float] = _col(default=0.5)
    deleted: db.Mapped[bool] = _col(default=False)
    create_time: db.Mapped[int] = _ctimecol()
    update_time: db.Mapped[int] = _utimecol()

    # schemas #
    class upload_schema(db.Schema):
        name: Annotated[str, MinLen(1)]
        school_id: Optional[ObjectId]
        permission: UserRole
        max_time: ActivityTime
        time_unit_hr: float = 0.5

        _coerce = utils.coerce_ienum(permission=UserRole)
        _ser = utils.serialize_ienum(permission=UserRole)

    class update_schema(db.Schema):
        name: Annotated[str, MinLen(1)]
        permission: UserRole

        _coerce = utils.coerce_ienum(permission=UserRole)
        _ser = utils.serialize_ienum(permission=UserRole)

    class schema(db.OrmSchema):
        object_id: str
        name: str
        school_id: Optional[str]
        permission: UserRole
        max_time: ActivityTime
        time_unit_hr: float
        deleted: bool
        create_time: int
        update_time: int

        _coerce = utils.coerce_ienum(permission=UserRole)
        _ser = utils.coerce_ienum(permission=UserRole)

class ActivityMember(db.Model):
    __tablename__ = 'members'

    # columns #
    activity_id: db.Mapped[str] = _foreigncol('activities', primary_key=True)
    user_id: db.Mapped[str] = _foreigncol('users', primary_key=True)
    pending_duration: db.Mapped[ActivityTime] = _actimecomp('pd')
    effective_duration: db.Mapped[ActivityTime] = _actimecomp('ed')
    create_time: _ctimecol()
    update_time: _utimecol()

    # cache columns #
    cached_class_id: db.Mapped[str] = _foreigncol('classes')
    cached_school_id: db.Mapped[str] = _foreigncol('schools')

    # schemas #
    class upload_schema(db.Schema):
        user_id: ObjectId
        duration: ActivityTime

    class schema(db.OrmSchema):
        user_id: str
        pending_duration: ActivityTime
        effective_duration: ActivityTime
        create_time: int
        update_time: int

class Activity(db.Model):
    __tablename__ = 'activities'

    # columns #
    object_id: db.Mapped[str] = _primarycol()
    name: db.Mapped[str] = _col()
    description: db.Mapped[Optional[str]] = _col(db.String(4000), default=None)
    status: db.Mapped[ActivityStatus] = _ienumcol(
        ActivityStatus, default=ActivityStatus.PENDING
    )
    template_id: db.Mapped[str] = _foreigncol('templates')
    school_id: db.Mapped[str] = _foreigncol('schools')
    parent_activity_id: db.Mapped[Optional[str]] = _foreigncol('activities')
    deleter_id: db.Mapped[Optional[str]] = _foreigncol('users')
    create_time: _ctimecol()
    update_time: _utimecol()

    # relationships #
    attachments: db.WriteOnlyMapped['Resource'] = _rel(
        'activities', ActivityResTable
    )

    # schemas #
    class upload_schema(db.Schema):
        name: Annotated[str, MinLen(1)]
        description: str
        template_id: ObjectId
        # 'school_id' is absent because it is inferred from token
        draft: bool
        child_activities: list[ObjectId] = []
        members: list[ActivityMember.upload_schema]
        attachments: list[ObjectId] = []
        audit_requests: list[ObjectId] = []

    class short_schema(db.OrmSchema):
        object_id: str
        name: str
        status: ActivityStatus
        template_id: str
        school_id: str
        deleter_id: Optional[str]
        create_time: str

        _coerce = utils.coerce_ienum(status=ActivityStatus)
        _ser = utils.serialize_ienum(status=ActivityStatus)

    class schema(db.OrmSchema):
        object_id: str
        name: str
        description: Optional[str]
        template_id: str
        school_id: str
        parent_activity_id: str
        deleter_id: Optional[str]
        create_time: str
        update_time: str
        attachments: Optional[list[str]]

        _coerce = utils.coerce_ienum(status=ActivityStatus)
        _ser = utils.serialize_ienum(status=ActivityStatus)
