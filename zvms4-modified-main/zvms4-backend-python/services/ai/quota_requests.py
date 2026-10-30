"""AI 配额重置申请：提交 / 审批 / 管理视图的唯一入口。

额度用尽后的**唯一**自助路径。三个刻意的边界：

1. **申请只对当月有效**。``period`` 与 ``AI_QUOTA`` 同口径（``YYYYMM``，本地时区），
   下个月额度本来就重置，不需要「跨月继承」这种藏起来第二套语义。
2. **审批复用既有计费封装**。``decide`` 里的重置/设额调用
   ``services.ai.quota.reset_used`` / ``set_user_limit``，本模块**不碰**
   ``AI_QUOTA`` 的读写——第二套计费逻辑就是第二套 bug。
3. **状态迁移由仓储的条件更新裁决**。服务层的「非 pending → 409」是给用户看的
   快路径，真正的并发闸门是 ``WHERE status = 'pending'``（两个审批人同时点通过
   时只有一个能改到，另一个拿 409）。

审计走 ``util.dual_auth.audit_system_op``：一次额度审批要能事后回答「谁、什么时候、
批了谁的什么」。审计写失败不影响业务结论（该函数内部已吞异常）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Iterable

from db import backends
from services import log_events
from util.dual_auth import audit_system_op

from . import quota

logger = logging.getLogger(__name__)

#: 可申请的能力。与 ``ck_ai_quota_requests_kind`` 一致——站点级 ``ocr_<engine>``
#: 是平台出资的口径，个人申请没有意义。
#:
#: 申请表保留 ``chat`` / ``ocr`` 两个历史词汇（审计要看得出当时申请的是哪一类
#: 消耗），但它们**落到额度上都是同一个 AF 池**（V051），见 :data:`QUOTA_KIND`。
KINDS: tuple[str, ...] = ("chat", "ocr")

#: 申请口径 → 额度 kind。V051 起用户侧只有 ``af``：批了 ``chat`` 申请就是给 AF
#: 池提额/清零，批了 ``ocr`` 申请也是同一个池子（OCR 与对话共用）。
QUOTA_KIND: dict[str, str] = {"chat": "af", "ocr": "af"}


def _quota_kind(request_kind: object) -> str:
    """申请表里的 kind → 真正落的额度 kind（未知值原样交给额度层去拒）。"""
    return QUOTA_KIND.get(str(request_kind), str(request_kind))

#: 申请状态机。``cancelled`` 留了位置（用户自己撤回）但当前没有端点写它。
STATUSES: tuple[str, ...] = ("pending", "approved", "rejected", "cancelled")

#: DB 列宽即契约（VARCHAR2(500)）。
MAX_REASON_CHARS = 500
MAX_NOTE_CHARS = 500

#: 审批台一页的默认/上限条数；超过上限按上限算（不报错，管理页不需要知道）。
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 50

#: 自助视图回多少条历史。
MINE_LIMIT = 10


class QuotaRequestError(Exception):
    """申请域的业务错误；``code`` 直接回给客户端（路由层翻成 HTTP）。"""

    def __init__(self, code: str, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _repo():
    """仓储入口（单一接缝：测试打桩这一层就够，不必碰 db.backends）。

    延迟导入保持「导入本模块不做任何 I/O」，也与 ``services.roles.policy._repo``
    同形（同样是「新表还没有 backends 工厂」的场景）。
    """
    from db.repos.oracle.ai_quota_requests import OracleAIQuotaRequestsRepo

    return OracleAIQuotaRequestsRepo()


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def check_kind(kind: object) -> str:
    """申请的能力名是封闭集合（``chat`` / ``ocr``），拼错的 key 不进库。"""
    if not isinstance(kind, str) or kind not in KINDS:
        raise QuotaRequestError(
            "AI_QUOTA_REQUEST_BAD_KIND",
            f"kind must be one of: {', '.join(KINDS)}",
        )
    return kind


def check_limit(limit: object) -> int | None:
    """申请/审批里的上限：``None``（不改上限）或非负整数。

    ``0`` 是合法值（「本月到此为止」），所以这里只拒负数与非整数——不能把 0
    当成「没填」，那会让「申请设成 0」变成一条静默无效的申请。
    """
    if limit is None:
        return None
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise QuotaRequestError(
            "AI_QUOTA_REQUEST_BAD_LIMIT",
            "limit must be a non-negative integer or null",
        )
    return limit


def _check_status(status: object) -> str:
    if not isinstance(status, str) or status not in STATUSES:
        raise QuotaRequestError(
            "AI_QUOTA_REQUEST_BAD_STATUS",
            f"status must be one of: {', '.join(STATUSES)}",
        )
    return status


def _clip(value: object, width: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:width] if text else None


def _page_args(page: object, size: object) -> tuple[int, int]:
    try:
        page_no = int(page)
    except (TypeError, ValueError):
        page_no = 1
    try:
        page_size = int(size)
    except (TypeError, ValueError):
        page_size = DEFAULT_PAGE_SIZE
    return max(1, page_no), max(1, min(page_size, MAX_PAGE_SIZE))


# ---------------------------------------------------------------------------
# 提交 / 自助查询
# ---------------------------------------------------------------------------


async def submit(
    user_oid: str,
    kind: object,
    reason: str | None = None,
    requested_limit: int | None = None,
) -> dict:
    """提交一条申请；同一用户同一月同一能力已有 pending → 409。

    查重是「先查后写」，并发双提交有极小的窗口（V044 文件头写了为什么不上函数
    唯一索引）。窗口的后果是两条 pending，审批端各自审批、各自重置一次 —— 比
    「申请被一条看不见的唯一约束顶成 500」好处理（管理员看得见两行）。
    """
    target_kind = check_kind(kind)
    limit = check_limit(requested_limit)
    period = quota.period_now()
    repo = _repo()

    existing = await repo.find_pending(user_oid, period, target_kind)
    if existing is not None:
        raise QuotaRequestError(
            "AI_QUOTA_REQUEST_PENDING",
            "本月该能力已有一条待审批的申请",
            409,
        )

    return await repo.create(
        user_oid,
        period,
        target_kind,
        reason=_clip(reason, MAX_REASON_CHARS),
        requested_limit=limit,
    )


async def list_mine(user_oid: str, limit: int = MINE_LIMIT) -> list[dict]:
    """本人最近的申请（新的在前）。不校验角色：谁都能看自己的。"""
    return await _repo().list_for_user(user_oid, max(1, min(int(limit), MINE_LIMIT)))


async def list_for_approver(
    status: object = "pending",
    page: object = 1,
    size: object = DEFAULT_PAGE_SIZE,
) -> dict:
    """审批台一页申请，附带提交人的姓名/学号。

    姓名不是审批的前提（审批人也能凭学号判断），所以用户查询失败只降级成
    ``None`` + 一行 warning，不让整个队列 500。
    """
    target_status = _check_status(status)
    page_no, page_size = _page_args(page, size)
    rows, total = await _repo().list_page(
        target_status, page=page_no, size=page_size
    )
    labels = await _user_labels([row["user_oid"] for row in rows])
    items = []
    for row in rows:
        name, business_id = labels.get(row["user_oid"], (None, None))
        items.append(
            {**row, "user_name": name, "user_id": business_id, "business_id": business_id}
        )
    return {"items": items, "total": total, "page": page_no, "size": page_size}


# ---------------------------------------------------------------------------
# 审批
# ---------------------------------------------------------------------------


async def decide(
    oid: str,
    approver_oid: str,
    approve: bool,
    *,
    reset: bool = True,
    limit: int | None = None,
    note: str | None = None,
) -> dict:
    """审批一条申请：通过（可选重置已用量 / 改上限）或拒绝。

    顺序是「**先按申请改额度，再落终态**」：

    * 额度操作在前，``WHERE status='pending'`` 的条件更新在后 —— 如果两个审批人
      同时点通过，后到的那条会在最后一步拿到 0 行并回 409，不会静默覆盖结论。
      极小窗口内两次 ``reset_used`` 可能都执行了，但重置是幂等的（``used = 0``），
      唯一可见后果是「上限被同一个值写了两次」，无害。
    * 反过来（先落终态再改额度）会把「额度没改成」变成一条已批准的申请——那才是
      无法自证的失败。

    ``approve=False`` 时**不动额度**：拒绝就是拒绝，不该顺手清零用量。
    """
    repo = _repo()
    row = await repo.get(oid)
    if row is None:
        raise QuotaRequestError(
            "AI_QUOTA_REQUEST_NOT_FOUND", "申请不存在", 404
        )
    if row.get("status") != "pending":
        raise QuotaRequestError(
            "AI_QUOTA_REQUEST_NOT_PENDING", "该申请已被处理", 409
        )

    target_limit = check_limit(limit)
    user_oid = str(row["user_oid"])
    # 审计记申请口径（``chat`` / ``ocr``），额度操作走统一 AF 池。
    kind = _quota_kind(row["kind"])

    if approve:
        if reset:
            await quota.reset_used(user_oid, kind)
        if target_limit is not None:
            await quota.set_user_limit(user_oid, kind, target_limit)
        status = "approved"
    else:
        status = "rejected"

    updated = await repo.decide(
        oid,
        status,
        decided_by=approver_oid,
        note=_clip(note, MAX_NOTE_CHARS),
    )
    if updated is None:
        raise QuotaRequestError(
            "AI_QUOTA_REQUEST_NOT_PENDING", "该申请已被处理", 409
        )

    await _audit(approver_oid, updated, status, target_limit)
    return updated


def _audit_detail(row: dict, status: str, limit: int | None) -> str:
    """审计 detail：申请 oid + 被操作用户 + 结论。

    ``request=`` 与 ``user=`` 两个键是刻意的（日志页按字符串搜得到），别改成
    只有人能读的一句话——排障时要能直接复制 oid。
    """
    parts = [
        f"request={row['_id']}",
        f"user={row['user_oid']}",
        f"kind={row['kind']}",
        f"period={row['period']}",
        f"status={status}",
    ]
    if limit is not None:
        parts.append(f"limit={limit}")
    if row.get("requested_limit") is not None:
        parts.append(f"requested={row['requested_limit']}")
    if row.get("decision_note"):
        parts.append(f"note={row['decision_note']}")
    return " ".join(parts)[:MAX_NOTE_CHARS]


async def _audit(
    approver_oid: str, row: dict, status: str, limit: int | None
) -> None:
    await audit_system_op(
        approver_oid,
        action=f"ai_quota_request_{status}",
        detail=_audit_detail(row, status, limit),
        # 审批/拒绝各有自己的 ltype：复用 ``system_settings_update`` 会让「本月
        # 谁批了谁的额度」混在在线设置里检索不出来（该类事件量大且语义无关）。
        log_type=log_events.QUOTA_DECISION_EVENTS.get(
            status, log_events.SYSTEM_SETTINGS_UPDATE
        ),
    )


# ---------------------------------------------------------------------------
# 管理表格：用户额度一览
# ---------------------------------------------------------------------------


async def _user_labels(
    oids: Iterable[str],
) -> dict[str, tuple[str | None, str | None]]:
    """批量取 ``oid -> (姓名, 学号)``；查不到就少几个键（不编造）。"""
    targets = list(dict.fromkeys(oid for oid in oids if oid))
    if not targets:
        return {}
    try:
        rows = await backends.get_users_repo().get_many_by_oids(targets)
    except Exception as exc:  # noqa: BLE001 - 展示用信息，失败不该拖垮队列
        logger.warning("读取申请人的用户信息失败: %s", type(exc).__name__)
        return {}
    return {
        str(oid): (doc.get("name"), doc.get("id"))
        for oid, doc in (rows or {}).items()
    }


async def list_users_with_quota(
    query: str = "", page: object = 1, size: object = DEFAULT_PAGE_SIZE
) -> dict:
    """管理端「配额管理」表格的一页：用户 + 当月 AF 额度 + 是否有待审申请。

    ``chat`` / ``ocr`` 两列在 V051 之后读的是同一个 AF 池（形状不变）。

    口径与真实扣费一致：``quota.snapshot`` 必须吃**目标用户**的 ``roles``（admin
    的 chat 默认是 1_000_000，按全局默认算会少一个数量级），所以这里再取一次完整
    用户行拿角色——``search`` 的投影里没有 ``roles``。

    额度快照按用户并发取（``asyncio.gather``）：一页 20 人 = 40 次点查，串行会
    把管理页拖成秒级。条数上限 50，不会失控。
    """
    page_no, page_size = _page_args(page, size)
    users_repo = backends.get_users_repo()
    rows, total = await users_repo.search(
        query or "", True, limit=page_size, offset=(page_no - 1) * page_size
    )
    oids = [str(row["_id"]) for row in rows]
    if not oids:
        return {"items": [], "total": int(total), "page": page_no, "size": page_size}

    period = quota.period_now()
    repo = _repo()
    full_docs: dict[str, Any] = {}
    pending: set[str] = set()
    try:
        full_docs = await users_repo.get_many_by_oids(oids) or {}
    except Exception as exc:  # noqa: BLE001 - 降级成「按全局默认展示」
        logger.warning("读取用户角色失败: %s", type(exc).__name__)
    try:
        pending = await repo.pending_user_oids(oids, period)
    except Exception as exc:  # noqa: BLE001 - 标记位失败不该让表格 500
        logger.warning("读取待审批申请失败: %s", type(exc).__name__)
        pending = set()

    snapshots = await asyncio.gather(
        *(
            quota.snapshot(oid, roles=(full_docs.get(oid) or {}).get("roles"))
            for oid in oids
        ),
        return_exceptions=True,
    )

    items: list[dict] = []
    for row, snap in zip(rows, snapshots):
        oid = str(row["_id"])
        if isinstance(snap, BaseException):
            # 单人快照失败只让这一格空着：表格其余行照常可用。
            logger.warning("读取额度快照失败 %s: %s", oid, type(snap).__name__)
            snap = {}
        items.append(
            {
                "oid": oid,
                "name": row.get("name"),
                "id": row.get("id"),
                "business_id": row.get("id"),
                "class_id": row.get("class_id"),
                "period": period,
                # 两列都读同一个 AF 池：合并后「对话额度」与「OCR 额度」是同一个
                # 数字，响应形状保持不变（前端仍是 chat / ocr 两列）。
                "chat": (snap or {}).get(_quota_kind("chat")),
                "ocr": (snap or {}).get(_quota_kind("ocr")),
                "pending_request": oid in pending,
            }
        )
    return {"items": items, "total": int(total), "page": page_no, "size": page_size}


__all__ = [
    "KINDS",
    "QUOTA_KIND",
    "MAX_PAGE_SIZE",
    "MAX_REASON_CHARS",
    "MINE_LIMIT",
    "STATUSES",
    "QuotaRequestError",
    "check_kind",
    "check_limit",
    "decide",
    "list_for_approver",
    "list_mine",
    "list_users_with_quota",
    "submit",
]
