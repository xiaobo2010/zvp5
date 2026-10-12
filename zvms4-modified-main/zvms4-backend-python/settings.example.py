MONGODB_URI = ""  # MongoDB URL
MONGODB_DB = ""  # MongoDB Database
SECRET_KEY = ""  # Secret Key
IMGBED_SECRET_KEY = ""  # Imagebed Secret Key
IMGBED_SERVER = ""  # Imagebed server
BLOCKED_USERS = [] # Blocked users
LANGUAGE = "zh-CN"  # util/calculate.py generate_description language
CHINESE_FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"  # util/statement.py PDF font (has built-in fallbacks)

# Oracle Autonomous Database (ADB) - see db/oracle/README.md. Scaffolding only; app is not wired to it yet.
ORACLE_ENABLED = False  # Master switch for the Oracle connection layer
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

# Modules that use the Oracle backend. Empty = everything stays on Mongo.
# Valid values: "users", "groups", "activities", "activity_members", "time", "stats", "logs".
# db/backends.py reads this to decide, per module, whether to hand out the Mongo
# or the Oracle repository implementation (a module is only ever on one backend).
ORACLE_MODULES: list[str] = []