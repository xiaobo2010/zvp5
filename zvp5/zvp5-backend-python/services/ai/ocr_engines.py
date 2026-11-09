"""OCR 引擎适配器注册表（腾讯 / 阿里 / 百度 / Groq / 智谱）。

注册表把「用哪个厂商的 OCR」收敛成一组同签名的适配器::

    result = await ENGINES[kind]().recognize(image, mime, secret, config)

``secret`` 是 ``ai_ocr_engines.secret_cipher`` 解密后的凭证 **dict**，``config``
是 ``config_json`` 解析后的 dict。凭证的加解密不在这里做：本模块只吃明文
（进程内），写库口径由 :mod:`services.ai.crypto` 决定。

统一结果形状（上层只依赖 ``text`` / ``lines`` / ``engine`` / ``raw``）::

    {"text": str,
     "lines": [{"text": str, "confidence": float | None, "box": [...] | None}],
     "engine": str,
     "raw": dict}

三条归一化约定，都是「上层不该被厂商差异污染」的具体化：

* ``confidence`` 统一到 ``0..1``。腾讯是 0-100，阿里 ``prob`` 本来就是 0-1
  —— 如果在适配器外再除一次 100，腾讯的分数就会被压成 0.0099，展示层看不出错，
  只有排序会悄悄坏掉。
* ``box`` **保留厂商原始结构**（腾讯是 ``{"X","Y","Width","Height"}``，阿里是
  四个点的列表）。上层不依赖它，所以不做格式统一：强行转成同一个形状只会丢掉
  这里用不上的信息。
* 空结果也是成功：识别不出字时返回 ``text=""`` 和空 ``lines``，而不是抛异常。
  「图上没字」和「引擎坏了」是两件事，混成同一个异常会让重试逻辑白跑。

依赖全部**延迟导入**：`tencentcloud-sdk-python-ocr` / `alibabacloud-ocr-api20210707`
都是重包，装不上或没装的部署不该在 import 阶段把整个网关带崩；缺依赖时抛
:class:`OcrEngineError`，消息可直接进审计日志（不含任何凭证）。

百度与 Groq 走项目自带的 ``httpx`` **手写 HTTP**，不引 SDK：百度 ``baidu-aip``
停更多年，Groq 只需一个 OpenAI 兼容端点，多一个包就多一份升级债。
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import threading
import time
from types import SimpleNamespace
from typing import Any, Mapping

import anyio

from . import credentials
from . import thinking as thinking_param

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# kind 常量：注册表只支持这五个**云端**引擎。DB 的 ``ck_ai_ocr_engines_kind`` 仍
# 保留 ``'rapidocr'``（本地引擎已下线，存量行由 ``select_engine`` 跳过、由运维清理）。
# ---------------------------------------------------------------------------

KIND_TENCENT = "tencent"
KIND_ALIYUN = "aliyun"
KIND_BAIDU = "baidu"
KIND_GROQ = "groq"
KIND_ZHIPU = "zhipu"

ENGINE_KINDS: tuple[str, ...] = (
    KIND_TENCENT,
    KIND_ALIYUN,
    KIND_BAIDU,
    KIND_GROQ,
    KIND_ZHIPU,
)

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

# 百度 OCR：token 端点与识别端点同域，``ocr_api`` 默认走高精度版（``accurate_basic``
# 比 ``general_basic`` 慢一点但中英混排准得多）。
BAIDU_DEFAULT_ENDPOINT = "https://aip.baidubce.com"
BAIDU_DEFAULT_OCR_API = "accurate_basic"
# 换 token 的提前量（秒）。上游 ``expires_in`` 是 30 天，掐点复用 token 会偶发
# ``error_code 110``；留 5 分钟缓冲，代价是每 30 天多一次换 token 请求。
BAIDU_TOKEN_REFRESH_MARGIN = 300.0

# Groq：OpenAI 兼容端点。``model`` 一律从 ``config`` 读，**不写死**——Groq 的
# 可用视觉模型换得比 SDK 版本还快，写死等于给自己埋一个定时 404。
GROQ_DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "qwen/qwen3.8-27b"
GROQ_DEFAULT_REASONING_EFFORT = "none"
# ``max_tokens`` **必须显式传**：不传时 Groq 按一个远高于实际需要的「预期输出」
# 计费（实测 1164），而免费/on_demand 档的 OTPM 上限就是 1000 → 每一页都 429。
# 1000 是免费层的硬上限；Dev Tier 可用 ``config["max_tokens"]`` 调大。
GROQ_DEFAULT_MAX_TOKENS = 1000

# 智谱 BigModel（OpenAI 兼容 v4）。定位是**境内免费兜底**：免费视觉模型没有公开
# 的日/月 token 上限，只按「并发数」限流（具体值在控制台），比腾讯/百度的
# 1000 次/月宽松得多。
#
# model 必须选**视觉**款：``glm-4.7-flash`` 确实存在，但官方模型页标注它的输入
# 模态是「文本」——传图片进去不会被识别，只会得到一段凭空编的文字。
ZHIPU_DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
ZHIPU_DEFAULT_MODEL = "glm-4.6v-flash"
# 免费视觉模型最大输出 32K / 默认 16K；一页 38 行约 1.2K token，4096 够用，
# 而且能把「截断」更早暴露出来（配合 finish_reason 检查）。
ZHIPU_DEFAULT_MAX_TOKENS = 4096
# 官方 temperature 区间是 (0, 1)，传 0 不合法；默认 0.6 对 OCR 偏发散。
ZHIPU_DEFAULT_TEMPERATURE = 0.1
# 智谱免费模型会偶发 1305（平台过载）/ 1302（账户并发）——都是瞬态，实测重试即成功。
# 上游不给 Retry-After，所以按固定退避：3 次尝试、间隔 2s/5s，最坏多等 7 秒。
# OCR 跑在后台任务里，这点等待不该让最终用户看到失败。
ZHIPU_DEFAULT_RETRY_ATTEMPTS = 3
ZHIPU_DEFAULT_RETRY_DELAYS = (2.0, 5.0)
# 值得重试的业务码：1302 账户并发、1305 平台过载。1308 是「使用上限已到」，
# 重试一百次也还是超限，只会把失败拖慢——必须立刻抛。
ZHIPU_RETRYABLE_CODES = frozenset({"1302", "1305"})


class OcrEngineError(RuntimeError):
    """引擎不可用：依赖缺失、凭证不全、上游返回无法解析。

    消息里**只有引擎名与缺失的字段名**：这个字符串会进审计与日志，任何情况下
    都不能拼进 secret 的值（TENCENT 的 key 一进日志就等于泄露）。
    """


class OcrEngineRateLimited(OcrEngineError):
    """上游限流，属于**可重试**失败（与「这页读不出来」区分开）。

    ``retry_after`` 是上游建议的等待秒数；上游没给提示时为 ``None``。归成一类
    独立异常而不是靠消息文本判断：调用方将来要做退避重试时，需要能按类型分支，
    字符串匹配在错误体换文案的那天就会静默失效。
    """

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


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
# 手写 HTTP（百度 / Groq 共用）
# ---------------------------------------------------------------------------

def _async_http_client(timeout: float) -> Any:
    """建一个 ``httpx.AsyncClient``；httpx 缺失时给可照装的提示。

    单独成函数是为了可测：用例 monkeypatch 这个工厂，把
    ``httpx.MockTransport`` 塞进去，就能在不碰网络、不用真凭证的前提下断言
    出站 URL / 头 / body（比 mock 掉整个适配器方法有信息量得多）。
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - 依赖已装在 .venv
        raise OcrEngineError("httpx is unavailable: pip install httpx") from exc
    return httpx.AsyncClient(timeout=timeout)


def _truncate(value: Any, limit: int = 200) -> str:
    """错误信息截断：上游 500 的 HTML 整页塞进异常会把日志冲爆。"""
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + "…"


def _ok_json(response: Any, engine: str) -> dict:
    """非 2xx / 非 JSON / 非对象一律转成 :class:`OcrEngineError`。

    错误体只截前 200 字符：够定位（``model_not_found`` 之类），又不会把整页
    HTML 灌进审计日志。响应体里不会有请求头，所以 ``Authorization`` 不会漏。
    """
    status = getattr(response, "status_code", 0)
    if not 200 <= status < 300:
        raise OcrEngineError(
            f"{engine} engine request failed with status {status}: {_truncate(getattr(response, 'text', ''))}"
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise OcrEngineError(
            f"{engine} engine returned a non-JSON response: {_truncate(getattr(response, 'text', ''))}"
        ) from exc
    if not isinstance(data, dict):
        raise OcrEngineError(f"{engine} engine returned an unexpected JSON payload")
    return data



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
# 百度 OCR（accurate_basic / general_basic）
# ---------------------------------------------------------------------------

# token 缓存：``(endpoint, api_key, secret 摘要)`` → ``(access_token, 到期时刻 monotonic)``。
# 为什么不把 token 拼进类的实例属性：适配器是**每次识别新建**的
# （``ENGINES[kind]()``），实例级缓存等于没有缓存，每次识别都多一次换 token 往返。
# 缓存键带 secret 摘要：轮换 secret_key 后，旧 secret 换出的 token 必须立刻作废
# （百度 token 有效期以月计，不作废等于「换了密钥还留着旧通道」）。摘要用
# sha256 前 16 位而不是原始密钥——键只做相等比较，不落原始密钥到内存长驻。
_BAIDU_TOKEN_LOCK = threading.Lock()
_BAIDU_TOKENS: dict[tuple[str, str, str], tuple[str, float]] = {}


def _baidu_secret_digest(secret_key: str) -> str:
    return hashlib.sha256(secret_key.encode("utf-8")).hexdigest()[:16]


def _baidu_token_key(endpoint: str, api_key: str, secret_key: str) -> tuple[str, str, str]:
    return (endpoint.rstrip("/"), api_key, _baidu_secret_digest(secret_key))


def _baidu_cached_token(endpoint: str, api_key: str, secret_key: str) -> str | None:
    key = _baidu_token_key(endpoint, api_key, secret_key)
    with _BAIDU_TOKEN_LOCK:
        entry = _BAIDU_TOKENS.get(key)
    if entry is None:
        return None
    token, expires_at = entry
    if time.monotonic() >= expires_at:
        _baidu_drop_token(endpoint, api_key, secret_key)
        return None
    return token


def _baidu_store_token(
    endpoint: str, api_key: str, secret_key: str, token: str, expires_in: Any
) -> None:
    try:
        ttl = float(expires_in)
    except (TypeError, ValueError):
        ttl = 0.0
    if ttl <= 0:
        # ``expires_in`` 缺失/异常：宁可不缓存，也不要拿一个寿命未知的 token
        # 去赌（过期后的 110 会以「识别失败」的形式冒出来，极难查）。
        return
    # 提前 ``BAIDU_TOKEN_REFRESH_MARGIN`` 秒视为过期；token 寿命比缓冲还短时
    # 按原寿命缓存，否则每次识别都会换 token，缓存等于没写。
    lifetime = ttl - BAIDU_TOKEN_REFRESH_MARGIN if ttl > BAIDU_TOKEN_REFRESH_MARGIN else ttl
    key = _baidu_token_key(endpoint, api_key, secret_key)
    # 临界区只做字典写入，**不跨 await**：线程锁配协程时，锁内 await 会同时
    # 卡住事件循环和别的 worker。
    with _BAIDU_TOKEN_LOCK:
        _BAIDU_TOKENS[key] = (token, time.monotonic() + lifetime)


def _baidu_drop_token(endpoint: str, api_key: str, secret_key: str) -> None:
    key = _baidu_token_key(endpoint, api_key, secret_key)
    with _BAIDU_TOKEN_LOCK:
        _BAIDU_TOKENS.pop(key, None)


def _clear_baidu_tokens() -> None:
    """清空 token 缓存（运维排障 / 用例隔离用）。"""
    with _BAIDU_TOKEN_LOCK:
        _BAIDU_TOKENS.clear()


async def _baidu_access_token(
    endpoint: str, api_key: str, secret_key: str, timeout: float
) -> str:
    """取 access_token：先查进程内缓存，未命中才走 OAuth。

    并发下最多出现两次并行换 token（错过窗口就各换各的），最后一次写入胜出——
    百度对同一组 client_id 不做旧 token 吊销，重复换是幂等的，加锁跨 await 反而
    会把所有识别请求串成一条队。
    """
    cached = _baidu_cached_token(endpoint, api_key, secret_key)
    if cached:
        return cached

    url = f"{endpoint.rstrip('/')}/oauth/2.0/token"
    async with _async_http_client(timeout) as client:
        response = await client.post(
            url,
            params={
                "grant_type": "client_credentials",
                "client_id": api_key,
                "client_secret": secret_key,
            },
        )

    status = getattr(response, "status_code", 0)
    if not 200 <= status < 300:
        raise OcrEngineError(
            f"baidu engine token request failed with status {status}"
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise OcrEngineError("baidu engine token endpoint returned non-JSON") from exc
    if not isinstance(data, dict) or not data.get("access_token"):
        # 只回放百度自己的错误字段：``response.text`` 整体可能带上别的参数，
        # 而这个异常会进审计日志。
        detail = None
        if isinstance(data, dict):
            detail = data.get("error_description") or data.get("error")
        raise OcrEngineError(
            f"baidu engine token request was rejected: {_truncate(detail) or 'no access_token'}"
        )

    token = str(data["access_token"])
    _baidu_store_token(endpoint, api_key, secret_key, token, data.get("expires_in"))
    return token


class BaiduOcrEngine:
    """百度 OCR 通用文字识别（``accurate_basic`` 默认，可配 ``general_basic``）。

    两次 HTTP：
    1. ``/oauth/2.0/token`` 换 access_token（进程内按 ``expires_in`` 缓存）；
    2. ``/rest/2.0/ocr/v1/{ocr_api}`` 送纯 base64 图片。

    ``confidence`` 一律为 ``None``：该接口不返回逐行置信度（``probability=true``
    才有 ``probability.average``，但那是整包均值，塞进每一行就是编数据）。
    """

    kind = KIND_BAIDU
    VERSION = "baidu-v1"

    async def recognize(
        self,
        image: bytes,
        mime: str,
        secret: Mapping[str, Any],
        config: Mapping[str, Any],
    ) -> dict:
        api_key = _require(secret, "api_key", self.kind)
        secret_key = _require(secret, "secret_key", self.kind)
        endpoint = str(
            secret.get("endpoint") or config.get("endpoint") or BAIDU_DEFAULT_ENDPOINT
        ).strip()
        ocr_api = str(
            config.get("ocr_api") or secret.get("ocr_api") or BAIDU_DEFAULT_OCR_API
        ).strip()
        timeout = float(config.get("timeout") or DEFAULT_TIMEOUT)

        payload = _base64_payload(image)
        if not payload:
            raise OcrEngineError("baidu engine requires non-empty image bytes")

        token = await _baidu_access_token(endpoint, api_key, secret_key, timeout)
        body = {
            "image": payload,
            "detect_direction": True,
            "paragraph": False,
        }
        url = f"{endpoint.rstrip('/')}/rest/2.0/ocr/v1/{ocr_api}"
        async with _async_http_client(timeout) as client:
            # 百度 v1 OCR 接口文档指定的 Content-Type 是
            # application/x-www-form-urlencoded（不是 JSON body）；发 JSON 时
            # 真机可能回 error_code 216202（参数错误），所以按官方口径发表单。
            response = await client.post(
                url,
                params={"access_token": token},
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

        data = _ok_json(response, self.kind)
        error_code = data.get("error_code")
        if error_code is not None:
            # 110/111 是 token 失效/过期：把缓存条目丢掉，下次调用自动换新的。
            if str(error_code) in {"110", "111"}:
                _baidu_drop_token(endpoint, api_key, secret_key)
            raise OcrEngineError(
                f"baidu engine rejected the request: error_code={error_code} "
                f"error_msg={_truncate(data.get('error_msg'))}"
            )

        return self._normalize(data)

    @staticmethod
    def _normalize(data: Mapping[str, Any]) -> dict:
        lines: list[dict] = []
        for item in data.get("words_result") or []:
            if not isinstance(item, Mapping):
                continue
            # ``location`` 原样保留（{"left","top","width","height"}），按模块约定
            # 不转换成统一 box 形状。
            lines.append(_line(item.get("words", ""), None, _plain(item.get("location"))))
        raw = {
            "words_result_num": data.get("words_result_num"),
            "direction": data.get("direction"),
        }
        return _result(lines=lines, engine=KIND_BAIDU, raw=raw)


# ---------------------------------------------------------------------------
# Groq（OpenAI 兼容 chat/completions，视觉模型）
# ---------------------------------------------------------------------------

# 提示词固定中文：线上主力是中文附件，用中文指令能让模型对「逐行、不改写」
# 更稳（英文提示词在中文票据上容易出现整段摘要）。
GROQ_PROMPT = (
    "你是 OCR 引擎，请把图片里的文字逐行提取出来。要求：\n"
    "1. 每行一个对象，text 是该行的文字内容，原样保留，不要翻译、改写、合并或补全。\n"
    "2. box 是该行文字在图片中的绝对像素坐标 [x1, y1, x2, y2]，"
    "x1,y1 是左上角，x2,y2 是右下角；不要输出归一化坐标。\n"
    "3. 保持阅读顺序：从上到下，同一行从左到右。\n"
    "4. 只输出 JSON，不要解释。图中没有文字时返回 {\"lines\": []}。"
)

# 智谱的提示词与 Groq 有一处刻意的不同：**不要坐标**。
# 智谱官方文档里没有 grounding / 行级 bbox 契约（全文检索零命中），向模型要坐标
# 只会得到一组看起来合理、实际对不上的数字，前端按它画高亮会画到错位置。
# 所以这里只要 text，box 一律 None —— 「没有」比「猜的」诚实。
ZHIPU_PROMPT = (
    "你是 OCR 引擎，请把图片里的文字逐行提取出来。要求：\n"
    "1. 只输出一个 JSON 对象，形如 {\"lines\":[{\"text\":\"第一行\"}]}，"
    "不要 Markdown 代码块，不要任何解释。\n"
    "2. 每行一个对象，text 是该行的文字，原样保留，不要翻译、改写、合并或补全。\n"
    "3. 保持阅读顺序：从上到下，同一行从左到右。\n"
    "4. 图中没有文字时输出 {\"lines\":[]}。\n"
    "5. 不要输出任何坐标字段。"
)

# ``strict: true`` 要求所有字段都进 ``required`` 且 ``additionalProperties: false``，
# 否则 Groq 会直接 400。box 用 items:number + minItems/maxItems 而不是 prefixItems：
# 兼容性最好，长度约束一样硬。
GROQ_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "box": {
                        "type": "array",
                        "items": {"type": "number"},
                        "minItems": 4,
                        "maxItems": 4,
                    },
                },
                "required": ["text", "box"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["lines"],
    "additionalProperties": False,
}


def _json_object_from_text(text: str) -> dict | None:
    """模型输出 → dict；顺手剥掉 ```` ```json ```` 围栏。

    ``strict`` 模式下理论上只有裸 JSON，但上游换模型/降级时偶尔会带上围栏
    或前导空白，剥一层比在这里报「解析失败」有用。
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        parts = cleaned.split("```")
        if len(parts) >= 2:
            cleaned = parts[1].strip()
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:].strip()
    try:
        parsed = json.loads(cleaned)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _retry_after_hint(response: Any) -> float | None:
    """从 429 响应里取「建议等多久」：优先 ``retry-after`` 头，其次错误体文案。

    Groq 的错误体形如 ``... Please try again in 59.4s.``；智谱的错误体只有中文
    message 且官方未文档化 ``Retry-After``，所以多数情况返回 ``None``——调用方
    按固定退避处理，不自己编一个数。
    """
    headers = getattr(response, "headers", None) or {}
    try:
        raw_header = headers.get("retry-after")
    except Exception:  # noqa: BLE001 - 测试里的假响应未必是 mapping
        raw_header = None
    if raw_header not in (None, ""):
        try:
            return float(str(raw_header).strip())
        except (TypeError, ValueError):
            pass
    text = getattr(response, "text", "") or ""
    match = re.search(r"try again in\s*([0-9.]+)\s*s", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _error_code(response: Any) -> str | None:
    """取上游错误体里的业务码（智谱 ``{"error":{"code":"1302"}}``）。

    只用于把「账户并发超限(1302)」和「平台过载(1305)」区分到日志里——处置动作
    不同，混成一条 message 之后运维只能靠猜。
    """
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001 - 非 JSON 错误体（网关 HTML 之类）
        return None
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, Mapping):
        return None
    code = error.get("code")
    return None if code is None else str(code)


def _openai_vision_user_content(
    prompt: str, mime_type: str, payload: str
) -> list[dict]:
    """OpenAI 兼容视觉消息体：一段文本 + 一张 base64 data URL 图。"""
    return [
        {"type": "text", "text": prompt},
        {
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{payload}"},
        },
    ]


def _openai_vision_choice(data: dict, kind: str, max_tokens: int) -> tuple[Any, Any]:
    """取 ``choices[0]``，并把「输出触顶」变成硬失败。

    OpenAI 兼容系（Groq / 智谱）在输出触顶时都返回 **HTTP 200 +**
    ``finish_reason="length"``：内容被砍掉一截，调用方却看不出任何错误。实测
    一张 38 行的正文页在 Groq 上只回 25 行且伴随改写——这种「成功但残缺」必须
    在归一化之前拦住，绝不放行到上层。
    """
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise OcrEngineError(f"{kind} engine returned no choices")
    first = choices[0]
    finish_reason = first.get("finish_reason") if isinstance(first, Mapping) else None
    if str(finish_reason or "").strip() == "length":
        raise OcrEngineError(
            f"{kind} engine output was truncated at max_tokens={max_tokens} "
            "(finish_reason=length); the page is too dense for one request, "
            "split the image or use another engine instead of trusting partial lines"
        )
    return first, finish_reason


def _openai_vision_lines(
    content: str, kind: str, *, allow_box: bool = True
) -> list[dict]:
    """把模型输出的 JSON 正文归一化成行列表。

    ``allow_box=False`` 用于**没有坐标契约**的上游（智谱）：即使模型自己塞了一组
    box，也一律丢弃。编出来的坐标比没有坐标更危险——前端会按它画高亮，画到错的
    位置比不画更误导。
    """
    parsed = _json_object_from_text(content)
    if parsed is None:
        raise OcrEngineError(
            f"{kind} engine returned unparsable content: {_truncate(content)}"
        )
    items = parsed.get("lines")
    if not isinstance(items, list):
        raise OcrEngineError(f"{kind} engine response is missing the 'lines' array")

    lines: list[dict] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        text = item.get("text")
        box = item.get("box")
        keep_box = allow_box and isinstance(box, (list, tuple))
        lines.append(
            _line(
                "" if text is None else text,
                None,
                _plain(box) if keep_box else None,
            )
        )
    return lines


class GroqOcrEngine:
    """Groq Chat Completions + 视觉模型，定位是**人工效果对照档**，非主力链路。

    免费层额度（RPM/RPD/TPM）撑不起生产流量，且不支持 logprobs（传了直接 400），
    逐行置信度拿不到，所以 ``confidence`` 一律 ``None``。

    因此这里**不做任何自动质量仲裁**：不因为行数偏少、box 越界、文本超长就重跑
    或切别的引擎——「哪个引擎读得更准」由人对着同一张图比。任何自动化判断都只是
    把模型的偏差换成代码的偏差。
    """

    kind = KIND_GROQ
    VERSION = "groq-openai-compatible"

    async def recognize(
        self,
        image: bytes,
        mime: str,
        secret: Mapping[str, Any],
        config: Mapping[str, Any],
    ) -> dict:
        api_key = _require(secret, "api_key", self.kind)
        base_url = str(
            secret.get("base_url") or config.get("base_url") or GROQ_DEFAULT_BASE_URL
        ).strip().rstrip("/")
        # model 必须来自 config：Groq 的视觉模型下线/改名没有过渡期。
        model = str(config.get("model") or GROQ_DEFAULT_MODEL).strip()
        timeout = float(config.get("timeout") or DEFAULT_TIMEOUT)
        reasoning_effort = config.get("reasoning_effort", GROQ_DEFAULT_REASONING_EFFORT)
        reasoning_effort = "" if reasoning_effort is None else str(reasoning_effort).strip()
        # 免费层 OTPM=1000 按「预期输出」计费：不传 max_tokens 时上游按 1164 估，
        # 直接 429。必须显式传，且默认值就取 1000（免费层硬上限）。
        # 用 ``is None`` 而不是 ``or``：``max_tokens=0`` 是非法值，不该被当成
        # 「没配」静默回落到默认值——那会让人以为配了个很小的上限，实际按 1000 跑。
        raw_max_tokens = config.get("max_tokens")
        try:
            max_tokens = int(
                GROQ_DEFAULT_MAX_TOKENS if raw_max_tokens is None else raw_max_tokens
            )
        except (TypeError, ValueError):
            raise OcrEngineError("groq engine max_tokens must be an integer") from None
        if max_tokens <= 0:
            raise OcrEngineError("groq engine max_tokens must be positive")

        payload = _base64_payload(image)
        if not payload:
            raise OcrEngineError("groq engine requires non-empty image bytes")
        mime_type = str(mime or "").strip()
        if not mime_type and isinstance(image, str) and image.strip().startswith("data:"):
            mime_type = image.split(";", 1)[0][5:]
        mime_type = mime_type or "image/png"

        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [
                {
                    "role": "user",
                    "content": _openai_vision_user_content(
                        GROQ_PROMPT, mime_type, payload
                    ),
                }
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "ocr_lines",
                    "strict": True,
                    "schema": GROQ_RESPONSE_SCHEMA,
                },
            },
        }
        # 非推理模型收到未知字段会 400；想彻底不传就把 config 里设成 ""。
        # 字段名与值域的翻译与 agent 共用一份（services.ai.thinking）。
        body.update(thinking_param.fields(reasoning_effort, {"name": KIND_GROQ}))
        # 不传 temperature：Groq 会把 0 静默改成 1e-8，传了等于没传，还多一个
        # 让人误以为「已锁定确定性」的假象。不传 logprobs：直接 400。

        headers = {"Authorization": f"Bearer {api_key}"}
        async with _async_http_client(timeout) as client:
            response = await client.post(
                f"{base_url}/chat/completions", json=body, headers=headers
            )

        # 429 单独成一类：限流不是「这页读不出来」，退避重试就能过。免费层
        # OTPM=1000 意味着密集页一分钟只能发一次，这个分支在生产里会常态触发。
        if getattr(response, "status_code", 0) == 429:
            retry_after = _retry_after_hint(response)
            wait = f"{retry_after:g}s" if retry_after is not None else "unknown"
            raise OcrEngineRateLimited(
                "groq engine rate limited (retry_after="
                f"{wait}): {_truncate(getattr(response, 'text', ''))}",
                retry_after=retry_after,
            )

        data = _ok_json(response, self.kind)
        first, finish_reason = _openai_vision_choice(data, self.kind, max_tokens)
        message = first.get("message") if isinstance(first, Mapping) else None
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, str) or not content.strip():
            raise OcrEngineError("groq engine returned empty message content")
        lines = _openai_vision_lines(content, self.kind)

        raw = {
            "model": data.get("model") or model,
            "finish_reason": finish_reason,
            "max_tokens": max_tokens,
            "usage": _plain(data.get("usage")),
        }
        return _result(lines=lines, engine=KIND_GROQ, raw=raw)


class ZhipuOcrEngine:
    """智谱 BigModel（OpenAI 兼容 v4）视觉模型，定位**境内免费兜底**。

    选它的理由是额度形态：免费视觉模型没有公开的日/月 token 上限，只按「并发数」
    限流（具体值在控制台），比腾讯/百度的 1000 次/月宽松得多。代价是下面四条，
    每条都写在这里，免得以后有人照着 Groq 的写法「对齐」掉：

    * **没有 json_schema/strict**：官方只支持 ``response_format={"type":
      "json_object"}``，且明说「不能保证返回数据始终合法」。所以 schema 写进
      prompt，解析后再校验 ``lines`` 形状，不合格直接失败——不猜、不补。
    * **没有 bbox 契约**：``box`` 恒 ``None``（见 ``ZHIPU_PROMPT`` 的注释）。
    * **限流按并发**：429 的业务码 1302（账户并发超限）/ 1305（平台过载），官方
      错误体没有 ``Retry-After``。
    * **temperature 不能为 0**：官方区间是 (0, 1)，传 0 不合法；默认 0.6 对 OCR
      偏发散，这里默认 0.1。
    """

    kind = KIND_ZHIPU
    VERSION = "zhipu-openai-compatible"

    async def recognize(
        self,
        image: bytes,
        mime: str,
        secret: Mapping[str, Any],
        config: Mapping[str, Any],
    ) -> dict:
        api_key = _require(secret, "api_key", self.kind)
        base_url = str(
            secret.get("base_url") or config.get("base_url") or ZHIPU_DEFAULT_BASE_URL
        ).strip().rstrip("/")
        # model 必须来自 config：智谱的免费型号换得比文档更新快（4V → 4.1V → 4.6V）。
        model = str(config.get("model") or ZHIPU_DEFAULT_MODEL).strip()
        timeout = float(config.get("timeout") or DEFAULT_TIMEOUT)

        raw_max_tokens = config.get("max_tokens")
        try:
            max_tokens = int(
                ZHIPU_DEFAULT_MAX_TOKENS
                if raw_max_tokens is None
                else raw_max_tokens
            )
        except (TypeError, ValueError):
            raise OcrEngineError("zhipu engine max_tokens must be an integer") from None
        if max_tokens <= 0:
            raise OcrEngineError("zhipu engine max_tokens must be positive")

        raw_attempts = config.get("retry_attempts")
        try:
            attempts = int(
                ZHIPU_DEFAULT_RETRY_ATTEMPTS
                if raw_attempts is None
                else raw_attempts
            )
        except (TypeError, ValueError):
            raise OcrEngineError(
                "zhipu engine retry_attempts must be an integer"
            ) from None
        if attempts < 1:
            raise OcrEngineError("zhipu engine retry_attempts must be positive")

        raw_temperature = config.get("temperature")
        try:
            temperature = float(
                ZHIPU_DEFAULT_TEMPERATURE
                if raw_temperature is None
                else raw_temperature
            )
        except (TypeError, ValueError):
            raise OcrEngineError("zhipu engine temperature must be a number") from None
        if not 0 < temperature < 1:
            # 官方区间 (0,1)：0 和 1 都不合法。本地先拦，别让上游用 400 告诉我们。
            raise OcrEngineError(
                "zhipu engine temperature must be between 0 and 1 (exclusive)"
            )

        payload = _base64_payload(image)
        if not payload:
            raise OcrEngineError("zhipu engine requires non-empty image bytes")
        mime_type = str(mime or "").strip()
        if not mime_type and isinstance(image, str) and image.strip().startswith("data:"):
            mime_type = image.split(";", 1)[0][5:]
        mime_type = mime_type or "image/png"

        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": _openai_vision_user_content(
                        ZHIPU_PROMPT, mime_type, payload
                    ),
                }
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        # json_object 是软约束；万一某个型号不收这个字段，把 config["json_mode"]
        # 设成 false 就退回纯 prompt 约束，而不是让整条链路吃 400。
        if config.get("json_mode", True):
            body["response_format"] = {"type": "json_object"}
        # 4.6V 可开关；换到强制思考的型号时把 config 里设成 "" 来省略该字段。
        body.update(
            thinking_param.fields(
                config.get("thinking", "disabled"), {"name": KIND_ZHIPU}
            )
        )

        headers = {"Authorization": f"Bearer {api_key}"}
        response = None
        for attempt in range(1, attempts + 1):
            async with _async_http_client(timeout) as client:
                response = await client.post(
                    f"{base_url}/chat/completions", json=body, headers=headers
                )
            if getattr(response, "status_code", 0) != 429:
                break

            retry_after = _retry_after_hint(response)
            code = _error_code(response) or "unknown"
            # 1308（使用上限已到）重试无意义；只有瞬态的并发/过载值得再试。
            retryable = code in ZHIPU_RETRYABLE_CODES or code == "unknown"
            if attempt >= attempts or not retryable:
                wait = f"{retry_after:g}s" if retry_after is not None else "unknown"
                raise OcrEngineRateLimited(
                    f"zhipu engine rate limited (code={code}, retry_after={wait}, "
                    f"attempts={attempt}/{attempts}): "
                    f"{_truncate(getattr(response, 'text', ''))}",
                    retry_after=retry_after,
                )
            # 上游给了 Retry-After 就听它的；否则走固定退避表（末项兜底）。
            delay = (
                retry_after
                if retry_after is not None
                else ZHIPU_DEFAULT_RETRY_DELAYS[
                    min(attempt - 1, len(ZHIPU_DEFAULT_RETRY_DELAYS) - 1)
                ]
            )
            _logger.info(
                "zhipu engine transient limit (code=%s), retry %s/%s in %.1fs",
                code,
                attempt,
                attempts - 1,
                delay,
            )
            await anyio.sleep(delay)

        data = _ok_json(response, self.kind)
        first, finish_reason = _openai_vision_choice(data, self.kind, max_tokens)
        message = first.get("message") if isinstance(first, Mapping) else None
        content = message.get("content") if isinstance(message, Mapping) else None
        if not isinstance(content, str) or not content.strip():
            raise OcrEngineError("zhipu engine returned empty message content")
        # allow_box=False：智谱没有坐标契约，模型自报的 box 一律丢弃。
        lines = _openai_vision_lines(content, self.kind, allow_box=False)

        raw = {
            "model": data.get("model") or model,
            "finish_reason": finish_reason,
            "max_tokens": max_tokens,
            "request_id": data.get("request_id"),
            "usage": _plain(data.get("usage")),
        }
        return _result(lines=lines, engine=KIND_ZHIPU, raw=raw)


# ---------------------------------------------------------------------------
# 上游地址的私网校验（写入口）
# ---------------------------------------------------------------------------
#
# 哪些字段是「用户可写的上游地址」：阿里/百度的 ``endpoint``（Tea SDK 的 endpoint
# 常常不带 scheme）、Groq/智谱的 ``base_url``。腾讯按 region 由 SDK 拼地址，没有
# 可写地址。
_ENDPOINT_FIELDS = ("base_url", "endpoint")


def _endpoint_hosts(source: object) -> list[tuple[str, str]]:
    if not isinstance(source, Mapping):
        return []
    hosts: list[tuple[str, str]] = []
    for field in _ENDPOINT_FIELDS:
        value = source.get(field)
        host = credentials.host_of_url(value)
        if host:
            hosts.append((field, host))
    return hosts


async def blocked_endpoint(*sources: object) -> str | None:
    """返回第一个指向私网的地址字段名；都干净则 ``None``。

    写入口（``POST/PATCH /api/ai/ocr-engines``）用它把「把 OCR 引擎指向内网」挡在
    落库之前：识别请求由**服务端**发出且带管理员凭证，等于一台可被 admin 驱使的
    内网跳板，与 ``ai_credentials.base_url`` 是同一类风险。
    """
    for source in sources:
        for field, host in _endpoint_hosts(source):
            if await credentials.host_is_private(host):
                return field
    return None


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

ENGINES: dict[str, type] = {
    KIND_TENCENT: TencentOcrEngine,
    KIND_ALIYUN: AliyunOcrEngine,
    KIND_BAIDU: BaiduOcrEngine,
    KIND_GROQ: GroqOcrEngine,
    KIND_ZHIPU: ZhipuOcrEngine,
}


__all__ = [
    "ENGINES",
    "ENGINE_KINDS",
    "KIND_TENCENT",
    "KIND_ALIYUN",
    "KIND_BAIDU",
    "KIND_GROQ",
    "KIND_ZHIPU",
    "TENCENT_DEFAULT_LANGUAGE",
    "ALIYUN_DEFAULT_ENDPOINT",
    "BAIDU_DEFAULT_ENDPOINT",
    "BAIDU_DEFAULT_OCR_API",
    "BAIDU_TOKEN_REFRESH_MARGIN",
    "GROQ_DEFAULT_BASE_URL",
    "GROQ_DEFAULT_MODEL",
    "GROQ_DEFAULT_REASONING_EFFORT",
    "GROQ_DEFAULT_MAX_TOKENS",
    "ZHIPU_DEFAULT_BASE_URL",
    "ZHIPU_DEFAULT_MODEL",
    "ZHIPU_DEFAULT_MAX_TOKENS",
    "ZHIPU_DEFAULT_TEMPERATURE",
    "DEFAULT_TIMEOUT",
    "OcrEngineError",
    "OcrEngineRateLimited",
    "blocked_endpoint",
    "TencentOcrEngine",
    "AliyunOcrEngine",
    "BaiduOcrEngine",
    "GroqOcrEngine",
    "ZhipuOcrEngine",
]
