"""Attachment signing, image normalization pipeline and upload quota.

Design: ``docs/FILES_API_DESIGN.md`` §5 (pipeline), §6.2 (HMAC signed URLs),
§8 (security). Nothing here touches the database or the HTTP layer: the router
maps the typed errors raised below onto HTTP status codes.

Only :mod:`Pillow` (already a dependency) is used for the image pipeline; every
Pillow call runs inside ``anyio.to_thread.run_sync`` behind a module-level
semaphore so the single-process event loop is never blocked (design P1-1).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import os
import time
import warnings
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import anyio
from PIL import Image, ImageOps, UnidentifiedImageError

from util import platform_key

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_BYTES = 15 * 1024 * 1024  # 15 MB, application-layer cap (design §5)
CHUNK_SIZE = 1024 * 1024  # 1 MB chunked reads
MAX_SIDE = 10000  # explicit single-side dimension cap (design §5 step 4c)
MAX_REENCODE_SIDE = 2560  # longest side after normalization (design §5 step 6)
THUMB_SIDE = 320  # thumbnail longest side (design §5 step 7)
JPEG_QUALITY = 85

ALLOWED_PURPOSES = {"volunteer_order"}  # v1 whitelist (design §3.1 / P3-1)

# Decompression-bomb hard ceiling: real pixel limit enforced by Pillow.
Image.MAX_IMAGE_PIXELS = 64_000_000

# Pillow is CPU-bound; the deployment is a single uvicorn process, so cap the
# number of concurrent normalization jobs (design §5 / P1-1).
IMAGE_CONCURRENCY_DEFAULT = 4
IMAGE_CONCURRENCY_MIN = 1
IMAGE_CONCURRENCY_MAX = 8


def image_concurrency() -> int:
    """``ZVMS_IMAGE_CONCURRENCY``（1..8），非法或越界回落默认值。"""
    raw = os.environ.get("ZVMS_IMAGE_CONCURRENCY")
    try:
        value = int(str(raw))
    except (TypeError, ValueError):
        return IMAGE_CONCURRENCY_DEFAULT
    if not IMAGE_CONCURRENCY_MIN <= value <= IMAGE_CONCURRENCY_MAX:
        return IMAGE_CONCURRENCY_DEFAULT
    return value


_image_semaphore = asyncio.Semaphore(image_concurrency())

_ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
_MIME_BY_FORMAT = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
_EXT_BY_MIME = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

# ---------------------------------------------------------------------------
# Typed errors (router maps these to 413/415/422/429)
# ---------------------------------------------------------------------------


class AttachmentError(Exception):
    """Base class for attachment pipeline failures."""

    status_code = 400


class PayloadTooLarge(AttachmentError):
    status_code = 413


class UnsupportedMedia(AttachmentError):
    status_code = 415


class InvalidImage(AttachmentError):
    status_code = 422


class QuotaExceeded(AttachmentError):
    status_code = 429


# ---------------------------------------------------------------------------
# HMAC signed URLs (design §6.2)
# ---------------------------------------------------------------------------

_SIGN_PURPOSE_LABEL = b"filesign"
_DEFAULT_TTL = 3600  # default validity: 1 hour (design §6.2)
_MAX_TTL = 86400  # hard cap: 24 hours (design §6.2)

_AES_KEY_PATH = platform_key.resolve_path() or platform_key.legacy_path()


def _load_sign_secret(path: Path) -> bytes:
    """读平台密钥；缺失或为空即启动失败，绝不退化成可预测的密钥。

    旧实现用相对 cwd 的 ``open("aes_key.txt")`` 并吞掉 ``OSError``：进程从别的
    目录启动（systemd ``WorkingDirectory`` 漂移、pytest、cron）时密钥悄悄变成
    ``b""``，于是 ``_SIGN_KEY = sha256(b"filesign")`` 人人可算，附件签名 URL 能被
    任意伪造。密钥与 ``rsa_*.pem`` 同层放在后端根，路径按 ``__file__`` 锚定，
    与 cwd 无关。空文件与缺失等价——空串同样推导出可预测的签名键。
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            f"附件签名密钥不可用：{path}（缺失或不可读，拒绝以空密钥启动）"
        ) from exc
    secret = raw.encode("utf-8")
    if not secret.strip():
        raise RuntimeError(f"附件签名密钥为空：{path}（拒绝以空密钥启动）")
    return secret


_aes_key_bytes = _load_sign_secret(_AES_KEY_PATH)

# Purpose-derived key, isolated from the bare JWT HS256 key (design §6.2).
_SIGN_KEY = hmac.new(_aes_key_bytes, _SIGN_PURPOSE_LABEL, hashlib.sha256).digest()


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _compute_sig(oid: str, exp: int, purpose: str) -> str:
    message = f"{oid}|{exp}|{purpose}".encode("utf-8")
    return _b64url_encode(hmac.new(_SIGN_KEY, message, hashlib.sha256).digest())


def make_signed_url(oid: str, ttl: int | None = None) -> tuple[str, int]:
    """Return ``(path_with_query, expires_at)`` for ``oid``.

    ``ttl`` is clamped to ``[60, _MAX_TTL]`` (default ≤ 1h, hard cap 24h).
    The returned ``exp``/``purpose`` are the exact values covered by the
    signature, so tampering fails verification.
    """
    ttl = _DEFAULT_TTL if ttl is None else int(ttl)
    ttl = max(60, min(ttl, _MAX_TTL))
    exp = int(time.time()) + ttl
    purpose = "attachment"
    sig = _compute_sig(oid, exp, purpose)
    url = f"/api/attachments/{oid}?exp={exp}&purpose={purpose}&sig={sig}"
    return url, exp


def verify_signed(oid: str, exp: object, purpose: object, sig: object) -> bool:
    """Verify a signed link. Order: signature, then ``exp`` against the clock.

    Returns ``False`` for any malformed/absent value so the router can answer a
    uniform 403 regardless of whether ``oid`` exists (design §6.2 / P1-3).
    """
    if not isinstance(sig, str) or not isinstance(purpose, str):
        return False
    try:
        exp_int = int(str(exp))
    except (TypeError, ValueError):
        return False
    if purpose != "attachment":
        return False
    expected = _compute_sig(oid, exp_int, purpose)
    if not hmac.compare_digest(expected, sig):
        return False
    if exp_int < int(time.time()):
        return False
    return True


# ---------------------------------------------------------------------------
# Filenames
# ---------------------------------------------------------------------------


def sanitize_filename(name: str | None) -> str:
    """Truncate to 255 chars and strip control characters (design P3-1)."""
    if not name:
        return ""
    cleaned = "".join(ch for ch in name if ord(ch) >= 32 and ord(ch) != 127)
    return cleaned[:255]


def ext_for_mime(mime: str | None) -> str:
    return _EXT_BY_MIME.get(mime or "", "bin")


# ---------------------------------------------------------------------------
# Chunked upload read
# ---------------------------------------------------------------------------


async def read_capped(upload, max_bytes: int = MAX_BYTES) -> bytes:
    """Read ``upload`` in 1 MB chunks, aborting with 413 past ``max_bytes``.

    ``upload`` is a Starlette ``UploadFile`` (``await upload.read(n)``).
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise PayloadTooLarge("File exceeds the maximum allowed size")
        chunks.append(chunk)
    return b"".join(chunks)


# ---------------------------------------------------------------------------
# Image normalization (design §5)
# ---------------------------------------------------------------------------


@dataclass
class ProcessedImage:
    content: bytes
    mime: str
    width: int
    height: int
    size: int
    sha256: str
    thumb: bytes
    thumb_mime: str
    thumb_width: int
    thumb_height: int
    thumb_size: int
    thumb_sha256: str


def _resize_longest_side(image: Image.Image, longest: int) -> Image.Image:
    if max(image.width, image.height) <= longest:
        return image
    copy = image.copy()
    copy.thumbnail((longest, longest), Image.LANCZOS)
    return copy


def _jpeg_bytes(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buffer.getvalue()


def _process_image_sync(raw: bytes) -> ProcessedImage:
    """Blocking Pillow work; always invoked through a worker thread."""
    if not raw:
        raise InvalidImage("Empty file")

    with warnings.catch_warnings():
        # Upgrade the decompression-bomb warning to a hard error (design §5 4b).
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            with Image.open(io.BytesIO(raw)) as probe:
                image_format = probe.format
                probe.verify()
        except Image.DecompressionBombError:
            raise PayloadTooLarge("Image dimensions exceed the safe limit")
        except Image.DecompressionBombWarning:
            raise PayloadTooLarge("Image dimensions exceed the safe limit")
        except (UnidentifiedImageError, OSError, ValueError):
            raise InvalidImage("File is not a readable image")

        if image_format not in _ALLOWED_FORMATS:
            raise UnsupportedMedia("Unsupported image format")

        # Reopen after verify(): verify() leaves the file unusable (design §5).
        try:
            with Image.open(io.BytesIO(raw)) as reopened:
                if reopened.width > MAX_SIDE or reopened.height > MAX_SIDE:
                    raise PayloadTooLarge("Image dimensions exceed the safe limit")
                oriented = ImageOps.exif_transpose(reopened)
                rgb = oriented.convert("RGB")

                normalized = _resize_longest_side(rgb, MAX_REENCODE_SIDE)
                content = _jpeg_bytes(normalized)
                width, height = normalized.size

                thumb = _resize_longest_side(rgb, THUMB_SIDE)
                thumb_bytes = _jpeg_bytes(thumb)
                thumb_width, thumb_height = thumb.size
        except Image.DecompressionBombError:
            raise PayloadTooLarge("Image dimensions exceed the safe limit")

    return ProcessedImage(
        content=content,
        mime="image/jpeg",
        width=width,
        height=height,
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        thumb=thumb_bytes,
        thumb_mime="image/jpeg",
        thumb_width=thumb_width,
        thumb_height=thumb_height,
        thumb_size=len(thumb_bytes),
        thumb_sha256=hashlib.sha256(thumb_bytes).hexdigest(),
    )


async def process_upload(raw: bytes) -> ProcessedImage:
    """Run the normalization pipeline off-loop behind the concurrency cap."""
    async with _image_semaphore:
        return await anyio.to_thread.run_sync(_process_image_sync, raw)


# ---------------------------------------------------------------------------
# Per-user daily quota (v1)
# ---------------------------------------------------------------------------
#
# LIMITATION: this is a best-effort in-process counter. It resets on restart and
# is not shared across processes — acceptable for the current single-uvicorn
# deployment (design §1 C11), but must move to the DB if the app ever scales to
# multiple workers. It is a coarse abuse guard, not a correctness boundary.

DAILY_MAX_FILES = 50
DAILY_MAX_BYTES = 100 * 1024 * 1024  # 100 MB / user / day

_quota: dict[tuple[str, str], list[int]] = {}
_quota_day: str | None = None


def _today() -> str:
    return date.today().isoformat()


def _prune_quota(today: str) -> None:
    """按日切换时丢掉过期 key：只保留当天与前 1 天。"""
    global _quota_day
    if _quota_day == today:
        return
    keep = {today, (date.fromisoformat(today) - timedelta(days=1)).isoformat()}
    for key in [key for key in _quota if key[0] not in keep]:
        del _quota[key]
    _quota_day = today


def effective_daily_limits(user_oid: str | None = None) -> tuple[int, int]:
    """该用户当日的 ``(文件数上限, 字节上限)``。

    优先级：``USER_QUOTA_OVERRIDES``（按用户覆盖）→ ``ops.upload_*``（在线设置
    的全局默认）→ 本模块的硬编码常量。覆盖值是**按用户每日**语义，不是「一次性
    额外额度」。

    读的是 :mod:`services.settings_runtime` 的进程内快照（5 秒刷新），所以本函数
    保持同步、不改调用方签名；管理员在设置中心改完，同进程立即生效。
    ``settings_runtime`` 在函数内导入：它反过来要读本模块的 ``DAILY_MAX_*`` 作为
    注册表默认值，模块级互相导入会成环。
    """
    from services import settings_runtime

    files, total_bytes = settings_runtime.effective_daily_limits(user_oid)
    # 注册表默认值就是下面两个常量；这里再兜一次「库/配置给出非法值」的情况。
    files = files if isinstance(files, int) and files >= 0 else DAILY_MAX_FILES
    total_bytes = (
        total_bytes
        if isinstance(total_bytes, int) and total_bytes >= 0
        else DAILY_MAX_BYTES
    )
    return files, total_bytes


def check_quota(user_oid: str) -> None:
    today = _today()
    _prune_quota(today)
    count, used = _quota.get((today, user_oid), [0, 0])
    files_limit, bytes_limit = effective_daily_limits(user_oid)
    if count >= files_limit or used >= bytes_limit:
        raise QuotaExceeded("Daily upload quota exceeded")


def record_upload(user_oid: str, size: int) -> None:
    today = _today()
    _prune_quota(today)
    entry = _quota.setdefault((today, user_oid), [0, 0])
    entry[0] += 1
    entry[1] += size


# ---------------------------------------------------------------------------
# Read permission (design §4)
# ---------------------------------------------------------------------------


async def can_read_attachment(user: dict, row: dict, class_id: str | None) -> bool:
    """owner | admin | department | secretary in the same class.

    从 ``routers/attachments_router.py`` 抽出来，因为 AI 网关的 OCR 入口也要用同一
    条判定：附件读权限只能有一份实现，两处各写一遍迟早漂移。

    ``class_id`` 是当前登录用户自己的班级（由 ``util.object_id._authenticate``
    带出），这里只读 owner 的那一行。``backends`` 在函数内导入：``db`` 侧会反向
    依赖 util，模块级导入容易成环。
    """
    from db import backends

    owner_oid = str(row.get("owner_oid") or "")
    if owner_oid and owner_oid == str(user.get("id") or ""):
        return True

    per = user.get("per") or []
    if "admin" in per or "department" in per:
        return True

    if "secretary" in per and owner_oid and class_id is not None:
        owner = await backends.get_users_repo().get_by_oid(owner_oid)
        if (
            owner is not None
            and owner.get("class_id") is not None
            and str(class_id) == str(owner.get("class_id"))
        ):
            return True
    return False
