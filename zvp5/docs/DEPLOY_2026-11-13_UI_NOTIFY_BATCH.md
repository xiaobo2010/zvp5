# 2026-11-13 前端四项改造 + 通知已读上云

四车道并行交付，全部改动已推送 canary，**未部署**（生产发布顺序见文末）。

## 一、改动总览

| # | 需求 | 落点 | 状态 |
|---|---|---|---|
| 1 | 登录卡 AUTH 标头上移 + 切角改右侧 | `前端约定/css/phi-theme.css`（源）+ `UserLogin.vue` | 已推送 |
| 2 | 义工录入卡片左侧条纹统一纯色 | `前端约定/css/phi-legacy-pages.css`（源）`.reg-card::before` | 已推送 |
| 3 | 服务器管理全部 Tab 对齐平台管理 + MAGI 六边形 | `views/server/**` + `前端约定/css/phi-server.css`（新）+ `ZHexUnit.vue` | 已推送 |
| 4 | 通知：已读上云 + 顶部横幅 + 用户收件页 | 前后端一体（V053 表 + 两接口 + 横幅 + `/notifications`） | 已推送 |

## 二、各项要点

### 1. 登录卡 AUTH 标头

- 根因：`.yg-login__card > * { position: relative }` 把标头的 absolute 定位覆盖掉，标头以普通流贴住标题。
- 修法：标头移出卡片（clip-path 容器会裁子元素），包进新外壳 `.yg-login__card-shell` 跨坐卡片顶边（top:-12px / right:28px 让开卡片右上 20px 切角）；切角从左上改右上（`polygon(0 0, calc(100%-8px) 0, 100% 8px, 100% 100%, 0 100%)`），与卡片本体切角同向。
- `ZLoginReveal.vue` 用的是 `login-reveal__*` 自有 class，不受影响（已核）。
- observer 截图复核：桌面深浅 + 移动端 390 全 PASS，无裁切。

### 2. 录入页左侧条纹

- `.reg-card::before` 的 `linear-gradient(--legacy-accent, transparent 72%)` 在矮卡上显示成实色、仅高卡（表格卡 531px）露出渐变尾——即用户看到的「两个没渐变一个有」。
- 修法：全局改纯色 `var(--legacy-accent)`，一处改动所有 `.reg-card` 一致。

### 3. 服务器管理全部 Tab（7 文件）

- 新建公共视觉层 `前端约定/css/phi-server.css`（源）→ 同步副本 `src/assets/styles/yg/phi-server.css`，`main.ts` 引入；作用域 `.phi-srv`，不外溢到平台管理/用户端。
- 新组件 `views/server/components/ZHexUnit.vue`：MAGI 六边形消息单元（clip-path 六边形徽章 + HUD mono 标签 + 说明文字；tone: accent/ok/warn/danger/idle；danger 呼吸光晕，reduced-motion 静止）。
- 7 个文件（Home/Maintenance/Totp/Files/DbContent/Database/Ssh）统一：phi 令牌配色、切角+硬阴影、HUD 读数条、危险态强调置顶、次要信息降噪。
- 响应/执行逻辑：六个分区 chunk `prefetchRoutesWhenIdle` 空闲预取 + `@pointerenter` 即时预取；骨架屏（`.phi-srv-skel`）；in-flight 禁用+扫光（`.phi-srv-busy`）防双击；SSH `openTerminal`/`disconnect` 增加状态守卫（双击不再开出两个授权框）；顶部加载条。
- 顺带修的两个真 bug：`ServerDatabase.vue` `formatMetric`/`formatInterval` 只判 null 不判 undefined（API 缺字段整页渲染崩）——补齐与同文件其余工具一致的 `null | undefined` 双判。
- 新增 `ServerDatabase.spec.ts`（该 2043 行视图此前零覆盖；按仓库 vitest 口径整包 mock element-plus——深路径 vi.mock 会被 vitest alias 塌成同一模块 id 互相顶掉，见 vitest.config.ts）。

### 4. 通知三件套（前后端）

**后端**（`notification_reads` 上云）：
- 新表 `V053__notification_reads.sql`（(user_oid, notification_oid) 复合 PK、无 FK、幂等 CREATE），models.py + V001 基线同步（V047/V052 口径）。
- 仓储：`list_read` / `mark_read`（Oracle MERGE 单语句 upsert）；通知硬删同事务清理 read 行；read 路径不进全局列表缓存、mark_read 不 bump epoch（不改变列表语义）。
- 路由：`GET /v1/notifications/read`、`POST /v1/notifications/{oid}/read`（幂等；404=通知不存在；任何已登录用户可调）。`GET /v1/notifications` 响应结构零改动。
- 语义：已读 = `read[oid] >= notification.version`；通知编辑 version+1 自动重新未读；suppress 不上云（设备级）。

**前端**：
- `utils/notificationState.ts` 重构：已读真源改服务端；本地只留 `suppressed` + `lastGlobalVersion`；内存四层（serverRead/pendingRead/sentRead/read 合并取大——旧响应不打回新写入）；旧 localStorage 的 read 标记迁移为待补写项。
- `ZNotificationReminder.vue`：ElDialog 全屏模态 → `.zn-banner` 顶部非模态消息框（无遮罩/不锁滚动/不抢焦点，Esc/×可收起；ResizeObserver 量高写 `--zn-banner-offset` 让内容区让位）。已读写穿+失败回滚+横幅重试；suppress 仅本地。10s 延迟、60s 节流、队列进度（第 N/M 条）保留。
- 新页 `/notifications`（`UserNotifications.vue`）：全部现役通知（已读+未读），未读六边形角标高亮，ZMarkdown 展开正文，就地标记已读；骨架/空态/错误态齐全。导航入口仅普通用户可见（admin 走 `/manage/notifications` 编辑+发送页，避免双入口冲突）；空闲预取已接入。
- 新增共享层 `components/notification/notificationStore.ts`：横幅/收件页/资料页共享同一份已读视图，任一处标记全局生效。

## 三、验证（2026-11-13 全量门禁）

```
前端 vitest 全量:   99 files / 1144 tests passed
后端 pytest 全量:    4544 passed, 1 warning（既有 httpx 弃用提示）
前端构建:           vue-tsc + vite build + PWA(generateSW) 通过，
                    UserNotifications/ServerDatabase 独立分包，未进首屏 chunk
后端 ddl_check:     PASS notification_reads / 49 tables, 62 indexes - no drift
ruff:               全部改动文件 All checks passed
design:sync:        源→副本 0 更新（无漂移）
V053 dry-run:       1 statement 解析通过（未连库）
observer 视觉复核:  登录卡与条纹前后对比全 PASS
```

## 四、发布顺序（生产部署时）

1. **先上表**：后端生产机 `cd /www/zvms4/backend && sudo -u www-data .venv/bin/python misc/apply_ddl.py v053`
   （部署脚本的 backend 段只 rsync+重启，不会自动建表；跳过这步新接口会 ORA-00942 → 500，旧接口不受影响）。
2. 再发后端（`/tmp/opencode/deploy_prod_20261112.sh backend`）。
3. 最后发前端（前端 资产先上、后端壳后上）。
   - 前端先行亦可：已读接口 404 期间横幅有降级（拉取失败保留上次结果、写穿失败回滚+重试），不会崩，但「已读」不生效。

## 五、回滚

- 通知表：`DROP TABLE notification_reads;`（只丢已读状态，通知本身不受影响）。
- 前端：revert 对应 commit 即可，无状态迁移残留（旧本地 read 迁移是一次性补写，服务端清表后重拉会重新提示未读，符合预期）。

## 六、遗留与已知非阻塞问题

1. dev 环境（无后端）下 API 404 的原始 HTML 会被 toast 直接渲染——错误处理未解析非 JSON 响应体，生产有后端不触发，建议后续统一 `errorMessage` 口径。
2. 录入页「加行」按钮被底部悬浮主题切换胶囊部分遮挡（既有 z-index 问题，非本批改动引入，待单独处理）。
3. `mark_read` 是「先读版本再 MERGE」两次往返，与管理员编辑并发时可能落旧版本——已读判定只会偏保守（重新提示未读），语义安全；彻底消除需把版本子查询塞进 MERGE。
4. 按 `notification_oid` 清理依赖复合 PK skip scan，表大后如变慢可加 `(notification_oid)` 索引（需 V054）。
5. 服务器管理页截图级视觉验收未做（需 admin 登录态）：spec+构建已覆盖，视觉细节待 canary 上线后肉眼过一遍。
