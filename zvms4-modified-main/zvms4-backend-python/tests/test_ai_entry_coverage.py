"""AI 上游调用点的覆盖性守卫：新增入口漏接安全认证必须**变红**。

为什么要有这份测试：``safety_guard`` 只在「有人记得调用它」时才生效，而
``providers.chat_completion`` 是一条随时会被新功能再引用一次的低层函数。靠人
review 记住「新入口要过闸」不可靠——漏掉的那一刻不会有任何报错，只会在生产里
表现为「某个入口可以绕开认证」。

做法是**静态扫描**：把 ``routers/`` 与 ``services/`` 下所有调用
``chat_completion`` / ``chat_completion_stream`` 的文件（含
``from ... import chat_completion as cc`` 这类别名）找出来，要求每一个都出现在
:data:`GATED_CALL_SITES`（已接入闸门）或 :data:`EXEMPT_CALL_SITES`（有正当理由
不需要闸门）里。新增文件没登记 → 本测试失败，并在断言信息里列出调用点。

这份扫描只看「有没有调用」，不判断逻辑对错；调用点内部是否真的调用了
``screen`` 由各入口自己的用例负责。

同一个文件里还有第二条同类守卫（见「功能开关（V043 ROLE_FEATURES）覆盖性」）：
``routers/`` 下每个 import 了 AI 接缝（providers / run_queue / executor /
scheduled_runner / textgen / OCR）的模块，必须调用 ``ensure_feature`` /
``has_feature``，或在 ``FEATURE_GATE_EXEMPT`` 写明理由。会话主入口曾经只过角色
白名单、漏了 ``ai.chat`` / ``ai.agent``，靠人工 review 记住是不可靠的。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRS = ("routers", "services")

PROVIDERS_MODULE = "services.ai.providers"
API_NAMES = frozenset({"chat_completion", "chat_completion_stream"})


@dataclass(frozen=True)
class CallSite:
    """扫描到的一个调用点（足够定位到「哪个文件的哪个函数」）。"""

    path: str
    function: str
    api: str
    lineno: int

    def __str__(self) -> str:  # 失败信息里直接可读
        return f"{self.path}:{self.lineno} {self.function}() -> {self.api}"


#: 已接入 LLM 安全认证（或其调用者已在更外层拦截）的调用点。
GATED_CALL_SITES: dict[str, str] = {
    "routers/ai_router.py": (
        "OpenAI 兼容面 /v1/chat/completions：_screen_chat_request 在候选选择与"
        "频控/额度之前拦截 department（JWT 与 zvk_ key 的 owner 同一套判定）"
    ),
    "services/agent/run_queue.py": (
        "agent 提交入口：submit() 在入队前接 safety_guard.screen（本文件不直接调"
        "providers，登记它是因为它才是真正的用户入口）"
    ),
    "services/ai/scheduled_runner.py": (
        "定时任务提交入口：submit_task_run() 在入队前按 department owner 过一遍"
        "安全认证；判定成立记违规并跳过本周期，判定跑不起来只跳过（本文件不直接"
        "调 providers）"
    ),
    "services/agent/executor.py": (
        "agent 每轮上游调用：已在 run_queue.submit 的提交入口拦截，执行期不再重复"
        "调用判定（同一 prompt 每轮再审一次既贵又会把正常任务打断）"
    ),
    "services/ai/textgen.py": (
        "SSH 命令起草：调用者必然是 admin（路由层 admin-only），is_department 恒"
        "为 False，挂安全认证只会白烧一次上游调用"
    ),
}

#: 有正当理由**不**接闸门的调用点（理由必填，供 review 与审计追溯）。
#:
#: OCR（``POST /api/ai/ocr``、``POST /api/ai/volunteer-ocr``）**不在**这份清单里：
#: 它们的输入是图片，走 ``services/ai/ocr*.py`` 的 OCR 协议，从不调用
#: ``chat_completion``。没有可审的用户文本，所以既不是调用点，也不需要豁免条目。
EXEMPT_CALL_SITES: dict[str, str] = {
    "services/ai/safety_guard.py": (
        "安全认证自身的判定调用：闸门不能审自己（递归调用 + 自己坏掉时无法"
        "fail-open）"
    ),
}


# ---------------------------------------------------------------------------
# AST 扫描
# ---------------------------------------------------------------------------


def _chain(node: ast.AST) -> list[str]:
    """``a.b.c`` → ``["a","b","c"]``；遇到非 Name 返回空。"""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return []
    parts.append(node.id)
    return list(reversed(parts))


def _aliases(tree: ast.AST) -> tuple[set[str], dict[str, str]]:
    """本文件里指向 providers 模块 / 两个函数的本地名字。

    覆盖三种写法：

    * ``from services.ai import providers``（以及 ``as prov``）
    * ``import services.ai.providers`` / ``import ... as p``
    * ``from services.ai.providers import chat_completion``（以及 ``as cc``）

    ``direct_names`` 是 ``本地名 → 真实 API 名``：``cc`` 也要被记成
    ``chat_completion``，否则报告里的调用点会变成「调用了 cc」这种不可读的名字。
    """
    module_aliases: set[str] = {PROVIDERS_MODULE, "providers"}
    direct_names: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == PROVIDERS_MODULE:
                for alias in node.names:
                    if alias.name in API_NAMES:
                        direct_names[alias.asname or alias.name] = alias.name
            elif node.module in ("services.ai", "services") or node.module.startswith(
                "services.ai."
            ):
                for alias in node.names:
                    if alias.name == "providers":
                        module_aliases.add(alias.asname or "providers")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == PROVIDERS_MODULE:
                    # ``import services.ai.providers`` binds ``services``.
                    module_aliases.add(alias.asname or PROVIDERS_MODULE)
                    if alias.asname is None:
                        module_aliases.add("services")
    return module_aliases, direct_names


def _walk_calls(
    node: ast.AST,
    *,
    relpath: str,
    function: str,
    module_aliases: set[str],
    direct_names: dict[str, str],
    out: list[CallSite],
) -> None:
    for child in ast.iter_child_nodes(node):
        scope = function
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope = f"{function}.{child.name}" if function != "<module>" else child.name
        elif isinstance(child, ast.ClassDef):
            scope = f"{function}.{child.name}" if function != "<module>" else child.name

        if isinstance(child, ast.Call):
            target = _call_target(child, module_aliases, direct_names)
            if target is not None:
                out.append(CallSite(relpath, function, target, child.lineno))

        _walk_calls(
            child,
            relpath=relpath,
            function=scope,
            module_aliases=module_aliases,
            direct_names=direct_names,
            out=out,
        )


def _call_target(
    call: ast.Call, module_aliases: set[str], direct_names: dict[str, str]
) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return direct_names.get(func.id)
    if not isinstance(func, ast.Attribute):
        return None
    chain = _chain(func)
    if len(chain) < 2 or chain[-1] not in API_NAMES:
        return None
    prefix = ".".join(chain[:-1])
    return chain[-1] if prefix in module_aliases else None


def scan_call_sites(root: Path, dirs: tuple[str, ...] = SCAN_DIRS) -> list[CallSite]:
    """扫描 ``root/<dir>/**/*.py``，返回所有 providers 上游调用点。"""
    sites: list[CallSite] = []
    for directory in dirs:
        base = root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            relpath = path.relative_to(root).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relpath)
            module_aliases, direct_names = _aliases(tree)
            _walk_calls(
                tree,
                relpath=relpath,
                function="<module>",
                module_aliases=module_aliases,
                direct_names=direct_names,
                out=sites,
            )
    return sites


def unregistered_sites(
    root: Path,
    *,
    gated: dict[str, str] | None = None,
    exempt: dict[str, str] | None = None,
) -> list[CallSite]:
    """找出「扫描到了但两个清单都没登记」的调用点。"""
    known = set(GATED_CALL_SITES if gated is None else gated)
    known |= set(EXEMPT_CALL_SITES if exempt is None else exempt)
    return [site for site in scan_call_sites(root) if site.path not in known]


# ---------------------------------------------------------------------------
# 真实仓库：每个调用点都必须登记
# ---------------------------------------------------------------------------


def test_every_provider_call_site_is_registered() -> None:
    missing = unregistered_sites(BACKEND_ROOT)
    assert missing == [], (
        "发现未登记的上游调用点——新增 AI 文本入口必须接 safety_guard.screen，"
        "或在 EXEMPT_CALL_SITES 写明豁免理由：\n"
        + "\n".join(f"  - {site}" for site in missing)
    )


def test_gated_and_exempt_lists_do_not_overlap() -> None:
    overlap = set(GATED_CALL_SITES) & set(EXEMPT_CALL_SITES)
    assert overlap == set(), f"同一文件不能既已接入又豁免：{sorted(overlap)}"


def test_every_entry_has_a_reason() -> None:
    for sites, label in (
        (GATED_CALL_SITES, "GATED"),
        (EXEMPT_CALL_SITES, "EXEMPT"),
    ):
        for path, reason in sites.items():
            assert reason.strip(), f"{label} 缺少理由：{path}"


def test_required_entries_are_present() -> None:
    """这几个入口是产品契约的一部分：删掉登记等于静默放弃闸门。"""
    gated = set(GATED_CALL_SITES)
    assert {
        "routers/ai_router.py",
        "services/agent/run_queue.py",
        "services/agent/executor.py",
        "services/ai/textgen.py",
    } <= gated


def test_scan_finds_the_real_entry_points() -> None:
    """扫描本身要有产出：零命中说明规则写坏了，等于永远绿。"""
    paths = {site.path for site in scan_call_sites(BACKEND_ROOT)}
    assert {
        "routers/ai_router.py",
        "services/agent/executor.py",
        "services/ai/textgen.py",
        "services/ai/safety_guard.py",
    } <= paths


# ---------------------------------------------------------------------------
# 扫描器自身：正例 / 负例
# ---------------------------------------------------------------------------


def _temp_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return tmp_path


def test_scanner_handles_import_aliases(tmp_path: Path) -> None:
    root = _temp_tree(
        tmp_path,
        {
            "routers/alias_direct.py": (
                "from services.ai.providers import chat_completion as cc\n"
                "async def run(p, e, k):\n"
                "    return await cc(p, e, k)\n"
            ),
            "services/alias_module.py": (
                "import services.ai.providers as prov\n"
                "async def go(p, e, k):\n"
                "    return await prov.chat_completion_stream(p, e, k)\n"
            ),
        },
    )

    found = {(site.path, site.function, site.api) for site in scan_call_sites(root)}

    assert (
        "routers/alias_direct.py",
        "run",
        "chat_completion",
    ) in found
    assert (
        "services/alias_module.py",
        "go",
        "chat_completion_stream",
    ) in found


def test_new_unregistered_call_site_fails_the_guard(tmp_path: Path) -> None:
    """负例：新文件里的调用没登记 → ``unregistered_sites`` 必须报出来。"""
    root = _temp_tree(
        tmp_path,
        {
            "routers/brand_new_entry.py": (
                "from services.ai import providers\n"
                "async def handler(p, e, k):\n"
                "    return await providers.chat_completion(p, e, k)\n"
            ),
        },
    )

    missing = unregistered_sites(root, gated={}, exempt={})

    assert [(site.path, site.function) for site in missing] == [
        ("routers/brand_new_entry.py", "handler")
    ]


def test_registered_call_site_passes_the_guard(tmp_path: Path) -> None:
    """正例：同样的临时文件登记后不再报错。"""
    root = _temp_tree(
        tmp_path,
        {
            "routers/brand_new_entry.py": (
                "from services.ai import providers\n"
                "async def handler(p, e, k):\n"
                "    return await providers.chat_completion(p, e, k)\n"
            ),
        },
    )

    assert (
        unregistered_sites(
            root, gated={"routers/brand_new_entry.py": "已接闸门"}, exempt={}
        )
        == []
    )


def test_ignores_unrelated_functions_with_the_same_name(tmp_path: Path) -> None:
    """本地同名函数不算 providers 调用（否则扫描会到处误报）。"""
    root = _temp_tree(
        tmp_path,
        {
            "services/local.py": (
                "async def chat_completion(p, e, k):\n"
                "    return None\n"
                "async def run():\n"
                "    return await chat_completion({}, {}, '')\n"
            ),
        },
    )

    assert scan_call_sites(root) == []


# ---------------------------------------------------------------------------
# 功能开关（V043 ROLE_FEATURES）覆盖性：只接安全认证不够，还得接开关
# ---------------------------------------------------------------------------
#
# 为什么再加一条：``safety_guard`` 守的是「说了什么」，``ROLE_FEATURES`` 守的是
# 「这个角色能不能用」。会话主入口（``ai_conversations_router``）曾经只过角色
# 白名单：管理员在「角色策略」页关掉 ``ai.agent``，用户照样能建会话、提 run。
# 漏接的那一刻同样没有任何报错，所以这里把「谁能把请求送到模型」也做成静态清单。

FEATURES_MODULE = "util.permission.features"
FEATURE_APIS = frozenset({"ensure_feature", "has_feature", "require_feature"})

#: 「能把请求真的送到模型 / 入队」的接缝模块：import 它们就等于这个 router 是
#: 一个 AI 入口。OCR 也算（``ai.ocr`` 是同一套开关）。
AI_SEAM_MODULES = frozenset(
    {
        "services.ai.providers",
        "services.ai.scheduled_runner",
        "services.ai.textgen",
        "services.ai.ocr",
        "services.ai.ocr_engines",
        "services.ai.ocr_engines_admin",
        "services.agent.run_queue",
        "services.agent.executor",
        "services.agent.proposal",
    }
)

#: ``from <父包> import <接缝>`` 的写法（``from services.ai import providers``）。
AI_SEAM_PARENTS: dict[str, frozenset[str]] = {
    "services.ai": frozenset(
        {"providers", "scheduled_runner", "textgen", "ocr", "ocr_engines", "ocr_engines_admin"}
    ),
    "services.agent": frozenset({"run_queue", "executor", "proposal"}),
}

#: 有正当理由**不**接开关的 AI 入口（理由必填）。
#:
#: 目前只有一条：SSH 命令起草（``/api/system/ssh/suggest``）是「严格 admin +
#: 即时查库复核」的端点（``system_ssh_router._require_system_admin``）。策略层
#: ``admin`` 恒为 True（``policy.feature_enabled`` 的代码短路），在这里挂开关等于
#: 挂一个恒真的判断——审查者应该把它当「已由更强的角色闸门覆盖」，不是漏接。
FEATURE_GATE_EXEMPT: dict[str, str] = {
    "routers/system_ssh_router.py": (
        "SSH 命令起草：_require_system_admin 要求严格 admin + 即时查库复核；"
        "策略层 admin 恒 True，接开关只会得到恒真判断"
    ),
    "routers/ai_models_router.py": (
        "模型目录管理是 admin 配置面（_require_admin），写路径另有密码再验证；"
        "POST /discover 只是代管渠道去调它自己的 /models 取元数据（上下文窗口），"
        "不产出任何内容、不下发 prompt。按功能开关拦截会形成死锁：关掉 AI 功能后"
        "管理员就再也无法配置/修复模型目录，连重新打开功能所需的渠道信息都配不了。"
    ),
}


@dataclass(frozen=True)
class AiEntry:
    """一个 AI 入口模块：它用到的接缝 + 它调用的开关 API。"""

    path: str
    seams: tuple[str, ...]
    gates: tuple[str, ...]

    def __str__(self) -> str:
        return (
            f"{self.path} (接缝: {', '.join(self.seams)}; "
            f"开关: {', '.join(self.gates) or '无'})"
        )


def _ai_seams(tree: ast.AST) -> set[str]:
    """本模块 import 了哪些 AI 接缝。"""
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module in AI_SEAM_MODULES:
                found.add(node.module)
            parents = AI_SEAM_PARENTS.get(node.module, frozenset())
            for alias in node.names:
                child = f"{node.module}.{alias.name}"
                if child in AI_SEAM_MODULES or alias.name in parents:
                    found.add(child)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in AI_SEAM_MODULES:
                    found.add(alias.name)
    return found


def _feature_gate_calls(tree: ast.AST) -> set[str]:
    """本模块真的调用了哪些开关 API（``ensure_feature`` / ``has_feature`` / ...）。

    只看调用、不看 import：``from util.permission.features import ensure_feature``
    之后一次都没用，等于没接闸门。
    """
    direct: dict[str, str] = {}
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == FEATURES_MODULE:
                for alias in node.names:
                    if alias.name in FEATURE_APIS:
                        direct[alias.asname or alias.name] = alias.name
            elif node.module == "util.permission":
                for alias in node.names:
                    if alias.name == "features":
                        modules.add(alias.asname or "features")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == FEATURES_MODULE:
                    modules.add(alias.asname or FEATURES_MODULE)

    calls: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in direct:
            calls.add(direct[func.id])
        elif isinstance(func, ast.Attribute) and func.attr in FEATURE_APIS:
            if ".".join(_chain(func.value)) in modules:
                calls.add(func.attr)
    return calls


def scan_ai_entries(root: Path) -> list[AiEntry]:
    """扫描 ``root/routers/**/*.py``，返回所有触达 AI 的模块及其开关接入情况。"""
    entries: list[AiEntry] = []
    base = root / "routers"
    if not base.is_dir():
        return entries
    for path in sorted(base.rglob("*.py")):
        relpath = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relpath)
        seams = _ai_seams(tree)
        if not seams:
            continue
        entries.append(
            AiEntry(relpath, tuple(sorted(seams)), tuple(sorted(_feature_gate_calls(tree))))
        )
    return entries


def ungated_ai_entries(
    root: Path, *, exempt: dict[str, str] | None = None
) -> list[AiEntry]:
    """找出「是 AI 入口但一次开关都没调、也没登记豁免」的模块。"""
    allowed = FEATURE_GATE_EXEMPT if exempt is None else exempt
    return [entry for entry in scan_ai_entries(root) if not entry.gates and entry.path not in allowed]


def test_every_ai_router_entry_calls_a_feature_gate() -> None:
    missing = ungated_ai_entries(BACKEND_ROOT)
    assert missing == [], (
        "发现没接功能开关的 AI 入口——新增入口必须调用 ensure_feature / has_feature"
        "（或 require_feature 依赖），或在 FEATURE_GATE_EXEMPT 写明豁免理由：\n"
        + "\n".join(f"  - {entry}" for entry in missing)
    )


def test_ai_router_entries_are_actually_found() -> None:
    """扫描要有产出：零命中说明规则写坏了，等于永远绿。"""
    paths = {entry.path for entry in scan_ai_entries(BACKEND_ROOT)}
    assert {
        "routers/ai_router.py",
        "routers/ai_agent_router.py",
        "routers/ai_schedule_router.py",
        "routers/ai_conversations_router.py",
        "routers/attachments_router.py",
    } <= paths


def test_conversation_entries_are_gated() -> None:
    """会话主入口是这条守卫存在的原因：它们必须一直在扫描结果里且带闸门。"""
    entries = {entry.path: entry for entry in scan_ai_entries(BACKEND_ROOT)}

    for path in ("routers/ai_conversations_router.py",):
        assert path in entries, f"扫描没看见 {path}"
        assert entries[path].gates, f"{path} 没接功能开关"


def test_feature_gate_exemptions_have_reasons() -> None:
    for path, reason in FEATURE_GATE_EXEMPT.items():
        assert reason.strip(), f"FEATURE_GATE_EXEMPT 缺少理由：{path}"


def test_new_ai_entry_without_a_gate_fails_the_feature_guard(tmp_path: Path) -> None:
    """负例：新 router import 了 providers 但没用开关 → 必须报出来。"""
    root = _temp_tree(
        tmp_path,
        {
            "routers/brand_new_entry.py": (
                "from services.ai import providers\n"
                "async def handler(p, e, k):\n"
                "    return await providers.chat_completion(p, e, k)\n"
            ),
        },
    )

    missing = ungated_ai_entries(root, exempt={})

    assert [(entry.path, entry.seams) for entry in missing] == [
        ("routers/brand_new_entry.py", ("services.ai.providers",))
    ]


def test_ai_entry_with_a_gate_or_an_exemption_passes(tmp_path: Path) -> None:
    """正例：接上闸门，或写进豁免清单，都不再报错。"""
    gated = _temp_tree(
        tmp_path / "gated",
        {
            "routers/entry.py": (
                "from services.agent import run_queue\n"
                "from util.permission.features import ensure_feature\n"
                "async def handler(user):\n"
                "    ensure_feature(user, 'ai.agent')\n"
                "    return await run_queue.submit(user=user)\n"
            ),
        },
    )
    exempt = _temp_tree(
        tmp_path / "exempt",
        {
            "routers/entry.py": (
                "from services.ai import textgen\n"
                "async def handler():\n"
                "    return await textgen.draft()\n"
            ),
        },
    )

    assert ungated_ai_entries(gated) == []
    assert ungated_ai_entries(exempt) == [
        AiEntry("routers/entry.py", ("services.ai.textgen",), ())
    ]
    assert ungated_ai_entries(exempt, exempt={"routers/entry.py": "内部自检，没有 HTTP 入口"}) == []

