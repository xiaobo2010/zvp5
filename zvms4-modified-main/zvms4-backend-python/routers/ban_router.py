"""封禁规则与封禁流水的管理端点（前缀 ``/api/v3/ban``，admin only）。

为什么规则要有管理端点：规则的阈值/时间窗/处罚梯度全部在 ``BAN_RULES`` 里，
改一次就影响整条判定闸门。把它们暴露成「只改 tunables」的接口
（``code`` / ``trigger`` / ``builtin`` 不可改），既能让管理员按学校实际情况
调参，又不会在运行时把求值入口引用的 ``trigger`` 改坏。

管理员可以**新增/删除**规则，但**不能写检查逻辑**：检查项（trigger）由平台
在 :data:`TRIGGERS` 里注册，管理员只能从下拉里选。这是「谁能扩大封禁口径」
的边界——平台提供证据来源，学校决定阈值与梯度。

``CONFIG`` / ``PENALTIES`` 在库里是文本（DB 只保证 ``IS JSON``），解析与校验
都在本层：读路径对坏数据降级（``{}`` / ``[]``），写路径对非法值直接 422，
绝不把坏配置写进库。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from db import backends
from services import device_guard
from typings.log import inject_log
from util.object_id import get_current_user
from util.user import get_user_display_name

router = APIRouter()

# ---------------------------------------------------------------------------
# 检查项注册表
#
# 这是**平台**的能力清单，不是学校的配置：每一项对应一段求值代码（登录闸 /
# 上报闸）。管理员新增规则时只能从这张表里选 trigger，所以「封禁口径」的
# 扩张必须经过一次代码发布，而不是点几下管理界面。
#
# ``config`` 给的是新建规则的默认参数（阈值 / 窗口），前端据此填表单初值。
# ---------------------------------------------------------------------------

TRIGGERS: tuple[dict, ...] = (
    {
        "trigger": "foreign_device_login",
        "name": "非主学海设备登录",
        "evaluation": "login",
        "description": (
            "登录来自不属于本人的主学海设备：证据是临时密码连续输错次数与"
            "设备归属查询，达到阈值后账号按梯度封禁（设备可单独解封）。"
        ),
        "config": {"threshold": 3, "window_hours": 24},
    },
    {
        "trigger": "platform_misuse",
        "name": "平台违规使用",
        "evaluation": "report",
        "description": (
            "客户端上报的违规信号（页面被嵌套、学海桥缺失、调试器/注入痕迹）；"
            "窗口内累计到阈值才按梯度封号，管理员/学生会/团支书只记录不封。"
        ),
        "config": {"threshold": 3, "window_hours": 24},
    },
    {
        "trigger": "login_failure_burst",
        "name": "登录失败暴破",
        "evaluation": "monitor",
        "description": (
            "证据来源：服务端 APP_LOGS 中该账号的登录失败记录"
            "（ltype=login_fail，只记摘要不记密码）。密码失败无法可靠区分"
            "本人输错与第三方拿公开学号定向刷，默认只告警；确认来源可信后"
            "管理员可显式改为封禁。"
        ),
        "config": {"threshold": 10, "window_hours": 1, "mode": "alert"},
    },
    {
        "trigger": "ban_evasion_retry",
        "name": "封禁期内反复尝试登录",
        "evaluation": "monitor",
        "description": (
            "证据来源：APP_LOGS 中账号处于封禁态时的登录失败记录"
            "（ltype=login_fail 且摘要含 banned）。封禁期内持续尝试说明有人在"
            "撞库或不服判，达到阈值按梯度加刑。"
        ),
        "config": {"threshold": 5, "window_hours": 1, "mode": "ban"},
    },
    {
        "trigger": "challenge_flood",
        "name": "设备挑战刷取",
        "evaluation": "monitor",
        "description": (
            "证据来源：DEVICE_CHALLENGES 中该账号的挑战签发条数。非主设备登录"
            "每次都会签发一次性挑战，短时间大量签发说明在反复试探设备闸；"
            "默认只告警，管理员确认误报率后再切封禁。"
        ),
        "config": {"threshold": 8, "window_hours": 1, "mode": "alert"},
    },
    {
        "trigger": "temp_password_issue_burst",
        "name": "临时密码签发过频",
        "evaluation": "monitor",
        "description": (
            "证据来源：APP_LOGS 中签发人自己的 temp_password_create 记录。"
            "为大量账号连发临时密码可能是批量协助，也可能是账号被盗用后批量"
            "开门；默认只告警。"
        ),
        "config": {"threshold": 20, "window_hours": 1, "mode": "alert"},
    },
    {
        "trigger": "export_burst",
        "name": "导出任务过频",
        "evaluation": "monitor",
        "description": (
            "证据来源：APP_LOGS 中请求者发起的导出任务记录（ltype=export）。"
            "短时间大量导出是数据外带的前兆；默认只告警。"
        ),
        "config": {"threshold": 10, "window_hours": 1, "mode": "alert"},
    },
    {
        "trigger": "login_success_churn",
        "name": "短时间反复登录",
        "evaluation": "monitor",
        "description": (
            "证据来源：APP_LOGS 中该账号的成功登录记录（ltype=user_login）。"
            "同一账号一小时内的登录次数远超正常使用，可能是凭据被共享；"
            "默认只告警。"
        ),
        "config": {"threshold": 15, "window_hours": 1, "mode": "alert"},
    },
    {
        "trigger": "device_switch_burst",
        "name": "频繁更换设备",
        "evaluation": "monitor",
        "description": (
            "证据来源：APP_LOGS 中该账号的主设备不匹配记录"
            "（ltype=device_mismatch）。一天内反复在不同设备上尝试登录，"
            "可能是账号被多人共用；默认只告警。"
        ),
        "config": {"threshold": 5, "window_hours": 24, "mode": "alert"},
    },
    {
        "trigger": "ai_chat_burst",
        "name": "AI 对话请求突发",
        "evaluation": "monitor",
        "description": (
            "证据来源：AI_CALL_LOGS 中该账号的 chat 调用流水"
            "（capability=chat，含被拒/失败的行；agent 的每一轮也算一次）。"
            "短时间大量请求可能是脚本化刷取或循环调用；默认只告警——"
            "学生会（department）在豁免名单里，不会被这条规则自动封禁。"
        ),
        "config": {"threshold": 80, "window_hours": 1, "mode": "alert"},
    },
    {
        "trigger": "device_temp_password_abuse",
        "name": "临时密码滥用（设备）",
        "evaluation": "login",
        "description": (
            "设备侧处罚梯度：临时密码连错时只封当事设备，按本规则 penalties "
            "逐档加刑（1/3/7/14/30 天、末档永久）。触发阈值与窗口仍取"
            "「非主学海设备登录」规则——这条规则只决定「封这台机器多久」，"
            "不对应新的证据来源。"
        ),
        "config": {"threshold": 3, "window_hours": 24},
    },
    {
        "trigger": "agent_jailbreak",
        "name": "agent 越狱/提示词注入",
        "evaluation": "ai_moderation",
        "description": (
            "证据来源：agent 输入的关键词规则命中与服务端 LLM 安全认证拒绝"
            "（信号 agent_jailbreak）。首次违规只记当月警告（USER_WARNINGS），"
            "同月再犯按本规则 penalties 梯度封号；admin 豁免。"
        ),
        "config": {"threshold": 3, "window_hours": 24},
    },
)

TRIGGER_REGISTRY: dict[str, dict] = {item["trigger"]: item for item in TRIGGERS}

# 规则的 code 会出现在审计日志与流水里，限定成 ASCII 小写下划线：
# 中文/空格会让「引用了哪条规则」在日志里无法 grep。
RULE_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{2,63}$")


def _require_admin(user: dict) -> None:
    if "admin" not in (user.get("per") or []):
        raise HTTPException(status_code=403, detail="权限不足")



def _iso(value) -> Optional[str]:
    """``datetime`` → ``YYYY-MM-DDTHH:MM:SS``；None 原样返回（永久封禁）。"""
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    return None


def _load_json(raw, fallback):
    """读取宽进：库里是 JSON 文本，坏数据降级而不是让整页 500。"""
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


def _rule_config(rule: dict) -> dict:
    config = _load_json(rule.get("config"), {})
    return config if isinstance(config, dict) else {}


def _rule_penalties(rule: dict) -> list:
    penalties = _load_json(rule.get("penalties"), [])
    return penalties if isinstance(penalties, list) else []


def _trigger_name(trigger: str | None) -> str:
    """检查项的中文名；未知/空 trigger 返回空串（老规则或手工改库的数据）。"""
    entry = TRIGGER_REGISTRY.get(trigger or "")
    return entry["name"] if entry else ""


def _rule_view(rule: dict) -> dict:
    trigger = rule.get("trigger") or ""
    return {
        "code": rule["code"],
        "name": rule["name"],
        "trigger": trigger,
        "trigger_name": _trigger_name(trigger),
        "enabled": bool(rule.get("enabled")),
        "builtin": bool(rule.get("builtin")),
        "config": _rule_config(rule),
        "penalties": _rule_penalties(rule),
        "updated_at": _iso(rule.get("updated_at")),
    }


def _json_text(value) -> str:
    """日志里的结构化值统一用 JSON 文本：``null`` 档位与空列表必须能区分。"""
    return json.dumps(value, ensure_ascii=False)


class PutRuleConfig(BaseModel):
    # 只校验类型；范围在端点上判，才能给出中文 422（与既有 v3 端点一致）。
    threshold: Optional[int] = None
    window_hours: Optional[int] = None


class PutRuleRequest(BaseModel):
    # ``name`` 可改；``trigger`` 只用于「传了就报 422」的显式拒绝——静默忽略
    # 会让调用方以为改成功了。
    name: Optional[str] = None
    trigger: Optional[str] = None
    enabled: Optional[bool] = None
    config: Optional[PutRuleConfig] = None
    # 末位 ``null`` = 永久处罚（梯度用尽后不再累计天数）。
    penalties: Optional[list[Optional[int]]] = None


class PostRuleRequest(BaseModel):
    code: str
    name: str
    trigger: str
    enabled: bool = True
    # 省略的项取注册表默认值（前端表单初值就是它），显式传的必须过范围校验。
    config: Optional[PutRuleConfig] = None
    penalties: list[Optional[int]]


def _check_range(name: str, value: int, low: int, high: int) -> None:
    if not (low <= value <= high):
        raise HTTPException(
            status_code=422, detail=f"{name} 必须在 {low}–{high} 之间"
        )


def _validated_penalties(penalties: list[Optional[int]]) -> list[Optional[int]]:
    """1–6 档、每档 1–3650 天、严格升序；``null`` 只允许出现在最后一位。

    ``null`` 是「此后永久」，所以它之后不能再有档位——否则「永久」后面的天数
    永远轮不到，配置会变成一句谎话。中间档仍按非空值严格升序校验。
    """
    if not 1 <= len(penalties) <= 6:
        raise HTTPException(status_code=422, detail="处罚梯度必须是 1–6 项")

    last_index = len(penalties) - 1
    for index, step in enumerate(penalties):
        if step is None:
            if index != last_index:
                raise HTTPException(
                    status_code=422, detail="永久处罚（null）只能出现在最后一位"
                )
            continue
        _check_range("处罚天数", step, 1, 3650)

    # None 只可能在末位，取出来就是一段前缀；升序只需在这段前缀上判。
    concrete = [step for step in penalties if step is not None]
    for previous, current in zip(concrete, concrete[1:]):
        if current <= previous:
            raise HTTPException(
                status_code=422, detail="处罚梯度必须严格升序"
            )
    return list(penalties)


@router.get("/rules")
async def list_ban_rules(user=Depends(get_current_user)):
    _require_admin(user)
    rules = await backends.get_ban_rules_repo().list_all()
    return {
        "status": "ok",
        "code": 200,
        "data": [_rule_view(rule) for rule in rules],
    }


@router.get("/triggers")
async def list_triggers(user=Depends(get_current_user)):
    """平台注册的检查项：管理员新增规则时的下拉来源。

    返回 ``config`` 是**默认参数**，不是某条规则的现值——表单需要一个合理的
    初值，而现值属于 ``GET /rules``。
    """
    _require_admin(user)
    return {"status": "ok", "code": 200, "data": list(TRIGGERS)}


@router.post("/rules", status_code=201)
async def create_ban_rule(
    payload: PostRuleRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """新增一条规则（检查项由平台注册，管理员只能选不能写）。

    两条 409 是同一个约束的两面：``code`` 是管理员看到的名字，``trigger`` 是
    求值入口的入口键。后者尤其不能重复——同一检查项两条规则时，「这个用户该
    按哪条梯度封」取决于查询顺序，封禁天数就不可复现了。
    """
    _require_admin(user)

    if not RULE_CODE_PATTERN.match(payload.code or ""):
        raise HTTPException(
            status_code=422,
            detail="code 必须匹配 ^[a-z][a-z0-9_]{2,63}$（小写字母开头，可含数字与下划线）",
        )

    name = (payload.name or "").strip()
    if not 1 <= len(name) <= 128:
        raise HTTPException(status_code=422, detail="规则名称长度必须在 1–128 之间")

    entry = TRIGGER_REGISTRY.get(payload.trigger or "")
    if entry is None:
        raise HTTPException(
            status_code=422, detail=f"未知的检查项：{payload.trigger}"
        )

    repo = backends.get_ban_rules_repo()
    if await repo.get_by_code(payload.code) is not None:
        raise HTTPException(status_code=409, detail="规则 code 已存在")
    holder = await repo.get_by_trigger(payload.trigger)
    if holder is not None:
        raise HTTPException(
            status_code=409,
            detail=f"检查项「{entry['name']}」已被规则 {holder['code']} 使用",
        )

    # 以注册表默认值为底：只传了一项时另一项不会缺，配置形状始终完整。
    config = dict(entry["config"])
    if payload.config is not None:
        if payload.config.threshold is not None:
            _check_range("threshold", payload.config.threshold, 2, 200)
            config["threshold"] = payload.config.threshold
        if payload.config.window_hours is not None:
            _check_range("window_hours", payload.config.window_hours, 1, 168)
            config["window_hours"] = payload.config.window_hours
    penalties = _validated_penalties(payload.penalties)

    try:
        await repo.insert(
            payload.code,
            name,
            payload.trigger,
            enabled=payload.enabled,
            config=json.dumps(config, ensure_ascii=False),
            penalties=json.dumps(penalties, ensure_ascii=False),
        )
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="规则 code 已存在，或该检查项已有一条规则（同一检查项只允许一条）",
        )

    created = await repo.get_by_code(payload.code)
    if created is None:
        # 插入成功后立刻读不到 = 并发删除；对外就是「对象不存在」。
        raise HTTPException(status_code=404, detail="规则不存在")

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 新增封禁规则 "
        f"{payload.code}（检查项 {entry['name']}）："
        f"threshold {config.get('threshold')}，"
        f"window_hours {config.get('window_hours')}，"
        f"penalties {_json_text(penalties)}，"
        f"enabled {payload.enabled}",
        type="ban_change",
        level="warning",
    )
    await log.insert_log()

    return {"status": "ok", "code": 201, "data": _rule_view(created)}


@router.delete("/rules/{code}", status_code=204)
async def delete_ban_rule(
    code: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """删除规则。

    内置规则只能停用不能删除：它们对应平台的一条求值链路（设备闸/上报闸），
    删掉后那条链路会「静默不封」，管理员在界面上看不出区别，只会以为策略还在。
    """
    _require_admin(user)

    repo = backends.get_ban_rules_repo()
    rule = await repo.get_by_code(code)
    if rule is None:
        raise HTTPException(status_code=404, detail="规则不存在")
    if rule.get("builtin"):
        raise HTTPException(status_code=409, detail="内置规则不能删除，只能停用")

    if await repo.delete_by_code(code) == 0:
        # 前置读与删除之间的并发删除：对外仍是 404。
        raise HTTPException(status_code=404, detail="规则不存在")

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 删除了封禁规则 {code}"
        f"（{rule.get('name')}）",
        type="ban_change",
        level="warning",
    )
    await log.insert_log()


@router.put("/rules/{code}")
async def update_ban_rule(
    code: str,
    payload: PutRuleRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    _require_admin(user)
    repo = backends.get_ban_rules_repo()
    rule = await repo.get_by_code(code)
    if rule is None:
        raise HTTPException(status_code=404, detail="规则不存在")
    # 改动前的快照要在此刻取：后续 update 之后同一个 dict 可能已被就地改写。
    before = _rule_view(rule)

    # 检查项不可改：换 trigger 等于换一条规则（求值链路、证据来源全变了），
    # 静默忽略又会让调用方误以为改成功——两种都不行，直接 422。
    if payload.trigger is not None and payload.trigger != (rule.get("trigger") or ""):
        raise HTTPException(status_code=422, detail="规则的检查项（trigger）不可修改")

    fields: dict = {}
    if payload.name is not None:
        name = payload.name.strip()
        if not 1 <= len(name) <= 128:
            raise HTTPException(status_code=422, detail="规则名称长度必须在 1–128 之间")
        fields["name"] = name
    if payload.enabled is not None:
        fields["enabled"] = payload.enabled

    if payload.config is not None:
        # 整体序列化：``config`` 是一份完整配置，缺省项沿用库里现值，
        # 避免「只传 window_hours」把 threshold 冲掉。
        merged = dict(_rule_config(rule))
        if payload.config.threshold is not None:
            _check_range("threshold", payload.config.threshold, 2, 200)
            merged["threshold"] = payload.config.threshold
        if payload.config.window_hours is not None:
            _check_range("window_hours", payload.config.window_hours, 1, 168)
            merged["window_hours"] = payload.config.window_hours
        fields["config"] = json.dumps(merged, ensure_ascii=False)

    if payload.penalties is not None:
        fields["penalties"] = json.dumps(_validated_penalties(payload.penalties))

    # 空 patch 不产生写：仍然返回当前规则，语义上是幂等的「读回」。
    if fields:
        affected = await repo.update_by_code(code, **fields)
        if affected == 0:
            # 前置读与更新之间的并发删除：对外就是 404。
            raise HTTPException(status_code=404, detail="规则不存在")

    updated = await repo.get_by_code(code)
    if updated is None:
        raise HTTPException(status_code=404, detail="规则不存在")

    if fields:
        # 改的是判定闸门的阈值与处罚梯度，必须留痕：没有这条日志，事后
        # 「为什么突然开始封人」只能靠猜。字段用改动前后的值成对表达。
        after = _rule_view(updated)
        config_before, config_after = before["config"], after["config"]
        # 名称只在真的改过时出现：绝大多数改动是调阈值，日志要能一眼扫过。
        name_part = (
            f"name {before['name']}→{after['name']}，"
            if before["name"] != after["name"]
            else ""
        )
        log.with_text(
            f"管理员 {await get_user_display_name(user['id'])} 修改封禁规则 {code}："
            f"{name_part}"
            f"threshold {config_before.get('threshold')}→{config_after.get('threshold')}，"
            f"window_hours {config_before.get('window_hours')}→"
            f"{config_after.get('window_hours')}，"
            f"penalties {_json_text(before['penalties'])}→"
            f"{_json_text(after['penalties'])}，"
            f"enabled {before['enabled']}→{after['enabled']}",
            type="ban_change",
            level="warning",
        )
        await log.insert_log()

    return {"status": "ok", "code": 200, "data": _rule_view(updated)}


@router.get("/users")
async def list_banned_users(
    page: int = Query(1, ge=1),
    perpage: int = Query(20, ge=1, le=100),
    query: str = Query("", max_length=128),
    include_expired: int = Query(0, ge=0, le=1),
    user=Depends(get_current_user),
):
    """当前被封禁的用户（``USERS.BANNED = 1`` 且未刑满），不是流水。

    ``/records`` 是只增的封禁历史，一屏「封禁管理」要看的是「现在谁进不来」：
    人工解封后流水仍留着，照流水显示会把已解封的人继续挂在列表上。到期封禁
    的闸门列也不会自动归零，所以默认再排除 ``ban_until`` 已过期的存量行；
    ``include_expired=1`` 保留全量视角，供管理员排查脏数据。
    """
    _require_admin(user)
    rows, total = await backends.get_users_repo().list_banned_page(
        query, page, perpage, include_expired=bool(include_expired)
    )
    data = [
        {
            "oid": row["_id"],
            "name": row["name"],
            # 与 /records 同一命名约定：对外叫 user_id，值是学号。
            "user_id": row.get("id"),
            "class_name": row.get("class_name"),
            "reason": row.get("ban_reason"),
            # ban_until 为 NULL 且 banned=1 就是永久封禁。
            "permanent": row.get("ban_until") is None,
            "until": _iso(row.get("ban_until")),
            "source": row.get("ban_source"),
            "rule_code": row.get("ban_rule"),
        }
        for row in rows
    ]
    return {
        "status": "ok",
        "code": 200,
        "data": data,
        "metadata": {"size": total, "page": page, "perpage": perpage},
    }


# ---------------------------------------------------------------------------
# 设备封禁（BANNED_DEVICES）
#
# 设备封禁与账号封禁是两份事实：账号解封不会顺带放出设备，反之亦然。只有
# 「临时密码连错 3 次」会写显式设备封禁行；账号封禁对联动的设备是由
# ``services.device_guard.device_ban_status`` 实时推导的，不出现在本列表里。
# ---------------------------------------------------------------------------


def _device_view(row: dict, ban_count: int = 0, owner: dict | None = None) -> dict:
    until = row.get("until")
    return {
        "xuehai_id": row["xuehai_id"],
        "reason": row.get("reason"),
        # until 为 NULL 就是永久，与 /users、/records 的 permanent 语义一致。
        "permanent": until is None,
        "until": _iso(until),
        "source": row.get("source"),
        "rule_code": row.get("rule_code"),
        "created_at": _iso(row.get("created_at")),
        "ban_count": ban_count,
        "owner": owner,
    }


class PostDeviceBanRequest(BaseModel):
    """手动封禁一台设备；``until`` 省略/NULL 即永久。"""

    xuehai_id: str = Field(min_length=1, max_length=32, pattern=r"^\d+$")
    reason: Optional[str] = Field(default=None, max_length=512)
    until: Optional[datetime] = None


@router.get("/devices")
async def list_banned_devices(
    page: int = Query(1, ge=1),
    perpage: int = Query(20, ge=1, le=100),
    query: str = Query("", max_length=128),
    include_expired: int = Query(0, ge=0, le=1),
    user=Depends(get_current_user),
):
    """当前生效的设备封禁（``until`` 为空=永久，或尚未到期）。

    ``include_expired=1`` 连已刑满的行一起列（行不会自动删，只由判定层用
    ``ban_is_active`` 排除），供管理员排查存量数据。
    """
    _require_admin(user)
    rows, total = await backends.get_device_bans_repo().list_page(
        page, perpage, query, include_expired=bool(include_expired)
    )
    ids = [row["xuehai_id"] for row in rows]
    counts = await backends.get_device_ban_counters_repo().get_counts(ids)
    owners = await backends.get_users_repo().primary_owners(ids)
    data = [
        _device_view(
            row,
            ban_count=counts.get(row["xuehai_id"], 0),
            owner=owners.get(row["xuehai_id"]),
        )
        for row in rows
    ]
    return {
        "status": "ok",
        "code": 200,
        "data": data,
        "metadata": {"size": total, "page": page, "perpage": perpage},
    }


@router.post("/devices")
async def ban_device(
    payload: PostDeviceBanRequest,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """手动封禁一台设备（管理员判定，不动设备梯度计数器）。"""
    _require_admin(user)

    xuehai_id = payload.xuehai_id.strip()
    reason = (payload.reason or "").strip() or "管理员封禁"
    # 客户端 ``toISOString()`` 送来的 aware 时间必须折成本地 naive 再落库：
    # 列是 TIMESTAMP（无时区），直接绑定时驱动只丢 tzinfo、保留 UTC 墙钟，
    # 东八区下「封 24 小时」会被判定端读成只剩 16 小时。
    until = device_guard._to_local_naive(payload.until)
    await backends.get_device_bans_repo().upsert(
        xuehai_id,
        reason=reason,
        until=until,
        source="admin",
        rule_code=None,
        operator_oid=str(user["id"]),
    )

    until_text = _iso(until) or "永久"
    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 封禁了设备 {xuehai_id}"
        f"（原因：{reason}，到期：{until_text}）",
        type="ban_change",
        level="warning",
    )
    await log.insert_log()

    counts = await backends.get_device_ban_counters_repo().get_counts([xuehai_id])
    return {
        "status": "ok",
        "code": 200,
        "data": {
            "xuehai_id": xuehai_id,
            "reason": reason,
            "permanent": until is None,
            "until": _iso(until),
            "source": "admin",
            "rule_code": None,
            "ban_count": counts.get(xuehai_id, 0),
        },
    }


@router.post("/devices/{xuehai_id}/reset-gradient", status_code=204)
async def reset_device_gradient(
    xuehai_id: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """清零一台设备的处罚梯度（幂等：没有计数行也返回 204）。"""
    _require_admin(user)

    await backends.get_device_ban_counters_repo().delete(xuehai_id)
    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 重置了设备 {xuehai_id} 的封禁梯度",
        type="ban_change",
        level="warning",
    )
    await log.insert_log()


@router.delete("/devices/{xuehai_id}", status_code=204)
async def unban_device(
    xuehai_id: str,
    user=Depends(get_current_user),
    log=Depends(inject_log),
):
    """解除设备封禁（人工出口）。

    被删除的只是显式封禁行；因账号封禁而「实时推导为封禁」的设备无需在此处理
    ——账号解封后它们自动恢复。
    """
    _require_admin(user)

    deleted = await backends.get_device_bans_repo().delete(xuehai_id)
    if deleted == 0:
        # 没有这行就是没封过（或被并发解除）：对外是「对象不存在」，不是成功。
        raise HTTPException(status_code=404, detail="设备未被封禁")

    log.with_text(
        f"管理员 {await get_user_display_name(user['id'])} 解除了设备 {xuehai_id} 的封禁",
        type="ban_change",
        level="warning",
    )
    await log.insert_log()


@router.get("/records")
async def list_ban_records(
    page: int = Query(1, ge=1),
    perpage: int = Query(20, ge=1, le=100),
    query: str = Query("", max_length=128),
    user=Depends(get_current_user),
):
    _require_admin(user)
    rows, total = await backends.get_ban_records_repo().list_page(page, perpage, query)
    data = [
        {
            "_id": row["_id"],
            "user_oid": row["user_oid"],
            "user_name": row.get("user_name"),
            # 对外叫 user_id，前端沿用的是「学号 = 业务 id」的既有命名。
            "user_id": row.get("user_business_id"),
            "source": row["source"],
            "rule_code": row.get("rule_code"),
            "reason": row.get("reason"),
            "permanent": bool(row.get("permanent")),
            "started_at": _iso(row.get("started_at")),
            "expires_at": _iso(row.get("expires_at")),
            "operator_name": row.get("operator_name"),
        }
        for row in rows
    ]
    return {
        "status": "ok",
        "code": 200,
        "data": data,
        "metadata": {"size": total, "page": page, "perpage": perpage},
    }
