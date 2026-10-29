"""会话与消息的业务层（``AI_CONVERSATIONS`` / ``AI_MESSAGES``，契约 §3.1）。

这一层做三件事：把仓储行组装成对外视图、把 HTTP 层的意图翻成仓储调用、把「删一
段会话」这种跨表编排写在一个地方。它**不认识执行器，也不认识队列**——轮次落库只
借 ``AIMessagesRepo.append_batch`` 的原子性，run 的生命周期全在
:mod:`services.agent.run_queue`。

两条硬规则：

* **每条路径都先过归属闸**。``AIMessagesRepo`` 按会话 oid 查（消息表没有 owner
  列），所以消息路径必须先 :func:`require_row`；少一次过滤就是跨用户读别人的对话。
* **列表不投影正文**。单会话 200 轮 ≈1.6MB CLOB，翻页时把 ``content`` / ``summary``
  拖回来等于为了排序字段读几 MB；正文只走 ``GET /conversations/{oid}/messages``。

计数列一律读会话行的冗余列（``message_count`` / ``tokens_*``），不 COUNT 消息表:
列表页每行一次 COUNT 就是 N+1，而删除回执要的那个数字也不值得扫 CLOB。
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Mapping, Sequence
from uuid import uuid4

import settings

from db import backends
from services import db_content, settings_runtime
from services.agent.tool_impl import AgentError
from services.ai import prompt_audit

# 会话类型与状态：与表上的 CHECK 逐字一致（写错只会在 Oracle 上变成 ORA-02290，
# 而那时事务已经开了一半）。``chat`` 已下线，只为存量数据保留常量：老会话不能
# 再跑 run（调用方回 409），而不是被当成新数据继续使用。
MODES = ("agent",)
LEGACY_MODES = ("chat",)
STATUSES = ("active", "archived")

# 列宽配额：``title`` VARCHAR2(120)。超了是 ORA-12899，不如在这里截断。
TITLE_MAX = 120

# 分页上限与 HTTP 层同口径（数据层再兜一次，免得写错一层就拉全表）。
LIST_LIMIT_MAX = 100
LIST_LIMIT_DEFAULT = 50
MESSAGE_LIMIT_MAX = 100
MESSAGE_LIMIT_DEFAULT = 50

# 批删量：与 ``tasks/retention.py`` 的范式对齐（一次一条 DELETE，锁 500 行 CLOB）。
DELETE_BATCH = 500
# 批删的循环上限：只是防「删的同时有人一直插」把请求挂死，不是业务上限。
DELETE_MAX_BATCHES = 400

# 作废提案时的扫描窗口：``list_by_actor`` 没有游标，取一页够用的量。待确认提案
# 是「待办」，正常是几条；上限存在的意义是别为一次删除把整张事件表读一遍。
PROPOSAL_SCAN_LIMIT = 500
DELETED_PROPOSAL_SUMMARY = "会话已删除"

# 错误码（契约 §3.4）。路由层原样回出去，前端按 code 分支。
NOT_FOUND = "AI_CONVERSATION_NOT_FOUND"
MODE_MISMATCH = "AI_CONVERSATION_MODE_MISMATCH"
RUN_CONFLICT = "AI_RUN_CONFLICT"
BAD_CURSOR = "AI_CONVERSATION_BAD_CURSOR"
# 模型只在建会话时确定；PATCH 试图改它就是这个码（R2）。
MODEL_LOCKED = "AI_CONVERSATION_MODEL_LOCKED"

# 思考强度：三档，``None`` = 不加任何上游字段（用它自己的默认）。
THINKING_LEVELS = ("low", "medium", "high")

# 只读场景会话（V048 ``AI_CONVERSATIONS.SCENE``）：终端 / 文件编辑 / 义工分析
# 共用会话 + run 管线，但工具声明按 ``kind='read'`` 裁剪，写工具在场景里根本不存
# 在。``None`` = 普通会话；列表查询固定过滤它，场景会话只经
# ``/api/ai/scenes/{scene}/runs`` 触达。值域只在这里（DDL 无 CHECK），加场景只改
# 本元组 + ``SCENE_TITLES`` + 执行器的 ``SCENE_PROMPTS``。
SCENES = ("terminal", "file_edit", "volunteer")
SCENE_TITLES = {
    "terminal": "终端智能",
    "file_edit": "文件编辑智能",
    "volunteer": "义工分析",
}
# 场景会话的保留窗口（天）：一次性排障记录，内容含终端/文件片段，比普通会话激进。
SCENE_RETENTION_DAYS = 14
# 一轮清理最多扫多少条（批删循环上限，防「删的同时有人一直建」把作业挂死）。
SCENE_PURGE_BATCH = 50
SCENE_PURGE_MAX_BATCHES = 20
SCENE_BAD = "AI_SCENE_UNKNOWN"
# 参考内容（终端输出 / 文件片段）的入参上限：与 prompt 留存的上限同量级，
# 再长也没有「参考」的意义，只会挤占上下文。
SCENE_CONTEXT_MAX = 8000

# 键值形态的凭据：``password=...`` / ``token: ...``。值一律替换成 ``***``，
# 保留键名——「这里本来有个 token」本身是读代码需要的信息。
_CREDENTIAL_ASSIGNMENT_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_.-])([A-Za-z0-9_.-]{0,32}(?:password|passwd|pwd|secret|"
    r"token|api[_-]?key|access[_-]?key|private[_-]?key|authorization))"
    r"(\s*[:=]\s*)(\S+)"
)
# 值形态的凭据（散列 / JWT / sk- 前缀）：按空白切词逐个判，命中即整词替换。
_TOKEN_SPLIT_RE = re.compile(r"(\s+)")

# 上下文上限的兜底：``AI_DEFAULT_CONTEXT_LIMIT`` 缺省 200000（见 §5.3）。压缩
# 阈值仍是 0.75，所以约 150K 时触发一次摘要，不会等窗口真填满才动。
CONTEXT_LIMIT_DEFAULT = 200_000

Row = dict[str, Any]


def _repo() -> Any:
    """每次取一个新仓储：它自己开 session，长事务不该藏在模块状态里。"""
    return backends.get_ai_conversations_repo()


def _messages() -> Any:
    return backends.get_ai_messages_repo()


def _runs() -> Any:
    return backends.get_ai_runs_repo()


def _events() -> Any:
    return backends.get_ai_agent_events_repo()


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------

def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _iso(value: object) -> str | None:
    """TIMESTAMP → 字符串；与仓库其余 AI 面同一口径（空格分隔、秒精度）。"""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ", timespec="seconds")
    return value if isinstance(value, str) else None


def _json(value: object) -> object:
    """CLOB 里的 JSON 原文 → 对象；解析不了就原样回（宁可让前端显示原文，也不要
    把一条坏行变成 500）。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _clip_title(title: object) -> str:
    return str(title or "").strip()[:TITLE_MAX]


def _clean_thinking(value: object) -> str | None:
    """思考强度归一：``low`` / ``medium`` / ``high``，其余 400。

    不做「未知值当默认」的宽容：前端拼错一个档位时，静默变成「默认」会让用户以为
    设置生效了，而实际行为一直是默认。
    """
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text not in THINKING_LEVELS:
        raise AgentError(
            "AI_BAD_REQUEST",
            "thinking must be low, medium, high or null",
            400,
        )
    return text


def scene_of(row: Mapping[str, Any]) -> str | None:
    """会话行的场景标记；``None`` = 普通会话（空串也归一成 ``None``）。"""
    value = str(row.get("scene") or "").strip().lower()
    return value or None


def scene_hint() -> str:
    """场景的合法取值提示（按 ``SCENES`` 现拼）。

    不写死名字：以前这里手写过 ``terminal or file_edit``，每加一个场景就得记着改
    三处；漏一处的后果是「合法场景被 400 拒掉」，而错误文案本身就是排查线索。
    """
    return "scene must be one of: " + ", ".join(SCENES)


def is_readonly_scene(value: object) -> bool:
    """是否是只读场景（``SCENES`` 全员）。拼错的值一律按「不是」处理。"""
    return str(value or "").strip().lower() in SCENES


def _clean_scene(value: object) -> str | None:
    """场景归一：``SCENES`` 里的值或 ``None``，其余 400。

    与思考强度同一条纪律：拼错的场景不能静默当成普通会话——那会让一个本该裁掉
    写工具的 run 拿到完整工具集，安全边界变成「拼写对了才有」。
    """
    text = str(value or "").strip().lower()
    if not text:
        return None
    if text not in SCENES:
        raise AgentError(SCENE_BAD, scene_hint(), 400)
    return text


def title_from(content: object) -> str:
    """首条用户消息 → 会话标题（§2.1）。

    压缩换行/连续空白：标题是一行文本，换行会让列表项高度不受控；截断到列宽是
    唯一安全的上限，多出来的信息在消息里，不在标题里。
    """
    return " ".join(str(content or "").split())[:TITLE_MAX]


def context_limit(model: object) -> int:
    """该模型的上下文上限（§5.3 的 ``limit``，视图里的分母）。

    顺序与 :func:`services.agent.context.context_limit` 完全一致：**模型级**
    上下文（凭据里的 ``context``）→ ``AI_MODEL_CONTEXT_LIMITS`` → 默认值。同一个
    窗口在两处算出不同分母，水位就会自相矛盾。

    默认值可以被运行期设置 ``ai.context_limit_default`` 覆盖（在线改完即时生效）。
    配置坏了就回落 200000——分母是展示用的，不能因为一个笔误让会话页 500。
    """
    from . import credentials as ai_credentials

    if isinstance(model, str):
        exact = ai_credentials.model_context(model)
        if exact:
            return exact

    limits = getattr(settings, "AI_MODEL_CONTEXT_LIMITS", None)

    if isinstance(limits, Mapping) and isinstance(model, str):
        # 会话里存的可能是合格 ID（``provider/model``）：窗口是上游模型的属性，
        # 运维配的是裸名，所以先按原值查，再按去掉 provider 前缀的名字查一次。
        for key in (model, model.partition("/")[2]):
            value = limits.get(key) if key else None
            try:
                parsed = int(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                parsed = 0
            if parsed > 0:
                return parsed
    override = settings_runtime.get_override("ai.context_limit_default")
    if isinstance(override, int) and not isinstance(override, bool) and override > 0:
        return override
    try:
        fallback = int(getattr(settings, "AI_DEFAULT_CONTEXT_LIMIT", CONTEXT_LIMIT_DEFAULT))
    except (TypeError, ValueError):
        fallback = CONTEXT_LIMIT_DEFAULT
    return fallback if fallback > 0 else CONTEXT_LIMIT_DEFAULT


# ---------------------------------------------------------------------------
# 游标（base64 的 ``updated_at|oid``）
# ---------------------------------------------------------------------------

def encode_cursor(updated_at: datetime, oid: str) -> str:
    raw = f"{updated_at.isoformat()}|{oid}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    """游标是**不透明句柄**：解不开就是客户端传了脏数据，回 400 而不是静默从头翻
    页（静默重启分页会把第一页重复给用户，比报错更难查）。"""
    try:
        raw = base64.urlsafe_b64decode(str(cursor).encode("ascii")).decode("utf-8")
        stamp, _, oid = raw.partition("|")
        return datetime.fromisoformat(stamp), oid
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise AgentError(BAD_CURSOR, "invalid cursor", 400) from exc


# ---------------------------------------------------------------------------
# 视图
# ---------------------------------------------------------------------------

def conversation_view(row: Mapping[str, Any]) -> dict:
    """会话视图（§3.1）。列表与详情共用：两者的差别只在仓储投影，不在形状。"""
    limit = context_limit(row.get("model"))
    context = _int(row.get("context_tokens"))
    tokens = {
        "prompt": _int(row.get("tokens_prompt")),
        "completion": _int(row.get("tokens_completion")),
        "total": _int(row.get("tokens_total")),
    }
    upto = _int(row.get("summary_upto_seq"))
    return {
        "oid": str(row.get("_id") or ""),
        "mode": row.get("mode"),
        "title": str(row.get("title") or ""),
        "model": row.get("model"),
        # 思考强度（low|medium|high）；``None`` = 上游默认，前端据此显示「默认」。
        "thinking": row.get("thinking"),
        # 只读场景（见 ``SCENES``）；``None`` = 普通会话。
        "scene": row.get("scene"),
        "status": row.get("status"),
        "tokens": tokens,
        "context": {
            "tokens": context,
            "limit": limit,
            # 进度条的分母；超限时夹到 1.0——「占用 130%」会让条撑破容器，而超限
            # 这件事由压缩水位表达，不该由进度条表达。
            "ratio": min(1.0, round(context / limit, 4)) if limit else 0.0,
        },
        "compaction": {
            "upto_seq": upto,
            "summarized": upto > 0,
            "degraded_at": _iso(row.get("compaction_degraded_at")),
        },
        "message_count": _int(row.get("message_count")),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
    }


def _usage_of(row: Mapping[str, Any]) -> dict | None:
    """单条消息的该轮 usage；全为 NULL 就是「上游没报」，回 ``None``（不是 0）。

    0 是真实观测值（这一轮确实没花 token），NULL 是「不知道」；混用会让报表把
    「没报」统计成「免费」。
    """
    keys = (
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
    )
    values = {key: row.get(key) for key in keys}
    if all(value is None for value in values.values()):
        return None
    return {
        key: (None if value is None else _int(value)) for key, value in values.items()
    }


def message_view(row: Mapping[str, Any]) -> dict:
    return {
        "oid": str(row.get("_id") or ""),
        "seq": _int(row.get("seq")),
        "role": row.get("role"),
        "content": str(row.get("content") or ""),
        # 思考过程（上游的 ``reasoning_content``）；NULL = 这一轮没有/上游不返回。
        "reasoning": row.get("reasoning"),
        "tool_calls": _json(row.get("tool_calls")),
        "tool_call_id": row.get("tool_call_id"),
        "tool_name": row.get("tool_name"),
        "round_no": row.get("round_no"),
        "request_id": row.get("request_id"),
        "usage": _usage_of(row),
        "created_at": _iso(row.get("created_at")),
    }


# ---------------------------------------------------------------------------
# 归属闸
# ---------------------------------------------------------------------------

async def fetch_row(owner_oid: str, oid: str) -> Row | None:
    """本人的会话行（``None`` = 不存在或不是本人的）。"""
    return await _repo().get(str(oid), str(owner_oid))


async def require_row(*, user: Mapping[str, Any], oid: str) -> Row:
    """404 一律「不存在」：不区分「没有」与「不是你的」，否则 oid 就成了探针。"""
    row = await fetch_row(str(user.get("id") or ""), oid)
    if row is None:
        raise AgentError(NOT_FOUND, "conversation not found", 404)
    return row


# ---------------------------------------------------------------------------
# 会话 CRUD
# ---------------------------------------------------------------------------

async def create(
    *,
    user: Mapping[str, Any],
    mode: str = "agent",
    title: str | None = None,
    model: str | None = None,
    thinking: str | None = None,
    scene: str | None = None,
) -> dict:
    """建一个会话（``status='active'``，``summary_upto_seq=0``）。

    只保留 agent 语义：``chat`` 模式已下线，建它回 409（存量 chat 会话仍然可以
    读取/改名/删除，但不能跑 run——那是另一种产物）。

    **模型在这里定**（R2）：缺省取该用户可见目录的第一个模型。之后只有在会话还
    没有任何消息（``message_count == 0``）时才能 PATCH 改它——发过消息后换模型会
    让历史重放的缓存前缀与计费口径都换一套，而用户看到的只是「同一段对话」。取目
    录是 best-effort：拿不到（没配 provider / 目录查询失败）就留 ``None``，
    executor 执行时会再按同一条规则回落。
    """
    normalized = str(mode or "").strip().lower() or "agent"
    if normalized in LEGACY_MODES:
        raise AgentError(MODE_MISMATCH, "chat mode is no longer supported", 409)
    if normalized not in MODES:
        raise AgentError("AI_BAD_REQUEST", "mode must be agent", 400)
    chosen = (str(model).strip() or None) if model else None
    if chosen is None:
        chosen = await _default_model(user)
    row = await _repo().create(
        oid=uuid4().hex,
        owner_oid=str(user.get("id") or ""),
        mode=normalized,
        title=_clip_title(title) or None,
        model=chosen,
        thinking=_clean_thinking(thinking),
        scene=_clean_scene(scene),
    )
    return conversation_view(row)


async def _default_model(user: Mapping[str, Any]) -> str | None:
    """目录里的第一个模型（合格 ID）；目录不可用时 ``None``。"""
    from . import config as ai_config

    try:
        available = await ai_config.allowed_models_for(str(user.get("id") or ""))
    except Exception:  # noqa: BLE001 - 建会话不该因为一次目录查询失败而 500
        return None
    return next(iter(available), None)


async def ensure_scene(*, user: Mapping[str, Any], scene: str) -> Row:
    """取 (user, scene) 的最近一条会话，没有就建一条。

    复用而不是「每次新建」：终端 / 文件编辑是**持续的**一次排查过程，同一场景下
    的前文（刚读过哪个文件、哪条命令的输出）就是下一问的上下文；每问一次建一条
    会话等于把 agent 降级成单轮问答。

    已知取舍：会话键只有 ``(owner, scene)``，**不含目标对象**。所以义工分析场景
    里连续问两个不同义工共用同一条历史，旧对象会残留在上下文里。按义工 oid 分键
    需要改会话仓储（唯一键/查询都变），本轮不做；缓解手段在执行器的
    ``SCENE_PROMPTS['volunteer']``：以本轮参考内容为准，并要求回答里确认对象。

    并发建会话的窗口（同一管理员连点两次）可能留下两条场景会话：下一次取到的是
    ``updated_at`` 最新的那条，多余的由 14 天保留期收走——不值得为此加唯一索引，
    那会让「历史被删除后重建」变成一次 ORA-00001。
    """
    normalized = _clean_scene(scene)
    if normalized is None:
        raise AgentError(SCENE_BAD, scene_hint(), 400)
    owner = str(user.get("id") or "")
    row = await _repo().latest_by_scene(owner, normalized)
    if row is not None:
        return row
    return await _repo().create(
        oid=uuid4().hex,
        owner_oid=owner,
        mode="agent",
        title=SCENE_TITLES[normalized],
        model=await _default_model(user),
        thinking=None,
        scene=normalized,
    )


def scene_reference(context: object) -> str | None:
    """终端输出 / 文件片段 → 可安全入库与进 prompt 的「参考内容」。

    三道处理顺序固定：**先脱敏再截断**（反过来会把凭据切成半截，正则再也认不出
    完整的散列/JWT），最后才给长度上限。脱敏复用两处既有实现：

    * ``password=`` / ``token:`` 这类键值形态按键保留、按值替换（读代码时仍能
      看出「这里本来有个 token」）；
    * 值形态的凭据（bcrypt / JWT / 32·64 位 hex / ``sk-`` 前缀）用
      ``services.db_content`` 的同一个判定，避免「文件管理里遮、AI 参考段里不遮」；
    * 身份证与手机号走 ``services.ai.prompt_audit.mask_pii``（与 prompt 留存同口径）。

    返回 ``None`` = 没有参考内容（不产生空的参考段）。
    """
    text = context.strip() if isinstance(context, str) else ""
    if not text:
        return None
    text = _mask_credentials(text)
    text = prompt_audit.mask_pii(text)
    return text[:SCENE_CONTEXT_MAX]


def _mask_credentials(text: str) -> str:
    text = _CREDENTIAL_ASSIGNMENT_RE.sub(r"\1\2***", text)
    parts = _TOKEN_SPLIT_RE.split(text)
    for index, part in enumerate(parts):
        if not part or part.isspace():
            continue
        if db_content._looks_like_credential(part):
            parts[index] = "***"
    return "".join(parts)


async def purge_stale_scenes(
    *, days: int = SCENE_RETENTION_DAYS, now: datetime | None = None
) -> dict:
    """删除 ``updated_at`` 早于保留期的场景会话（走既有级联删除链）。

    与用户点「删除会话」是同一条路径（作废提案 → 批删 messages → runs → 会话），
    所以不会留下孤儿消息或还能被确认的提案。仍有活跃 run 的会话跳过：worker 正在
    上面跑，删掉本体只会让它在半路失败；下一轮（明天）再来收。

    返回 ``{cutoff, deleted, remaining}``，与 ``tasks/retention`` 的只增表口径一致。
    """
    moment = now or datetime.now()
    cutoff = moment - timedelta(days=max(1, int(days)))
    deleted = 0
    scanned = 0
    for _ in range(SCENE_PURGE_MAX_BATCHES):
        rows = await _repo().list_stale_scenes(cutoff, limit=SCENE_PURGE_BATCH)
        if not rows:
            break
        scanned += len(rows)
        for row in rows:
            owner = str(row.get("owner_oid") or "")
            try:
                await delete(user={"id": owner}, oid=str(row.get("_id") or ""))
            except AgentError:
                # 活跃 run / 并发删除：都是「这一轮删不了」，不是清理失败。
                continue
            deleted += 1
        if len(rows) < SCENE_PURGE_BATCH:
            break
    # 扫满批次上限说明还有剩：跳过的那几条（活跃 run）也算剩余。
    remaining = int(await _repo().count_stale_scenes(cutoff))
    return {
        "cutoff": cutoff.isoformat(sep=" ", timespec="seconds"),
        "deleted": deleted,
        "scanned": scanned,
        "remaining": remaining,
    }



async def list_page(
    *,
    user: Mapping[str, Any],
    status: str | None = None,
    cursor: str | None = None,
    limit: int = LIST_LIMIT_DEFAULT,
) -> dict:
    """本人的会话列表（``updated_at`` 倒序，键集分页）。"""
    wanted = (str(status).strip().lower() or None) if status else None
    if wanted is not None and wanted not in STATUSES:
        raise AgentError("AI_BAD_REQUEST", "status must be active or archived", 400)
    size = max(1, min(int(limit or LIST_LIMIT_DEFAULT), LIST_LIMIT_MAX))
    keyset = decode_cursor(cursor) if cursor else None
    rows, next_cursor = await _repo().list_page(
        str(user.get("id") or ""),
        status=wanted,
        cursor=keyset,
        limit=size,
    )
    return {
        "conversations": [conversation_view(row) for row in rows],
        "next_cursor": encode_cursor(*next_cursor) if next_cursor else None,
    }


async def get(*, user: Mapping[str, Any], oid: str) -> dict:
    return conversation_view(await require_row(user=user, oid=oid))


async def update(
    *,
    user: Mapping[str, Any],
    oid: str,
    title: str | None = None,
    status: str | None = None,
    thinking: str | None = None,
    clear_thinking: bool = False,
    model: str | None = None,
    clear_model: bool = False,
) -> dict:
    """改名 / 归档 / 调思考强度 / 空会话改模型。

    **模型在会话发过消息后定死**（R2）：已有消息时换模型会让历史重放的缓存前缀
    与计费口径都换一套，而用户看到的只是「同一段对话」。所以 ``message_count > 0``
    时 ``model`` / ``clear_model`` 一律 409（``AI_CONVERSATION_MODEL_LOCKED``），
    而不是被静默忽略——静默忽略会让前端以为改成功了，实际下一轮仍跑旧模型。

    空会话（``message_count == 0``，刚建出来还没提交过内容）允许改：那时还没有
    可回放的历史，换模型不产生任何「同一段对话两种口径」的事实，而「建完才发现
    选错模型」是唯一的修复窗口。
    """
    owner = str(user.get("id") or "")
    row = await require_row(user=user, oid=str(oid))
    values: dict[str, Any] = {}
    if title is not None:
        values["title"] = _clip_title(title)
    if status is not None:
        normalized = str(status).strip().lower()
        if normalized not in STATUSES:
            raise AgentError("AI_BAD_REQUEST", "status must be active or archived", 400)
        values["status"] = normalized
    if thinking is not None or clear_thinking:
        values["thinking"] = _clean_thinking(thinking)
    clear_model_value = False
    if model is not None or clear_model:
        if _int(row.get("message_count")) > 0:
            raise AgentError(
                MODEL_LOCKED,
                "model is fixed when the conversation is created",
                409,
            )
        # 空串等同「清空」：两者都让执行期回落到目录里的第一个模型。
        chosen = str(model).strip() if model is not None else ""
        values["model"] = chosen or None
        clear_model_value = not chosen
    changed = await _repo().update(
        str(oid),
        owner,
        title=values.get("title"),
        status=values.get("status"),
        thinking=values.get("thinking"),
        clear_thinking=bool(clear_thinking and values.get("thinking") is None),
        model=values.get("model"),
        clear_model=clear_model_value,
    )

    if not changed:
        # 归属闸刚放行、随即 0 行：只可能是并发删除。按「不存在」回，客户端重试
        # 一次就能拿到一致的结论。
        raise AgentError(NOT_FOUND, "conversation not found", 404)
    row = await fetch_row(owner, str(oid))
    if row is None:
        raise AgentError(NOT_FOUND, "conversation not found", 404)
    return conversation_view(row)


async def retitle(*, owner_oid: str, oid: str, title: str) -> None:
    """内部用：首条消息落库后补标题（§2.1），不经过视图。"""
    await _repo().update(str(oid), str(owner_oid), title=_clip_title(title))


# ---------------------------------------------------------------------------
# 删除（§3.1 的三步门禁）
# ---------------------------------------------------------------------------

async def _drain(purge: Callable[[], Awaitable[int]], *, batch: int = DELETE_BATCH) -> int:
    """循环批删到「不足一批」。循环有上限：防「一边删一边有人插」把请求挂死。"""
    total = 0
    for _ in range(DELETE_MAX_BATCHES):
        removed = await purge()
        total += int(removed or 0)
        if int(removed or 0) < batch:
            break
    return total


async def _invalidate_proposals(owner_oid: str, conversation_oid: str) -> int:
    """作废该会话所有 ``proposed`` 提案（评审 P0-3 第二步）。

    为什么必须做：``confirm`` 只校验提案的 ``actor_oid``，**不要求 run 还在**
    （``routers/ai_agent_router.py`` 的确认路径）。删掉会话却不作废提案，等于留着
    一个「用户以为已经删掉」的写操作，30 分钟内还能被执行。

    仓储层没有「按会话批量作废」的接口，所以这里按 actor 的待确认提案逐条回查它
    属于哪次 run（一次 run 只会属于一个会话），命中才 CAS 成 ``failed``。
    """
    events = _events()
    runs = _runs()
    rows = await events.list_by_actor(
        str(owner_oid), status="proposed", limit=PROPOSAL_SCAN_LIMIT
    )
    invalidated = 0
    for row in rows:
        request_id = str(row.get("request_id") or "")
        if not request_id:
            continue
        run = await runs.get(request_id, str(owner_oid))
        if run is None or str(run.get("conversation_oid")) != str(conversation_oid):
            continue
        changed = await events.update_status(
            str(row.get("_id") or row.get("oid") or ""),
            "failed",
            from_status="proposed",
            result_summary=DELETED_PROPOSAL_SUMMARY,
        )
        if changed:
            invalidated += 1
    return invalidated


async def delete(*, user: Mapping[str, Any], oid: str) -> dict:
    """硬删一段会话：门禁 → 作废提案 → 批删 ``messages → runs → conversation``。

    顺序不是风格问题：消息与 run 都必须先删（Oracle 没有级联），而提案作废必须
    发生在 run 还在的时候——run 删完就查不到它属于哪个会话了。
    """
    owner = str(user.get("id") or "")
    await require_row(user=user, oid=str(oid))

    active = await _runs().list_active(owner)
    if any(str(row.get("conversation_oid")) == str(oid) for row in active):
        raise AgentError(
            RUN_CONFLICT, "cancel the active run before deleting", 409
        )

    await _invalidate_proposals(owner, str(oid))

    messages = await _drain(lambda: _messages().purge_by_conversation(str(oid)))
    runs = await _drain(lambda: _runs().purge_by_conversation(str(oid)))
    removed = await _drain(lambda: _repo().delete_batched(str(oid), owner))
    if not removed:
        raise AgentError(NOT_FOUND, "conversation not found", 404)
    return {"deleted": True, "messages": messages, "runs": runs}


# ---------------------------------------------------------------------------
# 消息
# ---------------------------------------------------------------------------

async def list_messages(
    *,
    user: Mapping[str, Any],
    oid: str,
    after_seq: int = 0,
    limit: int = MESSAGE_LIMIT_DEFAULT,
) -> dict:
    """``seq > after_seq`` 的增量（刷新与轮询同一条路径）。

    ``has_more`` 是「这一页可能还有下一批」：取满一页就置真，让客户端再拉一次；
    判少一条也比漏一条便宜。``total`` 读冗余列（不为一个数字扫 CLOB 表）。
    """
    conversation = await require_row(user=user, oid=oid)
    size = max(1, min(int(limit or MESSAGE_LIMIT_DEFAULT), MESSAGE_LIMIT_MAX))
    views = await messages_since(str(oid), max(0, int(after_seq or 0)), limit=size)
    return {
        "messages": views,
        "has_more": len(views) >= size,
        "total": _int(conversation.get("message_count")),
    }


async def messages_since(
    conversation_oid: str, after_seq: int, *, limit: int = MESSAGE_LIMIT_DEFAULT
) -> list[dict]:
    """``seq > after_seq`` 的消息视图（升序）。

    调用方**必须**先过归属闸：消息表没有 owner 列，这条函数只按会话 oid 查。
    ``run_detail`` 复用它是为了让「run 增量」与「消息增量」的序列化形状严格一致
    —— 两处各写一份 view，哪怕只差一个字段，前端也要写两套解析。
    """
    size = max(1, min(int(limit or MESSAGE_LIMIT_DEFAULT), MESSAGE_LIMIT_MAX))
    rows = await _messages().list_after(
        str(conversation_oid), max(0, int(after_seq or 0)), limit=size
    )
    return [message_view(row) for row in rows]


async def append_user(
    conversation_oid: str,
    content: str,
    *,
    request_id: str | None = None,
) -> dict:
    """用户侧的一条消息（chat 同步面的入口；`append_round` 只收 assistant/tool）。

    为什么单独一条函数而不是放开 `append_round` 的角色校验：那条校验守的是
    「一次 run「重跑 queued」不能重复写用户消息」（§4.5），把 user 放进去等于拆掉
    幂等边界。chat 没有 run，用户消息就是这一轮的起点，天然只写一次，所以另设入口。

    `seq` / `message_count` / `updated_at` 仍由 `append_batch` 在一个事务里分配与
    累加——前端拿到的 `seq` 必须与随后 assistant 那条连续。返回值里的 ``oid`` 由本
    函数自己分配（不读仓储的 ``_id`` 别名：不同实现回不同键名，而调用方要用它做
    首帧句柄）。
    """
    oid = uuid4().hex
    row = {
        "oid": oid,
        "role": "user",
        "content": str(content or ""),
        "request_id": str(request_id) if request_id else None,
    }
    inserted = await _messages().append_batch(str(conversation_oid), [row])
    result = dict(inserted[0]) if inserted else {}
    result["oid"] = oid
    result.setdefault("seq", 0)
    return result


async def append_round(
    conversation_oid: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    usage: Mapping[str, int | None] | None = None,
) -> list[dict]:
    """一轮的 assistant + 全部 tool 消息落库，**同一事务**累加会话用量。

    原子性由 ``append_batch`` 保证（锁会话行 → 连续分配 seq → 逐行插入 → 更新会话
    行）。这里只做角色校验：``user`` 消息必须由 run 创建时写（§4.5 幂等边界），
    从这条路径进来只能是 assistant/tool，否则「重跑 queued」就会重复用户消息。
    """
    for row in rows:
        if str(row.get("role")) not in ("assistant", "tool"):
            raise ValueError(f"append_round only accepts assistant/tool, got {row.get('role')!r}")
    return await _messages().append_batch(str(conversation_oid), rows, usage=usage)


__all__ = [
    "BAD_CURSOR",
    "CONTEXT_LIMIT_DEFAULT",
    "DELETED_PROPOSAL_SUMMARY",
    "LIST_LIMIT_DEFAULT",
    "LIST_LIMIT_MAX",
    "LEGACY_MODES",
    "MESSAGE_LIMIT_DEFAULT",
    "MESSAGE_LIMIT_MAX",
    "MODES",
    "MODEL_LOCKED",
    "MODE_MISMATCH",
    "NOT_FOUND",
    "RUN_CONFLICT",
    "SCENE_BAD",
    "SCENE_CONTEXT_MAX",
    "SCENE_PURGE_BATCH",
    "SCENE_RETENTION_DAYS",
    "SCENES",
    "SCENE_TITLES",
    "STATUSES",
    "THINKING_LEVELS",
    "TITLE_MAX",
    "append_round",
    "append_user",
    "context_limit",
    "conversation_view",
    "create",
    "decode_cursor",
    "delete",
    "encode_cursor",
    "ensure_scene",
    "fetch_row",
    "get",
    "is_readonly_scene",
    "scene_hint",
    "list_messages",
    "list_page",
    "message_view",
    "messages_since",
    "purge_stale_scenes",
    "require_row",
    "retitle",
    "scene_reference",
    "scene_of",
    "title_from",
    "update",
]
