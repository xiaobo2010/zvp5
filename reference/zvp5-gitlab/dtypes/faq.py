from pydantic import *
from pydantic_core.core_schema import *

import enum
from typing import *
from annotated_types import *

from .. import database as db

__all__ = ['FaqCategory']

class FaqCategory(enum.IntFlag):
    NONE = 0
    COMMON = 1
    RULES = 2
    RELEASE_NOTE = 4
    UI = 8
    NETWORK = 16
    ACCOUNT = 32
    ACTIVITIES = 64
    WORKFLOWS = 128