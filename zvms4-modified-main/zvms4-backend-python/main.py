from fastapi import Request, FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
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
)
import socketio
from fastapi.middleware.cors import CORSMiddleware
from db import backends
from db.ids import InvalidOid
from db.oracle.engine import ping as oracle_ping
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from tasks.compute_time import compute_tasks, generate_reports

scheduler = AsyncIOScheduler()
sio = socketio.AsyncServer(async_mode="asgi")
socket = socketio.ASGIApp(sio)

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

    # Schedule compute_time to run daily at 00:00 HKT (UTC+8)
    scheduler.add_job(
        compute_tasks,
        "cron",
        hour=1,
        minute=0,
        second=0,
        timezone="Asia/Hong_Kong",
        id="daily_compute_time",
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
        await oracle_ping()
        return {"status": "ok", "code": 200, "data": "OK"}
    except Exception as e:
        return {"status": "error", "code": 500, "data": str(e)}


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
    return JSONResponse(
        content={"detail": "An internal server error occurred: %s" % type(exc).__name__}, status_code=500
    )


@app.get("/api/version")
async def get_version():
    return {"status": "ok", "code": 200, "data": await current_version()}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app=app, host="0.0.0.0", port=8000)
