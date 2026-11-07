# ZVP5 大修总账（2026-11-07 ~ 11-08）

> 一次完成：目录更名 zvp5、API 全量单版本化（/api/v1）、DB 无用字段清理、后端逻辑优化、前端性能优化、代码审查修复；main 与 v5-canary 双分支同步完成并推送。
> API 版本政策与撞车裁决的正式记录见 [`ADR_API_V1_REVERSIONING.md`](./ADR_API_V1_REVERSIONING.md)。

## 一、main 提交序列

| commit | 内容 |
|---|---|
| `b1cc85a`（canary 侧 `74e5535`） | 目录更名：`zvms4-modified-main→zvp5`、`zvms4-frontend→zvp5-frontend`、`zvms4-backend-python→zvp5-backend-python`；同笔修正 wrangler.jsonc / preview-worker README / configure_runtime.py / ci.yml 路径引用；服务器路径与品牌文案按决策不动 |
| `4944084` | API reversioning：后端 84 条 v2/v3 路由归并 `/api/v1`（未版本域 172 条与 `/api/ai/v1` 外部契约不动）；撞车 5 组裁决落地（见 ADR §3）；MCP 保留 `/api/v2/mcp` 兼容别名；前端 66+ 处调用点、hedgedGet 竞速正则、platformGuard 手工拼接同步；tests 234 处机械更新 + pwdm/privacy 语义改写 + 路由快照重冻结（257 条）；`docs/ADR_API_V1_REVERSIONING.md` 入库 |
| `78e2322` | DB 删除无用字段 `user_trash.restored_at`（471 列中唯一合格项，全生命周期只写 NULL 零读取）；新增 `db/oracle/ddl/V052__drop_user_trash_restored_at.sql`（幂等，只入库不自动执行）；V011 基线同步编辑 |
| `02eed4a` | 后端逻辑优化七项：realtime 指标采集下线程、users_v3 绑定/设备单 session 事务（写与审计同生共死）、导出进度推送降频（1 万行 500 次→101 次）、时长计算/agent 班级名/提案失效三处 N+1 消除、活动状态变更时长缓存批量写 |
| `1907117` | 前端性能：monaco 摘除首屏与全站 chunk 图（首屏 JS 5.33MB→1.94MB）、markdown-it/dompurify 出入口、chart.js 三条静态链懒加载、死资源清理（Ubuntu 字体/brand.jpeg/vant 依赖）、PWA precache 瘦身（~3.9MB→2.8MB） |
| `c7248f7` | 前端审查修复：P1×5（活动详情失败白页、重置密码失败弹成功、组列表永久 loading、导出失败空轮询、文件管理英文直出）+ P2×10（竞态守卫/双提示收口/i18n 迁移/请求序号/轮询判重/全局事件监听清理等） |
| `2009ed4` | 活文档全量同步：API_STRUCTURE.md 路由表按实装重写（257 条口径+撞车裁决节，删除「不要新增 v1」旧裁定）、根 README 44 路径+47 版本映射+裁决注、NOTIFICATIONS/AI_GATEWAY/后端 README 等 15 篇；REFERENCE_TREES_ALIGNMENT:25-32 历史裁定段原样保留 |

canary 分支 = 上述各阶段逐笔 merge + canary 独有两笔（`13ca249` vant 死 CSS 删除与 phi 字体 woff2 化 846KB→230KB；`835ec20` ZLoginReveal i18n 化与旧 user 界面死树 4756 行删除）+ 外部提交 `e6b6431`（义工标签配色）并入。终态：main=`2009ed4` 系，v5-canary=`7551f40` 系，均已推送。

## 二、校验凭证

- 后端：pytest **4496 passed**（含新增事务原子性/批量失效/v3 列表用例）；ruff 77 条=既有基线零新增；`db.ddl_check` 48 表 62 索引无漂移；路由快照按 `main.app` 实测重冻结，重复 (method,path)=0
- 前端 main：vitest 92 文件 **1054** 全过；vue-tsc 0 error；eslint 基线不变；构建断言 modulepreload 无 monaco、首屏 JS 1.94MB
- 前端 canary：vitest 90 文件 **1029** 全过；vue-tsc 0 error；构建断言 `.van-*` 选择器 0、woff2 三件 232K + TTF 兜底、legacy 产物完整、modulepreload 无 monaco

## 三、发版时序（锁步，生产窗口执行）

本方案为**干净切换**：v2/v3 前缀不再保留（仅 MCP 有 `/api/v2/mcp` 别名），因此后端与生产前端需近原子部署：

1. **后端 + 生产前端同窗切换**：先备好新前端 dist，`systemctl restart` 新后端后立即切换 dist。间隙内旧前端调 `/api/v2|/v3` 会 404，控制在分钟级。
2. **切换完成后执行 V052**：`python misc/apply_ddl.py v052`（`--dry-run` 可预演）。**必须在新后端代码上线之后**——旧代码的归档 INSERT 显式写 `restored_at`，先删列会让删除用户功能报 ORA-00904。回滚 SQL 在 V052 文件头注释。
3. **预览站重发**（canary.xiaobopisces.top 当前冻结在旧 `/v3` 调用版本，必须晚于生产后端上线）：
   ```
   cd worktrees/v5-canary/zvp5/zvp5-frontend
   VITE_API_BASE=https://zvp.xiaobopisces.top/api \
   VITE_API_RACE_BASE=https://zvpapi.xiaobopisces.top/api \
   VITE_API_CONTENT_BASE=https://zvp.xiaobopisces.top/api npx vite build
   rm -f dist/sw.js dist/sw.js.map dist/workbox-*.js*
   cd ../deployment/preview-worker
   CLOUDFLARE_API_TOKEN=$(cat ~/.config/opencode/credentials/cf-api-token) \
   CLOUDFLARE_ACCOUNT_ID=<CF_ACCOUNT_ID> npx wrangler@4 deploy
   ```
4. **PWA 旧缓存**：切换后旧缓存用户首次访问报错一次，Service Worker `controllerchange` 自动刷新即恢复（App.vue 已有该逻辑）。

## 四、遗留清单（未做项，按优先级）

1. **发版窗口**：已于 2026-11-08 执行完毕（后端+生产前端+V052+canary 全链路），见 [`DEPLOY_2026-11-08_V1_CUT.md`](./DEPLOY_2026-11-08_V1_CUT.md)。
2. **MCP 别名移除**：外部客户端确认迁移后删 `main.py` 的 `/api/v2/mcp` include（带 TODO 标记）并重冻结路由快照。
3. **element-plus 按需化**（JS 约 -300~400KB raw）：全局注册/`v-loading`/命令式组件/ElConfigProvider 语言包回归面大，单独立项。
4. **legacy 产物去留**：dist 的 48%（8.7MB）；现代端运行时代价≈0，收益是磁盘与构建时间。取决于学海平板 UA 实测（`src/plugins/ua.ts` 可采）。
5. **ai_quota_bak_v051**：V051 的生产回滚安全网，确认稳定后可 DROP（用户决策）。
6. `imports` 审计表整表评估、`app_logs.ts_at` 双时间列下线（下线=改 API 响应形状）、管理列表分页（notifications/ai_models/ban）。
7. 前端卫生项：`api/group/crud.ts` 英文**成功**提示、`translateKey` 参数插值仅单层、`App.vue` 的 `t('action.cancel','取消')` 键不存在靠 fallback、ElNotification 无 `grouping` 参数（api 层通知去重可改 `id` 方案）、败方 5 个函数体整体下线（本轮仅摘装饰器留档）、`util/calculate.py` 的 `time_with_origin` 死链、#6 服务层批量 SQL 待仓储化、`inspect.signature` 能力探测可移除。
8. pwdm「自定义密码」语义已随 v2 退役失去 HTTP 面（UI 现用 v3「是否设置过密码」）。
