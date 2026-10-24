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

# 管理员在 /api/ai/keys 里建的凭据（个人 / 共享）加密落库，主密钥从这里取：
# 优先环境变量 ZVMS_AI_KEY_SECRET；没有则读仓库根 aes_key.txt（65 字节随机串，
# 已在 .gitignore 里，不入库）。派生方式：
#   sha256(b"zvms-ai-credential-v1:" + secret) -> 32 字节 AES-256-GCM 主密钥
# 换主密钥等于让所有已存密文解不开（调用方会收到 502 AI_CREDENTIAL_UNREADABLE），
# 所以轮换时要先把旧 key 重新录入。
# ZVMS_AI_KEY_SECRET = ""
