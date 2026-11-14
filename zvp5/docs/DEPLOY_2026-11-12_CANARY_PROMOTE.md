# 2026-11-12 canary 转正发布记录

发布范围：`main` = `02976ad`（merge `v5-canary`），canary 分支 = `9403661`（含本轮
i18n 拆包、EP 深路径、后端安全修复、两笔远端 UI 提交 `78f1fb4`/`a7cfe82`）。
canary 预览站同步重发。

## 一、本轮收口的遗留项

| # | 遗留 | 处理 |
|---|---|---|
| 1 | `/manifest.webmanifest`、`/site.webmanifest` 返回 `application/octet-stream`（浏览器拒绝解析，PWA 安装/图标/主题色失效） | 后端 `zvp` vhost、前端备用 `zvp` vhost 各加 `location = /{manifest,site}.webmanifest`（`types { application/manifest+json webmanifest; }` + `default_type`）；线上复验两者均返回 `application/manifest+json`。仓库 `deployment/zvp5.nginx.conf` 同步落规则防回归 |
| 2 | 首屏 i18n 全量（17 个字典 ~208KB 源码） | ai/manage/server/activity 四个大字典按路由懒加载（详见下） |
| 3 | main 上两笔部署文档未 push | 已推送（`4c394ec..9b4e00d`） |
| 4 | 生产前后端落后于仓库最新代码 | 本次全量发布（前端双端 + 后端） |

## 二、前端：i18n 拆包（P0-2）

- `i18n/locales/core.ts`：13 个小字典（common/nav/login/home/device/validation/
  about/markdown/user/profile/log/maintenance/notificationCenter）留首屏。
- `i18n/locales/namespaces.ts`：路径→字典映射，按页面实际引用推导（`/manage` 需
  server——ManageRoles 复用 ZSystemAuthDialog；`/group`、`/user/*` 需 manage+activity）。
- `router` 全局守卫 `await ensureLocaleForPath(to.path)`，失败不阻断导航
  （维护模式跳转在同一条守卫的后半段）；`main.ts` mount 后 idle 补拉其余字典
  （saveData/2G 跳过）。
- `utils/errorMessage.ts` 改读运行时消息表——它位于 axios 入口链，原先静态 import
  全量 `zhCN`，不处理会让拆分完全失效。
- 测试环境关闭 `components.d.ts` 生成（vitest 走官方 barrel resolver，否则每次
  跑测试都把该声明文件改回 barrel）。

首屏 JS（现代构建）：

| 项 | 改前 | i18n 拆包后 | 相对最初 |
|---|---|---|---|
| 入口 index chunk | 548.6 KB / gzip 188.5 KB | 437.6 KB / gzip **150.3 KB** | 1182 KB → −63% |
| 首屏 JS 合计（含 vendor） | 774 KB / gzip 270 KB | 657 KB / gzip **230.3 KB** | gzip 380 → 230 KB（−39%） |
| ai / manage / server / activity | 在入口内 | 40 / 24 / 23 / 13 KB 独立 chunk | index 内零残留 |

SW precache 94 条 2157 KB（排除了 AIHome/ManageHome/VolunteerRegister/ZMarkdown）。

## 三、后端：5 项安全修复上线

| 项 | 线上验证 |
|---|---|
| `GET /api/exports/reports/download` 叠加 admin JWT + `granted_to`，token 支持 `X-Download-Token` 头 | 无 JWT `401`、伪 JWT `401`（修复前可匿名下载全站归档） |
| 登录密文 sha256 一次性消费（Redis `kv_set_nx`，TTL 120s） | 登录伪凭证仍 `422`；axios 重试仅对幂等 GET 生效，POST 不会被重放 |
| 匿名 `GET /api/users` 按 IP 60/60s 限流 | 单次查询 `200`，超限 `429` |
| Socket.IO 无 token 拒连 | 握手后发送 connect 包返回 `44{"message":"请先登录后再建立实时连接"}` |
| 盾牌审计 URL 只记 path | 已生效（日志无 query 凭证） |

部署后 `import main` 冒烟 257 路由、`systemctl restart zvms4` active、
15 分钟内 journal `Traceback` 计数 0。

## 四、部署步骤与安全网

1. 备份（`140155`）：后端 `frontend-dist.bak-20261112-140155`（22M）、
   `backend.bak-20261112-140155`（98M，排 `.venv`）；前端
   `frontend-dist.bak-20261112-140155`（22M）。
2. nginx 改动前备份：后端 `zvp.bak-manifest-20261112-134222`、
   前端 `zvp.xiaobopisces.top.conf.bak-manifest-20261112-134242`。
3. 前端构建：`VITE_API_BASE=https://zvp.xiaobopisces.top/api`
   + `VITE_API_RACE_BASE=https://zvpapi.xiaobopisces.top/api`
   + `VITE_API_CONTENT_BASE=https://zvp.xiaobopisces.top/api`
   （生产不设 content base 时按页面源推导，结果同为 zvp/api；此处显式设置，
   与 canary Worker 共用同一份产物）。
4. 预压缩 `gzip -9k`（230 个文本资产全部配 `.gz`，nginx `gzip_static on`）。
5. 顺序：**前端资产先上 → 验证入口可取 → 后端壳后上**（后端 `index.html` 由
   `sub_filter` 把 `/assets/` 改写到 zvpapi；顺序颠倒会出现新壳指旧资产）。
6. 后端：`git archive` → `rsync --delete`（排除 `.venv/ data/ export/ settings.py
   *.pem aes_key.txt ssh_key* ssh_known_hosts* configure_runtime.py logs/ *.log
   __pycache__ .pytest_cache`，**另加 `*.db`/`renovate.json`**——dry-run 发现
   `--delete` 会误删运行态 `system_state.db`/`perf_samples.db`）。
7. canary Worker：`/tmp` 产物副本删 `sw.js`/workbox 后部署（预览站不跑 PWA）。

## 五、验收

| 检查 | 结果 |
|---|---|
| 生产首页 / 入口 | 200；`index-Gn6i2Dxt.js` 从 zvpapi 取，`content-encoding: gzip` 153,722 B |
| 首屏资源失败数（无头浏览器） | 0；32 个资源，标题/中文文案正确，无原样 i18n key |
| manifest / sw.js | `application/manifest+json`；SW precache 88 条已改写为 zvpapi |
| API | `/api/version` `v4.3.0-7`、`/api/health` 200 |
| 安全面 | 下载接口 401、socket 匿名 44、匿名查询 200、伪登录 422 |
| canary | 200，入口同 hash，渲染无失败（直连 zvp/zvpapi） |
| 后端 | active，journal 无 Traceback |

## 六、回滚

1. 前端（两台同步）：
   `cd /www/zvms4 && rm -rf frontend-dist && mv frontend-dist.bak-20261112-140155 frontend-dist`
2. 后端：`rsync -a --exclude .venv /www/zvms4/backend.bak-20261112-140155/ /www/zvms4/backend/`
   → `chown -R www-data:www-data /www/zvms4/backend` → `systemctl restart zvms4`
3. nginx：恢复对应 `.bak-manifest-*` 后 `nginx -t && systemctl reload nginx`
4. canary：`npx wrangler@4 rollback`（上一版本 `cb27a7b7-669b-4368-93e5-3f8277be66fe`）

## 七、遗留（未做，另有记录）

- EP 全量 CSS（328KB raw / ~45KB gzip）按需化：命令式组件样式与测试副作用链
  风险大于收益，保留。
- 后端 `zvms4.service` 仍 `ProtectHome=yes`，文件管理器 `/home` 分支不可用
  （放开会削弱沙箱，需单独决策）。
- nginx 层无限流（`limit_req`）：应用层已有登录/读预算/AI 三处限流，D-6 暂缓。
- 后端 P2：JWT 10 天无轮换、三个 `BaseHTTPMiddleware` 纯 ASGI 化、OCR 客户端复用、
  `REGEXP_LIKE`→`LIKE`；以及审计文档 D-1~D-10 其余项。
