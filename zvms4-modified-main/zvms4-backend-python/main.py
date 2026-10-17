from fastapi import Request, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
import logging
import re
from datetime import datetime
from typings.log import ZVMSLog, xuehai_id_from
from routers import (
    users_router,
    groups_router,
    exports_router,
    imports_router,
    logs_router,
    activities_v2_router,
    users_v2_router,
    groups_v2_router,
    activity_statistics_router,
    times_router,
    mcp_router,
    activities_v3_router,
    users_v3_router,
    groups_v3_router,
    changelog_router,
    attachments_router,
    db_system_router,
)
import socketio
from socketio.exceptions import ConnectionRefusedError as SocketIOConnectionRefusedError
from fastapi.middleware.cors import CORSMiddleware
from db import backends
from db.ids import InvalidOid
from db.system_state import is_enabled as maintenance_enabled
from db.system_state import is_enabled_fresh as maintenance_enabled_fresh
import realtime
from routers.system_router import router as system_router
from db.oracle.engine import ping as oracle_ping
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from tasks.compute_time import rebuild_statistics, generate_reports

scheduler = AsyncIOScheduler()
_logger = logging.getLogger(__name__)
sio = socketio.AsyncServer(async_mode="asgi")
socket = socketio.ASGIApp(sio)

# 登记给 routers 用（开启维护模式时要断开所有实时连接），避免 router 反向
# import main 造成循环导入。
realtime.register_server(sio)

app = FastAPI(default_response_class=JSONResponse, docs_url=None)

# Neutral value returned when the version cannot be resolved from the database.
# Deliberately not a version literal: the CHANGELOG table is the only source of
# truth for the current version, so there is nothing here that can drift.
VERSION_UNKNOWN = "unknown"


async def current_version() -> str:
    """Return the current version, read from the ``CHANGELOG`` table.

    ``ChangelogRepo.list_all()`` is ordered by ``date`` desc, ``version`` desc,
    so the first entry is the latest release and thus the current version.

    Never raises: an empty table or a failing lookup yields
    :data:`VERSION_UNKNOWN`, keeping ``/api/`` and ``/api/version`` at 200.
    """
    try:
        entries = await backends.get_changelog_repo().list_all()
    except Exception as e:
        print(f"Failed to read version from changelog: {e}")
        return VERSION_UNKNOWN
    if not entries:
        return VERSION_UNKNOWN
    version = entries[0].get("version")
    return str(version) if version else VERSION_UNKNOWN


# 维护模式对 socket.io 的准入：socket.io 挂在与本中间件栈不同的 ASGI 子应用上
# （见文件末尾的 app.mount("/socket.io", socket)），/api/ 那层中间件管不到它，
# 所以单独加一层纯 ASGI 中间件在握手阶段拒绝新连接。
# 注册顺序：本行在 CORSMiddleware 之前注册 ⇒ 中间件栈里它在 CORS 内侧（503 会
# 被 CORS 补上响应头）；shield_middleware 最后注册，仍在最外层（非 Mozilla UA
# 先吃 418）。
app.add_middleware(realtime.MaintenanceSocketGuard)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:4173",
        "http://localhost:5173",
        "https://v4.zvms.site",
        "https://v4-netlify.zvms.site",
        "https://main--zvms.netlify.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

# 维护模式放行清单：登录、证书、版本、健康检查、首页，以及整个 /api/system
# 前缀（管理员必须能在维护期间关掉维护模式）。
MAINTENANCE_EXEMPT_PATHS = {
    "/api",
    "/api/",
    "/api/users/auth",
    "/api/cert",
    "/api/version",
    "/api/health",
}
MAINTENANCE_EXEMPT_PREFIXES = ("/api/system",)


# 顺序至关重要：add_middleware 是「后注册的更外层」，本中间件定义在
# shield_middleware 之前，保证盾牌仍在最外层（非 Mozilla UA 仍先吃 418）。
@app.middleware("http")
async def maintenance_middleware(request: Request, call_next):
    path = request.url.path
    if (
        path.startswith("/api/")
        and str(request.method).upper() != "OPTIONS"  # 放行 CORS 预检
        and path not in MAINTENANCE_EXEMPT_PATHS
        and not path.startswith(MAINTENANCE_EXEMPT_PREFIXES)
        and maintenance_enabled()
    ):
        return JSONResponse(
            status_code=503,
            content={"detail": "系统维护中，请稍后再试", "maintenance": True},
        )
    return await call_next(request)


@app.middleware("http")
async def shield_middleware(request: Request, call_next):
    if (
        SEC_PATTERN.search(request.url.path)
        or "Mozilla" not in request.headers.get("User-Agent", "")
        or str(request.method).upper() not in allowed_methods
    ):
        try:
            await ZVMSLog(
                url=str(request.url),
                user="",
                data="系统检测到异常请求",
                timestamp=datetime.now().timestamp(),
                type="security_alert",
                level="warning",
                xuehai_id=xuehai_id_from(request),
            ).insert_log()
        except Exception as e:
            print(f"Failed to write security log: {e}")
        return JSONResponse(status_code=418, content="请求已被拦截")
    return await call_next(request)


app.mount("/socket.io", socket)


@sio.event
async def connect(sid, environ):
    # 兜底：正常路径上 MaintenanceSocketGuard 已在握手阶段拒掉新连接（并且
    # GET /api/system/maintenance 已放行），这里再查一次**绕过缓存**的最新状态，
    # 万一有入口绕过那层中间件也不会漏进连接。
    if maintenance_enabled_fresh():
        print(f"refuse connect {sid} (maintenance)")
        raise SocketIOConnectionRefusedError("系统维护中，请稍后再试")
    print(f"connect {sid}")


@sio.event
async def disconnect(sid):
    print(f"disconnect {sid}")


async def mark_all_tasks_failed():
    await backends.get_tasks_repo().fail_pending("Program interrupted unexpectedly")


async def startup_event():
    """Startup event handler to initialize the application."""
    print("Starting up...")
    scheduler.start()

    # Rebuild the derived volunteer-time statistics daily at 01:00 HKT (UTC+8).
    scheduler.add_job(
        rebuild_statistics,
        "cron",
        hour=1,
        minute=0,
        second=0,
        timezone="Asia/Hong_Kong",
        id="daily_compute_time",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # Schedule compute_time to run daily at 00:00 HKT (UTC+8)
    scheduler.add_job(
        generate_reports,
        "cron",
        hour=4,
        minute=0,
        second=0,
        timezone="Asia/Hong_Kong",
        id="daily_generate_reports",
    )

    print("Application started.")


async def shutdown_event():
    """Shutdown event handler to clean up resources."""
    print("Shutting down...")
    await mark_all_tasks_failed()
    scheduler.shutdown()
    print("Application shut down.")


# Register events
app.add_event_handler("startup", startup_event)
app.add_event_handler("shutdown", shutdown_event)

# Register routes
app.include_router(users_router.router, prefix="/api/users", tags=["users"])
app.include_router(groups_router.router, prefix="/api/groups", tags=["groups"])
app.include_router(exports_router.router, prefix="/api/exports", tags=["exports"])
app.include_router(imports_router.router, prefix="/api/imports", tags=["imports"])
app.include_router(logs_router.router, prefix="/api/logs", tags=["logs"])

app.include_router(activities_v2_router.router, prefix="/api/v2/activities")
app.include_router(users_v2_router.router, prefix="/api/v2/users")
app.include_router(groups_v2_router.router, prefix="/api/v2/groups")
app.include_router(times_router.router, prefix="/api/v2/times")
app.include_router(
    activity_statistics_router.router, prefix="/api/v2/statistics/activities"
)
app.include_router(mcp_router.router, prefix="/api/v2/mcp")
app.include_router(activities_v3_router.router, prefix="/api/v3/activities")
app.include_router(users_v3_router.router, prefix="/api/v3/users")
app.include_router(groups_v3_router.router, prefix="/api/v3/groups")
app.include_router(
    changelog_router.router, prefix="/api/changelog", tags=["changelog"]
)
app.include_router(
    attachments_router.router, prefix="/api/attachments", tags=["attachments"]
)
app.include_router(system_router, prefix="/api/system/maintenance", tags=["System"])
app.include_router(db_system_router.router, prefix="/api/system/db", tags=["System"])


@app.get("/api/")
async def home():
    return {
        "status": "ok",
        "code": 200,
        "data": {
            "message": "Welcome to ZVMS API",
            "version": await current_version(),
            "author": "ZZDev",
            "license": "MIT",
            "source": "https://github.com/zvms/zvms4-backend-python.git",
            "apis": {
                "user": "/api/users",
                "activity": "/api/activities",
                "group": "/api/groups",
                "exports": "/api/exports",
                "imports": "/api/imports",
                "logs": "/api/logs",
            },
        },
    }


# Redirect singular routes to plural routes
@app.api_route("/api/user/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def redirect_user(request: Request, path: str):
    new_url = request.url.replace(path="/api/users/" + path)
    return RedirectResponse(url=new_url)


@app.api_route("/api/activity/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def redirect_activity(request: Request, path: str):
    new_url = request.url.replace(path="/api/activities/" + path)
    return RedirectResponse(url=new_url)


@app.api_route("/api/group/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def redirect_group(request: Request, path: str):
    new_url = request.url.replace(path="/api/groups/" + path)
    return RedirectResponse(url=new_url)


@app.api_route("/api/export/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def redirect_export(request: Request, path: str):
    new_url = request.url.replace(path="/api/exports/" + path)
    return RedirectResponse(url=new_url)


@app.api_route("/api/import/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def redirect_import(request: Request, path: str):
    new_url = request.url.replace(path="/api/imports/" + path)
    return RedirectResponse(url=new_url)


@app.api_route("/api/log/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def redirect_log(request: Request, path: str):
    new_url = request.url.replace(path="/api/logs/" + path)
    return RedirectResponse(url=new_url)


@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    try:
        # ADB is the only backend: always verify DB connectivity.
        if not await oracle_ping():
            raise RuntimeError("database ping returned false")
        return {"status": "ok", "code": 200, "data": "OK"}
    except Exception:
        _logger.exception("Health check failed")
        return JSONResponse(
            status_code=503,
            content={"status": "error", "code": 503, "data": "Service unavailable"},
        )


@app.get("/api/cert")
async def get_cert():
    return {
        "status": "ok",
        "code": 200,
        "data": open("./rsa_public_key.pem", "r").read(),
    }


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError):
    """Convert Pydantic errors to a readable string for frontend display."""
    errors = exc.errors()
    formatted_errors = []

    for error in errors:
        field = " → ".join(map(str, error["loc"]))  # Format location as "body → field"
        message = error["msg"]
        formatted_errors.append(f"{field}: {message}")

    error_string = "\n".join(formatted_errors)  # Combine into a single string
    return JSONResponse(content={"detail": error_string}, status_code=422)


@app.exception_handler(InvalidOid)
async def invalid_oid_exception_handler(_: Request, exc: InvalidOid):
    """Map a malformed id caught at the data boundary to HTTP 400.

    Repositories normalize ids via ``db.ids`` at the data boundary; without
    this handler an ``InvalidOid`` escaping a route (e.g. a business id where
    an object id was expected) would fall through to the generic 500 handler.
    """
    return JSONResponse(content={"detail": "Invalid Object ID"}, status_code=400)


@app.exception_handler(Exception)
async def generic_exception_handler(_: Request, exc: Exception):
    """Catch-all exception handler to return a generic error message."""
    _logger.exception("Unhandled request exception", exc_info=exc)
    return JSONResponse(
        content={"detail": "An internal server error occurred"}, status_code=500
    )


@app.get("/api/version")
async def get_version():
    return {"status": "ok", "code": 200, "data": await current_version()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app=app, host="0.0.0.0", port=8000)
