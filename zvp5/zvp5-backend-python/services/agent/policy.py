"""ZVAgent 的双人授权分层策略。

``ToolDef.dual_auth_action`` 是**工具级**开关，``db_update`` 一个名字同时承担
「改义工活动」与「改平台账号表」两种风险。把授权强度绑在工具名上，只能二选一：
要么所有 DML 都要另一位管理员，要么平台账号表也能单人改。

本模块提供按**参数**分层的唯一判据：

* :data:`VOLUNTEER_TABLES` 是义工业务数据表（活动、成员、时长、指标、组织）。
  这些表的 DML 属于日常业务操作，提案仍需本人确认，但不再要求第二位管理员
  的动态码；
* 其余表（``USERS`` / ``AI_*`` / ``BAN_*`` / ``NOTIFICATIONS`` /
  ``SYSTEM_SETTINGS`` 等）属于平台数据，维持双人码；
* DDL、文件写、SSH 属于平台操作，永远不豁免。

判定必须是**纯函数**：propose、list、confirm 三条路径都用它，任何一条另写一套
就会让「界面上说不用码、确认时却要码」。表名归一化刻意宽松（去空白/双引号、
忽略大小写），但只做精确匹配——不做前缀/通配，避免 ``ACTIVITIES_LOG`` 之类
被顺手带进豁免面。
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


def _normalize_table(name: object) -> str:
    if not isinstance(name, str):
        return ""
    return name.strip().strip('"').upper()


def is_volunteer_table(name: object) -> bool:
    """``name`` 是否是义工业务表（大小写/引号不敏感，非字符串一律 False）。"""
    return _normalize_table(name) in VOLUNTEER_TABLES


def volunteer_table_exempt(arguments: dict) -> bool:
    """DML 工具的豁免谓词：只针对 ``arguments["table"]`` 判断。

    参数形状由 ``db_insert`` / ``db_update`` / ``db_delete`` 的 schema 保证；
    这里仍用 ``.get`` + 类型判定，缺字段时返回 False（= 不免除），与
    ``ToolDef.requires_dual_auth`` 的 fail-closed 方向一致。
    """
    if not isinstance(arguments, dict):
        return False
    return is_volunteer_table(arguments.get("table"))


__all__ = [
    "VOLUNTEER_TABLES",
    "is_volunteer_table",
    "volunteer_table_exempt",
]
