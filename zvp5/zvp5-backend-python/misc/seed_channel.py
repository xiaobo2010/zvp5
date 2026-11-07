"""把 SiliconFlow 的免费模型挂成 ``shared`` 渠道（平台出资、全员可见）。

为什么是脚本而不是网页表单：``shared`` 凭据的主人不是某个管理员，轮换 key 也不
该由人在页面上手工粘；而这个渠道有一条硬规则——**只上免费模型**。规则写进脚本
才拦得住手滑，写进流程文档拦不住。

``AI_SILICONFLOW_FREE_MODELS``（运维维护的免费白名单）是唯一模型来源：脚本可以
用 key 去问上游「你现在能看到哪些模型」，但结果的用途只有**收窄**白名单，永远
不会把白名单外的模型写进凭据。

用法（在后端根目录）::

    .venv/bin/python misc/seed_channel.py --dry-run --free-only
    .venv/bin/python misc/seed_channel.py --free-only \
        --key-file ~/.config/zvms/siliconflow.env

退出码：0 = 成功（含 dry-run）；2 = 参数错 / 白名单为空 / 无交集 / 拿不到 key。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

# 以 ``python misc/seed_channel.py`` 运行时 ``sys.path[0]`` 是 ``misc/``，``services``
# 与 ``settings`` 都不可见；把后端根放进来，脚本才能复用运行时那套服务层。
_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import httpx  # noqa: E402

from services.ai import config as ai_config  # noqa: E402
from services.ai import credentials  # noqa: E402

DEFAULT_NAME = "siliconflow"
DEFAULT_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_KEY_ENV = "SILICONFLOW_API_KEY"

_HTTP_TIMEOUT = 15.0

# 上限取自凭据服务：超过它 ``create_entry`` 直接 400，脚本自己再写一个数迟早漂。
MAX_MODELS = getattr(credentials, "_MODELS_MAX", 20)

# 服务层的 admin 断言只认 ``per``，凭据真正的主人由 scope 决定（shared 固定是
# 24 个 0 的哨兵），所以这里构造一个「够权限的形状」就够，不需要真实管理员账号。
ADMIN = {"id": credentials.SHARED_OWNER, "per": ["admin"]}


class ChannelError(Exception):
    """可预期的失败：打一行原因、退出码 2，不打 traceback。"""


# ---------------------------------------------------------------------------
# 参数与输入
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="把 SiliconFlow 免费模型写成 shared 渠道凭据",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--name", default=DEFAULT_NAME, help="凭据名（同名即轮换）")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, dest="base_url")
    parser.add_argument(
        "--key-file",
        default=None,
        dest="key_file",
        help="dotenv 路径；优先从这里取 key",
    )
    parser.add_argument(
        "--key-env",
        default=DEFAULT_KEY_ENV,
        dest="key_env",
        help="dotenv 与环境变量里的变量名",
    )
    parser.add_argument(
        "--free-only",
        action="store_true",
        dest="free_only",
        help="用 key 问上游 /models，与免费白名单取交集（收窄，不扩集）",
    )
    parser.add_argument(
        "--models",
        action="append",
        default=[],
        metavar="M1,M2",
        help="显式指定要写入的模型（必须都在免费白名单内，可重复）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="只打印计划，不写库",
    )
    return parser.parse_args(argv)


def _read_dotenv(path: Path) -> dict[str, str]:
    """最小 dotenv 解析：``KEY=VALUE``，忽略注释/空行/``export`` 前缀与成对引号。

    不引第三方解析器：这里只读一个变量，多一个依赖等于给运维多一个装不上的机会。
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ChannelError(f"读不到 key 文件 {path}: {exc}") from None

    values: dict[str, str] = {}
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        if text.startswith("export "):
            text = text[len("export ") :].lstrip()
        key, _, raw = text.partition("=")
        value = raw.strip().strip('"').strip("'")
        if key.strip() and value:
            values[key.strip()] = value
    return values


def resolve_api_key(*, key_file: str | None, key_env: str) -> str:
    """key 来源：``--key-file`` 的 dotenv 优先，其次进程环境变量。

    顺序按「显式指定 > 隐式继承」排：文件是运维这一次指过来的，进程环境里可能是
    上一条命令留下的旧 key。
    """
    if key_file:
        value = _read_dotenv(Path(key_file).expanduser()).get(key_env)
        if value:
            return value

    value = os.environ.get(key_env, "").strip()
    if value:
        return value

    raise ChannelError(
        f"拿不到 {key_env}：用 --key-file 指一个 dotenv，或把它放进环境变量"
    )


def _split_models(raw: list[str] | None) -> list[str]:
    """``--models a,b --models c`` → 去重后的有序清单。"""
    models: list[str] = []
    for chunk in raw or []:
        for item in str(chunk).split(","):
            model = item.strip()
            if model and model not in models:
                models.append(model)
    return models


def _clean_base_url(value: str) -> str:
    """只做形状校验（不解析 DNS）：dry-run 也要能提前发现写成 ``api.siliconflow``。"""
    url = str(value or "").strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ChannelError(f"base_url 必须是 http(s) 地址: {value!r}")
    if parts.query or parts.fragment:
        raise ChannelError(
            "base_url 不能带 query/fragment（后面要拼 /models 与 /chat/completions）"
        )
    return url


# ---------------------------------------------------------------------------
# 模型选择
# ---------------------------------------------------------------------------


def select_models(
    whitelist: list[str],
    *,
    explicit: list[str] | None = None,
    available: list[str] | None = None,
) -> list[str]:
    """从免费白名单里挑出这次要写入的模型。

    三条硬规则：

    * 白名单为空 → 直接失败。此时「上游全部模型」看起来最省事，但那几乎必然包含
      付费档，等于把平台的钱包交给所有登录用户；
    * ``explicit`` 只能收窄，不能越界：写进凭据的每个模型都必须已在白名单里；
    * ``available`` 只能收窄：上游看不到的模型写进凭据只会变成调用时的 404。
    """
    if not whitelist:
        raise ChannelError(
            "免费模型白名单为空：先把 AI_SILICONFLOW_FREE_MODELS 配进 settings（脚本不猜哪些模型免费）"
        )

    if explicit:
        outside = [model for model in explicit if model not in whitelist]
        if outside:
            raise ChannelError(
                "这些模型不在免费白名单里，拒绝写入凭据: " + ", ".join(outside)
            )
        picked = list(explicit)
    else:
        picked = list(whitelist)

    if available is not None:
        visible = {str(model).strip() for model in available if isinstance(model, str)}
        picked = [model for model in picked if model in visible]
        if not picked:
            raise ChannelError(
                "白名单与上游 /models 无交集：免费档可能已变，先核对 AI_SILICONFLOW_FREE_MODELS"
            )

    models: list[str] = []
    for model in picked:
        if model not in models:
            models.append(model)

    if len(models) > MAX_MODELS:
        print(
            f"注意：{len(models)} 个模型超过单条凭据上限 {MAX_MODELS}，只取前 {MAX_MODELS} 个",
            file=sys.stderr,
        )
        models = models[:MAX_MODELS]

    if not models:
        raise ChannelError("没有可写入的模型：检查白名单与 --models")
    return models


# ---------------------------------------------------------------------------
# 上游 /models
# ---------------------------------------------------------------------------


async def _fetch_model_ids(base_url: str, api_key: str) -> list[str]:
    """``GET {base_url}/models`` → 上游当前可见的模型 id。

    上游报错一律按失败处理，不降级成「白名单全上」：拿不到可见性是运维该知道的
    事实，不是可以静默绕过的障碍。
    """
    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise ChannelError(f"请求 {url} 失败: {type(exc).__name__}") from None

    if response.status_code != 200:
        # 不打响应体：部分上游的错误体会回显请求头里的 key 片段。
        raise ChannelError(f"请求 {url} 返回 HTTP {response.status_code}")

    try:
        payload = response.json()
    except ValueError:
        raise ChannelError(f"{url} 返回的不是 JSON") from None

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise ChannelError(f"{url} 的响应里没有 data 列表")

    models: list[str] = []
    for item in data:
        if isinstance(item, dict):
            model = item.get("id")
        elif isinstance(item, str):
            model = item
        else:
            continue
        if isinstance(model, str) and model.strip():
            models.append(model.strip())
    return models


# ---------------------------------------------------------------------------
# 落库
# ---------------------------------------------------------------------------


async def _upsert(
    *, name: str, base_url: str, models: list[str], api_key: str
) -> tuple[str, dict]:
    """同名 shared 已存在就轮换，否则新建（可以反复跑）。

    解析出的 key 只在本次调用里存在：服务的响应只回 ``key_hint``。
    """
    existing = None
    for view in await credentials.list_entries(ADMIN):
        if view.get("scope") == credentials.SCOPE_SHARED and view.get("name") == name:
            existing = view
            break

    if existing is not None:
        view = await credentials.update_entry(
            oid=str(existing["oid"]),
            actor=ADMIN,
            fields={
                "base_url": base_url,
                "models": models,
                "api_key": api_key,
                "enabled": True,
            },
        )
        return "updated", view

    view = await credentials.create_entry(
        user_oid=ADMIN["id"],
        scope=credentials.SCOPE_SHARED,
        name=name,
        base_url=base_url,
        models=models,
        api_key=api_key,
    )
    return "created", view


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


async def _execute(args: argparse.Namespace) -> int:
    try:
        # 名字规则复用凭据服务的口径：dry-run 通过、真正落库才 400 最浪费时间。
        name = credentials._clean_name(args.name)
    except credentials.CredentialError as exc:
        raise ChannelError(exc.message) from None
    base_url = _clean_base_url(args.base_url)

    whitelist = ai_config.siliconflow_free_models()
    if not whitelist:
        # 网络之前就停：白名单没配时，连「上游有哪些模型」都不该去看。
        raise ChannelError(
            "AI_SILICONFLOW_FREE_MODELS 为空：先把免费模型清单配进 settings（脚本不猜哪些模型免费）"
        )

    explicit = _split_models(args.models)
    api_key = ""
    available: list[str] | None = None
    if args.free_only:
        api_key = resolve_api_key(key_file=args.key_file, key_env=args.key_env)
        available = await _fetch_model_ids(base_url, api_key)

    models = select_models(whitelist, explicit=explicit, available=available)

    if args.dry_run:
        print(
            f"dry-run: shared 渠道 name={name} base_url={base_url} models={len(models)}"
        )
        return 0

    if not api_key:
        api_key = resolve_api_key(key_file=args.key_file, key_env=args.key_env)

    action, view = await _upsert(
        name=name, base_url=base_url, models=models, api_key=api_key
    )
    written = view.get("models") or []
    print(f"{action} shared 渠道 {view.get('name')}")
    print(f"  base_url: {view.get('base_url')}")
    print(f"  key_hint: {view.get('key_hint') or '****'}")
    print(f"  models ({len(written)}): {', '.join(written)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(_execute(args))
    except ChannelError as exc:
        print(f"seed_channel: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
