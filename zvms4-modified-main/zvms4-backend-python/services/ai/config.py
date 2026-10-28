"""AI 网关的配置视图。

所有配置都经 ``getattr(settings, ...)`` 读并给默认值：网关是可选能力，老部署的
``settings.py`` 里没有这些键也必须能正常启动。非法条目（缺 ``base_url``、``kind``
不认识）一律跳过而不是抛异常——写错一个 provider 不该让整个网关 500。
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import config
import settings

from services import settings_runtime

DEFAULT_QUOTA = {
    "ocr": config.AI_DEFAULT_OCR_MONTHLY_UNITS,
    "chat": config.AI_DEFAULT_CHAT_MONTHLY_UNITS,
}

# 站点级 OCR 额度的默认值。**不能**回落到 ``DEFAULT_QUOTA["ocr"]``：那个 100 是
# 「单个用户每月能免费用几次」的口径，站点额度是「平台愿意为所有用户垫多少上游
# 调用」——两者差一个数量级。回落到 100 的结果是：几十个用户一天就把站点额度
# 打满，之后全站 OCR 一起 503。
_DEFAULT_SITE_OCR_MONTHLY_UNITS = 1000


def site_quota() -> dict[str, int]:
    """``settings.AI_SITE_QUOTA``：按引擎 kind 覆盖站点月额度。

    形如 ``{"ocr_tencent": 1000, "ocr_rapidocr": 1000000}``。值非法（负数/非整数）
    直接跳过，保留默认值——写错一个条目不该让额度变成 0 把整条链路掐死。
    """
    raw = getattr(settings, "AI_SITE_QUOTA", {}) or {}
    out: dict[str, int] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            try:
                limit = int(value)
            except (TypeError, ValueError):
                continue
            if limit >= 0:
                out[str(key)] = limit
    return out


def default_site_ocr_limit() -> int:
    """站点级 OCR 额度的兜底值（``settings.AI_DEFAULT_SITE_OCR_MONTHLY_UNITS``）。"""
    raw = getattr(
        settings,
        "AI_DEFAULT_SITE_OCR_MONTHLY_UNITS",
        _DEFAULT_SITE_OCR_MONTHLY_UNITS,
    )
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        limit = _DEFAULT_SITE_OCR_MONTHLY_UNITS
    return max(0, limit)

OPENAI_COMPAT = "openai_compat"
_DEFAULT_CONCURRENCY = 8


def is_enabled() -> bool:
    return bool(getattr(settings, "AI_ENABLED", True))


def allowed_roles() -> frozenset[str]:
    """允许使用 AI 网关的角色集合（``per`` 口径）。

    默认值放 ``config`` 里（代码常量），这里只做 settings 覆盖：写成可配置是为了
    以后放开/收紧（比如只给学生会）不用改代码。空集合或类型不对一律回退到默认
    ——「配置写错」不该变成「谁都用不了」，也不该变成「谁都能用」。

    运行期设置（``ai.allowed_roles``）优先于 ``settings.py``：在线改完即时生效，
    且注册表已保证它是非空字符串数组，不需要再兜底。
    """
    override = settings_runtime.get_override("ai.allowed_roles")
    if isinstance(override, list) and override:
        return frozenset(override)

    raw = getattr(settings, "AI_ALLOWED_ROLES", None)
    if isinstance(raw, str) or not isinstance(raw, (list, tuple, set, frozenset)):
        return frozenset(config.AI_ALLOWED_ROLES)
    roles = {item for item in raw if isinstance(item, str) and item}
    return frozenset(roles) if roles else frozenset(config.AI_ALLOWED_ROLES)


RATE_LIMIT_KINDS = ("ocr", "chat")
ROLE_QUOTA_PRIORITY = ("admin", "department")


def _positive_int(value: object, fallback: int) -> int:
    """只认真正的正整数：``True``/``"10"``/``1.5`` 都不算，回退。

    bool 是 int 的子类，不单独排除的话 ``True`` 会变成「每分钟 1 次」——比配置
    写错更难查。
    """
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return fallback
    return value


def rate_limit_per_minute(kind: str) -> int:
    """某能力的每用户每分钟上限；``0`` 表示不限。

    ``kind`` 不在封闭集合里直接返回 0：未知能力不该被静默套上别的能力的额度。
    settings 只覆盖它写了的 kind，没写的沿用 ``config`` 里的常量。
    """
    if not isinstance(kind, str) or kind not in RATE_LIMIT_KINDS:
        return 0

    fallback = config.AI_RATE_LIMIT_PER_MINUTE.get(kind, 0)
    if isinstance(fallback, bool) or not isinstance(fallback, int) or fallback < 0:
        fallback = 0

    # 运行期设置优先（在线改完即时生效）；没有覆盖才看 settings.py 与 config。
    raw = settings_runtime.get_override("ai.rate_limit_per_minute")
    if not isinstance(raw, dict):
        raw = getattr(settings, "AI_RATE_LIMIT_PER_MINUTE", None)
    if not isinstance(raw, dict) or kind not in raw:
        return fallback
    return _positive_int(raw.get(kind), fallback)


def rate_limit_window_seconds() -> float:
    """滑动窗口长度（秒）。非正数/非数值一律回退默认值。"""
    fallback = float(config.AI_RATE_LIMIT_WINDOW_SECONDS)
    raw = getattr(settings, "AI_RATE_LIMIT_WINDOW_SECONDS", None)
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return fallback
    return float(raw) if raw > 0 else fallback


def role_quota() -> dict[str, dict[str, int]]:
    """``role -> {kind: 月额度}`` 的规范化视图（settings 可覆盖默认表）。

    只保留 ``ocr``/``chat`` 两个 kind 与非负整数：拼错的 kind 写进额度表等于给
    一个永远不会被查询的行定上限，而且不会报错。整张表被 settings 换掉时按整表
    替换（与 ``AI_ALLOWED_ROLES`` 同一语义），不合并。运行期设置
    （``ai.role_quota``）优先于 ``settings.py``。
    """
    raw = settings_runtime.get_override("ai.role_quota")
    if not isinstance(raw, dict):
        raw = getattr(settings, "AI_ROLE_QUOTA", None)
    if not isinstance(raw, dict):
        raw = config.AI_ROLE_QUOTA

    table: dict[str, dict[str, int]] = {}
    for role, limits in raw.items():
        if not isinstance(role, str) or not isinstance(limits, dict):
            continue
        entry: dict[str, int] = {}
        for kind in RATE_LIMIT_KINDS:
            value = limits.get(kind)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                continue
            entry[kind] = value
        if entry:
            table[role] = entry
    return table if table else _normalized_role_quota(config.AI_ROLE_QUOTA)


def _normalized_role_quota(raw: object) -> dict[str, dict[str, int]]:
    """默认表的兜底规范化（默认表是代码常量，理论上一定合法）。"""
    table: dict[str, dict[str, int]] = {}
    if not isinstance(raw, dict):
        return table
    for role, limits in raw.items():
        if not isinstance(role, str) or not isinstance(limits, dict):
            continue
        entry = {
            kind: value
            for kind, value in limits.items()
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0
        }
        if entry:
            table[role] = entry
    return table


def providers() -> dict[str, dict]:
    """``settings.AI_PROVIDERS`` 的规范化视图。

    ``headers``/``api_path`` 是给非 OpenAI 形状的服务端（自定义鉴权头、非标准
    路径）用的，透传给服务层；非 dict 条目与非法形状按「没配」跳过——写错一个
    provider 不该让整个网关 500，与 :func:`is_configured` 的约定一致。
    """
    raw = getattr(settings, "AI_PROVIDERS", {}) or {}
    if not isinstance(raw, dict):
        return {}
    view: dict[str, dict] = {}
    for name, cfg in raw.items():
        if not isinstance(cfg, dict):
            continue
        entry = dict(cfg)
        headers = _config_headers(cfg.get("headers"))
        if headers:
            entry["headers"] = headers
        else:
            entry.pop("headers", None)
        path = _config_api_path(cfg.get("api_path"))
        if path:
            entry["api_path"] = path
        else:
            entry.pop("api_path", None)
        view[str(name)] = entry
    return view


def _config_headers(raw: object) -> dict[str, str]:
    """settings 里的 headers：只留 str→str 且头名合法的项，其余静默丢弃。"""
    if not isinstance(raw, dict):
        return {}
    headers: dict[str, str] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not isinstance(value, str) or not name:
            continue
        if "\r" in name or "\n" in name or "\r" in value or "\n" in value:
            continue
        if any(ch.isspace() or ch < " " or ch == "\x7f" or ch == ":" or ord(ch) > 127 for ch in name):
            continue
        headers[name] = value
    return headers


def _config_api_path(raw: object) -> str:
    """settings 里的 api_path：只接受相对路径，非法按未配置。"""
    if not isinstance(raw, str):
        return ""
    path = raw.strip()
    if (
        not path
        or path.startswith("//")
        or "://" in path
        or "?" in path
        or "#" in path
        or not path.startswith("/")
    ):
        return ""
    if any(ch.isspace() or ch < " " or ch == "\x7f" for ch in path):
        return ""
    return path


def siliconflow_free_models() -> list[str]:
    """``settings.AI_SILICONFLOW_FREE_MODELS``：SiliconFlow 免费模型白名单。

    免费档会变，所以清单是**运维资产**而不是代码常量：网关只负责读，不负责猜。
    空列表表示「没配」，调用方（``misc/seed_channel.py``）据此拒绝上线——回落到
    「上游全部模型」几乎必然把付费档暴露给所有登录用户，比渠道不可用糟得多。

    非字符串条目与空白项逐个丢弃，重复项按首次出现保留：这份清单会直接变成凭据的
    ``models``，脏数据留着只会让候选列表变长。
    """
    raw = getattr(settings, "AI_SILICONFLOW_FREE_MODELS", []) or []
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return []
    models: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        model = item.strip()
        if model and model not in models:
            models.append(model)
    return models


def max_ocr_image_bytes() -> int:
    """OCR 单图体积上限（字节）；``settings`` 可覆盖 ``config`` 的默认 20MB。

    非正数/非整数一律回退默认值：把上限配成 0 或负数等于把 OCR 整条链路掐死，
    而这类错误在启动时看不出来，只有第一个用户撞上才发现。
    """
    fallback = int(config.AI_OCR_MAX_IMAGE_BYTES)
    raw = getattr(settings, "AI_OCR_MAX_IMAGE_BYTES", fallback)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def max_concurrency() -> int:
    try:
        value = int(getattr(settings, "AI_MAX_CONCURRENCY", _DEFAULT_CONCURRENCY))
    except (TypeError, ValueError):
        return _DEFAULT_CONCURRENCY
    return value if value >= 1 else _DEFAULT_CONCURRENCY


def worker_count() -> int:
    """部署的 worker 进程数（单 worker = 1）。

    读法依次是 ``settings.AI_WORKER_COUNT`` → ``config.AI_WORKER_COUNT`` →
    环境变量 ``WEB_CONCURRENCY`` → ``UVICORN_WORKERS`` → 1。环境变量是 gunicorn /
    uvicorn 的标准旋钮，运维在启动命令上写 ``--workers 4`` 时只有
    ``UVICORN_WORKERS`` 会跟着变，所以两者都要认。

    非正整数（``"abc"``、``0``、``-1``、``True``）一律回退 1：把 worker 数写成 0
    会让进程内阈值除零，写成 ``True`` 会变成「1 个 worker 但阈值按 1 折算」的
    假象——都不该静默通过。
    """
    candidates = (
        getattr(settings, "AI_WORKER_COUNT", None),
        getattr(config, "AI_WORKER_COUNT", None),
        os.environ.get("WEB_CONCURRENCY"),
        os.environ.get("UVICORN_WORKERS"),
    )
    for value in candidates:
        count = _strict_positive_int(value)
        if count is not None:
            return count
    return 1


def _strict_positive_int(value: object) -> int | None:
    """只认真正的正整数；``True``/``"4"`` 这类「看起来像」的值分别处理。

    字符串要认（环境变量天生是字符串），bool 不认（``True`` 是 int 的子类）。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            value = int(text)
        except ValueError:
            return None
    if not isinstance(value, int):
        return None
    return value if value >= 1 else None


def api_key_cache_ttl() -> float:
    """API key 校验缓存的秒数；非正数/非数值回退 30 秒。"""
    fallback = float(getattr(config, "AI_API_KEY_CACHE_TTL", 30))
    try:
        value = float(getattr(settings, "AI_API_KEY_CACHE_TTL", fallback))
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def api_key_rate_limit_per_minute() -> int:
    """单个 API key 的每分钟上限（额外于用户维度）；0 = 不限。

    默认 60 而不是沿用 chat 的 20：一把 key 通常代表一个自动化客户端，比交互式
    对话更容易突发；真正的成本闸门仍是月额度。
    """
    fallback = int(getattr(config, "AI_API_KEY_RATE_LIMIT_PER_MINUTE", 60))
    try:
        value = int(getattr(settings, "AI_API_KEY_RATE_LIMIT_PER_MINUTE", fallback))
    except (TypeError, ValueError):
        return fallback
    return value if value >= 0 else fallback


_AGENT_MAX_ITERATIONS = 10
# 与上下文窗口（``AI_DEFAULT_CONTEXT_LIMIT`` 默认 200000）对齐：预算低于窗口
# 时，agent 会在窗口填满之前先被预算掐断，调大窗口等于白调。
_AGENT_TOKEN_BUDGET = 200_000
_AGENT_PROPOSAL_TTL_SECONDS = 600


def _agent_positive_int(name: str, fallback: int) -> int:
    """读 ``settings`` 里的正整数；非法一律回退。

    上限配成 0 或负数等于把 agent 整条链路掐死（一轮都不跑），配成 ``True``
    会变成 1 —— 两种都该回退到默认值而不是静默生效。
    """
    raw = getattr(settings, name, fallback)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw <= 0:
        return fallback
    return raw


def agent_max_iterations() -> int:
    """一次 agent run 最多几轮工具调用（``settings.AI_AGENT_MAX_ITERATIONS``）。

    运行期设置 ``ai.agent_max_iterations`` 优先；在线改完即时生效。
    """
    override = settings_runtime.get_override("ai.agent_max_iterations")
    if isinstance(override, int) and not isinstance(override, bool) and override > 0:
        return override
    return _agent_positive_int("AI_AGENT_MAX_ITERATIONS", _AGENT_MAX_ITERATIONS)


def agent_token_budget() -> int:
    """一次 agent run 累计可消耗的 token 上限（含 prompt + completion）。

    运行期设置 ``ai.agent_token_budget`` 优先；在线改完即时生效。
    """
    override = settings_runtime.get_override("ai.agent_token_budget")
    if isinstance(override, int) and not isinstance(override, bool) and override > 0:
        return override
    return _agent_positive_int("AI_AGENT_TOKEN_BUDGET", _AGENT_TOKEN_BUDGET)


def agent_proposal_ttl_seconds() -> int:
    """写工具提案的有效期（秒）；过期后确认回 410。"""
    return _agent_positive_int(
        "AI_AGENT_PROPOSAL_TTL_SECONDS", _AGENT_PROPOSAL_TTL_SECONDS
    )


# prompt 受控留存（AI_PROMPT_AUDIT）的三个默认值。``services.ai.prompt_audit``
# 与 ``tasks.retention`` 都只从这里取口径，避免「捕获按一个上限、清理按另一个
# 窗口」这种分裂。
_PROMPT_CAPTURE_ROLES = ("department",)
_PROMPT_RETENTION_DAYS = 30
_PROMPT_MAX_CHARS = 8000


def _config_positive_int(name: str, fallback: int) -> int:
    """读 ``config`` 里的正整数；字符串数字也算（与 ``tasks.retention`` 同口径）。

    ``_positive_int`` 对 ``"30"`` 一律回退，那条规则是给**运行时阈值**的（
    ``True`` 会变成 1）。保留期不同：它可能由部署脚本从环境变量解析成字符串写进
    ``config.py``，而 ``tasks.retention`` 一直是 ``int(raw)`` 的宽松口径。两处
    读同一个键，口径必须一致，否则「捕获按 30 天、清理按 7 天」。
    """
    raw = getattr(config, name, fallback)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return fallback
    return value if value > 0 else fallback


def prompt_capture_roles() -> frozenset[str]:
    """允许留存正文的角色集合（``per`` 口径）。

    ``settings.AI_PROMPT_CAPTURE_ROLES`` 是显式的**关闭开关**：写成 ``()``
    （空元组/空列表）表示一个角色都不留；类型写错（字符串、``None``）才回退
    默认集合。这与 :func:`allowed_roles` 有意不同——那边的空集合是「配置写坏」，
    这边空集合是最常见的隐私诉求，不该被静默改回「开着」。

    运行期设置 ``ai.prompt_capture_roles`` 优先，且同样把空数组当成「关闭」。
    """
    override = settings_runtime.get_override("ai.prompt_capture_roles")
    if isinstance(override, list):
        return frozenset(item for item in override if isinstance(item, str) and item)

    raw = getattr(settings, "AI_PROMPT_CAPTURE_ROLES", None)
    if isinstance(raw, str) or not isinstance(raw, (list, tuple, set, frozenset)):
        return frozenset(config.AI_PROMPT_CAPTURE_ROLES)
    return frozenset(item for item in raw if isinstance(item, str) and item)


def prompt_retention_days() -> int:
    """正文保留天数；``config`` 是兜底，``settings`` 覆盖，非法值逐层回退。

    运行期设置 ``ai.prompt_retention_days`` 优先级最高。
    """
    override = settings_runtime.get_override("ai.prompt_retention_days")
    if isinstance(override, int) and not isinstance(override, bool) and override > 0:
        return override
    fallback = _config_positive_int("AI_PROMPT_RETENTION_DAYS", _PROMPT_RETENTION_DAYS)
    return _positive_int(
        getattr(settings, "AI_PROMPT_RETENTION_DAYS", fallback), fallback
    )


def prompt_max_chars() -> int:
    """单条正文留存上限（字符）；超长在服务端截断。

    运行期设置 ``ai.prompt_max_chars`` 优先级最高。
    """
    override = settings_runtime.get_override("ai.prompt_max_chars")
    if isinstance(override, int) and not isinstance(override, bool) and override > 0:
        return override
    fallback = _config_positive_int("AI_PROMPT_MAX_CHARS", _PROMPT_MAX_CHARS)
    return _positive_int(getattr(settings, "AI_PROMPT_MAX_CHARS", fallback), fallback)


def provider_api_key(cfg: dict) -> str | None:
    """先环境变量，再 settings。

    环境变量优先：密钥不该为了生效而落进 ``settings.py``（它会被 rsync 到部署机）。
    """
    if not isinstance(cfg, dict):
        return None
    env_name = cfg.get("api_key_env")
    if not env_name:
        return None
    value = os.environ.get(str(env_name))
    if not value:
        value = getattr(settings, str(env_name), None)
    if not value:
        return None
    return str(value)


def is_configured(cfg: dict) -> bool:
    """可用 = kind 受支持 + 有 base_url + 能拿到 key。三者缺一就当作没配。"""
    if not isinstance(cfg, dict):
        return False
    if cfg.get("kind") != OPENAI_COMPAT:
        return False
    if not cfg.get("base_url"):
        return False
    return provider_api_key(cfg) is not None


def credentials_cache_ttl() -> float:
    """凭据目录的进程内缓存时长（秒）。

    TTL 短一点是刻意的：管理员在 A 实例改完 key，B 实例最多晚这么久才看到；改完
    立刻生效的路径是 ``credentials.invalidate()``（同进程）。
    """
    try:
        value = float(getattr(config, "AI_CREDENTIALS_CACHE_TTL", 30))
    except (TypeError, ValueError):
        return 30.0
    return value if value > 0 else 30.0


async def allowed_models_for(user_oid: str) -> dict[str, str]:
    """该用户可见的 model -> 提供它的 entry key。

    目录按用户算（个人 key 只对本人可见），所以这里必须 async 且带 user_oid。

    键有**两种**：

    * **合格 ID** ``provider/model`` —— 每个 provider 的每个模型各一条，同名模型
      因此不会互相遮蔽（前端下拉、``/v1/models``、新落库的数据都用它）；
    * **裸名别名** —— 只在「整个目录里只有这一个 provider 提供它」时才给。老数据
      （会话、定时任务、历史调用记录）存的是裸名，靠这条别名继续能调到；同名多
      渠道时**不给**别名，否则又回到「先出现者胜」那条死路，歧义必须在
      ``credentials.resolve_model`` 里显式报出来。

    合格 ID 先写入、裸名别名只做 ``setdefault``：万一某个裸名恰好等于另一个渠道
    的合格 ID，以合格 ID 为准（确定性优先）。
    """
    # 延迟导入：credentials 依赖本模块的 providers()/is_configured()，
    # 模块级互相导入会成环。
    from . import credentials

    entries = await credentials.catalog(user_oid)
    rows = credentials.catalog_models(entries)

    mapping: dict[str, str] = {}
    bare_counts: dict[str, int] = {}
    for entry, model, qualified in rows:
        mapping.setdefault(qualified, entry["key"])
        bare_counts[model] = bare_counts.get(model, 0) + 1

    for entry, model, _qualified in rows:
        if bare_counts.get(model) == 1:
            mapping.setdefault(model, entry["key"])
    return mapping


def public_provider_view() -> list[dict]:
    """供 ``/api/ai/health`` 的脱敏视图：只留 host，不含 key、不含完整路径。"""
    view: list[dict] = []
    for name, cfg in providers().items():
        if not isinstance(cfg, dict):
            continue
        models = cfg.get("models") or []
        view.append(
            {
                "name": name,
                "kind": cfg.get("kind"),
                "base_url": _host_only(cfg.get("base_url")),
                "models": [m for m in models if isinstance(m, str)] if isinstance(models, list) else [],
                "configured": is_configured(cfg),
            }
        )
    return view


# 文档明确支持 ``stream_options.include_usage`` 的 OpenAI 兼容服务（host 精确匹配）。
# SiliconFlow 与 OpenAI 形状一致且认这个字段：不加进默认值，公开渠道的流式调用
# 就永远拿不到 token 数，额度只能靠字符估算。
_STREAM_USAGE_HOSTS = frozenset(
    {"api.openai.com", "api.deepseek.com", "api.siliconflow.cn"}
)


def stream_usage_hosts() -> frozenset[str]:
    """允许带 ``stream_options.include_usage`` 的上游 host 白名单。

    默认只含**确定**接受这个参数的 OpenAI 兼容服务；``settings.AI_STREAM_USAGE_HOSTS``
    可以往白名单里**追加**（自建 vLLM / 网关确认支持时写自己的 host）。``"*"`` =
    全部允许，留给「所有上游都是自己的、都确认支持」的部署。

    非法条目（非字符串 / 带路径 / 带空格 / 带端口）逐个丢弃，全非法则只剩默认
    集合——配错一行不该让「拿不到 usage」升级成「流式请求被上游 400」。

    为什么按 host 而不是按 provider 名：网关里的 provider 名是运维起的标签，
    与上游真实实现没有绑定关系（``ai_credentials.kind`` 只有一个
    ``openai_compat``），拿名字判断等于拿注释当类型。
    """
    return _sanitised_hosts(
        _STREAM_USAGE_HOSTS, getattr(settings, "AI_STREAM_USAGE_HOSTS", None)
    )


PROMPT_CACHE_MODES = ("off", "auto", "explicit")

# ``prompt_cache_key`` 是 OpenAI 2026 的标准请求字段（只影响路由亲和）。服务端自动
# 做前缀缓存的上游（DeepSeek / Gemini 一类）不吃这个字段，所以默认只放确定的 host。
_DEFAULT_PROMPT_CACHE_KEY_HOSTS = frozenset({"api.openai.com"})

_PROMPT_CACHE_KEY_PREFIX_DEFAULT = "zvp"
# 前缀要拼进 ``prompt_cache_key``（上游上限 64），留够指纹与分隔符的空间。
_PROMPT_CACHE_KEY_PREFIX_MAX_LEN = 24


def prompt_cache_mode() -> str:
    """``settings.AI_PROMPT_CACHE``：``off`` / ``auto`` / ``explicit``。

    ``auto`` 只注入 ``prompt_cache_key``（路由提示，形状不变）；``explicit`` 额外给
    system 段与 tools 段打 ``cache_control`` 断点（会改写 content 形状，只对会翻译
    这个标记的兼容层有效）。

    配错（非字符串 / 拼歪的值）一律退回 ``off``：这个开关决定「要不要往请求体里加
    字段」，值不认识时最安全的结果是原样透传——替运维猜一个值等于在没确认过的
    上游身上做实验，最坏情况是上游 400 把对话打挂。
    """
    raw = getattr(settings, "AI_PROMPT_CACHE", "auto")
    if not isinstance(raw, str):
        return "off"
    mode = raw.strip().lower()
    return mode if mode in PROMPT_CACHE_MODES else "off"


def prompt_cache_key_hosts() -> frozenset[str]:
    """允许注入 ``prompt_cache_key`` 的上游 host 白名单。

    ``prompt_cache_key`` 是 OpenAI 2026 的标准请求字段，语义只有「同一前缀尽量落到
    同一个缓存节点」——不改内容、不改计费，最坏情况是上游忽略它。DeepSeek / Gemini
    这类服务端自动前缀缓存不吃这个字段，所以默认只放**确定**认它的 host；
    ``settings.AI_PROMPT_CACHE_HOSTS`` 可以追加（自建 vLLM / 网关确认支持时）。
    ``"*"`` = 全部允许。

    净化规则与 :func:`stream_usage_hosts` 完全一致（同一份实现）：非法条目逐个
    丢弃，全非法则只剩默认集合——配错一行不该让「多省一点钱」升级成「请求被上游
    400」。
    """
    return _sanitised_hosts(
        _DEFAULT_PROMPT_CACHE_KEY_HOSTS,
        getattr(settings, "AI_PROMPT_CACHE_HOSTS", None),
    )


def prompt_cache_breakpoint_hosts() -> frozenset[str]:
    """允许注入 Anthropic 风格 ``cache_control`` 断点的上游 host 白名单（默认空）。

    这是给 OpenRouter / LiteLLM 这类会把 ``cache_control`` 翻译成上游原生缓存标记
    的兼容层用的，所以默认空集：**直连 OpenAI 官方端点不要加**——那边的显式缓存
    字段名与形状都不同，多出来的 ``cache_control`` 只是未知字段，严格校验请求体的
    端点会直接 400。

    净化规则同上（``settings.AI_PROMPT_CACHE_BREAKPOINT_HOSTS`` 追加）。
    """
    return _sanitised_hosts(
        frozenset(), getattr(settings, "AI_PROMPT_CACHE_BREAKPOINT_HOSTS", None)
    )


def prompt_cache_key_prefix() -> str:
    """``prompt_cache_key`` 的前缀（默认 ``zvp``），用来区分「这把 key 是谁发的」。

    只保留 ``[A-Za-z0-9_-]``、截到 24 字符，清空后回退默认值：前缀是拼进客户端可见
    字段的，带空格 / 引号 / 非 ASCII 会变成脏数据，而超长会把指纹挤出 64 字符上限。
    """
    raw = getattr(
        settings, "AI_PROMPT_CACHE_KEY_PREFIX", _PROMPT_CACHE_KEY_PREFIX_DEFAULT
    )
    if not isinstance(raw, str):
        return _PROMPT_CACHE_KEY_PREFIX_DEFAULT
    cleaned = "".join(ch for ch in raw if _is_key_prefix_char(ch))
    cleaned = cleaned[:_PROMPT_CACHE_KEY_PREFIX_MAX_LEN]
    return cleaned or _PROMPT_CACHE_KEY_PREFIX_DEFAULT


def _is_key_prefix_char(ch: str) -> bool:
    """ASCII 字母 / 数字 / ``_`` / ``-``。``str.isalnum()`` 会放行中文，不能用。"""
    return (
        "a" <= ch <= "z"
        or "A" <= ch <= "Z"
        or "0" <= ch <= "9"
        or ch == "_"
        or ch == "-"
    )


def _sanitised_hosts(
    defaults: frozenset[str], raw: object
) -> frozenset[str]:
    """默认集合 + settings 追加项，逐条净化。

    非字符串 / 空 / 带路径 / 带空格 / 带端口的条目直接丢：host 白名单只做精确匹配，
    一条 ``https://api.x.com/v1`` 混进来就永远命中不了，而 ``" * "`` 这种笔误要是被
    当成通配符，会把「确认过的上游」变成「所有上游」。
    """
    hosts = set(defaults)
    if isinstance(raw, (list, tuple, set, frozenset)):
        for item in raw:
            if not isinstance(item, str):
                continue
            host = item.strip().lower()
            if not host:
                continue
            if host == "*":
                hosts.add("*")
                continue
            if "/" in host or " " in host or ":" in host:
                continue
            hosts.add(host)
    return frozenset(hosts)


def _host_only(url: object) -> str:
    if not url or not isinstance(url, str):
        return ""
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


# ---------------------------------------------------------------------------
# 请求体上限（``/v1/chat/completions``）
# ---------------------------------------------------------------------------
#
# 网关以前收裸 dict：一个 100MB 的 body 会被 FastAPI 整个读进内存再解析，单 worker
# 下这是「一个请求把进程内存打满」的入口。三档上限互相独立：
#
# * ``body_bytes`` —— 总字节（先看 Content-Length，再按实际读取二次校验）；
# * ``messages`` —— 条数（防「一万条空消息」这种结构化膨胀）；
# * ``content_chars`` —— 单条正文（``content`` 是字符串，或 multimodal 的 text 段）。
_CHAT_MAX_BODY_BYTES = 4 * 1024 * 1024
_CHAT_MAX_MESSAGES = 200
_CHAT_MAX_CONTENT_CHARS = 120_000


def _runtime_positive_int(override_key: str, setting_name: str, fallback: int) -> int:
    """运行期设置优先（在线改完即时生效），非法值回退 ``config`` 里的常量。"""
    override = settings_runtime.get_override(override_key)
    if isinstance(override, int) and not isinstance(override, bool) and override > 0:
        return override
    return _positive_int(getattr(settings, setting_name, fallback), fallback)


def chat_max_body_bytes() -> int:
    """单次 chat 请求体的字节上限（默认 4MiB）。"""
    return _runtime_positive_int(
        "ai.chat_max_body_bytes", "AI_CHAT_MAX_BODY_BYTES", _CHAT_MAX_BODY_BYTES
    )


def chat_max_messages() -> int:
    """单次 chat 请求 ``messages`` 的条数上限（默认 200）。"""
    return _runtime_positive_int(
        "ai.chat_max_messages", "AI_CHAT_MAX_MESSAGES", _CHAT_MAX_MESSAGES
    )


def chat_max_content_chars() -> int:
    """单条 ``content`` 的字符上限（默认 120000）。"""
    return _runtime_positive_int(
        "ai.chat_max_content_chars",
        "AI_CHAT_MAX_CONTENT_CHARS",
        _CHAT_MAX_CONTENT_CHARS,
    )


# ---------------------------------------------------------------------------
# 请求时 SSRF 复核的白名单快路径
# ---------------------------------------------------------------------------
#
# 官方端点永远是公网 DNS：给它们留一条「一次 DNS 都不做」的快路径，正常请求的
# 延迟与加固前完全一致。自建/中转上游要享受同样的快路径，用
# ``settings.AI_SSRF_ALLOW_HOSTS`` 显式声明（``"*"`` = 全部跳过，只在「所有上游
# 都在内网、由我们自己运维」的部署里使用）。
_OFFICIAL_UPSTREAM_HOSTS = frozenset(
    {
        "api.openai.com",
        "api.anthropic.com",
        "api.deepseek.com",
        "api.siliconflow.cn",
        "api.moonshot.cn",
        "api.mistral.ai",
        "api.groq.com",
        "api.together.xyz",
        "openrouter.ai",
        "open.bigmodel.cn",
        "dashscope.aliyuncs.com",
        "generativelanguage.googleapis.com",
    }
)


def upstream_host_allowlist() -> frozenset[str]:
    """请求时 SSRF 复核跳过 DNS 的 host 白名单（精确匹配，``"*"`` = 全部跳过）。

    净化规则与 :func:`stream_usage_hosts` 同一份实现：带路径/端口/空格的条目逐个
    丢弃——白名单是**放宽**安全判定，一条写歪的条目不该被当成通配符。
    """
    return _sanitised_hosts(
        _OFFICIAL_UPSTREAM_HOSTS, getattr(settings, "AI_SSRF_ALLOW_HOSTS", None)
    )


# ---------------------------------------------------------------------------
# 定时任务配额
# ---------------------------------------------------------------------------

# 学生会（department）能建几条定时任务。admin 不限（平台运维本来就要能造样例），
# 其他角色连创建入口都没有（路由层 403）——所以这里只有 department 的数值。
AI_SCHEDULE_QUOTA_DEPARTMENT = 1


def schedule_quota_department() -> int:
    """``ai.schedule_quota_department``：在线可调，默认 :data:`AI_SCHEDULE_QUOTA_DEPARTMENT`。

    0 是合法取值（临时关闭学生会的创建能力），所以这里只排除负数与非整数，
    不能像额度那样把 0 当非法值回退——那会让「关掉」这个意图变成「恢复默认 1」。
    """
    override = settings_runtime.get_override("ai.schedule_quota_department")
    if isinstance(override, int) and not isinstance(override, bool) and override >= 0:
        return override
    return int(AI_SCHEDULE_QUOTA_DEPARTMENT)


# ---------------------------------------------------------------------------
# LLM 安全认证（department 专属的灰区闸门）
# ---------------------------------------------------------------------------

# 默认开启：这是一道「只在学生会提交时多一次调用」的闸门，关掉等于放弃灰区
# 识别；但它必须是**在线可关**的——上游持续抖动时运维要能一键退回关键词规则。
AI_SAFETY_GUARD_ENABLED = True
# 判定跑在用户等待的同步路径上，8 秒是「宁可放行也不要让对话卡住」的上限。
AI_SAFETY_GUARD_TIMEOUT_SECONDS = 8


def safety_guard_enabled() -> bool:
    """``ai.safety_guard_enabled``：在线可调，默认 :data:`AI_SAFETY_GUARD_ENABLED`。

    只认真正的布尔值：``settings.py`` 里写成字符串 ``"false"`` 会被当成默认
    （开启），因为 ``bool("false") == True`` 会让「关掉」这个意图静默失效。
    """
    override = settings_runtime.get_override("ai.safety_guard_enabled")
    if isinstance(override, bool):
        return override
    raw = getattr(settings, "AI_SAFETY_GUARD_ENABLED", AI_SAFETY_GUARD_ENABLED)
    return raw if isinstance(raw, bool) else AI_SAFETY_GUARD_ENABLED


def safety_guard_timeout_seconds() -> float:
    """``ai.safety_guard_timeout_seconds``：单次判定调用的超时秒数。"""
    override = settings_runtime.get_override("ai.safety_guard_timeout_seconds")
    if isinstance(override, int) and not isinstance(override, bool) and override > 0:
        return float(override)
    raw = getattr(
        settings,
        "AI_SAFETY_GUARD_TIMEOUT_SECONDS",
        AI_SAFETY_GUARD_TIMEOUT_SECONDS,
    )
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw <= 0:
        raw = AI_SAFETY_GUARD_TIMEOUT_SECONDS
    return float(raw)
