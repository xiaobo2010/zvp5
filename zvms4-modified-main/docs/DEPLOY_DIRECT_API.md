# 浏览器直连 API（2026-11-01）

前端仍由 `zvp-frontend`分发，但**浏览器不再经内容站反代**，直接请求 API 域名
`zvpapi.xiaobopisces.top`（后端）。这是 DEPLOY_API_SPLIT.md 那套「同源反代」的升级版。

## 为什么要改

反代架构下，国内用户的每个 API 请求路径是：

```
国内浏览器 → 前端(内容站) → 后端(API) → 数据库(ADB)
```

多背一个跨太平洋往返。globalping 国内探针实测（2026-11-01）：

| 路径 | 广州 | 成都 | 北京 | 武汉 |
|---|---|---|---|---|
| 直连 API（后端） | 374ms | 435ms | 449ms | 2377ms(DNS 慢) |
| 经前端反代 | 794ms | **超时失败** | 651ms | 1019ms |

TCP 建连：直连 59ms vs 反代 163ms —— 即国内到前端 RTT ≈160ms，国内到后端 ≈55ms。
反代让每个 API 请求多付 160ms 往返 + 一次完整 TLS 握手；成都探针在反代路径上直接超时。

## 现状拓扑

```
国内浏览器
 ├─ HTML/JS/CSS ──▶ zvp.xiaobopisces.top (<FRONTEND_IP> 前端) nginx → dist
 └─ /api/*, WS, SSE ──▶ zvpapi.xiaobopisces.top (<BACKEND_IP> 后端) nginx → uvicorn:8018
                                          └─▶ Oracle ADB（区域见部署配置）
```

- DNS 不变：`zvp` 与 `zvpapi` 均为灰云 A 记录。
- 前端侧的 `/api/` 反代**保留不动**，作为回滚兜底（浏览器已不再走它）。

## 三处必改

### 1. 前端构建期注入绝对 API 基址

`src/plugins/axios.ts` 已支持 `VITE_API_BASE`（缺省 `/api`）。构建时注入：

```bash
cd zvms4-modified-main/zvms4-frontend
VITE_API_BASE=https://zvpapi.xiaobopisces.top/api npx vite build
rsync -az --delete dist/ zvp-frontend:/www/zvms4/frontend-dist/
```

⚠️ **不要把生产域名提交进仓库**：`fbc90bb` 特意删掉了 `.env.production`。
域名只在构建命令里给，仓库保持无生产域名状态。

### 2. WS URL 拼接必须支持绝对基址

`src/api/system/ssh.ts` 的 `buildSshWsUrl` 原本写死 `window.location.host`，
绝对基址下会拼出 `wss://内容站域名 + https://api...` 的畸形 URL。本次已改为
按基址形态分流（相对→用页面 origin，绝对→取绝对地址自身的 origin）。
回归测试见同目录 `ssh.spec.ts`（6 例）。

### 3. 内容站 CSP 放行 API 源

前端 vhost 的 `map $http_xuehai_id $zvms_csp` 两档都要加：

```
connect-src 'self' https://zvpapi.xiaobopisces.top wss://zvpapi.xiaobopisces.top
```

两档都要改：带 `Xuehai-Id`（学海设备）那档是收紧档，漏改会让该场景下的
API 调用被浏览器直接拦掉。

## 顺手修掉的一个现网 bug：WS 握手 404

后端 nginx 的 `/api/` 段没有转发 `Upgrade` 头，WebSocket 升级请求到 uvicorn
被当成普通 HTTP GET，`/api/system/ssh/ws` 只有 websocket 路由，于是返回
FastAPI 的 404 `{"detail":"Not Found"}`。**浏览器直连后端前，这个洞就已经存在**：
前端侧转发了 Upgrade，但后端这层又丢了。

已在后端 `/etc/nginx/sites-available/zvpapi` 补一段（与前端侧同形）：

```nginx
location /api/system/ssh/ws {
    proxy_pass http://127.0.0.1:8018;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $zvpapi_connection_upgrade;
    ...
}
```

验证：握手返回 `101 Switching Protocols`（修复前 404）。

## 跨域可行性（已实测）

- **认证是 Bearer token 存 localStorage，后端不设 cookie** → 跨域没有 cookie 障碍。
- 后端 CORS 白名单已含 `https://zvp.xiaobopisces.top`（`ZVMS_CORS_ALLOWED_ORIGINS`），
  实测预检返回 `access-control-allow-origin: https://zvp.xiaobopisces.top` +
  `access-control-allow-credentials: true`，自定义头 `xuehai-id` 亦放行。
- WS 端点无 Origin 校验，认证靠一次性 ticket（查询参数，浏览器握手带不了
  Authorization 头），跨域直连安全。
- Service Worker 只做本地资源 precache，**无 runtime 缓存**，不拦截跨域 API。

## 验证记录（2026-11-01）

| 项 | 结果 |
|---|---|
| 基线等价性 | 用仓库源码重建（无 VITE_API_BASE）→ 与线上产物 174 文件哈希 **0 差异**，证明重建不夹带意外变更 |
| 源码改动 | 仅 `src/api/system/ssh.ts`（+18/-3） |
| 前端单测 | 87 文件 / **980 例全绿**（含新增 `ssh.spec.ts` 6 例） |
| typecheck | `vue-tsc --noEmit` 零错误 |
| 部署一致性 | 线上产物与本地产物 174 文件哈希完全一致 |
| CORS 预检 | `OPTIONS /api/v3/ban/violations` → 200，`allow-origin` + `allow-credentials` + `xuehai-id` 全通 |
| WS 握手 | 直连后端 / 经前端 均 `101`（修复前 404） |
| 站点健康 | 首页 200；直连 API `/api/system/maintenance` → `{"status":"ok",...}` |
| 真实流量 | 后端侧出现国内用户直连 `<CLIENT_IP> /api/system/maintenance`；前端侧 `/api` 命中停止新增 |

## 回滚

```bash
# 1. 还原前端产物
ssh zvp-frontend 'cd /www/zvms4 && rm -rf frontend-dist && \
  cp -a frontend-dist-pre-direct-api-20261101-224858 frontend-dist && \
  chown -R www:www frontend-dist'

# 2. （可选）还原 CSP：备份在 zvp-frontend
#    zvp.xiaobopisces.top.conf.bak-predirect-api-20261101-223312

# 3. （可选）还原后端 WS 段：备份在 zvp-backend
#    /etc/nginx/sites-available/zvpapi.bak-ws-20261101-223312
```

前端还原后浏览器回到同源 `/api`，自动改走前端反代，无需改 DNS。

## 遗留

- **静态资源仍在前端**：17M assets 从前端发给国内用户（~160ms RTT）。
  要彻底解决需把 dist 也搬到后端机并改 `zvp` 的 A 记录 —— 那是方案 A，本次未做。
- 前端那台 1c/1G 仍承担全部静态分发。
