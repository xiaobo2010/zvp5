from pydantic import *
from pydantic_core.core_schema import *

import enum
from typing import *
from annotated_types import *

from .. import database as db

__all__ = ['WorkflowStatus']

class WorkflowStatus(enum.IntFlag):
    '''表示审核流程状态/义工状态(别名ActivityStatus)'''

    DRAFT = enum.auto() # similar to Git PR state 'draft'
    PENDING = enum.auto() # similar to Git PR state 'open, waiting approval'
    EFFECTIVE = enum.auto() # similar to Git PR state 'merged and closed'
    REJECTED = enum.auto() # similar to Git PR state 'changes requested'
    INVALID = enum.auto() # similar to Git PR state 'closed, not merged'

    # Removed following values of original ActivityStatus enum:
    # 1. Removed 'MERGED': test presence of 'parent_activity_id' instead
    # 2. Removed 'RECYCLED_*': test presence of 'deleter_id' instead