"""agent 工具注册表（**封闭集合**）。

能力限制在工具定义层，而不是靠 prompt：白名单以外的名字一律拒绝，模型说什么
都不影响。这是「提示词不是安全边界」的直接落地。

两条铁律：

1. **默认只读**。第一批 8 个读工具全部只查不写；写工具只有 2 个，且都以「提案」
   的形式出现——模型的调用**不会**直接落库，必须由用户在平台上确认
   （见 ``services.agent.proposal``）。审批在服务端二次校验，不认模型自称。
2. **权限镜像对应的 REST 端点**。工具不放宽也不收紧既有口径：REST 端点谁可读，
   工具就谁可读；写工具的条件与 ``util.permission.volunteer_member`` 的成员写
   校验保持一致（同一条件，见 :func:`_member_writer`）。

每个工具声明 JSON Schema（``params``），执行前由 :func:`prepare_arguments` 做
最小校验与类型收敛：模型给的参数是**不可信输入**，string/int 混用很常见，
但「把 ``limit`` 传成 ``"100"`` 就报错」会让模型反复重试同样的错误。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from . import help_docs, tool_impl
from .tool_impl import AgentError

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查，运行时不导入（proposal 依赖本模块）
    from .proposal import ProposalDraft

READ = "read"
WRITE = "write"

# 学生会（department）可见的工具集合。声明层过滤是**能力边界**的一部分：
# 运维工具（ops_tools 注册的那 17 个）只对管理员声明，学生会连「有这个能力」都
# 不该知道——执行期的 permission/guard 能否决调用，但模型仍会照着 tools 数组
# 去试、去承诺，用户看到的是「它说能改数据库」。
VOLUNTEER_TOOL_NAMES = frozenset(
    {
        "search_activities",
        "list_activities",
        "get_activity",
        "activity_statistics",
        "activity_types_summary",
        "list_activity_members",
        "user_volunteer_time",
        "user_activities",
        "list_groups",
        "update_member_duration",
        "set_member_status",
    }
)

PLATFORM_HELP_TOOL = "platform_help"

# 非管理员看到的 = 义工管理 11 个 + 平台帮助。帮助文档对所有 AI 可用角色开放。
DEPARTMENT_TOOL_NAMES = VOLUNTEER_TOOL_NAMES | {PLATFORM_HELP_TOOL}

# 允许「直接执行」（不建提案、不过 TOTP）的写工具名单。**模块级常量**而不是
# ``ToolDef`` 上的自由开关：哪种写操作能绕过人工确认是一份需要 review 的清单，
# 加一个名字就得改这里，改不动就是定义错误（注册期直接抛）。
#
# 只收「封禁/解封」这类**可逆、且只作用于非管理员**的动作：巡检 agent 发现
# 账号被灌爆时，等一位管理员上线点确认的窗口里攻击已经完成了。真正的护栏不在
# 提案层，而在工具实现里（不能碰自己、不能碰 admin、每次现查库、全量审计）。
DIRECT_WRITE_TOOL_NAMES = frozenset({"user_ban", "user_unban", "device_unban"})


@dataclass(frozen=True)
class ToolDef:
    """一个工具的完整定义（名字、说明、读写、参数、权限、实现）。

    ``permission`` 收的是**当前用户**（认证后的 dict），返回 bool：True = 允许
    调用。它比抛异常更好组合——executor 需要对「被拒」写一条 ``denied`` 事件而
    不是让整个 run 崩掉。

    三个 v2 字段（全部有默认值，既有 10 个活动域工具的调用点不用改）：

    ``prepare``
        仅写工具用：``await prepare(user, **kwargs)`` → :class:`ProposalDraft`
        （人读摘要 + 结构化预览）。为 ``None`` 时提案预览回落成
        ``{"kind": "args", "args": {...}}``，确认界面仍然有东西可看。
    ``guard``
        现查库的权限闸：``await guard(user)``，不通过就抛（``AgentError`` 或任意
        异常）。``permission`` 是**纯函数**、只能做快速否决；「这个人现在是不是
        系统管理员」必须每次查库（``ops_auth.ensure_system_admin``）。
    ``dual_auth_action``
        非空 → 确认这条提案时必须由**另一位**管理员提供 6 位动态码，
        ``action`` 原样传给 ``util.dual_auth.require_dual_auth``。
    ``direct``
        ``True`` → 这个写工具**直接执行**：过完 ``permission`` / ``guard`` /
        参数收敛后立即调用 ``impl``，不建提案、不要 TOTP。仅限
        :data:`DIRECT_WRITE_TOOL_NAMES` 里的名字（见那里的理由），默认 ``False``
        ——既有写工具的行为一个字节都不变。
    """

    name: str
    description: str
    kind: str
    params: dict
    permission: Callable[[dict], bool]
    impl: Callable[..., Awaitable[Any]]
    prepare: Callable[..., Awaitable["ProposalDraft"]] | None = None
    guard: Callable[[dict], Awaitable[None]] | None = None
    dual_auth_action: str | None = None
    direct: bool = False


def _always(user: dict) -> bool:
    """任何已登录用户：``get_current_user`` 已经挡过未认证请求。

    对应 REST 端点（``GET /api/v1/activities``、``GET /api/v1/users/{id}/time``
    等）在「已登录」之外没有额外角色闸——工具不额外收紧。
    """
    return True


def _privileged(user: dict) -> bool:
    """admin / department：与 ``GET /api/groups`` 的 ``privileged`` 同一条件。"""
    per = set(user.get("per") or [])
    return bool({"admin", "department"} & per)


def _member_writer(user: dict) -> bool:
    """写成员：镜像成员写端点的权限判定。

    对应 ``util.permission.volunteer_member.validate_create_permission``（改状态）
    与 ``validate_update_permission``（改时长）：两者在「谁能写」上是同一条件
    「admin 或 department」，其余角色一律 403。谓词必须在**不查库**的前提下可判
    （注册表的 permission 是纯函数），所以这里复制的是条件本身；两个校验函数的
    分支只要有一个放宽，这个谓词必须跟着改——测试
    ``test_agent_tools.py::test_write_tools_mirror_member_validators`` 会盯住它。
    """
    per = set(user.get("per") or [])
    return bool({"admin", "department"} & per)


def _schema(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_STR = {"type": "string"}
_INT = {"type": "integer"}
_NUM = {"type": "number"}

TOOLS: dict[str, ToolDef] = {}


def _register(tool: ToolDef) -> ToolDef:
    """注册一个工具，并按契约校验 v2 字段的组合是否自洽。

    校验写在注册时（import 期）而不是调用时：一个「声明了 TOTP 却没有预览/权限
    闸」的写工具是**定义错误**，让它跑起来只会把「双人授权」变成一句空话。
    """
    if tool.kind not in (READ, WRITE):
        raise ValueError(f"tool {tool.name}: kind must be read|write")
    if tool.name in TOOLS:
        raise ValueError(f"tool {tool.name}: duplicate registration")
    if tool.kind == READ:
        # 读工具自动执行，没有提案：声明预览或 TOTP 只可能是抄错了 kind。
        if tool.prepare is not None:
            raise ValueError(f"tool {tool.name}: read tools cannot declare prepare")
        if tool.dual_auth_action is not None:
            raise ValueError(
                f"tool {tool.name}: read tools cannot declare dual_auth_action"
            )
        if tool.direct:
            raise ValueError(f"tool {tool.name}: only write tools can be direct")
    else:
        if tool.impl is None:
            raise ValueError(f"tool {tool.name}: write tools must declare impl")
        if tool.direct:
            # 「直接执行」是绕过人工确认的能力，三条同时成立才允许：
            # 名字在模块级清单里、有现查库的 guard（否则只靠 token 里的 per
            # 快照）、有 impl（不然没东西可执行）。任何一条缺失都是定义错误。
            if tool.name not in DIRECT_WRITE_TOOL_NAMES:
                raise ValueError(
                    f"tool {tool.name}: direct write is not in DIRECT_WRITE_TOOL_NAMES"
                )
            if tool.guard is None:
                raise ValueError(
                    f"tool {tool.name}: direct write tools need a guard"
                )
            if tool.dual_auth_action is not None:
                # direct 与 TOTP 是互斥语义：executor 先看 direct，声明了两者
                # 会静默跳过双人授权——那是把安全开关写成了摆设。
                raise ValueError(
                    f"tool {tool.name}: direct write cannot declare dual_auth_action"
                )
        # 运维写工具的判据就是 dual_auth_action：它有 TOTP 就意味着
        # 「确认时必须有东西给用户看（prepare）+ 执行前必须现查库（guard）」。
        if tool.dual_auth_action is not None and (
            tool.prepare is None or tool.guard is None
        ):
            raise ValueError(
                f"tool {tool.name}: dual-auth write tools need both prepare and guard"
            )
    TOOLS[tool.name] = tool
    return tool


# ---------------------------------------------------------------------------
# 读工具（8 个）
# ---------------------------------------------------------------------------


async def _impl_search_activities(
    user: dict, query: str, limit: int = 10
) -> dict:
    return await tool_impl.search_activities(query=query, limit=limit)


_register(
    ToolDef(
        name="search_activities",
        description="按关键词搜索义工活动（返回活动摘要与分页信息）。",
        kind=READ,
        params=_schema(
            {
                "query": {**_STR, "description": "活动名/描述关键词"},
                "limit": {
                    **_INT,
                    "minimum": 1,
                    "maximum": 20,
                    "description": "最多返回多少条（1-20，默认 10）",
                },
            },
            ("query",),
        ),
        permission=_always,
        impl=_impl_search_activities,
    )
)


async def _impl_list_activities(
    user: dict, page: int = 1, perpage: int = 10, activity_type: str = "all"
) -> dict:
    return await tool_impl.list_activities(
        page=page, perpage=perpage, activity_type=activity_type
    )


_register(
    ToolDef(
        name="list_activities",
        description="分页列出义工活动，可按活动类型过滤。",
        kind=READ,
        params=_schema(
            {
                "page": {**_INT, "minimum": 1, "description": "页码（从 1 起）"},
                "perpage": {
                    **_INT,
                    "minimum": 1,
                    "maximum": 100,
                    "description": "每页条数（1-100）",
                },
                "activity_type": {
                    **_STR,
                    "enum": ["all", *tool_impl.ACTIVITY_TYPES],
                    "description": "活动类型；all 表示全部",
                },
            },
            (),
        ),
        permission=_always,
        impl=_impl_list_activities,
    )
)


async def _impl_get_activity(user: dict, activity_id: str) -> dict:
    return await tool_impl.get_activity_detail(activity_id)


_register(
    ToolDef(
        name="get_activity",
        description="读取单个活动的详情（含成员数）。",
        kind=READ,
        params=_schema({"activity_id": {**_STR, "description": "活动 oid"}}, ("activity_id",)),
        permission=_always,
        impl=_impl_get_activity,
    )
)


async def _impl_activity_statistics(user: dict, activity_id: str) -> dict:
    return await tool_impl.activity_statistics(activity_id)


_register(
    ToolDef(
        name="activity_statistics",
        description="读取活动的时长统计（参与人数、均值、中位数、分位数等，仅统计已生效成员）。",
        kind=READ,
        params=_schema({"activity_id": {**_STR, "description": "活动 oid"}}, ("activity_id",)),
        # 镜像 ``activity_statistics_router._require_statistics_reader``：
        # 团支书及以上才能看统计。
        permission=lambda user: bool(
            {"admin", "department", "secretary"} & set(user.get("per") or [])
        ),
        impl=_impl_activity_statistics,
    )
)


async def _impl_activity_types_summary(user: dict) -> dict:
    return await tool_impl.activity_types_summary()


_register(
    ToolDef(
        name="activity_types_summary",
        description="按活动类型聚合的条数与状态分布（只读汇总，不含成员明细）。",
        kind=READ,
        params=_schema({}, ()),
        # 与 activity_statistics 同档：团支书及以上才能看统计。
        permission=lambda user: bool(
            {"admin", "department", "secretary"} & set(user.get("per") or [])
        ),
        impl=_impl_activity_types_summary,
    )
)


async def _impl_list_activity_members(
    user: dict,
    activity_id: str,
    page: int = 1,
    perpage: int = 10,
    search: str = "",
) -> dict:
    return await tool_impl.list_activity_members(
        activity_id=activity_id, page=page, perpage=perpage, search=search
    )


_register(
    ToolDef(
        name="list_activity_members",
        description="分页列出某活动的成员报名记录，可按姓名/学号搜索。",
        kind=READ,
        params=_schema(
            {
                "activity_id": {**_STR, "description": "活动 oid"},
                "page": {**_INT, "minimum": 1, "description": "页码（从 1 起）"},
                "perpage": {
                    **_INT,
                    "minimum": 1,
                    "maximum": 100,
                    "description": "每页条数（1-100）",
                },
                "search": {**_STR, "description": "姓名或学号关键词"},
            },
            ("activity_id",),
        ),
        permission=_always,
        impl=_impl_list_activity_members,
    )
)


async def _impl_user_volunteer_time(
    user: dict, user_id: str | None = None
) -> dict:
    return await tool_impl.user_volunteer_time(user_id or user["id"])


_register(
    ToolDef(
        name="user_volunteer_time",
        description="读取某用户的义工时长；不传 user_id 就是查询自己的。",
        kind=READ,
        params=_schema({"user_id": {**_STR, "description": "用户 oid，缺省=自己"}}, ()),
        permission=_always,
        impl=_impl_user_volunteer_time,
    )
)


async def _impl_user_activities(
    user: dict, user_id: str | None = None, page: int = 1
) -> dict:
    return await tool_impl.user_activities(user_id=user_id or user["id"], page=page)


_register(
    ToolDef(
        name="user_activities",
        description="分页列出某用户参与的活动及其本人报名记录；不传 user_id 就是自己。",
        kind=READ,
        params=_schema(
            {
                "user_id": {**_STR, "description": "用户 oid，缺省=自己"},
                "page": {**_INT, "minimum": 1, "description": "页码（从 1 起）"},
            },
            (),
        ),
        permission=_always,
        impl=_impl_user_activities,
    )
)


async def _impl_list_groups(user: dict, page: int = 1, size: int = 10) -> list[dict]:
    return await tool_impl.list_groups(
        page=page, size=size, include_permission=_privileged(user)
    )


_register(
    ToolDef(
        name="list_groups",
        description="列出班级分组（有管理权限时附带权限组）。",
        kind=READ,
        params=_schema(
            {
                "page": {**_INT, "minimum": 1, "description": "页码（从 1 起）"},
                "size": {
                    **_INT,
                    "minimum": 1,
                    "maximum": 100,
                    "description": "每页条数（1-100）",
                },
            },
            (),
        ),
        permission=_always,
        impl=_impl_list_groups,
    )
)


# ---------------------------------------------------------------------------
# 写工具（2 个，全部走「提案 → 用户确认」）
# ---------------------------------------------------------------------------


async def _impl_update_member_duration(
    user: dict, activity_id: str, member_id: str, duration: float
) -> dict:
    return await tool_impl.apply_member_duration(
        activity_id=activity_id, member_id=member_id, duration=duration, user=user
    )


_register(
    ToolDef(
        name="update_member_duration",
        description="修改某成员在某活动中的义工时长（小时）。写操作：会先生成待确认提案。",
        kind=WRITE,
        params=_schema(
            {
                "activity_id": {**_STR, "description": "活动 oid"},
                "member_id": {
                    **_STR,
                    "description": "成员报名记录 oid（不是用户 oid）",
                },
                "duration": {
                    **_NUM,
                    "minimum": 0,
                    "maximum": 1000,
                    "description": "新的时长（小时，非负）",
                },
            },
            ("activity_id", "member_id", "duration"),
        ),
        permission=_member_writer,
        impl=_impl_update_member_duration,
    )
)


async def _impl_set_member_status(
    user: dict, activity_id: str, member_id: str, status: str
) -> dict:
    return await tool_impl.apply_member_status(
        activity_id=activity_id, member_id=member_id, status=status, user=user
    )


_register(
    ToolDef(
        name="set_member_status",
        description="审核某成员在某活动中的报名状态（effective/pending/refused）。写操作：会先生成待确认提案。",
        kind=WRITE,
        params=_schema(
            {
                "activity_id": {**_STR, "description": "活动 oid"},
                "member_id": {
                    **_STR,
                    "description": "成员报名记录 oid（不是用户 oid）",
                },
                "status": {
                    **_STR,
                    "enum": list(tool_impl.MEMBER_STATUSES),
                    "description": "目标状态",
                },
            },
            ("activity_id", "member_id", "status"),
        ),
        permission=_member_writer,
        impl=_impl_set_member_status,
    )
)


# ---------------------------------------------------------------------------
# 平台帮助（静态知识库，不查库、不写库）
# ---------------------------------------------------------------------------


async def _impl_platform_help(user: dict, query: str) -> dict:
    result = help_docs.find_help(query)
    return {
        "answer": result["answer"],
        "sections": result["sections"],
        "matched": result["matched"],
    }


_register(
    ToolDef(
        name=PLATFORM_HELP_TOOL,
        description=(
            "查询平台使用帮助（怎么报名、临时密码、义工时长规则、批量登记、附件上传、"
            "ZVAgent、封禁申诉等）。用户询问平台用法时先调用它，并按 answer 的内容作答。"
        ),
        kind=READ,
        params=_schema(
            {
                "query": {
                    **_STR,
                    "description": "用户的问题或关键词（如：临时密码、义工时长、封禁）",
                }
            },
            ("query",),
        ),
        permission=lambda user: bool(
            {"admin", "department"} & set(user.get("per") or [])
        ),
        impl=_impl_platform_help,
    )
)


# ---------------------------------------------------------------------------
# 查找 / 参数校验
# ---------------------------------------------------------------------------

# 传给上游的 ``tools`` 数组（OpenAI function-calling 格式）。
#
# **按 name 排序**是缓存契约的一部分：上游的前缀缓存以「tools 数组的字节」为键，
# 注册顺序一变（有人把 import 排了个序）整段前缀就失效，全部请求变成未命中。
#
# 声明层按角色过滤：``admin`` 拿到全部工具；其余角色（实际只有 ``department``
# 能过 AI 门禁）只拿到义工管理 + 平台帮助。两份列表**按注册表规模缓存**，同一进程
# 内只物化一次——每轮重新排序 27 个工具是白花的 CPU，而且每轮都要重新分配一批
# dict，等于把前缀缓存本该省下的东西又花回去。
_SPECS_CACHE: dict[str, tuple[int, list[dict]]] = {}


def _is_admin(user: dict) -> bool:
    return "admin" in set((user or {}).get("per") or [])


def _build_specs(names: frozenset[str] | set[str]) -> list[dict]:
    ordered = sorted(
        (tool for name, tool in TOOLS.items() if name in names),
        key=lambda tool: tool.name,
    )
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.params,
            },
        }
        for tool in ordered
    ]


def tool_specs(user: dict, *, scene: str | None = None) -> list[dict]:
    """当前用户可见的工具声明（按角色裁剪，结果按 ``name`` 排序）。

    缓存键除角色外还带注册表规模：注册表是开放集合（``ops_tools`` 在导入时追加），
    测试里也会临时注册/移除工具；只按角色缓存会让「加了工具却看不到」变成一个
    无法复现的幽灵问题。

    ``scene`` 非空 = 只读场景会话（终端 / 文件编辑）：只声明 ``kind == READ`` 的
    工具。这是**能力边界**，不是提示词约束——写工具、提案流、``ssh_exec`` 与数据库
    DML/DDL 都不出现在 ``tools`` 数组里，模型连「有这个能力」都不知道。

    返回浅拷贝：调用方（executor、上游适配层）不该能把缓存里的列表改掉。
    """
    is_admin = _is_admin(user)
    key = ("admin" if is_admin else "department") + (f"|{scene}" if scene else "")
    revision = len(TOOLS)
    cached = _SPECS_CACHE.get(key)
    if cached is None or cached[0] != revision:
        names = set(TOOLS) if is_admin else DEPARTMENT_TOOL_NAMES
        specs = _build_specs(names)
        if scene:
            specs = [
                spec
                for spec in specs
                if (tool := TOOLS.get(spec["function"]["name"])) is not None
                and tool.kind == READ
            ]
        cached = (revision, specs)
        _SPECS_CACHE[key] = cached
    return list(cached[1])


def get_tool(name: object) -> ToolDef | None:
    """白名单查询：非字符串 / 未注册的名字一律 ``None``（不是抛异常）。

    调用方要能把「模型编了个工具名」写成一条 ``denied`` 事件并继续对话，而不是
    让整个 run 500。
    """
    if not isinstance(name, str):
        return None
    return TOOLS.get(name)


# 模型偶尔会把工具名带上函数调用层的命名空间前缀（``functions.platform_logs``），
# 或者大小写漂移（``Platform_Logs``）。前缀是协议噪声，不是另一个工具。
_TOOL_NAME_PREFIXES = ("functions.", "function.", "tool.", "tools.")

# 别名表：只收「模型见过的写法」→「注册表里的名字」。**不新增工具**——别名只
# 负责把既有名字翻译过来，目标不在 ``TOOLS`` 里就照样落空（下面还查一次注册表）。
TOOL_NAME_ALIASES = {"platformHelp": PLATFORM_HELP_TOOL}
_ALIASES_FOLDED = {key.lower(): value for key, value in TOOL_NAME_ALIASES.items()}


def normalize_tool_name(name: object) -> str | None:
    """把模型给的名字映射到注册表名；未知返回 ``None``。

    规则（按序）：去空白 → 去 ``functions.``/``function.``/``tool.``/``tools.``
    前缀 → 精确命中 → 大小写不敏感唯一命中 → 别名表（``platformHelp``）。

    「唯一命中」是承重条件：注册表里若真出现两个只差大小写的名字，宁可不归一化
    （返回 ``None``，走拒绝路径），也不能替模型挑一个执行——猜错的代价是副作用。
    """
    if not isinstance(name, str):
        return None
    text = name.strip()
    if not text:
        return None
    while True:
        folded_prefix = text.lower()
        prefix = next(
            (item for item in _TOOL_NAME_PREFIXES if folded_prefix.startswith(item)),
            None,
        )
        if prefix is None:
            break
        text = text[len(prefix) :].strip()
        if not text:
            return None
    if text in TOOLS:
        return text
    folded = text.lower()
    matches = [key for key in TOOLS if key.lower() == folded]
    if len(matches) == 1:
        return matches[0]
    alias = TOOL_NAME_ALIASES.get(text) or _ALIASES_FOLDED.get(folded)
    if alias is not None and alias in TOOLS:
        return alias
    return None


def _coerce(tool: str, key: str, value: Any, spec: dict) -> Any:
    """按 schema 收敛一个参数；收敛不了就报 ``AI_AGENT_BAD_ARGUMENT``。

    ``NaN`` / ``±Infinity`` 一律拒绝：它们能通过 ``minimum``/``maximum`` 比较
    （``NaN < 0`` 是 False，所以上下界形同虚设）而一路走到数据库/文件系统，
    最终以「值太大/太小」的驱动异常收场。模型给的 ``arguments`` 是不可信输入，
    非有限数在这里就该断掉。
    """
    kind = spec.get("type")
    if kind == "integer":
        if isinstance(value, bool):
            raise AgentError(
                "AI_AGENT_BAD_ARGUMENT", f"{key} must be an integer", 400
            )
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            raise AgentError(
                "AI_AGENT_BAD_ARGUMENT", f"{key} must be an integer", 400
            ) from None
        if not math.isfinite(number):
            raise AgentError(
                "AI_AGENT_BAD_ARGUMENT", f"{key} must be a finite integer", 400
            )
        value = number
    elif kind == "number":
        if isinstance(value, bool):
            raise AgentError("AI_AGENT_BAD_ARGUMENT", f"{key} must be a number", 400)
        try:
            value = float(value)
        except (TypeError, ValueError, OverflowError):
            raise AgentError(
                "AI_AGENT_BAD_ARGUMENT", f"{key} must be a number", 400
            ) from None
        if not math.isfinite(value):
            raise AgentError(
                "AI_AGENT_BAD_ARGUMENT", f"{key} must be a finite number", 400
            )
    elif kind == "string":
        if not isinstance(value, str):
            raise AgentError("AI_AGENT_BAD_ARGUMENT", f"{key} must be a string", 400)

    if "enum" in spec and value not in spec["enum"]:
        allowed = ", ".join(str(item) for item in spec["enum"])
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT", f"{key} must be one of: {allowed}", 400
        )

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = spec.get("minimum")
        maximum = spec.get("maximum")
        if minimum is not None and value < minimum:
            raise AgentError(
                "AI_AGENT_BAD_ARGUMENT", f"{key} must be >= {minimum}", 400
            )
        if maximum is not None and value > maximum:
            raise AgentError(
                "AI_AGENT_BAD_ARGUMENT", f"{key} must be <= {maximum}", 400
            )
    return value


def _reject_json_constant(token: str) -> Any:
    """``json.loads`` 的 ``parse_constant``：``NaN`` / ``Infinity`` 一律报错。

    Python 的 ``json`` 默认**接受**这三个非标准字面量（``NaN``、``Infinity``、
    ``-Infinity``），而模型很乐意输出它们。让它们进来等于把「无法比较的浮点」
    塞进上下界校验（见 :func:`_coerce`），所以在这里就按非法 JSON 拒掉。
    """
    raise ValueError(f"non-finite number {token} is not allowed")


def prepare_arguments(tool: ToolDef, arguments: object) -> dict:
    """模型给的 ``arguments`` JSON → 校验过的 kwargs。

    ``arguments`` 在 OpenAI 协议里是**字符串**（``function.arguments``），但也有
    实现直接给对象；两种都收。未知参数一律拒绝而不是忽略：忽略会让模型以为
    「我传了过滤条件」，实际拿到全量数据。
    """
    if arguments is None or arguments == "":
        raw: dict = {}
    elif isinstance(arguments, str):
        try:
            raw = json.loads(arguments, parse_constant=_reject_json_constant)
        except ValueError:
            raise AgentError(
                "AI_AGENT_BAD_ARGUMENT", "tool arguments must be valid json", 400
            ) from None
    elif isinstance(arguments, dict):
        raw = dict(arguments)
    else:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT", "tool arguments must be a json object", 400
        )

    if not isinstance(raw, dict):
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT", "tool arguments must be a json object", 400
        )

    properties = tool.params.get("properties") or {}
    required = tool.params.get("required") or []

    unknown = [key for key in raw if key not in properties]
    if unknown:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            f"unknown parameter(s): {', '.join(sorted(unknown))}",
            400,
        )

    cleaned: dict = {}
    for key, value in raw.items():
        if value is None:
            # NULL 不等于「没传」：有默认值的参数显式传 null 时按未传处理，
            # 必填参数则在下一步报缺失。
            continue
        cleaned[key] = _coerce(tool.name, key, value, properties[key])

    missing = [key for key in required if key not in cleaned]
    if missing:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            f"missing required parameter(s): {', '.join(missing)}",
            400,
        )
    return cleaned


def run_allowed(tool: ToolDef, user: dict) -> bool:
    """权限判定（注册表里每个工具一个谓词）。"""
    try:
        return bool(tool.permission(user))
    except Exception:  # noqa: BLE001 - 权限谓词抛错按拒绝处理（fail closed）
        return False


__all__ = [
    "DEPARTMENT_TOOL_NAMES",
    "DIRECT_WRITE_TOOL_NAMES",
    "PLATFORM_HELP_TOOL",
    "READ",
    "TOOL_NAME_ALIASES",
    "TOOLS",
    "VOLUNTEER_TOOL_NAMES",
    "WRITE",
    "ToolDef",
    "get_tool",
    "normalize_tool_name",
    "prepare_arguments",
    "run_allowed",
    "tool_specs",
]
