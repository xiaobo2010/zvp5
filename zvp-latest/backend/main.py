import sys
import datetime as dt

ts = dt.datetime.now().timestamp()
logfile = sys.stderr = open(f'./logs/zvms.{ts}.err.log', 'a')

import traceback as tb
from fastapi import Request, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse
from pymongo.errors import OperationFailure
import signal
from fastapi.staticfiles import StaticFiles
import os
from routers import (
    users_router,
    groups_router,
    exports_router,
    imports_router,
    logs_router,
    activities_v2_router,
    users_v2_router,
    users_v3_router,
    groups_v2_router,
    groups_v3_router,
    activities_v3_router,
    xh_router,
)
import routers
from database import close_mongo_connection, connect_to_mongo, db
from fastapi.middleware.cors import CORSMiddleware
import asyncio
from contextlib import asynccontextmanager
import re
from util.log import APILogger
from bson import ObjectId

@asynccontextmanager
async def lifespan(_: FastAPI):
    await connect_to_mongo()
    try:
        yield
    finally:
        await close_mongo_connection()


app = FastAPI(default_response_class=JSONResponse, docs_url=None, lifespan=lifespan)

@app.get('/dev')
@app.get('/stats0.html')
def _test():
    return FileResponse('./test.html')

SEC_PATTERN = re.compile("|".join([
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
    r"openapi"
]))

allowed_methods = [
    "GET","POST","DELETE","PUT","PATCH","HEAD","OPTIONS"
]

@app.middleware('http')
async def shield_middleware(request: Request, call_next):
    path = request.url.path
    request.state.zvp_session = request.cookies.get('zvp_session', None) 
    if request.state.zvp_session is None:
        request.state.zvp_session = str(ObjectId())
    if SEC_PATTERN.search(path) or 'Mozilla' not in request.headers.get('User-Agent','') and 'com.xh' not in request.headers.get('User-Agent','') or str(request.method).upper() not in allowed_methods:
        print(
            f'*** SECURITY ALERT {dt.datetime.now()} ***',
            f'{request.method = }',
            f'{request.url = }',
            f'{request.client.host = }',
            f'{request.headers.get("Host",None) = }',
            f'{request.headers.get("User-Agent",None) = }',
            f'{request.headers.get("X-Forwarded-For",None) = }',
            f'{dict(request.headers) = }',
            f'{(await request.body()) = }',
            sep='\n',
            end='\n\n',
            file=logfile
        )
        logfile.flush()
        is_violate = request.headers.get('X-Requested-With', None) in ('com.xh.browser', 'com.xh.datacenter')
        await APILogger(request).log(
            '系统检测到违规使用平板行为' if is_violate else '系统检测到异常请求',
            level='warning',
            type='rule_violation' if is_violate else 'security_alert'
        )
        response = JSONResponse(
            status_code=418,
            content="黑客我艹你M，没门"
        )
    else:
        response = await call_next(request)
    response.set_cookie(
        key='zvp_session',
        value=request.state.zvp_session,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=5184000,
        path='/',
    )
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routes
app.include_router(users_router.router, prefix="/api/users")
app.include_router(groups_router.router, prefix="/api/groups")
app.include_router(exports_router.router, prefix="/api/exports")
app.include_router(imports_router.router, prefix="/api/imports")
app.include_router(logs_router.router, prefix="/api/logs")
app.include_router(activities_v2_router.router, prefix="/api/v2/activities")
app.include_router(users_v2_router.router, prefix="/api/v2/users")
app.include_router(users_v3_router.router, prefix="/api/v3/users")
app.include_router(groups_v2_router.router, prefix="/api/v2/groups")
app.include_router(groups_v3_router.router, prefix="/api/v3/groups")
app.include_router(activities_v3_router.router, prefix="/api/v3/activities")
app.include_router(xh_router.app, prefix="/xh")
app.include_router(routers.v1.platform.api, prefix="/api/v1/platform")

@app.get("/api/health")
async def health_check():
    """Health check endpoint."""
    try:
        # Check MongoDB connection
        await db.zvms.command("ping")
        return {"code": 200, "status": "UP", "mongo": {"status": "UP"}, "data": "服务器正常运行"}
    except Exception as e:
        return {"status": "error", "code": 500, "data": str(e)}


@app.get("/api/cert")
async def get_cert():
    raise HTTPException(status_code=410, detail='请更新平台')


@app.exception_handler(ValidationError)
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: ValidationError | RequestValidationError):
    """Convert Pydantic errors to a readable string for frontend display."""
    errors = exc.errors()
    formatted_errors = []

    for error in errors:
        field = " → ".join(map(str, error["loc"])) 
        message = error["msg"]
        formatted_errors.append(f"{field}: {message}")

    error_string = "\n".join(formatted_errors)
    return JSONResponse(content={"detail": error_string}, status_code=422)


@app.exception_handler(Exception)
async def generic_exception_handler(_: Request, exc: Exception):
    """Catch-all exception handler to return a generic error message."""
    return JSONResponse(
        content={"detail": "An internal server error occurred:\n%s" % ''.join(tb.format_exception_only(exc)) }, status_code=500
    )


@app.exception_handler(OperationFailure)
async def operation_failure_exception_handler(_: Request, exc: OperationFailure):
    """Catch-all exception handler to return a generic error message."""
    return JSONResponse(content={"detail": exc.details["errmsg"]}, status_code=400)


app.mount("/", StaticFiles(directory="../frontend/dist", html=True), name="static")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app=app, host="0.0.0.0", port=os.environ.get('ZVP_PORT', 8008))

