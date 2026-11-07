"""路由表冻结快照（router 拆分/重构的回归护栏）。

本文件把 ``main.app`` 的路由表逐条写死：路径 + 排序后的 HTTP 方法。
任何 router 拆分、include_router 顺序调整、prefix 变更都必须让这张表
逐条不变 —— 一旦增删或方法变化，本用例失败并列出差异项。

没有 ``methods`` 的路由（``socket.io`` 的 Mount、SSH 的 WebSocket 路由）无法
与普通路由同构比较，因此单独断言：Mount 恰好一个、路径固定 ``/socket.io``、
挂载的是 ``socketio.ASGIApp``；WebSocket 路由路径固定 ``/api/system/ssh/ws``。
``Mount`` 内部 ``app`` 的对象身份不稳定（每次 import 都是新实例），所以只比较
类型与路径。
"""

from __future__ import annotations

from collections import Counter

from fastapi.routing import APIWebSocketRoute, Mount

import main

# 257 个 route 对象 = 255 条带 methods 的路由（APIRoute 237 + 两个
# 自定义 APIRoute 子类 13 + 5）+ 1 个 socket.io Mount + 1 个 WebSocket 路由。
# 文档路由不计入：``docs_url`` / ``openapi_url`` 都是 None（见
# tests/test_cert_endpoint.py）。
TOTAL_ROUTE_OBJECTS = 257

FROZEN_ROUTES: list[tuple[str, tuple[str, ...]]] = [
    ('/api/', ('GET',)),
    ('/api/activity/{path:path}', ('DELETE', 'GET', 'POST', 'PUT')),
    ('/api/ai/agent/proposals', ('GET',)),
    ('/api/ai/agent/proposals/{oid}/confirm', ('POST',)),
    ('/api/ai/agent/proposals/{oid}/reject', ('POST',)),
    ('/api/ai/agent/unlock', ('POST',)),
    ('/api/ai/api-keys', ('GET',)),
    ('/api/ai/api-keys', ('POST',)),
    ('/api/ai/api-keys/{oid}', ('DELETE',)),
    ('/api/ai/api-keys/{oid}', ('PATCH',)),
    ('/api/ai/conversations', ('GET',)),
    ('/api/ai/conversations', ('POST',)),
    ('/api/ai/conversations/{oid}', ('DELETE',)),
    ('/api/ai/conversations/{oid}', ('GET',)),
    ('/api/ai/conversations/{oid}', ('PATCH',)),
    ('/api/ai/conversations/{oid}/compact', ('POST',)),
    ('/api/ai/conversations/{oid}/messages', ('GET',)),
    ('/api/ai/conversations/{oid}/runs', ('POST',)),
    ('/api/ai/health', ('GET',)),
    ('/api/ai/keys', ('GET',)),
    ('/api/ai/keys', ('POST',)),
    ('/api/ai/keys/{oid}', ('DELETE',)),
    ('/api/ai/keys/{oid}', ('PATCH',)),
    ('/api/ai/logs', ('GET',)),
    ('/api/ai/logs/stats', ('GET',)),
    ('/api/ai/models', ('GET',)),
    ('/api/ai/models/discover', ('POST',)),
    ('/api/ai/models/{model:path}', ('DELETE',)),
    ('/api/ai/models/{model:path}', ('PUT',)),
    ('/api/ai/ocr', ('POST',)),
    ('/api/ai/ocr-engines', ('GET',)),
    ('/api/ai/ocr-engines', ('POST',)),
    ('/api/ai/ocr-engines/{oid}', ('DELETE',)),
    ('/api/ai/ocr-engines/{oid}', ('PATCH',)),
    ('/api/ai/ocr/file', ('POST',)),
    ('/api/ai/prompts', ('GET',)),
    ('/api/ai/prompts/{oid}', ('GET',)),
    ('/api/ai/quota', ('GET',)),
    ('/api/ai/quota/requests', ('GET',)),
    ('/api/ai/quota/requests', ('POST',)),
    ('/api/ai/quota/requests/mine', ('GET',)),
    ('/api/ai/quota/requests/{oid}/approve', ('POST',)),
    ('/api/ai/quota/requests/{oid}/reject', ('POST',)),
    ('/api/ai/quota/usage', ('GET',)),
    ('/api/ai/quota/users', ('GET',)),
    ('/api/ai/quota/{user_oid}', ('GET',)),
    ('/api/ai/quota/{user_oid}', ('PUT',)),
    ('/api/ai/quota/{user_oid}/reset', ('POST',)),
    ('/api/ai/runs', ('GET',)),
    ('/api/ai/runs/{run_id}', ('GET',)),
    ('/api/ai/runs/{run_id}/cancel', ('POST',)),
    ('/api/ai/scenes/{scene}/runs', ('POST',)),
    ('/api/ai/scheduled-tasks', ('GET',)),
    ('/api/ai/scheduled-tasks', ('POST',)),
    ('/api/ai/scheduled-tasks/{oid}', ('DELETE',)),
    ('/api/ai/scheduled-tasks/{oid}', ('PATCH',)),
    ('/api/ai/scheduled-tasks/{oid}/review', ('POST',)),
    ('/api/ai/scheduled-tasks/{oid}/run-now', ('POST',)),
    ('/api/ai/scheduled-tasks/{oid}/runs', ('GET',)),
    ('/api/ai/v1/chat/completions', ('POST',)),
    ('/api/ai/v1/models', ('GET',)),
    ('/api/ai/volunteer-ocr', ('POST',)),
    ('/api/attachments', ('GET',)),
    ('/api/attachments', ('POST',)),
    ('/api/attachments/{oid}', ('DELETE',)),
    ('/api/attachments/{oid}', ('GET',)),
    ('/api/attachments/{oid}/audit', ('GET',)),
    ('/api/attachments/{oid}/bind', ('POST',)),
    ('/api/attachments/{oid}/link', ('POST',)),
    ('/api/attachments/{oid}/meta', ('GET',)),
    ('/api/attachments/{oid}/ocr', ('GET',)),
    ('/api/attachments/{oid}/ocr', ('POST',)),
    ('/api/attachments/{oid}/status', ('PATCH',)),
    ('/api/attachments/{oid}/thumb', ('GET',)),
    ('/api/attachments/{oid}/verify', ('POST',)),
    ('/api/cert', ('GET',)),
    ('/api/changelog', ('GET',)),
    ('/api/changelog', ('POST',)),
    ('/api/changelog/{oid}', ('DELETE',)),
    ('/api/changelog/{oid}', ('PUT',)),
    ('/api/export/{path:path}', ('DELETE', 'GET', 'POST', 'PUT')),
    ('/api/exports', ('GET',)),
    ('/api/exports/reports', ('POST',)),
    ('/api/exports/reports/download', ('GET',)),
    ('/api/exports/time', ('POST',)),
    ('/api/exports/users', ('POST',)),
    ('/api/exports/{task_id}', ('GET',)),
    ('/api/exports/{task_id}/file', ('GET',)),
    ('/api/group/{path:path}', ('DELETE', 'GET', 'POST', 'PUT')),
    ('/api/groups', ('GET',)),
    ('/api/groups', ('POST',)),
    ('/api/groups/{group_id}', ('DELETE',)),
    ('/api/groups/{group_id}', ('GET',)),
    ('/api/groups/{group_id}/activities', ('GET',)),
    ('/api/groups/{group_id}/description', ('PUT',)),
    ('/api/groups/{group_id}/name', ('PUT',)),
    ('/api/groups/{group_id}/template', ('GET',)),
    ('/api/groups/{group_id}/time', ('GET',)),
    ('/api/groups/{group_id}/user', ('GET',)),
    ('/api/health', ('GET',)),
    ('/api/import/{path:path}', ('DELETE', 'GET', 'POST', 'PUT')),
    ('/api/imports/activities', ('POST',)),
    ('/api/log/{path:path}', ('DELETE', 'GET', 'POST', 'PUT')),
    ('/api/logs', ('GET',)),
    ('/api/system/db/backup/progress', ('GET',)),
    ('/api/system/db/backup/run', ('POST',)),
    ('/api/system/db/cache/flush', ('POST',)),
    ('/api/system/db/cache/refresh', ('POST',)),
    ('/api/system/db/content/ddl', ('POST',)),
    ('/api/system/db/content/query', ('POST',)),
    ('/api/system/db/content/schema', ('GET',)),
    ('/api/system/db/content/table/{name}/columns', ('GET',)),
    ('/api/system/db/content/table/{name}/rows', ('DELETE',)),
    ('/api/system/db/content/table/{name}/rows', ('GET',)),
    ('/api/system/db/content/table/{name}/rows', ('POST',)),
    ('/api/system/db/content/table/{name}/rows', ('PUT',)),
    ('/api/system/db/maintenance/overview', ('GET',)),
    ('/api/system/db/maintenance/plan', ('POST',)),
    ('/api/system/db/maintenance/progress', ('GET',)),
    ('/api/system/db/maintenance/run', ('POST',)),
    ('/api/system/db/maintenance/suggestions', ('GET',)),
    ('/api/system/db/metrics/endpoints', ('GET',)),
    ('/api/system/db/metrics/history', ('GET',)),
    ('/api/system/db/metrics/host', ('GET',)),
    ('/api/system/db/metrics/realtime', ('GET',)),
    ('/api/system/db/metrics/top-sql', ('GET',)),
    ('/api/system/db/retention/preview', ('POST',)),
    ('/api/system/db/retention/progress', ('GET',)),
    ('/api/system/db/retention/run', ('POST',)),
    ('/api/system/db/status', ('GET',)),
    ('/api/system/files/chmod', ('POST',)),
    ('/api/system/files/delete', ('POST',)),
    ('/api/system/files/download', ('GET',)),
    ('/api/system/files/edit', ('POST',)),
    ('/api/system/files/list', ('GET',)),
    ('/api/system/files/mkdir', ('POST',)),
    ('/api/system/files/preview', ('GET',)),
    ('/api/system/files/rename', ('POST',)),
    ('/api/system/files/stat', ('GET',)),
    ('/api/system/files/upload', ('POST',)),
    ('/api/system/maintenance', ('GET',)),
    ('/api/system/maintenance/disable', ('POST',)),
    ('/api/system/maintenance/enable', ('POST',)),
    ('/api/system/role-policies', ('GET',)),
    ('/api/system/role-policies', ('PUT',)),
    ('/api/system/role-policies/me', ('GET',)),
    ('/api/system/role-policies/{role}', ('PUT',)),
    ('/api/system/settings', ('GET',)),
    ('/api/system/settings', ('PUT',)),
    ('/api/system/ssh/session', ('POST',)),
    ('/api/system/ssh/suggest', ('POST',)),
    ('/api/system/ssh/targets', ('GET',)),
    ('/api/system/user-quotas/{oid}', ('GET',)),
    ('/api/system/user-quotas/{oid}', ('PUT',)),
    ('/api/user/{path:path}', ('DELETE', 'GET', 'POST', 'PUT')),
    ('/api/users', ('GET',)),
    ('/api/users', ('POST',)),
    ('/api/users/auth', ('POST',)),
    ('/api/users/auth/device', ('POST',)),
    ('/api/users/auth/logout', ('POST',)),
    ('/api/users/{target}', ('DELETE',)),
    ('/api/users/{user_oid}', ('GET',)),
    ('/api/users/{user_oid}', ('PUT',)),
    ('/api/users/{user_oid}/activities', ('GET',)),
    ('/api/users/{user_oid}/activity/{path:path}', ('GET',)),
    ('/api/users/{user_oid}/group', ('POST',)),
    ('/api/users/{user_oid}/group/{group_id}', ('DELETE',)),
    ('/api/users/{user_oid}/logs', ('GET',)),
    ('/api/users/{user_oid}/password', ('PUT',)),
    ('/api/users/{user_oid}/past', ('POST',)),
    ('/api/users/{user_oid}/past/{past_identity_idx}', ('DELETE',)),
    ('/api/users/{user_oid}/time', ('GET',)),
    ('/api/v1/activities', ('GET',)),
    ('/api/v1/activities', ('POST',)),
    ('/api/v1/activities/amalgamation', ('POST',)),
    ('/api/v1/activities/{activity_id}', ('DELETE',)),
    ('/api/v1/activities/{activity_id}', ('GET',)),
    ('/api/v1/activities/{activity_id}/info', ('PUT',)),
    ('/api/v1/activities/{activity_id}/members', ('GET',)),
    ('/api/v1/activities/{activity_id}/members', ('POST',)),
    ('/api/v1/activities/{activity_id}/members/{document_id}/record', ('PUT',)),
    ('/api/v1/activities/{activity_id}/members/{session_id}', ('DELETE',)),
    ('/api/v1/activities/{activity_id}/members/{session_id}', ('GET',)),
    ('/api/v1/activities/{activity_id}/members/{session_id}/status', ('PUT',)),
    ('/api/v1/activities/{activity_id}/status', ('PUT',)),
    ('/api/v1/ban/devices', ('GET',)),
    ('/api/v1/ban/devices', ('POST',)),
    ('/api/v1/ban/devices/{xuehai_id}', ('DELETE',)),
    ('/api/v1/ban/devices/{xuehai_id}/reset-gradient', ('POST',)),
    ('/api/v1/ban/records', ('GET',)),
    ('/api/v1/ban/rules', ('GET',)),
    ('/api/v1/ban/rules', ('POST',)),
    ('/api/v1/ban/rules/{code}', ('DELETE',)),
    ('/api/v1/ban/rules/{code}', ('PUT',)),
    ('/api/v1/ban/triggers', ('GET',)),
    ('/api/v1/ban/users', ('GET',)),
    ('/api/v1/ban/violations', ('GET',)),
    ('/api/v1/ban/violations', ('POST',)),
    ('/api/v1/groups', ('GET',)),
    ('/api/v1/groups', ('POST',)),
    ('/api/v1/groups/{group_id}', ('DELETE',)),
    ('/api/v1/groups/{group_id}', ('GET',)),
    ('/api/v1/groups/{group_id}/activities', ('GET',)),
    ('/api/v1/groups/{group_id}/name', ('PUT',)),
    ('/api/v1/groups/{group_id}/statistics/compliance', ('GET',)),
    ('/api/v1/groups/{group_id}/time', ('GET',)),
    ('/api/v1/groups/{group_id}/users', ('GET',)),
    ('/api/v1/groups/{group_id}/users/{user_id}', ('DELETE',)),
    ('/api/v1/groups/{group_id}/users/{user_id}', ('POST',)),
    ('/api/v1/mcp/activities', ('GET',)),
    ('/api/v1/mcp/activities/search/suggestions', ('GET',)),
    ('/api/v1/mcp/activities/types/summary', ('GET',)),
    ('/api/v1/mcp/activities/{activity_id}', ('GET',)),
    ('/api/v1/mcp/activities/{activity_id}/statistics', ('GET',)),
    ('/api/v1/notifications', ('GET',)),
    ('/api/v1/notifications', ('POST',)),
    ('/api/v1/notifications/admin', ('GET',)),
    ('/api/v1/notifications/{oid}', ('DELETE',)),
    ('/api/v1/notifications/{oid}', ('PUT',)),
    ('/api/v1/notifications/{oid}/rollback', ('POST',)),
    ('/api/v1/notifications/{oid}/versions', ('GET',)),
    ('/api/v1/statistics/activities/grades/{grade}/indicators', ('GET',)),
    ('/api/v1/statistics/activities/grades/{grade}/percentiles', ('GET',)),
    ('/api/v1/statistics/activities/{activity_id}/analysis', ('GET',)),
    ('/api/v1/statistics/activities/{activity_id}/description', ('GET',)),
    ('/api/v1/statistics/activities/{activity_id}/layers', ('GET',)),
    ('/api/v1/times', ('GET',)),
    ('/api/v1/users', ('GET',)),
    ('/api/v1/users', ('POST',)),
    ('/api/v1/users/me/device', ('GET',)),
    ('/api/v1/users/me/device-bindings', ('GET',)),
    ('/api/v1/users/me/primary-device', ('DELETE',)),
    ('/api/v1/users/me/primary-device', ('PUT',)),
    ('/api/v1/users/{user_id}', ('DELETE',)),
    ('/api/v1/users/{user_id}', ('GET',)),
    ('/api/v1/users/{user_id}/activities', ('GET',)),
    ('/api/v1/users/{user_id}/ban-records/reset', ('POST',)),
    ('/api/v1/users/{user_id}/banned', ('PUT',)),
    ('/api/v1/users/{user_id}/bindings', ('GET',)),
    ('/api/v1/users/{user_id}/bindings', ('PUT',)),
    ('/api/v1/users/{user_id}/bindings/{xuehai_id}', ('DELETE',)),
    ('/api/v1/users/{user_id}/password', ('DELETE',)),
    ('/api/v1/users/{user_id}/primary-device', ('DELETE',)),
    ('/api/v1/users/{user_id}/roles', ('PUT',)),
    ('/api/v1/users/{user_id}/student-id', ('PUT',)),
    ('/api/v1/users/{user_id}/temp-password', ('POST',)),
    ('/api/v1/users/{user_id}/time', ('GET',)),
    ('/api/v1/users/{user_id}/time_statistics', ('GET',)),
    ('/api/v1/users/{user_id}/totp', ('GET',)),
    ('/api/v2/mcp/activities', ('GET',)),
    ('/api/v2/mcp/activities/search/suggestions', ('GET',)),
    ('/api/v2/mcp/activities/types/summary', ('GET',)),
    ('/api/v2/mcp/activities/{activity_id}', ('GET',)),
    ('/api/v2/mcp/activities/{activity_id}/statistics', ('GET',)),
    ('/api/version', ('GET',)),
]


def _runtime_routes() -> list[tuple[str, tuple[str, ...]]]:
    """收集运行时路由：(path, sorted(methods))，按 path/methods 排序。"""
    rows: list[tuple[str, tuple[str, ...]]] = []
    for route in main.app.routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if methods is None or path is None:
            continue
        rows.append((path, tuple(sorted(methods))))
    rows.sort()
    return rows


def test_route_count_is_frozen():
    assert len(main.app.routes) == TOTAL_ROUTE_OBJECTS


def test_route_table_matches_frozen_snapshot():
    runtime = _runtime_routes()
    if runtime == FROZEN_ROUTES:
        return

    added = Counter(runtime) - Counter(FROZEN_ROUTES)
    removed = Counter(FROZEN_ROUTES) - Counter(runtime)
    lines = [
        f"路由表变了：冻结 {len(FROZEN_ROUTES)} 条 -> 运行时 {len(runtime)} 条"
        "（(path, sorted(methods)) 组合）",
        f"新增 {sum(added.values())} 条：",
        *[
            f"  + {path} {list(methods)}"
            for (path, methods), times in sorted(added.items())
            for _ in range(times)
        ],
        f"删除 {sum(removed.values())} 条：",
        *[
            f"  - {path} {list(methods)}"
            for (path, methods), times in sorted(removed.items())
            for _ in range(times)
        ],
    ]
    if not added and not removed:
        # 集合相同，只有排序不同：多半是比较函数被改坏了。
        for index, (got, want) in enumerate(zip(runtime, FROZEN_ROUTES)):
            if got != want:
                lines.append(f"首个顺序差异 at {index}: {got} != {want}")
                break
    raise AssertionError("\n".join(lines))


def test_socketio_mount_is_frozen():
    mounts = [route for route in main.app.routes if isinstance(route, Mount)]
    assert [mount.path for mount in mounts] == ["/socket.io"]
    assert type(mounts[0].app).__module__.startswith("socketio")


def test_websocket_route_is_frozen():
    websockets = [
        route for route in main.app.routes if isinstance(route, APIWebSocketRoute)
    ]
    assert [(route.path, route.name) for route in websockets] == [
        ("/api/system/ssh/ws", "open_terminal")
    ]
