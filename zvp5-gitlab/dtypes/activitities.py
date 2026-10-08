from pydantic import *
from pydantic_core.core_schema import *

import enum
from typing import *
from annotated_types import *

from .. import database as db

from .workflows import WorkflowStatus as ActivityStatus

__all__ = ['ActivityStatus', 'ActivityTime']

# Although ActivityTime extends db.Schema, it is not itself a request payload
# schema but rather a data type, so we put it here, not in the 'schemas' dir.
class ActivityTime(db.Schema):
    on_campus: Annotated[float, Ge(0)]
    off_campus: Annotated[float, Ge(0)]
    social_practice: Annotated[float, Ge(0)]

    def __bool__(self):
        return bool(self.on_campus + self.off_campus + self.social_practice)

    def __add__(self, other):
        if isinstance(other, ActivityTime):
            return ActivityTime(
                on_campus=self.on_campus + other.on_campus,
                off_campus=self.off_campus + other.off_campus,
                social_practice=self.social_practice + other.social_practice,
            )
        return NotImplemented

    def __iadd__(self, other):
        '''支持用reduce(ActivityTime.__iadd__, some_iterable)进行统计'''

        if isinstance(other, ActivityTime):
            self.on_campus += other.on_campus
            self.off_campus += other.off_campus
            self.social_practice += other.social_practice
            return self
        return NotImplemented