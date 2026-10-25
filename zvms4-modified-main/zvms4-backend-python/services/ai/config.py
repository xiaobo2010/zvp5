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
    """
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
    替换（与 ``AI_ALLOWED_ROLES`` 同一语义），不合并。
    """
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

    目录按用户算（个人 key 只对本人可见），所以这里必须 async 且带 user_oid；
    同名 model 出现在多个 provider 上时保留目录里先出现的那个（个人 > 共享 >
    服务端），与遮蔽规则一致。
    """
    # 延迟导入：credentials 依赖本模块的 providers()/is_configured()，
    # 模块级互相导入会成环。
    from . import credentials

    mapping: dict[str, str] = {}
    for entry in await credentials.catalog(user_oid):
        for model in entry.get("models") or []:
            if isinstance(model, str) and model:
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
_STREAM_USAGE_HOSTS = frozenset({"api.openai.com", "api.deepseek.com"})


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
    hosts = set(_STREAM_USAGE_HOSTS)
    raw = getattr(settings, "AI_STREAM_USAGE_HOSTS", None)
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
