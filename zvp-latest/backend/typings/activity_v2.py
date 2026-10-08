from datetime import datetime
from pydantic import BaseModel
from typing import Literal


class Activity(BaseModel):
    _id: str
    type: Literal["on-campus", "off-campus", "social-practice", "hybrid"]
    name: str
    description: str
    date: int # datetime  # ISO 8601
    createdAt: int # datetime  # ISO 8601
    updatedAt: int # datetime  # ISO 8601
    appointee: str | None # ID of the responsible user
    approver: str | None  # ID or literal "authority" | "member"
    creator: str
    status: Literal["pending", "effective", "refused"]
    place: str | None
    origin: str | None


class ActivityMember(BaseModel):
    _id: str
    member: str
    activity: str
    status: Literal["effective", "refused", "pending"] = "effective"
    mode: Literal["on-campus", "off-campus", "social-practice"]
    duration: float
