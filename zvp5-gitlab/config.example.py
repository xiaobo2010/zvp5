from typing import *

DATABASE_URI: LiteralString = 'sqlite:///:memory:'
REDIS_URI: LiteralString = 'localhost:3333'
# --- Oracle Autonomous Database (ADB) 示例 ---
# 采用 python-oracledb thin 模式 + mTLS wallet（无需 Oracle Instant Client）。
# wallet 目录需含 tnsnames.ora / ewallet.pem；口令从环境变量或凭证文件注入，切勿写死。
#
# import os
# WALLET = os.environ["ADB_WALLET_DIR"]      # 例: /root/adb-keepalive/wallet
# PW     = os.environ["ADB_ADMIN_PASSWORD"]  # 从凭证文件读取
# DATABASE_URI: LiteralString = (
#     f'oracle+oracledb://ADMIN:{PW}@zvp5adb_high'
#     f'?config_dir={WALLET}&wallet_location={WALLET}&wallet_password={PW}'
# )
#
# 注意: 口令含 URL 特殊字符需百分号编码（如 '#' -> '%23'），否则 URL 会被截断。
# 并在 create_async_engine(...) 增加: pool_pre_ping=True, pool_recycle=1800
# （ADB 会终止空闲连接：资源紧张 >5min、空闲 >24h 一律断）
