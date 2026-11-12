# CORS 预检延迟：调查与修复（2026-11-07）

> 起因：测试员反馈「Preflight 的时候会因为拿学海 ID 的 API 发生 HTTP CORS 预检造成延迟」。
> 本文记录实测到的浏览器行为、线上证据，以及本轮落地的两处修复。

## 一、结论

「拿学海 ID 的 API」= `GET /api/v3/users/me/device`（前端 `readMyDevice()`，返回 `xuehai_id`
与设备封禁状态）。它**不是病根**，只是启动关键路径上第一个撞上预检的接口。

真正的成因是三件事叠加：

1. **跨域结构**：跨域请求必然预检。生产页面 `https://zvp.xiaobopisces.top` 与主 API 基址
   `https://zvp.xiaobopisces.top/api` **同源**，主路零预检；预检只发生在
   ①竞速备腿 `https://zvpapi.xiaobopisces.top/api`（跨域）②预览站 canary/yuren（页面在
   Cloudflare、API 在前端，全部跨域）。
2. **预检缓存键太细**：键里含**完整 URL（含 query）**与**请求头集合**。换 query 就是一次新
   预检；请求头集合**变大**（多一个头）也是一次新预检。
3. **前端并发重复请求没有合并**：同一秒内同一个 URL 被请求 4~5 次，于是被打 4~5 次预检。

## 二、浏览器预检缓存的实测语义

用真实 Chromium（`chrome-headless-shell`）+ 一个复刻线上响应头的本地 mock 服务器测得
（线上响应头见 `deployment/nginx/zvms-api-preflight.conf`：`204` +
`Access-Control-Allow-Origin` 回显 + `Access-Control-Max-Age: 7200`）：

| 场景 | 新增预检次数 |
| --- | --- |
| 同 URL 同请求头，连发 3 次 | **1**（`Max-Age` 生效） |
| 只改 query string | **每次 1 次**（翻页/搜索各算一个 URL） |
| 请求头集合**变大**（加 `Xuehai-Id` / `X-Device-Unlock`） | **每次 1 次** |
| 请求头集合**变小**（去掉一个头） | 0（子集复用上一条缓存） |
| 冷缓存下并发同 URL ×4 | **4**（浏览器不做在途合并） |
| 已有缓存后并发同 URL ×4 | 0 |

结论：Chrome 的预检缓存条目记录的是「上次允许的请求头集合」，新的请求头集合若是它的**子集**
就复用，**超集就重新预检**。因此「中途多出一个请求头」会让已知的每个 URL 集体重新预检。

## 三、线上证据

后端（`zvpapi`）nginx 访问日志里真浏览器的 OPTIONS（Referer 为空是因为站点响应带
`referrer-policy: no-referrer`）：

- 54 个不同 URL；`/api/v3/users/me/device` 7 次；`/api/system/maintenance` 68 次。
- 同一个 `/api/ai/conversations/{id}`：`OPTIONS` ×5 + `GET` ×4。

前端（`zvp`）访问日志：

- 564 条真浏览器预检，**全部**来自 canary / yuren 预览域；生产域 0 条（主路同源）。
- 近 5000 条请求里 233 条 OPTIONS，去重 66 个 URL，93 条带 query。
- OPTIONS → GET 的实测间隔 0~1 秒（秒级精度），即预检那一轮往返直接压在真实请求前面。

## 四、本轮落地的修复

### 1. 在途合并（single-flight）——`src/plugins/singleFlight.ts` + `src/plugins/axios.ts`

同一时刻、同一身份、同一 URL 的 GET 合并成一次网络往返：预检次数与真实请求次数一起降为 1。

刻意收窄的边界（避免改变既有语义）：

- 只合并**在途**请求，不做任何结果缓存——响应新鲜度仍由发起方决定；
- 只合并 GET；写请求不可共享；
- 调用方自带 `signal` / 进度回调 / 非 JSON·文本响应一律不参与；
- 跟随者拿到深拷贝，不会与发起者互相污染；
- 跟随者不重复弹窗、不重复跳登录、不重复重试——这些由发起者那一次请求统一处理；
- 重试沿用同一个航班（否则重试会「加入」自己发起的航班并永远等下去）。

### 2. 请求头集合恒定——`src/plugins/axios.ts` + `src/api/platformGuard.ts`

平板解锁凭证 `X-Device-Unlock` 原先「解锁后才出现」，属于**请求头集合变大**，
会让已知的每个 URL 在解锁那一刻集体重新预检。现在**没有票据时也发一个空值头**：

```ts
if (xuehaiId > 0 && !config.headers['X-Device-Unlock']) {
  config.headers['X-Device-Unlock'] = cachedUnlockToken()
}
```

空值与不带等价：后端 `(request.headers.get("X-Device-Unlock") or "").strip()` 取到空串，
与请求头缺失是同一条分支（`routers/attachments_router.py:130`）。

请求头集合因此在一次会话内保持恒定（除登录时新增 `Authorization` 这一次性变化）。

## 五、未做 / 待定

- **预览站同源化**（未做）：在 canary Worker 里把 `/api/*` 反代到
  `https://zvpapi.xiaobopisces.top/api/*`，构建基址改成相对路径 `/api`，预览站即可零预检。
  `deployment/preview-worker/README.md` 里「Worker 无法反代 `/api`」的结论只对
  `zvp.xiaoboisces.top` 成立（该域名 Cloudflare 返回 `1016 Origin DNS error`）；
  实测 Worker → `zvpapi.xiaoboisces.top` 返回 **200、无 `Cdn-Loop`**。
  需要处理：`Set-Cookie` 去 Domain、WS(`/api/system/ssh/ws`) 用 `WebSocketPair` 转发、SSE 直通。
- **登录态改走同源 Cookie**（未做，收益大但面大）：跨域请求即可退化为简单请求，浏览器彻底
  不发预检。代价是 CSRF 防护、登录态存储与所有鉴权读点的改造。
- **登录瞬间的一次性重预检**（接受）：`Authorization` 头在登录后才出现，属于集合变大，
  必然带来一次重预检。要消除只能让未登录请求也发一个空 `Authorization`，那会改变后端
  看到的请求形态与状态码语义（403/401），越过契约红线，不做。

## 六、契约边界

本轮改动全部在前端，**未触碰后端**：路由、方法、状态码、响应体、错误文案、响应头一律不变。
`X-Device-Unlock` 的空值语义与「不带」在现有后端实现下完全等价。

## 七、发布记录（2026-11-07）

| 项 | 值 |
| --- | --- |
| 提交 | `e942d0f perf(frontend): 合并在途相同 GET，并让请求头集合保持恒定` |
| 远端 | `origin/main` = `e942d0f` |
| 目标 | 前端 `zvp-frontend` `/www/zvms4/frontend-dist`（nginx `root`，`gzip_static on`） |
| 备份 | `/www/zvms4/frontend-dist.bak-20261107-133820`（22M） |
| 产物 | `index-Cj24z6Rz.js` / `index-ZSk6j9bi.css` / `index-legacy-DBGALj1D.js`，178 个文件 + 160 个 `.gz` |
| 构建 | `VITE_API_BASE=https://zvp.xiaobopisces.top/api` + `VITE_API_RACE_BASE=https://zvpapi.xiaobopisces.top/api` |
| 未动 | 后端（`zvms4` 未重启）、canary 预览站、nginx 配置 |

验收：首页 / `/user/login` `/activities` `/ai` `/manage` `/about` 全 200；`/api/health`
`/api/version` `/api/changelog` `/api/` 全 200；`gzip_static` 生效（js 171KB / css 26.7KB，
均带 `content-encoding: gzip`）；旧产物 `index-Ch_yOuv0.js` 已随 `--delete` 移除（404）；
前端近 500 条访问日志 0 条 5xx；`zvms4` 服务 active、近 10 分钟 0 条 Traceback。

回滚：

```bash
rsync -a /www/zvms4/frontend-dist.bak-20261107-133820/ /www/zvms4/frontend-dist/
find /www/zvms4/frontend-dist -type f -exec chown www:www {} +
```

不涉及数据库变更，无需回滚数据。

## 八、canary 预览站同步（2026-11-07 夜）

| 项 | 值 |
| --- | --- |
| 合并 | `e691be6 Merge remote-tracking branch 'origin/main' into v5-canary`（带上 `origin/v5-canary` 的 `68937ec`） |
| 冲突 | 仅 `src/plugins/axios.ts`：canary 已移除 `nprogress`，main 新增航班收尾。保留 `releaseRequest` + `__flightHandle.settle/fail`，丢弃 `nprogress.done()` |
| 校验 | `vitest` 40 用例（axios/singleFlight）通过；`vue-tsc` 0 error；`eslint` 干净 |
| 构建 | 直接 `VITE_API_BASE=https://zvp.xiaobopisces.top/api` + `VITE_API_RACE_BASE=https://zvpapi.xiaobopisces.top/api` `npx vite build`（省掉「先按预览域构建再 sed」那一步，顺带让 `index.html` 的 preconnect 指向真正的 API 域） |
| 产物 | `index-B1BuCZuE.js` 456KB（gzip 174KB）/ `index-BxN-jjj6.css` 494KB / `index-legacy-C_EBKhKP.js`；`sw.js` 与 workbox 已删 |
| 部署 | `npx wrangler@4 deploy`，`zvp-yuren` Version ID `bf94e33c-33d9-4448-a64e-d1016e0eb685`，88 个新文件 / 92 个复用 |

验收：`https://canary.xiaobopisces.top/` 200，`index.html` 引用新 bundle；新旧 bundle
均 200 但旧文件名返回 `text/html`（SPA fallback，非真实旧产物）、`/sw.js` 同样回落
`text/html`，不会注册 Service Worker；headless Chromium 渲染出登录页（`#app[data-v-app]`），
无白屏。跨域预检：`OPTIONS` + `access-control-request-headers:
authorization,xuehai-id,x-device-unlock,content-type` 回显 `access-control-allow-origin:
https://canary.xiaobopisces.top`、`max-age 7200`。

回滚：`npx wrangler@4 rollback <version-id>`（上一版见 Cloudflare Workers 版本列表），
或在旧提交上重新构建后 deploy。

## 九、线上“CORS 全失败”事故复核（2026-11-07 22:10）

随后线上反馈「API 全部失败」，复核发现 CORS 服务端配置并未失效，而是发布的
`index-Cj24z6Rz.js` 把两个 API 域名都写成了不存在的 `*.xiaoboisces.top`（少了
`p`）。该域名 DNS 为 NXDOMAIN，浏览器在真正发出请求前即失败；页面和主 JS 里的
preconnect 也指向了同一错误域名。服务端预检与普通响应仍能正确回显
`Origin: https://zvp.xiaobopisces.top`。

处置：

- 使用正确基址重新构建：`VITE_API_BASE=https://zvp.xiaobopisces.top/api`、
  `VITE_API_RACE_BASE=https://zvpapi.xiaobopisces.top/api`；
- 新产物 `index-cEdSHq54.js` 校验后仅含上述两个正确基址，全量 grep
  `xiaoboisces` / `xiaobolisces` 均为 0；
- 前端 `/www/zvms4/frontend-dist` 原子切换，原目录备份到
  `/www/zvms4/frontend-dist.bak-corsfix-20261107-220742`；
- 新目录共 178 个文件 + 160 个 `.gz`，属主 `www:www`，`nginx -t` 后重载。

线上验收：

- `/api/health` 200，`Access-Control-Allow-Origin: https://zvp.xiaobopisces.top`；
- `zvpapi` 预检 204，Allow-Origin / Credentials / Headers / Max-Age 齐备；
- 首页引用 `index-cEdSHq54.js`，线上取包只含两个正确 API 基址。

自动化防回归：发布工作流从 Environment 读取两个生产基址，先检查格式并探活各自
`/api/health`，随后验证基址已注入 `dist/assets`、对应 origin 已进入
`index.html` 的 preconnect。工作流不保存具体生产域名；生产部署也已改为仅由
非预发布 GitHub Release 触发，普通 `main` 推送不再上线。

回滚：

```bash
mv /www/zvms4/frontend-dist /www/zvms4/frontend-dist.failed-corsfix-20261107-220742
mv /www/zvms4/frontend-dist.bak-corsfix-20261107-220742 /www/zvms4/frontend-dist
nginx -s reload
```
