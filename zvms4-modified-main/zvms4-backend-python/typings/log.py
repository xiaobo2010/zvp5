from fastapi import Request, Depends
from datetime import datetime

from db import backends
from util.object_id import authenticated_log_user, log_level_floor


#: ``APP_LOGS.USER_AGENT`` 的列宽（``VARCHAR2(256 CHAR)``）。
USER_AGENT_MAX_LEN = 256

#: ``APP_LOGS.CLIENT_IP`` 的列宽；45 是 IPv6 文本最长形式的长度。
CLIENT_IP_MAX_LEN = 45


class ZVMSLog:
    url: str
    user: str
    data: str
    timestamp: float
    type: str
    level: str
    xuehai_id: str
    client_ip: str
    user_agent: str

    def __init__(
        self,
        url: str = "",
        user: str = "",
        data: str = "",
        timestamp: float | None = None,
        type: str = "",
        level: str = "info",
        xuehai_id: str = "",
        client_ip: str = "",
        user_agent: str = "",
    ):
        self.url = url
        self.user = user
        self.data = data
        self.timestamp = datetime.now().timestamp() if timestamp is None else timestamp
        self.type = type
        self.level = level
        self.xuehai_id = xuehai_id
        self.client_ip = client_ip
        self.user_agent = user_agent

    def model_dump(self) -> dict:
        return {
            "url": self.url,
            "user": self.user,
            "data": self.data,
            "timestamp": self.timestamp,
            "type": self.type,
            "level": self.level,
            "xuehai_id": self.xuehai_id,
            "client_ip": self.client_ip,
            "user_agent": self.user_agent,
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
            self.client_ip,
            self.user_agent,
        )


def xuehai_id_from(request: Request) -> str:
    value = (request.headers.get("Xuehai-Id") or "").strip()
    return value if value.isascii() and value.isdecimal() and len(value) <= 32 else ""


def client_ip_from(request: Request) -> str:
    """请求来源 IP（字符串）；取不到时返回空串。

    生产在 nginx 后面（uvicorn 只听 ``127.0.0.1:8018``），``request.client.host``
    永远是回环地址，真实来源只在 ``X-Forwarded-For`` 里。取链条上**第一个非空
    段**：那是 nginx 侧记下的原始客户端，后面的段是各级代理。没有该头（直连/
    测试）才回落到 socket 对端地址。

    返回值只用于审计展示，不做任何信任判定——转发头可以被客户端伪造，但「谁
    声称自己从哪来」本身就是需要留痕的事实。
    """
    forwarded = request.headers.get("X-Forwarded-For") or ""
    for part in forwarded.split(","):
        candidate = part.strip()
        if candidate:
            return candidate[:CLIENT_IP_MAX_LEN]
    peer = getattr(request, "client", None)
    host = getattr(peer, "host", "") if peer is not None else ""
    return (host or "")[:CLIENT_IP_MAX_LEN]


def user_agent_from(request: Request) -> str:
    """请求 UA（截断到列宽）；缺失时返回空串。"""
    return (request.headers.get("User-Agent") or "").strip()[:USER_AGENT_MAX_LEN]


def inject_log(
    request: Request,
    user=Depends(authenticated_log_user),
):
    url = str(request.url)
    user = user["id"]
    data = ""
    timestamp = datetime.timestamp(datetime.now())
    xuehai_id = xuehai_id_from(request)
    return ZVMSLog(
        url,
        user,
        data,
        timestamp,
        xuehai_id=xuehai_id,
        client_ip=client_ip_from(request),
        user_agent=user_agent_from(request),
    )
