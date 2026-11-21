"""ZVMS 后端入口：装配子模块 → 注册路由表 → 起 uvicorn。

拆分前的 ``main.py`` 是 886 行的混合体，现在按职责分家：

===================  ====================================================
模块                 职责
===================  ====================================================
``app_setup``        FastAPI 实例 + CORS / 盾牌 / 安全头 / 维护模式 /
                     耗时统计中间件（**注册顺序即契约**）
``socket_events``    socket.io 服务端实例与 CONNECT / DISCONNECT 事件
``scheduler``        14 个 APScheduler 周期任务 + startup / shutdown
``meta_routes``      ``/api/``、``/api/health``、``/api/cert``、``/api/version``
``error_handlers``   422 中文本地化、``InvalidOid`` → 400、兜底 500
``bindings``         少数「测试会打桩」的运行时协作者的活绑定
===================  ====================================================

本文件只留三件事：import、``ROUTERS`` 表 + 按序注册、``python main.py`` 入口。
路由注册顺序是业务契约（FastAPI 先匹配先注册），改动前先读表里的注释。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import error_handlers
import meta_routes
from util.env import getenv
from app_setup import (  # noqa: F401 - 兼容导出：tests/ 与运维脚本按 main.<name> 取
    DEFAULT_CORS_ALLOWED_ORIGINS,
    MAINTENANCE_EXEMPT_PATHS,
    MAINTENANCE_EXEMPT_PREFIXES,
    SECURITY_RESPONSE_HEADERS,
    SHIELD_BLOCKED_MESSAGE,
    _write_security_audit,
    create_app,
    cors_allowed_origins,
)
from db.oracle.engine import ping as oracle_ping  # noqa: F401 - 兼容导出
from db.system_state import is_enabled_async as maintenance_enabled  # noqa: F401 - 兼容导出
from db.system_state import is_enabled_fresh_async as maintenance_enabled_fresh  # noqa: F401 - 兼容导出
from error_handlers import (  # noqa: F401 - 兼容导出
    _localize_validation_message,
    invalid_oid_exception_handler,
    validation_exception_handler,
)
from meta_routes import (  # noqa: F401 - 兼容导出
    CERT_PATH,
    VERSION_UNKNOWN,
    current_version,
)
from routers import (
    activities_router,
    activity_statistics_router,
    ai_agent_router,
    ai_api_keys_router,
    ai_conversations_router,
    ai_models_router,
    ai_quota_requests_router,
    ai_router,
    ai_scenes_router,
    ai_schedule_router,
    attachments_router,
    ban_router,
    ban_violations_router,
    changelog_router,
    db_metrics_router,
    db_system_router,
    exports_router,
    groups_router,
    imports_router,
    logs_router,
    notifications_router,
    system_db_content_router,
    system_files_router,
    system_role_policies_router,
    system_settings_router,
    system_ssh_router,
    theme_router,
    times_router,
    users_router,
)
from routers.system_router import router as system_router
from scheduler import (  # noqa: F401 - 兼容导出
    mark_all_tasks_failed,
    scheduler,
    shutdown_event,
    startup_event,
)
from socket_events import sio, socket  # noqa: F401 - sio 兼容导出
from typings.log import ZVMSLog  # noqa: F401 - 兼容导出

# ``main._cert_pem``：``/api/cert`` 的公钥缓存（真实读写见 meta_routes，经
# bindings 落在这个名字上）。这里只放初始值，让既有测试的
# ``monkeypatch.setattr(main, "_cert_pem", None)`` 有对象可打桩。
_cert_pem: str | None = None


@asynccontextmanager
async def lifespan(_app):
    """Run scheduler startup/shutdown through FastAPI's supported lifespan API."""
    await startup_event()
    try:
        yield
    finally:
        await shutdown_event()


app = create_app(lifespan=lifespan)


# 路由表：(router, prefix, tags)。顺序即契约 —— 先注册先匹配。
ROUTERS: tuple[tuple, ...] = (
    (users_router.router, "/api/v1/users", ["users"]),
    (groups_router.router, "/api/v1/groups", ["groups"]),
    (exports_router.router, "/api/exports", ["exports"]),
    (imports_router.router, "/api/imports", ["imports"]),
    (logs_router.router, "/api/logs", ["logs"]),
    (activities_router.router, "/api/v1/activities", None),
    (times_router.router, "/api/v1/times", None),
    (theme_router.router, "/api/v1/themes", ["themes"]),
    (activity_statistics_router.router, "/api/v1/statistics/activities", None),
    (changelog_router.router, "/api/changelog", ["changelog"]),
    (attachments_router.router, "/api/attachments", ["attachments"]),
    (system_router, "/api/system/maintenance", ["System"]),
    (system_settings_router.router, "/api/system", ["System"]),
    (db_system_router.router, "/api/system/db", ["System"]),
    (db_metrics_router.router, "/api/system/db/metrics", ["System"]),
    (system_ssh_router.router, "/api/system/ssh", ["System"]),
    (system_files_router.router, "/api/system/files", ["System"]),
    (system_db_content_router.router, "/api/system/db/content", ["System"]),
    (system_role_policies_router.router, "/api/system/role-policies", ["System"]),
    (ban_router.router, "/api/v1/ban", ["ban"]),
    (notifications_router.router, "/api/v1/notifications", ["notifications"]),
    (ban_violations_router.router, "/api/v1/ban", ["ban"]),
    # 必须先于 ai_router 注册：``ai_router`` 的 ``GET /quota/{user_oid}`` 会把
    # ``/api/ai/quota/requests`` 与 ``/api/ai/quota/users`` 当成 oid 匹配掉（FastAPI
    # 按注册顺序匹配），回一个与业务无关的 400 Invalid Object ID。
    (ai_quota_requests_router.router, "/api/ai/quota", ["ai"]),
    (ai_router.router, "/api/ai", ["ai"]),
    # 模型目录独立挂载在 /api/ai/models 下：``ai_router`` 的同级路由没有这个前缀，
    # 两者互不遮蔽（``/api/ai/v1/models`` 是 OpenAI 兼容面，仍然归 ai_router）。
    (ai_models_router.router, "/api/ai/models", ["ai"]),
    (ai_api_keys_router.router, "/api/ai/api-keys", ["ai"]),
    (ai_agent_router.router, "/api/ai/agent", ["ai"]),
    (ai_conversations_router.router, "/api/ai", ["ai"]),
    (ai_scenes_router.router, "/api/ai", ["ai"]),
    (ai_schedule_router.router, "/api/ai/scheduled-tasks", ["ai"]),
)


# socket.io 是独立 ASGI 子应用，挂载点单独一层（维护准入见
# realtime.MaintenanceSocketGuard）。挂载在路由注册之前，与拆分前一致。
app.mount("/socket.io", socket)

for _router, _prefix, _tags in ROUTERS:
    app.include_router(_router, prefix=_prefix, tags=_tags)

# 首页 → 探活/证书 → 异常处理 → 版本。
# 这个交错顺序是拆分前的原样（路由表 dump 与它逐条对齐），别顺手重排。
meta_routes.register_index(app)
meta_routes.register_probes(app)
error_handlers.register(app)
meta_routes.register_version(app)


if __name__ == "__main__":
    import uvicorn

    # 部署口径：生产由 systemd 以 ``uvicorn main:app --host 127.0.0.1 --port 8018``
    # 拉起（nginx / docker 反代 127.0.0.1:8018，见 deployment/zvp5.nginx.conf 与
    # docs/DEPLOY_*.md）。这里的默认值跟部署对齐，不再硬编码历史遗留的 8000；
    # 本机裸跑要别的端口就 ``ZVP5_PORT=8000 python main.py``。
    uvicorn.run(
        app=app,
        host="0.0.0.0",
        port=int(getenv("ZVP5_PORT", "8018")),
    )
