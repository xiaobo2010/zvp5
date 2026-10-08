from typing import *
from annotated_types import *

from pydantic import AfterValidator
from uuid import uuid4

from .shared import *
from .relation import (
    NoticeReadStatTable,
)

__all__ = ['Notice', 'Notification']

class Notification(db.Model):
    __tablename__ = 'missed_n12ns' # oh no the word is too long

    # columns #
    uuid: db.Mapped[str] = _col(
        db.String(36),
        primary_key=True,
        default_factory=lambda: str(uuid4()),
    )
    user_id: db.Mapped[str] = _foreigncol('users')
    data: db.Mapped[dict] = _jsoncol()

    # no schema here, raw data is pushed through ws

class Notice(db.Model):
    __tablename__ = 'notices'

    # columns #
    object_id: db.Mapped[str] = _primarycol()
    school_id: db.Mapped[Optional[str]] = _foreigncol('schools')
    title: db.Mapped[str] = _col()
    description: db.Mapped[str] = _col()
    link: db.Mapped[Optional[str]] = _col()
    shows_on: db.Mapped[int] = _ctimecol()
    expires_on: db.Mapped[int] = _col()
    total_users: db.Mapped[int] = _col() # internal, for stats
    create_time: db.Mapped[int] = _ctimecol()
    update_time: db.Mapped[int] = _utimecol()

    # relationships #
    unread_users: WriteOnlyMapped['User'] = _rel(
        'unread_notices', NoticeReadStatTable
    )

    # schemas #
    class upload_schema(db.Schema):
        # 'school_id' is inferred from token, or None for SYSTEM
        title: Annotated[str, MinLen(1)]
        description: str
        link: Optional[str]
        shows_on: Annotated[int, db.Field(default_factory=utils.timestamp)]
        expires_on: int

    class limited_perm_schema(db.OrmSchema):
        object_id: str
        title: str
        description: str
        link: Optional[str]
        shows_on: int
        unread: bool = False # fetched using db query

    class schema(db.OrmSchema):
        object_id: str
        school_id: str
        title: str
        description: str
        link: Optional[str]
        shows_on: int
        expires_on: int
        total_users: str
        total_unread: str = 0 # fetched using a join
        create_time: int
        update_time: int