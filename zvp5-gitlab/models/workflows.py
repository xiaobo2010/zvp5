from typing import *
from annotated_types import *

from ..dtypes import (
    WorkflowStatus,
)
from .shared import *
from .relation import (
    AuditRequestTable,
)

__all__ = ['Workflow', 'WorkflowItem']

class Workflow(db.Model):
    __tablename__ = 'workflows'

    # columns #
    object_id: db.Mapped[str] = _primarycol()
    activity_id: db.Mapped[str] = _foreigncol('activities')
    creator_id: db.Mapped[str] = _foreigncol('users')
    status: db.Mapped[WorkflowStatus] = _ienumcol(WorkflowStatus)
    is_initial: db.Mapped[bool] = _col(default=False) # internal
    title: db.Mapped[str] = _col()
    description: db.Mapped[str] = _col(db.Text)
    create_time: db.Mapped[int] = _ctimecol()
    update_time: db.Mapped[int] = _utimecol()

    # cache columns #
    cache_school_id: db.Mapped[str] = _foreigncol('schools')

    # relationships #
    audit_requests: WriteOnlyMapped['User'] = _rel(
        'pending', AuditRequestTable
    )

    # schemas #
    class schema(db.OrmSchema):
        object_id: str
        activity_id: str
        creator_id: str
        status: WorkflowStatus
        title: str
        description: str
        create_time: int
        update_time: int

class WorkflowItem(db.Model):
    __tablename__ = 'workflow_items'

    # columns #
    object_id: db.Mapped[str] = _primarycol()
    workflow_id: db.Mapped[str] = _foreigncol('workflows')
    data: db.Mapped[dict] = _jsoncol()
    create_time: db.Mapped[int] = _ctimecol()

    # no schema here, returns raw data in api
    # WorkflowItem is created by api, not user JSON, so it's safe
