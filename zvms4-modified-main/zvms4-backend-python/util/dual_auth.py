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
import re
import time

from sqlalchemy.exc import IntegrityError

from db import backends
from typings.log import ZVMSLog
from util.cert import match_totp_counter

# 6 位数字是 TOTP 的唯一合法形态；其它形态一律 422，不消耗限流额度（那属于
# 客户端 bug，不是猜码）。
_CODE_RE = re.compile(r"^[0-9]{6}$")

_FAIL_WINDOW_SECONDS = 60.0
# 单 worker 前提：计数存在进程内存里，多 worker / 多机部署时每个进程各自计数，
# 实际阈值会按进程数放大。真要横向扩容，把这里换成共享存储（Redis / DB）再谈。
_FAIL_LIMIT = 5

# operator_oid -> 最近失败时刻（time.monotonic，不受系统时间跳变影响）。
_failures: dict[str, list[float]] = {}


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


async def _write_audit(
    *,
    action: str,
    operator_oid: str,
    approver_oid: str = "",
    reason: str = "",
) -> None:
    """写一条双人授权审计。``reason`` 只在服务端留存，不会回给客户端。"""
    payload: dict[str, str] = {"action": action, "operator": operator_oid}
    if approver_oid:
        payload["approver"] = approver_oid
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
        # 把失败原因打到 stderr 留痕即可。
        print(f"Failed to write dual-auth log: {exc}")


async def audit_system_op(
    operator_oid: str,
    *,
    action: str,
    detail: str = "",
    log_type: str = "system_op",
    level: str = "warning",
) -> None:
    """给三块系统能力的普通操作留审计（同样只写结果，不写凭证）。"""
    try:
        await ZVMSLog(
            url=f"system:{action}",
            user=operator_oid,
            data=json.dumps(
                {"action": action, "operator": operator_oid, "detail": detail},
                ensure_ascii=False,
            ),
            type=log_type,
            level=level,
        ).insert_log()
    except Exception as exc:  # noqa: BLE001
        print(f"Failed to write system op log: {exc}")


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
