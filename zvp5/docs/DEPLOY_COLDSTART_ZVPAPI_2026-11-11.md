# 冷启动加速：首屏资源改走 zvpapi(前端)（2026-11-11）

## 背景

生产 `zvp.xiaobopisces.top` 的 SPA 静态资源与 API 都由**后端**（<BACKEND_IP>）承载。
后端机出网带宽偏低，冷启动时 `index.html` 引用的首屏 JS/CSS（合计约 2MB）
必须从后端下载，用户体感是"打开很慢"。

前端机（<FRONTEND_IP>，`zvp-frontend`）到用户侧带宽明显优于后端，且前端上已有同一份
`frontend-dist`（与后端 `index.html` md5 一致：`5595bd9d5c72ddf189d7e4cd4dcb4f53`）。

**方案**：页面壳与 API 仍留后端（保持同源 API、SW scope 不变），只把**首屏静态资源**
改从 `zvpapi.xiaobopisces.top`（前端）直出。

## 前置：zvpapi 解析与前端侧站点

`zvpapi.xiaoboisces.top` 的 A 记录已从后端改为前端（<FRONTEND_IP>，TTL 300，不走代理）。

前端侧新增 vhost `/www/server/panel/vhost/nginx/zvpapi.xiaobopisces.top.conf`：

- `/api/` → 反代 `upstream zvpapi_backend`（<BACKEND_IP>:443，Host/SNI = zvpapi.xiaobopisces.top）
- `/assets/` → 本机 `/www/zvms4/frontend-dist`，返回 `Access-Control-Allow-Origin`（跨域 module script 必需）
- `/socket.io/`、`/api/system/ssh/ws` → 同上游，带 Upgrade
- 真实 IP：`X-Forwarded-For $remote_addr`（覆盖，不用 `$proxy_add_x_forwarded_for`，防伪造）；
  后端 `zvpapi` vhost 用 `set_real_ip_from <FRONTEND_IP>` + `X-Forwarded-For` 还原

### 证书

后端 zvpapi 证书的 CN 是 `zvpapi.xiaobopisces.top`。前端侧需要自己的证书：

- `certbot certonly --webroot -w /www/wwwroot/zvpapi.xiaobopisces.top -d zvpapi.xiaobopisces.top`
- **不使用** `/etc/letsencrypt/live` 软链直接给 nginx：该机上 `/etc/letsencrypt/archive`
  偶发 ENOENT（`ls` 正常但 `open` 失败），nginx `-t` 会直接报
  `cannot load certificate`。故复制成实体文件：
  - `/www/server/nginx/conf/ssl/zvpapi.fullchain.pem`
  - `/www/server/nginx/conf/ssl/zvpapi.privkey.pem`
- 续期同步脚本：`/etc/letsencrypt/renewal-hooks/deploy/copy-zvpapi-cert.sh`
  （先写临时文件再 rename，避免 `cat` 失败把目标截空）

## 后端侧改造（`/etc/nginx/sites-available/zvp`）

### 1. index.html 资源改写

```nginx
location = /index.html {
    gzip_static off;
    sub_filter_once off;
    sub_filter 'src="/assets/' 'src="https://zvpapi.xiaobopisces.top/assets/';
    sub_filter 'href="/assets/' 'href="https://zvpapi.xiaobopisces.top/assets/';
    try_files $uri =404;
}
```

`sub_filter` 只作用于 `text/html`，SPA 深层路由（`/user/login` 等）经
`try_files` 回落到 `/index.html` 时同样命中（已实测）。

### 2. SW precache 清单改写

```nginx
location = /sw.js {
    gzip_static off;
    sub_filter_once off;
    sub_filter_types application/javascript;
    sub_filter 'url:"assets/' 'url:"https://zvpapi.xiaobopisces.top/assets/';
    try_files $uri =404;
}
```

把 workbox precache 的 71 个 `assets/*` 条目也指向前端，避免后端再被拉一遍 3MB。
`index.html` / `manifest.webmanifest` / 图标条目保持相对路径（scope 相关，不能跨域）。

### 3. CSP 放行

后端 `zvpapi` vhost 里的 `$zvms_csp` map（`map $http_xuehai_id`）需在
`script-src` / `style-src` / `font-src` 加上 `https://zvpapi.xiaobopisces.top`：

```
script-src 'self' https://zvpapi.xiaobopisces.top;
style-src  'self' 'unsafe-inline' https://zvpapi.xiaobopisces.top;
font-src   'self' data: https://zvpapi.xiaobopisces.top;
```

`connect-src` 本来就含 zvpapi（含 `wss://`），无需改。

## 验收

| 项 | 结果 |
|---|---|
| nginx -t | 后端、前端均 `syntax is ok` |
| `zvpapi` TLS | SAN = `zvpapi.xiaobopisces.top`，`/api/health` 200 |
| 后端 `index.html` | 首屏 `src`/`href` 全部指向 `https://zvpapi.xiaobopisces.top/assets/...` |
| 前端 `/assets/` CORS | `access-control-allow-origin: https://zvp.xiaobopisces.top` |
| 两端产物一致 | `assets/` 343 文件、`index-Cv7_FQOa.js` md5 相同 |
| SW | `active=true`，precache 77 条，其中 71 条来自 zvpapi，cross-origin 命中 `type=cors status=200` |
| 页面渲染 | 登录页表单正常，浅色主题，无错位/样式缺失 |
| 后端残余流量 | 仅 `index.html` / `sw.js` / `pwa-192x192.png` / `favicon.ico` / API，首屏大文件全部走前端 |

## 回滚

1. 后端 `zvp` vhost 去掉两处 `location = /index.html` 与 `location = /sw.js`
   的 `sub_filter`（或直接恢复备份），`nginx -s reload`；
2. 后端 `zvpapi` vhost 的 CSP map 去掉 zvpapi 域（可选，不加也能跑）；
3. DNS 把 `zvpapi` A 记录改回 `<BACKEND_IP>`（如需 API 也回后端）。

备份：

- 后端 `/etc/nginx/sites-available/zvp.bak-coldstart-20261111-182754`
- 后端 `/etc/nginx/sites-available/zvpapi.bak-coldstart-20261111-182754`
- 前端 `/www/server/panel/vhost/nginx/zvpapi.xiaoboisces.top.conf`（首次建立，无旧版）

## 注意事项

- **前端上的 `frontend-dist` 必须与后端保持同步**。当前两边同 md5，但后续发版
  需同时更新两台，否则首屏资源版本不一致会白屏。
- 后端仍持有 `/assets/` 完整副本（前端缺资源时回落），但正常路径不再命中。
- 前端根分区 e2fsck 报 bitmap 错误（`errors=remount-ro` 风险），只读检查未做任何修复；
  后续有空建议在维护窗口跑一次 `e2fsck -f`。
