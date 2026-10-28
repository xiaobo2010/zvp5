"""角色策略的数据访问层（``ROLE_FEATURES`` / ``ROLE_QUOTAS``，V043）。

两张表加起来最多 ``3 角色 × (13 开关 + 3 额度) = 48`` 行，所以接口和
:mod:`db.repos.oracle.system_settings` 一样是**粗粒度**的：

* :meth:`OracleRolePoliciesRepo.list_all` 一次取回两张全表，由
  :mod:`services.roles.policy` 的 5 秒快照统一持有；不做「每个 key 一次查询」，
  否则每次 AI 请求都要多几条往返。
* :meth:`OracleRolePoliciesRepo.upsert_role` 是**单事务全量替换**：管理员提交的
  是某一个角色的完整矩阵（UI 就是一张勾选表），所以「请求里没出现的 key」
  语义上等于「删掉，回落到代码默认」。用 DELETE + INSERT 而不是逐行 diff：
  主键就是整行的身份、没有任何从表引用它，逐行 diff 只会多出「哪些 key 变了」
  的判断代码却换来 0 的收益。删除与插入在同一个事务里，读者永远看不到中间态。

Session ownership mirrors :mod:`db.repos.oracle.system_settings`: with no injected
session each method opens its own through :func:`db.oracle.session.session_scope`
and commits once; an injected session is neither committed nor closed.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import role_features as features_tbl
from db.models import role_quotas as quotas_tbl
from db.oracle.session import session_scope

Row = dict[str, Any]


@asynccontextmanager
async def _session_for(
    session: AsyncSession | None,
) -> AsyncIterator[tuple[AsyncSession, bool]]:
    """Yield ``(session, owns)``; ``owns`` is True only for repo-created ones."""
    if session is not None:
        yield session, False
        return
    async with session_scope() as owned:
        yield owned, True


def _now() -> datetime:
    """Naive ``datetime.now()`` — the schema convention for ``TIMESTAMP`` columns."""
    return datetime.now()


def _as_int(value: Any) -> int | None:
    """NUMBER 列可能以 Decimal 回来；``None`` 保持 ``None``（= 没填）。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _latest(
    current: datetime | None, candidate: datetime | None
) -> datetime | None:
    """同一角色的多行共享「最后一次修改」；取最大时间戳，不猜写入顺序。"""
    if candidate is None:
        return current
    if current is None or candidate > current:
        return candidate
    return current


class OracleRolePoliciesRepo:
    """``ROLE_FEATURES`` / ``ROLE_QUOTAS``：角色 -> 能力矩阵。"""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def list_all(self) -> dict[str, list[Row]]:
        """两张全表一次取回：``{"features": [...], "quotas": [...]}``。"""
        async with _session_for(self._session) as (session, _owns):
            feature_rows = (
                await session.execute(
                    select(
                        features_tbl.c.role,
                        features_tbl.c.feature_key,
                        features_tbl.c.enabled,
                        features_tbl.c.updated_at,
                        features_tbl.c.updated_by,
                    )
                )
            ).all()
            quota_rows = (
                await session.execute(
                    select(
                        quotas_tbl.c.role,
                        quotas_tbl.c.kind,
                        quotas_tbl.c.monthly_limit,
                        quotas_tbl.c.updated_at,
                        quotas_tbl.c.updated_by,
                    )
                )
            ).all()

        features: list[Row] = []
        for row in feature_rows:
            mapping = row._mapping
            features.append(
                {
                    "role": mapping["role"],
                    "feature_key": mapping["feature_key"],
                    # NUMBER(1) 在 Oracle 里没有原生 bool：0/1 在这里翻成 False/True，
                    # 上层（DEFAULTS 合并、UI 勾选）只处理布尔。
                    "enabled": bool(_as_int(mapping["enabled"]) or 0),
                    "updated_at": mapping["updated_at"],
                    "updated_by": mapping["updated_by"],
                }
            )

        quotas: list[Row] = []
        for row in quota_rows:
            mapping = row._mapping
            quotas.append(
                {
                    "role": mapping["role"],
                    "kind": mapping["kind"],
                    "monthly_limit": _as_int(mapping["monthly_limit"]),
                    "updated_at": mapping["updated_at"],
                    "updated_by": mapping["updated_by"],
                }
            )
        return {"features": features, "quotas": quotas}

    async def get_role(self, role: str) -> Row:
        """某角色的**库里实际存在**的行（没配过就是空 features/quotas）。

        返回形状固定，缺行不返回 ``None``：调用方要区分的是「这个 key 有没有
        覆盖」，那是 ``features``/``quotas`` 字典里有没有键，不是整个角色在不在。
        """
        stmt_features = select(
            features_tbl.c.feature_key,
            features_tbl.c.enabled,
            features_tbl.c.updated_at,
            features_tbl.c.updated_by,
        ).where(features_tbl.c.role == role)
        stmt_quotas = select(
            quotas_tbl.c.kind,
            quotas_tbl.c.monthly_limit,
            quotas_tbl.c.updated_at,
            quotas_tbl.c.updated_by,
        ).where(quotas_tbl.c.role == role)

        async with _session_for(self._session) as (session, _owns):
            feature_rows = (await session.execute(stmt_features)).all()
            quota_rows = (await session.execute(stmt_quotas)).all()

        features: dict[str, bool] = {}
        quotas: dict[str, int | None] = {}
        updated_at: datetime | None = None
        updated_by: str | None = None
        for row in feature_rows:
            mapping = row._mapping
            features[mapping["feature_key"]] = bool(_as_int(mapping["enabled"]) or 0)
            updated_at = _latest(updated_at, mapping["updated_at"])
            updated_by = mapping["updated_by"] or updated_by
        for row in quota_rows:
            mapping = row._mapping
            quotas[mapping["kind"]] = _as_int(mapping["monthly_limit"])
            updated_at = _latest(updated_at, mapping["updated_at"])
            updated_by = mapping["updated_by"] or updated_by

        return {
            "role": role,
            "features": features,
            "quotas": quotas,
            "updated_at": updated_at,
            "updated_by": updated_by,
        }

    async def upsert_role(
        self,
        role: str,
        features: dict[str, bool],
        quotas: dict[str, int | None],
        actor: str | None = None,
    ) -> Row:
        """单事务全量替换某角色的矩阵；请求里没出现的 key 被删除。

        ``quotas`` 里值为 ``None`` 的 kind **不落行**（= 回落到代码默认）：
        「用系统默认」在库里就是「没有这行」，与两张表的稀疏覆盖语义一致。

        调用方负责校验（role 在可编辑集合里、key 在封闭枚举里、额度非负）：
        这一层只保证「写进去的就是调用方给的那一份」，不重复业务判断。
        """
        now = _now()
        feature_values = [
            {
                "role": role,
                "feature_key": key,
                "enabled": 1 if enabled else 0,
                "updated_at": now,
                "updated_by": actor,
            }
            for key, enabled in features.items()
        ]
        quota_values = [
            {
                "role": role,
                "kind": kind,
                "monthly_limit": int(limit),
                "updated_at": now,
                "updated_by": actor,
            }
            for kind, limit in quotas.items()
            if limit is not None
        ]

        async with _session_for(self._session) as (session, owns):
            await session.execute(
                delete(features_tbl).where(features_tbl.c.role == role)
            )
            await session.execute(delete(quotas_tbl).where(quotas_tbl.c.role == role))
            if feature_values:
                await session.execute(insert(features_tbl), feature_values)
            if quota_values:
                await session.execute(insert(quotas_tbl), quota_values)
            if owns:
                await session.commit()

        return await self.get_role(role)


__all__ = ["OracleRolePoliciesRepo"]
