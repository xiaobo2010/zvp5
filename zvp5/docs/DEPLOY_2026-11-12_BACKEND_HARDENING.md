# 2026-11-12 后端鲁棒性与性能收口

审计结论：代码无 P0，运行面健康（24h `/api` 0 个 5xx、0 条 Traceback、负载近零）。
本轮只落地审计里优先级最高的三项（nginx 限流/超时、凭据、SQL 收口 + OCR 连接复用），
其余遗留见文末。

## 一、已落地：nginx 限流 + 上游超时 + 排障日志字段（生产，后端）

改动两份 vhost（`zvp` / `zvpapi`）与一份新片段：

| 项 | 前 | 后 |
|---|---|---|
| `/api/` 限流 | 无（扫描直接穿透到单进程 uvicorn） | `limit_req zone=zvp_api` 30r/s + burst 60 nodelay |
| `POST /api/users/auth` | 无 nginx 闸门（只有应用层 5/账号、60/IP/分） | `limit_req zone=zvp_auth` 120r/m + burst 30 nodelay |
| 上游超时 | `proxy_read_timeout 3600s` / `send 3600s` | **120s**；Web SSH(`/api/system/ssh/ws`)与 `/socket.io/` 仍 3600s |
| 访问日志 | 只有基础字段 | `rt=$request_time urt=$upstream_response_time ua=$upstream_addr` |

阈值刻意比应用层宽松：校内 NAT 后面几十个学生同时刷新也不该被挡，这里只用于拦
扫描/爬取。nginx 层的 429 在日志里 `ua=-`，可与应用层限流的 429 区分。

片段入库：`deployment/nginx/zvp-limits.conf`（`limit_req_zone` + `log_format`，放
`/etc/nginx/conf.d/`）。

验证：

```
300 并发 GET /api/__ratecheck → 225×404 + 75×429（429 行 ua=-）
6 秒后 /api/health → 200（配额正常回填）
/api/health → 200，POST /api/users/auth({}) → 422（未被误限）
```

回滚：`/root/nginx-backup-20261112-224348/`（两份 vhost 原件），`cp` 回去 +
`nginx -t && systemctl reload nginx`；`conf.d/zvp-limits.conf` 可直接删除。

## 二、已落地：ADB 口令迁出 settings.py（生产，后端）

| 项 | 前 | 后 |
|---|---|---|
| `settings.py` 权限 | `664 www-data:www-data`（同机任何用户可读明文口令） | **0600 www-data:www-data** |
| 口令存放 | `settings.py` 明文常量 | `/etc/zvms4/zvp-secrets.env`（root:root 0600），systemd `EnvironmentFile` 注入 |
| 读取方式 | 模块常量 | `_required_env()` 读环境变量，缺失即启动失败（fail-fast，不会静默用空口令连库） |

unit 变更：`EnvironmentFile=/etc/zvms4/zvp-secrets.env`（`deployment/zvms4.service` 已同步）。

验证：

```
迁移前：systemd-run --uid=www-data --property=EnvironmentFile=... python probe_db.py
        → DB_OK (1,)
重启后：systemctl is-active → active；/proc/<pid>/environ 含 ZVMS_ORACLE_PASSWORD
        journal 无 error/traceback；/api/health 200；登录接口 422（正常校验）
```

回滚：`/root/settings-backup-20261112-224544/`（`settings.py` + `zvms4.service`），
`cp` 回去 + `systemctl daemon-reload && systemctl restart zvms4`。

注意：`settings.py` 不参与 rsync（部署脚本已排除），`deployment/configure_runtime.py`
生成的口令仍是明文写法——下次用它重建 settings 时要一并改成 env 读取。

## 三、已推送未部署：REGEXP_LIKE 收口 + OCR 连接复用

代码改动只在仓库，**本轮不部署**（生产后端仍跑 `3be6068`）。

### 3.1 关键字检索改字面量 LIKE

- 新增 `db/repos/oracle/search.py`：`like_pattern()` / `like_prefix_pattern()` /
  `SEARCH_QUERY_MAX` / `LIKE_ESCAPE`，收敛此前 `times` / `app_logs` / `device_bans`
  三份重复实现。
- `users` / `members` / `groups` / `activities` / `violations` / `ban_records` /
  `app_logs` / `times` / `device_bans` 的 `REGEXP_LIKE` 全部改为
  `LOWER(col) LIKE :p ESCAPE '\'`；多关键词仍是 AND；`%`/`_`/`\` 按字面量。
- 上游 router / agent 工具里喂给仓储的 `re.escape` / `escape_oracle_regex` 预转义
  一并删除（否则会被 LIKE 二次转义）。
- 刻意保留：`db/models.py` 6 处 `REGEXP_LIKE` 是建表 `CheckConstraint`（改了会与
  已部署库的约束不一致）；`services/db_monitor.py:272` 是监控视图对 `v$sql` 的过滤。
- 语义变化：`regex` 查询参数在 5 个 v2 端点仍保留，但**已废弃**（前端与 Agent 均
  未传），关键字一律按字面量匹配。

### 3.2 OCR 复用 httpx client

`services/ai/ocr_engines.py` 由「每次调用新建 `AsyncClient`」改为模块级复用（百度
token + OCR、Groq、智谱），逐请求 timeout 与失败切换逻辑不变；连接池 limits 显式
设置；事件循环变化时自动重建；`scheduler.shutdown_event()` 里 `await ocr_engines.aclose()`。

### 3.3 验证

```
pytest -q                          → 4524 passed（新增 24 例）
ruff check db routers services ...  → All checks passed
仓库层 REGEXP_LIKE 残留              → 0（仅 DDL 与监控视图保留）
```

## 四、本轮未处理（按优先级）

1. **备份语义**：`adb-sync` 是每日全表镜像到第二个 ADB，误删/误更新 T+1 即被覆盖，
   没有可回溯副本；仍需 Data Pump 导出 + 保留 N 天 + 恢复演练。
2. **单点**：单机单进程 uvicorn、单 Redis，无外部探活；`Restart=on-failure` 救不了机器故障。
3. **连接池耗尽会冻结整个事件循环**（`db/oracle/engine.py` 文档自认的权衡）：池 8+8、
   checkout 是阻塞 `queue.get`，Oracle 抖动期是整站失联而非局部 503。
4. `X-Real-IP` 无条件信任（部署绑 127.0.0.1 兜底，非 systemd 方式启动会被伪造 header 击穿）。
5. JWT 10 天无轮换；三个 `BaseHTTPMiddleware`；上传配额 TOCTOU；下载令牌仅存进程内存。
6. `TNS_ADMIN` 指向备份库钱包（死配置，实际生效的是 `config_dir`）；`adb-sync.log` 无 logrotate。
7. 生产前端仍是 11-08 代的旧构建（与本轮无关，单独处理）。
