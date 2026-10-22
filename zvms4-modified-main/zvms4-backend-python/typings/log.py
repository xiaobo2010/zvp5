from fastapi import Request, Depends
from datetime import datetime

from db import backends
from util.object_id import authenticated_log_user, log_level_floor


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

    def effective_level(self) -> str:
        """应用请求级级别下限（临时密码会话内的 info 提升为 warning）。

        「提升」而不是「覆盖」：调用方显式写了 ``warning``/``error`` 的必须原样
        保留，下限只负责把**看起来正常**的 info 记录拉进安全审计的视野。
        空级别（``ZVMSLog`` 允许传 ``""``）按未指定处理，同样受下限约束——
        否则随手省略 level 的日志就能绕过这条规则。
        """
        floor = log_level_floor.get()
        if floor == "warning" and self.level in ("", "info"):
            return "warning"
        return self.level

    async def insert_log(self, session=None):
        repo = backends.get_applogs_repo(session) if session is not None else backends.get_applogs_repo()
        return await repo.insert(
            self.url,
            self.user,
            self.data,
            self.timestamp,
            self.type,
            self.effective_level(),
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
