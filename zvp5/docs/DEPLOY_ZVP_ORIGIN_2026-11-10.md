# Origin=zvp 部署与缓存刷新规范（2026-11-10）

## 最终口径

- 浏览器 Origin：`https://zvp.xiaobopisces.top`
- 主 API：`https://zvp.xiaobopisces.top/api`（与页面同源）
- 静态资源：同源 `/assets/*`
- PWA：同源 `/manifest.webmanifest`、`/sw.js`
- 备用 API 域名仅用于竞速或紧急回退，不是页面 Origin；其具体域名由部署环境变量提供。

生产构建变量：

```text
PROD_API_BASE=/api
PROD_API_RACE_BASE=https://<backup-api-domain>/api
PROD_FRONTEND_HEALTH_URL=https://zvp.xiaobopisces.top/
```

## DNS 切换提醒

代码部署完成后，DNS **不会自动切换**。需要人工把：

```text
zvp.xiaobopisces.top
```

解析到最终承载 SPA 与 API 的主机，并确认 A/AAAA、证书和 Nginx `server_name` 一致。

切换前：

1. 两台候选主机都需部署同一份 `frontend-dist`。
2. 新主机的 `/api/health`、首页、静态 bundle、WebSocket/SSE 必须先在测试域名通过。
3. DNS TTL 到期后同时检查 IPv4/IPv6、HTTP→HTTPS、首页和 `/api/health`。
4. 回滚只需把 DNS 解析恢复到原主机；代码和构建产物无需重新发布。

## 刷新缓存行为

前端仍有 30 秒进程内 GET 缓存，用于自动进入页面时减少重复请求，但：

- 用户点击刷新、分页、搜索、确认或其他按钮后，进入 5 秒 fresh-request 窗口；
- 窗口内的 GET 跳过进程内缓存和 single-flight，直接发起新的网络请求；
- 新响应覆盖旧缓存；
- 网络失败时仍可回落本地缓存并显示“本地缓存数据”提示；
- 写入操作成功后继续清空内存缓存。

该规则是全局行为，不依赖每个页面单独记得传 bypass 参数。

## 发布步骤

1. 执行前端单测、类型检查、生产构建。
2. 使用 `VITE_API_BASE=/api` 构建，确认 index 中资源保持相对路径。
3. 部署 dist 到当前 zvp 主站和 DNS 切换后的目标主机。
4. `nginx -t` 后 reload；后端无需因本次缓存修复重启。
5. 验证刷新按钮、分页、搜索、写后读取、登录和 PWA。
6. 人工修改 DNS 后再执行最终 zvp 验收。

## 验收

- zvp 首页和 SPA 深层路由返回 200。
- 主 API 请求为同源请求，不产生 OPTIONS。
- 连续点击刷新按钮时，第二次请求进入 Network 面板而不是直接返回旧缓存。
- 写操作后列表立即体现新数据。
- PWA scope/start_url 为 `/`，SW 注册在 `https://zvp.xiaobopisces.top/sw.js`。
- DNS 未切换前的备用实例页面不作为最终 Origin 证据。


## 本次上线记录（2026-11-10 00:20 CST）

| 项 | 值 |
|---|---|
| 当前 zvp 解析 | `<FRONTEND_IP>`（前端，尚未修改 DNS） |
| DNS 切换目标 | `<BACKEND_IP>`（后端） |
| 最终主 bundle | `index-Cv7_FQOa.js` |
| 主 API 构建基址 | `/api`（同源） |
| 竞速/备用 API | 通过部署环境变量提供，不作为页面 Origin |
| 前端 静态目录 | `/www/zvms4/frontend-dist` |
| 后端 静态目录 | `/www/zvms4/frontend-dist` |

两个主机的 `frontend-dist` 已部署同一份最终产物。当前访问：

- `https://zvp.xiaobopisces.top`：前端 实例，页面和静态资源同源，主 API 经同源 Nginx 反代。
- 备用 API 实例只用于竞速/回退，不作为最终页面 Origin。

验收：

| 检查 | 结果 |
|---|---|
| zvp 首页与备用实例首页 | 200，均为 `index-Cv7_FQOa.js` |
| zvp 浏览器 Origin | `https://zvp.xiaobopisces.top` |
| 主 API 请求 | 同源 `/api`，无 CORS preflight |
| PWA Service Worker | scope `https://zvp.xiaobopisces.top/`，77 条 precache |
| 刷新按钮缓存 | 用户点击后 5 秒内绕过进程缓存并重新请求 |
| 单元测试 | 92 文件 / 1056 用例通过 |
| 类型检查 / ESLint | 通过 |

备份：

- 前端 原 vhost：`/www/server/panel/vhost/nginx/zvp.xiaobopisces.top.conf.bak-origin-split-20261109-233250`
- 前端 上线前 dist：`/www/zvms4/frontend-dist.bak-origin-split-20261109-233250`
- 后端 原 API vhost：部署前同目录下的 `*.bak-origin-split-*` 备份
- 后端 上线前 dist：`/www/zvms4/frontend-dist.bak-zvp-origin-20261110`

> **待人工操作**：把 `zvp.xiaobopisces.top` 的 DNS 解析从 `<FRONTEND_IP>` 改为 `<BACKEND_IP>`。
> 修改后等待 TTL 生效，再重新验证首页、`/api/health`、WebSocket/SSE 和 PWA。
