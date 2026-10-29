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
# 会话上下文窗口（token）。AI_DEFAULT_CONTEXT_LIMIT 是所有模型的兜底；
# AI_MODEL_CONTEXT_LIMITS 按模型名覆盖，其中的 "*" 键是该字典的通配兜底。
# 压缩阈值 AI_CONTEXT_COMPACT_RATIO 默认 0.80，即 200000 窗口下约 160K 触发摘要
# （也可在「系统设置」里按运行期键 ai.context_compact_ratio 覆盖）。
# 注意：生产 settings.py 不随 rsync 同步，改窗口要手改服务器上的配置，或者改
# services/agent/context.py 的代码默认值——只改这个模板不会影响线上。
# AI_DEFAULT_CONTEXT_LIMIT = 200000
# AI_MODEL_CONTEXT_LIMITS = {}  # 形如 {"gpt-x": 400000, "*": 128000}
# 保存渠道、或管理端点从上游拉取模型元数据时，探测 GET {base_url}/models 的超时
# （秒）。<= 0 表示彻底关闭探测：保存时不再联网补上下文，/discover 也直接拒绝发包。
# AI_MODEL_PROBE_TIMEOUT_SECONDS = 2
# 智能体：单次 run 的轮数上限与累计 token 预算（防死循环烧钱）。每一轮都是一次
# 独立计费的上游调用，所以最坏成本 ≈ 轮数 × 单轮 token。预算低于上下文窗口时，
# agent 会在窗口填满之前先被预算掐断，所以默认值与窗口对齐（200000）。
# AI_AGENT_MAX_ITERATIONS = 10
# AI_AGENT_TOKEN_BUDGET = 200000
# 单次 agent run 的 wall-clock 上限（秒）；超时返回已有结果并标 stop_reason=timeout。
# AI_AGENT_RUN_TIMEOUT_SECONDS = 300
# 写操作提案的有效期（秒）；过期后确认回 410。
# AI_AGENT_PROPOSAL_TTL_SECONDS = 600
# 双人授权缓存窗口（秒）：同一 operator 的同一 action 在窗口内不必再输一次动态码。
# 只服务显式 opt-in 的调用方（当前仅 agent 写操作确认）；0 = 关闭缓存，每次都要码。
# DUAL_AUTH_CACHE_TTL_SECONDS = 1800
# prompt 受控留存（AI_PROMPT_AUDIT）：只有这些角色（per 口径）的对话正文会被留
# 一份。默认学生会（department）与管理员（admin）；写成空元组 () 等于关闭正文留存。
# AI_PROMPT_CAPTURE_ROLES = ("admin", "department")
# 正文保留天数（日清任务清理，缺省 30）与单条字符上限（超出截断，缺省 8000）。
# AI_PROMPT_RETENTION_DAYS = 30
# AI_PROMPT_MAX_CHARS = 8000
# LLM 安全认证：department 提交对话前用一次模型调用判定「无关且破坏性」的请求。
# 默认开启；上游持续抖动时可关掉退回纯关键词规则。超时按放行处理。
# AI_SAFETY_GUARD_ENABLED = True
# AI_SAFETY_GUARD_TIMEOUT_SECONDS = 8
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

# SiliconFlow 公开渠道的免费模型白名单（运维维护，misc/seed_channel.py 读取）。
# 只有列在这里的模型会被写进 shared 凭据；留空 = 该渠道拒绝上线，脚本不会去猜。
# 口径（2026-10-25 核对，均已在线的常规免费档）：
#   - 只收支持 function calling 的通用模型：agent 模式会带 tools，选到不支持的模型
#     上游直接报错，宁可不上架；
#   - 不收「限免」模型（翻译/OCR 专用，随时可能恢复收费）与非 chat 模型；
#   - 免费档会变，换档前先看官方定价页，改这里再跑 misc/seed_channel.py。
AI_SILICONFLOW_FREE_MODELS = [
    "zai-org/GLM-5.3-Flash",           # 1M 上下文，多模态，工具调用
    "Qwen/Qwen3.5-4B",                 # 256K，多模态，工具调用
    "XingChenAGI/Xing4.0-29B",         # 256K，工具调用
    "Qwen/Qwen3-8B",                   # 128K，工具调用，主力
    "THUDM/GLM-Z1-9B-0414",            # 128K，推理 + 工具调用
    "THUDM/GLM-4-9B-0414",             # 32K，通用/代码
    "Qwen/Qwen2.5-7B-Instruct",        # 32K，JSON 稳定
]

# 管理员在 /api/ai/keys 里建的凭据（个人 / 共享）加密落库，主密钥从这里取：
# 优先环境变量 ZVMS_AI_KEY_SECRET；没有则读仓库根 aes_key.txt（65 字节随机串，
# 已在 .gitignore 里，不入库）。派生方式：
#   sha256(b"zvms-ai-credential-v1:" + secret) -> 32 字节 AES-256-GCM 主密钥
# 换主密钥等于让所有已存密文解不开（调用方会收到 502 AI_CREDENTIAL_UNREADABLE），
# 所以轮换时要先把旧 key 重新录入。
# ZVMS_AI_KEY_SECRET = ""
