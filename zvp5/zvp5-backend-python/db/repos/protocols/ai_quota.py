"""Per-user AI quota and its approval requests (``AI_QUOTA`` /
``AI_QUOTA_REQUESTS``) — the counter and the request that changes it."""

from __future__ import annotations

from typing import Protocol, Sequence

from .common import Row


class AIQuotaRepo(Protocol):
    """Data access for ``AI_QUOTA`` (按 用户 + 月份 + 能力 计的额度, V024).

    Rows use the Mongo document shape ``{"_id": <hex str>, "user_oid": str,
    "period": str, "kind": "ocr" | "chat", "used": int, "limit_count": int,
    "updated_at": datetime}``. ``period`` is ``YYYYMM``.

    The unique key ``(user_oid, period, kind)`` is what makes :meth:`consume`
    safe: the decrement is a single conditional ``UPDATE``, so concurrent calls
    cannot oversell the quota.
    """

    async def consume(
        self,
        user_oid: str,
        period: str,
        kind: str,
        amount: int,
        default_limit: int,
        want_state: bool = True,
    ) -> tuple[bool, int, int]:
        """原子扣减，返回 (allowed, used, limit)。不超卖。

        ``want_state=False`` 用于**丢弃返回值**的调用点（只看 allowed）：实现可以
        跳过成功后的回读，此时 used/limit 无意义，调用方不得使用。
        """
        ...

    async def get(self, user_oid: str, period: str, kind: str) -> Row | None:
        """Return the quota row, or ``None`` when the user has not used it yet."""
        ...

    async def set_limit(
        self, user_oid: str, period: str, kind: str, limit_count: int
    ) -> None:
        """Set the quota ceiling, creating the row (``used = 0``) when absent."""
        ...

    async def reset_used(self, user_oid: str, period: str, kind: str) -> int:
        """Zero ``used`` and keep ``limit_count``.

        Returns the affected row count: ``0`` means the row does not exist,
        which is success (idempotent) rather than an error.
        """
        ...

    async def refund(self, user_oid: str, period: str, kind: str, amount: int) -> int:
        """Give ``amount`` units back in a single conditional ``UPDATE``.

        ``used - amount`` is clamped at zero (the table has a ``used >= 0``
        CHECK): a compensation must never turn the counter negative, and it must
        never fail either — the caller already decided to give the units back.
        Returns the affected row count (``0`` = no such row, nothing to give back).
        """
        ...


class AIQuotaRequestsRepo(Protocol):
    """Data access for ``AI_QUOTA_REQUESTS``（配额重置申请, V044）。

    Rows use the Mongo document shape::

        {"_id": <hex str>, "user_oid": str, "period": "YYYYMM",
         "kind": "chat" | "ocr",
         "status": "pending" | "approved" | "rejected" | "cancelled",
         "reason": str | None, "requested_limit": int | None,
         "decided_by": str | None, "decided_at": datetime | None,
         "decision_note": str | None,
         "created_at": datetime, "updated_at": datetime}

    ``requested_limit`` 的 ``None`` 与 ``0`` 语义不同：``None`` = 只要求重置已用
    量，``0`` = 上限设成 0（「本月到此为止」）。

    :meth:`decide` 是**条件更新**（``WHERE status = 'pending'``）：两个审批人同时
    点「通过」时只有一条能改到，另一条拿到 ``None``，由服务层翻成 409。这条
    并发语义放在 SQL 里而不是「先查再写」，与 ``AI_QUOTA`` 的扣减同口径。
    """

    async def create(
        self,
        user_oid: str,
        period: str,
        kind: str,
        *,
        reason: str | None = None,
        requested_limit: int | None = None,
    ) -> Row:
        """写一条 ``status='pending'`` 的申请并返回该行。"""
        ...

    async def get(self, oid: str) -> Row | None:
        """按申请 oid 取行；不存在返回 ``None``。"""
        ...

    async def find_pending(
        self, user_oid: str, period: str, kind: str
    ) -> Row | None:
        """该用户该月该能力**尚未处理**的那一条申请（查重用）。"""
        ...

    async def list_page(
        self, status: str | None, *, page: int = 1, size: int = 20
    ) -> tuple[list[Row], int]:
        """审批台视图：按 ``created_at`` 倒序一页 + 总数。

        ``status=None`` 表示不筛状态（审计视角看全量），否则按状态精确过滤。
        """
        ...

    async def list_for_user(self, user_oid: str, limit: int = 10) -> list[Row]:
        """某人最近的申请（新的在前）。"""
        ...

    async def decide(
        self,
        oid: str,
        status: str,
        *,
        decided_by: str,
        note: str | None = None,
    ) -> Row | None:
        """把 **pending** 行改成终态并写审批人与备注。

        返回更新后的行；行不存在或已经不是 ``pending`` 时返回 ``None``（调用方
        据此区分 404 / 409 —— 本层不猜业务语义）。
        """
        ...

    async def pending_user_oids(
        self, user_oids: Sequence[str], period: str
    ) -> set[str]:
        """这一批用户里当月还有待审批申请的 oid 集合（管理表格的 ``pending_request``）。

        一次 ``IN`` 查询取代逐个用户各查一次。
        """
        ...
