"""agent 工具注册表的封闭性与参数收敛（离线，无上游、无数据库）。

三条不变式，任何一条破了都等于把能力边界交给了模型：

* **封闭集合**：白名单以外（或不是字符串）的工具名一律 ``None``，executor 据此
  落 ``denied`` 事件并继续对话，而不是 500 或"猜测最接近的工具"；
* **默认只读**：10 个工具里 8 个 read、2 个 write，写操作必须在提案里过一遍；
* **权限镜像**：读工具与对应 REST 端点同口径（登录即可），写工具与
  ``util.permission.volunteer_member`` 的成员写校验同条件。

参数收敛（``prepare_arguments``）是第二道闸：模型给的 ``arguments`` 是不可信
输入，类型/枚举/上下界在这里收，未知参数一律拒绝（静默忽略会让模型以为过滤
条件生效了）。
"""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from services.agent import tools
from services.agent.tool_impl import AgentError
from util.permission import volunteer_member

ADMIN = {"id": "000000000000000000000001", "per": ["admin"]}
DEPARTMENT = {"id": "000000000000000000000002", "per": ["department"]}
SECRETARY = {"id": "000000000000000000000003", "per": ["secretary"]}
STUDENT = {"id": "000000000000000000000004", "per": ["student"]}


# ---------------------------------------------------------------------------
# 注册表封闭性
# ---------------------------------------------------------------------------

EXPECTED_READ = {
    "search_activities",
    "list_activities",
    "get_activity",
    "activity_statistics",
    "list_activity_members",
    "user_volunteer_time",
    "user_activities",
    "list_groups",
}
EXPECTED_WRITE = {"update_member_duration", "set_member_status"}


def test_activity_domain_ten_tools_are_registered() -> None:
    """活动域 10 个工具必须在注册表里，且 kind 不漂移。

    v2 起注册表是**开放**的（运维工具由 ``services.agent.ops_tools`` 追加注册），
    所以这里断言的是「这 10 个仍然在、分类没变、总数不少于 10」，而不是「集合
    恰好等于 10 个」——后者会让每加一个工具都要改这条测试，变成无意义的仪式。
    白名单封闭性由 :func:`test_unknown_tool_names_are_refused` 单独守着。
    """
    assert EXPECTED_READ | EXPECTED_WRITE <= set(tools.TOOLS)
    assert len(tools.TOOLS) >= 10
    assert {name for name in EXPECTED_READ if tools.TOOLS[name].kind == tools.READ} == (
        EXPECTED_READ
    )
    assert {
        name for name in EXPECTED_WRITE if tools.TOOLS[name].kind == tools.WRITE
    } == EXPECTED_WRITE
    for name in EXPECTED_READ:
        assert tools.TOOLS[name].kind == tools.READ, name
    for name in EXPECTED_WRITE:
        assert tools.TOOLS[name].kind == tools.WRITE, name


@pytest.mark.parametrize("name", ["", "drop_table", "search_activity", None, 42, ["x"]])
def test_unknown_tool_names_are_refused(name) -> None:
    assert tools.get_tool(name) is None


def test_every_tool_declares_a_description_and_schema() -> None:
    for tool in tools.TOOLS.values():
        assert tool.description, tool.name
        assert tool.params["type"] == "object", tool.name
        assert isinstance(tool.params.get("properties"), dict), tool.name
        # 参数说明是给模型看的：缺 description 的 schema 会让它瞎猜。
        for key, spec in tool.params["properties"].items():
            assert spec.get("description"), f"{tool.name}.{key}"


def test_tool_specs_use_the_openai_function_calling_shape() -> None:
    specs = tools.tool_specs()

    assert len(specs) == len(tools.TOOLS)
    for spec in specs:
        assert spec["type"] == "function"
        function = spec["function"]
        assert function["name"] in tools.TOOLS
        assert function["parameters"] == tools.TOOLS[function["name"]].params


# ---------------------------------------------------------------------------
# 权限
# ---------------------------------------------------------------------------


def test_read_tools_are_open_to_any_authenticated_user() -> None:
    for name in EXPECTED_READ - {"activity_statistics"}:
        assert tools.run_allowed(tools.TOOLS[name], STUDENT), name


def test_activity_statistics_mirrors_the_rest_reader_rule() -> None:
    """统计读取是「团支书及以上」：镜像 ``activity_statistics_router``。"""
    tool = tools.TOOLS["activity_statistics"]

    assert tools.run_allowed(tool, STUDENT) is False
    for user in (SECRETARY, DEPARTMENT, ADMIN):
        assert tools.run_allowed(tool, user) is True


@pytest.mark.asyncio
async def test_write_tools_mirror_member_validators() -> None:
    """写工具的谓词必须与两个成员写校验函数逐一对齐。

    这里**调用真函数**而不是复制条件：校验函数若放宽（比如加了 secretary），
    谓词没跟着改就会在这条断言上红。
    """
    for name in EXPECTED_WRITE:
        tool = tools.TOOLS[name]
        for user in (ADMIN, DEPARTMENT, SECRETARY, STUDENT):
            allowed = tools.run_allowed(tool, user)
            try:
                # 两个校验函数在「谁能写」上是同一条件；目标参数在分支判定里
                # 完全没被用到（未授权时才 403）。
                await volunteer_member.validate_create_permission(user, None, None)
                await volunteer_member.validate_update_permission(user)
                expected = True
            except HTTPException:
                expected = False
            assert allowed is expected, f"{name} / {user['per']}"


def test_permission_predicate_failure_closes_the_gate() -> None:
    def _boom(_user):
        raise RuntimeError("boom")

    broken = tools.ToolDef(
        name="broken",
        description="x",
        kind=tools.READ,
        params={"type": "object", "properties": {}, "required": []},
        permission=_boom,
        impl=None,  # type: ignore[arg-type]
    )
    assert tools.run_allowed(broken, ADMIN) is False


# ---------------------------------------------------------------------------
# 参数收敛
# ---------------------------------------------------------------------------


def _tool(name: str) -> tools.ToolDef:
    return tools.TOOLS[name]


def test_arguments_accept_a_json_string() -> None:
    cleaned = tools.prepare_arguments(
        _tool("search_activities"), json.dumps({"query": "图书馆"})
    )
    assert cleaned == {"query": "图书馆"}


def test_arguments_accept_an_object() -> None:
    cleaned = tools.prepare_arguments(
        _tool("list_activities"), {"page": 2, "perpage": 20}
    )
    assert cleaned == {"page": 2, "perpage": 20}


def test_numeric_strings_are_coerced() -> None:
    """模型常把 ``limit`` 写成 ``"5"``：报错只会让它反复重试同一个错误。"""
    cleaned = tools.prepare_arguments(_tool("search_activities"), {"query": "x", "limit": "5"})
    assert cleaned["limit"] == 5


@pytest.mark.parametrize(
    "arguments",
    [
        # Python 的 json 默认接受这三个非标准字面量；模型也爱输出。
        '{"query": "x", "limit": NaN}',
        '{"query": "x", "limit": Infinity}',
        '{"query": "x", "limit": -Infinity}',
        # 有限的字面量但溢出成 inf：parse_constant 看不到，靠 _coerce 兜。
        '{"query": "x", "limit": 1e999}',
        {"query": "x", "limit": float("nan")},
        {"query": "x", "limit": float("inf")},
    ],
)
def test_nan_and_infinity_are_refused(arguments) -> None:
    """``NaN`` 能骗过 ``minimum``/``maximum``（``NaN < 0`` 是 False），必须拒。

    这条不是洁癖：``limit=NaN`` 一路走到 SQL 的 ``LIMIT``/``ROWNUM`` 比较，最终
    以驱动异常收场，而模型只会看到「工具坏了」。
    """
    with pytest.raises(AgentError) as excinfo:
        tools.prepare_arguments(_tool("search_activities"), arguments)
    assert excinfo.value.code == "AI_AGENT_BAD_ARGUMENT"
    assert excinfo.value.status_code == 400


def test_float_nan_is_refused_for_number_fields() -> None:
    """``number`` 分支同样拒非有限值（``update_member_duration.duration``）。"""
    with pytest.raises(AgentError) as excinfo:
        tools.prepare_arguments(
            _tool("update_member_duration"),
            {"activity_id": "a" * 24, "member_id": "b" * 24, "duration": float("nan")},
        )
    assert excinfo.value.code == "AI_AGENT_BAD_ARGUMENT"


def test_tool_specs_are_sorted_by_name() -> None:
    """前缀缓存的稳定性：``tools`` 数组的字节不能随注册/import 顺序漂移。"""
    names = [spec["function"]["name"] for spec in tools.tool_specs()]
    assert names == sorted(names)


# ---------------------------------------------------------------------------
# ToolDef v2 注册校验
# ---------------------------------------------------------------------------


def _write_tool(**overrides) -> tools.ToolDef:
    base = dict(
        name="fake_write",
        description="x",
        kind=tools.WRITE,
        params={"type": "object", "properties": {}, "required": []},
        permission=tools._always,
        impl=_noop_impl,
    )
    base.update(overrides)
    return tools.ToolDef(**base)


async def _noop_impl(_user, **_kwargs):
    return {}


async def _noop_prepare(_user, **_kwargs):
    return None


async def _noop_guard(_user):
    return None


def test_read_tools_cannot_declare_prepare_or_dual_auth() -> None:
    """读工具自动执行、没有提案：声明这两项只可能是 kind 抄错了。"""
    for overrides in (
        {"prepare": _noop_prepare},
        {"dual_auth_action": "fs_write"},
    ):
        broken = _write_tool(kind=tools.READ, **overrides)
        with pytest.raises(ValueError):
            tools._register(broken)


def test_dual_auth_write_tool_needs_prepare_and_guard() -> None:
    """声明了 TOTP 却没有预览/权限闸 = 把双人授权做成一句空话。"""
    with pytest.raises(ValueError):
        tools._register(_write_tool(dual_auth_action="fs_write"))
    with pytest.raises(ValueError):
        tools._register(
            _write_tool(dual_auth_action="fs_write", prepare=_noop_prepare)
        )
    with pytest.raises(ValueError):
        tools._register(_write_tool(dual_auth_action="fs_write", guard=_noop_guard))

    tool = tools._register(
        _write_tool(
            dual_auth_action="fs_write", prepare=_noop_prepare, guard=_noop_guard
        )
    )
    try:
        assert tool.dual_auth_action == "fs_write"
    finally:
        tools.TOOLS.pop("fake_write", None)


def test_activity_write_tools_do_not_require_totp() -> None:
    """活动域两条写工具保持 ``requires_dual_auth=false``：行为不变。"""
    for name in EXPECTED_WRITE:
        tool = tools.TOOLS[name]
        assert tool.dual_auth_action is None, name
        assert tool.prepare is None, name
        assert tool.guard is None, name


@pytest.mark.parametrize(
    "arguments",
    [
        {"query": "x", "limit": 21},
        {"query": "x", "limit": 0},
        {"query": "x", "unknown": 1},
        {"limit": 3},
        {"query": 123},
        {"query": "x", "limit": "abc"},
        "not-json",
        [1, 2],
    ],
)
def test_bad_arguments_are_refused(arguments) -> None:
    with pytest.raises(AgentError) as excinfo:
        tools.prepare_arguments(_tool("search_activities"), arguments)
    assert excinfo.value.code == "AI_AGENT_BAD_ARGUMENT"
    assert excinfo.value.status_code == 400


def test_enum_and_number_bounds_are_enforced() -> None:
    status_schema = _tool("set_member_status")

    assert tools.prepare_arguments(
        status_schema,
        {"activity_id": "a" * 24, "member_id": "b" * 24, "status": "effective"},
    )["status"] == "effective"

    with pytest.raises(AgentError):
        tools.prepare_arguments(
            status_schema,
            {"activity_id": "a" * 24, "member_id": "b" * 24, "status": "approved"},
        )

    with pytest.raises(AgentError):
        tools.prepare_arguments(
            _tool("update_member_duration"),
            {"activity_id": "a" * 24, "member_id": "b" * 24, "duration": -1},
        )


def test_explicit_null_is_treated_as_absent() -> None:
    cleaned = tools.prepare_arguments(
        _tool("user_volunteer_time"), {"user_id": None}
    )
    assert cleaned == {}

    with pytest.raises(AgentError):
        tools.prepare_arguments(_tool("search_activities"), {"query": None})


def test_unknown_tool_cannot_be_prepared() -> None:
    assert tools.get_tool("nope") is None
