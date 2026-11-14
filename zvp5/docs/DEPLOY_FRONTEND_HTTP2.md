# 前端源站回前端 + HTTP/2 与连接复用（2026-11-02）

> 本文修正 2026-11-01「前端 443 大面积不可达」的定性，并把 h2、上游 keepalive、
> 客户端预连接三项改动落到仓库与生产。
> 前置阅读：`DEPLOY_FRONTEND_TO_BACKEND_HOST.md`（11-01 前端源站暂落后端机）、`DEPLOY_DIRECT_API.md`
> （浏览器直连 API）、`DEPLOY_API_SPLIT.md`（域名拆分）。

## 1. 根因修正：前端 的 443 是宝塔面板国家级 DROP，不是供应商封锁

11-01 的结论是「前端(zvp-frontend, <FRONTEND_IP>) 的 443 对相当一部分网络不可达，
供应商侧路由/过滤，机内无法修复」。实测推翻了这个判断：

- `iptables -L IN_BT_Country -n -v`：200+ 条 `-p tcp --dport 443 -j DROP`，每条按
  国家 ipset 匹配，命中计数持续增长（US 2522 / SG 367 / JP 218 / KR 91）。
  名单里没有 CN，所以国内住宅 IP 一直是通的。
- `tcpdump -ni any 'tcp and port 443 and host <BACKEND_IP>'`：后端发起的三次
  SYN 全部到达 `ens3`，前端 不回 SYN-ACK —— 是本机 netfilter 丢弃，不是上游路由。
- 修复前后同一组探针：后端 / 另一处主机 / 另一处主机三处出口从 `timeout` 变为 `200 + HTTP/2`。

**结论**：11-01 的「事故」是探针出口落在被 DROP 的国家（韩国、日本、另一处主机、新加坡、
美国全在名单里），不是 前端 机房的问题。国内用户当时并未受影响。

## 2. 拓扑（2026-11-02 起，取代后端方案）

```
浏览器 ──┬─ https://zvp.xiaobopisces.top     → 前端(<FRONTEND_IP>) nginx h2
         │      ├─ 静态 SPA / 静态资源（gzip_static）
         │      └─ /api/system/{files,ssh}/* → 后端 nginx → 127.0.0.1:8018（机器本地端点）
         └─ https://zvpapi.xiaobopisces.top → 后端(<BACKEND_IP>) nginx h2
                ├─ /api/*       → upstream zvms4_api（keepalive 32）→ uvicorn 8018
                └─ /socket.io/  → 同上（升级请求另走 h1.1）
```

后端**不跟着回前端**：ADB 在数据库侧，`db/oracle/engine.py` 注释记录实测
「前端→数据库侧单程 138ms」。后端回 前端 等于给每条 SQL 加 138ms，只有静态资源适合放在 前端。

## 3. 仓库改动

| 文件 | 改动 | 为什么 |
|---|---|---|
| `deployment/zvms4.nginx.conf` | `http2 on`；`upstream zvms4_local_api { keepalive 32; keepalive_requests 1000; keepalive_timeout 60s; }`；`proxy_set_header Connection ""`；`keepalive_timeout 65`；`open_file_cache`；`ssl_session_timeout 1d` | h2 让一次页面加载的资源/接口复用同一条 TLS 连接；清空 `Connection` 才能让 nginx→uvicorn 的连接池真正生效 |
| `deployment/zvms4.service` | `uvicorn ... --timeout-keep-alive 75` | uvicorn 默认 5s 空闲即断，浏览器两次操作之间就要重连；75s 大于 nginx 侧 65s，由 nginx 先关 |
| `zvms4-frontend/vite.config.ts` | 新增 `preconnectApiOrigin()` 插件，构建期按 `VITE_API_BASE` 注入 `<link rel="preconnect">` + `<link rel="dns-prefetch">` | 页面在 前端、API 在后端，跨源首请求原本要等 JS 加载完才握手；预连接与 bundle 下载并行 |

`http2 on` 需要 nginx ≥ 1.25.1（`listen ... http2` 旧写法已废弃）。生产实测：
后端 1.26.3、前端 1.28.3。

### 3.1 为什么 h2 不会破坏 Web SSH

`Upgrade` 是逐跳头，在 h2 里非法；但浏览器对 `new WebSocket()` **固定新建 h1.1 连接**，
不会复用已协商 h2 的那条。同端口靠 ALPN 分流即可，实测 `/api/system/ssh/ws` 返回 101。
11-01 曾因此「刻意不开 http2」，属于过度保守。

## 4. 生产变更流水

时间 = 2026-11-02，本机 +0800。

| # | 主机 | 操作 | 备份 |
|---|---|---|---|
| 1 | 前端 | `iptables -F IN_BT_Country`，并从 `/www/server/panel/data/iptablesdata` 删除对应行（该文件是面板的持久化快照，不清会随重启回来） | `/root/backup-20261102/iptables-pre-http2.save`、`iptablesdata.bak` |
| 2 | 前端 | 重写 vhost：`http2 on`、上游 keepalive、`Connection ""`、`keepalive_timeout`、`open_file_cache`、`ssl_session_timeout 1d` | `/root/backup-20261102/zvp.xiaobopisces.top.conf` |
| 3 | 前端 | `dist` 用后端线上同一份产物替换（330 文件 / 154 个 `.gz`），再叠加本次含 preconnect 的构建 | `frontend-dist.pre-la-http2-20261102-164715` |
| 4 | 前端 | 证书路径从面板副本（2026-12-13 到期）切到 `/etc/letsencrypt/live/zvp.xiaobopisces.top/`（2027-01-21 到期），并新增 `/etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh` | 原 vhost 备份 |
| 5 | 后端 | `sites-available/zvp`、`zvpapi` 加 `http2 on`；新建 `/etc/nginx/conf.d/zvms4-upstream.conf`（upstream 定义两 vhost 共用）；`/api/` 改走 upstream 并清 `Connection` | `/root/backup-20261102/{zvp,zvpapi,zvms4.service}` |
| 6 | 后端 | `zvms4.service` 加 `--timeout-keep-alive 75`，`daemon-reload` + `restart` | 同上 |
| 7 | Cloudflare | `zvp` A 记录 `<BACKEND_IP> → <FRONTEND_IP>`（`record_id <CF_RECORD_ID>`，灰云，TTL 300） | 改回旧 IP 即回滚 |

> Cloudflare 凭据：`cf-api-token-new` 只有读权限，写 DNS 要用
> `~/.config/opencode/credentials/cf-api-token`。

## 5. 验证记录（可复跑）

| 项 | 方法 | 结果 |
|---|---|---|
| 前端 443 可达性 | 后端 / 另一处主机 / OCI 三地 `curl --resolve ...:443:<FRONTEND_IP>` | 修复前 timeout → 修复后 `200`，`http_version=2` |
| DNS | 8.8.8.8（各处出口各自解析）+ DoH | 均为 `<FRONTEND_IP>` |
| 首页 | `curl -A Mozilla/5.0 https://zvp.../` | `HTTP/2 200`，gzip 后 wire 795B |
| 主 bundle | 带 `Accept-Encoding: gzip` | `HTTP/2 200`，`content-encoding: gzip`，168134B（原 445KB） |
| 预连接 | 抓首页 HTML | 含 `rel="preconnect" href="https://zvpapi.xiaobopisces.top"` |
| API | `https://zvpapi.../api/version` ×3 | `200`，`http_version=2` |
| 上游复用 | 后端 `ss -tn state established '( dport = :8018 )'` | 连续请求后仍有常驻连接（keepalive 生效） |
| Web SSH | 带 `Upgrade` 头请求 `/api/system/ssh/ws` | `101 Switching Protocols` |
| CORS 预检 | `OPTIONS` + 浏览器 UA + `Origin: https://zvp.xiaobopisces.top` | `200`，ACAO/ACAM/ACAH 齐备 |
| 证书 | `openssl s_client` + `x509 -dates` | `notAfter=Jan 21 2027`（certbot 链） |
| 线上日志 | 前端 `error.log`（11-02） | 0 条；access log 出现真实国内用户 `200/HTTP2` |
| 前端单测 | `npx vitest run` | 见仓库提交记录 |

## 6. 回滚

1. **DNS**：CF 把 `zvp` A 记录改回 `<BACKEND_IP>`（TTL 300，约 5 分钟）。后端
   `zvp` vhost 仍保留 SPA + 本机 `/api` 反代，可直接承载。
2. **前端 vhost**：`cp /root/backup-20261102/zvp.xiaobopisces.top.conf` 回原路径，`nginx -t && nginx -s reload`。
3. **前端 防火墙**：`iptables-restore < /root/backup-20261102/iptables-pre-http2.save`，
   再把 `/root/backup-20261102/iptablesdata.bak` 覆盖回面板快照。
4. **后端**：`cp /root/backup-20261102/{zvp,zvpapi} /etc/nginx/sites-available/ && cp /root/backup-20261102/zvms4.service /etc/systemd/system/`，
   `nginx -t && systemctl reload nginx && systemctl daemon-reload && systemctl restart zvms4`；
   同时删除 `/etc/nginx/conf.d/zvms4-upstream.conf`（回滚后的 vhost 不再引用它，留着也无害）。

## 7. 遗留与注意

- **不要再用「境外 IP 探针超时」判定 前端 不可达**：先看 `iptables -L IN_BT_Country`
  和 `tcpdump` 是否收到 SYN。
- 前端 面板里 200+ 个国家 ipset 仍占内存且已无规则引用，可择机
  `ipset list -n | grep in_bt_country | xargs -n1 ipset destroy` 释放；本次未动，
  留着便于回滚。
- 前端 `/www/wwwlogs/zvp.xiaobopisces.top.log` 已 147MB 未轮转，建议 logrotate。
- 前端 旧后端 `/www/zvms4/backend` 含过期机密副本（已 chmod 600），随机器退役清理。
- 后端 `sites-enabled` 仍有 3 个 `zvms4.bak-*` 残留（同名冲突告警，加载无影响）。
- 后端 `zvp` vhost 现在是**热备**：DNS 不回滚就不会接管流量，但配置保持可服务状态。
