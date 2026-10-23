"""OCR 入队：选引擎、扣两份额度、把活交给后台任务。

只接受**已通过 attachments API 落库**的图片：网关不提供直传通道，否则 OCR 会变成
一条绕过附件配额与内容审核的旁路。真正的识别由 ``util/attachment_ocr.process_ocr``
在后台任务里跑，引擎按 ``ai_ocr_engines`` 注册表的 ``priority`` 选。

步骤顺序不能变：先鉴权再扣配额——先扣后拒等于白扣用户额度。两份额度也分先后：

* **用户额度**（``ocr``）先 ``peek`` 后 ``consume``：预检不花代价，真扣留在
  「引擎已选中」之后，避免「没有可用引擎」也把用户额度扣掉；
* **站点额度**（``ocr_<engine>``）在选引擎时按 ``priority`` 逐个尝试，第一个
  ``allowed`` 的胜出——被拒的那次不扣（仓储是原子条件自增），所以轮换无副作用。
"""

from __future__ import annotations

from fastapi import HTTPException

from db import backends
from services.ai.routing import RouteError
from util.attachment import can_read_attachment

from . import audit, ocr_engines, quota, rate_limit

ALLOWED_MIME = frozenset({"image/jpeg", "image/png", "image/webp"})

_NO_ENGINE_MESSAGE = "no OCR engine is configured"
_NO_SITE_QUOTA_MESSAGE = "every configured OCR engine is out of site quota"


async def enqueue(user: dict, attachment_oid: str) -> dict:
    """把附件挂进 OCR 队列；``attachment_oid`` 已由路由校验为合法 oid。

    返回值里除了对外的 ``status`` / ``attachment_oid`` / ``engine``，还带一个
    ``engine_kind``：路由层要用它把**同一个**引擎传给后台任务。对外响应会把
    这个内部字段摘掉（见 ``routers/ai_router.request_ocr``）。
    """
    repo = backends.get_files_repo()
    row = await repo.get_meta(attachment_oid)
    if row is None:
        raise HTTPException(status_code=404, detail="Attachment not found")

    if not await can_read_attachment(user, row, user.get("class_id")):
        raise HTTPException(status_code=403, detail="Permission denied")

    mime = str(row.get("mime") or "").lower()
    if mime not in ALLOWED_MIME:
        raise HTTPException(status_code=400, detail="AI_UNSUPPORTED_MEDIA")

    # 已经在跑的不重复扣配额，也不重复入队。
    if row.get("ocr_status") == "processing":
        return {"status": "processing", "attachment_oid": attachment_oid}

    # 先频控再扣额度：分钟窗口挡突发，月额度挡长期滥用。顺序反了的话，被频控
    # 拒掉的请求已经扣过额度，用户会看到「没成功但额度少了」。
    try:
        rate_limit.check(user["id"], "ocr")
    except rate_limit.RateLimitError as exc:
        raise HTTPException(
            status_code=429,
            detail="AI_RATE_LIMITED",
            headers={"Retry-After": str(exc.retry_after)},
        ) from None

    # 预检用户额度：不足就直接拒，别先去占一份站点额度（占完再拒要回滚，
    # 而站点额度的扣减是不可逆的自增）。
    allowed, used, limit = await quota.peek(
        user["id"], "ocr", 1, roles=user.get("per")
    )
    if not allowed:
        raise _quota_exceeded(used, limit)

    engine = await _select_engine()

    allowed, used, limit = await quota.consume(
        user["id"], "ocr", 1, roles=user.get("per")
    )
    if not allowed:
        # 预检与真扣之间的竞态：另一个并发请求刚把额度用完。站点额度已经占了
        # 一次，这里不退还——OCR 的单次成本远低于一次退款补偿逻辑的维护成本，
        # 而且「额度用满」本身不是异常状态。
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
    )

    return {
        "status": "pending",
        "attachment_oid": attachment_oid,
        "engine": engine["name"],
        "engine_kind": engine["kind"],
    }


def _quota_exceeded(used: int, limit: int) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail="AI_QUOTA_EXCEEDED",
        headers={
            "X-AI-Quota-Used": str(used),
            "X-AI-Quota-Limit": str(limit),
        },
    )


async def _select_engine() -> dict:
    """按 ``priority`` 取第一个「站点额度还够」的引擎。

    空表与「全被额度挡下」是两种运维状态，错误码必须分开：前者是没配引擎，
    后者是额度打满；混成一个码的话，运维会去查一张根本没配的表。
    """
    rows = await backends.get_ai_ocr_engines_repo().list_all(enabled_only=True)
    if not rows:
        raise RouteError("AI_OCR_NO_ENGINE", _NO_ENGINE_MESSAGE, 503)

    considered = 0
    for row in rows:
        kind = str(row.get("kind") or "")
        if kind not in ocr_engines.ENGINES:
            # DB 的 ck_ai_ocr_engines_kind 已经挡住非法 kind，这里只是不让一行
            # 脏数据把整条路由打死（运维还能靠其它引擎继续跑）。
            continue
        considered += 1
        allowed, _used, _limit = await quota.site_consume(kind, 1)
        if allowed:
            return {"kind": kind, "name": _display_name(row, kind)}

    if considered == 0:
        raise RouteError("AI_OCR_NO_ENGINE", _NO_ENGINE_MESSAGE, 503)
    raise RouteError(
        "AI_OCR_SITE_QUOTA_EXHAUSTED", _NO_SITE_QUOTA_MESSAGE, 503
    )


def _display_name(row: dict, kind: str) -> str:
    """``ai_call_logs.provider`` 是 VARCHAR2(40)：名字缺省时回落到 kind。"""
    return str(row.get("name") or kind)


__all__ = ["ALLOWED_MIME", "enqueue"]
