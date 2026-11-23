# ZVMS Pro 设计原则

本文档定义了 ZVMS Pro 应用界面的视觉语言与技术规范，确保产品体验的一致性与品质。

## 核心理念

ZVMS Pro 的设计灵感源自 Apple 的账户与产品界面——Apple ID、iCloud、Wallet、Health：**克制、清晰、有用**。这不是营销页面，而是志愿者每天使用的工作台。我们追求一目了然的实用性，通过精准的排版、液态玻璃材质与真实的光影营造专业而温暖的日常体验。

### 设计支柱

- **一目了然**：关键信息在第一屏可见，用户无需滚动就能理解状态
- **身份优先**：用户首先看到自己是谁，然后是时长与待办
- **克制的材质**：液态玻璃只用于导航、按钮、时长面板与工作列表；身份卡片用纯白
- **真实的光**：一个光源方向，柔和的氛围光晕，可信的阴影深度
- **安静的运动**：有意义的互动反馈，避免炫技式动画

## 视觉原则

### 画布与层级 (Canvas & Hierarchy)

ZVMS Pro 使用 Apple 式的浅灰画布 + 白色表面的层级系统：

```css
--color-bg-canvas: #f5f5f7;    /* 应用画布 */
--color-bg-elevated: #ffffff;  /* 卡片、面板 */
--color-bg-sunken: #fafafa;    /* 内嵌区域、次要表面 */
```

**规则**：
- 页面底色是 `#f5f5f7`，营造柔和的背景
- 身份卡片、总时长锚点使用纯白 `#ffffff` 表面
- 液态玻璃面板（导航、时长、工作列表）使用半透明渐变
- 白色表面使用极细的发丝线边框：`rgb(0 0 0 / 0.06)`
- 阴影非常克制：静态卡片几乎不可见，hover 时才轻微抬升

### 光影系统 (Light System)

所有页面共享**一个微妙的光源**：从上方照射，营造柔和的氛围而非戏剧性效果。

```css
/* 环境光晕 — 非常克制，仅在顶部创建轻微的氛围 */
body::before {
  background: radial-gradient(
    ellipse 1400px 900px at 50% 0%,
    rgb(0 113 227 / 0.06),
    transparent 70%
  );
}
```

**规则**：
- 光源非常微妙，不应引人注意
- 玻璃材质的高光边缘应与光源方向一致
- 阴影方向应反映光源位置（顶部光源 = 向下的阴影）

### 液态玻璃材质 (Liquid Glass)

液态玻璃是 ZVMS Pro 的标志材质，用于导航、按钮、时长面板与工作列表。这是基于研究的权威配方。

**完整配方**：
```css
.glass-panel {
  position: relative;
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.5) 0%,
    rgba(207, 234, 255, 0.38) 54%,
    rgba(255, 255, 255, 0.24) 100%
  );
  /* 先写 -webkit- 再写标准属性：构建器会把重复声明折叠，顺序反了会只剩前缀版本 */
  -webkit-backdrop-filter: blur(18px) saturate(190%) brightness(1.05);
  backdrop-filter: blur(18px) saturate(190%) brightness(1.05);
  border: 1px solid rgba(255, 255, 255, 0.78); /* 高光边缘 */
  border-radius: var(--radius-xl); /* 28px */
  box-shadow:
    0 20px 50px rgba(28, 68, 120, 0.09),
    0 2px 6px rgba(28, 68, 120, 0.04),
    inset 0 1px 0 rgba(255, 255, 255, 0.94),
    inset 0 -1px 0 rgba(255, 255, 255, 0.32);
  isolation: isolate;
}

/* 顶部高光 — 模拟光线折射 */
.glass-panel::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 50%;
  background: linear-gradient(
    180deg,
    rgba(255, 255, 255, 0.8) 0%,
    rgba(255, 255, 255, 0) 50%
  );
  border-radius: var(--radius-xl) var(--radius-xl) 0 0;
  pointer-events: none;
  opacity: 0.4;
}

/* 降级支持 */
@supports not (backdrop-filter: blur(14px)) {
  .glass-panel {
    background: rgba(255, 255, 255, 0.85);
  }
}
```

**玻璃按钮**（带移动光泽）：
```css
.glass-button {
  position: relative;
  background: linear-gradient(
    135deg,
    rgba(0, 113, 227, 0.12) 0%,
    rgba(0, 113, 227, 0.08) 100%
  );
  -webkit-backdrop-filter: blur(8px) saturate(180%);
  backdrop-filter: blur(8px) saturate(180%);
  border: 1px solid rgba(255, 255, 255, 0.82);
  border-radius: var(--radius-md);
  color: var(--color-brand);
  overflow: hidden;
  isolation: isolate;
}

/* 顶部高光 */
.glass-button::before {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(
    180deg,
    rgba(255, 255, 255, 0.8) 0%,
    rgba(255, 255, 255, 0) 50%
  );
  pointer-events: none;
  opacity: 0.3;
}

/* 对角光泽 — 仅在 hover 时移动 */
.glass-button::after {
  content: '';
  position: absolute;
  inset: 0;
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0) 0%,
    rgba(255, 255, 255, 0.3) 50%,
    rgba(255, 255, 255, 0) 100%
  );
  opacity: 0;
  transform: translateX(-100%);
  transition: all 350ms cubic-bezier(0.4, 0, 0.2, 1);
  pointer-events: none;
}

.glass-button:hover {
  transform: scale(1.01);
}

.glass-button:hover::after {
  opacity: 1;
  transform: translateX(100%);
}

.glass-button:active {
  transform: scale(0.98);
}
```

**允许使用场景**：
- ✅ 全局导航栏（sticky glass nav）
- ✅ 导航栏图标按钮（搜索、通知、头像）
- ✅ 志愿时长面板（三类时长的容器）
- ✅ 工作列表面板（待审核活动、最近记录）
- ✅ 所有操作按钮（编辑资料、明细、刷新）

**禁止事项**：
- ❌ 不要在身份卡片或总时长锚点用玻璃（用纯白）
- ❌ 不要嵌套半透明表面（玻璃套玻璃）
- ❌ 不要在玻璃内的列表行再用玻璃（用透明背景 + 发丝线分隔）

**性能注意**：
- `backdrop-filter` 模糊限制在 8–18px
- 不要动画 `backdrop-filter` 属性
- 使用 `isolation: isolate` 防止嵌套混合问题
- 为不支持 `backdrop-filter` 的浏览器提供降级

### 形状与圆角 (Shape & Radius)

Apple 式的适度圆角——柔和但不过度圆润。

```css
--radius-xs: 6px;   /* 小标签、徽章 */
--radius-sm: 10px;  /* 导航按钮、小图标按钮 */
--radius-md: 14px;  /* 标准按钮、输入框 */
--radius-lg: 22px;  /* 身份卡片、白色表面 */
--radius-xl: 28px;  /* 玻璃面板、时长面板 */
--radius-pill: 999px; /* 胶囊按钮、状态标签 */
```

**规则**：
- 身份卡片、总时长使用 `--radius-lg` (22px)
- 玻璃面板使用 `--radius-xl` (28px)
- 图标按钮使用 `--radius-sm` (10px)
- 状态标签使用 `--radius-pill`

## 色彩系统 (Color System)

ZVMS Pro 使用**高度克制的调色板**：一个品牌蓝作为稀缺的强调色，大量的灰阶构建层级。

### 主色调

```css
/* 品牌色 — 仅用于关键操作与强调 */
--color-brand: #0071e3;         /* ZVMS 天空蓝 */
--color-brand-hover: #0077ed;   /* hover 状态 */
--color-brand-active: #006edb;  /* active 状态 */

/* 文本层级 */
--color-text-primary: #1d1d1f;    /* 标题、正文 */
--color-text-secondary: #6e6e73;  /* 辅助信息 */
--color-text-tertiary: #86868b;   /* 元数据、标签 */
--color-text-inverse: #f5f5f7;    /* 深色背景上的文字 */

/* 背景层级 */
--color-bg-canvas: #f5f5f7;    /* 应用画布 */
--color-bg-elevated: #ffffff;  /* 卡片、面板 */
--color-bg-sunken: #fafafa;    /* 内嵌区域 */

/* 边框 */
--color-border-hairline: rgb(0 0 0 / 0.06); /* 极细发丝线 */
--color-border-subtle: rgb(0 0 0 / 0.08);   /* 轻微分隔 */
--color-border-moderate: rgb(0 0 0 / 0.12); /* 明显分隔 */
```

### 使用规则

- **品牌蓝的使用场景**：玻璃按钮、进度条、用户头像背景、focus outline、已确认状态
- **禁止**：不要大面积使用品牌色；不要在一屏内出现超过 3-4 个蓝色主要元素
- **发丝线边框**：白色卡片在 `#f5f5f7` 画布上使用 `--color-border-hairline` (0.06 opacity)
- **语义色**：
  - 成功/已确认：`#34c759` (rgba(52, 199, 89))
  - 待确认/进行中：`#0071e3` (品牌蓝)
  - 警告/待审核：`#ff9f0a` (rgba(255, 159, 10))
  - 错误：`#ff453a` (rgba(255, 69, 58))

## 字体排印 (Typography)

使用系统字体栈以获得最佳性能与本地化体验。中文优先显示 PingFang SC。

### 字体栈

```css
--font-system: 'SF Pro Display', 'SF Pro Text', -apple-system, BlinkMacSystemFont,
  'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
```

### 字阶系统 (Type Scale)

```css
--font-size-greeting: clamp(56px, 6vw, 72px);  /* 首屏问候 */
--font-size-stats: clamp(40px, 4vw, 56px);     /* 统计数据数值 */
--font-size-title-3: clamp(24px, 3vw, 36px);   /* 身份姓名 */
--font-size-headline: 21px;                     /* 面板标题 */
--font-size-body-large: 19px;                   /* 日期显示 */
--font-size-body: 17px;                         /* 标准正文 */
--font-size-callout: 15px;                      /* 辅助信息 */
--font-size-caption: 13px;                      /* 标签、元数据 */
```

### 行高与字距

```css
--line-height-tight: 1.05;   /* 大标题（问候语、统计数值） */
--line-height-snug: 1.2;     /* 标题 */
--line-height-normal: 1.5;   /* 正文 */
--line-height-relaxed: 1.6;  /* 说明文本 */

--letter-spacing-tight: -0.02em;  /* 大标题紧缩 */
--letter-spacing-normal: -0.01em; /* 默认 */
```

### 使用规则

- **问候语**：使用 `--font-size-greeting` + `--line-height-tight` + 字重 600
- **统计数值**：使用 `--font-size-stats` + `--line-height-tight` + 字重 600
- **面板标题**：使用 `--font-size-headline` (21px) + 字重 600
- **正文最大宽度**：中文段落限制在 `60-70ch` 以保证可读性
- **中文排版**：避免单字孤行 (orphan)，使用 `text-wrap: balance` 辅助

## 间距系统 (Spacing)

基于 4px 的 12 级间距阶梯。

```css
--space-1: 4px;
--space-2: 8px;
--space-3: 12px;
--space-4: 16px;
--space-5: 24px;
--space-6: 32px;
--space-7: 48px;
--space-8: 64px;
--space-9: 80px;
--space-10: 96px;
--space-11: 128px;
--space-12: 160px;
```

### 使用规则

- **页面内边距**：`clamp(20px, 4vw, 40px)`
- **区块垂直间距**：`--space-7` 至 `--space-10` (48-96px)
- **卡片 padding**：`--space-6` (32px)
- **玻璃面板 padding**：`--space-6` (32px)
- **内容最大宽度**：`1200px`

### 响应式断点

```css
/* 移动优先 */
/* 默认: 320px - 767px */

@media (min-width: 768px) {
  /* 平板：身份行 7/5 分栏，时长 2 列，工作列表 2 列 */
}

@media (min-width: 1024px) {
  /* 桌面：身份行 7/5，时长 3 列，工作列表 7/5 */
}
```

## 阴影系统 (Shadows)

极度克制的阴影——Apple 式的"几乎看不见"。

```css
--shadow-sm: 0 1px 1px rgb(0 0 0 / 0.04);
--shadow-md: 0 2px 4px rgb(0 0 0 / 0.04), 0 1px 2px rgb(0 0 0 / 0.02);
--shadow-lg: 0 4px 12px rgb(0 0 0 / 0.06), 0 1px 3px rgb(0 0 0 / 0.04);
--shadow-xl: 0 8px 24px rgb(0 0 0 / 0.08), 0 2px 6px rgb(0 0 0 / 0.04);
--glass-shadow: 0 20px 50px rgba(28, 68, 120, 0.09), 0 2px 6px rgba(28, 68, 120, 0.04), inset 0 1px 0 rgba(255, 255, 255, 0.94), inset 0 -1px 0 rgba(255, 255, 255, 0.32);
```

**使用建议**：
- 静态白色卡片：几乎无阴影，依靠发丝线边框分隔
- Hover 抬升效果：从无阴影过渡到 `--shadow-md`
- 玻璃表面：`--glass-shadow`
- 玻璃按钮 hover：`0 4px 12px rgba(0, 113, 227, 0.15)`（品牌色光晕）

## 动效系统 (Motion)

ZVMS Pro 的动效追求「Q弹」(springy) 的手感——快速响应但有自然的回弹。

### 缓动函数 (Easing)

```css
--ease-spring: cubic-bezier(0.34, 1.56, 0.64, 1);   /* Q弹回弹，用于按压/悬停 */
--ease-smooth: cubic-bezier(0.4, 0, 0.2, 1);        /* 平滑过渡 */
--ease-out: cubic-bezier(0, 0, 0.2, 1);             /* 淡出/离开 */
--ease-in-out: cubic-bezier(0.4, 0, 0.6, 1);        /* 进入-离开 */
```

### 持续时间

```css
--duration-fast: 200ms;    /* 小元素、即时反馈 */
--duration-base: 350ms;    /* 标准过渡 */
--duration-slow: 500ms;    /* 大区块、页面入场 */
```

### 使用规则

- **只动画 transform 和 opacity**：避免触发 layout 或 paint 的属性
- **按压反馈**：
  ```css
  .button:hover {
    transform: scale(1.01);
    transition: all var(--duration-fast) var(--ease-spring);
  }
  .button:active {
    transform: scale(0.98);
  }
  ```
- **玻璃光泽移动**：仅在 hover 时 `translateX(-100%)` → `translateX(100%)`
- **入场序列**：元素分组错开 100ms 出现
  ```css
  .greeting { transition-delay: 0ms; }
  .identity-row { transition-delay: 100ms; }
  .hours-panel { transition-delay: 200ms; }
  .work-lists { transition-delay: 300ms; }
  ```

### 无障碍动效

**必须**支持 `prefers-reduced-motion`：

```css
@media (prefers-reduced-motion: reduce) {
  :root {
    --ease-spring: cubic-bezier(0, 0, 1, 1);
    --duration-fast: 10ms;
    --duration-base: 10ms;
    --duration-slow: 10ms;
  }

  .glass-button::after {
    display: none; /* 禁用移动光泽 */
  }
}
```

## 组件规范

### 导航栏 (Navigation)

签入应用导航：slim 56px 高度，液态玻璃材质，产品 chrome 而非营销 header。

```css
.site-nav {
  position: sticky;
  top: 0;
  height: 56px;
  background: var(--glass-bg);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border-bottom: 1px solid var(--glass-border);
  box-shadow: var(--glass-shadow);
}
```

**组成**：
- 左侧：ZVMS wordmark
- 中左：首页、活动、时长、组织
- 右侧：搜索 icon、通知 icon、头像菜单（都是玻璃按钮）

### 问候区 (Greeting)

用户登录后看到的第一个元素：时间问候 + 姓名 + 日期。

```css
.greeting__title {
  font-size: var(--font-size-greeting); /* 56-72px */
  font-weight: 600;
  line-height: var(--line-height-tight);
  letter-spacing: -0.02em;
}

.greeting__date {
  font-size: var(--font-size-body-large); /* 19px */
  color: var(--color-text-secondary);
}
```

### 身份卡片 (Identity Card)

Apple ID 风格的账户表面：头像、姓名、学号、组织、角色、编辑资料。

```css
.identity-card {
  background: var(--color-bg-elevated); /* 纯白 */
  border: 1px solid var(--color-border-hairline);
  border-radius: var(--radius-lg); /* 22px */
  padding: var(--space-6);
}
```

**组成**：
- 64px 圆形头像（品牌蓝背景 + 姓氏）
- 姓名（title-3 大小）+ 学号（callout 大小）
- 组织 + 角色（带图标）
- 编辑资料按钮（玻璃按钮）

### 总时长锚点 (Total Hours Anchor)

大数值显示累计记录总和，作为视觉锚点。

```css
.total-hours {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-hairline);
  border-radius: var(--radius-lg);
  padding: var(--space-6);
  text-align: center;
}

.total-hours__value {
  font-size: var(--font-size-stats); /* 40-56px */
  font-weight: 600;
  line-height: var(--line-height-tight);
  letter-spacing: -0.02em;
}
```

### 志愿时长面板 (Hours Panel)

液态玻璃容器，展示三类时长及折算规则。

```css
.hours-panel {
  background: var(--glass-bg);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl); /* 28px */
  box-shadow: var(--glass-shadow);
  padding: var(--space-6);
}
```

**组成**：
- 标题 + 明细/刷新按钮（玻璃按钮）
- 三个时长类别（校内、校外、社会实践）：
  - 实际值（大数值 40-48px）
  - 标准要求
  - 折算后值（带 +bonus 标记）
  - 进度百分比标签（绿色 >100%，橙色 <100%）
  - 进度条
- 折算规则说明（caption 大小）

**折算逻辑**：
- 校外 > 15h：每 2h 折算 1h 校内，`round((off - 15) / 2)`
- 校内 > 25h：每 3h 折算 1h 校外，`round((on - 25) / 3)`

### 工作列表 (Work Lists)

液态玻璃容器，展示待审核活动和最近记录。

```css
.work-list {
  background: var(--glass-bg);
  -webkit-backdrop-filter: var(--glass-blur);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--glass-shadow);
  padding: var(--space-6);
}
```

**组成**：
- 标题（headline 大小）
- 列表行（透明背景 + 发丝线分隔符）：
  - 活动名称（body 大小，font-weight 500）
  - 元数据（组织/日期/时长）
  - 状态标签（pill 形状，语义色）

**不使用 `<table>`**：用发丝线分隔的行（`border-bottom`），最后一行无边框。

### 图标 (Icons)

手写的内联 SVG，1.5px 描边，线条风格：

```html
<!-- 搜索 icon -->
<svg width="20" height="20" viewBox="0 0 20 20" fill="none" aria-hidden="true">
  <circle cx="9" cy="9" r="6" stroke="currentColor" stroke-width="1.5"/>
  <path d="M13.5 13.5L17 17" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
</svg>
```

**规则**：
- 统一使用 `currentColor` 继承文本颜色
- `aria-hidden="true"` 对辅助技术隐藏装饰性图标
- 描边宽度 1.5px
- 不要使用 icon 字体或 npm 包

## 性能预算 (Performance Budget)

ZVMS Pro 不使用任何第三方动画库、icon 库或重型资产。

### 硬性约束

- ❌ **禁止**：GSAP, Framer Motion, Anime.js 等动画库
- ❌ **禁止**：Font Awesome, Material Icons 等 icon 字体或包
- ❌ **禁止**：网络字体 (使用系统字体栈)
- ❌ **禁止**：大尺寸光栅图片
- ✅ **允许**：手写的内联 SVG icon (< 2KB)
- ✅ **允许**：CSS 渐变、`backdrop-filter`、`clip-path` 等 CSS 特性
- ✅ **允许**：轻量级的 Vue transition 组件

### 目标

- **首次内容绘制 (FCP)**：< 1.5s (3G 网络)
- **最大内容绘制 (LCP)**：< 2.5s
- **帧率**：保持 60fps，中端设备无卡顿

## 无障碍 (Accessibility)

ZVMS Pro 遵循 WCAG 2.1 AA 标准。

### 必要规范

1. **语义化 HTML**：
   - 每页一个 `<h1>`（问候语）
   - 逻辑的标题层级 (h1 → h2 → h3)
   - 使用 `<nav>`, `<main>`, `<section>`, `<article>` 等地标

2. **键盘可访问**：
   - 所有交互元素可通过 Tab 键访问
   - 使用 `:focus-visible` 提供清晰的焦点样式
   - 移动菜单支持 Escape 关闭

3. **对比度**：
   - 正文文本至少 4.5:1 (`#1d1d1f` on `#ffffff` = 16.1:1 ✓)
   - 辅助文本至少 4.5:1 (`#6e6e73` on `#ffffff` = 4.6:1 ✓)
   - 玻璃材质上的文本需验证对比度

4. **ARIA 标签**：
   ```html
   <nav aria-label="主导航">
   <button aria-expanded="false" aria-controls="menu-id">
   <svg aria-hidden="true">
   ```

5. **触摸目标**：
   - 最小尺寸 44×44px (移动端)
   - 按钮之间至少 8px 间距

### Focus 样式

```css
:focus {
  outline: none;
}

:focus-visible {
  outline: 2px solid var(--color-brand);
  outline-offset: 3px;
  border-radius: var(--radius-xs);
}
```

## 布局规范

### 签入首页 (Signed-in Home)

阅读顺序：**身份 → 时长 → 工作**。

```
┌─ 问候语 + 日期 ────────────────────────────┐
│ 下午好，知远。                               │
│ 2026年11月18日 星期三                        │
└─────────────────────────────────────────┘

┌─ 身份卡片 (7 cols) ──────┬─ 总时长 (5 cols) ┐
│ 头像 + 姓名 + 学号         │   累计记录          │
│ 组织 + 角色               │   54.5h           │
│ [编辑资料]                │   包含三类时长       │
└─────────────────────────┴──────────────────┘

┌─ 志愿时长（玻璃面板，full width）─────────────┐
│ 志愿时长                    [明细] [刷新]      │
│ ┌─校内─┐ ┌─校外─┐ ┌─社会实践─┐            │
│ │28.0h │ │17.0h │ │  9.5h  │             │
│ │116% │ │120% │ │  53%   │             │
│ └─────┘ └─────┘ └────────┘              │
│ 折算规则说明...                             │
└─────────────────────────────────────────┘

┌─ 待审核活动 (7 cols) ────┬─ 最近记录 (5 cols) ┐
│ 待审核活动                │ 最近记录            │
│ • 社区环保宣传 [待审核]    │ • 市图书馆 [待确认]  │
│ • 敬老院陪伴 [待审核]      │ • 校园整理 [已确认]  │
│ • 校园开放日 [待审核]      │ • 社区家教 [已确认]  │
└─────────────────────────┴──────────────────┘
```

**响应式**：
- 移动端：单列，身份 → 总时长 → 时长面板（单列）→ 工作列表（单列）
- 平板：身份/总时长 7/5，时长 2 列，工作列表 2 列
- 桌面：身份/总时长 7/5，时长 3 列，工作列表 7/5

## Do / Don't 示例

### ✅ Do

- **液态玻璃权威配方**：使用文档中的完整配方（渐变背景、blur、高光、光泽）
- **身份优先**：用户首先看到自己是谁
- **克制的玻璃**：导航、按钮、时长面板、工作列表；身份卡片用纯白
- **发丝线分隔**：列表行用透明背景 + 发丝线，不嵌套玻璃
- **大数值显示**：总时长 40-56px，时长类别 40-48px
- **真实的材质感**：玻璃有高光、阴影、光泽移动
- **实用优先**：信息密度足够让用户一眼理解状态

### ❌ Don't

- **不要在身份卡片用玻璃**：用纯白 + 发丝线
- **不要嵌套玻璃**：列表行不再套玻璃，用透明背景
- **不要过度玻璃**：不是每个元素都用玻璃材质
- **不要粗重的阴影**：静态卡片几乎无阴影，依靠发丝线分隔
- **不要滥用动画**：光泽只在 hover 时移动一次，不无限循环
- **不要低对比度**：玻璃上的文本对比度必须通过 WCAG AA

## 实现清单

开发新页面时的自查：

- [ ] 使用 `tokens.css` 中的液态玻璃变量
- [ ] 玻璃面板包含 `::before` 高光和 `::after` 光泽
- [ ] 按钮有 hover scale(1.01) 和 active scale(0.98)
- [ ] 页面有一个 `<h1>` 且标题层级正确
- [ ] 交互元素有 `:focus-visible` 样式
- [ ] 文本对比度满足 WCAG AA (4.5:1)
- [ ] 按钮尺寸 >= 44px (移动端)
- [ ] 白色卡片使用发丝线边框 (`--color-border-hairline`)
- [ ] 动画使用 `transform`/`opacity`，避免 layout 属性
- [ ] 支持 `prefers-reduced-motion`
- [ ] 响应式测试：390px, 768px, 1024px, 1440px
- [ ] 没有水平滚动条
- [ ] 中文标题没有单字孤行 (orphan)
- [ ] 使用语义化的 HTML5 地标元素
- [ ] 使用系统字体，没有引入外部字体或 icon 库
- [ ] 性能：没有大图片、重型库、无限动画循环
- [ ] 为 `backdrop-filter` 提供 `@supports` 降级

---

**设计原则版本**：v3.0 (Signed-in Home with Liquid Glass)
**最后更新**：2026-11-18
