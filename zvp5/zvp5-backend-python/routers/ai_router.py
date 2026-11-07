"""AI 网关 HTTP 层（``/api/ai``）—— 路由聚合器。

历史上这里是 2844 行的单文件；现在按端点域拆到 :mod:`routers.ai` 包。本模块
只做两件事：

1. 按**原装饰器定义顺序** ``include_router`` 子路由。顺序是契约的一部分：
   ``GET /quota/usage`` 必须排在 ``GET /quota/{user_oid}`` 之前，否则静态段会
   被动态段匹配掉（oid 校验回 400），与 ``main`` 里 quota-requests 先于本模块
   注册是同一类约束。
2. re-export 旧的外部引用符号（``_require_admin`` / ``_error_type`` /
   ``_has_ai_access`` / ``AI_FORBIDDEN_MESSAGE`` / ``ai_scene`` / 请求模型等），
   避免尚未迁移的导入点立刻炸；新代码请直接从 :mod:`routers.ai.common` 或对应
   的端点域模块导入。

两套协议故意并存，不统一错误体：

* ``/api/ai/ocr``、``/api/ai/health``、``/api/ai/keys`` 走仓库约定
  ``{"status","code","data"}`` + ``HTTPException``；
* ``/api/ai/v1/*`` 是 OpenAI 兼容面，错误体必须是 ``{"error":{"message","type","code"}}``，
  因为客户端是 OpenAI SDK，它不认识 zvp 的 ``detail``，读到别的形状只会抛解析错误。

流式请求**不做 provider 切换**：字节已经吐给客户端了，重放等于把两份答案拼在一起。
"""

from __future__ import annotations

from fastapi import APIRouter

from db import backends, state  # noqa: F401  (re-export)
from routers.ai import (  # noqa: F401  (re-export 子模块，便于定位)
    chat,
    common,
    completion,
    health,
    keys,
    logs,
    ocr,
    ocr_engines,
    streaming,
)
from routers.ai import quota as quota_routes
from routers.ai.chat import (  # noqa: F401  (re-export)
    BODY_TOO_LARGE_CODE,
    INVALID_BODY_CODE,
    MESSAGE_TOO_LONG_CODE,
    TOO_MANY_MESSAGES_CODE,
    _content_chars,
    _last_user_text,
    _payload_limit_error,
    _read_chat_payload,
    _record_violation,
    _screen_chat_request,
    _too_large_error,
    chat_completions,
    list_models,
)
from routers.ai.common import (  # noqa: F401  (re-export)
    AI_FORBIDDEN_MESSAGE,
    BAN_CHECK_EVERY_CHUNKS,
    BURST_TRIGGER,
    MAX_PROVIDER_ATTEMPTS,
    PERSONAL_SCOPE,
    REJECTED_CODES,
    RETRYABLE_CODES,
    SCENE_HEADER,
    SCENE_MAX_LEN,
    SCENE_OCR,
    STREAM_CAPTURE_MAX_BYTES,
    STREAM_DONE_FRAME,
    STREAM_ERROR_FALLBACK,
    STREAM_ERROR_MESSAGES,
    STREAM_UNSUPPORTED_BODY_MAX,
    STREAM_UNSUPPORTED_STATUSES,
    STREAM_UNSUPPORTED_TTL_SECONDS,
    USER_BANNED_CODE,
    USER_BANNED_MESSAGE,
    VOLUNTEER_PROVIDER_ROLES,
    _audit_status,
    _billable,
    _charge,
    _credential_error,
    _elapsed_ms,
    _error_type,
    _evaluate_burst,
    _feature_error,
    _has_ai_access,
    _is_admin,
    _mark_stream_unsupported,
    _openai_error,
    _quota_error,
    _rate_limited,
    _reject,
    _reject_http,
    _require_admin,
    _require_ai_access,
    _scene_from,
    _sse_data_frame,
    _stream_clock,
    _stream_error_frame,
    _stream_unsupported,
    _stream_unsupported_cached,
    _teardown_step,
    _usage_fields,
    ai_scene,
    get_ai_client,
    reset_stream_unsupported_cache,
)
from routers.ai.completion import (  # noqa: F401  (re-export)
    _answer_text,
    _blocking_completion,
    _stream_answer_text,
)
from routers.ai.health import _warmup_local_engines, ai_health  # noqa: F401
from routers.ai.keys import (  # noqa: F401  (re-export 请求模型与端点)
    KeyCreateRequest,
    KeyDeleteRequest,
    KeyUpdateRequest,
    create_key,
    delete_key,
    list_keys,
    update_key,
)
from routers.ai.logs import (  # noqa: F401  (re-export)
    LOG_CAPABILITIES,
    LOG_KEY_SCOPES,
    LOG_STATUSES,
    PROMPT_LIST_DAYS,
    PROMPT_MAX_DAYS,
    STATS_MAX_DAYS,
    _bad_filter,
    _epoch_to_local,
    call_log_stats,
    get_prompt_audit,
    list_call_logs,
    list_prompt_audit,
)
from routers.ai.ocr import (  # noqa: F401  (re-export)
    OCR_FILE_MIME_TYPES,
    FileOcrRequest,
    OcrRequest,
    VolunteerOcrRequest,
    _enqueue_ocr,
    _read_ocr_image,
    request_file_ocr,
    request_ocr,
    request_volunteer_ocr,
)
from routers.ai.ocr_engines import (  # noqa: F401  (re-export)
    OcrEngineCreateRequest,
    OcrEngineUpdateRequest,
    create_ocr_engine,
    delete_ocr_engine,
    list_ocr_engines,
    update_ocr_engine,
)
from routers.ai.quota import (  # noqa: F401  (re-export)
    QuotaResetRequest,
    QuotaSetRequest,
    _PERIOD_RE,
    _USAGE_KEYS,
    _quota_period_bounds,
    _target_roles,
    my_quota,
    my_quota_usage,
    reset_user_quota,
    set_user_quota,
    user_quota,
)
from routers.ai.streaming import (  # noqa: F401  (re-export)
    _client_stream_response,
    _looks_like_sse,
    _sse_stream_response,
    _stream_completion,
    _stream_failure_code,
)
from services import file_ops, risk_monitor, violation_guard  # noqa: F401
from services.ai import (  # noqa: F401  (re-export 服务模块，测试按模块打桩)
    api_keys,
    audit,
    ban_state,
    credentials,
    prompt_audit,
    providers,
    quota,
    rate_limit,
    routing,
    safety_guard,
)
from services.ai import config as ai_config  # noqa: F401
from services.ai import ocr as ai_ocr  # noqa: F401
from services.ai import usage as ai_usage  # noqa: F401
from services.ai.credentials import CredentialError  # noqa: F401
from services.ai.routing import RouteError  # noqa: F401
from typings.log import inject_log, xuehai_id_from  # noqa: F401
from util.attachment_ocr import process_ocr  # noqa: F401
from util.object_id import (  # noqa: F401  (re-export)
    _authenticate,
    _log_level_scope,
    get_current_user,
    oauth2_scheme,
    require_password_verification,
    validate_object_id,
)
from util.permission.features import ensure_feature  # noqa: F401
from util.read_throttle import read_budget  # noqa: F401
from util.user import get_user_display_name, get_user_display_names  # noqa: F401

router = APIRouter()


# 顺序 = 原 ``ai_router.py`` 里装饰器的定义顺序，不得调整（见模块 docstring）。
router.include_router(ocr.router)
router.include_router(keys.router)
router.include_router(logs.router)
router.include_router(ocr_engines.router)
router.include_router(quota_routes.router)
router.include_router(health.router)
router.include_router(chat.router)
