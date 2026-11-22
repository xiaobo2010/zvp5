# 分支预览通道：通用模板

以后再开预览分支时，**不用再复制一份三百多行的 workflow**。本文件说明怎么用
`.github/workflows/preview-deploy.yml`（reusable workflow）开一条新通道。

已落地的实例：

| 分支 | wrapper | 预览域 | Worker |
| --- | --- | --- | --- |
| `canary-anthropic` | `.github/workflows/canary-anthropic.yml` | `canary-claude.xiaobopisces.top` | `zvp-yuren-canary-claude` |
| `v5-canary` / `v5-canary-pro` / `v5-canary2` | 老通道，暂未迁移 | `canary` / `canary-pro` / `canary2` | 各自独立 |

## 1. 新分支上放一个 wrapper

先把模板文件取过来（它在 main 上）：

```bash
git checkout main -- .github/workflows/preview-deploy.yml
```

然后写一个十几行的 wrapper：

```yaml
name: My branch preview

on:
  push:
    branches: [my-branch]
  workflow_dispatch:

jobs:
  preview:
    uses: ./.github/workflows/preview-deploy.yml
    with:
      slug: canary-mybranch              # 通道标识
      branch: my-branch                  # 允许部署的分支
      worker_name: zvp-yuren-canary-mybranch
      host: canary-mybranch.xiaobopisces.top
      frontend_dir: zvp5/zvp5-frontend   # 被发布的前端目录
      wrangler_config: zvp5/deployment/preview-worker-canary-mybranch/wrangler.jsonc
      environment_auto: canary-mybranch-auto
      environment_gated: canary-mybranch
      auto_actors: myaccount             # 逗号分隔，免审核账号
    secrets: inherit
```

参数说明：

| input | 含义 |
| --- | --- |
| `slug` | 通道标识，用于 environment 命名与摘要 |
| `branch` | 允许部署的分支；environment 的分支策略必须与之一致 |
| `worker_name` / `host` / `wrangler_config` | Cloudflare Worker 三件套 |
| `frontend_dir` | 被发布的前端目录（相对仓库根）；单测 / lint / 类型 / 构建都跑它 |
| `api_base` | 注入前端的 API 基址，默认 `https://zvp.xiaobopisces.top/api` |
| `e2e_dir` | 跑 Cypress E2E 的目录，默认主前端 `zvp5/zvp5-frontend` |
| `environment_auto` / `environment_gated` | 两个 environment 的名字，见下 |
| `auto_actors` | 逗号分隔的免审核账号（`github.actor`） |

## 2. 建两个 environment

用仓库 API（`gh api` 或 `curl`）建，两个都要设成**自定义分支策略**，且分支策略
只放这一个分支：

```bash
R=https://api.github.com/repos/xiaobo2010/zvp5
H=(-H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json")

curl -X PUT "${H[@]}" "$R/environments/canary-mybranch-auto" \
  -d '{"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}'
curl -X POST "${H[@]}" "$R/environments/canary-mybranch-auto/deployment-branch-policies" \
  -d '{"name":"my-branch","type":"branch"}'
```

两个 environment 的差别只在令牌：

| environment | 变量 | secret | 谁走 |
| --- | --- | --- | --- |
| `<slug>-auto` | `CLOUDFLARE_ACCOUNT_ID` | `CLOUDFLARE_API_TOKEN` | `auto_actors` 名单里的账号 → 免审核直发 |
| `<slug>` | `CLOUDFLARE_ACCOUNT_ID` | **不挂令牌** | 其他推送 → 部署步骤必然失败，等仓库所有者手动放行 |

> **为什么用「令牌可用性」当审批闸**：GitHub environment 的 required-reviewers
> 接口对本仓库不可用（`deployment-protection-rules` 一律 404），所以闸门落在
> 「有没有部署凭据」上。不在名单里的推送拿不到令牌，部署必然失败——效果等同
> 人工放行，而所有者 `workflow_dispatch` 重跑即可放行（手动触发与 push 同规则）。

写令牌时注意 PAT 权限：**仓库 secret 写不了就写 environment variable**，
workflow 里用 `${{ secrets.X || vars.X }}` 取。

## 3. Cloudflare 侧

Worker 目录（`src/index.js` + `wrangler.jsonc`）照抄
`zvp5/deployment/preview-worker-canary-claude/` 改名字与产物目录即可。Worker 逻辑
（强制 https 跳转 + HSTS）三份预览通道完全一样。

**两个坑**：

1. **custom domain 权限**：能建 Worker custom domain 的令牌才能让边缘给该主机名
   单独签证书。`POST /accounts/{id}/workers/domains` 返回 10405
   Method not allowed for this authentication scheme，说明令牌没有这个权限
   （`PUT` 同路径会返回 200，但不会真的建立绑定，别被它骗了）。这种情况只能走
   **zone route**（`POST /zones/{z}/workers/routes`，pattern
   `canary-mybranch.xiaobopisces.top/*`）。
2. **zone route 的 DNS 形态**：记 `CNAME canary-mybranch → <worker>.nblilychen.workers.dev`
   （橙云），跟 `hellomc.xiaobopisces.top` 一个路子。**不要用 `AAAA 100::`**——
   那是 custom domain 用的占位记录，走 zone route 时边缘不认，握手直接失败
   （`SSL: SSLV3_ALERT_HANDSHAKE_FAILURE`）。
3. **DNS 记录要用相对名**：创建记录时 name 传 `canary-mybranch`，不要传完整域名
   `canary-mybranch.xiaobopisces.top`——后者会被 Cloudflare 再拼一次 zone，变成
   `…xiaobopisces.top.xiaobopisces.top`，解析直接 NXDOMAIN。
4. 若令牌后来补上了 custom domain 权限，可以在 CF 控制台给 Worker 加 custom
   domain（更规范），加好之后 zone route 那条 CNAME 才能删。

## 4. 后端 CORS

预览域必须在后端 CORS 白名单里，否则页面能开但所有 API 被 400 拒掉：

- 首尔后端：`/etc/systemd/system/zvp5.service.d/cors.conf` 的
  `ZVP5_CORS_ALLOWED_ORIGINS` 追加该域，然后 `systemctl restart zvp5.service`
- 两台 nginx：`zvp5-cors-map.conf` 的 `map $http_origin $zvp5_cors_origin` 追加

改完按现有条目自检（别手打域名，手滑过一次）：

```bash
V=$(sudo grep -o 'ZVP5_CORS_ALLOWED_ORIGINS=[^"]*' /etc/systemd/system/zvp5.service.d/cors.conf | cut -d= -f2-)
IFS=,; for o in $V; do
  printf '%-45s ' "$o"
  curl -sS -o /dev/null -D- -X OPTIONS -H "Origin: $o" \
    -H 'Access-Control-Request-Method: POST' \
    -H 'Access-Control-Request-Headers: authorization,content-type' \
    -A 'Mozilla/5.0' http://127.0.0.1:8018/api/users/login | grep -i access-control-allow-origin
done
```

## 5. 门禁内容

`preview-deploy.yml` 里五道门禁，与 canary 通道同构：

1. 后端：Ruff（F821 / F401）+ pytest + compileall
2. 前端 Cypress E2E（`e2e_dir`，默认主前端）
3. workflow + 密钥扫描（actionlint / gitleaks）
4. 依赖漏洞（`pnpm audit --prod` + trivy）
5. 发布 job：vitest → eslint → `vue-tsc && vite build` → 断言产物 → wrangler deploy → 冒烟

任何一项红都不会发布。
