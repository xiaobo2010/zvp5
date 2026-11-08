# canary 平板滚动流畅性优化（2026-11-08）

> 目标受众：三星 P615C / P620（Galaxy Tab S6 Lite 级 SoC，Mali GPU，60Hz，
> 4GB RAM）。要求：页面上下拖动时减少断裂感；可积极 prefetch；**不改任何
> 页面设计**。范围：v5-canary 分支（含本文件的改动暂不合入 main）。

## 一、断裂源诊断（只读侦察结论）

| 嫌疑 | 实况 | 判定 |
| --- | --- | --- |
| 毛玻璃导航 | `.yg-glass-nav` fixed + `backdrop-filter: blur(28px) saturate(175%)`（yg-glass-nav.css），滚动时每帧对下层内容重采样 | 设计本体，**不降级**，靠撤走并行负载保帧 |
| 装饰动画 | phi 主题 8 个 `infinite` 动画与滚动并行跑合成：全屏 3D 网格 `.yg-phi::before`（18s，fixed 大纹理）、速度线 ×3、判定线脉冲、时段面板扫边、分段器高光、标题/徽标 glitch | 主治理对象（感知盲区可冻结） |
| 路由切换 | 全部路由 `() => import()` 懒加载，此前零预取——平板点击导航才开始下载 chunk，切换动画走完后白屏一下 | 主治理对象 |
| 字体 | phi 显示字体 Bebas/Rajdhani `font-display: swap`，首次绘制 FOUT 文本跳动 | 预热消除 |
| scroll 监听 | 仅 2 处（ZAiChat 已 `.passive`；ServerSsh xterm viewport 未标） | 顺手补 passive |
| 长列表 | `ZActivityList` 是 el-table + `max-height`（EP 自管滚动），行数几十级 | `content-visibility` 收益低 + 嵌套滚动跳动风险，**不动** |
| 图片 | 仅 9 处 `<img>`，缩略图已有按需加载策略（ZAttachmentAudit） | 不动 |

## 二、改动（9 文件，全部为性能层，无视觉变化）

### 1. 滚动时暂停纯装饰动画（核心）

- `utils/scrollActivity.ts`（新增）：window 捕获阶段 `passive` scroll 监听，
  滚动进行中给 `<html>` 打 `data-scrolling`，静止 160ms 移除。捕获阶段
  一网打尽内部滚动容器（`.yg-layout__content`、el-table、弹层、终端）；
  handler 仅做标志位短路 + 定时器重置，高频事件开销≈0。
- `assets/styles/perf-tablet.css`（新增，main.ts 引入）：`html[data-scrolling]`
  下冻结 5 个平滑装饰动画（grid-float / velocity / judgment-pulse /
  edge-flow / seg-glint）。
- **刻意排除**：`phi-title-glitch`、`phi-emblem-rotate/glitch` 是
  `steps()/alternate` 故障动画，冻结在中间帧会显示「错位」态——观感会变，
  违背不改设计约束。选择器与 phi-theme.css（design:sync 生成物）同名，
  覆盖规则全部落在生成物之外的独立层，同步不丢。

### 2. 积极预取（用户点名）

- `utils/routePrefetch.ts`（新增）：
  - `prefetchRoute`：调用路由记录的懒加载工厂 = 触发 chunk 下载执行，
    Set 幂等，错误全吞。
  - `prefetchRoutesWhenIdle`：`requestIdleCallback` 空闲批量。
  - `warmupFonts(WhenIdle)`：`document.fonts.load` 预热 phi 字体。
- `UserNav.vue`：watch `visibleNavs`（非一次性快照——`position` 由
  `refreshUser()` 异步拉回，首帧快照会漏管理/服务器/AI 项）空闲批量预取
  可见导航路由；两个按钮分支 `@pointerenter` 立即预取（hover/触摸按下
  与 click 间的百毫秒窗口完成命中）。
- `App.vue`：挂载时 `warmupFontsWhenIdle(['400 1em Bebas', '500 1em
  Rajdhani', '700 1em Rajdhani'])`——先于登录揭示动画（ZLoginReveal 静态
  import 已在主 chunk，无预取需求）与首页大标题首次绘制，消 FOUT。

### 3. 顺手微修

- `ServerSsh.vue`：xterm viewport scroll 监听补 `{ passive: true }`
  （handler 只读状态，绝不 preventDefault）。

## 三、明确不做（及理由）

| 项 | 理由 |
| --- | --- |
| 降毛玻璃 blur 半径 / 换实色 | 改视觉，违背约束（flat.css 已有先例：侧栏光斑用渐变替代 blur，那是等价替换，本次无等价方案） |
| `content-visibility: auto` | 列表是 el-table 内滚动，收益低；EP 嵌套容器上高度估值易跳动 |
| ZLoginReveal 再收敛 | 上轮 `bddfc6f` 刚做完碎片预算 + 单次测量 + 定时器治理，不动 |
| manualChunks 重排 | 主 chunk 结构稳定，收益小风险大 |
| 图片 lazy/decoding | 仅 9 处且缩略图已有按需策略，收益趋零 |

## 四、验证

- vitest **1042/1042**（新增 13：scrollActivity 4 + routePrefetch 9；
  修复过程教训：模块级 Set 跨用例串味 + 断言失败导致 fake timers 泄漏
  连锁超时——spec 内每用例独立路由命名空间 + `afterEach` 兜底恢复）。
- vue-tsc 0 error；eslint 0 error（仅剩既有 ZMarkdown v-html warning，基线一致）。
- **rolldown `ILLEGAL_REASSIGNMENT` 教训**：`<script setup>` 里对 const
  （watch 返回的 WatchStopHandle）重赋值 `= null`，vue-tsc 与 eslint 均静默，
  只有构建期能抓——watch 句柄不需要置空，直接调用即可。构建是必过关卡。
- 构建产物：`index-CrDLRL50.js`；`data-scrolling` 暂停规则进
  `index-bxVvAM1Z.css`（线上复核 grep 命中）；sw.js/workbox 按 canary
  runbook 构建后剥离。
- 部署：Worker `zvp-yuren` Version `21f44e92-d184-4d07-8288-ce884d55683b`。
- headless 行为冒烟（chrome-headless-shell + playwright-core）：
  - 内部容器滚动（捕获通道）与真实 `window.scrollBy` 均触发
    `html[data-scrolling]`，静止后 ~160ms 移除：**PASS**
  - phi 字体 idle 预热生效：`document.fonts.check` 三个字重全 true
    （Bebas 400 / Rajdhani 500 / 700），`fonts.status = "loaded"`：**PASS**
  - 登录页渲染正常（input ×2 + 应用挂载 + 文本可见）：**PASS**
  - 唯一 console.error 为 `The script has an unsupported MIME type
    ('text/html')`——定性：PWA `injectRegister: 'auto'` 在生产构建注入
    SW 注册，canary 剥 sw.js 后 Worker 按 SPA 回落返回 index.html 所致，
    **11-07 起的既有设计行为**（生产站有真 sw.js 无此报错），非本次引入；
    响应层复核零 404、零 script 拿 html 的真实资源。

## 五、遗留

- 真机（P615C/P620）实测由持有设备者在 canary.xiaobopisces.top 验证：
  重点页面为活动列表（el-table 长滚动）与首页（装饰动画最密）。
- 若真机仍有断感，下一梯队候选：`phi-judgment-pulse` 移出暂停名单
  观察对比；el-table 行内重阴影降载（需对照设计确认）。
