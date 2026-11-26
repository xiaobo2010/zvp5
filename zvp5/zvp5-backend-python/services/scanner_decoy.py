"""未登录目录扫描诱饵。

扫描器（dirsearch / gobuster / ffuf 等）会用字典挨个请求不存在的路径。这个
模块只做三件事：

1. 把请求路径归一化（百分号解码、折叠重复斜杠、去掉末尾斜杠、转小写），
   再和 ``scanner_paths.txt`` 里的高信号路径比对；
2. 只对**未登录的 GET/HEAD** 生效，且只作用于 canary 根站（``/themes/``
   与带 canary-pro Referer 的请求直接放过）；
3. 命中后返回 200 + 管理端配置的纯文本正文。

为什么放在 ``shield_middleware`` 最外层：扫描器的 UA 通常不含 ``Mozilla``，
如果先走盾牌就会得到 418，看不到诱饵。先判断诱饵，再执行原有的 418 逻辑。

这里刻意用 ``text/plain`` 而不是 ``text/html``：正文来自在线设置中心，管理端
可以随便编辑；按纯文本返回，即使正文里带 HTML 也只会被浏览器显示成文字，
不会变成一个可注入的 XSS 面。
"""

from __future__ import annotations

import logging
import posixpath
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

from fastapi import Request
from fastapi.responses import PlainTextResponse

from services import settings_runtime
from util.cert import jwt_decode

logger = logging.getLogger(__name__)

_DICT_PATH = Path(__file__).with_name("scanner_paths.txt")

#: 单条路径的最大长度；再长的直接不匹配（正常扫描字典不会这么长）。
_MAX_PATH_LEN = 512
#: 正文上限与 ``settings_runtime`` 的校验保持一致，防止异常值撑爆响应。
_MAX_BODY_CHARS = 16_384

#: 这些路径是公开入口/静态资源，不能因为字典里有同名条目就被诱饵吃掉。
_ALLOW_EXACT = frozenset(
    {
        "/",
        "/index.html",
        "/favicon.ico",
        "/robots.txt",
        "/manifest.webmanifest",
        "/site.webmanifest",
        "/sw.js",
        "/themes.json",
        "/theme-switcher.js",
        "/theme-switcher.css",
        "/assets",
        "/api",
        "/api/",
        "/api/health",
        "/api/version",
        "/api/cert",
        "/api/changelog",
    }
)

_ALLOW_PREFIXES = (
    "/assets/",
    "/themes/",
    "/socket.io/",
    "/.well-known/acme-challenge/",
    "/api/v1/users/auth",
    "/api/v1/themes/server-time",
    # OpenAI SDK 的兼容面保持原行为：未带 token 时回 401，而不是诱饵。
    "/api/ai/v1/",
)

#: ``/api/<x>`` 只有在 ``<x>`` 本身是高信号扫描目标时才去掉 ``/api`` 再比对。
#: 否则 ``/api/logs``、``/api/users``、``/api/exports`` 这些正常接口会被
#: 字典里的通用目录名误伤。
_API_STRIP_HINTS = (
    "/.",
    "/admin",
    "/administrator",
    "/wp-",
    "/phpmyadmin",
    "/pma",
    "/adminer",
    "/actuator",
    "/server-status",
    "/server-info",
    "/cgi-bin",
    "/web-inf",
    "/meta-inf",
    "/backup",
    "/swagger",
    "/v2/api-docs",
    "/v3/api-docs",
    "/openapi",
    "/graphql",
)
_API_STRIP_EXTENSIONS = (
    ".php",
    ".sql",
    ".env",
    ".bak",
    ".old",
    ".zip",
    ".tar",
    ".gz",
    ".tgz",
    ".rar",
    ".7z",
    ".json",
    ".yaml",
    ".yml",
    ".xml",
    ".txt",
    ".log",
    ".ini",
    ".conf",
    ".config",
)

#: 高置信扫描目录：字典里通常只有目录名，扫描器会继续请求目录下的文件。
#: 只做「等于或作为前缀」匹配，不做子串匹配。
_PREFIX_PATHS = frozenset(
    {
        "/.git",
        "/.svn",
        "/.hg",
        "/.bzr",
        "/.aws",
        "/.ssh",
        "/.docker",
        "/.kube",
        "/.terraform",
        "/wp-admin",
        "/wp-content",
        "/wp-includes",
        "/phpmyadmin",
        "/pma",
        "/myadmin",
        "/adminer",
        "/actuator",
        "/server-status",
        "/server-info",
        "/cgi-bin",
        "/web-inf",
        "/meta-inf",
        "/backup",
        "/backups",
        "/database",
        "/db",
        "/sql",
        "/logs",
        "/vendor",
        "/node_modules",
        "/bower_components",
    }
)


def normalize_path(raw: object) -> str:
    """把任意请求路径归一化成字典里的形态；非法输入返回空串。"""
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if not text:
        return "/"
    # 只取 path 部分：查询串与片段不参与匹配。
    text = text.split("?", 1)[0].split("#", 1)[0]
    text = unquote(text)
    if len(text) > _MAX_PATH_LEN:
        return ""
    if not text.startswith("/"):
        text = "/" + text
    text = re.sub(r"/+", "/", text)
    text = posixpath.normpath(text)
    if not text.startswith("/"):
        text = "/" + text
    if len(text) > 1:
        text = text.rstrip("/")
    return text.lower()


def load_paths(path: Path | None = None) -> frozenset[str]:
    """读取字典文件；读不到就返回空集（功能自动失效，不影响启动）。"""
    target = path or _DICT_PATH
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError:
        logger.warning("scanner decoy dictionary missing: %s", target)
        return frozenset()

    out: set[str] = set()
    for line in lines:
        entry = line.strip()
        if not entry or entry.startswith("#"):
            continue
        normalized = normalize_path(entry)
        if normalized and normalized != "/":
            out.add(normalized)
    return frozenset(out)


PATHS: frozenset[str] = load_paths()


def _setting_bool(key: str, default: bool) -> bool:
    try:
        return bool(settings_runtime.get_sync(key))
    except Exception:  # noqa: BLE001 - 设置层不可用时不能把请求打成 500
        logger.warning("scanner decoy setting %s unavailable; using default", key)
        return default


def _setting_str(key: str, default: str) -> str:
    try:
        value = settings_runtime.get_sync(key)
    except Exception:  # noqa: BLE001 - 同上
        logger.warning("scanner decoy setting %s unavailable; using default", key)
        return default
    if not isinstance(value, str):
        return default
    return value[:_MAX_BODY_CHARS]


def _enabled() -> bool:
    return _setting_bool("security.scanner_decoy_enabled", True)


def _body() -> str:
    return _setting_str("security.scanner_decoy_body", "Not Found")


def is_allowlisted(path: str) -> bool:
    """公开入口与静态资源不参与诱饵判定。"""
    normalized = normalize_path(path)
    if not normalized:
        return True
    if normalized in _ALLOW_EXACT:
        return True
    return any(normalized.startswith(prefix) for prefix in _ALLOW_PREFIXES)


def is_scanner_path(path: str) -> bool:
    """路径是否命中字典（``/api`` 前缀会被去掉再比对一次）。"""
    normalized = normalize_path(path)
    if not normalized or is_allowlisted(normalized):
        return False

    candidates = [normalized]
    if normalized.startswith("/api/"):
        rest = normalized[4:]
        if rest.startswith(_API_STRIP_HINTS) or rest.endswith(_API_STRIP_EXTENSIONS):
            candidates.append(rest)

    for candidate in candidates:
        if candidate in PATHS:
            return True
        for prefix in _PREFIX_PATHS:
            if candidate == prefix or candidate.startswith(prefix + "/"):
                return True
    return False


def is_authenticated(request: Request) -> bool:
    """是否携带可验证的 access/temporary token。

    只验签与声明，不查库：这里只需要区分「匿名」和「登录用户」，角色与封禁
    由真正的路由鉴权负责。任何解析失败都按匿名处理。
    """
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return False
    try:
        payload = jwt_decode(token.strip())
    except Exception:  # noqa: BLE001 - 伪造/过期 token 一律当作匿名
        return False
    return payload.get("scope") in ("access_token", "temporary_token")


def is_canary_request(request: Request) -> bool:
    """只对 canary 根站生效；Pro 的 ``/themes/<id>/`` 页面与 API 调用放过。"""
    path = request.url.path
    if path.startswith("/themes/"):
        return False
    referer = request.headers.get("referer")
    if referer:
        try:
            referer_path = urlsplit(referer).path
        except ValueError:
            referer_path = ""
        if referer_path.startswith("/themes/"):
            return False
    return True


def should_decoy(request: Request) -> bool:
    """命中即返回诱饵；所有条件都满足才生效。"""
    if request.method.upper() not in {"GET", "HEAD"}:
        return False
    if not _enabled():
        return False
    if not is_canary_request(request):
        return False
    # 先做便宜的字典比对，再验 token：正常请求不应为每次鉴权多付一次 HMAC。
    if not is_scanner_path(request.url.path):
        return False
    return not is_authenticated(request)


def decoy_response() -> PlainTextResponse:
    """200 + 管理端配置的纯文本正文。"""
    return PlainTextResponse(
        _body(),
        status_code=200,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Robots-Tag": "noindex, nofollow",
        },
    )


__all__ = [
    "PATHS",
    "decoy_response",
    "is_allowlisted",
    "is_authenticated",
    "is_canary_request",
    "is_scanner_path",
    "load_paths",
    "normalize_path",
    "should_decoy",
]
