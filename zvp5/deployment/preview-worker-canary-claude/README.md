# canary-anthropic 预览（Cloudflare Worker）

`canary-anthropic` 分支的**前端预览**通道：把 `zvp5/zvp5-frontend-canary/dist/`
发到 Worker `zvp-yuren-canary-claude`，预览域 `canary-claude.xiaoboisces.top`。
**只发静态文件，不反代 API、不 SSH 主机、不碰数据库。**

与 `preview-worker/`（v5-canary 通道）的差别只有三处：Worker 名、预览域、
被部署的前端目录。Worker 逻辑（强制 https 跳转 + HSTS）完全一致，
`src/index.js` 是 `preview-worker/src/index.js` 的副本。

## 流水线

`.github/workflows/canary-anthropic.yml`：`push` 到 `canary-anthropic`
（或手动 `workflow_dispatch`）时触发。

五道质量门禁全绿才发布：后端 pytest/Ruff、前端 Cypress E2E、workflow+密钥扫描、
依赖漏洞扫描、前端单测/lint/类型/构建。

## 环境与安全策略

| environment | 分支策略 | 放行 |
| --- | --- | --- |
| `canary-anthropic` | 只接受 `canary-anthropic` 分支 | `required_reviewers`，人工放行 |
| `canary-anthropic-auto` | 只接受 `canary-anthropic` 分支 | 无闸 |

workflow 按 `github.actor`（GitHub 认证过的推送者，不是提交里可伪造的 author）
选 environment：本分支创建者 `wzxundesigned` 与仓库所有者账号推送时走
`canary-anthropic-auto`（免审核），其余推送一律走 `canary-anthropic`，
必须人工放行才能发布。

两个 environment 都需要：

| 名称 | 类型 | 用途 |
| --- | --- | --- |
| `CLOUDFLARE_API_TOKEN` | secret | Workers Scripts:Edit + Workers Routes:Edit |
| `CLOUDFLARE_ACCOUNT_ID` | variable | Cloudflare 账号 ID（不进仓库） |

## 必需的前端构建参数

```text
VITE_API_BASE=https://zvp.xiaoboisces.top/api
```

`zvp5-frontend-canary` 默认把 API 基址回退成相对 `/api`；预览域下 `/api` 会被
SPA fallback 接走（200 + index.html），前端直接抛错。workflow 里已强制注入绝对
基址，并在构建后断言产物里确实带着这个地址。

## 后端 CORS

预览域必须在后端 CORS 白名单里，否则页面能开但所有 API 被 400 拒掉：

```text
ZVP5_CORS_ALLOWED_ORIGINS=...,https://canary-claude.xiaobopisces.top
```

改完在**后端机**（首尔 tencent-43）`systemctl restart zvp5.service` 生效。

## 验证

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://canary-claude.xiaoboisces.top/   # 200
curl -s -D- -o /dev/null -X OPTIONS \
  -H 'Origin: https://canary-claude.xiaoboisces.top' \
  -H 'Access-Control-Request-Method: POST' \
  -H 'Access-Control-Request-Headers: authorization,content-type' \
  https://zvp.xiaoboisces.top/api/users/login | grep -i access-control-allow-origin
# 应回显 https://canary-claude.xiaoboisces.top
```
