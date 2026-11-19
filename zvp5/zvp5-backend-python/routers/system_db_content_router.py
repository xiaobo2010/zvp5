"""数据库内容管理端点（Stage 2，契约 §4）。

挂载前缀由 ``main.py`` 决定（``/api/system/db/content``），本文件内路径不带前缀。

鉴权三层，顺序固定：

1. 管理员闸 ``_require_system_admin``：严格 ``admin`` + 即时查库复核，不吃 token
   里的权限快照（与 ``routers/system_router.py`` 同源）。
2. 写操作：另一位管理员的 TOTP（``util/dual_auth.require_dual_auth``）。
3. 会话：**鉴权全部通过之后**才开数据库会话 —— 所以双人码失败时一行 SQL 都
   不会执行（用例直接断言替身会话的 ``execute`` 调用数为 0）。

``/query`` 不带双人码，但只读闸在服务层（``services/db_content``）：多语句、写
关键字、非 SELECT/WITH 开头一律 400。写入口只有带双人码的 ``/ddl`` 和行 CRUD。
"""

from __future__ import annotations

import functools
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field

from db import backends, cache
from services import db_content
from util.dual_auth import DualAuthError, audit_system_op, require_dual_auth
from util.ban import ban_is_active, raise_banned
from util.group import get_user_permissions
from util.object_id import get_current_user

router = APIRouter()

# 审计 detail 上限（契约 §4.4.6：语句摘要 ≤ 500 字）。
_AUDIT_DETAIL_LIMIT = 500


class RowInsertRequest(BaseModel):
    values: dict[str, Any]
    totp_code: str = ""


class RowUpdateRequest(BaseModel):
    key: dict[str, Any]
    values: dict[str, Any]
    totp_code: str = ""


class RowDeleteRequest(BaseModel):
    key: dict[str, Any]
    totp_code: str = ""


class QueryRequest(BaseModel):
    # 长度上限只是防「一次粘贴 10MB」；真正的合法性由只读闸判定。
    sql: str = Field(min_length=1, max_length=20000)
    # 契约 §4.3.3：limit ≤ 500。超过直接 422，不静默截断（与 files 车道一致）。
    limit: int = Field(default=100, ge=1, le=db_content.MAX_QUERY_LIMIT)


class DdlRequest(BaseModel):
    sql: str = Field(min_length=1, max_length=20000)
    confirm: str | None = None
    # 空字符串交给守卫判 422（6 位数字），避免 Pydantic 的必填报错盖掉中文提示。
    totp_code: str = ""


async def _require_system_admin(user: dict) -> dict:
    """严格 admin + 即时查库复核（不信 token 里的 per 快照）。"""
    if "admin" not in (user.get("per") or []):
        raise HTTPException(status_code=403, detail="权限不足")

    me = await backends.get_users_repo().get_by_oid(user["id"])
    if me is None:
        raise HTTPException(status_code=403, detail="权限不足")
    # 到期封禁会保留 banned=1，只有 ban_is_active 才能反映「此刻是否可放行」。
    if ban_is_active(me):
        raise_banned(me)
    if "admin" not in (await get_user_permissions(me)):
        raise HTTPException(status_code=403, detail="权限不足")
    return me


async def _approve(user: dict, code: str, *, action: str) -> str:
    """校验另一位管理员的动态码，返回审批人 oid。"""
    try:
        return await require_dual_auth(user, code, action=action)
    except DualAuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _translates_content_errors(handler):
    """把 ``DbContentError`` 翻译成 HTTP 错误；服务层因此不依赖 FastAPI。"""

    @functools.wraps(handler)
    async def wrapper(*args, **kwargs):
        try:
            return await handler(*args, **kwargs)
        except db_content.DbContentError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    return wrapper


def _ok(data: Any) -> dict[str, Any]:
    return {"status": "ok", "code": 200, "data": data}


async def _invalidate_read_caches() -> None:
    """写库之后按域自增缓存版本号。

    控制台能改任意表的任意行，缓存代理根本看不到这次写（它只包了仓储方法），
    所以失效只能在这里补。用 ``bump_epoch`` 而不是 ``flush_scopes``：O(1)、不发
    SCAN，旧 key 靠 TTL 自然淘汰。必须覆盖 ``cache.SCOPE_PREFIXES`` 里的全部域
    （漏一个，控制台改完那类数据仍旧从缓存读到旧值）。
    """
    for scope in (
        "users",
        "groups",
        "activities",
        "conversations",
        "notifications",
        "changelog",
        "roles",
        "settings",
    ):
        await cache.bump_epoch(scope)


async def _audit(user: dict, action: str, detail: str, *, log_type: str) -> None:
    """写一条审计。``detail`` 只含语句摘要/列名，**永不含列值**。

    列值里可能有密码哈希、动态码种子之类的敏感字段，审计表的读者远比操作者多。
    """
    await audit_system_op(
        user["id"],
        action=action,
        detail=detail[:_AUDIT_DETAIL_LIMIT],
        log_type=log_type,
    )


# --------------------------------------------------------------------------- #
# 只读
# --------------------------------------------------------------------------- #


@router.get("/schema")
@_translates_content_errors
async def read_schema(user=Depends(get_current_user)):
    await _require_system_admin(user)
    async with db_content.session_scope() as session:
        return _ok(await db_content.list_schema(session))


@router.get("/table/{name}/columns")
@_translates_content_errors
async def read_columns(
    name: str = Path(..., max_length=64),
    user=Depends(get_current_user),
):
    await _require_system_admin(user)
    async with db_content.session_scope() as session:
        return _ok(await db_content.get_columns(session, name))


@router.get("/table/{name}/rows")
@_translates_content_errors
async def read_rows(
    name: str = Path(..., max_length=64),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=db_content.MAX_ROW_LIMIT),
    order_by: str | None = Query(default=None, max_length=64),
    desc: bool = Query(default=False),
    user=Depends(get_current_user),
):
    await _require_system_admin(user)
    async with db_content.session_scope() as session:
        data = await db_content.read_rows(
            session, name, offset=offset, limit=limit, order_by=order_by, desc=desc
        )
    await _audit(
        user,
        "db_read",
        f"read {data['table']} offset={data['offset']} rows={len(data['rows'])}",
        log_type="system_db_read",
    )
    return _ok(data)


@router.post("/query")
@_translates_content_errors
async def run_readonly_query(
    payload: QueryRequest,
    user=Depends(get_current_user),
):
    """只读查询：不带双人码，但只读闸在服务层（契约 §4.3）。"""
    await _require_system_admin(user)
    async with db_content.session_scope() as session:
        data = await db_content.run_query(session, payload.sql, limit=payload.limit)
    # Query text can contain column values in literals; the audit row records
    # only shape/count/timing, never the SQL body.
    await _audit(
        user,
        "db_query",
        f"query rows={len(data['rows'])} truncated={data['truncated']} "
        f"elapsed_ms={data['elapsed_ms']}",
        log_type="system_db_query",
    )
    return _ok(data)


# --------------------------------------------------------------------------- #
# 行写操作（双人码 + 按主键定位）
# --------------------------------------------------------------------------- #


@router.post("/table/{name}/rows")
@_translates_content_errors
async def insert_row(
    payload: RowInsertRequest,
    name: str = Path(..., max_length=64),
    user=Depends(get_current_user),
):
    await _require_system_admin(user)
    approver = await _approve(user, payload.totp_code, action="db_dml")

    async with db_content.session_scope() as session:
        data = await db_content.insert_row(session, name, payload.values)

    columns = ",".join(sorted(str(key).upper() for key in payload.values))
    await _audit(
        user,
        "db_dml",
        f"insert {name.upper()} ({columns}) approver={approver}",
        log_type="system_db_dml",
    )
    await _invalidate_read_caches()
    return _ok(data)


@router.put("/table/{name}/rows")
@_translates_content_errors
async def update_row(
    payload: RowUpdateRequest,
    name: str = Path(..., max_length=64),
    user=Depends(get_current_user),
):
    await _require_system_admin(user)
    approver = await _approve(user, payload.totp_code, action="db_dml")

    async with db_content.session_scope() as session:
        data = await db_content.update_row(
            session, name, payload.key, payload.values
        )

    columns = ",".join(sorted(str(key).upper() for key in payload.values))
    await _audit(
        user,
        "db_dml",
        f"update {name.upper()} ({columns}) approver={approver}",
        log_type="system_db_dml",
    )
    await _invalidate_read_caches()
    return _ok(data)


@router.delete("/table/{name}/rows")
@_translates_content_errors
async def delete_row(
    payload: RowDeleteRequest,
    name: str = Path(..., max_length=64),
    user=Depends(get_current_user),
):
    await _require_system_admin(user)
    approver = await _approve(user, payload.totp_code, action="db_dml")

    async with db_content.session_scope() as session:
        data = await db_content.delete_row(session, name, payload.key)

    await _audit(
        user,
        "db_dml",
        f"delete {name.upper()} approver={approver}",
        log_type="system_db_dml",
    )
    await _invalidate_read_caches()
    return _ok(data)


# --------------------------------------------------------------------------- #
# DDL / 写语句
# --------------------------------------------------------------------------- #


@router.post("/ddl")
@_translates_content_errors
async def run_ddl(
    payload: DdlRequest,
    user=Depends(get_current_user),
):
    await _require_system_admin(user)

    # 先做纯静态校验：非法/缺 confirm/自保护表都在开会话之前就拒掉，
    # 所以这些失败路径连一条 SQL 都不会发出去。
    plan = db_content.validate_ddl_sql(payload.sql, payload.confirm)
    action = "db_ddl" if plan["is_ddl"] else "db_dml"
    log_type = "system_db_ddl" if plan["is_ddl"] else "system_db_dml"

    approver = await _approve(user, payload.totp_code, action=action)

    async with db_content.session_scope() as session:
        data = await db_content.run_ddl(session, payload.sql, payload.confirm)

    await _audit(
        user,
        action,
        f"{plan['summary']} approver={approver}",
        log_type=log_type,
    )
    await _invalidate_read_caches()
    return _ok(data)
