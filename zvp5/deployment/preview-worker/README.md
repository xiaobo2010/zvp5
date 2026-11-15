# 前端分支预览（Cloudflare Worker）

把任意前端分支的 `dist/` 丢到 Cloudflare Worker 上，用 `canary.xiaobopisces.top`
这种临时域名给人看效果。**只发静态文件，不反代 API。**

## 为什么不在 Worker 里反代 `/api`

试过：Worker 里 `fetch('https://zvpapi.xiaobopisces.top/api/...')` 会吃到
Cloudflare 同区回环——响应带 `Cdn-Loop: cloudflare; loops=1`，状态 301 指回自己，
换 `zpv`、前端、甚至直接打 IP 都一样。同区灰色 DNS 主机从 Worker 出站无法落回源站，
所以 Workers 侧做不了同源反代。

改用的办法：**构建产物里把 API 基址指到已在后端 CORS 白名单里的正式域名**
（`https://zvp.xiaobopisces.top/api`），浏览器直连该域名跨域调用。

> **2026-11-07 复核（未落地，仅更正结论）**：上面那条「Worker 侧做不了同源反代」只对
> `zvp.xiaobopisces.top` 成立——那个域名从 Worker 出站会拿到 Cloudflare
> `1016 Origin DNS error`。用一个一次性 Worker 实测三个目标：
>
> | 目标 | 结果 |
> | --- | --- |
> | `https://zvpapi.xiaobopisces.top/api/version` | **200**，无 `Cdn-Loop` |
> | `https://zvp.xiaobopisces.top/api/version` | 530 / `1016` |
> | `https://<FRONTEND_IP>/api/version` | 403 / `1003` |
>
> 也就是说**反代到 `zvpapi` 是可行的**：预览站若改成 `/api` 同源相对路径，
> CORS 预检会彻底消失（预检是跨域才有的，同源请求浏览器根本不发 OPTIONS）。
> 落地前需要处理三件事：`Set-Cookie` 去掉 Domain 再下发、WS(`/api/system/ssh/ws`) 用
> `WebSocketPair` 转发、SSE 直通不缓冲。
> 背景与实测见 `docs/CORS_PREFLIGHT_2026-11-07.md`。

## 自动化（2026-11-14 起）

`push` 到 `v5-canary`（或手动 `workflow_dispatch` 该分支）会走
`.github/workflows/canary.yml`：五道质量门禁（后端 pytest、前端单测/lint/类型/
构建、Cypress、workflow+密钥扫描、依赖漏洞扫描）全绿后，`deploy-canary` 把
**前端静态产物**发到 Worker `zvp-yuren`（`canary.xiaobopisces.top`）。
这条流水线不部署后端、不 SSH 任何主机、不碰数据库。

必需仓库 Secret：

| 名称 | 用途 |
| --- | --- |
| `CLOUDFLARE_API_TOKEN` | Workers Scripts:Edit + Workers Routes:Edit |

必需环境 Variable：`CLOUDFLARE_ACCOUNT_ID`（canary environment 里配置，不进仓库）。

可选环境 Variables（不配则用下面括号里的默认值）：`CANARY_API_BASE`
（`https://zvp.xiaobopisces.top/api`）、`CANARY_API_RACE_BASE`
（`https://zvpapi.xiaobopisces.top/api`）、`CANARY_API_CONTENT_BASE`
（`https://zvp.xiaobopisces.top/api`）。

CI 里的构建参数与手工版一致，另有两条是预览站专属的：

```text
VITE_STATIC_BASE=/                              # 静态资源由 Worker 自己托管
VITE_PAGE_BASE=https://canary.xiaobopisces.top/  # PWA start_url/scope 锚在预览域
```

发布后自动冒烟：比对线上入口 chunk 与本地产物的 sha256、确认 `/sw.js` 回落到
`text/html`（旧 service worker 不能被当成 JS 下发）、确认后端 CORS 回显预览域。

## 步骤（手工应急）

分支预览在仓库内的 git worktree 里构建（分支名已由 `愚人节ui` 改名 `phigros` 后又改名 `v5-canary`）：
`worktrees/v5-canary/zvp5/zvp5-frontend`。`wrangler.jsonc` 的
`assets.directory` 必须指向**该分支的** `dist`，默认写的是 main 的产物，直接
deploy 会把正式产物当预览发出去。

```bash
# 1. 构建（注意不要用 npm run build，见下方坑 1）
#    直接按正式 API 域构建，省掉「先按预览域构建再 sed」那一步：
#    index.html 的 preconnect 与各类内联脚本因此都指向真域名，不必二次改写。
cd worktrees/v5-canary/zvp5/zvp5-frontend
VITE_API_BASE=https://zvp.xiaobopisces.top/api \
VITE_API_RACE_BASE=https://zvpapi.xiaobopisces.top/api \
VITE_API_CONTENT_BASE=https://zvp.xiaobopisces.top/api \
npx vite build

# 2. 预览域只发静态资产：`VITE_API_CONTENT_BASE` 必须给，否则 /system/files/*
#    会打到页面源的 /api，被 SPA fallback 接走（200 + index.html），
#    文件管理页面会抛 TypeError，再用兜底文案误报成「动态码无效或已过期」。

# 3. 去掉 service worker（workbox 预缓存哈希与改写后的文件对不上）
rm -f dist/sw.js dist/sw.js.map dist/workbox-*.js dist/workbox-*.js.map

# 4. 部署
cd ../deployment/preview-worker
CLOUDFLARE_API_TOKEN=$(cat ~/.config/opencode/credentials/cf-api-token) \
CLOUDFLARE_ACCOUNT_ID=$(cat ~/.config/opencode/credentials/cf-account-id) \
npx wrangler@4 deploy

# 5. 后端 CORS 放行预览域（zvp-backend，改完 daemon-reload + restart）
#    /etc/systemd/system/zvp5.service.d/cors.conf（改名过渡期仍是
#    zvms4.service.d/cors.conf，两者内容一致即可）
#    Environment="ZVP5_CORS_ALLOWED_ORIGINS=https://zvp.xiaobopisces.top,...,https://canary.xiaobopisces.top"
#    （旧名 ZVMS_CORS_ALLOWED_ORIGINS 由 util/env.py 回落，改哪台生效哪台）
```

拆环境：`npx wrangler delete zvp-yuren`，并把 `cors.conf` 里的预览域去掉重启。

## 坑 1：`npm run build` 会删掉 `user-v2.css`

`src/assets/styles/yg/` 是 `scripts/sync-design-css.mjs` 的**生成目录**（源是仓库根
`前端约定/css/`），`prebuild` 每次都会重建它并删除多余文件。若分支把样式写在
生成目录里、却没同步源目录，`npm run build` 必然报
`Could not resolve './assets/styles/yg/user-v2.css'`。

绕过：直接用 `npx vite build`（不吃 `prebuild`），或先把样式文件补进
`前端约定/css/`。

`v5-canary` 分支已修：主题样式（`user-v2` / `phi-theme` / `phi-portals`）落在
`前端约定/css/` 源目录，生成目录只是副本，`npm run build` 可直接用。
注意另一端也可能只改生成目录——发版前用
`diff <(tail -n +8 src/assets/styles/yg/X.css) 前端约定/css/X.css` 核对两边一致。

同一个根因还会让 `scripts/vite-plugin-externalize-inline-scripts.ts` 在
`closeBundle` 报 `ENOENT dist/index.html`——真实错误是前面的模块解析失败，
这个插件只是把错误盖住，看日志时别被它带偏。

## 验证

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://canary.xiaobopisces.top/            # 200
curl -s -D- -o /dev/null -X OPTIONS \
  -H 'Origin: https://canary.xiaobopisces.top' \
  -H 'Access-Control-Request-Method: POST' \
  -H 'Access-Control-Request-Headers: authorization,content-type' \
  https://zvp.xiaobopisces.top/api/users/login | grep -i access-control-allow-origin
# 应回显 https://canary.xiaobopisces.top
```
