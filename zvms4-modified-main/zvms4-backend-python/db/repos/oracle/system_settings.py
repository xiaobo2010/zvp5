"""在线设置中心的数据访问层（``SYSTEM_SETTINGS`` / ``USER_QUOTA_OVERRIDES``，V036）。

两张表都很小（几十个可调项 + 少数按用户覆盖），所以接口刻意做得**粗粒度**：

* ``all_rows()`` 一次取全表，由 :mod:`services.settings_runtime` 的 5 秒快照缓存
  统一持有；不做「每个键一次查询」，那会让每次 AI 请求都多几条往返。
* ``upsert_many()`` 在一个事务里按键逐条 UPDATE → 缺失则 INSERT。不用 Oracle
  ``MERGE`` 的原因与 :mod:`db.repos.oracle.ai_quota` 的 ``set_limit`` 一致：UPDATE
  的 ``rowcount`` 就是「行在不在」的权威答案，INSERT 撞唯一键是小概率并发，吞掉
  后重试一次 UPDATE 即可，不需要把 500 抛给管理员。
* ``value_json`` 的 JSON 编解码只在这里做（列是裸 CLOB，见 V036 的说明）：写入
  ``json.dumps``、读取 ``json.loads``，脏数据按「这一行不存在」处理而不是让整个
  设置中心 500。

Session ownership mirrors :mod:`db.repos.oracle.ai_api_keys`: with no injected
session each method opens its own through :func:`db.oracle.session.session_scope`,
writes commit once; an injected session is neither committed nor closed.

Nothing here opens a connection at import time.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from db.ids import normalize_oid
from db.models import system_settings as settings_tbl
from db.models import user_quota_overrides as quotas_tbl
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


def _decode(raw: Any) -> Any:
    """把 CLOB 里的 JSON 文本解回 Python 值；坏行返回 :data:`_INVALID`。"""
    if raw is None:
        return _INVALID
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", "replace")
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return _INVALID


class _Invalid:
    """「这一行的值读不出来」的哨兵（与合法的 ``None`` 区分）。"""

    def __repr__(self) -> str:  # pragma: no cover - 只为调试可读
        return "<invalid>"


_INVALID = _Invalid()


def _settings_columns():
    return (
        settings_tbl.c.setting_key,
        settings_tbl.c.value_json,
        settings_tbl.c.updated_by,
        settings_tbl.c.updated_at,
    )


class OracleSystemSettingsRepo:
    """``SYSTEM_SETTINGS``：键 -> JSON 值 + 最后修改人/时间。"""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def all_rows(self) -> list[Row]:
        """全表一次取回；坏 JSON 的行标 ``invalid=True`` 由调用方跳过。"""
        stmt = select(*_settings_columns())
        async with _session_for(self._session) as (session, _owns):
            result = await session.execute(stmt)
            rows = [row._mapping for row in result]
        out: list[Row] = []
        for row in rows:
            value = _decode(row["value_json"])
            out.append(
                {
                    "setting_key": row["setting_key"],
                    "value": None if value is _INVALID else value,
                    "invalid": value is _INVALID,
                    "updated_by": row["updated_by"],
                    "updated_at": row["updated_at"],
                }
            )
        return out

    async def upsert_many(
        self, values: dict[str, Any], actor_oid: str | None = None
    ) -> None:
        """逐键写库（调用方负责校验）：UPDATE 命中即改，未命中才 INSERT。"""
        if not values:
            return
        now = _now()
        async with _session_for(self._session) as (session, owns):
            for key, value in values.items():
                payload = json.dumps(value, ensure_ascii=False)
                result = await session.execute(
                    update(settings_tbl)
                    .where(settings_tbl.c.setting_key == key)
                    .values(
                        value_json=payload,
                        updated_by=actor_oid,
                        updated_at=now,
                    )
                )
                if result.rowcount:
                    continue
                try:
                    async with session.begin_nested():
                        await session.execute(
                            insert(settings_tbl).values(
                                setting_key=key,
                                value_json=payload,
                                updated_by=actor_oid,
                                updated_at=now,
                            )
                        )
                except IntegrityError:
                    # 并发写入同一键：别人先插了，改成更新它（否则这次修改静默丢失）。
                    await session.execute(
                        update(settings_tbl)
                        .where(settings_tbl.c.setting_key == key)
                        .values(
                            value_json=payload,
                            updated_by=actor_oid,
                            updated_at=now,
                        )
                    )
            if owns:
                await session.commit()


class OracleUserQuotaOverridesRepo:
    """``USER_QUOTA_OVERRIDES``：按用户覆盖每日上传配额，NULL = 继承全局。"""

    def __init__(self, session: AsyncSession | None = None) -> None:
        self._session = session

    async def all_rows(self) -> list[Row]:
        stmt = select(
            quotas_tbl.c.user_oid,
            quotas_tbl.c.daily_max_files,
            quotas_tbl.c.daily_max_bytes,
            quotas_tbl.c.updated_by,
            quotas_tbl.c.updated_at,
        )
        async with _session_for(self._session) as (session, _owns):
            result = await session.execute(stmt)
            rows = [row._mapping for row in result]
        return [
            {
                "user_oid": row["user_oid"],
                "daily_max_files": (
                    None
                    if row["daily_max_files"] is None
                    else int(row["daily_max_files"])
                ),
                "daily_max_bytes": (
                    None
                    if row["daily_max_bytes"] is None
                    else int(row["daily_max_bytes"])
                ),
                "updated_by": row["updated_by"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    async def get(self, user_oid: str) -> Row | None:
        target = normalize_oid(user_oid)
        stmt = select(
            quotas_tbl.c.user_oid,
            quotas_tbl.c.daily_max_files,
            quotas_tbl.c.daily_max_bytes,
            quotas_tbl.c.updated_by,
            quotas_tbl.c.updated_at,
        ).where(quotas_tbl.c.user_oid == target)
        async with _session_for(self._session) as (session, _owns):
            result = await session.execute(stmt)
            row = result.first()
        if row is None:
            return None
        mapping = row._mapping
        return {
            "user_oid": mapping["user_oid"],
            "daily_max_files": (
                None
                if mapping["daily_max_files"] is None
                else int(mapping["daily_max_files"])
            ),
            "daily_max_bytes": (
                None
                if mapping["daily_max_bytes"] is None
                else int(mapping["daily_max_bytes"])
            ),
            "updated_by": mapping["updated_by"],
            "updated_at": mapping["updated_at"],
        }

    async def set(
        self,
        user_oid: str,
        daily_max_files: int | None,
        daily_max_bytes: int | None,
        actor_oid: str | None = None,
    ) -> None:
        """写入覆盖；两列都是 ``None`` 时**删除**该行（= 完全继承全局）。

        删除而不是留一行 NULL：表里「没有这行」与「这行两列都 NULL」语义相同，
        但前者让 ``all_rows()`` 的加载量最小，也不会积累无意义的空行。
        """
        target = normalize_oid(user_oid)
        async with _session_for(self._session) as (session, owns):
            if daily_max_files is None and daily_max_bytes is None:
                await session.execute(
                    delete(quotas_tbl).where(quotas_tbl.c.user_oid == target)
                )
                if owns:
                    await session.commit()
                return

            now = _now()
            result = await session.execute(
                update(quotas_tbl)
                .where(quotas_tbl.c.user_oid == target)
                .values(
                    daily_max_files=daily_max_files,
                    daily_max_bytes=daily_max_bytes,
                    updated_by=actor_oid,
                    updated_at=now,
                )
            )
            if not result.rowcount:
                try:
                    async with session.begin_nested():
                        await session.execute(
                            insert(quotas_tbl).values(
                                user_oid=target,
                                daily_max_files=daily_max_files,
                                daily_max_bytes=daily_max_bytes,
                                updated_by=actor_oid,
                                updated_at=now,
                            )
                        )
                except IntegrityError:
                    await session.execute(
                        update(quotas_tbl)
                        .where(quotas_tbl.c.user_oid == target)
                        .values(
                            daily_max_files=daily_max_files,
                            daily_max_bytes=daily_max_bytes,
                            updated_by=actor_oid,
                            updated_at=now,
                        )
                    )
            if owns:
                await session.commit()


__all__ = ["OracleSystemSettingsRepo", "OracleUserQuotaOverridesRepo"]
