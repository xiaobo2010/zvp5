from typing import *
from annotated_types import *

from ..dtypes import (
    TaskType,
    TaskStatus,
)
from .shared import *

__all__ = ['SavedTask']

class SavedTask(db.Model):
    __tablename__ = 'saved_tasks'

    # columns #
    object_id: db.Mapped[str] = _primarycol()
    type: db.Mapped[TaskType] = _ienumcol(TaskType)
    user_id: db.Mapped[Optional[str]] = _foreigncol('users')
    progress: db.Mapped[float] = _col(default=0)
    status: db.Mapped[TaskStatus] = _ienumcol(
        TaskStatus, default=TaskStatus.PENDING
    )
    result: db.Mapped[Optional[str]] = _col()
    start_time: db.Mapped[int] = _ctimecol()

    # schemas #
    class schema(db.OrmSchema):
        object_id: str
        type: TaskType
        user_id: Optional[str]
        progress: float
        status: TaskStatus
        result: Optional[str]
        start_time: int

        _coerce = utils.coerce_ienum(type=TaskType, status=TaskStatus)
        _ser = utils.serialize_ienum(type=TaskType, status=TaskStatus)