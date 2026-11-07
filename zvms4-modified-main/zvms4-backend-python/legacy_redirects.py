"""单数 → 复数路由兼容层（6 条 307 重定向）。

前端历史代码还在打 ``/api/user/...`` 这类单数路径，这里整条转发到复数路由。
只做 ``RedirectResponse``，不改任何状态码/查询串：``request.url.replace()`` 保留
原始 query 与 scheme/host（反代之后仍指向外部可见地址）。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse


def register(app: FastAPI) -> None:
    """按原顺序注册 6 条重定向：user / activity / group / export / import / log。"""

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
