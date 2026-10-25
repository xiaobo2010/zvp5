"""Pluggable OCR interface + state machine helpers (design §7).

引擎的**实现**不在这里：真实适配器都在 :mod:`services.ai.ocr_engines`（腾讯 /
阿里 / RapidOCR），本模块只负责「跑选中的引擎 → 失败回退 → 落库 → 推进状态机」。

引擎由入队方先选一个（``services.ai.ocr.enqueue`` 已扣用户额度与那一份站点
额度），本模块只**跟随**：第一次尝试必须是入队时的那一行——即使它在入队后被改名
或禁用，也先如实记一次失败。之后的回退由 :func:`services.ai.ocr.select_engine`
提供候选：同一套 ``priority`` 顺序、同一个 ``site_consume`` 闸门，回退的每一次
尝试都要新扣一份站点额度、并写一条审计。

失败不回退额度（上游可能已经计费）：只有「站点额度已扣、用户额度竞态失败」才由
``quota.site_refund`` 退还，那条补偿在入队侧。

State machine: ``stored -> (ocr pending -> processing -> done|failed) ->
verified|rejected``. Every transition is driven through
``FilesRepo.set_ocr`` with an audit row in ``file_events``.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Protocol
from uuid import uuid4

from db import backends
from services.ai import audit, crypto, ocr_engines
from services.ai import config as ai_config
from services.ai import ocr as ai_ocr
from services.ai.routing import RouteError

_logger = logging.getLogger(__name__)

OCR_PENDING = "pending"
OCR_PROCESSING = "processing"
OCR_DONE = "done"
OCR_FAILED = "failed"

# 引擎失败在审计表里的稳定错误码（``error_code`` 是 VARCHAR2(40)）。
ERROR_ENGINE = "AI_OCR_ENGINE_ERROR"


class OcrEngine(Protocol):
    """A synchronous OCR engine (legacy in-process shape)."""

    name: str
    version: str

    def recognize(self, image: bytes) -> dict[str, Any]:
        """Return ``{"text": str, "confidence": float, "raw": Any}``."""
        ...


class NoopEngine:
    """Placeholder engine: recognizes nothing, never fails.

    保留是为了向后兼容（旧代码/第三方引用按类名导入）；``process_ocr`` 不再用它
    ——现在识别走注册表里的真实引擎。
    """

    name = "noop"
    version = "0"

    def recognize(self, image: bytes) -> dict[str, Any]:
        return {"text": "", "confidence": 0.0, "raw": {}}


class FatalOcrError(RuntimeError):
    """与引擎无关的失败（内容缺失 / 体积超限）：换引擎也无解，不该再花一份额度。

    单独成一类而不是靠消息文本判断：``ValueError`` 这种通用异常在适配器里也可能
    出现，按类型分支才不会在文案改动后静默失效。
    """


DEFAULT_ENGINE: OcrEngine = NoopEngine()


async def _set_ocr(file_oid: str, status: str, *, bump_retry: bool = False) -> None:
    await backends.get_files_repo().set_ocr(
        file_oid, status, bump_retry=bump_retry
    )


async def _add_event(
    file_oid: str, action: str, from_status: str | None, to_status: str | None
) -> None:
    # ``None`` actor = system-driven transition.
    await backends.get_files_repo().add_event(
        file_oid, None, action, from_status, to_status, ""
    )


async def mark_ocr_processing(file_oid: str) -> None:
    await _set_ocr(file_oid, OCR_PROCESSING)
    await _add_event(file_oid, "ocr_start", OCR_PENDING, OCR_PROCESSING)


async def mark_ocr_done(file_oid: str) -> None:
    await _set_ocr(file_oid, OCR_DONE)
    await _add_event(file_oid, "ocr_done", OCR_PROCESSING, OCR_DONE)


async def mark_ocr_failed(file_oid: str) -> None:
    await _set_ocr(file_oid, OCR_FAILED, bump_retry=True)
    await _add_event(file_oid, "ocr_failed", OCR_PROCESSING, OCR_FAILED)


async def process_ocr(
    file_oid: str,
    engine_kind: str,
    engine_name: str | None = None,
    user_oid: str | None = None,
) -> None:
    """Run the enqueued engine against the stored bytes, falling back on failure.

    ``engine_kind`` / ``engine_name`` 由入队方透传（见模块 docstring）：第一个
    候选固定是它们，之后按 ``priority`` 回退，最多共 ``MAX_ENGINE_ATTEMPTS`` 次。
    ``user_oid`` 只用来写审计（谁触发的这次识别）；调用方不传时跳过逐次审计，
    不影响状态机。

    一次识别的**所有**尝试共用同一个 ``request_id``（本函数生成）：入队那一条
    （``units=1``）与后续每一次回退因此能串成一条链，不必再靠「同用户 + 相邻
    时间」去猜。

    Drives ``set_ocr`` transitions and writes ``file_ocr`` + ``file_events``.
    Any failure lands the file in ``failed`` (retry bumped) and is swallowed so a
    background task can never crash the caller. 异常消息**不含凭证**：适配器的
    ``OcrEngineError`` 只拼引擎名与字段名，解密失败抛的是通用 ``ValueError``。
    """
    repo = backends.get_files_repo()
    request_id = uuid4().hex
    try:
        await mark_ocr_processing(file_oid)
        content = await repo.get_content(file_oid)
        if content is None:
            raise FatalOcrError("Attachment content is unavailable")

        meta = await repo.get_meta(file_oid) or {}
        mime = str(meta.get("mime") or "").lower()

        limit = ai_config.max_ocr_image_bytes()
        if len(content) > limit:
            # 入队时按元数据判过一次；这里按**真实字节**再判一次，防元数据与实际
            # 不一致（幂等分支、历史数据、绕过入队直接调用都会走到这里）。
            raise FatalOcrError(f"attachment exceeds the {limit}-byte OCR limit")

        await _recognize(
            repo,
            file_oid,
            bytes(content),
            mime,
            meta,
            engine_kind,
            engine_name,
            user_oid,
            request_id,
        )
        await mark_ocr_done(file_oid)
    except Exception as exc:  # noqa: BLE001 - 后台任务不能把异常抛回调用方
        # 只记类型与消息：适配器的消息里只有引擎名/字段名，不含凭证值。
        _logger.warning(
            "ocr failed for %s: %s: %s", file_oid, type(exc).__name__, exc
        )
        try:
            await mark_ocr_failed(file_oid)
        except Exception:  # noqa: BLE001 - 状态机本身失败只能放弃
            pass


async def _recognize(
    repo,
    file_oid: str,
    image: bytes,
    mime: str,
    meta: dict,
    engine_kind: str,
    engine_name: str | None,
    user_oid: str | None,
    request_id: str,
) -> None:
    """按候选顺序识别，第一个成功的写 ``file_ocr`` 并返回。

    每次尝试都写一条 ``ai_call_logs``：``model`` 是引擎名，失败记 ``error_code``。
    ``units=0``——额度与「这次调用被计费」的事实已经在入队时记过一笔（那条是
    ``units=1``），这里记的是「这一次尝试用的是谁、成没成」，重复计 unit 会让
    对账表的总额翻倍。

    每行同时带上 ``request_id``（整条链共用）/ ``attachment_oid`` /
    ``engine_kind`` / ``attempt_no``：这四列合起来才能回答「这张图第几次尝试、
    用了哪种引擎、成没成」而不必跨表拼。
    """
    tried: set[str] = set()
    last_error: BaseException | None = None

    for attempt in range(ai_ocr.MAX_ENGINE_ATTEMPTS):
        try:
            if attempt == 0:
                row = await _select_row(engine_kind, engine_name)
            else:
                # 回退候选由入队同一套逻辑给出（同 priority 顺序 + 扣该引擎的
                # 站点额度）；已经试过的名字跳过，不会重复计费。
                row = (await ai_ocr.select_engine(exclude=tried))["row"]
        except RouteError as exc:
            # 没有下一个候选（引擎表空 / 全试过 / 全部站点额度打满）。
            last_error = last_error or exc
            break
        except Exception as exc:  # noqa: BLE001 - 取行失败等于这一次尝试失败
            last_error = exc
            # 只有确实知道引擎名时才记账/排除：拿 kind 当名字会把同名的另一行
            # 一起挡掉（回退候选本来可能是好的）。
            if engine_name:
                tried.add(engine_name)
                await _audit_attempt(
                    user_oid,
                    meta,
                    engine_name,
                    status="error",
                    error_code=ERROR_ENGINE,
                    engine_kind=engine_kind,
                    attachment_oid=file_oid,
                    request_id=request_id,
                    attempt_no=attempt + 1,
                )
            continue

        kind = str(row["kind"])
        label = _engine_label(row, kind)
        tried.add(label)
        started = time.monotonic()
        try:
            adapter = ocr_engines.ENGINES[kind]()
            result = await adapter.recognize(
                image, mime, _secret(row), row.get("config") or {}
            )
        except FatalOcrError:
            raise
        except Exception as exc:  # noqa: BLE001 - 换下一个引擎，失败写审计
            last_error = exc
            _logger.warning(
                "ocr attempt %s failed for %s on %s: %s: %s",
                attempt + 1,
                file_oid,
                label,
                type(exc).__name__,
                exc,
            )
            await _audit_attempt(
                user_oid,
                meta,
                label,
                status="error",
                error_code=ERROR_ENGINE,
                latency_ms=_elapsed_ms(started),
                engine_kind=kind,
                attachment_oid=file_oid,
                request_id=request_id,
                attempt_no=attempt + 1,
            )
            continue

        await repo.add_ocr(
            file_oid,
            label,
            type(adapter).VERSION,
            str(result.get("text", "")),
            _confidence(result),
            result.get("raw", {}),
        )
        await _audit_attempt(
            user_oid,
            meta,
            label,
            status="success",
            latency_ms=_elapsed_ms(started),
            engine_kind=kind,
            attachment_oid=file_oid,
            request_id=request_id,
            attempt_no=attempt + 1,
        )
        return

    raise last_error or ocr_engines.OcrEngineError(
        "no OCR engine could handle the attachment"
    )


async def _audit_attempt(
    user_oid: str | None,
    meta: dict,
    engine: str,
    *,
    status: str,
    error_code: str | None = None,
    latency_ms: int | None = None,
    engine_kind: str | None = None,
    attachment_oid: str | None = None,
    request_id: str | None = None,
    attempt_no: int | None = None,
) -> None:
    """写一条「这一次尝试」的流水；没有 ``user_oid`` 就没法归属，只能跳过。

    ``purpose`` 恒为 ``'ocr'``：OCR 只有一个场景，不像 chat 那样由前端区分。
    ``engine_kind`` 是**注册表里的 kind**（tencent/aliyun/rapidocr/baidu/
    groq/zhipu），``provider``/``model`` 仍是引擎名——后者是运维认引擎的标签，
    前者才能按「引擎类型」聚合失败率。
    """
    if not user_oid:
        return
    await audit.record(
        user_oid=user_oid,
        capability="ocr",
        endpoint=audit.ENDPOINT_OCR,
        provider=engine,
        model=engine,
        status=status,
        error_code=error_code,
        latency_ms=latency_ms,
        units=0,
        input_bytes=meta.get("size_bytes"),
        key_scope="server",
        purpose="ocr",
        engine_kind=engine_kind,
        attachment_oid=attachment_oid,
        request_id=request_id,
        attempt_no=attempt_no,
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


async def _select_row(engine_kind: str, engine_name: str | None) -> dict:
    """按 kind/name 取引擎行。

    ``name`` 只是同一 ``kind`` 内的消歧（同类型可以有多行：不同 region / 不同
    配额来源）；名字对不上就回落到该 kind 的第一行，而不是直接失败——引擎被改名
    后旧的入队记录仍然应该能跑完。
    """
    rows = await backends.get_ai_ocr_engines_repo().list_all(enabled_only=True)
    if not rows:
        raise ocr_engines.OcrEngineError("no OCR engine is enabled")

    candidates = [row for row in rows if str(row.get("kind") or "") == engine_kind]
    if not candidates:
        # 引擎在入队之后被禁用/删除了：这一次尝试如实失败，回退由 ``_recognize``
        # 里的下一个候选接手。
        raise ocr_engines.OcrEngineError(
            f"OCR engine {engine_kind!r} is no longer enabled"
        )
    if engine_name:
        for row in candidates:
            if str(row.get("name") or "") == engine_name:
                return row
    return candidates[0]


def _secret(row: dict) -> dict:
    """``secret_cipher`` → 凭证 dict；``None`` = 本地引擎，不需要凭证。"""
    cipher = row.get("secret_cipher")
    if not cipher:
        return {}
    parsed = json.loads(crypto.decrypt_secret(str(cipher)))
    if not isinstance(parsed, dict):
        raise ocr_engines.OcrEngineError("stored OCR credential is not an object")
    return parsed


def _engine_label(row: dict, kind: str) -> str:
    """落 ``file_ocr.engine``：用注册表里的名字（运维按名字认引擎）。"""
    return str(row.get("name") or kind)


def _confidence(result: dict) -> float:
    """统一结果没有顶层 confidence：按行取平均（全空则 0.0）。

    单行结果下平均值就是那一行的分数，多行时它代表整张图的识别质量——比取
    第一行稳定（第一行往往是标题，分数分布和正文不同）。
    """
    scores = [
        float(line["confidence"])
        for line in result.get("lines") or []
        if isinstance(line, dict) and line.get("confidence") is not None
    ]
    if not scores:
        return 0.0
    return max(0.0, min(1.0, sum(scores) / len(scores)))


__all__ = [
    "DEFAULT_ENGINE",
    "ERROR_ENGINE",
    "FatalOcrError",
    "NoopEngine",
    "OCR_DONE",
    "OCR_FAILED",
    "OCR_PENDING",
    "OCR_PROCESSING",
    "OcrEngine",
    "mark_ocr_done",
    "mark_ocr_failed",
    "mark_ocr_processing",
    "process_ocr",
]
