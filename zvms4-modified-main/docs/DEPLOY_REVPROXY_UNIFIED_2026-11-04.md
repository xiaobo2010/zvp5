# 2026-11-04 恢复「前端服务器统一反代」拓扑

## 2026-11-06 标准发布命令（当前唯一口径）

竞速能力是**构建期注入**的，漏注入不会报错、只会静默失效，所以发布走固定命令：

```bash
cd zvms4-frontend
VITE_API_BASE=https://zvp.xiaobopisces.top/api \
VITE_API_RACE_BASE=https://zvpapi.xiaobopisces.top/api \
npm run build
rsync -az --delete ./dist/ zvp-frontend:/www/zvms4/frontend-dist/
ssh zvp-frontend 'chown -R www:www /www/zvms4/frontend-dist'
```

发布后必须核对产物，两个基址缺一即视为竞速未生效：

```bash
grep -c "Both API paths failed" dist/assets/index-*.js        # 竞速模块在包里
grep -oh "https://zvp[a-z]*\.xiaobopisces\.top/api" dist/assets/*.js | sort -u
# 期望同时看到 zvp.xiaobopisces.top（主路，走 前端反代）和 zvpapi.xiaobopisces.top（备路，直连后端）
```

**为什么曾经"没生效"**：2026-11-04 那次发布是双路竞速代码合入之前构建的，产物里根本
没有竞速模块；后来即使带着代码重新构建，若只注入 `VITE_API_BASE`，
`hedgedGetRelayBase()` 会因为备用基址为空直接返回 `null`，代码在包里也一次都不会触发。

## 结论

浏览器只访问 `https://zvp.xiaobopisces.top`（zvp-frontend，<FRONTEND_IP>）。
该机的 nginx 把 `/api/`、`/api/system/ssh/ws`、`/socket.io/` 反代到
zvp-backend 的 `https://zvpapi.xiaobopisces.top`（<BACKEND_IP>:443，backend
uvicorn 127.0.0.1:8018）。前端产物不再出现 `zvpapi` 字样，请求全部同源。

```
浏览器 ──TLS──> zvp.xiaobopisces.top (zvp-frontend, nginx)
                     ├── /            -> /www/zvms4/frontend-dist (静态)
                     ├── /api/        -> https://zvpapi.xiaobopisces.top (zvp-backend)
                     ├── /api/system/ssh/ws  -> 同上（单独 location，带 Upgrade）
                     └── /socket.io/  -> 同上（带 Upgrade）
```

## 为什么之前是直连

2026-11-02 的 API 归属分层把前端迁到前端机、API 留在后端机，构建时注入
`VITE_API_BASE=https://zvpapi.xiaobopisces.top/api`，浏览器直接跨源打后端。
前端 上的 `/api/` 反代一直存在，但没有流量走它。现在改回注入同源基址：

```bash
cd zvms4-frontend
VITE_API_BASE=https://zvp.xiaobopisces.top/api npm run build
rsync -az --delete ./dist/ zvp-frontend:/www/zvms4/frontend-dist/
ssh zvp-frontend 'chown -R www:www /www/zvms4/frontend-dist'
```

`VITE_API_BASE` 仍必须是绝对 HTTPS URL（`src/utils/apiBaseConfig.ts` 的强校验），
只是host 换成页面自己的 host，语义上是同源。

## 关键配套：真实客户端 IP

反代把「所有用户」收敛成 前端 一个对端 IP，直接后果是登录限流、封禁、审计日志
全部按 `<FRONTEND_IP>` 计数——一人触发全站遭殃。因此两层都要改：

**zvp-frontend vhost**（`/www/server/panel/vhost/nginx/zvp.xiaobopisces.top.conf`）

```nginx
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $remote_addr;   # 覆盖，不再 $proxy_add_x_forwarded_for
```

覆盖而非追加：客户端自带的 XFF 不会被带进上游，转发链只有一跳，语义确定。

**zvp-backend vhost**（`/etc/nginx/sites-available/zvpapi`，文件顶部 http 上下文）

```nginx
set_real_ip_from <FRONTEND_IP>;
real_ip_header X-Forwarded-For;
real_ip_recursive on;
```

只有 前端 的转发可信，`$remote_addr` 被还原成真实客户端，随后各 location 的
`proxy_set_header X-Real-IP $remote_addr` 就是真实 IP。后端
`typings/log.py::client_ip_from` 以 `X-Real-IP` 为准，所以这一条不能省。

验证（2026-11-04 23:28，zvp-backend `/var/log/nginx/access.log`）：

```
<CLIENT_IP> - - [...] "GET /api/health HTTP/1.1" 200 38 "-" "Mozilla/5.0 realip-probe-"
<CLIENT_IP> - - [...] "GET /api/v3/users/me/device HTTP/1.1" 200 151 ...
```

各用户 IP 独立成行，没有挤到 <FRONTEND_IP>。

## 验收记录

| 项 | 结果 |
|---|---|
| 产物 API 基址 | `assets/*.js` 仅含 `https://zvp.xiaobopisces.top/api`，全量 grep `zvpapi` = 0 |
| 页面 | `GET /` 200，index 引用 `index-aIh1pLpn.js` |
| API 透传 | `/api/health` 200 `{"status":"ok"}`；`/api/system/maintenance` 200 |
| 等价性 | POST `/api/users/login`、`/api/v3/users/me/device`、ssh ws 升级，直连 43 与走 前端反代返回码逐项一致（405 / 401 / 404） |
| WebSocket | `/socket.io/?EIO=4&transport=polling` 200 |
| 反代头 | 前端 `X-Forwarded-For $remote_addr` 3 处；后端 `real_ip` 生效 |
| 静态资源 | `/assets/index-aIh1pLpn.js` 200，446611 bytes；`/sw.js`、`/manifest.webmanifest` 200 |

## 副作用与回滚

- 切回同源后浏览器 origin 变了：`zvpapi` 域下的 cookie、localStorage、
  IndexedDB 不再命中，用户需重新登录一次，设备解锁令牌（Xuehai-Id 相关）也会重置一次。
- 上传上限从后端的 30m 变为前端的 100m（`client_max_body_size 100m`），非收紧。
- 回滚两条命令：
  - 前端：`VITE_API_BASE=https://zvpapi.xiaobopisces.top/api npm run build` 后重新发布，
    或直接把 `/www/zvms4/frontend-dist.bak-20261104-232651` 换回去。
  - 反代：`/root/zvp-vhost.bak-prerevproxy-20261104-231901`（前端）、
    `/etc/nginx/sites-available/zvpapi.bak-prerevproxy-20261104-231850`（后端）。
- `open_file_cache` 生效期内（最长 120s）发布 index.html 会短暂读到旧引用，属正常。

## 遗留可选项

- `zvpapi.xiaobopisces.top` 仍对公网开放，且本机还留着一份 2026-11-02 的 SPA 副本。
  若要「真正统一入口」，可把该 vhost 的 `/api/` 限定 `allow <FRONTEND_IP>; deny all;`，
  根路径 301 到 zvp 域；代价是 前端 出口 IP 变化时会全站 502，需同时留后门 SSH 改配置。

## 2026-11-06 小型 JSON GET 双路竞速

统一反代仍是正常请求路径；浏览器同时向后端直连 API 发一份副本。两路都完整
收到后，第一条成功响应获胜，另一条随即取消。该能力只覆盖明确的常用 JSON
GET 白名单（版本、维护状态、通知、活动列表/详情、用户/分组详情等），不覆盖
写请求、文件、导出、SSE 和流式接口。

构建时必须同时注入：

```bash
VITE_API_BASE=https://zvp.xiaobopisces.top/api \
VITE_API_RACE_BASE=https://zvpapi.xiaobopisces.top/api \
pnpm build
```

- `VITE_API_BASE` 继续是浏览器正常同源路径。
- `VITE_API_RACE_BASE` 是后端直连竞速路径；未配置时竞速自动关闭。
- 后端已经允许页面 Origin 的 CORS，前端 CSP 也已放行 `zvpapi` Origin。
- 发布前必须确认线上页面 Bundle 同时包含两个 API Origin 和竞速模块，不能只
  看 `VITE_API_BASE`。

## 2026-11-06 双路竞速首次上线（发布记录）

| 项 | 值 |
| --- | --- |
| 产物 | `assets/index-aUiB2Zdp.js`（449082 bytes）、`index-legacy-BYOQHxif.js`、`index-DLiD5bBa.css` |
| 注入 | `VITE_API_BASE=https://zvp.xiaobopisces.top/api`、`VITE_API_RACE_BASE=https://zvpapi.xiaobopisces.top/api` |
| 产物自检 | 竞速模块（`Both API paths failed`）命中 1 处；两个 API 基址同时出现在 `assets/*.js` |
| 线上取包 | `GET /assets/index-aUiB2Zdp.js` 200 / 449082 bytes，两个基址齐备 |
| 主路 | `GET https://zvp.xiaoboisces.top/api/version` 200 `v4.3.0-7`（经 前端反代） |
| 备路 | `GET https://zvpapi.xiaoboisces.top/api/version` 200 `v4.3.0-7`；带 `Origin: https://zvp.xiaobopisces.top` 时回显 `Access-Control-Allow-Origin` + `Allow-Credentials: true` |
| 静态资源 | index.html 引用的 20 个资源全部存在，无 404；`sw.js`、`manifest.webmanifest` 200 |
| 回滚 | `/www/zvms4/frontend-dist.bak-20261106-170011`（前端） |

竞速由浏览器端发起，服务器侧只能确认「代码在包里 + 两路都通」。要看到同一路径在
后端的 access log 里出现两次（一次经前端、一次直连），需要有真实浏览器打开页面。
