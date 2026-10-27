"""agent 输入审核的规则层：纯函数、不写库、不做 IO。

为什么是规则而不是模型：这一层要在提交瞬间给出确定的拒绝，延迟必须远低于一次
上游调用，判定结果还要能被审计复现。模型分适合处理灰区；把「拒」的权限交给一个
会抖动的分类器，等于把门禁变成概率。

为什么只收「命令式」形态：教育性提问（「什么是越狱」「什么是 system prompt」）与
攻击文本的词面高度重叠，区别在于**是否在对模型下命令**。所以每条规则都要求前导
动词或祈使结构，宁可漏掉一些变体，也不要让正常提问被判违规。

命中之后做什么由调用方决定（拒绝提交 / 记违规信号 / 封禁梯度），本模块不感知
封禁，也不决定处罚。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# 零宽字符与 Unicode tag：肉眼不可见，但会改变模型看到的 token 序列，是「同一句
# 话在规则层与被审对象眼里不一样」的经典手法。出现即视为混淆载荷，不做白名单。
_ZERO_WIDTH = frozenset("\u200b\u200c\u200d\u200e\u200f\u2060\ufeff")
_TAG_START = 0xE0000
_TAG_END = 0xE007F

# 全角 ASCII（U+FF01..U+FF5E）与半角相差固定偏移。
_FULLWIDTH_OFFSET = 0xFEE0

# ≥120 个 base64 字符：正常中文提问不会出现这种连续块，长密钥/载荷才会。
_BASE64_BLOCK = re.compile(r"[A-Za-z0-9+/=]{120,}")

# 规则名是审计字段：改名会让历史数据与代码对不上，只能加不能改。
RULE_INSTRUCTION_OVERRIDE = "instruction_override"
RULE_PROMPT_EXTRACTION = "prompt_extraction"
RULE_PROTOCOL_INJECTION = "protocol_injection"
RULE_ROLEPLAY_JAILBREAK = "roleplay_jailbreak"
RULE_REFUSAL_SUPPRESSION = "refusal_suppression"
RULE_TOOL_PRIVILEGE = "tool_privilege"
RULE_OBFUSCATION = "obfuscation"

# 对外只回一句固定中文：命中规则属于内部判定，回给用户会让攻击者按差异调参。
REJECTED_MESSAGE = "该请求已被内容审核拦截。"

_GAP = r"[^。！？；\n]{0,12}"


@dataclass(frozen=True)
class NormalizedText:
    """归一化结果；``obfuscated`` 表示原文含不可见/私有区字符。"""

    text: str
    obfuscated: bool


@dataclass(frozen=True)
class ModerationResult:
    """审核结论；``rule`` 只在 ``hit`` 时非空。"""

    hit: bool
    rule: str | None = None


def _to_halfwidth(text: str) -> str:
    out: list[str] = []
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:
            out.append(chr(code - _FULLWIDTH_OFFSET))
        else:
            out.append(ch)
    return "".join(out)


def _strip_invisible(text: str) -> tuple[str, bool]:
    out: list[str] = []
    dropped = False
    for ch in text:
        code = ord(ch)
        if ch in _ZERO_WIDTH or _TAG_START <= code <= _TAG_END:
            dropped = True
            continue
        out.append(ch)
    return "".join(out), dropped


def normalize(text: object) -> NormalizedText:
    """NFKC → 全角转半角 → 剥离不可见字符 → 小写 → 空白折叠。

    顺序不能换：先 NFKC 才能让全角/兼容字符落回 ASCII，再剥离不可见字符（NFKC
    不会删它们），最后小写与折叠空白，让英文规则只需写一种词形。
    """
    raw = "" if text is None else str(text)
    folded = unicodedata.normalize("NFKC", raw)
    halfwidth = _to_halfwidth(folded)
    stripped, obfuscated = _strip_invisible(halfwidth)
    return NormalizedText(text=" ".join(stripped.lower().split()), obfuscated=obfuscated)


_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        RULE_INSTRUCTION_OVERRIDE,
        re.compile(
            r"(?:忽略|忽视|无视|不要管|别管|忘记|忘掉|抛开)"
            + _GAP
            + r"(?:之前|以上|前面|上面|先前|原有|原来)"
            + _GAP
            + r"(?:指令|设定|规则|约束|提示)"
            + r"|\b(?:ignore|disregard|forget|override)\b[^\n]{0,40}"
            r"\b(?:previous|prior|above|earlier|preceding)\b[^\n]{0,40}"
            r"\b(?:instructions?|rules?|prompts?|directions?)\b"
        ),
    ),
    (
        RULE_PROMPT_EXTRACTION,
        re.compile(
            r"(?:输出|打印|显示|透露|泄露|泄漏|重复|复述|告诉我|说出|列出)"
            r"[^。！？；\n]{0,12}"
            r"(?:系统提示词?|初始指令|初始设定|系统设定|系统消息|system\s*prompt)"
            + r"|(?:打印|输出|显示|透露|泄露|泄漏|列出)[^。！？；\n]{0,12}"
            r"(?:环境变量|api\s*key|apikey|密钥|秘钥|数据库连接|连接串|数据库密码)"
            + r"|\b(?:show|print|output|reveal|repeat|dump|tell\s+me)\b[^\n]{0,24}"
            r"\b(?:system\s+prompt|initial\s+instructions?|instructions\s+above)\b"
        ),
    ),
    (
        RULE_PROTOCOL_INJECTION,
        re.compile(
            r"<\|im_start\|>|<\|im_end\|>|<system>|</system>"
            r"|(?m:^\s*###\s*(?:instruction|system|assistant)\b)"
            r"|(?m:^\s*assistant\s*:)"
        ),
    ),
    (
        RULE_ROLEPLAY_JAILBREAK,
        re.compile(
            r"\bdan\s+mode\b|\bdo\s+anything\s+now\b|\bdeveloper\s+mode\b"
            r"|(?:进入|开启|启用|打开|扮演|切换到|切换至)[^。！？；\n]{0,12}"
            r"(?:无限制|无约束|开发者模式|越狱模式|dan模式)"
            r"|没有[^。！？；\n]{0,10}(?:道德|安全|伦理)[^。！？；\n]{0,10}"
            r"(?:约束|限制)的[^。！？；\n]{0,8}(?:ai|助手|模型)"
        ),
    ),
    (
        RULE_REFUSAL_SUPPRESSION,
        re.compile(
            r"(?:不要|不许|别|不准)[^。！？；\n]{0,10}(?:说|告诉我|回答|提醒)"
            r"[^。！？；\n]{0,10}(?:不能|不行|不可以|做不到)"
            r"|(?:跳过|绕过|关闭|解除)[^。！？；\n]{0,10}"
            r"(?:安全|内容|合规|道德)?(?:审查|审核|过滤|限制)"
            r"|\bnever\s+say\s+(?:you\s+)?(?:can't|cannot|can\s+not|are\s+unable)\b"
            r"|\b(?:do\s+not|don'?t)\s+refuse\b"
        ),
    ),
    (
        RULE_TOOL_PRIVILEGE,
        re.compile(
            r"(?:给我|帮我|替我|为我把|授予我|赐予我)[^。！？；\n]{0,12}"
            r"(?:admin|管理员|root|超级用户|超级管理员)[^。！？；\n]{0,10}"
            r"(?:权限|身份|角色|账号)"
            r"|(?:提升|获取|拿到|获得|提权到|越权)[^。！？；\n]{0,10}"
            r"(?:权限|admin|root|管理员权限|超级用户)"
            r"|(?:删除|清空|删掉|清除)[^。！？；\n]{0,8}(?:所有|全部|全站)"
            r"[^。！？；\n]{0,8}(?:用户|数据|记录|账号|班级)"
        ),
    ),
    (
        RULE_OBFUSCATION,
        re.compile(
            r"(?:base64|rot13|hex|十六进制)[^。！？；\n]{0,8}"
            r"(?:解码|解密|解出|还原|转换)"
            r"|(?:解码|解密)[^。！？；\n]{0,8}(?:base64|rot13|hex|十六进制)"
            r"|\b(?:decode|decrypt)\b[^\n]{0,16}\b(?:base64|rot13|hex)\b"
        ),
    ),
)


def check(text: object) -> ModerationResult:
    """命中第一条硬规则即返回；不可见字符与超长 base64 块优先判定为混淆。"""
    normalized = normalize(text)
    if normalized.obfuscated:
        return ModerationResult(hit=True, rule=RULE_OBFUSCATION)
    if _BASE64_BLOCK.search(normalized.text):
        return ModerationResult(hit=True, rule=RULE_OBFUSCATION)
    for rule, pattern in _RULES:
        if pattern.search(normalized.text):
            return ModerationResult(hit=True, rule=rule)
    return ModerationResult(hit=False, rule=None)


__all__ = [
    "REJECTED_MESSAGE",
    "RULE_INSTRUCTION_OVERRIDE",
    "RULE_OBFUSCATION",
    "RULE_PROMPT_EXTRACTION",
    "RULE_PROTOCOL_INJECTION",
    "RULE_REFUSAL_SUPPRESSION",
    "RULE_ROLEPLAY_JAILBREAK",
    "RULE_TOOL_PRIVILEGE",
    "ModerationResult",
    "NormalizedText",
    "check",
    "normalize",
]
