# Art 分支预览（Cloudflare Worker）

Art 分支的前端静态产物（来自 `zvp5/zvp5-frontend-pro`）发到 Worker
`zvp-yuren-canary-art`，预览域 `canary-art.xiaobopisces.top`。
**只发静态文件，不反代 API。**

## 自动化

`.github/workflows/art-preview.yml`：`push` 到 `Art` 分支（或手动
`workflow_dispatch` 该分支）时，仓库 API 触发该 workflow——五道质量门禁
（后端 pytest/Ruff、前端 Cypress E2E、workflow+密钥扫描、依赖漏洞扫描、
前端构建）全绿后，把产物发到 Worker 并冒烟。

预览站跨域直连正式 API `https://zvp.xiaobopisces.top/api`，浏览器侧的
Origin 必须在后端 CORS 白名单里，否则页面能开但所有 API 会被 400 拒掉。

## 审批闸

两个 environment 都只接受 `Art` 分支的 job：

| environment | 变量 | secret | 谁走 |
| --- | --- | --- | --- |
| `art-auto` | `CLOUDFLARE_ACCOUNT_ID` | `CLOUDFLARE_API_TOKEN` | `albxy`（分支创建者）与 `xiaobo2010` → 免审核直发 |
| `art` | `CLOUDFLARE_ACCOUNT_ID` | **不挂令牌** | 其他推送 → 部署步骤必然失败，等仓库所有者手动放行 |

判定只看 **github.actor（推送者身份）**，不是提交里的 author：author 是提交
内容的一部分，任何人都能写别人的邮箱；actor 由 GitHub 认证，骗不了。

## Cloudflare 侧

- Worker：`zvp-yuren-canary-art`（`src/index.js` 强制 https + HSTS，HTML
  入口 no-store）
- 路由：`canary-art.xiaobopisces.top/*`（zone route，不是 custom domain——
  现有令牌没有建 custom domain 的权限）
- DNS：`CNAME canary-art → zvp-yuren-canary-art.nblilychen.workers.dev`
  （橙云）。**记录名只写相对名** `canary-art`，写全域名会被 Cloudflare 再拼
  一次 zone 变成 NXDOMAIN。

## 域名拼写护栏

`canary-art`（少个字母的 `canary`）历史上踩过坑：线上产物因此指向一个不存在的
域名，页面能开但所有 API 全挂。workflow 里内置了三处护栏：host 必须以
`xiaobopisces.top` 结尾、产物里必须出现绝对 API 基址、产物里不允许出现
`xiaobo[a-z]*.top` 之外的域名。
