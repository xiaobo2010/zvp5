"""FastAPI 实例与中间件装配（CORS / 盾牌 / 安全头 / 维护模式 / 耗时统计）。

本模块只负责两件事：构造 ``app``、按**固定顺序**装中间件。路由注册留在
``main``（那张 ``ROUTERS`` 表），因为「先注册先匹配」本身是业务契约。

顺序即契约：``add_middleware`` / ``@app.middleware("http")`` 都是「后注册的更靠
外层」，所以 :func:`create_app` 里六次注册的先后决定了 418（盾牌）/ 503（维护）/
CORS 补头 / 耗时统计的组合行为。动之前先读那一大段注释。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from urllib.parse import urlsplit

from util.env import getenv

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import bindings
import realtime
from db.system_state import is_enabled_async as maintenance_enabled
from db.system_state import is_enabled_fresh_async as maintenance_enabled_fresh
from services import scanner_decoy
from services.api_metrics import ApiMetricsMiddleware
from typings.log import ZVMSLog, client_ip_from, user_agent_from, xuehai_id_from
from util.object_id import is_admin_token

_logger = logging.getLogger(__name__)

# 本地开发固定放行；生产页面 Origin 由部署环境提供，不把域名写进仓库。
DEFAULT_CORS_ALLOWED_ORIGINS = (
    "http://localhost:4173",
    "http://localhost:5173",
)


def cors_allowed_origins() -> list[str]:
    """Return local development origins plus deploy-configured origins.

    ``ZVP5_CORS_ALLOWED_ORIGINS`` is a comma-separated list of exact browser
    origins. Wildcards are rejected because credentialed CORS requires an
    explicit origin echo.
    """

    configured = getenv("ZVP5_CORS_ALLOWED_ORIGINS", "")
    origins = list(DEFAULT_CORS_ALLOWED_ORIGINS)
    for raw_origin in configured.split(","):
        origin = raw_origin.strip().rstrip("/")
        if not origin:
            continue
        parsed = urlsplit(origin)
        if (
            origin == "*"
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError(
                "ZVP5_CORS_ALLOWED_ORIGINS must contain comma-separated "
                f"http(s) origins, got {raw_origin.strip()!r}"
            )
        if origin not in origins:
            origins.append(origin)
    return origins


SEC_PATTERN = re.compile(
    "|".join(
        [
            r"[^\w]env",
            r"\.\.",
            r"/\.",
            r"\.sql",
            r"\.bak",
            r"api/chat",
            r"api/network",
            r"api/file",
            r"assets/%",
            r"swagger",
            r"doc",
            r"php",
            r"\.py",
            r"openapi",
        ]
    )
)

allowed_methods = ["GET", "POST", "DELETE", "PUT", "PATCH", "HEAD", "OPTIONS"]

# OpenAI SDK 的 UA 是 ``OpenAI/Python x.y``，不含 Mozilla。AI 兼容面如果照旧吃
# 418，SDK 客户端一个请求都发不出去。只放开这一条前缀，且只放开 UA 判断：
# SEC_PATTERN 与 method 检查原样保留，鉴权仍走 Authorization: Bearer。
AI_OPENAI_PREFIX = "/api/ai/v1/"

# 维护模式放行清单：登录、证书、版本、健康检查、首页，以及整个 /api/system
# 前缀（管理员必须能在维护期间关掉维护模式）。
# 注意：users 路由本轮已迁到 ``/api/v1/users``（见 main.ROUTERS），旧前缀串
# 保留是为了兼容外部仍在打旧路径的重定向/探活，两个前缀必须同时放行。
MAINTENANCE_EXEMPT_PATHS = {
    "/api",
    "/api/",
    "/api/users/auth",
    "/api/users/auth/logout",
    "/api/v1/users/auth",
    "/api/v1/users/auth/logout",
    "/api/cert",
    "/api/version",
    "/api/health",
}
MAINTENANCE_EXEMPT_PREFIXES = ("/api/system",)

# 异常 UA / 可疑路径的审计写入：外部探针能把它变成写放大，所以限并发、放后台。
SECURITY_AUDIT_CONCURRENCY = 8

_security_audit_semaphore = asyncio.Semaphore(SECURITY_AUDIT_CONCURRENCY)
_security_audit_tasks: set[asyncio.Task] = set()


async def _write_security_audit(log: ZVMSLog) -> None:
    try:
        async with _security_audit_semaphore:
            await log.insert_log()
    except Exception as exc:  # noqa: BLE001 - 审计失败不改变 418 结论
        _logger.warning("security alert audit failed: %s", type(exc).__name__)


def _spawn_security_audit(log: ZVMSLog) -> None:
    task = asyncio.create_task(_write_security_audit(log))
    _security_audit_tasks.add(task)
    task.add_done_callback(_security_audit_tasks.discard)


async def maintenance_middleware(request: Request, call_next):
    """维护模式闸门：/api/ 下除白名单外一律 503（admin token 放行）。

    维护开启期间 admin 全域放行：从 ``Authorization: Bearer`` 取 token，走
    ``util.object_id.is_admin_token``（与 get_current_user 同一条认证路径，含
    banned / session 校验，``per`` 取库里的当前 roles）。
    门控是「缓存 + fresh」两级：缓存快路径（2s）挡住绝大多数请求，fresh 再复核一次
    兜「缓存陈旧」的窗口。fresh 自己带 1s 短缓存（db.system_state.FRESH_CACHE_TTL），
    所以准入路径最多每秒一次 SQLite 读，而不是每请求一次；本进程 enable/disable 经
    set_state → invalidate_cache() 两级缓存即刻失效。跨进程 enable 最多 2s 陈旧放行
    （与纯缓存的既有行为一致），跨进程 disable 最多 1s 内被 fresh 纠正。

    两个开关走 :mod:`bindings` 取：测试在 ``main`` 上打桩它们，打桩必须当场生效。
    """

    path = request.url.path
    if (
        path.startswith("/api/")
        and str(request.method).upper() != "OPTIONS"  # 放行 CORS 预检
        and path not in MAINTENANCE_EXEMPT_PATHS
        and not path.startswith(MAINTENANCE_EXEMPT_PREFIXES)
        and await bindings.get("maintenance_enabled", maintenance_enabled)()
        and await bindings.get("maintenance_enabled_fresh", maintenance_enabled_fresh)()
        and not await is_admin_token(request.headers.get("Authorization"))
    ):
        return JSONResponse(
            status_code=503,
            content={"detail": "系统维护中，请稍后再试", "maintenance": True},
        )
    return await call_next(request)


SECURITY_RESPONSE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Strict-Transport-Security": "max-age=31536000",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
        "font-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'none'"
    ),
}


async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_RESPONSE_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


SHIELD_BLOCKED_MESSAGE = "您的请求被服务器判定为非法访问。已经记录您的IP和UA数据，管理员将进行安全追责。"


def _decoy_audit_payload(
    request: Request, *, reason: str, match: str | None
) -> dict:
    """诱捕日志只记请求侧证据，不把 Authorization/Cookie 写进日志。"""
    headers: dict[str, str] = {}
    for name, value in request.headers.items():
        if name.lower() in {"authorization", "cookie", "set-cookie", "proxy-authorization"}:
            continue
        if len(headers) >= 32:
            break
        headers[name] = value[:512]
    return {
        "reason": reason,
        "match": match,
        "path": request.url.path,
        "query": request.url.query,
        "method": request.method,
        "referer": request.headers.get("Referer"),
        "origin": request.headers.get("Origin"),
        "xff": request.headers.get("X-Forwarded-For"),
        "content_type": request.headers.get("Content-Type"),
        "content_length": request.headers.get("Content-Length"),
        "accept": request.headers.get("Accept"),
        "accept_language": request.headers.get("Accept-Language"),
        "headers": headers,
    }


def _audit_scanner_decoy(
    request: Request, *, reason: str, match: str | None = None
) -> None:
    """把命中诱饵的请求写进安全日志（后台写，不拖响应）。"""
    try:
        log_class = bindings.get("ZVMSLog", ZVMSLog)
        _spawn_security_audit(
            log_class(
                url=request.url.path,
                user="",
                data=json.dumps(
                    _decoy_audit_payload(request, reason=reason, match=match),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                timestamp=datetime.now().timestamp(),
                type="security_alert",
                level="warning",
                xuehai_id=xuehai_id_from(request),
                client_ip=client_ip_from(request),
                user_agent=user_agent_from(request),
            )
        )
    except Exception as exc:  # noqa: BLE001 - 日志失败不能影响诱饵响应
        _logger.warning("scanner decoy audit failed: %s", type(exc).__name__)


async def shield_middleware(request: Request, call_next):
    """非浏览器 UA / 可疑路径 / 非法方法 → 418，并异步落后台审计。

    最外层先看「未登录目录扫描诱饵」：命中字典的匿名 GET/HEAD 直接回 200，
    不进入 418 分支。旧盾牌的 ``SEC_PATTERN`` 触发条件也并入诱饵：匿名 GET/HEAD
    命中可疑路径时同样回 200 并记安全日志；UA / 方法检查仍保留 418 兜底。
    诱饵只作用于 canary 根站，Pro 的 ``/themes/`` 请求放过。

    审计用的日志类走 :mod:`bindings` 取（测试在 ``main.ZVMSLog`` 上打桩）。
    """

    pattern_hit = SEC_PATTERN.search(request.url.path)
    bad_ua = (
        "Mozilla" not in request.headers.get("User-Agent", "")
        and not request.url.path.startswith(AI_OPENAI_PREFIX)
    )
    bad_method = str(request.method).upper() not in allowed_methods
    if scanner_decoy.should_decoy(request, shield_hit=bool(pattern_hit)):
        reason = (
            "scanner_decoy"
            if scanner_decoy.is_scanner_path(request.url.path)
            else "path_pattern"
        )
        _audit_scanner_decoy(
            request,
            reason=reason,
            match=pattern_hit.group(0)[:64] if pattern_hit else None,
        )
        return scanner_decoy.decoy_response()

    if pattern_hit or bad_ua or bad_method:
        payload = {
            "reason": (
                "path_pattern"
                if pattern_hit
                else ("user_agent" if bad_ua else "method")
            ),
            "match": pattern_hit.group(0)[:64] if pattern_hit else None,
            "method": request.method,
            "referer": request.headers.get("Referer"),
            "origin": request.headers.get("Origin"),
            "xff": request.headers.get("X-Forwarded-For"),
            "content_length": request.headers.get("Content-Length"),
            "accept_language": request.headers.get("Accept-Language"),
        }
        log_class = bindings.get("ZVMSLog", ZVMSLog)
        _spawn_security_audit(
            log_class(
                url=request.url.path,
                user="",
                data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                timestamp=datetime.now().timestamp(),
                type="security_alert",
                level="warning",
                xuehai_id=xuehai_id_from(request),
                client_ip=client_ip_from(request),
                user_agent=user_agent_from(request),
            )
        )
        return JSONResponse(status_code=418, content=SHIELD_BLOCKED_MESSAGE)
    return await call_next(request)


def create_app(
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    """构造 app 并按「顺序即契约」装好全部中间件。"""

    app = FastAPI(
        default_response_class=JSONResponse,
        docs_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    # 维护模式对 socket.io 的准入：socket.io 挂在与本中间件栈不同的 ASGI 子应用上
    # （见 main 里的 app.mount("/socket.io", socket)），/api/ 那层中间件管不到它，
    # 所以单独加一层纯 ASGI 中间件在握手阶段拒绝新连接。
    # 注册顺序：本行在 CORSMiddleware 之前注册 ⇒ 中间件栈里它在 CORS 内侧（503 会
    # 被 CORS 补上响应头）；shield_middleware 最后注册，仍在最外层（非 Mozilla UA
    # 先吃 418）。
    app.add_middleware(realtime.MaintenanceSocketGuard)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_allowed_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # 封禁拒绝是 403，原因/到期走响应头（见 util/ban.py）。浏览器的 JS 默认读
        # 不到自定义响应头，跨域本地调试会丢原因；显式暴露后前端才能提示用户。
        expose_headers=[
            "X-Ban-Reason",
            "X-Ban-Until",
            "Retry-After",
            "X-AI-Request-Id",
            "X-AI-Quota-Used",
            "X-AI-Quota-Limit",
        ],
    )

    # 端点耗时统计：纯 ASGI 中间件，注册位置刻意夹在 CORSMiddleware 与
    # maintenance_middleware 之间（add_middleware 后注册者更靠外层）。这样它位于维护
    # 中间件**内侧** —— 被维护模式 503 挡下的请求不会进入统计（它们没有执行任何
    # 路由），而 shield 仍然是最外层。它只记录真正匹配到路由模板的请求（§5.4）。
    app.add_middleware(ApiMetricsMiddleware)

    # 顺序至关重要：add_middleware 是「后注册的更外层」，下面三行的注册次序决定
    # 了栈的自外向内顺序 shield → security_headers → maintenance。
    # * security_headers 在 shield 内侧：418 用自己的响应体，不参与补头；
    # * maintenance 在 security_headers 内侧：维护 503 会带上基线安全头；
    # * maintenance 在 ApiMetrics 外侧：被 503 挡下的请求不计入耗时统计。
    app.middleware("http")(maintenance_middleware)
    app.middleware("http")(security_headers_middleware)
    app.middleware("http")(shield_middleware)

    return app
