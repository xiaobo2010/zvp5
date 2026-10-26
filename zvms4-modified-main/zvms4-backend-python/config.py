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

# 部署的 worker 进程数（``None`` = 按环境变量自动探测 WEB_CONCURRENCY /
# UVICORN_WORKERS，都缺省为 1）。频控/熔断/凭据缓存都是进程内状态，进程内阈值
# 按这个数折算，否则加了 --workers N 会静默变成「限额 × N」。
AI_WORKER_COUNT = None

# 独立 API key 的进程内缓存秒数（校验命中时不在每个请求上读库）。
AI_API_KEY_CACHE_TTL = 30
# 单个 API key 的每分钟调用上限（额外于用户维度的 chat 频控）。
AI_API_KEY_RATE_LIMIT_PER_MINUTE = 60

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

# agent（智能体）执行的硬上限，防「死循环烧钱」（Denial of Wallet）。
# 每一轮都是一次独立的上游调用、各扣一次 chat 额度，所以这两个数字直接决定
# 一次 run 的最坏成本：iterations × 单轮 token。
AI_AGENT_MAX_ITERATIONS = 10
AI_AGENT_TOKEN_BUDGET = 200_000
# 写工具提案的有效期（秒）。过期后 confirm 回 410，需要重新生成提案 ——
# 「10 分钟前看到的那条改动」不该在半小时后被确认执行。
AI_AGENT_PROPOSAL_TTL_SECONDS = 600

# prompt 受控留存（审计层，AI_PROMPT_AUDIT）：AI_CALL_LOGS「零正文」的唯一例外。
# 只有这些角色（per 口径）的对话正文会留一份；默认学生会（department）与管理员
# （admin）。管理员自查同样需要留痕：出问题时「谁在什么时候问过什么」不能因为
# 提问者恰好是管理员就断链。
# 判定只看 roles 交集（services.ai.prompt_audit.should_capture），多角色账号命中
# 任一角色即捕获，不按角色优先级只取一个。
AI_PROMPT_CAPTURE_ROLES = ("admin", "department")
# 正文保留天数（tasks/retention.py 日清，缺省 30 天）。
AI_PROMPT_RETENTION_DAYS = 30
# 单条正文的字符上限（超出截断）：CLOB 不是垃圾桶。
AI_PROMPT_MAX_CHARS = 8000
