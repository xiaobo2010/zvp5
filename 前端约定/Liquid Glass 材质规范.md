# Liquid Glass 材质规范 v1

> 与《前端设计准则 v1.0》配套。准则管排版/网格/颜色，这一份只管**材质**：
> 什么该是玻璃、什么不该是、玻璃有几档、深浅色怎么各自调、怎么验证。
>
> 实现位置：
> - 令牌 `src/assets/styles/v2/tokens.css`（`--yg2-lens-*`）
> - 材质层 `src/assets/styles/v2/glass.css`（最后加载，覆盖前面各层的扁平材质）
> - 光场 `src/assets/styles/v2/materials.css` + `src/ui/shell/AmbientField.vue`

## 1. 一条原则

**玻璃是给「容器」用的，不是给「字」用的。**

玻璃解决的是「这块东西浮在什么上面」的空间问题，不解决「这块东西好看」的装饰问题。
所以：越靠近内容、越需要长时间阅读、信息越密集 → 越实；
越靠近导航、越是一次性浮层 → 越透。

推论：

| 值得玻璃 | 不要玻璃 |
| --- | --- |
| 侧栏、悬浮工具条、底部标签栏 | 表格、日志、长文正文 |
| 卡片、统计面板 | 列表行、菜单项（除非是选中态） |
| 弹窗、抽屉、下拉、Toast、Tooltip | 表单输入框（要「内陷」，见 §5） |
| 主按钮之外的控件（--secondary / --quiet） | 主按钮本身的常规填充 |

## 2. 四个厚度档

厚度不是随便调的，是按「离观察者多远」排的，任取其一即可，不要为单个组件新造一档。

| 档 | 令牌前缀 | 典型使用者 | 浅色 fill | 深色 fill | blur |
| --- | --- | --- | --- | --- | --- |
| 卡片 | `--yg2-lens-matte-*` | `.yg2-card` | 白 95%→74% 竖向渐变 | `#2c2f39` 82%→76% | 18px |
| 轻玻璃 | `--yg2-lens-quiet-*` | 侧栏内分组、说明块 | 白 86%→66% | `#2c2f39` 90%→82% | 20px |
| 页面级 | `--yg2-lens-glass-*` | 登录卡、`.yg2-glass-surface` | 白 92%→82% | `#2e313c` 90%→84% | 30px |
| 导航 | `--yg2-lens-chrome-*` | 侧栏 `.yg2-sidebar` | 冷灰 78%→62% | `#24262e` 86%→78% | 34px |

另有两条专用：

- `--yg2-lens-panel-fill`：`.yg2-panel` / `.yg2-xcard`。**比卡片实一档**（浅 97%→93%，深 94%→90%），
  专给表格、更新日志、活动详情这种「一屏都是字」的容器。字压在上面不牺牲可读性。
- `--yg2-lens-control-*` / `--yg2-lens-input-*`：控件与输入框，见 §5。

## 3. 一层面板玻璃由 4 个零件构成

缺一个就会退化成「普通半透明卡片」：

1. **fill**：带竖向明暗差的渐变（上亮下暗）。均质 fill 看起来是纸，不是玻璃。
2. **blur + saturate**：`backdrop-filter: blur() saturate(190% 浅 / 165% 深)`。
   saturate 是被忽略得最多的一项——背景颜色变艳，玻璃才有"折射过"的味道。
3. **顶光 `inset 0 1px 0`**：模拟玻璃上沿被光打亮的一条线。深色下这条最出效果。
4. **方向性镜面边 `::after`**：`padding:1px` + `mask-composite: exclude`，
   背景用 `--yg2-lens-rim-grad`（152deg，左上纯白 → 右下冷灰）。
   均匀的 `inset 0 0 0 1px` 是"描边"，方向性的才像"倒角"。

再加一层 **cast**（投影）把它从背景上"抬起来"。深色下 cast 要更黑更紧，浅色下要更散更淡。

## 4. 光场：玻璃需要有东西可折

玻璃本身没有质感，质感来自**它背后被模糊的东西**。所以环境光场是这套材质的一半：

- 4 团 blob（左上冷 / 右上暖 / 左下紫 / 中部焦点）+ 1 团跟随鼠标的柔光。
- 浅色 `--yg2-bg #e9edf5`，光斑饱和度高、透明度 0.92；深色 `--yg2-bg #0e1015`，光斑压到 0.66。
- **深色下 `data-ambient` 的覆盖必须写成后代选择器**：`data-theme` 在 `<html>`，`data-ambient` 在
  `.yg2-ambient` 上，写成 `[data-theme="dark"][data-ambient="x"]` 永远匹配不到，深色会一直用浅色马卡龙。
  这是本项目踩过的坑，已修正为 `[data-theme="dark"] [data-ambient="x"]`。

## 5. 控件与输入

- **按钮/图标按钮/分段控件/底部标签**：fill 用 `--yg2-lens-control-*`，hover 提亮 + 浮起，
  `:active` 压暗并 `translateY(0.5px)`。指针柔光（`::before` + `--lx/--ly`）只在 hover 时 `opacity:1`。
- **输入框**:反过来做「内陷」——`inset 0 1px 2px` 暗 + `inset 0 0 0 1px` 细边，
  focus 时把细边换成 accent 环。凸起的输入框在玻璃体系里会显得像按钮。
- **选中态**（侧栏 `.is-active`）：玻璃底 + 品牌色 20%→11% 的斜向渐变 + 内侧亮边，
  不要再加左侧色条。

## 6. 主按钮与文字

- 主按钮保持品牌色实心（`--yg2-accent`），只加顶光与同色投影，**不要**给它套玻璃——
  一屏里唯一的实心色块是视觉锚点，玻璃化会把它稀释掉。
- 玻璃上的文字只用 `--yg2-text` / `--yg2-text-secondary`，不要在玻璃上再叠半透明文字。
- 深色下浅色玻璃最容易出问题的是「亮蓝玻璃 + 灰字」。凡是 >40% 白的 fill，深色必须换深 fill。

## 7. 顶栏（本项目的特例）

顶栏**不做玻璃**，是实体 chrome 条：

- 内容区是独立滚动容器（`.yg2-app__content`），永远滚不到顶栏下面；
  给它 `backdrop-filter` 只会把它背后的环境光糊成一条灰。
- 所以顶栏用 `--yg2-lens-chrome-solid` 纯色 + 顶部一条高光 + 底部一条发丝分隔线。

什么时候可以改成玻璃：内容真能滚到顶栏下面（把滚动容器挪到 `.yg2-app`），
否则先别改。

## 8. 动效

| 场景 | 时长 | 缓动 |
| --- | --- | --- |
| hover 提亮 | `--yg2-dur-1` (120ms) | `--yg2-ease-out` |
| 按下 | 90ms | ease-out |
| 弹层进出 | 180–240ms | `--yg2-ease-emphasis`（不要弹跳） |
| 光场漂移 | 34–120s | linear / ease-in-out alternate |

- 指针跟随的柔光用 rAF + 线性插值写 CSS 变量，不在事件里直接改样式。
- `prefers-reduced-motion: reduce` → 关掉漂移、指针光、过渡（glass.css 统一收口）。
- `prefers-reduced-transparency: reduce` → 全部玻璃退成 `--yg2-surface` 实色，并移除镜面边。

## 9. 性能

- 每个玻璃面 = 一个 backdrop-filter 层。列表里逐行玻璃会直接打穿 GPU 预算，
  **列表不要玻璃**，只在容器上做一层。
- 卡片默认 `blur(18px)`，不要超过 24px：模糊半径越大采样核越大，收益递减。
- 光场只用 `radial-gradient`，不要 `filter: blur()`（会溢出、会重绘）。

## 10. 加一个新玻璃面（三步）

1. 优先复用：`class="yg2-glass-surface"`（+ `--chrome` / `--quiet`）即可，无需写 CSS。
2. 需要新厚度：先在 tokens 里加一档 `--yg2-lens-xxx-*`（浅/深各一套），不要在组件里写死 rgba。
3. 记得在 `glass.css` 的 §8 降级块里登记它（`@supports not (backdrop-filter)` 与
   `prefers-reduced-transparency`），否则无障碍场景会露馅。

## 11. 怎么验收（本项目没有视觉工具时的做法）

- 截图：`.liquid-glass-lab/site/shoot.mjs`（CDP 直连，支持 theme / dpr / 视口 / 滚动 / hover / 采样）。
- 采样：`probe:[selector]` 会打印 computed `background / backdrop-filter / box-shadow / border`，
  用来确认「这一层到底吃没吃到令牌」，比肉眼看截图可靠。
- 数值断言：玻璃外 vs 玻璃内的 `sd`（结构被抹平）、边缘 vs 中心的 luma（rim 增益）、
  深色下玻璃内部 luma（防"亮蓝玻璃灰字"）。

## 12. 列表项与分页器（实测后的结论）

- **列表项（票券 / 表格行 / 日志条目）不做玻璃**，但材质语言必须和卡片一致：
  用 `--yg2-lens-panel-fill` 的实色渐变 + `inset 0 1px 0 var(--yg2-lens-matte-bloom)` 顶光
  + `inset 0 0 0 1px var(--yg2-lens-matte-hairline)` 发丝内边 + `--yg2-lens-matte-cast` 投影。
  参考 `src/components/activity/ZActivityList.vue` 的 `.yg2-ticket`。
- **分页器保持品牌色实心当前页**（`src/components/utils/ZPagination.vue` 的 `.yg-pg__thumb`，
  滑动的实心块 + 品牌色投影）。它是页面上少数「可以重」的元素，属于刻意强调；
  换成玻璃底反而会让"当前页"这个状态消失。`glass.css` 里的 `.el-pagination` 规则只为
  真正使用 Element Plus 分页的页面备用。
- 浮层（`el-popper` / `el-select__popper` / `el-message` / 用户菜单）吃 `--yg2-lens-overlay-*`，
  `el-message` 实测 `backdrop-filter: blur(36px) saturate(1.75)`，深浅两色都成立。

## 13. 踩过的坑（写令牌时最容易犯）

- **令牌插错块**：`tokens.css` 里浅色在 `:root`、深色在 `[data-theme="dark"]`。
  按注释字符串（如 `/* L1 轻玻璃 */`）做 sed 替换会把深色值也插进 `:root`，
  于是浅色页面用上深色 fill——表现为"浅色下票券/面板变黑板、深字压深底"。
  改完必须 `grep -n "--yg2-lens-xxx" tokens.css` 确认两条分别落在两个块里。
- **选择器 `[data-theme="dark"][data-ambient="..."]` 永远不匹配**：`data-theme` 在 `<html>`，
  `data-ambient` 在 `.yg2-ambient`，是后代关系，要写成 `[data-theme="dark"] [data-ambient="..."]`。
- **scoped 样式优先级**：组件里的 `scoped` 规则编译成 `.class[data-v-xxx]`（0,2,0），
  全局只写 `.class`（0,1,0）打不过。要么改组件自身（推荐，如登录卡/关于卡），
  要么用 `.parent .child`（0,2,0，靠加载顺序）或加元素名 `.parent section.child`（0,2,1）。

## 14. 浮层（dialog / drawer / popconfirm）实测结论

- 三件套都吃 `--yg2-lens-overlay-*`：`fill` 93%→85%（浅）/ 94%→90%（深）、
  `blur 36/38px`、rim + cast。**弹窗不做全景玻璃**——它是"读字 + 填表"的地方，
  透明度只用来和背后的页面区分层次，不能用来看风景。
- 遮罩（`.el-overlay`）是背景层，可以半透明：浅 `rgba(15,23,42,.34)` + `blur(4px)`，
  深 `rgba(4,6,10,.58)` + `blur(4px)`。**浅色遮罩别低于 30%**，否则白底页面上
  弹窗会"浮不起来"（实测 22% 时对话框和背景糊成一片）。
- **抽屉单独走面板档**（`--yg2-lens-panel-fill` + `blur(20px)`），不走 overlay 档。
  原因：业务里会出现 `fullscreen` 抽屉（如"添加成员"，100% 宽高），85% 不透明度会让
  背后页面从空白处透上来，像没加载完。这是实测后改的，不是设计偏好。
  半宽抽屉（如导出记录）用同一档也不会错，只是少一点通透。
- 自检方法：临时建 `glass-harness/`（Vite MPA 入口，只 import 样式 + Element Plus，
  用 `?theme=dark` 手动挂 `html.dark`/`data-theme`），截完图就删掉。
  注意必须 import `yg/main.css` 与 `assets/main.css`：全局 `box-sizing: border-box`
  在那里，缺了它输入框会顶出弹窗（这是踩过的坑，不是组件 bug）。

## 15. 为什么没有封装 `GlassSurface.vue`

刻意没做。玻璃在这里是**一层 CSS 材质**，不是**一段行为**：

- 没有 JS 状态的玻璃面，用 `<div class="yg2-glass-surface">` 比组件更轻，且属性、
  `aria-*`、事件都能直接透传，不会因为包一层组件而丢语义或丢 ref。
- 需要行为的那部分，项目里已经有两个现成的：`src/utils/liquidPointer.ts`（指针光）
  和 `src/utils/cardPointer.ts`（卡片聚光），都是事件委托，不依赖组件包裹。
- 真正需要组件的地方是**采样 + WebGL**（`src/ui/glass/LiquidGlass.vue`），
  那是有状态的，所以它才是组件。

结论：**材质用 token + 工具类，行为用现成的指令/工具；只有当某个玻璃面需要自己
维护状态（采样、动画、指针）时才封装组件。** 这条同样适用于以后新增的玻璃面。

## 16. 加载态：骨架屏，不要遮罩

**规则：所有面板/表格/列表的加载态用骨架屏，不用 `v-loading` 遮罩。**

原因不是审美偏好：遮罩是"把已有内容压暗"，骨架屏是"告诉用户这里将出现什么"。
在玻璃材质上尤其明显——遮罩是一层半透明黑，会直接吃掉刚做好的顶光与镜面边，
看起来像蒙了灰；骨架屏不动材质，只铺扫光条。

### 16.1 两个现成组件

| 组件 | 用在 | 关键 props |
|---|---|---|
| `src/ui/ux/Skeleton.vue` | 段落 / 卡片正文 / 弹窗内容 | `lines`、`height`、`width`、`radius` |
| `src/ui/ux/SkeletonTable.vue` | 表格 | `rows`、`header`、`cols` |

`SkeletonTable` 每行按列分块（默认 `[22,18,20,14,12]`），不要写成一条通栏灰条——
通栏灰条读起来像列表项，不像表格。

### 16.2 写法

```vue
<SkeletonTable v-if="loading" :rows="6" />
<ElTable v-else :data="rows" />
```

分页器要跟着 `v-if="!loading"`，否则加载时会先闪一下空分页器。

```vue
<ZPagination v-if="!loading" … />
```

异步组件的"首屏"要单独给骨架，别让页面空着：

```vue
<div v-if="loading && !activity" class="yg2-page">
  <Skeleton :lines="8" height="16px" />
</div>
<div v-else-if="activity" class="yg2-page">…</div>
```

### 16.3 配色必须走 token

骨架条的底色与扫光在深浅两色下语义相反，写死颜色一定出错：

| token | 浅色 | 深色 |
|---|---|---|
| `--yg2-skeleton-base` | `#e8ebf1` | `rgba(255,255,255,.07)` |
| `--yg2-skeleton-sheen` | `rgba(255,255,255,.9)` | `rgba(255,255,255,.16)` |

深色下曾经用 `--yg2-surface-sunken` 当底色：它和面板底几乎同色，扫光比底色还暗，
整片骨架是糊的。改令牌后扫光才亮起来。

### 16.4 例外

提交态（`saving`）保留按钮自身的 `:loading`，不再挂表单级遮罩：
按钮转圈已经说明了"正在提交"，再压一层遮罩只会让用户以为界面卡死。

## 17. 刷新控件：只用 `.yg2-icon-btn`

"刷新"是**面板级操作**，不是页面级操作，所以统一成一个小圆角图标按钮：

```vue
<button class="yg2-icon-btn" type="button" :disabled="loading" title="刷新" aria-label="刷新">
  <ElIcon><Refresh /></ElIcon>
</button>
```

- 尺寸 34×34，材质走 `control` 档（和分段控件、图标按钮同一层）。
- **必须**给 `title` + `aria-label`：图标按钮没有文字，读屏和悬停提示都靠它。
- 面板标题已经说明了刷什么，按钮内不要再放"刷新"两个字。
- 带文案的按钮（如"刷新主机快照"）不要改成图标按钮——那里文字是主语的一部分。

一个面板里的"刷新"要刷**这个面板里的全部数据**，不是只刷第一块。
`src/views/user/UserHome.vue` 的做法可以抄：

```ts
async function refreshAll() {
  await Promise.all([user.refreshUser(), timeRings.value?.refresh()])
}
```
