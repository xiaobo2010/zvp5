from typing import *
from annotated_types import *

from ..dtypes import (
    ObjectId,
    SchoolType,
    ActivityTime,
    ConversionRule,
)
from .shared import *
from .relations import (
    SchoolFaqTable,
    GroupMemberTable,
)

__all__ = ['School', 'Class', 'Group']

class School(db.Model):
    __tablename__ = 'schools'

    # columns #
    object_id: db.Mapped[str] = _primarycol()
    name: db.Mapped[str] = _col()
    type: db.Mapped[SchoolType] = _ienumcol(SchoolType)
    time_limits: db.Mapped[ActivityTime] = _actimecomp('tl')
    conversion_rules: db.Mapped[list[ConversionRule]] = _jsoncol(
        list[ConversionRule], default=[]
    )
    rules_faq_id: db.Mapped[Optional[str]] = _foreigncol('resources')
    create_time:db.Mapped[int] = _ctimecol()
    update_time: db.Mapped[int] = _utimecol()

    # relationships #
    faqs: db.WriteOnlyMapped['Help'] = _rel('schools', SchoolFaqTable)

    # schemas #
    class upload_schema(db.Schema):
        name: Annotated[str, MinLen(1)]
        type: SchoolType
        time_limits: ActivityTime
        conversion_rules: list[ConversionRule]

        _coerce = utils.coerce_iflag(type=SchoolType)
        _ser = utils.serialize_iflag(type=SchoolType)

    class short_schema(db.OrmSchema):
        object_id: str
        name: str
        type: SchoolType

        _coerce = utils.coerce_iflag(type=SchoolType)
        _ser = utils.serialize_iflag(type=SchoolType)

    class schema(db.OrmSchema):
        object_id: str
        name: str
        type: SchoolType
        time_limits: ActivityTime
        conversion_rules: list[ConversionRule]
        create_time: int
        update_time: int

        _coerce = utils.coerce_iflag(type=SchoolType)
        _ser = utils.serialize_iflag(type=SchoolType)

class Class(db.Model):
    __tablename__ = 'classes'

    # columns #
    object_id: db.Mapped[str] = _primarycol()
    name: db.Mapped[str] = _col()
    xhalias: db.Mapped[Optional[str]] = _col()
    enroll_year: db.Mapped[int] = _col()
    school_id: db.Mapped[str] = _foreigncol('schools')
    create_time: db.Mapped[int] = _ctimecol()
    update_time: db.Mapped[int] = _utimecol()

    # schemas #
    class upload_schema(db.Schema):
        name: Annotated[str, MinLen(1)]
        xhalias: Optional[str]
        school_id: ObjectId
        users: Optional[list[ObjectId]]

    class short_schema(db.OrmSchema):
        object_id: str
        name: str
        enroll_year: int
        school_id: str

    class schema(db.OrmSchema):
        object_id: str
        name: str
        xhalias: Optional[str]
        enroll_year: int
        school_id: str
        create_time: int
        update_time: int

class Group(db.Model):
    __tablename__ = 'groups'

    # columns #
    object_id: db.Mapped[str] = _primarycol()
    name: db.Mapped[str] = _col()
    creator_id: db.Mapped[str] = _foreigncol('users')
    create_time: db.Mapped[int] = _ctimecol()
    update_time: db.Mapped[int] = _utimecol()

    # relationships #
    members: db.WriteOnlyMapped['User'] = _rel('groups', GroupMemberTable)

    # schemas #
    class upload_schema(db.Schema):
        name: Annotated[str, MinLen(1)]
        users: Optional[list[ObjectId]]

    class schema(db.OrmSchema):
        name: str
        creator_id: str
        create_time: int
        update_time: int