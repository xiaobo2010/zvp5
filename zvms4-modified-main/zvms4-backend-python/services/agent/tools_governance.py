"""平台治理工具（5 读 + 4 写）：AI 网关、附件存储、通知公告、活动与义工核查。

注册表语义与 ``ops_tools`` 完全一致，所以这里不重写第二套：

* **读工具直接执行**，但每次都过 ``guard=ops_auth.ensure_system_admin`` 现查库
  ——token 里的 ``per`` 只是快照，撤权与封禁必须立刻生效；
* **写工具只出提案**：``prepare`` 只读、只产人读草案与结构化预览，``impl`` 在
  另一位管理员确认（6 位动态码）之后才被调用；``dual_auth_action`` 一填，
  ``proposal`` 就强制要求 ``prepare`` + ``guard`` 同时在位；
* 结果一律过 ``_cap``：模型看到的是「被截断了」，而不是半句 JSON。

本模块额外的铁律 —— 都是「别把治理工具做成泄露面」：

* ``AI_API_KEYS`` 只读 ``oid / name / user_oid / enabled / expires_at``。
  ``token_hash`` 与 ``key_hint`` 不出现在任何返回里：不返回密钥不是靠脱敏，
  是靠**不选**。
* 审计摘要先过 :func:`_mask_contacts`（手机号/邮箱），正文与密钥不进日志。
* 附件统计/孤儿检测/GC 预览都走单条聚合（``FilesRepo.count_snapshot``），
  不把 ``FILES`` 整表拉进内存，也永不投影 ``CONTENT``。
* ``notify_send`` 拒绝全站广播（``target.kind='all'``），目标上限 200 —— 一个
  模型不该有能力把公告板刷给全校。

口径说明（实现里都有一行注释对应）：

* 活动没有「标准时长」列（``ACTIVITIES`` 只存 ``activity_date`` 与类型），所以
  ``volunteer_audit`` 的「时长缺口」按**同一活动同一 mode 内的满额时长**
  （``max(duration)``）当基准，并在结果里带 ``standard_basis`` 说明来路 ——
  编造一个不存在的标准值比不给更糟。
* ``NOTIFICATIONS`` 是**全局公告板**（一行 = 所有人可见），没有「收件人」列。
  因此定向发送落成**一条**公告并在标题上标受众，而不是按人复制 N 条 ——
  复制只会把定向消息变成全校刷屏，与「拒绝 all」自相矛盾。
"""

from __future__ import annotations

import functools
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from db import backends
from db.ids import is_valid_oid
from db.oracle.session import session_scope
from db.repos.protocols import SHARED_OWNER_OID
from services import db_content
from services.ai import api_keys, quota
from tasks import ocr_upload_gc
from util.dual_auth import audit_system_op
from util.user import get_user_display_name

from . import ops_auth
from .ops_tools import WRITE_AUTH_ACTION, _admin_only, _cap, _draft
from .tool_impl import AgentError
from .tools import READ, WRITE, ToolDef, _register

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# 上限（都在 schema 里出现，服务端再夹一次：schema 只是给模型的建议）
# --------------------------------------------------------------------------- #

GATEWAY_DAYS_MIN = 1
GATEWAY_DAYS_MAX = 30
GATEWAY_DEFAULT_DAYS = 7
# 7 天内到期算「即将失效」：key 的到期是硬失效，留一周给人换凭据。
KEY_EXPIRY_SOON_DAYS = 7
# 异常清单一条查询最多取多少行（治理面板不需要全量，也不该全量）。
KEY_SCAN_MAX = 100
STATUS_TOP_USERS = 5
STATUS_TOP_MODELS = 5

PROMPT_DAYS_MIN = 1
PROMPT_DAYS_MAX = 90
PROMPT_DEFAULT_DAYS = 7
PROMPT_PAGE_MAX = 50
PROMPT_PAGE_DEFAULT = 20

ATTACHMENT_GC_RETENTION_DEFAULT = 3
ATTACHMENT_GC_RETENTION_MAX = 90
ATTACHMENT_GC_MAX_BATCHES = 10
ATTACHMENT_GC_BATCH_DEFAULT = 3

VOLUNTEER_DAYS_DEFAULT = 30
VOLUNTEER_DAYS_MAX = 365
# 逐个活动取成员是一活动一条查询；扫太多会让一次只读变成几十次往返。
VOLUNTEER_SCAN_MAX = 30
# 三类异常各列 10 条，合计 30 条（契约上限）。
VOLUNTEER_ITEMS_PER_BUCKET = 10

QUOTA_LIMIT_MAX = 100_000
# 用户侧额度只有一个 AF 池（V051）：agent 的 ai_quota_set 只认 ``af``，
# 站点级 ``ocr_<engine>`` 是平台出资的另一条链路，不在这里设。
QUOTA_KINDS = ("af",)

NOTIFY_RECIPIENTS_MAX = 200
NOTIFY_TITLE_MAX = 200  # NOTIFICATIONS.TITLE 是 VARCHAR2(200)
NOTIFY_CONTENT_MAX = 4000  # NOTIFICATIONS.CONTENT 是 VARCHAR2(4000)
NOTIFY_SAMPLE_MAX = 5
# 标题前缀里的受众标签最多多少字（名字字段是 VARCHAR2(128)，前缀不能无限长）。
AUDIENCE_LABEL_MAX = 60

AUDIT_LOG_TYPE = "ai_governance"
# 读取 prompt 正文的审计类型与端点 ``GET /api/ai/prompts/{oid}`` 一致：同一个
# 筛选条件在日志页里才能一次看全「谁看过学生对话」。
PROMPT_ACCESS_LOG_TYPE = "ai_prompt_access"


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #


def _clamp(value: Any, low: int, high: int, default: int) -> int:
    """不可信整数 → ``[low, high]``。失败就回默认值，不抛（调用方已声明范围）。"""
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(low, min(number, high))


def _iso(value: Any) -> str | None:
    moment = _as_datetime(value)
    return moment.isoformat(sep=" ", timespec="seconds") if moment else None


def _as_datetime(value: Any) -> datetime | None:
    """``datetime`` 或 ISO 字符串 → ``datetime``；其它一律 ``None``。

    跨层来源不同：仓储给 ``datetime``，``db_content.run_query`` 给序列化后的
    字符串。治理工具要拿两种情况都比得了时间。
    """
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _as_flag(value: Any, default: bool = True) -> bool:
    """``enabled`` 三种形态都收：``True/False``、``1/0``、``'1'/'0'``。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return default


def _lower_keys(row: dict) -> dict:
    """``run_query`` 的列名是驱动原样（Oracle 大写），统一成小写再读。"""
    return {str(key).lower(): value for key, value in row.items()}


_MOBILE_RE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def _mask_contacts(text: str) -> str:
    """日志/摘要里的手机号与邮箱脱敏（保留号段与后两位，够定位不够复原）。"""
    masked = _MOBILE_RE.sub(lambda m: f"{m.group(1)[:3]}****{m.group(1)[-2:]}", text)
    return _EMAIL_RE.sub("<email>", masked)


async def _audit(
    user: dict,
    action: str,
    detail: str,
    *,
    log_type: str = AUDIT_LOG_TYPE,
    level: str = "warning",
) -> None:
    """审计是旁路：写失败只记 warning，绝不把「已经做完的事」报成失败。"""
    try:
        await audit_system_op(
            str(user.get("id") or ""),
            action=action,
            detail=_mask_contacts(detail)[:500],
            log_type=log_type,
            level=level,
        )
    except Exception:  # noqa: BLE001 - 审计写失败不改变业务结论
        logger.warning("agent %s audit write failed", action, exc_info=True)


def _translate_quota_errors(
    func: Callable[..., Awaitable[Any]],
) -> Callable[..., Awaitable[Any]]:
    """``QuotaError`` / ``ApiKeyError`` → ``AgentError``（状态码原样带过去）。

    不翻译的话模型看到的是「工具失败」，改一个字符重试也没用；翻译后它至少知道
    是参数错（400）还是目标不存在（404）。
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except AgentError:
            raise
        except (quota.QuotaError, api_keys.ApiKeyError) as exc:
            raise AgentError(
                getattr(exc, "code", "AI_AGENT_TOOL_FAILED"),
                getattr(exc, "message", str(exc)),
                int(getattr(exc, "status_code", 400) or 400),
            ) from None

    return wrapper


async def _load_user(value: str) -> dict:
    """用户 oid 或学号 → 用户行；都没有就 404（不编造空用户）。"""
    target = str(value or "").strip()
    if not target:
        raise AgentError("AI_AGENT_BAD_ARGUMENT", "user must not be empty", 400)
    repo = backends.get_users_repo()
    row = await repo.get_by_oid(target) if is_valid_oid(target) else None
    if row is None:
        row = await repo.find_by_business_id(target)
    if row is None:
        raise AgentError("AI_AGENT_NOT_FOUND", "user not found", 404)
    return row


async def _load_api_key(value: str) -> dict:
    target = str(value or "").strip()
    if not is_valid_oid(target):
        raise AgentError("AI_AGENT_NOT_FOUND", "api key not found", 404)
    row = await backends.get_ai_api_keys_repo().get_by_oid(target)
    if row is None:
        raise AgentError("AI_AGENT_NOT_FOUND", "api key not found", 404)
    return row


def _params(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_STR = {"type": "string"}
_INT = {"type": "integer"}
_OBJ = {"type": "object"}


# --------------------------------------------------------------------------- #
# 读工具：AI 网关
# --------------------------------------------------------------------------- #


async def _site_quota_rows() -> list[dict]:
    """站点级（平台出资）额度：按引擎分开记在哨兵行上。

    行不存在时的展示值必须与扣费口径一致，所以取
    ``quota.default_limit_for(None, kind)``（站点口径 → ``AI_SITE_QUOTA`` →
    ``AI_DEFAULT_SITE_OCR_MONTHLY_UNITS``），不自己编一个默认数字。
    """
    period = quota.period_now()
    repo = backends.get_ai_quota_repo()
    rows: list[dict] = []
    for kind in quota.SITE_KINDS:
        row = await repo.get(SHARED_OWNER_OID, period, kind)
        if row is None:
            used, limit, source = 0, quota.default_limit_for(None, kind), "global_default"
        else:
            used = int(row.get("used") or 0)
            limit = int(row.get("limit_count") or 0)
            source = "explicit"
        rows.append(
            {
                "kind": kind,
                "period": period,
                "used": used,
                "limit": limit,
                "source": source,
            }
        )
    return rows


def _api_key_anomaly_sql(now: datetime) -> str:
    """异常 key 的只读查询：**只选非敏感列**，异常判定在库里完成。

    为什么不用 ``read_rows``：它按整表投影（会把 ``token_hash`` 也选出来再打
    码），而且只能在 Python 里筛一页。这里显式列名 + ``WHERE``，取回的每一行
    都是「确实需要处理」的 key，且永远不含密钥材料。

    ``TO_TIMESTAMP`` 的字面量由 ``datetime`` 格式化而来（不含用户输入），
    ``run_query`` 本身不接受绑定参数。
    """
    deadline = (now + timedelta(days=KEY_EXPIRY_SOON_DAYS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    return (
        "SELECT oid, name, user_oid, enabled, expires_at FROM ai_api_keys "
        "WHERE enabled = 0 OR (expires_at IS NOT NULL AND expires_at <= "
        f"TO_TIMESTAMP('{deadline}', 'YYYY-MM-DD HH24:MI:SS')) "
        "ORDER BY enabled ASC, expires_at ASC NULLS FIRST"
    )


async def _api_key_anomalies(now: datetime) -> dict:
    """平台 key 的异常清单：已禁用 / 已过期 / 7 天内到期。

    返回里没有 ``token``、``token_hash``、``key_hint`` —— 一条都没有，连掩码
    形式也不给（``key_hint`` 本身就是密钥的 12 个字符）。
    """
    async with session_scope() as session:
        data = await db_content.run_query(
            session, _api_key_anomaly_sql(now), limit=KEY_SCAN_MAX
        )
    rows = [_lower_keys(row) for row in (data.get("rows") or [])]
    deadline = now + timedelta(days=KEY_EXPIRY_SOON_DAYS)
    counts = {"disabled": 0, "expired": 0, "expiring_soon": 0}
    anomalies: list[dict] = []
    for row in rows:
        expires_at = _as_datetime(row.get("expires_at"))
        reasons: list[str] = []
        if not _as_flag(row.get("enabled")):
            reasons.append("disabled")
        if expires_at is not None:
            if expires_at <= now:
                reasons.append("expired")
            elif expires_at <= deadline:
                reasons.append("expiring_soon")
        for reason in reasons:
            counts[reason] += 1
        anomalies.append(
            {
                "oid": str(row.get("oid") or ""),
                "name": row.get("name"),
                "owner_oid": row.get("user_oid"),
                "enabled": _as_flag(row.get("enabled")),
                "expires_at": _iso(expires_at),
                "reasons": reasons,
            }
        )
    return {
        "scanned": len(rows),
        "capped": len(rows) >= KEY_SCAN_MAX,
        "counts": counts,
        "anomalies": anomalies,
        "fields": ["oid", "name", "owner_oid", "enabled", "expires_at", "reasons"],
        "note": "只回元数据：token 明文、token_hash、key_hint 一律不返回",
    }


async def _impl_ai_gateway_status(
    user: dict, user_oid: str | None = None, days: int = GATEWAY_DEFAULT_DAYS
) -> dict:
    window = _clamp(days, GATEWAY_DAYS_MIN, GATEWAY_DAYS_MAX, GATEWAY_DEFAULT_DAYS)
    until = datetime.now()
    since = until - timedelta(days=window)

    stats = await backends.get_ai_call_logs_repo().stats(since=since, until=until)

    quota_view: dict[str, Any] = {"site": await _site_quota_rows(), "user": None}
    if user_oid:
        target = await _load_user(user_oid)
        target_oid = str(target.get("_id") or "")
        snapshot = await quota.admin_snapshot(
            target_oid, roles=list(target.get("roles") or [])
        )
        # V051 起用户侧只有一个 AF 池：不再分 chat / ocr 两列（都读同一行）。
        quota_view["user"] = {
            "user_oid": target_oid,
            "roles": list(target.get("roles") or []),
            "af": snapshot.get("af"),
        }

    by_status = list(stats.get("by_status") or [])
    by_model = sorted(
        stats.get("by_model") or [],
        key=lambda row: int(row.get("calls") or 0),
        reverse=True,
    )[:STATUS_TOP_MODELS]
    top_users = sorted(
        stats.get("by_user") or [],
        key=lambda row: int(row.get("calls") or 0),
        reverse=True,
    )[:STATUS_TOP_USERS]

    return _cap(
        {
            "kind": "ai_gateway_status",
            "window": {"days": window, "since": _iso(since), "until": _iso(until)},
            "quota": quota_view,
            "calls": {
                "total": sum(int(row.get("calls") or 0) for row in by_status),
                "by_status": by_status,
                "by_model": by_model,
                "by_purpose": list(stats.get("by_purpose") or []),
                "daily": list(stats.get("daily") or []),
                "cache": stats.get("cache") or {},
            },
            "api_keys": await _api_key_anomalies(until),
            # 「谁在花钱」只看数字：名字属于 PII，排障时用 oid 再查更准确。
            "top_callers": [
                {
                    "user_oid": str(row.get("user_oid") or ""),
                    "calls": int(row.get("calls") or 0),
                    "errors": int(row.get("errors") or 0),
                    "total_tokens": int(row.get("total_tokens") or 0),
                    "units": int(row.get("units") or 0),
                }
                for row in top_users
            ],
        }
    )


# --------------------------------------------------------------------------- #
# 读工具：prompt 审计
# --------------------------------------------------------------------------- #


async def _impl_ai_prompt_audit_list(
    user: dict,
    oid: str | None = None,
    user_oid: str | None = None,
    purpose: str | None = None,
    days: int = PROMPT_DEFAULT_DAYS,
    page: int = 1,
    page_size: int = PROMPT_PAGE_DEFAULT,
) -> dict:
    repo = backends.get_ai_prompt_audit_repo()

    if oid:
        target = str(oid).strip()
        # oid 形状非法与记录不存在都回 404：两者的处置相同，且不泄露存在性
        # （与 ``GET /api/ai/prompts/{oid}`` 同口径）。
        if not is_valid_oid(target):
            raise AgentError("AI_AGENT_NOT_FOUND", "AI_PROMPT_NOT_FOUND", 404)
        row = await repo.get_by_oid(target)
        if row is None:
            raise AgentError("AI_AGENT_NOT_FOUND", "AI_PROMPT_NOT_FOUND", 404)
        await _audit(
            user,
            "agent_ai_prompt_view",
            f"oid={target} user_oid={row.get('user_oid') or ''}",
            log_type=PROMPT_ACCESS_LOG_TYPE,
            level="info",
        )
        return _cap(
            {
                "kind": "ai_prompt_audit_detail",
                "oid": str(row.get("_id") or target),
                "user_oid": row.get("user_oid"),
                "purpose": row.get("purpose"),
                "roles_snapshot": row.get("roles_snapshot"),
                "created_at": _iso(row.get("created_at")),
                "prompt_text": row.get("prompt_text") or "",
                "note": (
                    "正文读取已写 APP_LOGS 访问审计"
                    f"（type={PROMPT_ACCESS_LOG_TYPE}, level=info）"
                ),
            }
        )

    window = _clamp(days, PROMPT_DAYS_MIN, PROMPT_DAYS_MAX, PROMPT_DEFAULT_DAYS)
    size = _clamp(page_size, 1, PROMPT_PAGE_MAX, PROMPT_PAGE_DEFAULT)
    page_no = max(1, int(page or 1))
    since = datetime.now() - timedelta(days=window)

    filters: dict[str, Any] = {"purpose": purpose or None, "since": since}
    if user_oid:
        if not is_valid_oid(str(user_oid)):
            raise AgentError(
                "AI_AGENT_BAD_ARGUMENT", "user_oid must be a 24-char oid", 400
            )
        filters["user_oid"] = str(user_oid)

    rows = await repo.list_page(page=page_no, page_size=size, **filters)
    total = await repo.count(**filters)

    return _cap(
        {
            "kind": "ai_prompt_audit_list",
            "window": {"days": window, "since": _iso(since)},
            "pagination": {
                "page": page_no,
                "page_size": size,
                "total": int(total or 0),
            },
            "entries": [
                {
                    "oid": str(row.get("_id") or ""),
                    "user_oid": row.get("user_oid"),
                    "purpose": row.get("purpose"),
                    "roles_snapshot": row.get("roles_snapshot"),
                    "prompt_chars": int(row.get("prompt_chars") or 0),
                    "created_at": _iso(row.get("created_at")),
                }
                for row in rows
            ],
            "note": "列表只回元数据，不含正文；要看正文用 oid 参数（会写访问审计）",
        }
    )


# --------------------------------------------------------------------------- #
# 读工具：附件与存储
# --------------------------------------------------------------------------- #


def _bucket(groups: list[dict], field: str) -> list[dict]:
    """把 ``(kind, purpose, status, ocr_status)`` 分组摊成单一维度的小计。"""
    buckets: dict[str, dict] = {}
    for group in groups:
        key = str(group.get(field) or "(none)")
        item = buckets.setdefault(key, {"key": key, "files": 0, "bytes": 0})
        item["files"] += int(group.get("files") or 0)
        item["bytes"] += int(group.get("bytes") or 0)
    return sorted(buckets.values(), key=lambda item: (-item["files"], item["key"]))


async def _impl_attachment_stats(user: dict) -> dict:
    snapshot = await backends.get_files_repo().count_snapshot()
    groups = list(snapshot.get("groups") or [])
    total_files = sum(int(group.get("files") or 0) for group in groups)
    total_bytes = sum(int(group.get("bytes") or 0) for group in groups)
    orphans = dict(snapshot.get("orphans") or {})
    return _cap(
        {
            "kind": "attachment_stats",
            "totals": {"files": total_files, "bytes": total_bytes},
            "by_kind": _bucket(groups, "kind"),
            "by_purpose": _bucket(groups, "purpose"),
            "by_status": _bucket(groups, "status"),
            "by_ocr_status": _bucket(groups, "ocr_status"),
            "orphans": {
                "thumb_without_parent": int(
                    orphans.get("thumb_without_parent") or 0
                ),
                "original_without_thumb": int(orphans.get("original_without_thumb") or 0),
                "note": (
                    "thumb_without_parent 是父行已硬删后残留的不可达字节；"
                    "original_without_thumb 只是提示，非图片类附件本来就没有缩略图"
                ),
            },
            "note": "计数来自单条 GROUP BY 聚合，字节数取 SIZE_BYTES，从不读 CONTENT",
        }
    )


async def _impl_attachment_gc_preview(
    user: dict, retention_days: int = ATTACHMENT_GC_RETENTION_DEFAULT
) -> dict:
    days = _clamp(
        retention_days, 1, ATTACHMENT_GC_RETENTION_MAX, ATTACHMENT_GC_RETENTION_DEFAULT
    )
    cutoff = datetime.now() - timedelta(days=days)
    snapshot = await backends.get_files_repo().count_snapshot(gc_cutoff=cutoff)
    candidates = int((snapshot.get("gc") or {}).get("candidates") or 0)
    batch_size = ocr_upload_gc.batch_size()
    return _cap(
        {
            "kind": "attachment_gc_preview",
            "retention_days": days,
            "cutoff": _iso(cutoff),
            "candidates": candidates,
            "batch_size": batch_size,
            "est_batches": (candidates + batch_size - 1) // batch_size,
            "scope": (
                "purpose='volunteer_order' AND entity_type IS NULL AND "
                "kind='original' AND status NOT IN ('verified','rejected') AND "
                "created_at < cutoff AND 无非 thumb 子行"
            ),
            "note": (
                "只读预览：与 purge_ocr_uploads 共用同一份谓词"
                "（_ocr_upload_candidate_predicates），预览与实删不会算出不同的集合"
            ),
        }
    )


# --------------------------------------------------------------------------- #
# 读工具：活动与义工核查
# --------------------------------------------------------------------------- #


async def _impl_volunteer_audit(
    user: dict, days: int = VOLUNTEER_DAYS_DEFAULT
) -> dict:
    window = _clamp(days, 1, VOLUNTEER_DAYS_MAX, VOLUNTEER_DAYS_DEFAULT)
    since = datetime.now() - timedelta(days=window)

    activities, total = await backends.get_activities_repo().list_raw(
        search="", atypes=None, status=None, sort="date", asc=False, page=1,
        size=VOLUNTEER_SCAN_MAX,
    )
    scanned = [
        activity
        for activity in activities
        if (_as_datetime(activity.get("date")) or since) >= since
    ]

    zero_member: list[dict] = []
    zero_duration: list[dict] = []
    gaps: list[dict] = []
    counts = {"zero_member_activities": 0, "duration_gaps": 0, "zero_duration_records": 0}

    for activity in scanned:
        activity_oid = str(activity.get("_id") or "")
        members = await backends.get_members_repo().list_by_activity(activity_oid)
        identity = {
            "activity_oid": activity_oid,
            "activity_name": activity.get("name"),
            "activity_date": _iso(activity.get("date")),
            "activity_status": activity.get("status"),
        }
        if not members:
            counts["zero_member_activities"] += 1
            if len(zero_member) < VOLUNTEER_ITEMS_PER_BUCKET:
                zero_member.append(dict(identity))
            continue

        # 「标准时长」没有独立列：同一活动同一 mode 内出现的最大时长就是满额，
        # 比它低的记录即缺口（基准随结果一起返回，避免读者自行猜测口径）。
        standard: dict[str, float] = {}
        for member in members:
            if member.get("status") != "effective":
                continue
            mode = str(member.get("mode") or "")
            duration = float(member.get("duration") or 0)
            if duration > standard.get(mode, 0.0):
                standard[mode] = duration

        for member in members:
            if member.get("status") != "effective":
                continue
            mode = str(member.get("mode") or "")
            duration = float(member.get("duration") or 0)
            record = {
                **identity,
                "member_oid": str(member.get("member") or member.get("_id") or ""),
                "member_record_oid": str(member.get("_id") or ""),
                "mode": mode,
                "duration": duration,
            }
            if duration == 0:
                counts["zero_duration_records"] += 1
                if len(zero_duration) < VOLUNTEER_ITEMS_PER_BUCKET:
                    zero_duration.append(record)
                continue
            baseline = standard.get(mode, 0.0)
            if baseline > 0 and duration < baseline:
                counts["duration_gaps"] += 1
                if len(gaps) < VOLUNTEER_ITEMS_PER_BUCKET:
                    gaps.append(
                        {
                            **record,
                            "standard_hours": baseline,
                            "missing_hours": round(baseline - duration, 2),
                        }
                    )

    listed = len(zero_member) + len(zero_duration) + len(gaps)
    truncated = (
        counts["zero_member_activities"] > len(zero_member)
        or counts["duration_gaps"] > len(gaps)
        or counts["zero_duration_records"] > len(zero_duration)
    )
    return _cap(
        {
            "kind": "volunteer_audit",
            "window": {"days": window, "since": _iso(since)},
            "activities_total": int(total or 0),
            "activities_scanned": len(scanned),
            "counts": counts,
            "zero_member_activities": zero_member,
            "duration_gaps": gaps,
            "zero_duration_records": zero_duration,
            "listed": listed,
            "truncated": truncated,
            "standard_basis": "max_duration_within_activity_mode",
            "note": (
                "ACTIVITIES 没有标准时长列，缺口基准取同一活动同一 mode 的满额时长；"
                "activities_scanned 只覆盖最近 "
                f"{VOLUNTEER_SCAN_MAX} 个活动（每活动一条成员查询）"
            ),
        }
    )


# --------------------------------------------------------------------------- #
# 写工具：AI 治理
# --------------------------------------------------------------------------- #


async def _prepare_ai_api_key_revoke(user: dict, key_oid: str, reason: str) -> Any:
    row = await _load_api_key(key_oid)
    enabled = _as_flag(row.get("enabled"))
    expires_at = _iso(row.get("expires_at"))
    state = "启用中" if enabled else "已禁用"
    return _draft(
        f"吊销 API key「{row.get('name')}」（{state}，到期 {expires_at or '永不过期'}）",
        {
            "kind": "api_key_revoke",
            "key_oid": str(row.get("_id") or key_oid),
            "name": row.get("name"),
            "owner_oid": row.get("user_oid"),
            "expires_at": expires_at,
            "last_used_at": _iso(row.get("last_used_at")),
            "reason": reason,
            "from_enabled": enabled,
            "to_enabled": False,
            "effect": "enabled=0 软吊销（行保留供审计）+ 清空校验缓存",
        },
    )


@_translate_quota_errors
async def _impl_ai_api_key_revoke(user: dict, key_oid: str, reason: str) -> dict:
    row = await _load_api_key(key_oid)
    before = _as_flag(row.get("enabled"))
    # 复用服务层：它做 owner/admin 判定、唯一键翻译与缓存失效，工具层不重写。
    view = await api_keys.update(key_oid, user, {"enabled": False})
    await api_keys.invalidate(key_oid)
    await _audit(
        user,
        "agent_ai_api_key_revoke",
        f"oid={key_oid} name={view.get('name')} owner={row.get('user_oid')} "
        f"reason={reason}",
    )
    return _cap(
        {
            "kind": "api_key_revoke",
            "oid": key_oid,
            "name": view.get("name"),
            "owner_oid": row.get("user_oid"),
            "enabled": view.get("enabled"),
            "revoked": before,
            "already_disabled": not before,
            "reason": reason,
            "note": "已软吊销并清空校验缓存；行保留，需要硬删请走 /keys 管理端点",
        }
    )


def _check_quota_kind(kind: Any) -> str:
    target = str(kind or "").strip()
    if target not in QUOTA_KINDS:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            f"kind must be one of: {', '.join(QUOTA_KINDS)}",
            400,
        )
    return target


async def _prepare_ai_quota_set(
    user: dict, user_oid: str, kind: str, limit_count: int
) -> Any:
    target_kind = _check_quota_kind(kind)
    limit = _clamp(limit_count, 0, QUOTA_LIMIT_MAX, -1)
    if limit < 0:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            f"limit_count must be 0..{QUOTA_LIMIT_MAX}",
            400,
        )
    target = await _load_user(user_oid)
    target_oid = str(target.get("_id") or "")
    snapshot = await quota.admin_snapshot(
        target_oid, roles=list(target.get("roles") or [])
    )
    current = snapshot.get(target_kind) or {}
    current_limit = int(current.get("limit") or 0)
    used = int(current.get("used") or 0)
    return _draft(
        f"把用户 {target.get('name') or target_oid} 的 {target_kind} 月额度 "
        f"{current_limit} → {limit}（本月已用 {used}）",
        {
            "kind": "ai_quota_set",
            "user_oid": target_oid,
            "user_name": target.get("name"),
            "kind_name": target_kind,
            "period": current.get("period"),
            "from": {"limit": current_limit, "used": used, "source": current.get("source")},
            "to": {"limit": limit},
            "effect": "只改上限，不清已用量（重置用量是另一个动作）",
        },
    )


@_translate_quota_errors
async def _impl_ai_quota_set(
    user: dict, user_oid: str, kind: str, limit_count: int
) -> dict:
    target_kind = _check_quota_kind(kind)
    limit = _clamp(limit_count, 0, QUOTA_LIMIT_MAX, -1)
    if limit < 0:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT",
            f"limit_count must be 0..{QUOTA_LIMIT_MAX}",
            400,
        )
    target = await _load_user(user_oid)
    target_oid = str(target.get("_id") or "")
    result = await quota.set_user_limit(target_oid, target_kind, limit)
    await _audit(
        user,
        "agent_ai_quota_set",
        f"user={target_oid} kind={target_kind} limit={result.get('limit')} "
        f"used={result.get('used')}",
    )
    return _cap(
        {
            "kind": "ai_quota_set",
            "user_oid": target_oid,
            "kind_name": target_kind,
            "period": result.get("period"),
            "used": result.get("used"),
            "limit": result.get("limit"),
            "requested_limit": limit,
        }
    )


# --------------------------------------------------------------------------- #
# 写工具：附件清理
# --------------------------------------------------------------------------- #


async def _prepare_attachment_gc_run(
    user: dict,
    retention_days: int = ATTACHMENT_GC_RETENTION_DEFAULT,
    max_batches: int = ATTACHMENT_GC_BATCH_DEFAULT,
) -> Any:
    days = _clamp(
        retention_days, 1, ATTACHMENT_GC_RETENTION_MAX, ATTACHMENT_GC_RETENTION_DEFAULT
    )
    batches = _clamp(max_batches, 1, ATTACHMENT_GC_MAX_BATCHES, ATTACHMENT_GC_BATCH_DEFAULT)
    cutoff = datetime.now() - timedelta(days=days)
    snapshot = await backends.get_files_repo().count_snapshot(gc_cutoff=cutoff)
    candidates = int((snapshot.get("gc") or {}).get("candidates") or 0)
    batch_size = ocr_upload_gc.batch_size()
    return _draft(
        f"清理 {days} 天前的 OCR 上传图：候选 {candidates} 张，最多 {batches} 批"
        f"（每批 {batch_size}）",
        {
            "kind": "attachment_gc_run",
            "retention_days": days,
            "cutoff": _iso(cutoff),
            "candidates": candidates,
            "batch_size": batch_size,
            "max_batches": batches,
            "upper_bound": min(candidates, batches * batch_size),
            "effect": (
                "硬删 FILES 原图 + 其 thumb + FILE_OCR + FILE_EVENTS；"
                "已裁决（verified/rejected）与绑定活动的图不在范围内，删除不可逆"
            ),
        },
    )


async def _impl_attachment_gc_run(
    user: dict,
    retention_days: int = ATTACHMENT_GC_RETENTION_DEFAULT,
    max_batches: int = ATTACHMENT_GC_BATCH_DEFAULT,
) -> dict:
    days = _clamp(
        retention_days, 1, ATTACHMENT_GC_RETENTION_MAX, ATTACHMENT_GC_RETENTION_DEFAULT
    )
    batches = _clamp(max_batches, 1, ATTACHMENT_GC_MAX_BATCHES, ATTACHMENT_GC_BATCH_DEFAULT)
    cutoff = datetime.now() - timedelta(days=days)
    batch_size = ocr_upload_gc.batch_size()

    repo = backends.get_files_repo()
    totals = {"originals": 0, "thumbs": 0, "ocr_rows": 0, "events": 0}
    executed = 0
    try:
        for _ in range(batches):
            result = await repo.purge_ocr_uploads(cutoff, batch_size)
            executed += 1
            for key in totals:
                totals[key] += int(result.get(key) or 0)
            # 短批即停：这一批没凑满，说明过期行已经清完。
            if int(result.get("originals") or 0) < batch_size:
                break
    finally:
        # 失败也要审计：已经删掉的行是既成事实，「删到一半崩了」更需要留痕。
        await _audit(
            user,
            "agent_attachment_gc_run",
            f"cutoff={_iso(cutoff)} retention={days}d batches={executed} "
            f"originals={totals['originals']} thumbs={totals['thumbs']} "
            f"ocr_rows={totals['ocr_rows']} events={totals['events']}",
        )
    return _cap(
        {
            "kind": "attachment_gc_run",
            "retention_days": days,
            "cutoff": _iso(cutoff),
            "batches": executed,
            "deleted": totals,
        }
    )


# --------------------------------------------------------------------------- #
# 写工具：通知公告
# --------------------------------------------------------------------------- #


def _clean_text(value: str, limit: int, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise AgentError("AI_AGENT_BAD_ARGUMENT", f"{name} must not be empty", 400)
    if len(text) > limit:
        raise AgentError(
            "AI_AGENT_BAD_ARGUMENT", f"{name} must be at most {limit} chars", 400
        )
    return text


def _recipient(row: dict) -> dict:
    return {
        "oid": str(row.get("_id") or ""),
        "name": row.get("name"),
        "business_id": row.get("id"),
    }


async def _resolve_target(target: Any) -> tuple[list[dict], str]:
    """``target`` → (收件人, 受众标签)。

    收件人是**只读解析**结果：``prepare`` 与 ``impl`` 各调一次，中间目标集合变了
    也不会发出与提案不符的受众（公告板上无法撤回一条发错人的公告）。
    """
    payload = target if isinstance(target, dict) else None
    if not payload:
        raise AgentError("AI_AGENT_BAD_ARGUMENT", "target must be a json object", 400)

    kind = str(payload.get("kind") or "").strip().lower()
    if kind == "all":
        raise AgentError(
            "AI_AGENT_FORBIDDEN",
            "全站广播不在本工具能力内：请用 user 或 role 指定受众",
            403,
        )
    value = str(payload.get("value") or "").strip()

    if kind == "user":
        if not value:
            raise AgentError(
                "AI_AGENT_BAD_ARGUMENT", "target.value must be a user oid or 学号", 400
            )
        row = await _load_user(value)
        label = f"定向通知：{row.get('name') or value}"
        return [_recipient(row)], label

    if kind == "role":
        if not value:
            raise AgentError(
                "AI_AGENT_BAD_ARGUMENT", "target.value must be a role name", 400
            )
        rows = await backends.get_users_repo().list_all()
        picked = [row for row in rows if value in (row.get("roles") or [])]
        if not picked:
            raise AgentError(
                "AI_AGENT_NOT_FOUND", f"no user has role {value}", 404
            )
        if len(picked) > NOTIFY_RECIPIENTS_MAX:
            raise AgentError(
                "AI_AGENT_BAD_ARGUMENT",
                f"role {value} has {len(picked)} users, over the "
                f"{NOTIFY_RECIPIENTS_MAX} recipient cap",
                400,
            )
        return [_recipient(row) for row in picked], f"角色 {value}"

    raise AgentError(
        "AI_AGENT_BAD_ARGUMENT", "target.kind must be user or role", 400
    )


def _audience_title(title: str, label: str) -> str:
    """受众写在标题前缀上：公告板没有收件人列，标题是唯一的受众线索。

    前缀本身也要截断：用户名字段是 ``VARCHAR2(128)``，一个超长名字加上前缀
    就会把整条标题顶到列宽之外（Oracle 直接报错，公告发不出去）。受众的完整
    信息在审计里，标题只需要认得出来。
    """
    prefix = f"【{_mask_contacts(label)[:AUDIENCE_LABEL_MAX]}】"
    room = NOTIFY_TITLE_MAX - len(prefix)
    if len(title) > room:
        title = title[: max(0, room - 1)] + "…"
    return f"{prefix}{title}"


async def _prepare_notify_send(
    user: dict, title: str, content: str, target: dict
) -> Any:
    clean_title = _clean_text(title, NOTIFY_TITLE_MAX, "title")
    clean_content = _clean_text(content, NOTIFY_CONTENT_MAX, "content")
    recipients, label = await _resolve_target(target)
    return _draft(
        f"发布公告「{clean_title}」（{label}，覆盖 {len(recipients)} 人）",
        {
            "kind": "notification",
            "title": clean_title,
            "final_title": _audience_title(clean_title, label),
            "content": clean_content[:500],
            "content_truncated": len(clean_content) > 500,
            "content_chars": len(clean_content),
            "audience": label,
            "recipients": len(recipients),
            "recipients_sample": recipients[:NOTIFY_SAMPLE_MAX],
            "delivery": "global_board",
            "effect": (
                "NOTIFICATIONS 是全局公告板：落一条公告（全体可见），标题标注受众；"
                "不按人复制 N 条"
            ),
        },
    )


async def _impl_notify_send(
    user: dict, title: str, content: str, target: dict
) -> dict:
    clean_title = _clean_text(title, NOTIFY_TITLE_MAX, "title")
    clean_content = _clean_text(content, NOTIFY_CONTENT_MAX, "content")
    recipients, label = await _resolve_target(target)

    author_oid = str(user.get("id") or "")
    author_name = await get_user_display_name(author_oid)
    entry, version = await backends.get_notifications_repo().create(
        title=_audience_title(clean_title, label),
        content=clean_content,
        author_oid=author_oid,
        author_name=author_name,
    )
    await _audit(
        user,
        "agent_notify_send",
        f"oid={entry.get('_id')} audience={label} recipients={len(recipients)} "
        f"title={clean_title}",
    )
    return _cap(
        {
            "kind": "notification",
            "oid": str(entry.get("_id") or ""),
            "version": version,
            "title": entry.get("title"),
            "audience": label,
            "recipients": len(recipients),
            "delivery": "global_board",
            "note": "公告板全站可见，受众只体现在标题前缀与审计记录里",
        }
    )


# --------------------------------------------------------------------------- #
# 注册（9 个：5 读 + 4 写）
# --------------------------------------------------------------------------- #

_register(
    ToolDef(
        name="ai_gateway_status",
        description=(
            "AI 网关治理面板：站点与指定用户的额度快照、按模型/状态的调用统计、"
            "平台 key 的异常清单（已禁用/已过期/7 天内到期，只回元数据）、"
            "调用量 Top 5 用户。只读。"
        ),
        kind=READ,
        params=_params(
            {
                "user_oid": {**_STR, "description": "可选：要查看额度的用户 oid 或学号"},
                "days": {
                    **_INT,
                    "minimum": GATEWAY_DAYS_MIN,
                    "maximum": GATEWAY_DAYS_MAX,
                    "description": f"统计窗口天数（1-{GATEWAY_DAYS_MAX}，默认 7）",
                },
            },
            (),
        ),
        permission=_admin_only,
        impl=_impl_ai_gateway_status,
        guard=ops_auth.ensure_system_admin,
    )
)

_register(
    ToolDef(
        name="ai_prompt_audit_list",
        description=(
            "prompt 正文留存审计：按时间窗/用户/场景分页列出元数据（不含正文）；"
            "给 oid 时返回该条正文（会写一条 APP_LOGS 访问审计）。只读。"
        ),
        kind=READ,
        params=_params(
            {
                "oid": {**_STR, "description": "可选：取此条正文（24 位 oid）"},
                "user_oid": {**_STR, "description": "可选：按用户 oid 过滤"},
                "purpose": {**_STR, "description": "可选：调用场景（chat/agent/...）"},
                "days": {
                    **_INT,
                    "minimum": PROMPT_DAYS_MIN,
                    "maximum": PROMPT_DAYS_MAX,
                    "description": f"时间窗天数（1-{PROMPT_DAYS_MAX}，默认 7）",
                },
                "page": {**_INT, "minimum": 1, "description": "页码（从 1 起）"},
                "page_size": {
                    **_INT,
                    "minimum": 1,
                    "maximum": PROMPT_PAGE_MAX,
                    "description": f"每页条数（1-{PROMPT_PAGE_MAX}，默认 20）",
                },
            },
            (),
        ),
        permission=_admin_only,
        impl=_impl_ai_prompt_audit_list,
        guard=ops_auth.ensure_system_admin,
    )
)

_register(
    ToolDef(
        name="attachment_stats",
        description=(
            "附件与存储概览：按 kind/purpose/status/ocr_status 的计数与总字节，"
            "以及孤儿检测（thumb 的父行不存在、original 缺 thumb）。只读。"
        ),
        kind=READ,
        params=_params({}, ()),
        permission=_admin_only,
        impl=_impl_attachment_stats,
        guard=ops_auth.ensure_system_admin,
    )
)

_register(
    ToolDef(
        name="attachment_gc_preview",
        description=(
            "只读预览 OCR 上传图清理任务会删多少条（默认 3 天保留期），"
            "含口径说明与预计批数。只读。"
        ),
        kind=READ,
        params=_params(
            {
                "retention_days": {
                    **_INT,
                    "minimum": 1,
                    "maximum": ATTACHMENT_GC_RETENTION_MAX,
                    "description": (
                        f"保留天数（1-{ATTACHMENT_GC_RETENTION_MAX}，默认 3）"
                    ),
                }
            },
            (),
        ),
        permission=_admin_only,
        impl=_impl_attachment_gc_preview,
        guard=ops_auth.ensure_system_admin,
    )
)

_register(
    ToolDef(
        name="volunteer_audit",
        description=(
            "活动与义工记录核查：窗口内零报名的活动、时长为 0 的记录、"
            "低于同活动同 mode 满额时长的缺口（各最多 10 条 + 总数）。只读。"
        ),
        kind=READ,
        params=_params(
            {
                "days": {
                    **_INT,
                    "minimum": 1,
                    "maximum": VOLUNTEER_DAYS_MAX,
                    "description": f"时间窗天数（1-{VOLUNTEER_DAYS_MAX}，默认 30）",
                }
            },
            (),
        ),
        permission=_admin_only,
        impl=_impl_volunteer_audit,
        guard=ops_auth.ensure_system_admin,
    )
)

_register(
    ToolDef(
        name="ai_api_key_revoke",
        description=(
            "软吊销一把平台 API key（enabled=0 + 清空校验缓存，行保留供审计）。"
            "写操作：先生成提案，另一位管理员确认后执行。"
        ),
        kind=WRITE,
        params=_params(
            {
                "key_oid": {**_STR, "description": "API key 的 oid"},
                "reason": {
                    **_STR,
                    "maxLength": 200,
                    "description": "吊销原因（进审计）",
                },
            },
            ("key_oid", "reason"),
        ),
        permission=_admin_only,
        impl=_impl_ai_api_key_revoke,
        prepare=_prepare_ai_api_key_revoke,
        guard=ops_auth.ensure_system_admin,
        dual_auth_action=WRITE_AUTH_ACTION,
    )
)

_register(
    ToolDef(
        name="ai_quota_set",
        description=(
            "设置某用户统一 AF 池（af）的月度额度上限，不清已用量。"
            "写操作：先生成提案，另一位管理员确认后执行。"
        ),
        kind=WRITE,
        params=_params(
            {
                "user_oid": {**_STR, "description": "用户 oid 或学号"},
                "kind": {
                    **_STR,
                    "enum": list(QUOTA_KINDS),
                    "description": "能力（af：OCR 与对话共用的 AF 池）",
                },
                "limit_count": {
                    **_INT,
                    "minimum": 0,
                    "maximum": QUOTA_LIMIT_MAX,
                    "description": f"新的月上限（0-{QUOTA_LIMIT_MAX}）",
                },
            },
            ("user_oid", "kind", "limit_count"),
        ),
        permission=_admin_only,
        impl=_impl_ai_quota_set,
        prepare=_prepare_ai_quota_set,
        guard=ops_auth.ensure_system_admin,
        dual_auth_action=WRITE_AUTH_ACTION,
    )
)

_register(
    ToolDef(
        name="attachment_gc_run",
        description=(
            "执行 OCR 上传图清理（硬删过期原图及其缩略图/OCR/事件行，"
            "按批提交、批数封顶）。写操作：先生成提案，另一位管理员确认后执行。"
        ),
        kind=WRITE,
        params=_params(
            {
                "retention_days": {
                    **_INT,
                    "minimum": 1,
                    "maximum": ATTACHMENT_GC_RETENTION_MAX,
                    "description": (
                        f"保留天数（1-{ATTACHMENT_GC_RETENTION_MAX}，默认 3）"
                    ),
                },
                "max_batches": {
                    **_INT,
                    "minimum": 1,
                    "maximum": ATTACHMENT_GC_MAX_BATCHES,
                    "description": (
                        f"最多跑几批（1-{ATTACHMENT_GC_MAX_BATCHES}，默认 3）"
                    ),
                },
            },
            (),
        ),
        permission=_admin_only,
        impl=_impl_attachment_gc_run,
        prepare=_prepare_attachment_gc_run,
        guard=ops_auth.ensure_system_admin,
        dual_auth_action=WRITE_AUTH_ACTION,
    )
)

_register(
    ToolDef(
        name="notify_send",
        description=(
            "发布站内公告（全局公告板，标题标注受众）。target 限定 user 或 role，"
            f"目标最多 {NOTIFY_RECIPIENTS_MAX} 人，all 一律拒绝。"
            "写操作：先生成提案，另一位管理员确认后执行。"
        ),
        kind=WRITE,
        params=_params(
            {
                "title": {
                    **_STR,
                    "maxLength": NOTIFY_TITLE_MAX,
                    "description": f"标题（≤{NOTIFY_TITLE_MAX} 字）",
                },
                "content": {
                    **_STR,
                    "maxLength": NOTIFY_CONTENT_MAX,
                    "description": f"正文（≤{NOTIFY_CONTENT_MAX} 字）",
                },
                "target": {
                    **_OBJ,
                    "description": (
                        '{"kind":"user"|"role","value":"<用户 oid/学号 | 角色名>"}'
                    ),
                    "properties": {
                        "kind": {**_STR, "enum": ["user", "role", "all"]},
                        "value": _STR,
                    },
                    "required": ["kind"],
                    "additionalProperties": False,
                },
            },
            ("title", "content", "target"),
        ),
        permission=_admin_only,
        impl=_impl_notify_send,
        prepare=_prepare_notify_send,
        guard=ops_auth.ensure_system_admin,
        dual_auth_action=WRITE_AUTH_ACTION,
    )
)


__all__ = [
    "ATTACHMENT_GC_MAX_BATCHES",
    "ATTACHMENT_GC_RETENTION_MAX",
    "GATEWAY_DAYS_MAX",
    "NOTIFY_RECIPIENTS_MAX",
    "PROMPT_PAGE_MAX",
    "QUOTA_LIMIT_MAX",
    "VOLUNTEER_SCAN_MAX",
]
