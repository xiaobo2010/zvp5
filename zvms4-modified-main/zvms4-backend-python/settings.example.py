SECRET_KEY = ""  # Secret Key
IMGBED_SECRET_KEY = ""  # Imagebed Secret Key
IMGBED_SERVER = ""  # Imagebed server
BLOCKED_USERS = [] # Blocked users
LANGUAGE = "zh-CN"  # util/calculate.py generate_description language

# Oracle Autonomous Database (ADB) - see db/oracle/README.md.
ORACLE_ACTIVE = "prod"  # "prod" | "test" - which instance the config resolves to
ORACLE_USER = ""  # Shared ADB username (both instances)
ORACLE_PASSWORD = ""  # Shared ADB password (both instances)
ORACLE_DSN = ""  # prod: connect string / service name
ORACLE_WALLET_DIR = ""  # prod: wallet directory (thin mode config_dir)
ORACLE_TEST_DSN = ""  # test: connect string / service name
ORACLE_TEST_WALLET_DIR = ""  # test: wallet directory (thin mode config_dir)
# OCI *wallet* password (decrypts ewallet.pem) - NOT the DB user password above.
# Thin mode requires it for our encrypted ewallet.pem, else the driver prompts/hangs.
ORACLE_WALLET_PASSWORD = ""  # prod: wallet password
ORACLE_TEST_WALLET_PASSWORD = ""  # test: wallet password

# AI gateway (services/ai). 密钥不写在这里：api_key_env 指向环境变量名，
# 值走进程环境（settings 同名属性只作为测试兜底）。
AI_ENABLED = True
# name -> {"kind":"openai_compat","base_url":"https://...","api_key_env":"ZVMS_AI_XXX_KEY",
#          "models":["..."],"timeout":{"connect":5,"read":120}}
AI_PROVIDERS = {}
AI_MAX_CONCURRENCY = 8
# 部署的 worker 进程数。不写就按环境变量 WEB_CONCURRENCY / UVICORN_WORKERS 探测，
# 都缺省为 1。频控/熔断/缓存是进程内状态，进程内阈值会按这个数折算。
# AI_WORKER_COUNT = 1
# 独立 API key（/api/ai/api-keys）：单把 key 的每分钟上限与校验缓存秒数。
# AI_API_KEY_RATE_LIMIT_PER_MINUTE = 60
# AI_API_KEY_CACHE_TTL = 30
# 允许使用 /api/ai 的角色（per 口径）。默认学生会 + 管理员。
AI_ALLOWED_ROLES = ("admin", "department")
# 每用户每分钟调用上限（滑动窗口）；0 或缺省 kind 表示不限。
AI_RATE_LIMIT_PER_MINUTE = {"ocr": 10, "chat": 20}
AI_RATE_LIMIT_WINDOW_SECONDS = 60
# 按角色的月额度；未列出的角色回退 AI_DEFAULT_*。个人 key 不占额度。
AI_ROLE_QUOTA = {
    "admin": {"ocr": 500, "chat": 1000000},
    "department": {"ocr": 100, "chat": 200000},
}
# AI 调用日志保留天数（tasks/retention.py 读取，缺省 90）。
AI_CALL_LOGS_RETENTION_DAYS = 90
# 智能体：单次 run 的轮数上限与累计 token 预算（防死循环烧钱）。每一轮都是一次
# 独立计费的上游调用，所以最坏成本 ≈ 轮数 × 单轮 token。
# AI_AGENT_MAX_ITERATIONS = 10
# AI_AGENT_TOKEN_BUDGET = 100000
# 单次 agent run 的 wall-clock 上限（秒）；超时返回已有结果并标 stop_reason=timeout。
# AI_AGENT_RUN_TIMEOUT_SECONDS = 300
# 写操作提案的有效期（秒）；过期后确认回 410。
# AI_AGENT_PROPOSAL_TTL_SECONDS = 600
# 双人授权缓存窗口（秒）：同一 operator 的同一 action 在窗口内不必再输一次动态码。
# 只服务显式 opt-in 的调用方（当前仅 agent 写操作确认）；0 = 关闭缓存，每次都要码。
# DUAL_AUTH_CACHE_TTL_SECONDS = 1800
# prompt 受控留存（AI_PROMPT_AUDIT）：只有这些角色（per 口径）的对话正文会被留
# 一份。默认学生会（department）；写成空元组 () 等于关闭正文留存。
# AI_PROMPT_CAPTURE_ROLES = ("department",)
# 正文保留天数（日清任务清理，缺省 30）与单条字符上限（超出截断，缺省 8000）。
# AI_PROMPT_RETENTION_DAYS = 30
# AI_PROMPT_MAX_CHARS = 8000
# Prompt Cache 注入策略。off = 一律不加字段；auto = 只给白名单 host 加
# prompt_cache_key（路由提示，不改请求结构）；explicit = 额外给白名单 host 打
# cache_control 断点（只有会翻译这个标记的兼容层能用）。
AI_PROMPT_CACHE = "auto"
# prompt_cache_key 的 host 白名单（追加到默认的 api.openai.com）。host 写错只是
# 不生效；但把「不认识该字段」的上游加成 "*" 会让请求被上游 400。
AI_PROMPT_CACHE_HOSTS = []
# cache_control 断点的 host 白名单（默认空）。给 OpenRouter / LiteLLM 这类兼容层
# 用；直连 OpenAI 官方端点加进来会被未知字段校验挡下。
AI_PROMPT_CACHE_BREAKPOINT_HOSTS = []
# prompt_cache_key 的前缀（只留 [A-Za-z0-9_-]，最长 24）。太长会把指纹挤出 64 上限。
AI_PROMPT_CACHE_KEY_PREFIX = "zvp"

# 管理员在 /api/ai/keys 里建的凭据（个人 / 共享）加密落库，主密钥从这里取：
# 优先环境变量 ZVMS_AI_KEY_SECRET；没有则读仓库根 aes_key.txt（65 字节随机串，
# 已在 .gitignore 里，不入库）。派生方式：
#   sha256(b"zvms-ai-credential-v1:" + secret) -> 32 字节 AES-256-GCM 主密钥
# 换主密钥等于让所有已存密文解不开（调用方会收到 502 AI_CREDENTIAL_UNREADABLE），
# 所以轮换时要先把旧 key 重新录入。
# ZVMS_AI_KEY_SECRET = ""
