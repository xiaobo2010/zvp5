"""角色策略端点（``/api/system/role-policies``）。

为什么单独一个 router：``system_router`` 只负责维护模式，``system_settings_router``
是「系统设置项」，而这里是「角色 → 能力矩阵」的两个资源段（管理视图 + 自助视图）。
三者共用同一套守卫 :func:`routers.system_router._require_system_admin`（严格
``admin`` + 实时查库复核，不信 token 快照），未来双人授权的挂点也只有那一处。

鉴权三层，顺序固定（与 ``system_db_content_router`` 同形）：

1. 管理员闸：``GET`` 与 ``PUT`` 都是严格 admin。
2. 写操作：另一位管理员的 TOTP（``util.dual_auth.require_dual_auth``）——
   改一个角色的开关等于改所有人的能力边界，属于「事后必须能回答是谁批准的」。
3. 落库：**鉴权全部通过之后**才碰数据库。

``GET /me`` 是唯一对普通登录人开放的端点：它只回**本人**的生效策略，不传
任何 oid，也就没有「查别人」的入口。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import DatabaseError, InterfaceError, OperationalError

from routers.system_router import _require_system_admin
from services.roles import policy
from util.dual_auth import DualAuthError, audit_system_op, require_dual_auth
from util.object_id import DB_UNAVAILABLE_DETAIL, get_current_user

router = APIRouter()

logger = logging.getLogger(__name__)

try:  # 驱动层异常可能没被 SQLAlchemy 包成 DBAPIError（如连接中途断开）
    import oracledb

    _DRIVER_ERRORS: tuple[type[BaseException], ...] = (oracledb.Error,)
except ImportError:  # pragma: no cover - oracledb 是硬依赖，仅为导入路径兜底
    _DRIVER_ERRORS = ()

# 落库失败 = 库暂时不可用（可重试），不是请求写错了：这一层唯一的写动作是
# 「单事务全量替换」，除库抖动外没有第二种失败形态。
_DB_UNAVAILABLE_ERRORS: tuple[type[BaseException], ...] = (
    OperationalError,
    DatabaseError,
    InterfaceError,
    *_DRIVER_ERRORS,
)


# 单个角色单项月额度的上限。取得太大等于「无限」但又不是无限（admin 才是），
# 超过这个数一律 400：多半是前端把「不限」编码成了一个大数字。
QUOTA_MAX = 100_000_000

# 审计里 detail 的长度上限，与 ``system_db_content_router`` 保持一致。
_AUDIT_DETAIL_LIMIT = 500


class RolePolicyUpdateRequest(BaseModel):
    # 两个字典都放开成 ``dict``，由本文件做**显式校验**并回 400：Pydantic 的
    # ``ge``/``le`` 会给出 422，而契约要求「未知 key / 越界都是 400」。
    features: dict[str, Any] = Field(default_factory=dict)
    quotas: dict[str, Any] = Field(default_factory=dict)
    # 空字符串交给守卫判 422（6 位数字），避免 Pydantic 的必填报错盖掉中文提示。
    totp_code: str = ""


def _bad_request(reason: str) -> HTTPException:
    return HTTPException(status_code=400, detail=reason)


def _ok(data: Any) -> dict[str, Any]:
    return {"status": "ok", "code": 200, "data": data}


def _normalize_features(raw: dict[str, Any]) -> dict[str, bool]:
    """校验并规范化功能开关：未知 key / 非布尔值都是 400（不静默丢弃）。"""
    unknown = [key for key in raw if key not in policy.FEATURE_KEYS]
    if unknown:
        raise _bad_request(f"未知的功能项：{', '.join(sorted(unknown))}")

    normalized: dict[str, bool] = {}
    for key, value in raw.items():
        if not isinstance(value, bool):
            raise _bad_request(f"{key}: 必须是布尔值")
        normalized[key] = value
    return normalized


def _normalize_quotas(raw: dict[str, Any]) -> dict[str, int | None]:
    """校验并规范化月额度：必须列出三个 kind，值是非负整数或 ``null``。

    ``null`` 不是 0：它表示「这个 kind 不落覆盖，回落到代码默认」
    （前端把「用系统默认」编码成 null，0 表示「该角色不开放」——两者不能折叠）。

    要求**完整**（而不是「只改传上来的那个」）：PUT 的语义是「这个角色的完整
    矩阵」，缺一个 kind 会被当作 null 还是「不变」永远说不清；直接拒掉最省事，
    前端本来就是整表提交。
    """
    unknown = [kind for kind in raw if kind not in policy.QUOTA_KINDS]
    if unknown:
        raise _bad_request(f"未知的额度类型：{', '.join(sorted(unknown))}")

    missing = [kind for kind in policy.QUOTA_KINDS if kind not in raw]
    if missing:
        raise _bad_request(f"缺少额度：{', '.join(missing)}")

    normalized: dict[str, int | None] = {}
    for kind in policy.QUOTA_KINDS:
        value = raw[kind]
        if value is None:
            normalized[kind] = None
            continue
        # ``bool`` 是 ``int`` 的子类：``true`` 不该被当成 1。
        if isinstance(value, bool) or not isinstance(value, int):
            raise _bad_request(f"{kind}: 额度必须是整数或 null")
        if value < 0 or value > QUOTA_MAX:
            raise _bad_request(f"{kind}: 额度必须在 0 到 {QUOTA_MAX} 之间")
        normalized[kind] = value
    return normalized


def _summary(features: dict[str, bool], quotas: dict[str, int | None]) -> str:
    """审计摘要：只记「开了哪些 + 三个额度」，不记没开的格子（体积可控且够用）。"""
    enabled = ",".join(sorted(key for key, value in features.items() if value)) or "-"
    values = " ".join(
        f"{kind}={'default' if quotas[kind] is None else quotas[kind]}"
        for kind in policy.QUOTA_KINDS
    )
    return f"features={len(features)} enabled={enabled} {values}"


# --------------------------------------------------------------------------- #
# 管理视图
# --------------------------------------------------------------------------- #


@router.get("")
async def read_role_policies(user=Depends(get_current_user)):
    """三个可编辑角色的生效矩阵 + 键清单（管理页整页数据）。"""
    await _require_system_admin(user)
    return _ok(await policy.all_policies(force=True))


@router.put("")
async def update_missing_role(user=Depends(get_current_user)):
    """没带角色的写请求：与「未知角色」同一个出口（400），不是 405。

    ``PUT /api/system/role-policies`` 看着像「批量更新」，但这里只接受单角色
    全量替换，直接说清楚比让 Starlette 回一个 Method Not Allowed 更有用。
    """
    await _require_system_admin(user)
    raise _bad_request("缺少角色：PUT /api/system/role-policies/{role}")


@router.put("/{role}")
async def update_role_policy(
    role: str,
    payload: RolePolicyUpdateRequest,
    user=Depends(get_current_user),
):
    """**全量替换**一个角色的矩阵；需要另一位管理员的动态码。"""
    await _require_system_admin(user)

    if role not in policy.EDITABLE_ROLES:
        # 400 而不是 404：``admin`` 是真实存在但不可编辑的角色（代码短路），
        # 与拼错的角色名走同一个出口即可，前端只需要一句「不可编辑」。
        raise _bad_request(
            f"角色 {role} 不可编辑（可选：{', '.join(policy.EDITABLE_ROLES)}）"
        )

    features = _normalize_features(payload.features)
    quotas = _normalize_quotas(payload.quotas)

    try:
        approver = await require_dual_auth(
            user, payload.totp_code, action="role_policy"
        )
    except DualAuthError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    try:
        data = await policy.replace_role(role, features, quotas, user["id"])
    except _DB_UNAVAILABLE_ERRORS as exc:
        # 鉴权全部通过之后才碰库，所以失败点只可能是库本身（例如 ADB 上的
        # ``ORA-12838``）。不回 500：前端拿到的应当是一句「稍后重试」。
        # 日志带角色名与操作人并保留栈——事后要能回答「谁改哪个角色时库挂了」。
        logger.warning(
            "role_policy write failed: role=%s operator=%s",
            role,
            user.get("id"),
            exc_info=True,
        )
        raise HTTPException(
            status_code=503, detail=DB_UNAVAILABLE_DETAIL
        ) from exc

    await audit_system_op(
        user["id"],
        action="role_policy",
        detail=f"role={role} {_summary(features, quotas)} approver={approver}"[
            :_AUDIT_DETAIL_LIMIT
        ],
        # 复用既有 ltype：角色策略与「在线设置」是同一类改动（改系统行为、需要
        # 事后回答是谁改的），不新造一个只有这里会写的日志类型。
        log_type="system_settings_update",
    )
    return _ok(data)


# --------------------------------------------------------------------------- #
# 自助视图
# --------------------------------------------------------------------------- #


@router.get("/me")
async def read_my_policy(user=Depends(get_current_user)):
    """本人生效策略：任何登录人可读，且**只**回自己的。"""
    return _ok(await policy.effective_policy(user.get("per") or []))


__all__ = ["router"]
