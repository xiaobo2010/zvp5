from fastapi import Request, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
import asyncio
import json
import logging
import os
import re
import threading
from datetime import datetime
from typings.log import ZVMSLog, client_ip_from, user_agent_from, xuehai_id_from
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
    db_metrics_router,
    system_ssh_router,
    system_files_router,
    system_db_content_router,
    ban_router,
    notifications_router,
    ban_violations_router,
    ai_router,
    ai_quota_requests_router,
    ai_api_keys_router,
    ai_agent_router,
    ai_conversations_router,
    ai_scenes_router,
    ai_schedule_router,
    system_settings_router,
    system_role_policies_router,
)
import socketio
from socketio.exceptions import ConnectionRefusedError as SocketIOConnectionRefusedError
from fastapi.middleware.cors import CORSMiddleware
from db import backends
from db.ids import InvalidOid
from db.system_state import is_enabled_async as maintenance_enabled
from db.system_state import is_enabled_fresh_async as maintenance_enabled_fresh
import realtime
from routers.system_router import router as system_router
from db.oracle.engine import ping as oracle_ping
from util.object_id import _authenticate, is_admin_token
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from tasks.compute_time import rebuild_statistics, generate_reports
from tasks.files_retention import purge_expired_attachments
from tasks.ocr_upload_gc import run_ocr_upload_gc
from tasks.retention import purge_expired_records
from tasks.user_trash_gc import purge_expired_trash
from services import perf_sampler
from services import settings_runtime
from services.roles import policy as role_policy
from services.agent import run_queue
from services.ai import scheduled_runner
from services.api_metrics import ApiMetricsMiddleware

scheduler = AsyncIOScheduler()
_logger = logging.getLogger(__name__)
sio = socketio.AsyncServer(async_mode="asgi")
socket = socketio.ASGIApp(sio)

# 登记给 routers 用（开启维护模式时要断开所有实时连接），避免 router 反向
# import main 造成循环导入。
realtime.register_server(sio)

app = FastAPI(default_response_class=JSONResponse, docs_url=None, openapi_url=None)

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
    # 封禁拒绝是 403，原因/到期走响应头（见 util/ban.py）。浏览器的 JS 默认读
    # 不到自定义响应头，跨域本地调试会丢原因；显式暴露后前端才能提示用户。
    expose_headers=["X-Ban-Reason", "X-Ban-Until"],
)

# 端点耗时统计：纯 ASGI 中间件，注册位置刻意夹在 CORSMiddleware 与
# maintenance_middleware 之间（add_middleware 后注册者更靠外层）。这样它位于维护
# 中间件**内侧** —— 被维护模式 503 挡下的请求不会进入统计（它们没有执行任何路由），
# 而 shield 仍然是最外层。它只记录真正匹配到路由模板的请求（§5.4）。
app.add_middleware(ApiMetricsMiddleware)

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
MAINTENANCE_EXEMPT_PATHS = {
    "/api",
    "/api/",
    "/api/users/auth",
    "/api/users/auth/logout",
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


# 顺序至关重要：add_middleware 是「后注册的更外层」，本中间件定义在
# shield_middleware 之前，保证盾牌仍在最外层（非 Mozilla UA 仍先吃 418）。
#
# 维护开启期间 admin 全域放行：从 ``Authorization: Bearer`` 取 token，走
# ``util.object_id.is_admin_token``（与 get_current_user 同一条认证路径，含
# banned / session 校验，``per`` 取库里的当前 roles）。
# 门控是「缓存 + fresh」两级：缓存快路径（2s）挡住绝大多数请求，fresh 再复核一次
# 兜「缓存陈旧」的窗口。fresh 自己带 1s 短缓存（db.system_state.FRESH_CACHE_TTL），
# 所以准入路径最多每秒一次 SQLite 读，而不是每请求一次；本进程 enable/disable 经
# set_state → invalidate_cache() 两级缓存即刻失效。跨进程 enable 最多 2s 陈旧放行
# （与纯缓存的既有行为一致），跨进程 disable 最多 1s 内被 fresh 纠正。
@app.middleware("http")
async def maintenance_middleware(request: Request, call_next):
    path = request.url.path
    if (
        path.startswith("/api/")
        and str(request.method).upper() != "OPTIONS"  # 放行 CORS 预检
        and path not in MAINTENANCE_EXEMPT_PATHS
        and not path.startswith(MAINTENANCE_EXEMPT_PREFIXES)
        and await maintenance_enabled()
        and await maintenance_enabled_fresh()
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


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    for name, value in SECURITY_RESPONSE_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


SHIELD_BLOCKED_MESSAGE = "您的请求被服务器判定为非法访问。已经记录您的IP和UA数据，管理员将进行安全追责。"


@app.middleware("http")
async def shield_middleware(request: Request, call_next):
    pattern_hit = SEC_PATTERN.search(request.url.path)
    bad_ua = (
        "Mozilla" not in request.headers.get("User-Agent", "")
        and not request.url.path.startswith(AI_OPENAI_PREFIX)
    )
    bad_method = str(request.method).upper() not in allowed_methods
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
        _spawn_security_audit(
            ZVMSLog(
                url=str(request.url),
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


app.mount("/socket.io", socket)


@sio.event
async def connect(sid, environ, auth=None):
    # 兜底：正常路径上 MaintenanceSocketGuard 已在握手阶段拒掉新连接（并且
    # GET /api/system/maintenance 已放行），这里再查一次**绕过缓存**的最新状态，
    # 万一有入口绕过那层中间件也不会漏进连接。admin 在维护期间照常连接。
    token = realtime.connect_token(environ, auth)
    if await maintenance_enabled_fresh() and not await is_admin_token(token):
        _logger.warning("refuse socket connect %s (maintenance)", sid)
        raise SocketIOConnectionRefusedError("系统维护中，请稍后再试")
    if token:
        try:
            await _authenticate(token, "long", True)
        except HTTPException as exc:
            _logger.warning(
                "refuse socket connect %s (auth rejected: %s)", sid, exc.status_code
            )
            return False
        except Exception:
            _logger.exception("refuse socket connect %s (auth unavailable)", sid)
            return False
    _logger.info("connect %s", sid)


@sio.event
async def disconnect(sid):
    _logger.info("disconnect %s", sid)


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

    # Clear the bytes of soft-deleted attachments older than the retention
    # window daily at 03:00 HKT (UTC+8). Tombstone metadata is kept.
    scheduler.add_job(
        purge_expired_attachments,
        "cron",
        hour=3,
        minute=0,
        second=0,
        timezone="Asia/Hong_Kong",
        id="daily_purge_files",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # OCR 上传图（义工登记表）保留 3 天：到点硬删原图 + 缩略图 + OCR 行 + 事件，
    # 活动绑定的义工单不在此列（entity_type='activity' 是证据）。05:00 避开
    # 03:00 的附件墓碑清理与 03:30 的留存清理，三件事不同表但都要 ADB 写额度。
    scheduler.add_job(
        run_ocr_upload_gc,
        "cron",
        hour=5,
        minute=0,
        second=0,
        timezone="Asia/Hong_Kong",
        id="daily_ocr_upload_gc",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # Delete expired app_logs / export_tasks rows daily at 03:30 HKT (UTC+8).
    # minute=30: hour=3 is taken by daily_purge_files above.
    scheduler.add_job(
        purge_expired_records,
        "cron",
        hour=3,
        minute=30,
        second=0,
        timezone="Asia/Hong_Kong",
        id="daily_retention_purge",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # Delete user_trash rows whose 30-day recovery window has passed, daily at
    # 02:00 HKT (UTC+8). 02:00 is the only free slot: 01:00 compute_time,
    # 03:00 files, 03:30 retention, 04:00 reports, 04:30 perf retention.
    # Retention window: USER_TRASH_RETENTION_DAYS (default 30) in
    # tasks/user_trash_gc.py.
    scheduler.add_job(
        purge_expired_trash,
        "cron",
        hour=2,
        minute=0,
        second=0,
        timezone="Asia/Hong_Kong",
        id="daily_user_trash_gc",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # 性能采样三档 + 每天一次的采样清理（设计 §4）。间隔全部可配
    # （PERF_HOST_ORACLE... 见 services/perf_sampler.py），单 worker 下进程内采样可靠。
    # 30s 档纯本地零 DB 往返；120s 档为 1 条合并语句；600s 档为 3 条慢语句。
    scheduler.add_job(
        perf_sampler.sample_host,
        "interval",
        seconds=perf_sampler.host_interval_seconds(),
        id="perf_sample_host",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=30,
    )
    scheduler.add_job(
        perf_sampler.sample_oracle,
        "interval",
        seconds=perf_sampler.oracle_interval_seconds(),
        id="perf_sample_oracle",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=60,
    )
    scheduler.add_job(
        perf_sampler.sample_slow,
        "interval",
        seconds=perf_sampler.slow_interval_seconds(),
        id="perf_sample_slow",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    # 采样表保留期清理（PERF_RETENTION_DAYS，默认 14 天）。04:30 HKT：03:00
    # 附件、03:30 业务日志、04:00 报表均已占用。
    scheduler.add_job(
        perf_sampler.purge_expired,
        "cron",
        hour=4,
        minute=30,
        second=0,
        timezone="Asia/Hong_Kong",
        id="daily_perf_retention_purge",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )

    # 在线设置中心：每 5 秒把 SYSTEM_SETTINGS / USER_QUOTA_OVERRIDES 读进进程内
    # 快照。同步读取点（AI 配置视图、上下文窗口、上传配额）直接读内存，调用方
    # 签名不变；``PUT /api/system/settings`` 会立刻改写快照，本任务只负责把**别的
    # worker** 的修改在 5 秒内带过来。读库失败由 refresh() 内部吞掉并回落默认值。
    scheduler.add_job(
        settings_runtime.refresh,
        "interval",
        seconds=settings_runtime.REFRESH_SECONDS,
        id="system_settings_refresh",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=2,
    )

    # 角色功能开关与额度（V043）：每 5 秒把 ROLE_FEATURES / ROLE_QUOTAS 读进快照。
    # 功能闸（util.permission.features.has_feature）与额度预检都是同步判定，不刷
    # 就会一直按代码默认值判；管理页保存后也只对处理该请求的 worker 生效。
    scheduler.add_job(
        role_policy.refresh,
        "interval",
        seconds=role_policy.REFRESH_SECONDS,
        id="role_policy_refresh",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=2,
    )

    # AI 会话的后台 run 队列（设计 §4.3）：先把上次进程遗留的 running 收敛成
    # interrupted（单 worker 下「刚启动」等价于「没有合法在跑的任务」），再起泵
    # 去领 queued 的。收敛失败不该挡住启动——watchdog 每 30s 还会再试一次。
    try:
        recovered = await run_queue.recover_on_startup()
        if recovered:
            print(f"ai run queue: {recovered} interrupted run(s) recovered")
    except Exception:  # noqa: BLE001 - 启动阶段的库不可用由 health 报告
        _logger.exception("ai run queue startup recovery failed")

    # run 心跳 watchdog：心跳早于 AI_RUN_STALE_SECONDS 的 running 判僵尸并掐掉
    # 对应协程（阈值下限 max(360, run_timeout+60)，见 run_queue.stale_seconds）。
    scheduler.add_job(
        run_queue.watchdog_tick,
        "interval",
        seconds=30,
        id="ai_run_watchdog",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=10,
    )

    # AI 定时任务：每分钟扫一次到期任务。claim 是 CAS（见 scheduled_runner），
    # 所以这里的 max_instances=1 只是省重复扫描，不是正确性依赖。
    scheduler.add_job(
        scheduled_runner.tick,
        "interval",
        seconds=60,
        id="ai_schedule_tick",
        coalesce=True,
        max_instances=1,
        misfire_grace_time=30,
    )

    try:
        await run_queue.kick()
    except Exception:  # noqa: BLE001 - 起泵失败只影响后台执行，接口仍可用
        _logger.exception("ai run queue kick failed")

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
app.include_router(
    system_settings_router.router, prefix="/api/system", tags=["System"]
)
app.include_router(db_system_router.router, prefix="/api/system/db", tags=["System"])
app.include_router(
    db_metrics_router.router, prefix="/api/system/db/metrics", tags=["System"]
)
app.include_router(
    system_ssh_router.router, prefix="/api/system/ssh", tags=["System"]
)
app.include_router(
    system_files_router.router, prefix="/api/system/files", tags=["System"]
)
app.include_router(
    system_db_content_router.router,
    prefix="/api/system/db/content",
    tags=["System"],
)
app.include_router(
    system_role_policies_router.router,
    prefix="/api/system/role-policies",
    tags=["System"],
)
app.include_router(ban_router.router, prefix="/api/v3/ban", tags=["ban"])
app.include_router(
    notifications_router.router,
    prefix="/api/v3/notifications",
    tags=["notifications"],
)
app.include_router(
    ban_violations_router.router, prefix="/api/v3/ban", tags=["ban"]
)
# 必须先于 ai_router 注册：``ai_router`` 的 ``GET /quota/{user_oid}`` 会把
# ``/api/ai/quota/requests`` 与 ``/api/ai/quota/users`` 当成 oid 匹配掉（FastAPI
# 按注册顺序匹配），回一个与业务无关的 400 Invalid Object ID。
app.include_router(
    ai_quota_requests_router.router, prefix="/api/ai/quota", tags=["ai"]
)
app.include_router(ai_router.router, prefix="/api/ai", tags=["ai"])
app.include_router(
    ai_api_keys_router.router, prefix="/api/ai/api-keys", tags=["ai"]
)
app.include_router(
    ai_agent_router.router, prefix="/api/ai/agent", tags=["ai"]
)
app.include_router(
    ai_conversations_router.router, prefix="/api/ai", tags=["ai"]
)
app.include_router(
    ai_scenes_router.router, prefix="/api/ai", tags=["ai"]
)
app.include_router(
    ai_schedule_router.router, prefix="/api/ai/scheduled-tasks", tags=["ai"]
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
        if not await oracle_ping():
            raise RuntimeError("database ping returned false")
        return {"status": "ok", "code": 200, "data": "OK"}
    except Exception:
        _logger.exception("Health check failed")
        return JSONResponse(
            status_code=503,
            content={"status": "error", "code": 503, "data": "Service unavailable"},
        )


# 公钥按本文件定位，不依赖 cwd：systemd 的 WorkingDirectory 与手工启动时不同。
CERT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "rsa_public_key.pem"
)

_cert_lock = threading.Lock()
_cert_pem: str | None = None


def _load_cert() -> str:
    """读公钥并在内存里缓存：证书不轮换，只有失败才不缓存（修好后自愈）。"""
    global _cert_pem
    if _cert_pem is not None:
        return _cert_pem
    with _cert_lock:
        if _cert_pem is None:
            with open(CERT_PATH, "r", encoding="utf-8") as handle:
                _cert_pem = handle.read()
        return _cert_pem


@app.get("/api/cert")
async def get_cert():
    try:
        data = _load_cert()
    except (OSError, UnicodeDecodeError) as exc:
        _logger.warning("cert public key is unreadable: %s", exc)
        return JSONResponse(
            status_code=503,
            content={"status": "error", "code": 503, "data": "证书暂不可用"},
        )
    return {
        "status": "ok",
        "code": 200,
        "data": data,
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
