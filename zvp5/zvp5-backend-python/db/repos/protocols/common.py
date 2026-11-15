"""Shared row/type contracts used by every repository protocol.

``Row`` is the dict shape every protocol returns; ``UNSET`` separates
"argument not passed" from "explicitly cleared"; ``SHARED_OWNER_OID`` is
the owner sentinel of shared AI credentials.

Import from :mod:`db.repos.protocols` — this module only exists to keep
the shared vocabulary in one place."""

from __future__ import annotations

from typing import Any

# A row as seen by callers: the Mongo document shape, backend-independent.
Row = dict[str, Any]


# ``shared`` 凭据的 owner 哨兵：24 个 0。**不是 NULL** —— Oracle 的唯一索引里
# NULL 互不相等，``owner_oid`` 可空就等于 ``(scope, owner_oid, name)`` 对共享项
# 完全不生效，同名的共享 provider 可以插进任意多行。哨兵是个合法 hex oid，不指向
# 任何用户，但让唯一索引对两类行同样有效。
SHARED_OWNER_OID = "0" * 24


class _UnsetType:
    """「未传参」哨兵：与 ``None``（= 显式置空）区分开。

    只有 ``expires_at`` 用得到它。表里其他可空列的 ``None`` 一律表示「不改」，
    而「清除到期」是管理员的正常操作——没有哨兵就无法同时表达「不改」与「置空」。
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - 仅调试输出
        return "UNSET"


UNSET: Any = _UnsetType()
