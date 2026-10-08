from typing import *
from annotated_types import *

import pydantic

from ..dtypes import (
    ObjectId,
    UserRole,
    Hash,
)
from .shared import *
from .relations import (
    GroupMemberTable,
    AuditRequestTable,
    NoticeReadStatTable,
)
from .mgmt import Device

__all__ = ['User', 'Token']

class User(db.Model):
    __tablename__ = 'users'

    # columns #
    object_id: db.Mapped[str] = _primarycol()
    name: db.Mapped[str] = _col() # 显示的姓名
    username: db.Mapped[str] = _col() # 用户名, 即登录名
    id: db.Mapped[str] = _col() # 学号, 用于校内索引
    xhid: db.Mapped[Optional[int]] = _col() # 学海用户id, 用于绑定
    class_id: db.Mapped[str] = _foreigncol('classes')
    role: db.Mapped[UserRole] = _ienumcol(UserRole)
    can_create_activities: db.Mapped[bool] = _col()
    can_audit_activities: db.Mapped[bool] = _col()
    password: db.Mapped[Optional[Hash]] = _col()
    recovery_q1: db.Mapped[Optional[str]] = _col()
    recovery_q2: db.Mapped[Optional[str]] = _col()
    recovery_q3: db.Mapped[Optional[str]] = _col()
    recovery_a1: db.Mapped[Optional[Hash]] = _col()
    recovery_a2: db.Mapped[Optional[Hash]] = _col()
    recovery_a3: db.Mapped[Optional[Hash]] = _col()
    banned_until: db.Mapped[int] = _col(default=0)
    create_time: _ctimecol()
    update_time: _utimecol()

    # cache columns to improve query speed #
    cached_school_id: db.Mapped[str] = _foreigncol('schools')

    # relationships #
    groups: db.WriteOnlyMapped['Group'] = _rel(
        'members', GroupMemberTable
    )
    device: db.WriteOnlyMapped['Device'] = _rel('users', UserDeviceTable)
    pending: db.WriteOnlyMapped['Workflow'] = _rel(
        'audit_requests', AuditRequestTable
    )
    unread_notices: db.WriteOnlyMapped['Notice'] = _rel(
        'unread_users', NoticeReadStatTable
    )

    # schemas #
    class upload_schema(db.Schema):
        name: Annotated[str, MinLen(1)]
        username: Annotated[str, MinLen(1)]
        id: Annotated[str, MinLen(1)]
        xhid: Optional[int]
        class_id: ObjectId
        role: UserRole
        can_create_activities: bool
        can_audit_activities: bool

        _coerce = utils.coerce_iflag(role=UserRole)
        _ser = utils.serialize_iflag(role=UserRole)

    class short_schema(db.OrmSchema):
        object_id: str
        name: str
        id: str
        class_id: str
        role: UserRole
        can_create_activities: bool
        can_audit_activities: bool

        _coerce = utils.coerce_iflag(role=UserRole)
        _ser = utils.serialize_iflag(role=UserRole)

    class schema(db.OrmSchema):
        object_id: ObjectId
        name: str
        username: str
        id: str
        xhid: Optional[int]
        class_id: ObjectId
        role: UserRole
        can_create_activities: bool
        can_audit_activities: bool
        banned_until: int
        create_time: int
        update_time: int

        _coerce = utils.coerce_iflag(role=UserRole)
        _ser = utils.serialize_iflag(role=UserRole)

class Token(db.Schema):
    SYSTEM: ClassVar[Token] # No quotes needed in Python 3.14+

    device: Device.schema
    user: User.schema
    session_id: ObjectId
    is_system: Annotated[bool, db.Field(default=False)]
    scope: Literal['persist', 'authz', 'recovery']

# THIS TOKEN IS FOR INTERNAL CALLS ONLY!
# /api/v1/platform/login is responsible for SYSTEM login
Token.SYSTEM = Token.model_construct(
    device=None, # skips validation, pseudo token has no device id
    user=User.schema.model_construct(
        object_id=None, # SYSTEM has no oid
        name='SYSTEM',
        username=None, # SYSTEM has no username
        id=None, # SYSTEM is not a student and thus has no id
        xhid=None,
        class_id=None, # SYSTEM is not in any class
        role=UserRole.SYSTEM,
        can_create_activities=False,
        can_audit_activities=False,
        banned_until=0,
        create_time=0,
        update_time=0,
    ),
    session_id=utils.object_id(), # one session per restart
    is_system=True,
    scope=None, # not needed in internal calls
)
    