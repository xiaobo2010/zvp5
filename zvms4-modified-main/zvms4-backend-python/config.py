BASE_ON_CAMPUS = 25
BASE_OFF_CAMPUS = 15
BASE_SOCIAL_PRACTICE = 18
ON_TO_OFF_RATE = 1 / 3
OFF_TO_ON_RATE = 1 / 2
MAX_EXCEED_DISCOUNT = 100
MAX_QUERY_COUNT = 30
MAX_ACTIVITY_DESCRIPTION_LENGTH = 1000
APP_LOGS_RETENTION_DAYS = 90
EXPORT_TASKS_RETENTION_DAYS = 30
RETENTION_BATCH_SIZE = 5000
RETENTION_MAX_BATCHES = 20

# AI 网关默认月额度（未在 AI_QUOTA 表里单独设限时使用）。
AI_DEFAULT_OCR_MONTHLY_UNITS = 100
AI_DEFAULT_CHAT_MONTHLY_UNITS = 200000
AI_OCR_MAX_IMAGE_BYTES = 20 * 1024 * 1024

# 凭据目录（个人/共享/服务端三层）的进程内缓存秒数。改完 key 由 CRUD 主动失效，
# 这个 TTL 只兜「别的进程改了、本进程还没感知」的窗口。
AI_CREDENTIALS_CACHE_TTL = 30

# 允许使用 AI 网关的角色（per 口径）。学生会 = department，管理员 = admin。
AI_ALLOWED_ROLES = ("admin", "department")

# 每用户每分钟调用上限（滑动窗口）。月额度防长期滥用，这一层防瞬时打爆。
AI_RATE_LIMIT_PER_MINUTE = {"ocr": 10, "chat": 20}
AI_RATE_LIMIT_WINDOW_SECONDS = 60
# AI 调用日志保留天数（tasks/retention.py 防御式读取，缺省 90）。
AI_CALL_LOGS_RETENTION_DAYS = 90

# 按角色的月额度；未列出的角色回退 AI_DEFAULT_*。个人 key 仍不占额度。
AI_ROLE_QUOTA = {
    "admin": {"ocr": 500, "chat": 1_000_000},
    "department": {"ocr": 100, "chat": 200_000},
}
