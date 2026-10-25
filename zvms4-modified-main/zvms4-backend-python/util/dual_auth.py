"""双人授权守卫：高危系统操作必须由**另一位**管理员现场提供 TOTP 动态码。

Stage 2 的三块能力（Web SSH 终端 / 全盘文件管理 / 数据库内容管理）共用本模块，
各自只在路由里调用 :func:`require_dual_auth`，鉴权细节不重复实现。

设计要点：

* 校验对象是「另一位 admin」：绝不接受操作者自己的码，也绝不会因为系统里只有
  一个 admin 就放行——否则双人授权退化成单人确认。
* 拒绝提示统一为「动态码无效或已过期」：不透露命中的是谁，也不透露候选管理员
  有几个；这两条信息都能用来缩小爆破空间。
* 防重放由 ``dual_auth_uses`` 的唯一约束 ``(approver_oid, otp_counter)`` 做
  原子裁决，**不做「先查后插」**：并发两个请求在同一窗口用同一个码时，先查后插
  会双双读到「未使用」。
* 失败限流是**进程内**计数，前提是单 worker 部署（见 :data:`_FAIL_LIMIT`）。
"""

from __future__ import annotations

import json
import logging
import re
import time

from sqlalchemy.exc import IntegrityError

from db import backends
from typings.log import ZVMSLog
from util.cert import match_totp_counter

logger = logging.getLogger(__name__)

# 6 位数字是 TOTP 的唯一合法形态；其它形态一律 422，不消耗限流额度（那属于
# 客户端 bug，不是猜码）。
_CODE_RE = re.compile(r"^[0-9]{6}$")

_FAIL_WINDOW_SECONDS = 60.0
# 单 worker 前提：计数存在进程内存里，多 worker / 多机部署时每个进程各自计数，
# 实际阈值会按进程数放大。真要横向扩容，把这里换成共享存储（Redis / DB）再谈。
_FAIL_LIMIT = 5

# operator_oid -> 最近失败时刻（time.monotonic，不受系统时间跳变影响）。
_failures: dict[str, list[float]] = {}

# ---------------------------------------------------------------------------
# 授权缓存（30 分钟免二次验证）
#
# 只服务**显式 opt-in** 的调用方（目前只有 agent 写操作确认）。语义是「同一个
# operator、同一个 action 刚刚被另一位管理员批准过，短时间内不必再输一次码」，
# 不是「登录一次就免检」：
#
# * 键是 ``(operator_oid, action)``：换人 / 换动作都不继承（``agent_write`` 的
#   授权不会顺带解锁 ``web_ssh``）；
# * 只记**成功**的校验；失败仍然走 ``_failures`` 限流，且不会刷新缓存；
# * TTL 用 ``time.monotonic``，系统时间跳变不会把窗口拉长；
# * ``DUAL_AUTH_CACHE_TTL_SECONDS=0`` 可整体关闭（回到「每次都验」）；
# * 每次命中缓存都写一条 ``system_dual_auth`` 审计并标 ``cached``：事后必须能
#   区分「这次真的输了码」与「这次吃的是 30 分钟内的授权」。
#
# 进程内存储与 ``_failures`` / SSH ticket 同一个前提（单 worker）。多 worker 时
# 每个进程各自缓存，窗口行为会变成「命中率降低」而不是「越权」——保守方向。
# ---------------------------------------------------------------------------
_GRANT_TTL_DEFAULT_SECONDS = 1800  # 30 分钟
_GRANT_TTL_MAX_SECONDS = 86_400  # 配置写大了也不超过一天

# (operator_oid, action) -> (approver_oid, 到期时刻 monotonic)
_grants: dict[tuple[str, str], tuple[str, float]] = {}



class DualAuthError(Exception):
    """守卫拒绝；路由层转 ``HTTPException(status_code, detail)`` 即可。"""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _operator_oid(operator: dict) -> str:
    """当前登录管理员的 oid；``_id``（DB 文档形状）与 ``id``（token 形状）都认。"""
    value = operator.get("_id") or operator.get("id") or ""
    return str(value)


def _recent_failures(operator_oid: str, now: float) -> list[float]:
    stamps = [t for t in _failures.get(operator_oid, ()) if now - t < _FAIL_WINDOW_SECONDS]
    if stamps:
        _failures[operator_oid] = stamps
    else:
        _failures.pop(operator_oid, None)
    return stamps


def _record_failure(operator_oid: str) -> None:
    now = time.monotonic()
    stamps = _recent_failures(operator_oid, now)
    stamps.append(now)
    _failures[operator_oid] = stamps


def _is_unique_violation(exc: BaseException) -> bool:
    """唯一约束冲突判定。

    SQLAlchemy 会把 DBAPI 的完整性错误包成 :class:`IntegrityError`；直接拿
    ``oracledb`` 异常时错误码以 ORA-00001 出现。两条路都认，避免依赖某一种包装。
    """
    if isinstance(exc, IntegrityError):
        return True
    return "ORA-00001" in str(getattr(exc, "orig", exc))


# ---------------------------------------------------------------------------
# 30 分钟授权缓存
# ---------------------------------------------------------------------------


def dual_auth_cache_ttl_seconds() -> int:
    """授权窗口秒数；``0`` = 关闭缓存（回到每次都验）。

    配置项是 ``settings.DUAL_AUTH_CACHE_TTL_SECONDS``。非法值回退默认 1800，
    并且上限压到一天——配置写错成 ``864000`` 等于把双人授权废掉，不能静默生效。
    """
    try:
        import settings as site_settings

        raw = getattr(
            site_settings, "DUAL_AUTH_CACHE_TTL_SECONDS", _GRANT_TTL_DEFAULT_SECONDS
        )
    except Exception:  # noqa: BLE001 - 读不到配置就回默认值
        return _GRANT_TTL_DEFAULT_SECONDS
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        return _GRANT_TTL_DEFAULT_SECONDS
    return min(raw, _GRANT_TTL_MAX_SECONDS)


def cached_approval(operator: dict, *, action: str) -> str | None:
    """``(operator, action)`` 仍在窗口内则返回审批人 oid；否则 ``None``。"""
    if dual_auth_cache_ttl_seconds() <= 0:
        return None
    key = (_operator_oid(operator), str(action))
    entry = _grants.get(key)
    if entry is None:
        return None
    approver_oid, expires_at = entry
    if time.monotonic() >= expires_at:
        _grants.pop(key, None)
        return None
    return approver_oid


def remember_approval(
    operator: dict, *, action: str, approver_oid: str, ttl: float | None = None
) -> None:
    """记下一次成功的双人授权（只由 :func:`require_dual_auth_cached` 调用）。"""
    seconds = float(dual_auth_cache_ttl_seconds() if ttl is None else ttl)
    if seconds <= 0:
        return
    _grants[(_operator_oid(operator), str(action))] = (
        str(approver_oid),
        time.monotonic() + seconds,
    )


def clear_grants() -> None:
    """清空授权缓存（测试与「立即失效」场景用）。"""
    _grants.clear()


async def require_dual_auth_cached(
    operator: dict, code: str, *, action: str
) -> tuple[str, bool]:
    """双人授权 + 30 分钟缓存：返回 ``(approver_oid, 是否命中缓存)``。

    窗口内**不再要求**动态码（``code`` 可以是空串）；窗口外与
    :func:`require_dual_auth` 完全一致（空串 → 422 格式错误，由调用方翻译成
    「需要二次验证」）。缓存命中也会写一条审计并标 ``cached=true``，否则事后
    无法区分「真输了码」与「吃缓存」。
    """
    approver_oid = cached_approval(operator, action=action)
    if approver_oid is not None:
        await _write_audit(
            action=action,
            operator_oid=_operator_oid(operator),
            approver_oid=approver_oid,
            cached=True,
        )
        return approver_oid, True

    approver_oid = await require_dual_auth(operator, code, action=action)
    remember_approval(operator, action=action, approver_oid=approver_oid)
    return approver_oid, False


async def resolve_names(oids: list[str]) -> dict[str, str]:
    """批量把 oid 解析成姓名（一次 ``get_names``，不按行查）。

    审计的首要职责是**写得下**：取不到名字（用户已删、库抖动、替身 repo 没有
    该方法）绝不能连审计一起丢，所以任何异常都降级成空表，由调用方回落成
    空字符串——oid 仍在 payload 里，溯源不丢。
    """
    wanted = [oid for oid in dict.fromkeys(oids) if oid]
    if not wanted:
        return {}
    try:
        return await backends.get_users_repo().get_names(wanted)
    except Exception as exc:  # noqa: BLE001 - 取名失败不影响审计落库
        logger.warning("failed to resolve names for audit: %s", type(exc).__name__)
        return {}


async def _write_audit(
    *,
    action: str,
    operator_oid: str,
    approver_oid: str = "",
    reason: str = "",
    cached: bool = False,
) -> None:
    """写一条双人授权审计。``reason`` 只在服务端留存，不会回给客户端。

    ``data`` 同时带 oid 与姓名：oid 是机器可溯源的键，姓名是给管理员看的。
    ``ZVMSLog.user`` 固定写 oid —— 显示的姓名由日志列表的 ``users`` 连接解
    析，写路径不承担展示职责。

    ``cached=True`` 表示这次没有真的校验动态码，而是命中了 30 分钟授权缓存。
    该键只在为真时出现，既有审计形状（含测试断言）保持不变。
    """
    names = await resolve_names([operator_oid, approver_oid])
    payload: dict[str, str] = {
        "action": action,
        "operator": operator_oid,
        "operator_name": names.get(operator_oid, ""),
    }
    if approver_oid:
        payload["approver"] = approver_oid
        payload["approver_name"] = names.get(approver_oid, "")
    if cached:
        payload["cached"] = "true"
    if reason:
        payload["reason"] = reason
    try:
        await ZVMSLog(
            url=f"system:{action}",
            user=operator_oid,
            data=json.dumps(payload, ensure_ascii=False),
            type="system_dual_auth",
            level="warning",
        ).insert_log()
    except Exception as exc:  # noqa: BLE001 - 审计写失败不该改变鉴权结论
        # 结论（放行 / 拒绝）已经判定完毕，这里再抛异常只会让响应码不可预测；
        # 把失败原因打到日志留痕即可。
        logger.warning("failed to write dual-auth audit: %s", type(exc).__name__)


async def audit_system_op(
    operator_oid: str,
    *,
    action: str,
    detail: str = "",
    log_type: str = "system_op",
    level: str = "warning",
) -> None:
    """给三块系统能力的普通操作留审计（同样只写结果，不写凭证）。

    ``detail`` 原样保留（调用方已经写好人类可读的上下文），额外补
    ``operator_name``，免得管理员在日志页只看到串 oid。
    """
    names = await resolve_names([operator_oid])
    try:
        await ZVMSLog(
            url=f"system:{action}",
            user=operator_oid,
            data=json.dumps(
                {
                    "action": action,
                    "operator": operator_oid,
                    "operator_name": names.get(operator_oid, ""),
                    "detail": detail,
                },
                ensure_ascii=False,
            ),
            type=log_type,
            level=level,
        ).insert_log()
    except Exception as exc:  # noqa: BLE001
        logger.warning("failed to write system op audit: %s", type(exc).__name__)


async def require_dual_auth(operator: dict, code: str, *, action: str) -> str:
    """校验「另一位管理员」的动态码，成功返回审批人 oid。

    ``action`` 是调用方的动作标识（如 ``"web_ssh"`` / ``"db_ddl"``），会写进
    审计与使用记录，便于回溯某个动态码到底批准了什么。
    """
    operator_oid = _operator_oid(operator)
    if not operator_oid:
        # 认证链路给了空 oid：无法判定「另一位」，只能拒绝。
        raise DualAuthError(403, "动态码无效或已过期")

    now = time.monotonic()
    if len(_recent_failures(operator_oid, now)) >= _FAIL_LIMIT:
        await _write_audit(action=action, operator_oid=operator_oid, reason="失败次数超限")
        raise DualAuthError(429, "尝试过于频繁，请稍后再试")

    if not isinstance(code, str) or not _CODE_RE.fullmatch(code):
        await _write_audit(action=action, operator_oid=operator_oid, reason="动态码格式非法")
        raise DualAuthError(422, "动态码格式错误：需为 6 位数字")

    repo = backends.get_users_repo()
    admins = await repo.list_admin_oids()
    candidates = [str(oid) for oid in admins if str(oid) != operator_oid]

    for approver_oid in candidates:
        counter = match_totp_counter(approver_oid, code)
        if counter is None:
            continue
        try:
            await repo.insert_dual_auth_use(approver_oid, counter, action, operator_oid)
        except Exception as exc:  # noqa: BLE001 - 只把唯一冲突翻译成 409
            if _is_unique_violation(exc):
                await _write_audit(
                    action=action,
                    operator_oid=operator_oid,
                    approver_oid=approver_oid,
                    reason="动态码已被使用",
                )
                raise DualAuthError(409, "该动态码已被使用") from exc
            raise
        await _write_audit(
            action=action, operator_oid=operator_oid, approver_oid=approver_oid
        )
        return approver_oid

    # 码对不上（或根本没有第二位管理员）：对外提示完全一致，只有审计里区分原因。
    _record_failure(operator_oid)
    await _write_audit(
        action=action,
        operator_oid=operator_oid,
        reason="无其他管理员可审批" if not candidates else "动态码无效或已过期",
    )
    raise DualAuthError(403, "动态码无效或已过期")
