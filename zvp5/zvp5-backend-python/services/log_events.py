"""APP_LOGS 的 ``ltype`` 常量表。

历史原因，事件名一直是散落在调用点的自由字符串（列是 ``VARCHAR2(64)``，无枚举
约束）。这带来两个后果：拼错一个字母就多出一个「从未见过的类型」，而前端
``ZLogCard`` 的 label map 只能靠猜。本模块把**已经在写**的事件名收成唯一来源，
新代码引用常量而不是字面量；旧调用点按需替换，不做一次性大改。

只放常量，不 import 任何东西：``typings/log.py``、routers、services 都能安全引用，
不引入循环依赖。
"""

ACTIVITY_CREATE = "activity_create"
ACTIVITY_UPDATE = "activity_update"
ACTIVITY_DELETE = "activity_delete"
ACTIVITY_MEMBER_ADD = "activity_member_add"
ACTIVITY_MEMBER_UPDATE = "activity_member_update"
ACTIVITY_MEMBER_REMOVE = "activity_member_remove"
ACTIVITY_MEMBER_REVIEW = "activity_member_review"

USER_LOGIN = "user_login"
USER_VERIFY = "user_verify"
USER_LOGOUT = "user_logout"
LOGIN_FAIL = "login_fail"
PASSWD_CHANGE = "passwd_change"
TOTP_VIEW = "totp_view"
STUDENT_ID_CHANGE = "student_id_change"
PAST_CHANGE = "past_change"
AUTH_MAINTENANCE_BLOCKED = "auth_maintenance_blocked"

USER_CREATE = "user_create"
USER_HARD_DELETE = "user_hard_delete"
ROLE_CHANGE = "role_change"

GROUP_CREATE = "group_create"
GROUP_UPDATE = "group_update"
GROUP_DELETE = "group_delete"
GROUP_MEMBER_ADD = "group_member_add"
GROUP_MEMBER_REMOVE = "group_member_remove"

BAN_CHANGE = "ban_change"
BAN_RULE = "ban_rule"
BAN_RULE_SKIPPED = "ban_rule_skipped"
DEVICE_BIND = "device_bind"
DEVICE_UNBIND = "device_unbind"
DEVICE_BAN = "device_ban"
DEVICE_BAN_SKIPPED = "device_ban_skipped"
DEVICE_MISMATCH = "device_mismatch"
DEVICE_REQUIRED = "device_required"
BINDING_MISMATCH = "binding_mismatch"
TEMP_PASSWORD_CREATE = "temp_password_create"
TEMP_PASSWORD_LOGIN = "temp_password_login"
TEMP_PASSWORD_FAIL = "temp_password_fail"

IMPORT_EXCEL = "import_excel"
EXPORT = "export"
ATTACHMENT = "attachment"
#: 历史遗留：附件字节读取以前写的是粗粒度 ``attachment``，库里已有历史行用它，
#: 保留常量只为对照旧数据；新写入一律用下面更细的类型。
ATTACHMENT_VIEW = "attachment_view"
ATTACHMENT_AUDIT = "attachment_audit"
ATTACHMENT_UPLOAD = "attachment_upload"
ATTACHMENT_STATUS = "attachment_status"
ATTACHMENT_LINK = "attachment_link"
ATTACHMENT_VERIFY = "attachment_verify"
ATTACHMENT_BIND = "attachment_bind"
ATTACHMENT_DELETE = "attachment_delete"
ATTACHMENT_OCR_ENQUEUE = "attachment_ocr_enqueue"
CHANGELOG_CREATE = "changelog_create"
CHANGELOG_UPDATE = "changelog_update"
CHANGELOG_DELETE = "changelog_delete"

NOTIFICATION_CREATE = "notification_create"
NOTIFICATION_UPDATE = "notification_update"
NOTIFICATION_DELETE = "notification_delete"
NOTIFICATION_ROLLBACK = "notification_rollback"

SYSTEM_OP = "system_op"
SYSTEM_FILE = "system_file"
SYSTEM_DB_READ = "system_db_read"
SYSTEM_DB_QUERY = "system_db_query"
SYSTEM_DB_DML = "system_db_dml"
SYSTEM_SETTINGS_UPDATE = "system_settings_update"
SYSTEM_USER_QUOTA_UPDATE = "system_user_quota_update"
SYSTEM_DUAL_AUTH = "system_dual_auth"
SYSTEM_MAINTENANCE_ENABLE = "system_maintenance_enable"
SYSTEM_MAINTENANCE_DISABLE = "system_maintenance_disable"
SYSTEM_SSH = "system_ssh"
SYSTEM_SSH_COMMAND = "system_ssh_command"
SYSTEM_SSH_SUGGEST = "system_ssh_suggest"
SECURITY_ALERT = "security_alert"

AI_API_KEY_CREATE = "ai_api_key_create"
AI_API_KEY_UPDATE = "ai_api_key_update"
AI_API_KEY_DELETE = "ai_api_key_delete"
AI_KEY_CREATE = "ai_key_create"
AI_KEY_UPDATE = "ai_key_update"
AI_KEY_DELETE = "ai_key_delete"
AI_OCR_ENGINE_CREATE = "ai_ocr_engine_create"
AI_OCR_ENGINE_UPDATE = "ai_ocr_engine_update"
AI_OCR_ENGINE_DELETE = "ai_ocr_engine_delete"
AI_OCR_ENQUEUE = "ai_ocr_enqueue"
AI_PROMPT_ACCESS = "ai_prompt_access"
AI_QUOTA_SET = "ai_quota_set"
AI_QUOTA_RESET = "ai_quota_reset"
AI_QUOTA_REQUEST_APPROVE = "ai_quota_request_approve"
AI_QUOTA_REQUEST_REJECT = "ai_quota_request_reject"
AI_MODERATION_UNRESTRICTED = "ai_moderation_unrestricted"
AI_SAFETY_GUARD_ERROR = "ai_safety_guard_error"
AI_SAFETY_GUARD_SCHEDULED_SKIP = "ai_safety_guard_scheduled_skip"
AGENT_JAILBREAK_WARNING = "agent_jailbreak_warning"

PLATFORM_MISUSE_REPORT = "platform_misuse_report"
PLATFORM_MISUSE_VIOLATION = "platform_misuse_violation"
PLATFORM_MISUSE_EXEMPT = "platform_misuse_exempt"

QUOTA_DECISION_EVENTS = {
    "approved": AI_QUOTA_REQUEST_APPROVE,
    "rejected": AI_QUOTA_REQUEST_REJECT,
}
