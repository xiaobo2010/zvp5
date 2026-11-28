"""ZVAgent 的数据域写策略：**通用数据库写工具的表级边界**。

``db_insert`` / ``db_update`` / ``db_delete`` / ``db_ddl`` 是「任意表、任意列」的
通用写能力。模型看到的是 ``values: {列名: 值}``，它并不知道（也不该被允许决定）
``ACTIVITIES.CREATOR_OID``、``USERS.ROLES``、``ACTIVITY_MEMBERS.DURATION`` 这些
字段的业务含义——一次“顺手”的 DB 更新就能伪造活动创建人、给自己加角色、或改掉
已审核的时长。**提示词不能阻止这件事，只有工具层拒绝可以。**

因此本模块给出唯一判据：

* :data:`VOLUNTEER_TABLES` 是义工业务表（活动、成员、时长、指标、组织、导入/导出）；
* :data:`GENERIC_WRITE_BLOCKED_TABLES` = 上述全部 + 账号/权限/额度/审计/凭据/
  文件/会话表（与 ``services.db_content._PROTECTED_TABLES`` 的平台侧清单同档）。
  落在这张表里的目标，通用行 CRUD 与 ``db_ddl`` 一律拒绝；管理动作由专用工具
  承担（见 ``services.agent.tools_domain``）：活动创建人、审核人、账号角色、
  班级归属全部由**服务端从当前登录用户派生或经专用窄接口校验**，模型没有可传的
  身份字段；
* 其余未列入的表（``BAN_*`` / ``NOTIFICATIONS`` / ``SYSTEM_SETTINGS`` /
  ``AI_CALL_LOGS`` / ``EXPORT_TASKS`` 等）仍可经通用 DML 修改，但**一律要求另一位
  管理员的动态码**（不再有按表豁免）。

判定必须是**纯函数**：``prepare``（建提案前）与 ``impl``（确认执行时）都调用它。
只在 ``prepare`` 拦会让「本次修改之前就已存在的 pending 提案」和重放路径绕过
限制；只在 ``impl`` 拦则会让用户为一条注定失败的提案输一次动态码。

表名归一化刻意宽松（去空白/双引号、忽略大小写），但只做精确匹配——不做前缀/
通配，避免 ``ACTIVITIES_LOG`` 之类被顺手带进边界。
"""

from __future__ import annotations

VOLUNTEER_TABLES: frozenset[str] = frozenset(
    {
        "ACTIVITIES",
        "ACTIVITY_MEMBERS",
        "USER_TIME",
        "TIME_ACADEMIC_YEAR",
        "TIME_WITH_ORIGIN",
        "TIME_WITH_ORIGIN_STAT",
        "GROUP_INDICATORS",
        "INDICATORS",
        "GROUPS",
        "DAILY_EXPORTS",
        "IMPORTS",
    }
)

#: 账号 / 权限 / 审计 / 凭据 / 文件 / 会话表。通用行 CRUD 能改 ``ROLES``、
#: ``PASSWORD``、``CLASS_OID``、``OWNER_OID``，或绕过专用工具的额度上限；与
#: ``services.db_content._PROTECTED_TABLES`` 的平台侧清单保持同档（这里额外收了
#: ``ROLE_*`` / ``AI_QUOTA``，因为 agent 有对应的专用治理工具）。
PROTECTED_PLATFORM_TABLES: frozenset[str] = frozenset(
    {
        "USERS",
        "ROLE_FEATURES",
        "ROLE_QUOTAS",
        "USER_QUOTA_OVERRIDES",
        "AI_QUOTA",
        "APP_LOGS",
        "FILES",
        "FILE_EVENTS",
        "AI_API_KEYS",
        "AI_CONVERSATIONS",
        "AI_MESSAGES",
        "AI_RUNS",
        "AI_AGENT_EVENTS",
        "AI_SCHEDULED_TASKS",
    }
)

#: 通用数据库写工具（``db_insert`` / ``db_update`` / ``db_delete`` / ``db_ddl``）
#: 一律不得触碰的表。修改这份清单等于修改能力边界，必须随测试一起 review。
GENERIC_WRITE_BLOCKED_TABLES: frozenset[str] = (
    VOLUNTEER_TABLES | PROTECTED_PLATFORM_TABLES
)

#: 错误码：专用工具取代通用写法（不是“权限不足”，而是“这个入口没有这项能力”）。
GENERIC_WRITE_BLOCKED_CODE = "AI_AGENT_TABLE_WRITE_BLOCKED"


def normalize_table(name: object) -> str:
    """``name`` → 大写、去空白/双引号；非字符串回空串（= 不匹配任何表）。"""
    if not isinstance(name, str):
        return ""
    return name.strip().strip('"').upper()


def is_volunteer_table(name: object) -> bool:
    """``name`` 是否是义工业务表（大小写/引号不敏感，非字符串一律 False）。"""
    return normalize_table(name) in VOLUNTEER_TABLES


def is_generic_write_blocked(name: object) -> bool:
    """通用行 CRUD / DDL 是否禁止触碰该表。"""
    return normalize_table(name) in GENERIC_WRITE_BLOCKED_TABLES


def generic_write_blocked_reason(name: object) -> str | None:
    """拒绝原因（人读）；不命中返回 ``None``。

    文案要能让模型下一步做对的事：告诉它用专用工具，而不是“无权限”然后换个
    表名继续试。
    """
    table = normalize_table(name)
    if table not in GENERIC_WRITE_BLOCKED_TABLES:
        return None
    if table in PROTECTED_PLATFORM_TABLES:
        return (
            f"表 {table} 属于账号/权限/审计/凭据/会话等平台数据，禁止通过通用"
            "数据库写工具修改；请使用对应的专用工具或人工管理端点"
        )
    return (
        f"表 {table} 属于义工业务数据，禁止通过通用数据库写工具修改；"
        "请使用活动/成员专用工具（activity_create / activity_update / "
        "activity_review / update_member_duration / set_member_status）"
    )


__all__ = [
    "GENERIC_WRITE_BLOCKED_CODE",
    "GENERIC_WRITE_BLOCKED_TABLES",
    "PROTECTED_PLATFORM_TABLES",
    "VOLUNTEER_TABLES",
    "generic_write_blocked_reason",
    "is_generic_write_blocked",
    "is_volunteer_table",
    "normalize_table",
]
