"""AI 网关健康检查（admin 视图）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from routers.ai.common import _require_admin
from services.ai import config as ai_config
from services.ai import credentials, rate_limit, routing
from util.object_id import get_current_user

router = APIRouter()

# ---------------------------------------------------------------------------
# Health（admin 视图：不含密钥、不含上游原始报文）
# ---------------------------------------------------------------------------


@router.get("/health")
async def ai_health(
    user=Depends(get_current_user),
):
    _require_admin(user)

    entries = await credentials.catalog(user["id"])
    counts = {
        credentials.SCOPE_PERSONAL: 0,
        credentials.SCOPE_SHARED: 0,
        credentials.SCOPE_SERVER: 0,
    }
    shared_entries: list[dict] = []
    models: set[str] = set()
    for entry in entries:
        scope = str(entry.get("scope") or "")
        counts[scope] = counts.get(scope, 0) + 1
        models.update(m for m in entry.get("models") or [] if isinstance(m, str))
        if scope == credentials.SCOPE_SHARED:
            # 共享项是平台出资的，运维要能一眼看到「大家此刻能打到哪」。
            shared_entries.append(
                {
                    "name": entry.get("name"),
                    "base_url": entry.get("base_url"),
                    "models": list(entry.get("models") or []),
                    "enabled": bool(entry.get("enabled", True)),
                }
            )

    workers = ai_config.worker_count()
    return {
        "status": "ok",
        "code": 200,
        "data": {
            "enabled": ai_config.is_enabled(),
            "max_concurrency": ai_config.max_concurrency(),
            # 部署的进程数 + 每进程的实际频控阈值：两者一起才能算出「全局真实
            # 上限」（阈值 × N）。单 worker 时阈值就是配置值。
            "worker_count": workers,
            "rate_limit_per_minute": {
                kind: rate_limit.effective_limit(kind)
                for kind in ai_config.RATE_LIMIT_KINDS
            },
            "providers": ai_config.public_provider_view(),
            # 「当前生效的注入策略」（不是库里能配什么）：运维要能一眼看出这台
            # 实例到底会不会往请求体里加字段。只有 host，不含密钥、路径或前缀。
            "prompt_cache": {
                "mode": ai_config.prompt_cache_mode(),
                "key_hosts": sorted(ai_config.prompt_cache_key_hosts()),
                "breakpoint_hosts": sorted(ai_config.prompt_cache_breakpoint_hosts()),
            },
            # 冷却状态的可见范围：配了 Redis 就是全局共享的，否则只有本进程。
            "cooldown_seconds": await routing.cooldown_state(),
            "cooldown_scope": routing.cooldown_scope(),
            "models": sorted(models),
            "quota_defaults": dict(ai_config.DEFAULT_QUOTA),
            "credentials": {
                "personal": counts[credentials.SCOPE_PERSONAL],
                "shared": counts[credentials.SCOPE_SHARED],
                "server": counts[credentials.SCOPE_SERVER],
                "shared_entries": shared_entries,
            },
        },
    }
