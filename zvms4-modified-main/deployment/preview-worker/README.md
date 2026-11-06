# 前端分支预览（Cloudflare Worker）

把任意前端分支的 `dist/` 丢到 Cloudflare Worker 上，用 `canary.xiaobopisces.top`
这种临时域名给人看效果。**只发静态文件，不反代 API。**

## 为什么不在 Worker 里反代 `/api`

试过：Worker 里 `fetch('https://zvpapi.xiaobopisces.top/api/...')` 会吃到
Cloudflare 同区回环——响应带 `Cdn-Loop: cloudflare; loops=1`，状态 301 指回自己，
换 `zpv`、LA、甚至直接打 IP 都一样。同区灰色 DNS 主机从 Worker 出站无法落回源站，
所以 Workers 侧做不了同源反代。

改用的办法：**构建产物里把 API 基址指到已在后端 CORS 白名单里的正式域名**
（`https://zvp.xiaobopisces.top/api`），浏览器直连该域名跨域调用。

## 步骤

分支预览在仓库内的 git worktree 里构建（分支名已由 `愚人节ui` 改名 `phigros` 后又改名 `v5-canary`）：
`worktrees/v5-canary/zvms4-modified-main/zvms4-frontend`。`wrangler.jsonc` 的
`assets.directory` 必须指向**该分支的** `dist`，默认写的是 main 的产物，直接
deploy 会把正式产物当预览发出去。

```bash
# 1. 构建（注意不要用 npm run build，见下方坑 1）
cd worktrees/v5-canary/zvms4-modified-main/zvms4-frontend
VITE_API_BASE=https://canary.xiaobopisces.top/api npx vite build

# 2. 把产物里的 API 基址换成 CORS 白名单里的正式域名
sed -i 's|https://canary\.xiaobopisces\.top/api|https://zvp.xiaobopisces.top/api|g' \
  dist/assets/index-*.js

# 3. 去掉 service worker（workbox 预缓存哈希与改写后的文件对不上）
rm -f dist/sw.js dist/sw.js.map dist/workbox-*.js dist/workbox-*.js.map

# 4. 部署
cd ../deployment/preview-worker
CLOUDFLARE_API_TOKEN=$(cat ~/.config/opencode/credentials/cf-api-token) \
CLOUDFLARE_ACCOUNT_ID=2196ac54edddac2e69ae181e8466a5a1 \
npx wrangler@4 deploy

# 5. 后端 CORS 放行预览域（tencent-43，改完 daemon-reload + restart）
#    /etc/systemd/system/zvms4.service.d/cors.conf
#    Environment="ZVMS_CORS_ALLOWED_ORIGINS=https://zvp.xiaobopisces.top,...,https://canary.xiaobopisces.top"
```

拆环境：`npx wrangler delete zvp-yuren`，并把 `cors.conf` 里的预览域去掉重启。

## 坑 1：`npm run build` 会删掉 `user-v2.css`

`src/assets/styles/yg/` 是 `scripts/sync-design-css.mjs` 的**生成目录**（源是仓库根
`前端约定/css/`），`prebuild` 每次都会重建它并删除多余文件。若分支把样式写在
生成目录里、却没同步源目录，`npm run build` 必然报
`Could not resolve './assets/styles/yg/user-v2.css'`。

绕过：直接用 `npx vite build`（不吃 `prebuild`），或先把样式文件补进
`前端约定/css/`。

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
