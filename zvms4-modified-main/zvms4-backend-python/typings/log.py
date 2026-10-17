from fastapi import Request, Depends
from datetime import datetime

from db import backends
from util.object_id import authenticated_log_user


class ZVMSLog:
    url: str
    user: str
    data: str
    timestamp: float
    type: str
    level: str
    xuehai_id: str

    def __init__(
        self,
        url: str = "",
        user: str = "",
        data: str = "",
        timestamp: float | None = None,
        type: str = "",
        level: str = "info",
        xuehai_id: str = "",
    ):
        self.url = url
        self.user = user
        self.data = data
        self.timestamp = datetime.now().timestamp() if timestamp is None else timestamp
        self.type = type
        self.level = level
        self.xuehai_id = xuehai_id

    def model_dump(self) -> dict:
        return {
            "url": self.url,
            "user": self.user,
            "data": self.data,
            "timestamp": self.timestamp,
            "type": self.type,
            "level": self.level,
            "xuehai_id": self.xuehai_id,
        }

    def with_text(self, text: str, type: str = "", level: str = "") -> "ZVMSLog":
        self.data = text
        if type:
            self.type = type
        if level:
            self.level = level
        return self

    async def insert_log(self, session=None):
        repo = backends.get_applogs_repo(session) if session is not None else backends.get_applogs_repo()
        return await repo.insert(
            self.url,
            self.user,
            self.data,
            self.timestamp,
            self.type,
            self.level,
            self.xuehai_id,
        )


def xuehai_id_from(request: Request) -> str:
    value = (request.headers.get("Xuehai-Id") or "").strip()
    return value if value.isascii() and value.isdecimal() and len(value) <= 32 else ""


def inject_log(
    request: Request,
    user=Depends(authenticated_log_user),
):
    url = str(request.url)
    user = user["id"]
    data = ""
    timestamp = datetime.timestamp(datetime.now())
    xuehai_id = xuehai_id_from(request)
    return ZVMSLog(url, user, data, timestamp, xuehai_id=xuehai_id)
