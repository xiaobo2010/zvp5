"""AI 网关健康检查（admin 视图）。"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends

from routers.ai.common import _require_admin
from services.ai import config as ai_config
from services.ai import credentials, ocr_engines, rate_limit, routing
from util.object_id import get_current_user

router = APIRouter()

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Health（admin 视图：不含密钥、不含上游原始报文）
# ---------------------------------------------------------------------------

# 本地引擎（rapidocr）的预热状态。``None`` = 还没预热过。
# 只存布尔值：不存路径、不存模型名、不存任何加载细节，避免 health 视图变成
# 「服务器文件系统/引擎内部结构」的泄露面。
_local_engine_ready: bool | None = None


async def _warmup_local_engines() -> bool:
    """后台预热本地引擎；健康检查不能因此失败，异常一律降级为 ``False``。"""
    global _local_engine_ready
    try:
        _local_engine_ready = bool(await ocr_engines.warmup_local_engines())
    except Exception:  # noqa: BLE001 - warmup 契约上不抛，这里只是最后一道保险
        _logger.exception("ai local engine warmup failed")
        _local_engine_ready = False
    return _local_engine_ready


@router.get("/health")
async def ai_health(
    background_tasks: BackgroundTasks,
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

    # 预热本地引擎（rapidocr）：首次加载 onnxruntime 与模型要秒级时间，放在
    # 后台任务里跑，/health 立即返回；warmup 本身幂等，重复请求只是复用实例。
    # 预热的失败不影响本响应（失败只体现在下面 local_engine_ready 的取值上）。
    background_tasks.add_task(_warmup_local_engines)

    workers = ai_config.worker_count()
    return {
        "status": "ok",
        "code": 200,
        "data": {
            "enabled": ai_config.is_enabled(),
            # 只有一个布尔：预热是否已就绪。不暴露任何路径 / 模型名 / 密钥材料。
            "local_engine_ready": bool(_local_engine_ready),
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
