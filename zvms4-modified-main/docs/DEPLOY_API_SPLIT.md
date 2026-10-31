# 内容站 / API 拆分部署（2026-10-31）

后端从 `zgovps-la` 迁到 `tencent-43`，前端仍由 `zgovps-la` 分发；浏览器继续同源访问
`/api`，由内容站 nginx 反代到 API 域名。

## 拓扑

| 角色 | 域名 | 机器 | 进程 |
|---|---|---|---|
| 内容站（前端 dist） | `zvp.xiaobopisces.top` | zgovps-la `23.169.184.163` | nginx（宝塔），root `/www/zvms4/frontend-dist` |
| API | `zvpapi.xiaobopisces.top` | tencent-43 `43.128.154.199` | nginx → uvicorn `127.0.0.1:8018` |

- DNS：Cloudflare zone `xiaobopisces.top`，`zvpapi` 为 **A 记录 · 灰云**（`proxied=false`）。
  之所以没开橙云：该 zone 的 SSL 模式是 `flexible`，橙云会把回源请求打到 80 端口，
  与 80→443 的跳转形成循环；而用 Config Rule 给单域名切 `strict` 需要当前 token
  没有的权限（返回 `request is not authorized`）。要开橙云，先在控制台把 zone 的
  SSL 模式改为 Full (strict)，或加一条 `http_config_settings` 规则再把记录改橙。
- TLS：两台都用 Let's Encrypt；tencent-43 的证书在
  `/etc/letsencrypt/live/zvpapi.xiaobopisces.top/`，`certbot.timer` 负责续期。

## tencent-43（API 机）

- 代码：`/www/zvms4/backend`，systemd `zvms4.service`，`User=www-data`。
- 数据库：从 zgovps-la 复制了生产配置与钱包
  （`/www/zvms4/adb-wallet`，`ORACLE_ACTIVE="prod"`），必须保持 `www-data` 可读
  （本次踩过：wallet 属主是 `lighthouse` 导致 `DPY-4026 tnsnames.ora unreadable`）。
- Redis：`redis-server` 本机 6379，256MB、`allkeys-lru`、无持久化；
  `settings.py` 里 `REDIS_URI = "redis://127.0.0.1:6379/0"`。
- CORS：`/etc/systemd/system/zvms4.service.d/cors.conf`
  ```
  Environment="ZVMS_CORS_ALLOWED_ORIGINS=https://zvp.xiaobopisces.top,https://zvpapi.xiaobopisces.top"
  ```
  浏览器实际走同源（见下），这条主要给直连 API 的客户端/工具用。
- nginx：`/etc/nginx/sites-available/zvpapi`
  - 80：ACME challenge + 301 到 443
  - 443：`/api/`（`proxy_buffering off`，SSE 必需）、`/api/system/ssh/ws`（Upgrade）、
    `/socket.io/`（Upgrade），其余路径 404
- 资源参考：常驻内存约 155MB，CPU <1%。

## zgovps-la（内容站）

前端 dist 未变（仍按相对路径 `/api` 构建），本次只改 vhost 反代：

```nginx
upstream zvpapi_backend {
    server 43.128.154.199:443;
    keepalive 32;
}

location /api/ {
    proxy_pass https://zvpapi_backend;
    proxy_http_version 1.1;
    proxy_set_header Connection "";        # 保住 keepalive
    proxy_ssl_server_name on;
    proxy_ssl_name zvpapi.xiaobopisces.top;
    proxy_ssl_verify off;
    ...
}
location /api/system/ssh/ws { ... Upgrade ... }
location /socket.io/ { ... Upgrade ... }
```

- 配置备份：`zpv.xiaobopisces.top.conf.bak-pre-api-split-*`
- 旧后端已 `systemctl disable --now zvms4`（避免两个实例同时跑 APScheduler
  定时任务）；要回滚：改回 `proxy_pass http://127.0.0.1:8018` + `systemctl enable --now zvms4`。

## 为什么值得拆

| 指标 | zgovps-la（旧） | tencent-43（新） |
|---|---|---|
| ADB 查询往返 | ~570 ms | ~24 ms |
| 浏览器 → 内容站 | 直连 | 直连（未变） |
| 内容站 → API（keepalive 命中） | 本机 ~2 ms | ~165 ms |

用户请求仍只经历「浏览器 → 内容站」一段公网延迟；多出来的是机房之间 165ms 的
反代，而被抵消的是每次 ADB 查询 500ms+ 的跨洋往返。DB 查询越多的端点收益越大。

## 已知注意事项

1. **tencent-43 上原有的 `zvp.albwxy.fun` 站点**也指向 `/www/zvms4/backend`。本次把
   该后端切到了生产库，所以这个站点现在也吃生产数据；若它只是 staging，需要单独
   给它配 `ORACLE_ACTIVE="test"` 的进程或关掉。
2. 内容站反代是双层 nginx，API 侧 nginx 日志里的客户端 IP 是 `23.169.184.163`；
   需要真实 IP 时依赖 `X-Forwarded-For`（后端已按此解析）。
3. Redis 只在本机监听；`/etc/redis/redis.conf` 末尾的覆盖段不要删。
