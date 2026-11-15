# 2026-11-08 发布记录（/api/v1 干净切换 + V052 删列 + canary 重发）

发布范围：`main` = `ceb7c2d`（ZVP5 大修全套，见 [`OVERHAUL_2026-11-08_ZVP5.md`](./OVERHAUL_2026-11-08_ZVP5.md) 与 [`ADR_API_V1_REVERSIONING.md`](./ADR_API_V1_REVERSIONING.md)）；canary = `v5-canary` = `5d4898b`。

## 一、时序执行（锁步）

| 步 | 内容 | 结果 |
| --- | --- | --- |
| 1 | 备份 | `backend.bak-20261108-071409`（77M，排 `.venv`） |
| 2 | 后端 `git archive` 560 文件 → rsync `--delete`（排除 `.venv/ data/ export/ settings.py *.pem aes_key.txt ssh_key* ssh_known_hosts* configure_runtime.py CLAUDE.md TECH_REPORT.md settings.py.bak* logs/ *.log`） | 删除 254 项全为 `__pycache__` / `.pytest_cache`；服务器状态文件零误删 |
| 3 | 属主修正 + www-data 身份 import 冒烟 | 257 路由；`/api/v1` 77 路径；v3 残留 0；v2 仅剩 `/api/v2/mcp/*` 别名 |
| 4 | `systemctl restart zvms4` | 07:19:05 CST active，journal 零 traceback |
| 5 | 前端 + 后端 dist 原子 `mv` 对切（`.new` 预放 → 双 `mv`） | 两台各留 `frontend-dist.bak-20261108-switch` |
| 6 | V052（`misc/apply_ddl.py v052 --verify user_trash`） | USER_TRASH 6 列 → 5 列，`RESTORED_AT` 已删 |
| 7 | canary wrangler deploy（`zvp-yuren`） | Version `3f588706-8f43-45f0-88ca-0274fb9f99bf` |

### 虚惊记录：裸 curl 全 418

切换后首测用无 UA 的 curl 探测 `/api/health` 等，全部 418。排查为
`app_setup.py` 的 `shield_middleware` 按设计拒绝非 Mozilla UA（AI OpenAI
路径除外）并写审计日志——后端全程健康，虚惊一场。**教训：线上冒烟必须带
浏览器 UA**；journal 中 6 条 418 均为我方探测，非故障。

## 二、前端构建

- **生产**（main，前端与后端同树）：`VITE_API_BASE=https://zvp.xiaobopisces.top/api`
  + `VITE_API_RACE_BASE=https://zvpapi.xiaobopisces.top/api`（不设
  `VITE_API_CONTENT_BASE`，生产行为不变）。产物 `index-zr2uobXX.js`；
  modulepreload 仅 rolldown-runtime / element-plus / vue-vendor，**monaco
  零预载**（5.33MB → 1.94MB 优化落地）；PWA precache 77 条 2.8M；180 个
  `.gz` 本地补压后随 rsync 上传。
- **canary**（v5-canary）：三变量齐（含 `VITE_API_CONTENT_BASE`）。产物
  `index-ClEv9F7n.js`；`sw.js` / `workbox-*` 构建后删除（预览站不跑 PWA）。

## 三、验收

| 检查 | 结果 |
| --- | --- |
| 后端直连 `/api/health` `/api/version` `/api/changelog` | 200 |
| `/api/v1/activities`（前端链 + 竞速域 + canary Origin） | 401（路由存在，鉴权拦截） |
| `/api/v3/activities` | 404（干净切换坐实） |
| `/api/v2/mcp/activities` | 401（别名按 ADR 保留） |
| `POST /api/users/auth` 伪凭证 | 422 规范校验 JSON |
| socket.io polling（竞速域） | 200 + sid + websocket upgrade |
| canary 首页 / `sw.js` | 200 `index-ClEv9F7n.js`；`sw.js` 回落 `index.html`（0 workbox） |
| canary Origin 预检 `/api/v1/activities` | 204 + 回显 Origin |
| gzip_static（前端） | css 199KB → 26.4KB，`content-encoding: gzip` |
| 前端访问日志 tail-5000 | **0 × 5xx**；4xx 分布符合预期（401/404/418 盾牌与探测） |
| 后端 `zvpapi` SPA root（备用路径对等） | 同 hash `index-zr2uobXX.js` |
| 服务 | active，内存 165M（旧进程峰值 456M） |

## 四、回滚

1. 后端：`rsync -a --exclude .venv /www/zvms4/backend.bak-20261108-071409/ /www/zvms4/backend/` → 属主修正 → `systemctl restart zvms4`
2. 前端：两台 `cd /www/zvms4 && rm -rf frontend-dist && mv frontend-dist.bak-20261108-switch frontend-dist`
3. V052：`ALTER TABLE user_trash ADD restored_at TIMESTAMP NULL;`（历史值本就全 NULL，无损）
4. canary：回退到旧 `v5-canary` 构建重发（线上旧版 `f3dad79a` 调 /v3，须与后端回滚同窗，否则预览站 404）
5. 旧 PWA 缓存：App.vue `controllerchange` 自动刷新自愈，无需操作

## 五、遗留

- 两台 `frontend-dist.bak-20261108-switch` 与 `backend.bak-20261108-071409` 为本次回滚安全网，稳定一周后可与历史 `.bak` 一并清理（用户决策）。
- MCP `/api/v2/mcp` 别名移除仍待外部客户端迁移（OVERHAUL 遗留 #2 不变）。
