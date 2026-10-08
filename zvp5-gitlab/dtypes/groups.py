from pydantic import *
from pydantic_core.core_schema import *

import enum
from typing import *
from annotated_types import *

from .. import database as db

class SchoolType(enum.IntFlag):
    ELEMENTARY = 1
    JUNIOR_HIGH = 2
    SENIOR_HIGH = 4

    @property
    def grade_count(self) -> int:
        retval: int = 0
        if SchoolType.ELEMENTARY in self:
            retval += 6
        if SchoolType.JUNIOR_HIGH in self:
            retval += 3
        if SchoolType.SENIOR_HIGH in self:
            retval += 3
        return retval