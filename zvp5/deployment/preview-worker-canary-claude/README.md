# canary-anthropic 预览（Cloudflare Worker）

`canary-anthropic` 分支的**前端预览**通道：把 `zvp5/zvp5-frontend-canary/dist/`
发到 Worker `zvp-yuren-canary-claude`，预览域 `canary-claude.xiaobopisces.top`。
**只发静态文件，不反代 API、不 SSH 主机、不碰数据库。**

与 `preview-worker/`（v5-canary 通道）的差别只有三处：Worker 名、预览域、
被部署的前端目录。Worker 逻辑（强制 https 跳转 + HSTS）完全一致，
`src/index.js` 是 `preview-worker/src/index.js` 的副本。

## 预览域是怎么接上的（zone route，不是 custom domain）

本通道的 Cloudflare 令牌**没有创建 Worker custom domain 的权限**
（`POST /accounts/{id}/workers/domains` 与
`POST /accounts/{id}/workers/scripts/{s}/domains` 一律返回 10405
Method not allowed for this authentication scheme；`PUT` 同路径虽然返回 200，
但不会真的建立绑定），所以这里走的是**zone route**：

| 层 | 配置 | 谁维护 |
| --- | --- | --- |
| Worker | `zvp-yuren-canary-claude` | CI（`wrangler deploy`） |
| 路由 | `canary-claude.xiaobopisces.top/*` → 该 Worker | CI（wrangler 按 `routes` 幂等维护） |
| DNS | `CNAME canary-claude → zvp-yuren-canary-claude.nblilychen.workers.dev`（橙云） | 手工，一次性 |

与 `hellomc.xiaobopisces.top` 的接法完全一样（CNAME 指向 workers.dev 主机名）。

**DNS 记录必须用相对名创建**（`canary-claude` 而不是完整域名）：传完整域名会被
Cloudflare 再拼一次 zone，变成 `canary-claude.xiaobopisces.top.xiaobopisces.top`，
解析直接 NXDOMAIN。

**不要用 `AAAA 100::`**：那是 custom domain 用的占位记录，走 zone route 时边缘
不认，IPv4/IPv6 握手都会失败（`SSL: SSLV3_ALERT_HANDSHAKE_FAILURE`）。

> 若想在 CF 控制台给这个 Worker 加 custom domain（更规范、边缘会单独签证书），
> 加完之后 DNS 那条 CNAME 可以删掉——但注意那条 CNAME 是 zone route 生效的前提，
> 删之前先确认 custom domain 已建好。

## 流水线

`.github/workflows/canary-anthropic.yml`：`push` 到 `canary-anthropic`
（或手动 `workflow_dispatch`）时触发。

五道质量门禁全绿才发布：后端 pytest/Ruff、前端 Cypress E2E、workflow+密钥扫描、
依赖漏洞扫描、前端单测/lint/类型/构建。

冒烟会解析预览域、比对线上入口 chunk 与本次构建产物的 sha256、再验一次后端
CORS 预检。runner 侧 DNS/TLS 不通时降级成 warning（部署本身已由 wrangler 确认
成功），但「200 却内容对不上」一律判失败。

## 环境与安全策略

| environment | 分支策略 | 令牌 | 谁走 |
| --- | --- | --- | --- |
| `canary-anthropic-auto` | 只接受 `canary-anthropic` 分支 | 挂 `CLOUDFLARE_API_TOKEN` | 分支创建者 `wzxundesigned` 与所有者账号 → 免审核直发 |
| `canary-anthropic` | 只接受 `canary-anthropic` 分支 | **不挂** | 其余推送 → 部署必然失败，等所有者 `workflow_dispatch` 放行 |

workflow 按 `github.actor`（GitHub 认证过的推送者，不是提交里可伪造的 author）
选 environment；push 与手动触发同规则——所有者手动重跑就是「人工放行」那条路。

> GitHub environment 的 required-reviewers 接口对本仓库不可用
> （`deployment-protection-rules` 恒 404），所以闸门落在「有没有部署凭据」上：
> 免审核通道挂令牌，人工放行通道刻意不挂。

两个 environment 都需要：

| 名称 | 类型 | 用途 |
| --- | --- | --- |
| `CLOUDFLARE_ACCOUNT_ID` | variable | Cloudflare 账号 ID（不进仓库） |
| `CLOUDFLARE_API_TOKEN` | variable | Workers Scripts:Edit + Workers Routes:Edit（只在 auto 通道） |

## 必需的前端构建参数

```text
VITE_API_BASE=https://zvp.xiaobopisces.top/api
```

`zvp5-frontend-canary` 默认把 API 基址回退到相对 `/api`；预览域下 `/api` 会被
SPA fallback 接走（200 + index.html），前端直接抛错。workflow 里已强制注入绝对
基址，并在构建后断言产物里确实带着这个地址。

## 后端 CORS

预览域必须在后端 CORS 白名单里，否则页面能开但所有 API 被 400 拒掉：

```text
ZVP5_CORS_ALLOWED_ORIGINS=...,https://canary-claude.xiaobopisces.top
```

改完在**后端机**（首尔 tencent-43）`systemctl restart zvp5.service` 生效；
两台 nginx 的 `zvp5-cors-map.conf` 也要同步加同一行。

## 验证

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://canary-claude.xiaobopisces.top/   # 200
curl -s -D- -o /dev/null -X OPTIONS \
  -H 'Origin: https://canary-claude.xiaobopisces.top' \
  -H 'Access-Control-Request-Method: POST' \
  -H 'Access-Control-Request-Headers: authorization,content-type' \
  https://zvp.xiaobopisces.top/api/users/login | grep -i access-control-allow-origin
# 应回显 https://canary-claude.xiaobopisces.top
```

> 本机/部分机房有 fake-IP 型 DNS 代理，会把预览域解析到假地址导致握手失败；
> 换一台干净的机器（或直接用 CI 的 runner）验证更可靠。
