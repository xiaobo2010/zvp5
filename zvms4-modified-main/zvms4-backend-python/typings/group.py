from typing import Optional
from pydantic import BaseModel
from enum import Enum


class GroupType(str, Enum):
    class_ = "class"
    permission = "permission"
    group = "group"


class UserPosition(str, Enum):
    student = "student"
    secretary = "secretary"
    department = "department"
    admin = "admin"


class Group(BaseModel):
    _id: str
    name: str
    type: GroupType
    description: Optional[str]
    permissions: list[UserPosition]
    # Doesn't contain members, and it's not necessary to contain it in the model
