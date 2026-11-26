"""运行期设置层：在线设置中心的唯一读写入口。

为什么需要这一层：所有可调项原先只存在于 ``config.py`` / ``settings.py``，改一个
数字要改文件 + 重启（``settings.py`` 还被文件管理保护，根本改不到）。这里把
「哪些键可调、各自的类型与范围、默认值」收拢成一张**注册表**，值落
``SYSTEM_SETTINGS``，进程内做 5 秒快照缓存。

三个刻意的设计决定：

1. **默认值不在本文件里另写一份**。注册表构建时从 ``config`` / ``util.attachment``
   / ``services.agent.context`` / ``services.ai.config`` 现读，避免「在线默认值」与
   「代码默认值」两份常量悄悄漂移。
2. **同步读取用进程内快照，不把调用方改成 async**。``services/ai/config.py``、
   ``services/agent/context.py``、``util/attachment.py`` 的读取点分布在同步函数与
   同步的 FastAPI 依赖/参数默认值里，把它们改成协程会连锁改一大片调用方。所以：
   快照由 :func:`refresh`（启动时挂到调度器，每 5 秒一次）刷新，同步读取点用
   :func:`get_override` / :func:`get_sync` 直接读内存；需要「一定是最新」的接口路径
   用 :func:`ensure_fresh` 先刷一次。每次刷新先花一次 Redis GET 看 ``settings``
   版本号（``db.cache.version``）：版本没变就不读库，``set_many`` 写成功后自增
   版本号，别的进程下一轮就知道要回源。Redis 不可用时退回「每 5 秒读库」。
3. **``set_many`` 立即改写快照**，不等下一轮刷新 —— 管理员点完保存，同进程的读取点
   下一个请求就拿到新值（跨进程最多晚 :data:`REFRESH_SECONDS` 秒）。

读库失败时**回落代码默认值并打日志**，绝不把异常抛给业务：设置中心是运维便利，
不是业务的依赖；一次 ADB 抖动不该让 AI 网关或上传接口 500。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from db import backends, cache

logger = logging.getLogger(__name__)

# 快照有效期（秒）。与 ``util.dual_auth`` 的窗口口径一致：用 ``time.monotonic``
# 计时，系统时间跳变不会把缓存拉长或立刻过期。
REFRESH_SECONDS = 5.0

#: 版本闸生效时，最多多久不回源数据库（秒）。
#: 版本号只能证明「没人通过 API 改过」，挡不住「有人直接改了库」，所以保留
#: 这个强制回源兜底：最坏情况下手工改库 5 分钟内生效。
FULL_RELOAD_SECONDS = 300.0

#: Redis 里该域的版本键（写设置 / 写角色策略时 INCR，见 ``db.cache.flush_scopes``）。
VERSION_SCOPE = "settings"

GROUP_AI = "ai"
GROUP_OPS = "ops"
GROUP_SECURITY = "security"

_RATE_LIMIT_MAX = 1_000_000
_ROLE_QUOTA_MAX = 10_000_000
_MAX_LIST_ITEMS = 50
_MAX_ROLE_COUNT = 50

#: V051 之前的角色额度 kind（已并入 ``af`` 池）。只读兼容：库里还有这些值时
#: 归并进 ``af``，不拒收（否则设置项永远不生效、日志每 5 秒刷一次）。
LEGACY_QUOTA_KINDS: tuple[str, ...] = ("ocr", "chat")

#: 「旧 kind 已归并」的日志去重（``(role, kind)``）。module 级：一个进程只提一次。
_legacy_logged: set[tuple[str, str]] = set()


def _note_legacy_kind(role: str, kind: str) -> None:
    token = (role, kind)
    if token in _legacy_logged:
        return
    _legacy_logged.add(token)
    logger.info(
        "设置项 ai.role_quota：角色 %s 的历史能力 %r 已归并进 af", role, kind
    )


class SettingsValidationError(ValueError):
    """设置值不合法（未知键 / 类型不符 / 越界）；路由层转 400。"""

    def __init__(self, key: str, reason: str) -> None:
        super().__init__(f"{key}: {reason}")
        self.key = key
        self.reason = reason


@dataclass(frozen=True)
class SettingSpec:
    """一个可调项的元数据（默认值来自现有代码常量，见 :func:`_build_registry`）。"""

    key: str
    type: str
    default: Any
    group: str
    label: str
    description: str
    min: int | None = None
    max: int | None = None
    options: tuple[str, ...] | None = None
    coerce: Callable[[Any], Any] | None = field(default=None, compare=False)


#: 键注册表（首次使用时由 :func:`_build_registry` 填充；延迟构建是为了避开
#: ``services.ai.config`` 与本模块的循环导入）。
REGISTRY: dict[str, SettingSpec] = {}

# ---------------------------------------------------------------------------
# 进程内快照
# ---------------------------------------------------------------------------
#
# ``_db_values`` 只放**数据库里真实存在的覆盖值**：没有覆盖的键不出现在这里，
# 读取点用 ``get_override() is None`` 判断「该走原来的 settings/config 链路」。
# 这样既能在线覆盖，又完整保留「settings.py 优先于 config.py」的既有语义。

_db_values: dict[str, Any] = {}
_db_meta: dict[str, tuple[datetime | None, str | None]] = {}
_quota_rows: dict[str, dict[str, Any]] = {}
_last_refresh = 0.0
_refreshing = False
#: 上次读库时 Redis 里的版本号；``None`` = Redis 不可用 / 还没读过。
_last_version: int | None = None
#: 上次真正回源 ADB 的时刻（``_last_refresh`` 是「上次确认快照可用」的时刻）。
_last_full_read = 0.0


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

def _require_int(value: Any, low: int, high: int, label: str) -> int:
    """严格整数（``True`` 不算）：``bool`` 是 ``int`` 的子类，必须先排除。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label}必须是整数")
    if value < low or value > high:
        raise ValueError(f"{label}必须在 {low} 到 {high} 之间")
    return value


def _validate_roles(value: Any, *, allow_empty: bool) -> list[str]:
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ValueError("必须是角色字符串数组")
    roles: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("角色必须是非空字符串")
        role = item.strip()
        if role not in roles:
            roles.append(role)
    if not roles and not allow_empty:
        raise ValueError("至少需要一个角色")
    if len(roles) > _MAX_LIST_ITEMS:
        raise ValueError(f"最多 {_MAX_LIST_ITEMS} 个角色")
    return roles


def _validate_rate_limit(value: Any) -> dict[str, int]:
    """``{"ocr": N, "chat": N}``：未知能力直接拒，避免给永不查询的键设上限。"""
    from services.ai import config as ai_config

    if not isinstance(value, dict) or not value:
        raise ValueError("必须是 {能力: 每分钟次数} 对象")
    out: dict[str, int] = {}
    for kind, limit in value.items():
        if kind not in ai_config.RATE_LIMIT_KINDS:
            raise ValueError(f"未知能力 {kind!r}")
        out[str(kind)] = _require_int(limit, 0, _RATE_LIMIT_MAX, f"{kind} 的上限")
    return out


def _validate_compact_ratio(value: Any) -> float:
    """压缩阈值：``0 < ratio <= 1``。数字与数字字符串都收（文本框可能回传 "0.8"）。"""
    if isinstance(value, bool):
        raise ValueError("压缩阈值必须是 0 到 1 之间的小数")
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except (TypeError, ValueError):
            raise ValueError("压缩阈值必须是 0 到 1 之间的小数") from None
    if not isinstance(value, (int, float)):
        raise ValueError("压缩阈值必须是 0 到 1 之间的小数")
    ratio = float(value)
    if not 0.0 < ratio <= 1.0:
        raise ValueError("压缩阈值必须大于 0 且不超过 1")
    return ratio


def _validate_role_quota(value: Any) -> dict[str, dict[str, int]]:
    """``{角色: {能力: 月额度}}``。

    V051 把用户侧的 ``ocr`` / ``chat`` 并进了统一 AF 池（``af``），但库里仍有
    旧值。对旧 kind 的处理是**归并而不是拒收**：

    * 同角色已经有 ``af`` → 忽略旧键（``af`` 是现行口径，旧值只是历史残留；
      两个都加会让额度在每次启动时翻倍）；
    * 同角色只有旧键 → 旧键求和写进 ``af``（同一角色多旧键取和）。

    以前这里直接抛错，导致生产每 5 秒刷一条「ai.role_quota 不合法」的日志而
    设置项永远不生效。现在只在校验通过后记一次 info（module 级 set 去重）。
    """
    from services.ai import config as ai_config

    if not isinstance(value, dict) or not value:
        raise ValueError("必须是 {角色: {能力: 月额度}} 对象")
    if len(value) > _MAX_ROLE_COUNT:
        raise ValueError(f"最多 {_MAX_ROLE_COUNT} 个角色")
    out: dict[str, dict[str, int]] = {}
    for role, limits in value.items():
        if not isinstance(role, str) or not role.strip():
            raise ValueError("角色名必须是非空字符串")
        if not isinstance(limits, dict):
            raise ValueError(f"角色 {role} 的额度必须是对象")
        entry: dict[str, int] = {}
        legacy_total = 0
        has_legacy = False
        for kind, limit in limits.items():
            if kind in LEGACY_QUOTA_KINDS:
                legacy_total += _require_int(
                    limit, 0, _ROLE_QUOTA_MAX, f"角色 {role} 的 {kind} 额度"
                )
                has_legacy = True
                _note_legacy_kind(str(role).strip(), str(kind))
                continue
            # 额度 kind 与频控 kind 不是同一套：用户侧额度只有 ``af``（V051）。
            if kind not in ai_config.QUOTA_KINDS:
                raise ValueError(f"角色 {role} 出现未知能力 {kind!r}")
            entry[str(kind)] = _require_int(
                limit, 0, _ROLE_QUOTA_MAX, f"角色 {role} 的 {kind} 额度"
            )
        if has_legacy and "af" not in entry:
            entry["af"] = legacy_total
        if not entry:
            raise ValueError(f"角色 {role} 至少要有一个能力的额度")
        out[role.strip()] = entry
    return out


def _build_registry() -> None:
    """从现有模块现读默认值，构建注册表（只执行一次）。"""
    # 延迟导入：``util.attachment`` 与 ``services.ai.config`` 都会反向读取本模块，
    # 模块级互相导入会成环。
    import config as code_config
    from services.agent import context as agent_context
    from services.ai import config as ai_config
    from util import attachment

    specs = [
        SettingSpec(
            key="ai.allowed_roles",
            type="list",
            default=list(code_config.AI_ALLOWED_ROLES),
            group=GROUP_AI,
            label="AI 可用角色",
            description="允许调用 AI 网关的角色（per 口径），至少一个",
            coerce=lambda value: _validate_roles(value, allow_empty=False),
        ),
        SettingSpec(
            key="ai.rate_limit_per_minute",
            type="dict",
            default=dict(code_config.AI_RATE_LIMIT_PER_MINUTE),
            group=GROUP_AI,
            label="AI 每分钟调用上限",
            description="按能力（ocr/chat）的每用户每分钟上限，0 表示不限",
            coerce=_validate_rate_limit,
        ),
        SettingSpec(
            key="ai.prompt_capture_roles",
            type="list",
            default=list(code_config.AI_PROMPT_CAPTURE_ROLES),
            group=GROUP_AI,
            label="正文留存角色",
            description="允许留存对话正文的角色；空数组 = 完全关闭正文留存",
            coerce=lambda value: _validate_roles(value, allow_empty=True),
        ),
        SettingSpec(
            key="ai.prompt_retention_days",
            type="int",
            default=int(code_config.AI_PROMPT_RETENTION_DAYS),
            group=GROUP_AI,
            label="正文留存天数",
            description="AI_CALL_LOGS 之外留存的对话正文保留天数",
            min=1,
            max=3650,
        ),
        SettingSpec(
            key="ai.prompt_max_chars",
            type="int",
            default=int(code_config.AI_PROMPT_MAX_CHARS),
            group=GROUP_AI,
            label="正文单条上限",
            description="单条留存正文的最大字符数，超出截断",
            min=1,
            max=200_000,
        ),
        SettingSpec(
            key="ai.context_limit_default",
            type="int",
            default=int(agent_context.DEFAULT_CONTEXT_LIMIT),
            group=GROUP_AI,
            label="上下文窗口默认值",
            description="未在 AI_MODEL_CONTEXT_LIMITS 中列出的模型使用的上下文窗口（token）",
            min=1000,
            max=2_000_000,
        ),
        SettingSpec(
            key=agent_context.RUNTIME_COMPACT_RATIO,
            # 类型只用于展示：前端对未知类型回落到文本框，值由 coerce 严格校验。
            type="float",
            default=float(agent_context.DEFAULT_COMPACT_RATIO),
            group=GROUP_AI,
            label="上下文压缩阈值",
            description="会话占用达到窗口的该比例时自动压缩（0~1 的小数，如 0.8 表示 80%）",
            coerce=_validate_compact_ratio,
        ),
        SettingSpec(
            key="ai.agent_token_budget",
            type="int",
            default=int(ai_config._AGENT_TOKEN_BUDGET),
            group=GROUP_AI,
            label="ZVAgent token 预算",
            description="一次 ZVAgent 执行累计可消耗的 token 上限（prompt + completion）",
            min=1000,
            max=10_000_000,
        ),
        SettingSpec(
            key="ai.agent_max_iterations",
            type="int",
            default=int(ai_config._AGENT_MAX_ITERATIONS),
            group=GROUP_AI,
            label="ZVAgent 最大轮次",
            description="一次 ZVAgent 执行最多几轮工具调用",
            min=1,
            max=50,
        ),
        SettingSpec(
            key="ai.schedule_quota_department",
            type="int",
            default=int(ai_config.AI_SCHEDULE_QUOTA_DEPARTMENT),
            group=GROUP_AI,
            label="定时任务条数上限（学生会）",
            description="department（学生会）最多能建几条 AI 定时任务；0 = 关闭创建，admin 不受限",
            min=0,
            max=100,
        ),
        SettingSpec(
            key="ai.role_quota",
            type="dict",
            default={
                role: dict(limits)
                for role, limits in code_config.AI_ROLE_QUOTA.items()
            },
            group=GROUP_AI,
            label="按角色月额度",
            description="角色 -> 每月可用的 AF 额度（OCR 与对话共用）；未列出的角色走默认额度",
            coerce=_validate_role_quota,
        ),
        SettingSpec(
            key="ai.chat_max_body_bytes",
            type="int",
            default=int(ai_config._CHAT_MAX_BODY_BYTES),
            group=GROUP_AI,
            label="对话请求体上限",
            description="单次 /v1/chat/completions 请求体的字节上限，超出回 413",
            min=1024,
            max=256 * 1024 * 1024,
        ),
        SettingSpec(
            key="ai.chat_max_messages",
            type="int",
            default=int(ai_config._CHAT_MAX_MESSAGES),
            group=GROUP_AI,
            label="对话消息条数上限",
            description="单次对话请求 messages 的最大条数，超出回 400",
            min=1,
            max=10_000,
        ),
        SettingSpec(
            key="ai.chat_max_content_chars",
            type="int",
            default=int(ai_config._CHAT_MAX_CONTENT_CHARS),
            group=GROUP_AI,
            label="单条消息长度上限",
            description="单条 message.content 的最大字符数，超出回 400",
            min=1,
            max=2_000_000,
        ),
        SettingSpec(
            key="ai.safety_guard_enabled",
            type="bool",
            default=bool(ai_config.AI_SAFETY_GUARD_ENABLED),
            group=GROUP_AI,
            label="LLM 安全认证",
            description=(
                "学生会（department）提交对话前用一次模型调用判定「无关且破坏性」"
                "的请求；关闭后零额外开销"
            ),
        ),
        SettingSpec(
            key="ai.safety_guard_timeout_seconds",
            type="int",
            default=int(ai_config.AI_SAFETY_GUARD_TIMEOUT_SECONDS),
            group=GROUP_AI,
            label="LLM 安全认证超时",
            description="单次安全判定调用的超时秒数；超时按放行处理",
            min=1,
            max=60,
        ),
        SettingSpec(
            key="ops.upload_daily_max_files",
            type="int",
            default=int(attachment.DAILY_MAX_FILES),
            group=GROUP_OPS,
            label="每日上传文件数上限",
            description="每个用户每天最多上传的文件数（全局默认；可被用户级覆盖）",
            min=0,
            max=100_000,
        ),
        SettingSpec(
            key="ops.upload_daily_max_bytes",
            type="int",
            default=int(attachment.DAILY_MAX_BYTES),
            group=GROUP_OPS,
            label="每日上传字节上限",
            description="每个用户每天最多上传的字节数（全局默认；可被用户级覆盖）",
            min=0,
            max=100 * 1024 * 1024 * 1024,
        ),
        SettingSpec(
            key="ops.max_query_count",
            type="int",
            default=int(code_config.MAX_QUERY_COUNT),
            group=GROUP_OPS,
            label="查询串长度上限",
            description="日志检索等查询字符串的最大字符数",
            min=1,
            max=10_000,
        ),
        SettingSpec(
            key="ops.activity_description_max_length",
            type="int",
            default=int(code_config.MAX_ACTIVITY_DESCRIPTION_LENGTH),
            group=GROUP_OPS,
            label="活动描述长度上限",
            description="活动描述允许的最大字符数（受数据库列长度约束，上限 1000）",
            min=1,
            max=1000,
        ),
        SettingSpec(
            key="ops.log_retention_days",
            type="int",
            default=int(code_config.APP_LOGS_RETENTION_DAYS),
            group=GROUP_OPS,
            label="业务日志保留天数",
            description="APP_LOGS 的保留天数，过期行由每日清理任务删除",
            min=1,
            max=3650,
        ),
        SettingSpec(
            key="ops.export_retention_days",
            type="int",
            default=int(code_config.EXPORT_TASKS_RETENTION_DAYS),
            group=GROUP_OPS,
            label="导出任务保留天数",
            description="已完成导出任务的保留天数，过期行由每日清理任务删除",
            min=1,
            max=3650,
        ),
        SettingSpec(
            key="security.scanner_decoy_enabled",
            type="bool",
            # 默认开启：这是「匿名目录扫描」的诱饵，只有命中高信号字典才生效，
            # 正常用户和登录用户都不受影响。管理员可以在这里随时关掉。
            default=True,
            group=GROUP_SECURITY,
            label="扫描器诱饵",
            description=(
                "未登录的 GET/HEAD 命中目录扫描字典时返回 200 和下面的正文；"
                "只作用于 canary 根站，关闭后恢复原有 404/418 行为"
            ),
        ),
        SettingSpec(
            key="security.scanner_decoy_body",
            type="text",
            default="Not Found",
            group=GROUP_SECURITY,
            label="扫描器诱饵正文",
            description="命中时返回的纯文本，支持多行；最多 16384 个字符",
            max=16_384,
        ),
    ]
    for spec in specs:
        REGISTRY[spec.key] = spec


def _registry() -> dict[str, SettingSpec]:
    if not REGISTRY:
        _build_registry()
    return REGISTRY


def registry() -> dict[str, SettingSpec]:
    """公开的注册表视图（测试与文档用；与 :data:`REGISTRY` 是同一个对象）。"""
    return _registry()


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def _validate(spec: SettingSpec, value: Any) -> Any:
    """按注册表校验并规范化一个值；失败抛 :class:`SettingsValidationError`。"""
    if spec.coerce is not None:
        try:
            return spec.coerce(value)
        except SettingsValidationError:
            raise
        except ValueError as exc:
            raise SettingsValidationError(spec.key, str(exc)) from None
        except Exception as exc:  # noqa: BLE001 - 校验器自身出错也必须是 400
            raise SettingsValidationError(spec.key, f"无法校验：{exc}") from None

    if spec.type == "int":
        if isinstance(value, bool) or not isinstance(value, int):
            raise SettingsValidationError(spec.key, "必须是整数")
        low = spec.min if spec.min is not None else -(2**31)
        high = spec.max if spec.max is not None else 2**31 - 1
        if value < low or value > high:
            raise SettingsValidationError(
                spec.key, f"必须在 {low} 到 {high} 之间"
            )
        return value

    if spec.type == "bool":
        if not isinstance(value, bool):
            raise SettingsValidationError(spec.key, "必须是布尔值")
        return value

    if spec.type == "str":
        if not isinstance(value, str):
            raise SettingsValidationError(spec.key, "必须是字符串")
        return value

    if spec.type == "text":
        if not isinstance(value, str):
            raise SettingsValidationError(spec.key, "必须是字符串")
        limit = spec.max if spec.max is not None else 16_384
        if len(value) > limit:
            raise SettingsValidationError(
                spec.key, f"最多 {limit} 个字符"
            )
        return value

    raise SettingsValidationError(spec.key, f"未支持的类型 {spec.type}")


# ---------------------------------------------------------------------------
# 快照刷新
# ---------------------------------------------------------------------------

def _reset_state() -> None:
    """清空快照（测试钩子：用例之间不串值）。"""
    global _last_refresh, _last_version, _last_full_read
    _db_values.clear()
    _db_meta.clear()
    _quota_rows.clear()
    _last_refresh = 0.0
    _last_version = None
    _last_full_read = 0.0
    _legacy_logged.clear()


def reset_cache() -> None:
    """公开的缓存失效入口（测试/管理脚本用）。"""
    _reset_state()


async def _current_version() -> int | None:
    """Redis 里的设置版本号；Redis 不可用时返回 ``None``（闸门禁用，照旧读库）。"""
    try:
        return await cache.version_or_none(VERSION_SCOPE)
    except Exception:  # noqa: BLE001 - 缓存层不该影响设置读取
        return None


async def refresh(*, force: bool = False) -> None:
    """重建进程内快照；``force`` 绕过版本闸直接回源。

    版本闸：先花一次 Redis GET 看 ``settings`` 版本号。版本与上次相同、且上次
    回源距今不到 :data:`FULL_RELOAD_SECONDS` → 库里一定还是那份，直接返回。
    这让「每 5 秒一次刷新」从「4 条 SQL」降成「1 条 GET」，同时别的进程一写
    （``set_many`` → ``cache.flush_scopes(["settings"])``）本进程就看得见。
    """
    global _refreshing, _last_refresh, _last_version, _last_full_read
    if _refreshing:
        # 上一轮还没读完：这一轮直接跳过，不让并发刷新叠加连接。
        return
    _refreshing = True
    try:
        version = await _current_version()
        now = time.monotonic()
        if (
            not force
            and version is not None
            and version == _last_version
            and _last_full_read > 0
            and now - _last_full_read < FULL_RELOAD_SECONDS
        ):
            # 版本没变：别的进程没写过，快照仍然可信，只是把「确认时刻」推后。
            _last_refresh = now
            return

        specs = _registry()

        values: dict[str, Any] = {}
        meta: dict[str, tuple[datetime | None, str | None]] = {}
        rows: list[dict[str, Any]] = []
        try:
            rows = await backends.get_system_settings_repo().all_rows()
        except Exception:  # noqa: BLE001 - 设置中心不可用时业务照常
            logger.exception("读取 SYSTEM_SETTINGS 失败，本次回落代码默认值")
            rows = []

        for row in rows:
            key = row.get("setting_key")
            spec = specs.get(key) if isinstance(key, str) else None
            if spec is None:
                logger.warning("忽略未注册的设置项：%r", key)
                continue
            if row.get("invalid"):
                logger.warning("设置项 %s 的值不是合法 JSON，使用默认值", key)
                continue
            try:
                values[key] = _validate(spec, row.get("value"))
            except SettingsValidationError as exc:
                logger.warning(
                    "设置项 %s 的值不合法，使用默认值：%s", key, exc.reason
                )
                continue
            meta[key] = (row.get("updated_at"), row.get("updated_by"))

        quotas: dict[str, dict[str, Any]] = {}
        try:
            for row in await backends.get_user_quota_overrides_repo().all_rows():
                oid = row.get("user_oid")
                if isinstance(oid, str) and oid:
                    quotas[oid] = dict(row)
        except Exception:  # noqa: BLE001 - 同上：失败即「全部继承全局」
            logger.exception(
                "读取 USER_QUOTA_OVERRIDES 失败，本次全部继承全局设置"
            )
            quotas = {}

        _db_values.clear()
        _db_values.update(values)
        _db_meta.clear()
        _db_meta.update(meta)
        _quota_rows.clear()
        _quota_rows.update(quotas)
        _last_refresh = time.monotonic()
        _last_full_read = _last_refresh
        _last_version = version
    finally:
        _refreshing = False


async def ensure_fresh(force: bool = False) -> None:
    """快照过期（或 ``force``）时刷一次；没有库也不会抛。"""
    if (
        force
        or _last_refresh <= 0
        or time.monotonic() - _last_refresh >= REFRESH_SECONDS
    ):
        await refresh(force=force)


# ---------------------------------------------------------------------------
# 同步读取（不改调用方签名）
# ---------------------------------------------------------------------------

def get_override(key: str) -> Any:
    """返回库里的覆盖值；``None`` = 没有覆盖，调用方继续走原有 settings/config。"""
    _registry()  # 确保注册表已构建（未知键同样返回 None）
    return _db_values.get(key)


def get_sync(key: str) -> Any:
    """返回生效值（覆盖优先，否则注册表默认值）；未知键抛 ``KeyError``。"""
    spec = _registry().get(key)
    if spec is None:
        raise KeyError(f"未注册的系统设置项：{key!r}")
    return _db_values.get(key, spec.default)


def user_quota_override(user_oid: str | None) -> dict[str, Any] | None:
    """某用户的配额覆盖行（没有则 ``None``）。"""
    if not user_oid:
        return None
    row = _quota_rows.get(str(user_oid))
    return dict(row) if row is not None else None


def effective_daily_limits(user_oid: str | None = None) -> tuple[int, int]:
    """每日上传上限：用户级覆盖 → 全局设置 → 代码常量（返回 ``(文件数, 字节数)``）。"""
    files = int(get_sync("ops.upload_daily_max_files"))
    total_bytes = int(get_sync("ops.upload_daily_max_bytes"))
    row = user_quota_override(user_oid)
    if row is not None:
        if row.get("daily_max_files") is not None:
            files = int(row["daily_max_files"])
        if row.get("daily_max_bytes") is not None:
            total_bytes = int(row["daily_max_bytes"])
    return files, total_bytes


# ---------------------------------------------------------------------------
# 异步读取 / 写入
# ---------------------------------------------------------------------------

def _view(spec: SettingSpec) -> dict[str, Any]:
    updated_at, updated_by = _db_meta.get(spec.key, (None, None))
    return {
        "key": spec.key,
        "value": _db_values.get(spec.key, spec.default),
        "type": spec.type,
        "group": spec.group,
        "label": spec.label,
        "description": spec.description,
        "min": spec.min,
        "max": spec.max,
        "options": list(spec.options) if spec.options else None,
        "updated_at": updated_at,
        "updated_by": updated_by,
    }


async def get(key: str) -> Any:
    """读一个键的生效值（先保证快照不旧于 :data:`REFRESH_SECONDS`）。"""
    spec = _registry().get(key)
    if spec is None:
        raise KeyError(f"未注册的系统设置项：{key!r}")
    await ensure_fresh()
    return _db_values.get(key, spec.default)


async def all_settings(force: bool = False) -> dict[str, Any]:
    """``{"items": [...], "defaults": {...}}``；``force=True`` 绕过 TTL 强刷。"""
    await ensure_fresh(force=force)
    specs = _registry()
    return {
        "items": [_view(spec) for spec in specs.values()],
        "defaults": {spec.key: spec.default for spec in specs.values()},
    }


async def set_many(
    updates: dict[str, Any], actor_oid: str | None = None
) -> list[dict[str, Any]]:
    """校验通过后落库，并**立即**改写快照（改完即时生效）。

    先全量校验再落库：一批里有一个非法值时一个都不写，避免管理员的批量提交
    写进去一半。
    """
    global _last_refresh, _last_full_read
    specs = _registry()
    normalized: dict[str, Any] = {}
    for key, raw in updates.items():
        spec = specs.get(key)
        if spec is None:
            raise SettingsValidationError(str(key), "未知设置项")
        normalized[key] = _validate(spec, raw)
    if not normalized:
        return []

    await backends.get_system_settings_repo().upsert_many(normalized, actor_oid)

    now = datetime.now()
    for key, value in normalized.items():
        _db_values[key] = value
        _db_meta[key] = (now, actor_oid)
    # 快照已是最新：TTL 从现在重新计时，避免紧随其后的 ensure_fresh 再读一遍库。
    _last_refresh = _last_full_read = time.monotonic()
    await _publish_change()
    return [_view(specs[key]) for key in normalized]


async def _publish_change() -> None:
    """写后广播：失效该域缓存并自增版本号，让别的进程下一轮刷新回源。

    刻意吞异常：Redis 抖一下不能把「已经落库成功」的写变成 500。失败时把
    ``_last_version`` 置空，本进程下一轮 refresh 照旧读库。
    """
    global _last_version
    try:
        await cache.flush_scopes([VERSION_SCOPE])
        _last_version = await _current_version()
    except Exception:  # noqa: BLE001 - 广播失败只影响跨进程时效
        _last_version = None


def _quota_view(user_oid: str) -> dict[str, Any]:
    row = _quota_rows.get(user_oid) or {}
    files, total_bytes = effective_daily_limits(user_oid)
    return {
        "user_oid": user_oid,
        "daily_max_files": row.get("daily_max_files"),
        "daily_max_bytes": row.get("daily_max_bytes"),
        "effective": {
            "daily_max_files": files,
            "daily_max_bytes": total_bytes,
        },
    }


async def get_user_quota(user_oid: str) -> dict[str, Any]:
    """某用户的配额视图（覆盖值 + 生效值）。"""
    await ensure_fresh()
    return _quota_view(user_oid)


async def set_user_quota(
    user_oid: str,
    daily_max_files: int | None,
    daily_max_bytes: int | None,
    actor_oid: str | None = None,
) -> dict[str, Any]:
    """写用户级覆盖（两列都 ``None`` = 清除覆盖、继承全局），并立即更新快照。"""
    await backends.get_user_quota_overrides_repo().set(
        user_oid, daily_max_files, daily_max_bytes, actor_oid
    )
    if daily_max_files is None and daily_max_bytes is None:
        _quota_rows.pop(user_oid, None)
    else:
        _quota_rows[user_oid] = {
            "user_oid": user_oid,
            "daily_max_files": daily_max_files,
            "daily_max_bytes": daily_max_bytes,
            "updated_by": actor_oid,
            "updated_at": datetime.now(),
        }
    # 用户级覆盖也在同一份快照里：同样要广播（否则别的进程最多 5 分钟才看到，
    # 而上传配额是靠它兜的）。
    await _publish_change()
    return _quota_view(user_oid)


__all__ = [
    "GROUP_AI",
    "GROUP_OPS",
    "REFRESH_SECONDS",
    "REGISTRY",
    "SettingSpec",
    "SettingsValidationError",
    "all_settings",
    "effective_daily_limits",
    "ensure_fresh",
    "get",
    "get_override",
    "get_sync",
    "get_user_quota",
    "refresh",
    "registry",
    "reset_cache",
    "set_many",
    "set_user_quota",
    "user_quota_override",
]
