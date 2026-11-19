"""拆分后的装配契约：路由表、中间件顺序、兼容导出。

``main.py`` 从 886 行的混合体拆成 ``app_setup`` / ``socket_events`` /
``scheduler`` / ``meta_routes`` / ``error_handlers``。拆分要求「对外行为与路由
表逐条不变」，这里把三条最容易在后续重构里被无意破坏的性质钉住：

* 路由条数与关键顺序（``ai_quota_requests`` 必须先于 ``ai_router``；单数 →
  复数重定向层已在 v1 收口时退役；``/api/version`` 是最后一条业务路由）；
* 中间件栈顺序（418 / 503 / CORS / 耗时统计的组合行为全靠它）；
* ``main`` 上的兼容名字（运维脚本与既有测试按 ``main.<name>`` 取实例/打桩）。

拆分前的路由 dump 是 262 条（含 V053 站内通知已读、Agent 实时事件路由与 V054
Agent 警示已确认的 GET/POST）。逐条真相源是 ``tests/test_route_snapshot.py``；
这里只钉总数：当前 219 条（262 → 删 mcp 10 条 / amalgamation 1 条 / 两条失效
legacy 重定向，+2 条 agent ack；v1 收口时 users/groups 四份 router 合并为
单文件、legacy_redirects 整模块退役，净减 18 条；2026-11-19 死端点清理再减
12 条）。新增路由时把这里的数字一起改（并跑一次 ``tests/test_api_metrics.py``
之类的路由相关用例）。
"""

from __future__ import annotations

import main
from tests.route_utils import iter_leaf_routes

PRE_SPLIT_ROUTE_COUNT = 219
ROUTER_COUNT = 29


def test_route_count_matches_pre_split_dump() -> None:
    assert len(list(iter_leaf_routes(main.app.routes))) == PRE_SPLIT_ROUTE_COUNT


def test_middleware_stack_order_is_unchanged() -> None:
    """add_middleware 后注册者更靠外层：shield → 安全头 → 维护 → 统计 → CORS → socket。"""
    names = [
        getattr(middleware.cls, "__name__", str(middleware.cls))
        for middleware in main.app.user_middleware
    ]
    assert names == [
        "BaseHTTPMiddleware",  # shield_middleware（最外层：非 Mozilla UA 先吃 418）
        "BaseHTTPMiddleware",  # security_headers_middleware
        "BaseHTTPMiddleware",  # maintenance_middleware（503 会带基线安全头）
        "ApiMetricsMiddleware",  # 维护中间件内侧：503 不计入耗时统计
        "CORSMiddleware",
        "MaintenanceSocketGuard",
    ]


def test_router_table_order_keeps_the_quota_prefix_constraint() -> None:
    prefixes = [prefix for _router, prefix, _tags in main.ROUTERS]

    assert len(main.ROUTERS) == ROUTER_COUNT
    assert prefixes.index("/api/ai/quota") < prefixes.index("/api/ai")
    assert prefixes[0] == "/api/v1/users"
    assert prefixes[-1] == "/api/ai/scheduled-tasks"


def test_meta_routes_sit_around_the_router_table() -> None:
    paths = [getattr(route, "path", None) for route in iter_leaf_routes(main.app.routes)]

    # 所有 include_router 之后才是首页/探活/异常处理，version 收尾。
    assert paths.index("/api/v1/users") < paths.index("/api/")
    assert paths.index("/api/") < paths.index("/api/version")
    assert paths[-1] == "/api/version"
    # 单数 → 复数重定向层已退役：仓库里不再有这两条兜底路由。
    assert "/api/user/{path:path}" not in paths
    assert "/api/group/{path:path}" not in paths


def test_main_keeps_the_names_ops_and_tests_look_up() -> None:
    for name in (
        "app",
        "scheduler",
        "sio",
        "startup_event",
        "shutdown_event",
        "cors_allowed_origins",
        "current_version",
        "CERT_PATH",
        "_cert_pem",
        "ZVMSLog",
        "oracle_ping",
        "maintenance_enabled",
        "maintenance_enabled_fresh",
        "SECURITY_RESPONSE_HEADERS",
        "SHIELD_BLOCKED_MESSAGE",
        "_write_security_audit",
        "_localize_validation_message",
        "validation_exception_handler",
        "invalid_oid_exception_handler",
    ):
        assert hasattr(main, name), f"main.{name} 丢了：既有测试/运维脚本靠它打桩"
