"""OCR 引擎适配器注册表（腾讯 / 阿里 / RapidOCR）。

注册表把「用哪个厂商的 OCR」收敛成一组同签名的适配器::

    result = await ENGINES[kind]().recognize(image, mime, secret, config)

``secret`` 是 ``ai_ocr_engines.secret_cipher`` 解密后的凭证 **dict**（本地引擎
是 ``{}``），``config`` 是 ``config_json`` 解析后的 dict。凭证的加解密不在这里
做：本模块只吃明文（进程内），写库口径由 :mod:`services.ai.crypto` 决定。

统一结果形状（上层只依赖 ``text`` / ``lines`` / ``engine`` / ``raw``）::

    {"text": str,
     "lines": [{"text": str, "confidence": float | None, "box": [...] | None}],
     "engine": str,
     "raw": dict}

三条归一化约定，都是「上层不该被厂商差异污染」的具体化：

* ``confidence`` 统一到 ``0..1``。腾讯是 0-100，阿里 ``prob`` 与 RapidOCR 的
  ``score`` 本来就是 0-1 —— 如果在适配器外再除一次 100，腾讯的分数就会被压成
  0.0099，展示层看不出错，只有排序会悄悄坏掉。
* ``box`` **保留厂商原始结构**（腾讯是 ``{"X","Y","Width","Height"}``，阿里是
  四个点的列表，RapidOCR 是四点坐标数组）。上层不依赖它，所以不做格式统一：
  强行转成同一个形状只会丢掉这里用不上的信息。
* 空结果也是成功：识别不出字时返回 ``text=""`` 和空 ``lines``，而不是抛异常。
  「图上没字」和「引擎坏了」是两件事，混成同一个异常会让重试逻辑白跑。

依赖全部**延迟导入**：`tencentcloud-sdk-python-ocr` / `alibabacloud-ocr-api20210707`
/ `rapidocr` 都是重包（onnxruntime 尤甚），装不上或没装的部署不该在 import 阶段
把整个网关带崩；缺依赖时抛 :class:`OcrEngineError`，消息可直接进审计日志（不含
任何凭证）。
"""

from __future__ import annotations

import base64
import json
import logging
import threading
from types import SimpleNamespace
from typing import Any, Mapping

import anyio

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# kind 常量：与 ``ck_ai_ocr_engines_kind`` 的 CHECK 逐字一致
# ---------------------------------------------------------------------------

KIND_TENCENT = "tencent"
KIND_ALIYUN = "aliyun"
KIND_RAPIDOCR = "rapidocr"

ENGINE_KINDS: tuple[str, ...] = (KIND_TENCENT, KIND_ALIYUN, KIND_RAPIDOCR)

# 各引擎的上游超时（秒）。写入 ``ClientProfile.reqTimeout`` /
# ``RuntimeOptions.read_timeout``：默认 60 秒对一张 10MB 的图太长，占着并发名额
# 等一个已经死掉的上游没有意义。
DEFAULT_TIMEOUT = 30.0

# 腾讯 GeneralBasicOCR 的 ``LanguageType`` 默认值。``zh`` 是混合中英场景的口径；
# 单语种/其它语种可在 ``config_json`` 里用 ``language_type`` 覆盖。
TENCENT_DEFAULT_LANGUAGE = "zh"

# 阿里 OCR 的默认服务端点；``secret["endpoint"]`` 或 ``config["endpoint"]`` 可覆盖。
# 显式写默认值是因为 Tea SDK 的 ``Config`` 不自带 endpoint，缺了会拼不出 URL。
ALIYUN_DEFAULT_ENDPOINT = "ocr-api.cn-hangzhou.aliyuncs.com"


class OcrEngineError(RuntimeError):
    """引擎不可用：依赖缺失、凭证不全、上游返回无法解析。

    消息里**只有引擎名与缺失的字段名**：这个字符串会进审计与日志，任何情况下
    都不能拼进 secret 的值（TENCENT 的 key 一进日志就等于泄露）。
    """


# ---------------------------------------------------------------------------
# 归一化helpers
# ---------------------------------------------------------------------------

def _confidence(value: Any) -> float | None:
    """把厂商的置信度钳进 ``0..1``；无法解析时返回 ``None``。

    ``None`` 是合法的：不是所有引擎、所有行都给分数，上层必须能容忍它。NaN
    也按 ``None`` 处理——NaN 参与比较永远是 False，会静默穿透所有阈值判断。
    """
    if value is None:
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if score != score:  # NaN
        return None
    return max(0.0, min(1.0, score))


def _percent_confidence(value: Any) -> float | None:
    """腾讯的 ``Confidence`` 是 0-100 的整数，先除 100 再钳位。"""
    if value is None:
        return None
    try:
        return _confidence(float(value) / 100.0)
    except (TypeError, ValueError):
        return None


def _line(text: Any, confidence: Any = None, box: Any = None) -> dict:
    return {
        "text": "" if text is None else str(text),
        "confidence": confidence,
        "box": box,
    }


def _join_lines(lines: list[dict]) -> str:
    """行文本拼全文：换行符是唯一不丢信息的拼法（空格会污染中英混排）。"""
    return "\n".join(item["text"] for item in lines if item["text"])


def _result(*, lines: list[dict], engine: str, raw: dict) -> dict:
    return {
        "text": _join_lines(lines),
        "lines": lines,
        "engine": engine,
        "raw": raw if isinstance(raw, dict) else {},
    }


def _plain(value: Any) -> Any:
    """把 numpy 数组/元组等转成纯 Python 结构，保证结果可 JSON 序列化。

    ``raw`` 会被写进 ``file_ocr.raw_json``（CLOB），带 numpy 标量的 dict 在
    ``json.dumps`` 那一步才会炸——在适配器出口处收敛比在写库处补救便宜。
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        return tolist()
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    item = getattr(value, "item", None)  # 0 维 numpy 标量
    if callable(item):
        try:
            return item()
        except Exception:  # pragma: no cover - 非标量没有 item()
            return str(value)
    return str(value)


def _base64_payload(image: bytes | bytearray | str) -> str:
    """出站 base64：**不带** ``data:image/png;base64,`` 前缀。

    腾讯要求裸 base64。调用方如果直接传了 data URL（前端常见形态），在这里剥掉
    前缀而不是让请求带着 ``data:`` 去撞上游的 400——那种报错看不出真正原因。
    """
    if isinstance(image, (bytes, bytearray)):
        return base64.b64encode(bytes(image)).decode("ascii")
    text = str(image).strip()
    if text.startswith("data:"):
        text = text.split(",", 1)[-1].strip()
    return text


def _require(secret: Mapping[str, Any], field: str, engine: str) -> str:
    value = secret.get(field)
    if not isinstance(value, str) or not value.strip():
        raise OcrEngineError(f"{engine} engine requires secret field {field!r}")
    return value.strip()


# ---------------------------------------------------------------------------
# 腾讯 GeneralBasicOCR
# ---------------------------------------------------------------------------

def _load_tencent_sdk() -> SimpleNamespace:
    """延迟导入腾讯 SDK；缺依赖时给一句能照着装的话。

    单独成函数是为了可测：用例 monkeypatch 这个函数就能绕开真实 SDK，
    同时生产上的 import 成本仍然落在第一次调用。
    """
    try:
        from tencentcloud.common.credential import Credential
        from tencentcloud.common.profile.client_profile import ClientProfile
        from tencentcloud.common.profile.http_profile import HttpProfile
        from tencentcloud.ocr.v20181119 import models, ocr_client_async
    except ImportError as exc:  # pragma: no cover - 依赖已装在 .venv
        raise OcrEngineError(
            "tencent OCR SDK is unavailable: pip install "
            "tencentcloud-sdk-python-ocr tencentcloud-sdk-python-common"
        ) from exc
    return SimpleNamespace(
        Credential=Credential,
        ClientProfile=ClientProfile,
        HttpProfile=HttpProfile,
        models=models,
        client_cls=ocr_client_async.OcrClient,
    )


class TencentOcrEngine:
    """腾讯云通用印刷体识别（``GeneralBasicOCR``，异步 SDK 客户端）。"""

    kind = KIND_TENCENT
    # 落 ``file_ocr.engine_version``：运维要能区分「同一 kind 但上游接口版本变了」。
    VERSION = "tencent-v20181119"

    async def recognize(
        self,
        image: bytes,
        mime: str,
        secret: Mapping[str, Any],
        config: Mapping[str, Any],
    ) -> dict:
        secret_id = _require(secret, "secret_id", self.kind)
        secret_key = _require(secret, "secret_key", self.kind)
        region = str(secret.get("region") or config.get("region") or "").strip()
        if not region:
            # 腾讯的 region 必填（ap-guangzhou 之类），缺了 SDK 会拿空串拼 endpoint。
            raise OcrEngineError("tencent engine requires secret field 'region'")

        sdk = _load_tencent_sdk()
        request = sdk.models.GeneralBasicOCRRequest()
        request.ImageBase64 = _base64_payload(image)
        request.LanguageType = str(
            config.get("language_type") or TENCENT_DEFAULT_LANGUAGE
        )

        profile = sdk.ClientProfile()
        profile.httpProfile.reqTimeout = int(config.get("timeout") or DEFAULT_TIMEOUT)

        credential = sdk.Credential(secret_id, secret_key)
        # 异步客户端持有 httpx.AsyncClient，必须显式关：每次调用新建而不关，
        # 连接池会一路涨到上游拒绝新连接为止。用 async with 而不是 try/finally。
        async with sdk.client_cls(credential, region, profile) as client:
            response = await client.GeneralBasicOCR(request)

        return self._normalize(response)

    @staticmethod
    def _normalize(response: Any) -> dict:
        detections = getattr(response, "TextDetections", None) or []
        lines: list[dict] = []
        raw_lines: list[dict] = []
        for item in detections:
            text = getattr(item, "DetectedText", "") or ""
            confidence = _percent_confidence(getattr(item, "Confidence", None))
            polygon = getattr(item, "ItemPolygon", None)
            lines.append(_line(text, confidence, _polygon_dict(polygon)))
            raw_lines.append(
                {
                    "DetectedText": text,
                    "Confidence": getattr(item, "Confidence", None),
                    "ItemPolygon": _polygon_dict(polygon),
                }
            )
        raw = {
            "RequestId": getattr(response, "RequestId", None),
            "Language": getattr(response, "Language", None),
            "TextDetections": raw_lines,
        }
        return _result(lines=lines, engine=KIND_TENCENT, raw=raw)


def _polygon_dict(polygon: Any) -> dict | None:
    """``ItemPolygon`` → 纯 dict（保留 X/Y/Width/Height 原始键名）。"""
    if polygon is None:
        return None
    return {
        "X": getattr(polygon, "X", None),
        "Y": getattr(polygon, "Y", None),
        "Width": getattr(polygon, "Width", None),
        "Height": getattr(polygon, "Height", None),
    }


# ---------------------------------------------------------------------------
# 阿里 RecognizeGeneral
# ---------------------------------------------------------------------------

def _load_aliyun_sdk() -> SimpleNamespace:
    try:
        from alibabacloud_ocr_api20210707 import models
        from alibabacloud_ocr_api20210707.client import Client
        from alibabacloud_tea_openapi import models as open_api_models
        from alibabacloud_tea_util import models as util_models
    except ImportError as exc:  # pragma: no cover - 依赖已装在 .venv
        raise OcrEngineError(
            "aliyun OCR SDK is unavailable: pip install alibabacloud-ocr-api20210707"
        ) from exc
    return SimpleNamespace(
        Client=Client,
        models=models,
        open_api_models=open_api_models,
        util_models=util_models,
    )


class AliyunOcrEngine:
    """阿里云 OCR 通用识别（``RecognizeGeneral``，同步 Tea 客户端 + to_thread）。"""

    kind = KIND_ALIYUN
    VERSION = "aliyun-20210707"

    async def recognize(
        self,
        image: bytes,
        mime: str,
        secret: Mapping[str, Any],
        config: Mapping[str, Any],
    ) -> dict:
        access_key_id = _require(secret, "access_key_id", self.kind)
        access_key_secret = _require(secret, "access_key_secret", self.kind)
        endpoint = str(
            secret.get("endpoint")
            or config.get("endpoint")
            or ALIYUN_DEFAULT_ENDPOINT
        ).strip()

        sdk = _load_aliyun_sdk()
        client_config = sdk.open_api_models.Config(
            access_key_id=access_key_id,
            access_key_secret=access_key_secret,
        )
        client_config.endpoint = endpoint
        client = sdk.Client(client_config)

        # body 与 Url 二选一：走 body 是为了不吃「上游还得能公网抓到图」的隐性前提。
        request = sdk.models.RecognizeGeneralRequest(body=bytes(image))
        runtime = sdk.util_models.RuntimeOptions()
        runtime.read_timeout = int(config.get("timeout") or DEFAULT_TIMEOUT)
        runtime.connect_timeout = int(config.get("timeout") or DEFAULT_TIMEOUT)

        # Tea 客户端是阻塞的 requests 调用：直接在事件循环里跑会卡住整个 worker。
        response = await anyio.to_thread.run_sync(
            lambda: client.recognize_general_with_options(request, runtime)
        )
        return self._normalize(response)

    @staticmethod
    def _normalize(response: Any) -> dict:
        body = getattr(response, "body", None)
        payload = getattr(body, "data", None) if body is not None else None
        data = _parse_json_object(payload)
        if data is None:
            # 上游 200 但 data 不是 JSON 对象：当作「没识别出东西」，raw 里留下
            # 原始字符串，方便事后判断是网关错误页还是空结果。
            raw = {"data": payload if isinstance(payload, (str, type(None))) else None}
            return _result(lines=[], engine=KIND_ALIYUN, raw=raw)

        lines: list[dict] = []
        for word in data.get("prism_wordsInfo") or []:
            if not isinstance(word, Mapping):
                continue
            lines.append(
                _line(
                    word.get("word", ""),
                    _confidence(word.get("prob")),
                    _plain(word.get("pos")),
                )
            )
        if not lines:
            # 少数场景只回 content 不回逐行结构：按行切开凑 lines，保证上层拿到
            # 的 text 与 lines 不互相矛盾。
            content = data.get("content")
            if isinstance(content, str) and content.strip():
                lines = [_line(part) for part in content.splitlines() if part.strip()]
        return _result(lines=lines, engine=KIND_ALIYUN, raw=data)


def _parse_json_object(payload: Any) -> dict | None:
    """``body.data`` 是 **JSON 字符串**（Tea 模型不替你解），要二次解析。"""
    if isinstance(payload, Mapping):
        return dict(payload)
    if not isinstance(payload, str) or not payload.strip():
        return None
    try:
        parsed = json.loads(payload)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


# ---------------------------------------------------------------------------
# RapidOCR（本地兜底）
# ---------------------------------------------------------------------------

# 模型实例是**进程级**的：det/cls/rec 三个 ONNX 模型的加载成本以秒计，每次调用
# 重建会让第一张图之外的所有请求都白等。锁用 ``threading.Lock`` 而不是
# ``asyncio.Lock``：初始化发生在 ``to_thread`` 的工作线程里，而且 asyncio 原语
# 绑定事件循环，多 worker/重载时会出现「锁绑在旧 loop 上」的经典错误。
_RAPIDOCR_ENGINE: Any = None
_RAPIDOCR_INIT_LOCK = threading.Lock()


def _load_rapidocr_sdk() -> SimpleNamespace:
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:  # pragma: no cover - 依赖已装在 .venv
        raise OcrEngineError(
            "rapidocr is unavailable: pip install rapidocr onnxruntime"
        ) from exc
    return SimpleNamespace(RapidOCR=RapidOCR)


def _rapidocr_instance() -> Any:
    """取（必要时初始化）进程内单例。调用方必须在工作线程里调。"""
    global _RAPIDOCR_ENGINE
    if _RAPIDOCR_ENGINE is not None:
        return _RAPIDOCR_ENGINE
    with _RAPIDOCR_INIT_LOCK:
        if _RAPIDOCR_ENGINE is None:
            _RAPIDOCR_ENGINE = _load_rapidocr_sdk().RapidOCR()
    return _RAPIDOCR_ENGINE


class RapidOcrEngine:
    """RapidOCR：进程内 ONNX 推理，无凭证、无 HTTP，作为云端失败时的兜底。"""

    kind = KIND_RAPIDOCR
    VERSION = "rapidocr-2"

    async def recognize(
        self,
        image: bytes,
        mime: str,
        secret: Mapping[str, Any],
        config: Mapping[str, Any],
    ) -> dict:
        # secret 允许为空 dict：本地引擎没有凭证，「不需要」不是「配置缺失」。
        payload = bytes(image) if isinstance(image, (bytes, bytearray)) else b""
        if not payload:
            raise OcrEngineError("rapidocr engine requires non-empty image bytes")
        # 推理是 CPU 密集的同步调用，必须离开事件循环。
        return await anyio.to_thread.run_sync(self._recognize_sync, payload)

    @staticmethod
    def _recognize_sync(payload: bytes) -> dict:
        engine = _rapidocr_instance()
        output = engine(payload)  # rapidocr 3.x 直接接受 bytes

        txts = getattr(output, "txts", None) or ()
        scores = getattr(output, "scores", None) or ()
        boxes = getattr(output, "boxes", None)

        lines: list[dict] = []
        for index, text in enumerate(txts):
            score = scores[index] if index < len(scores) else None
            box = None
            if boxes is not None:
                try:
                    if index < len(boxes):
                        box = _plain(boxes[index])
                except TypeError:  # pragma: no cover - 非序列的 boxes
                    box = None
            lines.append(_line(text, _confidence(score), box))

        raw = {
            "elapse": _plain(getattr(output, "elapse", None)),
            "scores": [_confidence(score) for score in scores],
            "boxes": _plain(boxes),
        }
        return _result(lines=lines, engine=KIND_RAPIDOCR, raw=raw)


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

ENGINES: dict[str, type] = {
    KIND_TENCENT: TencentOcrEngine,
    KIND_ALIYUN: AliyunOcrEngine,
    KIND_RAPIDOCR: RapidOcrEngine,
}


__all__ = [
    "ENGINES",
    "ENGINE_KINDS",
    "KIND_TENCENT",
    "KIND_ALIYUN",
    "KIND_RAPIDOCR",
    "TENCENT_DEFAULT_LANGUAGE",
    "ALIYUN_DEFAULT_ENDPOINT",
    "DEFAULT_TIMEOUT",
    "OcrEngineError",
    "TencentOcrEngine",
    "AliyunOcrEngine",
    "RapidOcrEngine",
]
