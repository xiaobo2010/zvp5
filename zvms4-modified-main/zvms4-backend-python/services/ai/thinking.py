"""思考强度 → 上游请求字段的翻译（agent 与 OCR 共用一份）。

思考强度（``low`` / ``medium`` / ``high``）是**平台口径**，上游没有统一字段：

* OpenAI 兼容系（Groq / OpenAI / 硅基流动等）认 ``reasoning_effort``，值域就是
  这三档，原样发；
* 智谱（BigModel v4）只有开关语义 ``thinking={"type": "enabled"|"disabled"}``，
  没有档位。所以 ``low`` 翻成「关」（这是它最接近「少想一点」的表达），
  ``medium`` / ``high`` 翻成「开」——把没有的档位硬编成一个数字只会让人以为
  调了有用。

``enabled`` / ``disabled`` 这类上游原值直通，这样 OCR 引擎里既有的
``config["thinking"] = "disabled"`` 配置不用改语义。

空串 / ``None`` 一律**不加字段**：那表示「上游默认」，而给不支持该字段的模型塞
一个字段是 400 而不是「用默认值」。
"""

from __future__ import annotations

from typing import Any, Mapping

LEVELS = ("low", "medium", "high")

#: 智谱的档位 → 开关。没列出的档位按「开」处理（能思考总比悄悄关掉安全）。
ZHIPU_TYPES = {"low": "disabled", "medium": "enabled", "high": "enabled"}

#: 判定「是不是智谱」的线索：名字由管理员起，域名才是上游事实。
ZHIPU_HOSTS = ("bigmodel.cn", "zhipuai.cn")
ZHIPU_NAMES = ("zhipu", "glm", "bigmodel")


def normalize(value: object) -> str | None:
    """把外部输入归一成小写档位串；空值回 ``None``（= 不附带字段）。"""
    text = str(value or "").strip().lower()
    return text or None


def is_zhipu(entry: Mapping[str, Any] | None) -> bool:
    """该 provider 是否走智谱的 ``thinking`` 形状。

    先看 base_url 的 host（这是上游事实），再看管理员起的名字（``zhipu`` /
    ``glm`` 这类约定）。两者都认是为了兼容「自建反代把域名换掉」的部署。
    """
    if not isinstance(entry, Mapping):
        return False
    base_url = str(entry.get("base_url") or "").lower()
    if any(host in base_url for host in ZHIPU_HOSTS):
        return True
    name = str(entry.get("name") or "").lower()
    return any(token in name for token in ZHIPU_NAMES)


def fields(value: object, entry: Mapping[str, Any] | None) -> dict[str, Any]:
    """档位 → 要并进上游 body 的字段（空 = 不加任何字段）。"""
    level = normalize(value)
    if level is None:
        return {}
    if is_zhipu(entry):
        # ``enabled`` / ``disabled`` 是上游原值（OCR 的 config 一直这么写），直通；
        # 档位表里没有的其它值按「开」处理（能思考总比悄悄关掉安全）。
        kind = ZHIPU_TYPES.get(level)
        if kind is None:
            kind = level if level in ("enabled", "disabled") else "enabled"
        return {"thinking": {"type": kind}}
    return {"reasoning_effort": level}


def apply(body: dict[str, Any], value: object, entry: Mapping[str, Any] | None) -> dict:
    """就地合并 :func:`fields` 的结果并返回同一个 body（调用方习惯链式用）。"""
    body.update(fields(value, entry))
    return body


__all__ = [
    "LEVELS",
    "ZHIPU_TYPES",
    "apply",
    "fields",
    "is_zhipu",
    "normalize",
]
