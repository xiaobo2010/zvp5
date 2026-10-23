"""全盘文件管理的文件系统原语（Stage 2，契约 §3）。

本模块只做「路径 → 文件系统动作」，不碰 FastAPI / 鉴权 / 审计；路由层负责
管理员闸、双人 TOTP 与审计。这样路径安全与文件动作可以脱离 HTTP 单独测试。

安全模型（按契约 §3.2 逐条落地）：

* 输入必须是绝对路径；``os.path.realpath`` 先展开 ``..`` 与符号链接，**再**判定
  虚拟文件系统。顺序不能反：``/proc/self/../self/environ``、``/tmp/link -> /proc``
  这类只有在解析之后才看得出真实落点。
* 全盘放开（``/etc`` / ``/root`` / ``/usr`` / ``/dev`` / ``/var/lib/*`` 等都可读写），
  实际能不能操作由 OS 权限决定：进程是 ``www``，写不动自然得到 403。
* 唯一保留的拒绝面是 ``/proc``、``/sys`` 两个**虚拟文件系统**：``/proc/self/environ``
  之类会泄露后端进程的环境变量（含数据库口令），不属于「用户文件」。按「路径段」
  匹配（``/proc`` 命中 ``/proc`` 与 ``/proc/self/environ``，但不命中 ``/procfs``）。
* 拒绝 NUL / 换行：路径最终会进系统调用与审计日志，控制字符只会带来歧义。
* 所有同步文件 IO 都可能被路由层放进线程池执行（见 ``routers/system_files_router``），
  本模块自身保持同步，方便离线测试。
"""

from __future__ import annotations

import asyncio
import base64
import heapq
import mimetypes
import os
import shutil
import stat as stat_module
import tempfile
from typing import Awaitable, BinaryIO, Callable

# 契约 §3.3（2026-10-19 修订）：系统目录黑名单已放开，只保留两个虚拟文件系统的
# 保护。``/proc/self/environ``、``/proc/self/cmdline`` 会泄露后端进程的环境变量，
# 不属于「用户文件」；``/sys`` 同属内核虚拟视图，一起保留。
# 其余路径（``/etc``、``/root``、``/usr``、``/dev``、``/var/lib/*`` …）由 OS 权限裁决。
VIRTUAL_FS_PREFIXES: tuple[str, ...] = ("/proc", "/sys")

# Application/runtime secrets live on the same disk as user files. Full-disk
# access is deliberate for the admin file manager, but these names are never
# "user files": reading them turns one admin token into the JWT signing key,
# the DB password or the Oracle wallet. Match by real path (symlinks included),
# not just by the raw request string.
PROTECTED_BASENAMES: frozenset[str] = frozenset(
    {
        "aes_key.txt",
        "rsa_private_key.pem",
        "rsa_public_key.pem",
        "settings.py",
        "settings.example.py",
        "ewallet.pem",
        "cwallet.sso",
        "tnsnames.ora",
        "sqlnet.ora",
        "ojdbc.properties",
        "keystore.jks",
        "truststore.jks",
        "id_rsa",
        "id_ed25519",
    }
)
PROTECTED_PATH_SEGMENTS: frozenset[str] = frozenset({".oci", "adb-wallet"})


def _is_protected_secret(path: str) -> bool:
    """True when ``path`` names an application/credential artifact."""
    name = os.path.basename(path).lower()
    if name in PROTECTED_BASENAMES:
        return True
    segments = {part.lower() for part in path.split(os.sep) if part}
    return bool(segments & PROTECTED_PATH_SEGMENTS)

# 下载上限：256MB 是「能一次性交给浏览器且不撑爆 1.8G 小内存机器」的经验上限；
# 更大的文件应该走 SSH 终端里的 rsync/scp。
MAX_DOWNLOAD_BYTES = 256 * 1024 * 1024
# 上传上限：100MB，契约 §3.1。
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
# 单页上限：契约 §3.1，避免一次 scandir 结果全量返回打满前端表格。
MAX_LIST_LIMIT = 2000
# 保留窗口上限：目录再大也只维护「offset + limit」个候选条目（不超过此值）在内存里，
# 用 heapq.nsmallest 边扫边淘汰。10 万条目的目录不会因为排序把整张名单物化进内存。
# 超过该窗口的深分页返回空页；``total`` 仍是精确值（仍会完整枚举一遍目录）。
MAX_LIST_WINDOW = 20000
# 递归删除前的条目数闸门：超过就先拒绝，避免一次误操作拖垮机器。
MAX_RECURSIVE_ENTRIES = 10000

# 预览上限：文本只读首屏，512KB 已覆盖几千行源码/日志，再大就该下载或走 SSH；
# 图片 8MB 是 base64 后约 11MB 的 JSON 响应，超过就会明显拖慢小内存机器与前端渲染。
PREVIEW_TEXT_BYTES = 512 * 1024
PREVIEW_IMAGE_BYTES = 8 * 1024 * 1024
# 二进制嗅探只看前 4KB：够判 NUL 与编码，成本与文件大小无关。
PREVIEW_SNIFF_BYTES = 4096

# 文本白名单（含无扩展名的点文件：``.gitignore`` / ``.env``）按扩展名小写判定。
TEXT_EXTENSIONS: frozenset[str] = frozenset(
    {
        "py", "json", "txt", "md", "markdown", "yml", "yaml", "toml", "ini",
        "cfg", "conf", "env", "sh", "bash", "zsh", "fish", "js", "mjs", "cjs",
        "ts", "tsx", "jsx", "vue", "css", "scss", "sass", "less", "html", "htm",
        "xml", "xsl", "sql", "log", "go", "rs", "java", "kt", "c", "h", "cpp",
        "hpp", "cs", "rb", "php", "lua", "pl", "r", "swift", "scala", "gradle",
        "properties", "service", "timer", "csv", "tsv", "tex", "rst", "bat",
        "ps1", "psm1", "dockerfile", "makefile", "gitignore", "gitattributes",
        "editorconfig",
    }
)

# 图片先于文本判定：``svg`` 两边都在，按图片（``<img>`` 可直接渲染）处理。
IMAGE_MIME_TYPES: dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "ico": "image/x-icon",
    "svg": "image/svg+xml",
}

# ``mimetypes`` 在部分系统上把 .ts 认成 Qt 翻译文件、把 .md/.py 认成 octet-stream，
# 这里固定几个高频文本类型，其余仍交给 mimetypes。
TEXT_MIME_OVERRIDES: dict[str, str] = {
    "py": "text/x-python",
    "md": "text/markdown",
    "markdown": "text/markdown",
    "ts": "text/typescript",
    "tsx": "text/typescript",
    "jsx": "text/javascript",
    "vue": "text/plain",
}

CHUNK_SIZE = 64 * 1024
_UPLOAD_TEMP_PREFIX = ".zvms-upload-"


class FileOpError(Exception):
    """文件动作被拒；路由层翻译成 ``HTTPException(status_code, detail)``。"""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _reject_illegal_input(raw: str) -> None:
    if not isinstance(raw, str) or not raw:
        raise FileOpError(400, "路径不能为空")
    if "\x00" in raw or "\n" in raw or "\r" in raw:
        raise FileOpError(400, "路径包含非法字符")
    if not raw.startswith("/"):
        raise FileOpError(400, "仅接受绝对路径")


def _is_virtual_fs(path: str) -> bool:
    """按路径段判断虚拟文件系统：``/procfs`` 不能被 ``/proc`` 误杀。"""
    for blocked in VIRTUAL_FS_PREFIXES:
        if path == blocked or path.startswith(blocked + "/"):
            return True
    return False


def resolve_path(raw: str) -> str:
    """把请求路径解析成真实绝对路径，并拦截虚拟文件系统。"""
    _reject_illegal_input(raw)

    resolved = os.path.realpath(raw)
    # realpath 对绝对输入必然返回绝对路径；显式断言是为了防止未来换成
    # dirfd / 相对根的实现时悄悄放过一个相对结果。
    if not os.path.isabs(resolved):
        raise FileOpError(400, "仅接受绝对路径")

    # ``..`` 在 realpath 阶段已经展开：从 ``/`` 起算无法逃出根，落到 ``/proc`` /
    # ``/sys`` 的路径在这里被拦下（例如 ``/tmp/../../../proc/self/environ``）。
    if _is_virtual_fs(resolved):
        raise FileOpError(403, "虚拟文件系统不可访问")
    if _is_protected_secret(resolved):
        raise FileOpError(403, "受保护的应用密钥/凭据不可访问")
    return resolved


def resolve_entry_path(raw: str) -> tuple[str, str]:
    """返回 ``(目录项路径, 真实目标)``，用于「对条目本身」的操作。

    只解析父目录、保留最后一段的符号链接语义：删除/重命名/改权限作用于用户
    指名的那个条目，不能因为它是链接就把目标文件一起干掉。父目录仍走 realpath，
    所以 ``/tmp/linkdir/..`` 这类由内核语义决定的落点与校验结果一致。
    """
    _reject_illegal_input(raw)
    stripped = raw.rstrip("/") or "/"
    if stripped == "/":
        return "/", "/"

    name = os.path.basename(stripped)
    if name in {".", ".."}:
        # 末段是 . / .. 时没有「条目」可言，退回纯解析结果。
        resolved = resolve_path(raw)
        return resolved, resolved

    parent = os.path.realpath(os.path.dirname(stripped))
    if not os.path.isabs(parent):
        raise FileOpError(400, "仅接受绝对路径")
    if _is_virtual_fs(parent):
        raise FileOpError(403, "虚拟文件系统不可访问")

    entry = os.path.join(parent, name)
    target = os.path.realpath(entry)
    # 链接目标同样要过虚拟文件系统检查：``/tmp/link -> /proc`` 不允许借链接绕过。
    if _is_virtual_fs(target):
        raise FileOpError(403, "虚拟文件系统不可访问")
    if _is_protected_secret(target) or _is_protected_secret(entry):
        raise FileOpError(403, "受保护的应用密钥/凭据不可访问")
    return entry, target


def _entry_type(info: os.stat_result) -> str:
    if stat_module.S_ISLNK(info.st_mode):
        return "symlink"
    if stat_module.S_ISDIR(info.st_mode):
        return "dir"
    if stat_module.S_ISREG(info.st_mode):
        return "file"
    return "other"


def _describe(name: str, path: str) -> dict:
    """条目的展示字段。一律 lstat：符号链接要显示链接本身，不跟随目标。"""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        raise FileOpError(404, "路径不存在")
    except PermissionError:
        raise FileOpError(403, "权限不足，无法访问该路径")

    item = {
        "name": name,
        "type": _entry_type(info),
        "size": info.st_size,
        "mode": stat_module.S_IMODE(info.st_mode),
        "mtime": int(info.st_mtime),
        "is_symlink": stat_module.S_ISLNK(info.st_mode),
    }
    if item["is_symlink"]:
        try:
            item["target"] = os.readlink(path)
        except OSError:
            item["target"] = None
    return item


def list_dir(path: str, *, offset: int = 0, limit: int = 500) -> dict:
    """目录列表：目录在前、按名排序，不递归统计。

    大目录策略（2026-10-19）：不再把全部条目收进 list 再排序，而是用
    ``heapq.nsmallest`` 只维护 ``offset + limit`` 个最小候选（上限
    :data:`MAX_LIST_WINDOW`）。内存从 O(目录规模) 降到 O(page depth)。
    ``total`` 仍要求精确，所以目录仍会被完整枚举一遍；但从 ``scandir`` 到
    ``total`` 只有一次顺序读，远轻于「全量 list + sort + 逐条 stat」。
    深分页超过窗口时返回空 ``entries``，这是有意的取舍：前端分页是 500/页，
    不会走到 2 万条之后。
    """
    resolved = resolve_path(path)
    if offset < 0:
        raise FileOpError(400, "offset 不能为负")
    if limit < 1:
        raise FileOpError(400, "limit 必须大于 0")
    limit = min(limit, MAX_LIST_LIMIT)
    window = min(offset + limit, MAX_LIST_WINDOW)

    total = 0

    def _keyed_entries():
        nonlocal total
        with os.scandir(resolved) as iterator:
            for entry in iterator:
                total += 1
                # 只取名字与「是否目录」，stat 留到分页之后再打：大目录里
                # 对十万个条目逐个 stat 是纯粹的浪费。
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                except OSError:
                    is_dir = False
                yield (not is_dir, entry.name)

    try:
        retained = heapq.nsmallest(window, _keyed_entries())
    except NotADirectoryError:
        raise FileOpError(400, "目标不是目录")
    except FileNotFoundError:
        raise FileOpError(404, "目录不存在")
    except PermissionError:
        raise FileOpError(403, "权限不足，无法列出该目录")

    page = retained[offset : offset + limit]

    return {
        "path": resolved,
        "offset": offset,
        "limit": limit,
        "total": total,
        "entries": [
            _describe(name, os.path.join(resolved, name)) for _, name in page
        ],
    }


def stat_entry(path: str) -> dict:
    entry, target = resolve_entry_path(path)
    name = "/" if entry == "/" else os.path.basename(entry)
    data = _describe(name, entry)
    data["path"] = entry
    if data["is_symlink"]:
        data["target"] = target
    return data


def open_download(path: str) -> tuple[BinaryIO, int, str]:
    """打开待下载文件；返回 ``(file, size, name)``。"""
    resolved = resolve_path(path)
    try:
        info = os.stat(resolved)
    except FileNotFoundError:
        raise FileOpError(404, "路径不存在")
    except PermissionError:
        raise FileOpError(403, "权限不足，无法读取该路径")

    if stat_module.S_ISDIR(info.st_mode):
        raise FileOpError(400, "目录不支持下载")
    if not stat_module.S_ISREG(info.st_mode):
        raise FileOpError(400, "仅支持下载普通文件")
    if info.st_size > MAX_DOWNLOAD_BYTES:
        raise FileOpError(413, "文件超过 256MB 下载上限，请改用 SSH 终端传输")

    try:
        handle = open(resolved, "rb")
    except PermissionError:
        raise FileOpError(403, "权限不足，无法读取该文件")
    except OSError as exc:
        raise FileOpError(400, f"无法读取文件：{exc.strerror or exc}")

    return handle, info.st_size, os.path.basename(resolved)


def _extension(name: str) -> str:
    """取判定用的扩展名标识；``.env`` / ``Dockerfile`` 这类无后缀名字也能命中。"""
    lowered = name.lower()
    suffix = os.path.splitext(lowered)[1]
    if suffix and suffix != ".":
        return suffix[1:]
    return lowered.lstrip(".") if lowered.startswith(".") else lowered


def _guess_mime(name: str, *, is_text: bool) -> str:
    extension = _extension(name)
    if extension in IMAGE_MIME_TYPES:
        return IMAGE_MIME_TYPES[extension]
    if is_text and extension in TEXT_MIME_OVERRIDES:
        return TEXT_MIME_OVERRIDES[extension]
    guessed = mimetypes.guess_type(name, strict=False)[0]
    if is_text and guessed in (None, "application/octet-stream"):
        return "text/plain"
    return guessed or "application/octet-stream"


def _read_prefix(resolved: str, limit: int) -> bytes:
    try:
        with open(resolved, "rb") as handle:
            return handle.read(limit)
    except PermissionError:
        raise FileOpError(403, "权限不足，无法读取该文件")
    except OSError as exc:
        raise FileOpError(400, f"无法读取文件：{exc.strerror or exc}")


def _decodes(data: bytes, encoding: str) -> bool:
    try:
        data.decode(encoding)
        return True
    except UnicodeDecodeError as exc:
        # 前缀截断会把多字节字符切成两半：尾部残字节不算解码失败。
        if exc.end < len(data) - 3:
            return False
        try:
            data[: exc.start].decode(encoding)
            return True
        except UnicodeDecodeError:
            return False


def _decode_text(data: bytes, *, truncated: bool) -> tuple[str, str]:
    for encoding in ("utf-8", "gbk"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError as exc:
            # 截断读取可能切在多字节字符中间：丢掉这段残字节再当作已解码。
            if truncated and exc.end >= len(data) - 3:
                try:
                    return data[: exc.start].decode(encoding), encoding
                except UnicodeDecodeError:
                    pass
    return data.decode("latin-1"), "latin-1"


def _preview(kind: str, mime: str, size: int, **extra) -> dict:
    payload = {
        "kind": kind,
        "mime": mime,
        "size": size,
        "content": None,
        "encoding": None,
        "truncated": False,
    }
    payload.update(extra)
    return payload


def _text_preview(resolved: str, name: str, size: int) -> dict:
    data = _read_prefix(resolved, PREVIEW_TEXT_BYTES + 1)
    truncated = len(data) > PREVIEW_TEXT_BYTES
    if truncated:
        data = data[:PREVIEW_TEXT_BYTES]
    text, encoding = _decode_text(data, truncated=truncated)
    return _preview(
        "text",
        _guess_mime(name, is_text=True),
        size,
        content=text,
        encoding=encoding,
        truncated=truncated,
    )


def _looks_like_text(head: bytes) -> bool:
    # NUL 是二进制最便宜的判据：UTF-8/GBK 文本里不会出现裸 0x00。
    if b"\x00" in head:
        return False
    return _decodes(head, "utf-8") or _decodes(head, "gbk")


def _preview_regular_file(resolved: str, name: str, size: int) -> dict:
    extension = _extension(name)

    if extension in IMAGE_MIME_TYPES:
        mime = IMAGE_MIME_TYPES[extension]
        if size > PREVIEW_IMAGE_BYTES:
            return _preview("too_large", mime, size)
        data = _read_prefix(resolved, PREVIEW_IMAGE_BYTES)
        return _preview(
            "image", mime, size, content=base64.b64encode(data).decode("ascii")
        )

    if extension in TEXT_EXTENSIONS:
        return _text_preview(resolved, name, size)

    head = _read_prefix(resolved, PREVIEW_SNIFF_BYTES)
    if not _looks_like_text(head):
        return _preview("binary", _guess_mime(name, is_text=False), size)
    return _text_preview(resolved, name, size)


def preview_file(path: str) -> dict:
    """按内容类型返回预览载荷；异常口径与 ``open_download`` 一致。"""
    resolved = resolve_path(path)
    try:
        info = os.stat(resolved)
    except FileNotFoundError:
        raise FileOpError(404, "路径不存在")
    except PermissionError:
        raise FileOpError(403, "权限不足，无法读取该路径")

    if stat_module.S_ISDIR(info.st_mode):
        raise FileOpError(400, "目录不支持预览")
    if not stat_module.S_ISREG(info.st_mode):
        raise FileOpError(400, "仅支持预览普通文件")

    return _preview_regular_file(resolved, os.path.basename(resolved), info.st_size)


def make_dir(path: str) -> dict:
    resolved = resolve_path(path)
    try:
        os.makedirs(resolved, exist_ok=False)
    except FileExistsError:
        raise FileOpError(409, "目标已存在")
    except PermissionError:
        raise FileOpError(403, "权限不足，无法创建目录")
    except OSError as exc:
        raise FileOpError(400, f"创建目录失败：{exc.strerror or exc}")
    return {"path": resolved}


def _safe_upload_name(filename: str | None) -> str:
    raw = (filename or "").replace("\\", "/")
    name = os.path.basename(raw)
    if not name or name in {".", ".."}:
        raise FileOpError(400, "文件名非法")
    if "\x00" in name or "\n" in name or "\r" in name:
        raise FileOpError(400, "文件名包含非法字符")
    return name


async def save_upload(
    directory: str,
    filename: str | None,
    reader: Callable[[int], Awaitable[bytes]],
    *,
    overwrite: bool = False,
) -> dict:
    """流式落盘：先写同目录临时文件，再 ``os.replace`` 原子替换。

    ``os.replace`` 保证任何时刻目标路径要么是旧文件、要么是完整新文件，不会出现
    被进程重启/磁盘满截断的半成品。超限时删掉临时文件并 413。

    落盘的每个同步调用（realpath/isdir/stat/mkstemp/write/chmod/replace）都放进
    线程池：100MB 文件按 64KB 分片，一次 ``write`` 通常只是写页缓存，但事件循环
    仍不该替它等磁盘。
    """
    resolved_dir = await asyncio.to_thread(resolve_path, directory)
    if not await asyncio.to_thread(os.path.isdir, resolved_dir):
        raise FileOpError(404, "目标目录不存在")

    name = _safe_upload_name(filename)
    target = os.path.join(resolved_dir, name)
    if not overwrite and await asyncio.to_thread(os.path.lexists, target):
        raise FileOpError(409, "同名文件已存在")

    # 覆盖时沿用目标文件的权限位：上传替换 web 资源不能把它变成 0600。
    existing_mode = await asyncio.to_thread(_existing_mode, target)

    try:
        fd, temp_path = await asyncio.to_thread(_make_temp_file, resolved_dir)
    except PermissionError:
        raise FileOpError(403, "权限不足，无法写入该目录")
    except OSError as exc:
        raise FileOpError(400, f"无法创建临时文件：{exc.strerror or exc}")

    written = 0
    try:
        handle = os.fdopen(fd, "wb")
        try:
            while True:
                chunk = await reader(CHUNK_SIZE)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise FileOpError(413, "文件超过 100MB 上传上限")
                await asyncio.to_thread(handle.write, chunk)
        finally:
            await asyncio.to_thread(handle.close)
        await asyncio.to_thread(os.chmod, temp_path, existing_mode)
        await asyncio.to_thread(os.replace, temp_path, target)
    except FileOpError:
        await asyncio.to_thread(_discard, temp_path)
        raise
    except PermissionError:
        await asyncio.to_thread(_discard, temp_path)
        raise FileOpError(403, "权限不足，无法写入该目录")
    except OSError as exc:
        await asyncio.to_thread(_discard, temp_path)
        raise FileOpError(400, f"写入失败：{exc.strerror or exc}")
    except BaseException:
        # 客户端断开（ClientDisconnect / CancelledError）也要清掉临时文件。
        await asyncio.to_thread(_discard, temp_path)
        raise

    return {"path": target, "name": name, "size": written, "overwritten": overwrite}


def _existing_mode(target: str) -> int:
    try:
        return stat_module.S_IMODE(os.stat(target).st_mode)
    except OSError:
        return 0o644


def _make_temp_file(directory: str) -> tuple[int, str]:
    return tempfile.mkstemp(dir=directory, prefix=_UPLOAD_TEMP_PREFIX)


def _discard(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def rename_path(source: str, target: str) -> dict:
    source_entry, _ = resolve_entry_path(source)
    target_entry, _ = resolve_entry_path(target)

    if source_entry == "/":
        raise FileOpError(400, "不允许重命名根目录")
    if not os.path.lexists(source_entry):
        raise FileOpError(404, "源路径不存在")
    if os.path.lexists(target_entry):
        raise FileOpError(409, "目标已存在")

    if not os.path.isdir(os.path.dirname(target_entry)):
        raise FileOpError(404, "目标目录不存在")

    try:
        os.rename(source_entry, target_entry)
    except PermissionError:
        raise FileOpError(403, "权限不足，无法重命名")
    except OSError as exc:
        if exc.errno == 18:  # EXDEV：跨文件系统，语义上不是 rename 能做的
            raise FileOpError(400, "不能跨文件系统重命名，请改用复制")
        raise FileOpError(400, f"重命名失败：{exc.strerror or exc}")

    return {"from": source_entry, "to": target_entry}


def _count_entries(root: str) -> int:
    """广度优先统计条目数；超过闸门立刻返回，不做无谓的全盘遍历。"""
    total = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as iterator:
                for entry in iterator:
                    total += 1
                    if total > MAX_RECURSIVE_ENTRIES:
                        return total
                    # 符号链接不跟随：rmtree 同样不跟随，统计口径必须一致。
                    if entry.is_dir(follow_symlinks=False):
                        stack.append(entry.path)
        except OSError:
            # 不可读子树交给后续 rmtree 报错，统计阶段不吞掉整体结果。
            continue
    return total


def delete_path(path: str, *, recursive: bool = False) -> dict:
    entry, _ = resolve_entry_path(path)
    if entry == "/":
        raise FileOpError(400, "不允许删除根目录")
    if not os.path.lexists(entry):
        raise FileOpError(404, "路径不存在")

    # 符号链接只删链接本身，不碰目标。
    if os.path.islink(entry) or not os.path.isdir(entry):
        try:
            os.unlink(entry)
        except PermissionError:
            raise FileOpError(403, "权限不足，无法删除")
        except OSError as exc:
            raise FileOpError(400, f"删除失败：{exc.strerror or exc}")
        return {"path": entry, "deleted": 1, "recursive": False}

    if not recursive:
        try:
            with os.scandir(entry) as iterator:
                next(iterator)
        except StopIteration:
            pass
        except PermissionError:
            raise FileOpError(403, "权限不足，无法删除")
        else:
            raise FileOpError(409, "目录非空，需递归删除")
        try:
            os.rmdir(entry)
        except PermissionError:
            raise FileOpError(403, "权限不足，无法删除")
        except OSError as exc:
            raise FileOpError(400, f"删除失败：{exc.strerror or exc}")
        return {"path": entry, "deleted": 1, "recursive": False}

    count = _count_entries(entry)
    if count > MAX_RECURSIVE_ENTRIES:
        raise FileOpError(400, "目录条目超过 10000，拒绝递归删除")

    try:
        shutil.rmtree(entry)
    except PermissionError:
        raise FileOpError(403, "权限不足，无法删除")
    except OSError as exc:
        raise FileOpError(400, f"删除失败：{exc.strerror or exc}")

    return {"path": entry, "deleted": count, "recursive": True}


def chmod_path(path: str, mode: int) -> dict:
    if isinstance(mode, bool) or not isinstance(mode, int):
        raise FileOpError(400, "权限位必须是整数")
    # 0o777 上限同时排除了 setuid/setgid/sticky（0o7000 以上），不额外放行。
    if mode < 0 or mode > 0o777:
        raise FileOpError(400, "权限位只允许 0o000-0o777（含特殊位的一律拒绝）")

    entry, _ = resolve_entry_path(path)
    if not os.path.lexists(entry):
        raise FileOpError(404, "路径不存在")

    # follow_symlinks=False：改链接自己的权限位，不动目标。
    try:
        os.chmod(entry, mode, follow_symlinks=False)
    except PermissionError:
        raise FileOpError(403, "权限不足，无法修改权限")
    except OSError as exc:
        raise FileOpError(400, f"修改权限失败：{exc.strerror or exc}")

    return {"path": entry, "mode": mode}
