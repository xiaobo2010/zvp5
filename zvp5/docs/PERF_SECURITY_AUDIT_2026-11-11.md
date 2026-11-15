# canary 前端性能与后端安全审计修复(2026-11-11)

范围:`v5-canary` 分支整体审计——前端性能(主)+ 后端安全与性能(辅)。
审计→修复→全量门禁→部署 canary 一条龙。**后端改动只进分支,未部署生产**
(生产后端在 main 分支,部署需另行授权)。

## 一、前端性能修复(已验证)

### F-1 Element Plus 深路径按需加载(P0,核心)

**根因**:全部源码与 unplugin resolver 走 barrel(`element-plus` /
`element-plus/es`,同一物理模块 `es/index.mjs`)。rolldown 无法 tree-shake
EP barrel(组件带 `withInstall` 顶层副作用),只要入口链任一文件 import
barrel,124 个组件全部进首屏 chunk。

**修复**:
1. `vite.config.ts` 自定义 `deepElementPlusResolver`:扫描
   `node_modules/element-plus/es/components/*/index.mjs` 的导出语句构建
   「组件名→目录」映射(子组件如 `ElFormItem→form`、`ElTableColumn→table`
   天然映射到父目录),彻底消除 barrel fallback;组件路径用
   `createRequire.resolve` 定位(免疫 vite 把配置打包进 `.vite-temp` 的
   `import.meta.url` 漂移)。
2. 入口静态链(`main.ts`/`App.vue`/`plugins/axios.ts`/`plugins/short-token.ts`
   /`stores/user.ts`/`UserNav.vue`/`ZNotificationReminder.vue`
   /`ZPrimaryDeviceGate.vue`/`ZSystemAuthDialog.vue`/`modifyPasswordDialogs.ts`)
   手写深路径。
3. 其余 92 处运行时 `from 'element-plus'` 值导入(93 个文件,`type` 导入
   保留)批量改深路径;`dayjs`(EP re-export)改直接依赖。
4. `main.ts` 去掉 `app.use(ElementPlus)` 全量注册,仅保留 `v-loading`
   指令(`app.use(ElLoading)`,模板 52 处依赖);全局 size 默认值本就是
   `default`,无行为变化。
5. `manualChunks` 去掉 `element-plus` 强制合并(否则懒加载路由独占的
   重组件被并进首屏 preload 的共享 chunk)。

**vitest 兼容**:`vi.mock('element-plus')` 依赖「所有 EP import 落到同一
模块 id」才能连模板组件一起拦截。`vitest.config.ts` 加 alias 把深路径
统一指回 barrel,测试语义保持原样,**35 个 spec 零改动**;vite.config 的
resolver 在 `VITEST` 环境同样退回官方 barrel resolver(双保险)。

**收益**(构建实测):
| 项 | 修复前 | 修复后 |
|---|---|---|
| 入口 chunk | 1182 KB | 536 KB |
| 首屏 JS 合计(raw) | 1404 KB | 755 KB |
| 首屏 JS 合计(gzip) | ~380 KB | **184 KB(-52%)** |

EP 组件按路由自然分流:管理/服务器/AI 页的表格、上传、图表组件不再进
首屏(index chunk 仅剩 EP locale 文案字符串,组件实现零残留)。

### F-2 竞速 GET 改「延迟对冲」(P1)

`hedgedGet.ts`:relay 从「同 tick 无条件双发」改为主路先发、
`RELAY_DELAY_MS=200` 门槛后才启动备路;主路 2xx 快速返回 → 备路根本
不发(白名单接口常态出网请求减半);主路网络错误/5xx → **立即**启动
备路(保住原兜底语义);4xx 照旧立即放弃备路。观测数据兼容
(`startedAt=null` 已有 `durationMs:null` 语义)。测试:10 旧用例传
`relayDelayMs=0` 保留原时序,新增 2 用例钉门槛行为,12/12 过。

### F-3 登录后 idle 预取收窄(P1)

`UserNav.vue` 的 idle 预取只保留普通用户高频轻页(home/activity/profile);
管理/服务器/AI/登记的重 chunk(ManageHome 127KB + AIHome 196KB +
VolunteerRegister 71KB)改由已有的 `pointerenter` 即时预取兜底。
`routePrefetch.ts`:timeout 2000→5000,`navigator.connection.saveData`
与 2G 弱网下不后台预取(点击即时预取不受影响)。登录后 2s 内的额外
下载从 ~400KB 降到 ~80KB。

### F-4 SW 首装 precache 收缩(P1)

`globIgnores` 追加 `AIHome/ManageHome/VolunteerRegister/ZMarkdown` 的
js+css(与 monaco 同一取舍:离线不可用属可接受,在线运行时拉取)。
precache:81 条 3086KB → 90 条 **2153KB(-30% 体积)**(条数升是 EP
组件分散进各路由 chunk 所致)。

### F-5 资产清理(P2)

- `public/favicon.png`(330KB,仅作 pwa 生成源)→ `pwa-assets/`,不再进
  dist。
- `phi-theme.css` 删 3 处 `format('truetype')` 引用(woff2 覆盖
  Chrome64+/Safari12+ 全部目标机型),源目录「前端约定/css」与生成目录
  同步改,phi 字体 ttf(768KB)不再打包;ttf 源文件保留。
- `pnpm remove @amap/amap-jsapi-loader socket.io-client`(全仓零引用)。
- `vite.config.ts` 加 `traceElementPlusBarrel` 诊断插件(构建日志打印
  barrel 引用回归,EP 全量入 chunk 时直接看 `[EP-BARREL]` 行)。

## 二、后端安全修复(已验证,只进分支不部署)

审计结论:整体水位高(SQL 全参数化、JWT 服务端撤销、SSRF 双层拦截、
双人 TOTP、错误不吐堆栈),**无 P0 可利用漏洞**;修复以下 5 项
(pytest 4496 → **4500 passed**,新增 16 用例):

| # | 问题 | 修复 |
|---|---|---|
| B-1 | `GET /api/exports/reports/download` 仅凭 query 一次性 token,无认证,token 进 nginx 日志;下载物为全站数据 | 叠加 `get_current_user` + admin + `granted_to==user.id`;token 优先 `X-Download-Token` 头、query 兼容;不匹配统一 403 不泄露;不消费他人令牌;审计只记 path |
| B-2 | RSA 登录密文 ±60s 可重放;verify 密文可换 login 换 10 天会话 | `util/credential_replay.py`:密文 sha256 一次性消费(Redis NX 共享/进程内兜底,TTL 120s),重复 401「登录凭证已失效」;不改前后端协议 |
| B-3 | 匿名 `GET /api/users` 学号精确探测无限速 | `read_throttle.check_anonymous`:按 IP 60 次/60s,超限 429+Retry-After;登录用户不受影响 |
| B-4 | Socket.IO 匿名连接放行 | 无 token 拒连(44),warning 只记 sid |
| B-5 | 盾牌中间件审计把含 token 的 query 落盘 | `str(request.url)` → `request.url.path` |

## 三、验证记录

- vitest:**1098/1098**(基线 1094 + hedgedGet 2 + routePrefetch 2)
- vue-tsc:0 error(build 内含)
- eslint:0 error(存量 ZMarkdown v-html warning 不变)
- 后端 pytest:**4500 passed**;`compileall` 通过;无新依赖
- 构建:3m38s 成功;canary 部署后线上 smoke(见下)
- 部署:构建产物 `index-Dj7yHxiL.js`;删 `sw.js`/workbox 后 wrangler deploy

## 四、遗留项(记录未修)

- ~~**P0-2 i18n 按路由拆分**~~ **已于 2026-11-12 完成**:ai/manage/server/
  activity 四个大字典按路由懒加载,首屏入口 gzip 188.5→150.3KB(另见
  `DEPLOY_2026-11-12_CANARY_PROMOTE.md`)。
- EP 全量 CSS 328KB(`theme-chalk/index.css`):按需 CSS 曾因命令式组件
  样式/测试副作用链问题被关闭,gzip 后 ~45KB,风险收益比不佳,保留。
- P1-4 GET 缓存双写 + structuredClone 体积阈值;P2-3 草稿 deep watch;
  P2-2 路由过渡动画 340ms(视觉决策,归用户);legacy 双份产物 ~7MB
  (产品决策:覆盖校内老平板)。
- 后端 P2:JWT 10 天无轮换、三个 BaseHTTPMiddleware 纯 ASGI 化、OCR
  httpx client 复用、REGEXP_LIKE→LIKE、`X-Real-IP` 信任面收窄;
  部署层 D-1~D-10(8018 监听面、CORS drop-in、nginx 限流、上传上限
  不一致等)——生产 systemd/nginx 以实况为准,另行安排。

## 回滚

- canary:`npx wrangler@4 rollback`(上一版本 `e07f03d7`);或重部署
  `2ff72c8` 产物。
- 后端未部署,无需回滚;合并 main 前用 `git revert` 即可。
