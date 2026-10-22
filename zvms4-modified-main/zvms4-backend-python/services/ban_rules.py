"""规则封禁的公共积木：配置解析、处罚梯度、落封禁（不降级）。

为什么只剩「积木」：规则的**求值**已经搬到各自的入口
（``services.device_guard`` 的设备闸、``services.violation_guard`` 的上报闸），
因为三条规则用的证据完全不同（设备号 / 登录流水 / 客户端信号），硬塞进一个
函数只会让参数表变成一堆「这条规则用不到」的占位符。本模块只保留三者共用的
部分，算法只有一份，梯度不会各自漂移。

fail-open 原则仍在：配置读不到就回落默认值（``rule_config`` /
``rule_penalties``），判封禁是「额外保护」，不能变成登录或上报的单点故障。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from db import backends
from util.ban import ban_outranks

_logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {"threshold": 3, "window_hours": 24}
#: 检查项注册表（``routers.ban_router.TRIGGERS``）给的表单初值必须是同一份数值；
#: 两者曾出现 3/24 与 2/1 的冲突，同一件事有了两种「默认」。
REGISTRY_DEFAULT_CONFIG = {"threshold": 3, "window_hours": 24}
DEFAULT_PENALTIES = [1]


def _json_value(raw, fallback):
    """``CONFIG`` / ``PENALTIES`` 是 DB 只做 ``IS JSON`` 的文本，解析归本层。"""
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


def _positive_int(value) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def normalize_config(raw, defaults: dict | None = None) -> dict:
    """把任意形状的规则配置归一成完整的 ``{threshold, window_hours}``。

    这是**读取侧的唯一归一入口**：注册表默认值、V016 的 ``{"threshold":3}``
    种子、管理员只改了一个键的配置，都必须能在这里补成完整形状，判定层才不必
    各写一份「缺项怎么办」。``defaults`` 允许调用方（注册表）传入自己的基线，
    缺项与非法值都落回基线。
    """
    base = dict(defaults or DEFAULT_CONFIG)
    if not isinstance(raw, dict):
        if raw is not None:
            _logger.warning("规则 config 不是对象，回退默认值：%r", raw)
        return base
    threshold = _positive_int(raw.get("threshold"))
    window_hours = _positive_int(raw.get("window_hours"))
    if threshold is None and "threshold" in raw:
        _logger.warning("规则 config threshold 非法，回退默认值：%r", raw)
    if window_hours is None and "window_hours" in raw:
        _logger.warning("规则 config window_hours 非法，回退默认值：%r", raw)
    return {
        "threshold": threshold or base["threshold"],
        "window_hours": window_hours or base["window_hours"],
    }


def rule_config(rule: dict | None) -> dict:
    """规则的 ``{threshold, window_hours}``；缺项/坏数据**逐项**回退默认值。

    逐项归一而不是整体丢弃：规则的 config 是文本 JSON，历史上出现过只有
    ``{"threshold":3}`` 的形状（V016 的 seed、以及注册表默认值被原样写库的
    情况）。整体丢弃会把管理员已调过的 threshold 悄悄改回默认值，逐项合并则
    只补缺失项——注册表给的默认值因此不会「半份配置」直接进判定层。
    """
    return normalize_config(_json_value((rule or {}).get("config"), None))


def rule_penalties(rule: dict | None) -> list[int | None]:
    """天数梯度；空/非正数的坏数据回退 ``[1]``（永不因为配置坏而不处罚）。

    ``None`` 是「永久封禁」的档位，**只在末位有意义**：读到 ``None`` 就到此为止
    （后面的档位永远轮不到），这样 ``[1,3,7,14,30,null]`` 与 ``[1,3,null,9]``
    在语义上一致，调用方不必自己防御「中间夹 null」的坏配置。
    """
    raw = _json_value((rule or {}).get("penalties"), None)
    if not isinstance(raw, list):
        return list(DEFAULT_PENALTIES)
    steps: list[int | None] = []
    for item in raw:
        if item is None:
            steps.append(None)
            break
        value = _positive_int(item)
        if value is not None:
            steps.append(value)
    return steps or list(DEFAULT_PENALTIES)


def penalty_expiry(
    penalties: list[int | None],
    offense: int,
    at: datetime,
) -> tuple[datetime | None, int | None]:
    """第 ``offense`` 次处罚的 ``(到期时刻, 天数)``；末档 ``None`` → 永久。

    ``offense`` 超出梯度长度时取末档（与旧行为一致）；返回 ``(None, None)``
    表示永久封禁——调用方据此写 ``permanent=True`` 且 ``expires_at=None`` 的流水。
    抽成函数是为了让「临时密码连错」「平台违规使用」共用同一套梯度算法，
    每条规则各写一遍必然漂移。
    """
    if not penalties:
        penalties = list(DEFAULT_PENALTIES)
    index = min(max(int(offense), 1) - 1, len(penalties) - 1)
    step = penalties[index]
    if step is None:
        return None, None
    return at + timedelta(days=step), step


async def set_ban_unless_stronger(
    user_oid: str,
    *,
    reason: str,
    until: datetime | None,
    rule_code: str,
) -> datetime | None:
    """按规则落封禁，但**不降级**已有的更强封禁；返回库里实际生效的 ``until``。

    只加刑不减刑是自动规则该有的姿态：管理员落的永久封禁不该被梯度首档改成
    几天。被保留时写一条日志留痕，封禁流水仍由调用方照常落库。
    """
    repo = backends.get_users_repo()
    current = await repo.get_by_oid(user_oid)
    if ban_outranks(current, until):
        _logger.warning(
            "用户 %s 已有更强封禁（banned=%s, ban_until=%s），保留既有封禁，"
            "本次规则 %s 计算出的 until=%s 不写入",
            user_oid,
            (current or {}).get("banned"),
            (current or {}).get("ban_until"),
            rule_code,
            until,
        )
        return (current or {}).get("ban_until")

    await repo.set_ban(
        user_oid,
        reason=reason,
        until=until,
        source="rule",
        rule_code=rule_code,
    )
    return until


async def get_rule_by_trigger(trigger: str) -> dict | None:
    """按**检查项**取规则：求值入口只认 trigger，不认管理员起的名字。

    读表失败照旧冒泡给调用方——这里不吞异常，fail-open 由各入口自己决定
    （登录放行、设备闸回落默认上限），两处的「放行」语义并不相同。
    """
    return await backends.get_ban_rules_repo().get_by_trigger(trigger)


__all__ = [
    "DEFAULT_CONFIG",
    "REGISTRY_DEFAULT_CONFIG",
    "DEFAULT_PENALTIES",
    "normalize_config",
    "rule_config",
    "rule_penalties",
    "penalty_expiry",
    "set_ban_unless_stronger",
    "get_rule_by_trigger",
]
