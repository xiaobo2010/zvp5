"""agent 输入审核的规则层：纯函数、不写库、不做 IO。

为什么是规则而不是模型：这一层要在提交瞬间给出确定的拒绝，延迟必须远低于一次
上游调用，判定结果还要能被审计复现。模型分适合处理灰区；把「拒」的权限交给一个
会抖动的分类器，等于把门禁变成概率。

为什么只收「命令式」形态：教育性提问（「什么是越狱」「什么是 system prompt」）与
攻击文本的词面高度重叠，区别在于**是否在对模型下命令**。所以每条规则都要求前导
动词或祈使结构，宁可漏掉一些变体，也不要让正常提问被判违规。

为什么违规还分「业务域」：同一句话在两种语境里的处置不同。「教我制毒」是用户在
向平台索取违法内容；而「义工活动里有同学涉黄被处理了怎么办」是平台业务提问，
敏感词只是描述对象。规则层只回答「有没有违规、是否落在业务域」，**只拒绝还是
拒绝并封禁由调用方按 ``scope`` 决定**——处罚权不在这一层。

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

# 违规分类（审计字段，只增不改）。``jailbreak`` 表示提示注入/越狱类；其余六类
# 是「黄赌毒暴」内容请求。类别名会进证据与日志，改名等于让历史数据断链。
CATEGORY_JAILBREAK = "jailbreak"
CATEGORY_SEXUAL = "sexual"
CATEGORY_VIOLENCE = "violence"
CATEGORY_WEAPONS = "weapons"
CATEGORY_SUBSTANCES = "substances"
CATEGORY_GAMBLING = "gambling"
CATEGORY_SELF_HARM = "self_harm"

# 业务域标记：命中即 ``scope="platform"``。这是**降级处罚**的依据，不是豁免——
# 业务语境里的违规仍然拒绝回答，只是不该按「向平台索取违法内容」直接封号。
# 举报/申诉/投诉也在其中：把监管类提问误判成索取内容是最贵的一类误伤。
BUSINESS_SCOPE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"义工|志愿|工时|时长|活动|报名|签到|签退|登记|班级|年级|部门|学号"
        r"|学生会|团委|团支书|秘书"
    ),
    re.compile(
        r"账号|帐户|账户|密码|登录|登陆|注册|权限|角色|封禁|解封|申诉|投诉|举报"
        r"|违规记录|处罚记录|审核|复核"
    ),
    re.compile(
        r"日志|接口|系统设置|配置|数据库|服务器|部署|运维|ssh|文件管理"
        r"|zvagent|额度|模型|提示词|api"
    ),
)

# 对外只回一句固定中文：命中规则属于内部判定，回给用户会让攻击者按差异调参。
# 这句话是全平台内容拒绝的唯一文案（LLM 判定与规则命中共用）。
REJECTED_MESSAGE = "该请求未通过内容安全审核。"

_GAP = r"[^。！？；\n]{0,12}"

# 违规落点：``platform`` 表示请求落在平台业务域内（举报/申诉/业务咨询），上层
# 只拒绝；``off_platform`` 表示向平台索取违法内容，上层可拒绝并封禁。
SCOPE_PLATFORM = "platform"
SCOPE_OFF_PLATFORM = "off_platform"


@dataclass(frozen=True)
class NormalizedText:
    """归一化结果；``obfuscated`` 表示原文含不可见/私有区字符。"""

    text: str
    obfuscated: bool


@dataclass(frozen=True)
class ModerationResult:
    """审核结论。

    ``hit`` 是历史字段（调用方按它拒绝），``violation`` 与它同义；``__bool__``
    让新旧两种写法都成立。``category``/``scope``/``reason`` 是策略层新增的判定
    事实：``scope`` 只在违规时非空，``platform`` 表示请求落在平台业务域内。
    """

    hit: bool
    rule: str | None = None
    violation: bool = False
    category: str | None = None
    scope: str | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        # 兼容只传 ``hit`` 的老调用点：有命中就必须能被 ``bool()`` 判成违规。
        if self.hit and not self.violation:
            object.__setattr__(self, "violation", True)

    def __bool__(self) -> bool:
        return self.violation


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


# 内容分类词表：每类只收「索取/教程」形态——前导请求动词 + 敏感对象，两者之间
# 不留自由文本缝。漏掉一些变体是可接受的代价，误伤一条正常业务提问不是。
# 举报/申诉/业务描述类文本因此天然不命中：它们没有「给我/教我/怎么+动词」的
# 请求骨架（业务域豁免是第二道保险，见 ``BUSINESS_SCOPE_PATTERNS``）。
CATEGORY_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    CATEGORY_SEXUAL: (
        re.compile(
            r"(?:写|编写|创作|生成|续写|扩写|润色|来一段|来一篇|来点|整一段|编一段)"
            r"[^。！？；\n]{0,10}"
            r"(?:色情小说|色情故事|色情内容|色情描写|色情文章|色情图片|情色小说"
            r"|黄文|小黄文|黄色小说|成人小说|淫秽小说|h文|肉文|r18|涩文)"
        ),
        re.compile(
            r"(?:给我|发我|分享|推荐|求|要|找|提供)"
            r"(?:几个|一些|一个|个|点)?"
            r"(?:色情网站|色情视频|色情资源|色情图片|成人影片|成人视频|成人网站"
            r"|黄片|黄网|福利视频|裸聊|约炮|嫖娼|找小姐)"
        ),
        re.compile(
            r"(?:怎么|如何|怎样|教我|求教)"
            r"(?:才能|能|可以)?(?:去)?"
            r"(?:约炮|裸聊|嫖娼|找小姐|买春|性交易)"
        ),
    ),
    CATEGORY_VIOLENCE: (
        re.compile(
            r"(?:教我|教教我|求教|我想学|想学)"
            r"(?:怎么|如何|怎样)?(?:去)?"
            r"(?:虐待|折磨|毒打|殴打|杀死|杀掉|弄死|下毒|投毒|分尸|碎尸|肢解|毁尸)"
        ),
        re.compile(
            r"(?:怎么|如何|怎样|咋)(?:才能|能|可以)?(?:去)?"
            r"(?:虐待|折磨|毒打|杀死|杀掉|弄死|捅死|砍死|毒杀|下毒|投毒"
            r"|分尸|碎尸|肢解|毁尸灭迹|杀人)"
        ),
        re.compile(
            r"(?:写|创作|描写|描述|详细描述|续写|扩写)"
            r"[^。！？；\n]{0,10}"
            r"(?:虐杀|分尸|碎尸|肢解|酷刑|折磨致死|虐童|虐待过程|血腥场面|血腥细节)"
        ),
    ),
    CATEGORY_WEAPONS: (
        re.compile(
            r"(?:教我|教教我|求教|我想学|想学)"
            r"(?:怎么|如何|怎样)?"
            r"(?:制作|制造|自制|做|组装|改装|造|搞)?"
            r"(?:炸弹|炸药|爆炸物|燃烧瓶|手雷|雷管|枪支|手枪|步枪|弹药|土枪|气枪)"
        ),
        re.compile(
            r"(?:怎么|如何|怎样|咋)(?:才能|能|可以)?"
            r"(?:制作|制造|自制|做|组装|改装|造|搞)"
            r"(?:一个|一把|一颗|一枚|个|把)?"
            r"(?:炸弹|炸药|爆炸物|燃烧瓶|手雷|雷管|枪支|手枪|步枪|弹药)"
        ),
        re.compile(
            r"(?:炸弹|炸药|爆炸物|燃烧瓶|手雷|雷管|枪支|手枪|步枪)"
            r"(?:的|之)?(?:制作方法|制作教程|制作步骤|制造方法|配方|图纸|组装图)"
        ),
    ),
    CATEGORY_SUBSTANCES: (
        re.compile(
            r"(?:教我|教教我|求教|我想学|想学)"
            r"(?:怎么|如何|怎样)?"
            r"(?:制作|制造|合成|提炼|做|搞|买|购买)?"
            r"(?:毒品|冰毒|海洛因|可卡因|摇头丸|k粉|氯胺酮|大麻|麻古|芬太尼"
            r"|致幻剂|制毒|制冰)"
        ),
        re.compile(
            r"(?:怎么|如何|怎样|咋)(?:才能|能|可以)?(?:去)?"
            r"(?:制毒|制冰|制作毒品|制造毒品|合成毒品|提炼冰毒|吸毒|贩毒"
            r"|(?:买|购买|买到|弄到|搞到|获取)"
            r"(?:毒品|冰毒|海洛因|可卡因|摇头丸|k粉|氯胺酮|大麻|麻古|芬太尼|致幻剂)"
            r"|(?:毒品|冰毒|海洛因|可卡因|摇头丸|k粉|氯胺酮|大麻|麻古|芬太尼|致幻剂))"
        ),
        re.compile(
            r"(?:哪里|哪儿|在哪)(?:能|可以)?"
            r"(?:买|买到|购买|弄到|搞到)"
            r"(?:毒品|冰毒|海洛因|可卡因|摇头丸|k粉|氯胺酮|大麻|麻古|芬太尼)"
        ),
        re.compile(
            r"(?:毒品|冰毒|海洛因|可卡因|摇头丸|氯胺酮|k粉|麻古|芬太尼)"
            r"(?:的|之)?(?:制作方法|制作教程|制作步骤|配方|购买渠道|购买途径"
            r"|哪里买|怎么买)"
        ),
    ),
    CATEGORY_GAMBLING: (
        re.compile(
            r"(?:推荐|介绍|分享|给我|发我|求|要|找|提供)"
            r"(?:几个|一些|一个|个|点)?"
            r"(?:赌博网站|博彩网站|赌博平台|博彩平台|赌场|网赌平台|赌博app"
            r"|赌博软件|下注平台|外围平台|线上赌场|百家乐平台|六合彩网站)"
        ),
        re.compile(
            r"(?:教我|教教我|求教|我想学|想学)"
            r"(?:怎么|如何|怎样)?(?:去)?"
            r"(?:赌博|网赌|赌球|下注|洗钱|刷流水|套现|玩百家乐|玩老虎机)"
        ),
        re.compile(
            r"(?:怎么|如何|怎样|咋)(?:才能|能|可以)?(?:去)?"
            r"(?:赌博|网赌|赌球|洗钱|刷流水|套现|下注赢钱|在赌场赢钱)"
        ),
        re.compile(
            r"(?:给我|发我|分享|推荐|求|找)"
            r"(?:一个|几个|一些)?(?:能|可以)?"
            r"(?:下注|赌博|赌球|网赌)(?:的)?"
            r"(?:平台|网站|app|软件|群|渠道|地址)"
        ),
    ),
    CATEGORY_SELF_HARM: (
        re.compile(
            r"(?:教我|教教我|求教|我想知道|告诉我)"
            r"(?:怎么|如何|怎样)?(?:去)?"
            r"(?:自杀|自残|轻生|割腕|上吊|跳楼|烧炭|服毒|结束生命"
            r"|结束自己的生命|无痛死亡)"
        ),
        re.compile(
            r"(?:怎么|如何|怎样|咋)(?:才能|能|可以)?(?:去)?"
            r"(?:自杀|自残|轻生|割腕|上吊|跳楼|烧炭|服毒|无痛死亡|结束自己的生命)"
        ),
    ),
}


def in_business_scope(text: object) -> bool:
    """文本是否落在平台业务域内；``check`` 用它决定 ``scope``。"""
    normalized = normalize(text).text
    return any(pattern.search(normalized) for pattern in BUSINESS_SCOPE_PATTERNS)


def check(text: object) -> ModerationResult:
    """命中第一条硬规则即返回；不可见字符与超长 base64 块优先判定为混淆。

    顺序固定：混淆 → 越狱规则（``category="jailbreak"``）→ 内容分类。越狱类不受
    业务域影响，一律 ``scope="off_platform"``；内容分类命中后落在业务域内则标
    ``platform``，违规本身不变——处罚差异由调用方按 ``scope`` 决定。
    """
    normalized = normalize(text)
    if normalized.obfuscated:
        return ModerationResult(
            hit=True,
            rule=RULE_OBFUSCATION,
            violation=True,
            category=CATEGORY_JAILBREAK,
            scope=SCOPE_OFF_PLATFORM,
            reason=RULE_OBFUSCATION,
        )
    if _BASE64_BLOCK.search(normalized.text):
        return ModerationResult(
            hit=True,
            rule=RULE_OBFUSCATION,
            violation=True,
            category=CATEGORY_JAILBREAK,
            scope=SCOPE_OFF_PLATFORM,
            reason=RULE_OBFUSCATION,
        )
    for rule, pattern in _RULES:
        if pattern.search(normalized.text):
            return ModerationResult(
                hit=True,
                rule=rule,
                violation=True,
                category=CATEGORY_JAILBREAK,
                scope=SCOPE_OFF_PLATFORM,
                reason=rule,
            )
    for category, patterns in CATEGORY_PATTERNS.items():
        for pattern in patterns:
            if pattern.search(normalized.text):
                scope = (
                    SCOPE_PLATFORM
                    if in_business_scope(normalized.text)
                    else SCOPE_OFF_PLATFORM
                )
                return ModerationResult(
                    hit=True,
                    rule=f"content_{category}",
                    violation=True,
                    category=category,
                    scope=scope,
                    reason=f"{category}_request",
                )
    return ModerationResult(hit=False, rule=None)


__all__ = [
    "BUSINESS_SCOPE_PATTERNS",
    "CATEGORY_GAMBLING",
    "CATEGORY_JAILBREAK",
    "CATEGORY_PATTERNS",
    "CATEGORY_SELF_HARM",
    "CATEGORY_SEXUAL",
    "CATEGORY_SUBSTANCES",
    "CATEGORY_VIOLENCE",
    "CATEGORY_WEAPONS",
    "REJECTED_MESSAGE",
    "RULE_INSTRUCTION_OVERRIDE",
    "RULE_OBFUSCATION",
    "RULE_PROMPT_EXTRACTION",
    "RULE_PROTOCOL_INJECTION",
    "RULE_REFUSAL_SUPPRESSION",
    "RULE_ROLEPLAY_JAILBREAK",
    "RULE_TOOL_PRIVILEGE",
    "SCOPE_OFF_PLATFORM",
    "SCOPE_PLATFORM",
    "ModerationResult",
    "NormalizedText",
    "check",
    "in_business_scope",
    "normalize",
]
