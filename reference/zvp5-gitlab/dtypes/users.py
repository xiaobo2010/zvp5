from pydantic import *
from pydantic_core.core_schema import *

import enum
from typing import *
from annotated_types import *

from .. import database as db

__all__ = ['UserRole']

class UserRole(enum.IntFlag):
    '''表示用户的权限。“SYSTEM”仅供前端logs页面显示权限用，不用于验证。'''

    NONE = 0
    SECRETARY = 1
    STU_UNION = 2
    ADMIN = 4
    SYSTEM = 8