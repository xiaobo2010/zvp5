"""Stage 2 文件管理端点（契约 §3）。

挂载前缀由 ``main.py`` 决定（``/api/system/files``），本文件内路径不带前缀。

鉴权两层：

1. 系统管理员闸（``_require_system_admin``）：严格 ``admin`` + 即时查库复核，
   与 ``routers/system_router.py`` 同源，不吃 token 里的权限快照。
2. 写操作额外要求另一位管理员的 TOTP（``util/dual_auth.require_dual_auth``）；
   读操作不过双人码，但同样不能访问 ``/proc``、``/sys`` 虚拟文件系统。

同步文件 IO 一律经 ``asyncio.to_thread`` 执行：本服务是单 uvicorn worker，直接在
async 端点里 ``scandir``/``stat``/``write`` 会把事件循环连同 SSH WS 心跳一起卡住。
端点代码保持「一行一个 ``to_thread``」，这样 grep 就能核对是否漏了某个动作。
``download`` 只把 resolve/stat/open 放进线程；响应体的同步生成器由 Starlette
（``iterate_in_threadpool``）自动搬到线程池，不会阻塞事件循环。

所有写操作写一条 ``type=system_file`` 审计，``detail`` 只含操作与路径，不含
文件内容。
"""

from __future__ import annotations

import asyncio
import functools
import urllib.parse
from typing import Iterator

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from db import backends
from services import file_ops
from util.dual_auth import DualAuthError, audit_system_op, require_dual_auth
from util.group import get_user_permissions
from util.object_id import get_current_user

router = APIRouter()


class MkdirRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    totp_code: str = ""


class RenameRequest(BaseModel):
    # 契约里的字段名是 ``from`` / ``to``；``from`` 是 Python 关键字，用 alias 映射。
    model_config = ConfigDict(populate_by_name=True)

    source: str = Field(alias="from", min_length=1, max_length=4096)
    target: str = Field(alias="to", min_length=1, max_length=4096)
    totp_code: str = ""


class DeleteRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    recursive: bool = False
    totp_code: str = ""


class ChmodRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    # strict=True：字符串 "640" 不能被悄悄当成十进制 640（那样会撞上 0o777 上限），
    # 由校验层直接 422，避免前后端对进制理解不一致。
    mode: int = Field(strict=True)
    totp_code: str = ""


async def _require_system_admin(user: dict) -> dict:
    """严格 admin + 即时查库复核（不信 token 里的 per 快照）。"""
    if "admin" not in (user.get("per") or []):
        raise HTTPException(status_code=403, detail="权限不足")

    me = await backends.get_users_repo().get_by_oid(user["id"])
    if me is None:
        raise HTTPException(status_code=403, detail="权限不足")
    if me.get("banned"):
        raise HTTPException(status_code=403, detail="用户已被封禁")
    if "admin" not in (await get_user_permissions(me)):
        raise HTTPException(status_code=403, detail="权限不足")
    return me


async def _approve(user: dict, code: str) -> str:
    """校验另一位管理员的动态码，返回审批人 oid。"""
    try:
        return await require_dual_auth(user, code, action="fs_admin")
    except DualAuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


async def _audit(user: dict, op: str, path: str, approver_oid: str) -> None:
    # 审计里带上审批人，事后才能还原「谁批的这次改动」；细节仍只有 op + 路径。
    await audit_system_op(
        user["id"],
        action="fs_admin",
        detail=f"{op} {path} approver={approver_oid}",
        log_type="system_file",
    )


def _translates_file_errors(handler):
    """把 ``FileOpError`` 翻译成 HTTP 错误；服务层因此不必依赖 FastAPI。"""

    @functools.wraps(handler)
    async def wrapper(*args, **kwargs):
        try:
            return await handler(*args, **kwargs)
        except file_ops.FileOpError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return wrapper


def _ok(data) -> dict:
    return {"status": "ok", "code": 200, "data": data}


@router.get("/list")
@_translates_file_errors
async def list_files(
    path: str = Query(default="/", max_length=4096),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=file_ops.MAX_LIST_LIMIT),
    user=Depends(get_current_user),
):
    await _require_system_admin(user)
    data = await asyncio.to_thread(file_ops.list_dir, path, offset=offset, limit=limit)
    return _ok(data)


@router.get("/stat")
@_translates_file_errors
async def stat_file(
    path: str = Query(..., max_length=4096),
    user=Depends(get_current_user),
):
    await _require_system_admin(user)
    data = await asyncio.to_thread(file_ops.stat_entry, path)
    return _ok(data)


@router.get("/download")
@_translates_file_errors
async def download_file(
    path: str = Query(..., max_length=4096),
    user=Depends(get_current_user),
):
    await _require_system_admin(user)
    handle, size, name = await asyncio.to_thread(file_ops.open_download, path)

    def _stream() -> Iterator[bytes]:
        try:
            while True:
                chunk = handle.read(file_ops.CHUNK_SIZE)
                if not chunk:
                    break
                yield chunk
        finally:
            handle.close()

    quoted = urllib.parse.quote(name)
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{quoted}",
        "Content-Length": str(size),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(
        _stream(), media_type="application/octet-stream", headers=headers
    )


@router.post("/mkdir")
@_translates_file_errors
async def make_dir(payload: MkdirRequest, user=Depends(get_current_user)):
    await _require_system_admin(user)
    approver = await _approve(user, payload.totp_code)

    data = await asyncio.to_thread(file_ops.make_dir, payload.path)
    await _audit(user, "mkdir", data["path"], approver)
    return _ok(data)


@router.post("/upload")
@_translates_file_errors
async def upload_file(
    user=Depends(get_current_user),
    path: str = Form(..., max_length=4096),
    file: UploadFile = File(...),
    overwrite: bool = Form(default=False),
    totp_code: str = Form(default=""),
):
    await _require_system_admin(user)
    approver = await _approve(user, totp_code)

    data = await file_ops.save_upload(
        path, file.filename, file.read, overwrite=overwrite
    )
    await _audit(user, "upload", data["path"], approver)
    return _ok(data)


@router.post("/rename")
@_translates_file_errors
async def rename_file(payload: RenameRequest, user=Depends(get_current_user)):
    await _require_system_admin(user)
    approver = await _approve(user, payload.totp_code)

    data = await asyncio.to_thread(
        file_ops.rename_path, payload.source, payload.target
    )
    await _audit(user, "rename", f"{data['from']} -> {data['to']}", approver)
    return _ok(data)


@router.post("/delete")
@_translates_file_errors
async def delete_file(payload: DeleteRequest, user=Depends(get_current_user)):
    await _require_system_admin(user)
    approver = await _approve(user, payload.totp_code)

    data = await asyncio.to_thread(
        file_ops.delete_path, payload.path, recursive=payload.recursive
    )
    await _audit(user, "delete", data["path"], approver)
    return _ok(data)


@router.post("/chmod")
@_translates_file_errors
async def chmod_file(payload: ChmodRequest, user=Depends(get_current_user)):
    await _require_system_admin(user)
    approver = await _approve(user, payload.totp_code)

    data = await asyncio.to_thread(file_ops.chmod_path, payload.path, payload.mode)
    await _audit(user, "chmod", f"{data['path']} {oct(data['mode'])}", approver)
    return _ok(data)
