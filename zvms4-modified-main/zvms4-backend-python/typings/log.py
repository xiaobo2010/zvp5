from fastapi import Request, Depends
from datetime import datetime

from database import db
from util.object_id import get_current_user


class ZVMSLog:
    url: str
    user: str
    data: str
    timestamp: float
    type: str
    level: str

    def __init__(
        self,
        url: str = "",
        user: str = "",
        data: str = "",
        timestamp: float = datetime.now().timestamp(),
        type: str = "",
        level: str = "info",
    ):
        self.url = url
        self.user = user
        self.data = data
        self.timestamp = timestamp
        self.type = type
        self.level = level

    def model_dump(self) -> dict:
        return {
            "url": self.url,
            "user": self.user,
            "data": self.data,
            "timestamp": self.timestamp,
            "type": self.type,
            "level": self.level,
        }

    def with_text(self, text: str, type: str = "", level: str = "") -> "ZVMSLog":
        self.data = text
        if type:
            self.type = type
        if level:
            self.level = level
        return self

    async def insert_log(self):
        return await db.zvms.logs.insert_one(self.model_dump())


def inject_log(
    request: Request,
    user=Depends(get_current_user),
):
    url = str(request.url)
    user = user["id"]
    data = ""
    timestamp = datetime.timestamp(datetime.now())
    return ZVMSLog(url, user, data, timestamp)