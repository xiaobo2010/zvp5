"""OCR 入队：选引擎、扣两份额度、把活交给后台任务。

只接受**已通过 attachments API 落库**的图片：网关不提供直传通道，否则 OCR 会变成
一条绕过附件配额与内容审核的旁路。真正的识别由 ``util/attachment_ocr.process_ocr``
在后台任务里跑，引擎按 ``ai_ocr_engines`` 注册表的 ``priority`` 选。

步骤顺序不能变：先鉴权再扣配额——先扣后拒等于白扣用户额度。两份额度也分先后：

* **用户额度**（``ocr``）先 ``peek`` 后 ``consume``：预检不花代价，真扣留在
  「引擎已选中」之后，避免「没有可用引擎」也把用户额度扣掉；
* **站点额度**（``ocr_<engine>``）在选引擎时按 ``priority`` 逐个尝试，第一个
  ``allowed`` 的胜出——被拒的那次不扣（仓储是原子条件自增），所以轮换无副作用。

站点额度先扣、用户额度后扣，中间存在的竞态由 :func:`quota.site_refund` 补偿：
用户额度竞态失败时把刚占的站点额度退回去，不留「白扣一次平台成本」的尾巴。

引擎选择本身开放给后台任务复用（:func:`select_engine`）：识别失败要能按同一套
``priority`` 顺序换下一个候选，优先级逻辑只允许有一份实现。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from uuid import uuid4

from fastapi import HTTPException

from db import backends
from services.ai.routing import RouteError
from util.attachment import can_read_attachment

from . import audit, config as ai_config, crypto, ocr_engines, quota, rate_limit

_logger = logging.getLogger(__name__)

ALLOWED_MIME = frozenset({"image/jpeg", "image/png", "image/webp"})

# 一次识别最多尝试几个引擎：入队选中的那个 + 最多两次回退。三这个数字是
# 「覆盖最常见的单点故障（某家 key 失效/额度打满）」与「失败请求的耗时/成本」
# 的折中——每多试一个就多扣一次站点额度、多等一个上游超时。
MAX_ENGINE_ATTEMPTS = 3

# 允许指定 ``provider`` 的默认角色集合。入口不同权限就不同：``/api/ai/ocr`` 只给
# admin，义工登记页（已过密码复核）把「可登记义工的管理者」也放进来——由调用方
# 显式传入自己的集合，而不是在这里写死。
ADMIN_PROVIDER_ROLES = frozenset({"admin"})

_NO_ENGINE_MESSAGE = "no OCR engine is configured"
_NO_SITE_QUOTA_MESSAGE = "every configured OCR engine is out of site quota"

#: 引擎全部失败时的错误码（与后台任务写进审计的是同一个字面量）。
ERROR_ENGINE = "AI_OCR_ENGINE_ERROR"

#: 文件路径入口的审计端点名（与挂载前缀一致，便于和附件入口分开统计）。
FILE_ENDPOINT = audit.ENDPOINT_OCR + "/file"


async def enqueue(
    user: dict,
    attachment_oid: str,
    *,
    provider: str | None = None,
    provider_roles: frozenset[str] = ADMIN_PROVIDER_ROLES,
) -> dict:
    """把附件挂进 OCR 队列；``attachment_oid`` 已由路由校验为合法 oid。

    ``provider`` 是**引擎 kind**（``tencent`` / ``baidu`` / ...），不是模型名：
    有值时只在该 kind 的引擎里选，但**照常扣站点额度**——指定引擎是定向排障的
    手段，不是免费通道。默认仅 admin 可用，其他人传了直接 403；``provider_roles``
    让入口自报「哪些角色可以指定」（义工登记页的管理者集合与网关不同）。

    返回值里除了对外的 ``status`` / ``attachment_oid`` / ``engine``，还带一个
    ``engine_kind``：路由层要用它把**同一个**引擎传给后台任务。对外响应会把
    这个内部字段摘掉（见 ``routers/ai_router.request_ocr``）。
    """
    # 门禁放在最前面：没资格的人拿 provider 试探，不该先泄露附件是否存在。
    requested = (provider or "").strip().lower() or None
    if requested is not None and not (
        provider_roles & set(user.get("per") or [])
    ):
        raise RouteError("AI_FORBIDDEN", "provider is not allowed for your role", 403)
    engine_kind_filter = _requested_kind(requested)

    repo = backends.get_files_repo()
    row = await repo.get_meta(attachment_oid)
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    if not await can_read_attachment(user, row, user.get("class_id")):
        raise HTTPException(status_code=403, detail="Permission denied")

    mime = str(row.get("mime") or "").lower()
    if mime not in ALLOWED_MIME:
        raise HTTPException(status_code=400, detail="AI_UNSUPPORTED_MEDIA")

    await _reject_oversized(repo, attachment_oid, row)

    # 已经在跑的不重复扣配额，也不重复入队。
    if row.get("ocr_status") == "processing":
        return {"status": "processing", "attachment_oid": attachment_oid}

    # 先频控再扣额度：分钟窗口挡突发，月额度挡长期滥用。顺序反了的话，被频控
    # 拒掉的请求已经扣过额度，用户会看到「没成功但额度少了」。
    try:
        rate_limit.check(user["id"], "ocr")
    except rate_limit.RateLimitError as exc:
        await _audit_reject(
            user["id"], attachment_oid, status_code=429, code="AI_RATE_LIMITED"
        )
        raise HTTPException(
            status_code=429,
            detail="AI_RATE_LIMITED",
            headers={"Retry-After": str(exc.retry_after)},
        ) from None

    # 预检用户额度：不足就直接拒，别先去占一份站点额度。
    allowed, used, limit = await quota.peek(
        user["id"], "ocr", 1, roles=user.get("per")
    )
    if not allowed:
        await _audit_reject(
            user["id"],
            attachment_oid,
            status_code=429,
            code="AI_QUOTA_EXCEEDED",
            input_bytes=row.get("size_bytes"),
        )
        raise _quota_exceeded(used, limit)

    try:
        engine = await select_engine(engine_kind=engine_kind_filter)
    except RouteError as exc:
        # 没有可用引擎 / 站点额度打满：也是「一次没成的调用」，落 rejected
        # （我们自己的闸门，不是上游故障——那才是 busy/error）。
        await _audit_reject(
            user["id"],
            attachment_oid,
            status_code=exc.status_code,
            code=exc.code,
            input_bytes=row.get("size_bytes"),
        )
        raise

    allowed, used, limit = await quota.consume(
        user["id"], "ocr", 1, roles=user.get("per")
    )
    if not allowed:
        # 预检与真扣之间的竞态：另一个并发请求刚把额度用完。站点额度已经占了
        # 一次，退回去——那一次识别根本不会发生，平台不该为它付上游成本。
        await _refund_site(engine["kind"])
        await _audit_reject(
            user["id"],
            attachment_oid,
            status_code=429,
            code="AI_QUOTA_EXCEEDED",
            input_bytes=row.get("size_bytes"),
        )
        raise _quota_exceeded(used, limit)

    await repo.set_ocr(attachment_oid, "pending")
    await repo.add_event(
        attachment_oid,
        user["id"],
        "ocr_enqueue",
        row.get("ocr_status"),
        "pending",
        "",
    )

    await audit.record(
        user_oid=user["id"],
        capability="ocr",
        endpoint=audit.ENDPOINT_OCR,
        provider=engine["name"],
        model=engine["name"],
        status="success",
        units=1,
        input_bytes=row.get("size_bytes"),
        # 引擎凭证是站点级的（``ai_ocr_engines`` 全站生效），所以这里恒记 server：
        # 按 key_scope 对账的报表要靠它把这笔成本算到平台头上。
        key_scope="server",
        # 入队这一行是整条链的起点：purpose 固定 ocr，engine_kind 让人能按引擎
        # 类型聚合，attachment_oid 让「这张图」可追。request_id 由后台任务生成并
        # 覆盖它自己那几次尝试，入队这一行没有（它不在那条链上）。
        purpose="ocr",
        engine_kind=engine["kind"],
        attachment_oid=attachment_oid,
        attempt_no=1,
    )

    return {
        "status": "pending",
        "attachment_oid": attachment_oid,
        "engine": engine["name"],
        "engine_kind": engine["kind"],
    }


async def _audit_reject(
    user_oid: str,
    attachment_oid: str | None,
    *,
    status_code: int,
    code: str,
    input_bytes: int | None = None,
) -> None:
    """OCR 拒绝路径的审计行（额度 / 频控 / 无引擎）。

    ``units=0`` 与 ``status='rejected'``：额度一分没扣、上游一次没打。``attempt_no``
    留 NULL——这一行不是「第几次尝试」，而是「这次请求在第一步就被挡了」。
    """
    await audit.record(
        user_oid=user_oid,
        capability="ocr",
        endpoint=audit.ENDPOINT_OCR,
        status="rejected",
        http_status=status_code,
        error_code=code,
        units=0,
        input_bytes=input_bytes,
        purpose="ocr",
        attachment_oid=attachment_oid,
    )


async def recognize_bytes(
    user: dict,
    image: bytes,
    *,
    mime: str,
    provider: str | None = None,
    provider_roles: frozenset[str] = ADMIN_PROVIDER_ROLES,
) -> dict:
    """对**内存里的图片字节**做一次 OCR，返回 ``{"text", "engine", "engine_kind"}``。

    附件入口（:func:`enqueue`）把活交给后台任务并按附件的状态机推进；文件预览里的
    图片没有附件 oid，走这条同步入口。两边的**记账口径完全相同**（频控 → 用户额度
    peek → 站点额度选引擎 → 用户额度 consume → 每次尝试一条审计），差别只在
    「字节从哪来、结果往哪去」。

    同步返回而不是排队：一次识别本来就是一个上游请求，调用方（文件预览的 admin）
    就是在等这段文本；排成任务只会多一次「再查一次」的往返。
    """
    requested = (provider or "").strip().lower() or None
    if requested is not None and not (provider_roles & set(user.get("per") or [])):
        raise RouteError("AI_FORBIDDEN", "provider is not allowed for your role", 403)
    engine_kind_filter = _requested_kind(requested)

    normalized = str(mime or "").lower()
    if normalized not in ALLOWED_MIME:
        raise HTTPException(status_code=400, detail="AI_UNSUPPORTED_MEDIA")
    if len(image) > ai_config.max_ocr_image_bytes():
        raise HTTPException(status_code=400, detail="AI_OCR_IMAGE_TOO_LARGE")

    try:
        rate_limit.check(user["id"], "ocr")
    except rate_limit.RateLimitError as exc:
        await _audit_reject(user["id"], None, status_code=429, code="AI_RATE_LIMITED")
        raise HTTPException(
            status_code=429,
            detail="AI_RATE_LIMITED",
            headers={"Retry-After": str(exc.retry_after)},
        ) from None

    allowed, used, limit = await quota.peek(
        user["id"], "ocr", 1, roles=user.get("per")
    )
    if not allowed:
        await _audit_reject(
            user["id"],
            None,
            status_code=429,
            code="AI_QUOTA_EXCEEDED",
            input_bytes=len(image),
        )
        raise _quota_exceeded(used, limit)

    try:
        engine = await select_engine(engine_kind=engine_kind_filter)
    except RouteError as exc:
        await _audit_reject(
            user["id"],
            None,
            status_code=exc.status_code,
            code=exc.code,
            input_bytes=len(image),
        )
        raise

    allowed, used, limit = await quota.consume(
        user["id"], "ocr", 1, roles=user.get("per")
    )
    if not allowed:
        await _refund_site(engine["kind"])
        await _audit_reject(
            user["id"],
            None,
            status_code=429,
            code="AI_QUOTA_EXCEEDED",
            input_bytes=len(image),
        )
        raise _quota_exceeded(used, limit)

    await audit.record(
        user_oid=user["id"],
        capability="ocr",
        endpoint=FILE_ENDPOINT,
        provider=engine["name"],
        model=engine["name"],
        status="success",
        units=1,
        input_bytes=len(image),
        key_scope="server",
        purpose="ocr",
        engine_kind=engine["kind"],
        attempt_no=1,
    )
    return await _recognize_bytes(
        user, image, normalized, engine, request_id=uuid4().hex
    )


async def _recognize_bytes(
    user: dict,
    image: bytes,
    mime: str,
    engine: dict,
    *,
    request_id: str,
) -> dict:
    """按 ``priority`` 跑候选引擎；成功的那个返回文本，全失败才抛。

    回退候选由 :func:`select_engine` 给出（同一顺序、同样扣那一份站点额度）——
    与后台任务用的是同一份逻辑，只是这里不写附件状态机。
    """
    tried: set[str] = set()
    last_error: BaseException | None = None
    row = engine["row"]

    for attempt in range(MAX_ENGINE_ATTEMPTS):
        if attempt > 0:
            try:
                row = (await select_engine(exclude=tried))["row"]
            except RouteError as exc:
                last_error = last_error or exc
                break
        kind = str(row.get("kind") or engine["kind"])
        label = _display_name(row, kind)
        tried.add(label)
        started = time.monotonic()
        try:
            adapter = ocr_engines.ENGINES[kind]()
            result = await adapter.recognize(
                image, mime, _engine_secret(row), row.get("config") or {}
            )
        except Exception as exc:  # noqa: BLE001 - 换下一个引擎，失败写审计
            last_error = exc
            _logger.warning(
                "ocr file attempt %s failed on %s: %s",
                attempt + 1,
                label,
                type(exc).__name__,
            )
            await _audit_attempt(
                user["id"],
                label,
                status="error",
                error_code=ERROR_ENGINE,
                latency_ms=_elapsed_ms(started),
                engine_kind=kind,
                request_id=request_id,
                attempt_no=attempt + 1,
                input_bytes=len(image),
            )
            continue

        await _audit_attempt(
            user["id"],
            label,
            status="success",
            latency_ms=_elapsed_ms(started),
            engine_kind=kind,
            request_id=request_id,
            attempt_no=attempt + 1,
            input_bytes=len(image),
        )
        return {
            "text": str(result.get("text") or ""),
            "engine": label,
            "engine_kind": kind,
        }

    _logger.warning(
        "ocr file failed for all engines: %s",
        type(last_error).__name__ if last_error else "RouteError",
    )
    raise RouteError(ERROR_ENGINE, "no OCR engine could handle the image", 502)


def _engine_secret(row: dict) -> dict:
    """``secret_cipher`` → 凭证 dict；``None`` = 本地引擎，不需要凭证。"""
    cipher = row.get("secret_cipher")
    if not cipher:
        return {}
    parsed = json.loads(crypto.decrypt_secret(str(cipher)))
    if not isinstance(parsed, dict):
        raise RouteError(ERROR_ENGINE, "stored OCR credential is not an object", 502)
    return parsed


async def _audit_attempt(
    user_oid: str,
    engine: str,
    *,
    status: str,
    error_code: str | None = None,
    latency_ms: int | None = None,
    engine_kind: str | None = None,
    request_id: str | None = None,
    attempt_no: int | None = None,
    input_bytes: int | None = None,
) -> None:
    """一次尝试一条流水（``units=0``）：额度事实已在入口那条 ``units=1`` 记过。"""
    await audit.record(
        user_oid=user_oid,
        capability="ocr",
        endpoint=FILE_ENDPOINT,
        provider=engine,
        model=engine,
        status=status,
        error_code=error_code,
        latency_ms=latency_ms,
        units=0,
        input_bytes=input_bytes,
        key_scope="server",
        purpose="ocr",
        engine_kind=engine_kind,
        request_id=request_id,
        attempt_no=attempt_no,
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _requested_kind(provider: str | None) -> str | None:
    """``provider`` → 引擎 kind；拼错的 kind 是 400 而不是「没有可用引擎」。

    两者混成一个 503 的话，运维会去查引擎表，而问题其实在请求参数里。
    """
    if provider is None:
        return None
    kind = provider.strip().lower()
    if kind not in ocr_engines.ENGINE_KINDS:
        allowed = ", ".join(ocr_engines.ENGINE_KINDS)
        raise RouteError(
            "AI_OCR_ENGINE_BAD_KIND", f"provider must be one of: {allowed}", 400
        )
    return kind


async def _reject_oversized(repo, attachment_oid: str, row: dict) -> None:
    """体积闸门：大图进 base64 会把内存和上游请求体一起撑爆。

    元数据里的 ``size_bytes`` 是首选口径；缺这个字段时才回落到读一次内容长度
    ——那条路径会物化 BLOB，只在「元数据不完整」的异常数据上走。
    """
    limit = ai_config.max_ocr_image_bytes()
    size = _row_size(row)
    if size is None:
        content = await repo.get_content(attachment_oid)
        size = len(content) if content else 0
    if size > limit:
        raise HTTPException(status_code=400, detail="AI_OCR_IMAGE_TOO_LARGE")


def _row_size(row: dict) -> int | None:
    raw = row.get("size_bytes")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def _refund_site(kind: str) -> None:
    """退还一次站点额度；退款失败只记日志，不改变已经做出的拒绝决定。"""
    try:
        await quota.site_refund(kind)
    except Exception as exc:  # noqa: BLE001 - 补偿失败不该变成另一个错误码
        _logger.warning(
            "ai site quota refund failed for %s: %s", kind, type(exc).__name__
        )


def _quota_exceeded(used: int, limit: int) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail="AI_QUOTA_EXCEEDED",
        headers={
            "X-AI-Quota-Used": str(used),
            "X-AI-Quota-Limit": str(limit),
        },
    )


async def select_engine(
    *,
    exclude: Iterable[str] = (),
    engine_kind: str | None = None,
) -> dict:
    """按 ``priority`` 取第一个「站点额度还够」的引擎，并扣一次它的站点额度。

    返回 ``{"kind", "name", "row"}``：``row`` 给后台任务直接用，省一次查询，也
    避免「选中了 A 却按 kind 回落到 B」这种花了 A 的钱跑了 B 的错位。

    ``exclude`` 是**已经试过**的引擎名（``name`` 全局唯一，比 ``kind`` 更细：
    同一 kind 可以有多行）：跳过它们，而且不为它们再扣一次站点额度——回退的
    每一次尝试都要花新的一份钱，试过的不能重复计费。

    ``engine_kind`` 非空时只在这个 kind 里选（admin 传 ``provider`` 时走这里）：
    仍然逐个 ``site_consume``，指定引擎绕不过站点额度。
    """
    rows = await backends.get_ai_ocr_engines_repo().list_all(enabled_only=True)
    if not rows:
        raise RouteError("AI_OCR_NO_ENGINE", _NO_ENGINE_MESSAGE, 503)

    tried = {str(name) for name in exclude}
    considered = 0
    for row in rows:
        kind = str(row.get("kind") or "")
        if kind not in ocr_engines.ENGINES:
            # DB 的 ck_ai_ocr_engines_kind 已经挡住非法 kind，这里只是不让一行
            # 脏数据把整条路由打死（运维还能靠其它引擎继续跑）。
            continue
        name = _display_name(row, kind)
        if name in tried:
            continue
        if engine_kind is not None and kind != engine_kind:
            continue
        considered += 1
        allowed, _used, _limit = await quota.site_consume(kind, 1)
        if allowed:
            return {"kind": kind, "name": name, "row": row}

    if considered == 0:
        # 空表 / 全被 exclude / 指定 kind 没配：都不是「额度满了」。把它们混进
        # 站点额度耗尽里，运维会去调额度，而真正的问题是没配引擎。
        raise RouteError("AI_OCR_NO_ENGINE", _NO_ENGINE_MESSAGE, 503)
    raise RouteError(
        "AI_OCR_SITE_QUOTA_EXHAUSTED", _NO_SITE_QUOTA_MESSAGE, 503
    )


def _display_name(row: dict, kind: str) -> str:
    """``ai_call_logs.provider`` 是 VARCHAR2(40)：名字缺省时回落到 kind。"""
    return str(row.get("name") or kind)


__all__ = [
    "ALLOWED_MIME",
    "ERROR_ENGINE",
    "FILE_ENDPOINT",
    "MAX_ENGINE_ATTEMPTS",
    "enqueue",
    "recognize_bytes",
    "select_engine",
]

