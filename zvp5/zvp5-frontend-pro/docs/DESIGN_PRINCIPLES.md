# ZVMS Pro 设计原则

本文档定义了 ZVMS Pro 应用界面的视觉语言与技术规范，确保产品体验的一致性与品质。

## 核心理念

ZVMS Pro 的设计灵感源自 Apple 的账户与产品界面——Apple ID、iCloud、Wallet、Health：**克制、清晰、有用**。这不是营销页面，而是志愿者每天使用的工作台。我们追求一目了然的实用性，通过精准的排版、适度的材质与真实的光影营造专业而温暖的日常体验。

### 设计支柱

- **一目了然**：关键信息在第一屏可见，用户无需滚动就能理解状态
- **一个焦点对象**：每个页面有一个主要视觉锚点，其余元素退居辅助
- **克制的材质**：玻璃只用于导航与焦点元素，大量使用白色卡片与发丝线分隔
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
- 卡片、stats、bento grid 使用纯白 `#ffffff` 表面
- 白色表面使用极细的发丝线边框：`rgb(0 0 0 / 0.06-0.08)`
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

### 玻璃材质 (Liquid Glass)

玻璃材质是 ZVMS Pro 的标志，但必须**极度克制使用**：仅导航栏与一个焦点元素（下一场活动 pass）。

**配方**：
```css
.glass-surface {
  background: var(--glass-bg); /* color-mix(in srgb, #f5f5f7 75%, transparent) */
  backdrop-filter: var(--glass-blur); /* blur(20px) saturate(180%) */
  border: 1px solid var(--glass-border); /* rgb(255 255 255 / 0.5) */
  border-radius: var(--radius-xl); /* 28px */
  box-shadow: var(--glass-shadow);
  position: relative;
}

/* 顶部高光边缘 — 模拟光线折射 */
.glass-surface::before {
  content: '';
  position: absolute;
  inset: -1px;
  background: linear-gradient(
    135deg,
    rgba(255, 255, 255, 0.6) 0%,
    rgba(255, 255, 255, 0) 50%
  );
  border-radius: var(--radius-xl);
  pointer-events: none;
  opacity: 0.8;
}
```

**允许使用场景**：
- ✅ 全局导航栏（sticky glass nav）
- ✅ 首屏焦点元素（下一场活动 pass）
- ✅ 浮动的操作按钮或控件（如需要）

**禁止事项**：
- ❌ 不要在 bento grid 的每个卡片都用玻璃
- ❌ 不要嵌套半透明表面（玻璃套玻璃）
- ❌ 不要在玻璃内使用半透明背景的子元素

### 形状与圆角 (Shape & Radius)

Apple 式的适度圆角——柔和但不过度圆润。

```css
--radius-xs: 6px;   /* 小标签、徽章 */
--radius-sm: 10px;  /* 按钮、输入框 */
--radius-md: 14px;  /* 中等卡片、对话框 */
--radius-lg: 22px;  /* 标准卡片 */
--radius-xl: 28px;  /* 玻璃表面、焦点区域 */
--radius-pill: 999px; /* 胶囊按钮、状态标签 */
```

**规则**：
- bento grid 卡片使用 `--radius-lg` (22px)
- 焦点 pass 使用 `--radius-xl` (28px)
- 不要把每个按钮都做成 pill 形状——只有状态标签和特殊 CTA
- 卡片内的小元素使用比卡片本身更小的圆角

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

- **品牌蓝的使用场景**：主要 CTA、"已报名"等重要状态、focus outline、用户头像背景
- **禁止**：不要大面积使用品牌色；不要在一屏内出现超过 3 个蓝色主要元素
- **发丝线边框**：白色卡片在 `#f5f5f7` 画布上使用 `--color-border-hairline` (0.06 opacity)
- **语义色**：如需成功/错误/警告状态，使用 iOS 系统色调：
  - 成功/招募中：`rgb(52 199 89)` 或 `#34c759`
  - 警告：`rgb(255 159 10)`
  - 错误：`rgb(255 69 58)`

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
--font-size-title-3: clamp(24px, 3vw, 36px);   /* 焦点 pass 标题 */
--font-size-headline: 21px;                     /* bento 卡片标题 */
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

--letter-spacing-tight: -0.02em;  /* 大标题紧缩 */
--letter-spacing-normal: -0.01em; /* 默认 */
```

### 使用规则

- **问候语**：使用 `--font-size-greeting` + `--line-height-tight` + 字重 600
- **统计数值**：使用 `--font-size-stats` + `--line-height-tight` + 字重 600，配合极小的标签文本
- **卡片标题**：使用 `--font-size-headline` (21px) + 字重 600
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
- **区块垂直间距**：`--space-8` 至 `--space-10` (64-96px)
- **卡片 padding**：`--space-5` 至 `--space-6` (24-32px)
- **bento grid gap**：`--space-5` (24px)
- **内容最大宽度**：`1440px`

### 响应式断点

```css
/* 移动优先 */
/* 默认: 320px - 767px */

@media (min-width: 768px) {
  /* 平板：bento grid 2 列 */
}

@media (min-width: 1024px) {
  /* 桌面：bento grid 12 列布局 */
}

@media (min-width: 1280px) {
  /* 大屏：焦点 pass 增加内边距 */
}
```

## 阴影系统 (Shadows)

极度克制的阴影——Apple 式的"几乎看不见"。

```css
--shadow-sm: 0 1px 1px rgb(0 0 0 / 0.04);
--shadow-md: 0 2px 4px rgb(0 0 0 / 0.04), 0 1px 2px rgb(0 0 0 / 0.02);
--shadow-lg: 0 4px 12px rgb(0 0 0 / 0.06), 0 1px 3px rgb(0 0 0 / 0.04);
--shadow-xl: 0 8px 24px rgb(0 0 0 / 0.08), 0 2px 6px rgb(0 0 0 / 0.04);
--glass-shadow: 0 4px 20px rgb(0 0 0 / 0.06), 0 1px 2px rgb(0 0 0 / 0.03);
```

**使用建议**：
- 静态 bento 卡片：几乎无阴影，依靠发丝线边框分隔
- Hover 抬升效果：从无阴影过渡到 `--shadow-md`
- 玻璃表面：`--glass-shadow`
- 焦点 pass 上的 CTA hover：`0 4px 12px rgb(0 113 227 / 0.2)`（品牌色光晕）

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
    transform: translateY(-1px);
    transition: all var(--duration-fast) var(--ease-spring);
  }
  .button:active {
    transform: scale(0.98);
  }
  ```
- **卡片悬停**：轻微抬升 `translateY(-2px)` + 阴影增强
- **入场序列**：元素分组错开 100ms 出现
  ```css
  .greeting { transition-delay: 0ms; }
  .pass { transition-delay: 100ms; }
  .stats { transition-delay: 200ms; }
  .bento { transition-delay: 300ms; }
  ```

### 滚动触发动画

使用 `IntersectionObserver` 触发元素的 `.visible` 类：

```typescript
const observer = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        const id = entry.target.getAttribute('data-section')
        if (id) {
          sectionsVisible.value[id] = true
        }
      }
    })
  },
  { threshold: 0.1, rootMargin: '-60px' }
)
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
}
```

## 组件规范

### 导航栏 (Navigation)

签到应用导航：slim 56px 高度，玻璃材质，产品 chrome 而非营销 header。

```css
.site-nav {
  position: sticky;
  top: 0;
  height: 56px;
  background: var(--glass-bg);
  backdrop-filter: var(--glass-blur);
  border-bottom: 1px solid var(--glass-border);
  box-shadow: var(--glass-shadow);
}
```

**组成**：
- 左侧：ZVMS wordmark
- 中左：首页、活动、时长、组织
- 右侧：搜索 icon、通知 icon、头像菜单

### 问候区 (Greeting)

用户登录后看到的第一个元素：时间问候 + 姓名 + 日期 + 上下文。

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

.greeting__context {
  font-size: var(--font-size-body);
  color: var(--color-text-tertiary);
}
```

### 焦点 Pass (Focal Activity)

玻璃材质的"票券"式表面，展示下一场志愿活动：

```css
.pass__inner {
  background: var(--glass-bg);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--glass-border);
  border-radius: var(--radius-xl); /* 28px */
  padding: var(--space-6);
  box-shadow: var(--glass-shadow);
  position: relative;
}

/* 光晕效果 */
.pass__inner::before {
  content: '';
  position: absolute;
  inset: -1px;
  background: linear-gradient(135deg,
    rgba(255, 255, 255, 0.6) 0%,
    rgba(255, 255, 255, 0) 50%
  );
  border-radius: var(--radius-xl);
  pointer-events: none;
  opacity: 0.8;
}
```

### 统计行 (Stats Row)

4 个并排的统计指标，大数值 + 小标签：

```css
.stat {
  background: var(--color-bg-elevated); /* 白色 */
  border: 1px solid var(--color-border-hairline);
  border-radius: var(--radius-lg); /* 22px */
  padding: var(--space-5);
  text-align: center;
}

.stat__value {
  font-size: var(--font-size-stats); /* 40-56px */
  font-weight: 600;
  line-height: var(--line-height-tight);
  letter-spacing: -0.02em;
}

.stat__label {
  font-size: var(--font-size-callout);
  color: var(--color-text-tertiary);
}
```

### Bento Grid

响应式网格布局：12 列桌面、2 列平板、1 列手机。

```css
.bento {
  display: grid;
  grid-template-columns: 1fr; /* 移动端 */
  gap: var(--space-5);
}

@media (min-width: 768px) {
  .bento {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (min-width: 1024px) {
  .bento {
    grid-template-columns: repeat(12, 1fr);
  }

  .bento-card--hours { grid-column: span 6; }
  .bento-card--pending { grid-column: span 6; }
  .bento-card--activities { grid-column: span 5; }
  .bento-card--cert { grid-column: span 4; }
  .bento-card--actions { grid-column: span 3; }
}
```

**Bento 卡片**：
```css
.bento-card {
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border-hairline);
  border-radius: var(--radius-lg);
  padding: var(--space-5);
  transition: all var(--duration-fast) var(--ease-smooth);
}

.bento-card:hover {
  box-shadow: var(--shadow-md);
}
```

### 图标 (Icons)

手写的内联 SVG，1.5–1.8px 描边，线条风格：

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
- 描边宽度 1.5–1.8px
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
   - 使用 `<nav>`, `<main>`, `<section>`, `<article>`, `<footer>` 等地标

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

## Do / Don't 示例

### ✅ Do

- **一个焦点对象**：焦点 pass 是视觉锚点，其余元素安静地支持
- **克制的玻璃**：只有导航与焦点 pass 使用玻璃，不是每个卡片
- **发丝线分隔**：白色卡片在浅灰画布上使用极细边框 (0.06-0.08 alpha)
- **大数值显示**：统计指标用 40-56px 大数值 + 13px 小标签
- **真实的材质感**：玻璃有高光、阴影、折射
- **实用优先**：信息密度足够让用户一眼理解状态

### ❌ Don't

- **不要到处使用玻璃**：bento 卡片不需要玻璃材质，用白色即可
- **不要粗重的阴影**：静态卡片几乎无阴影，依靠发丝线分隔
- **不要彩色 icon 方块**：不要在每个卡片放圆角彩色 icon 背景
- **不要把所有按钮做成 pill**：只有状态标签用 pill 形状
- **不要滥用动画**：不要让所有元素都动，不要无限循环
- **不要低对比度**：文本对比度必须通过 WCAG AA

## 实现清单

开发新页面时的自查：

- [ ] 使用 `tokens.css` 中的设计变量
- [ ] 页面有一个 `<h1>` 且标题层级正确
- [ ] 交互元素有 `:focus-visible` 样式
- [ ] 文本对比度满足 WCAG AA (4.5:1)
- [ ] 按钮尺寸 >= 44px (移动端)
- [ ] 玻璃材质只用于导航与焦点元素
- [ ] 白色卡片使用发丝线边框 (`--color-border-hairline`)
- [ ] 动画使用 `transform`/`opacity`，避免 layout 属性
- [ ] 支持 `prefers-reduced-motion`
- [ ] 响应式测试：390px, 768px, 1024px, 1440px
- [ ] 没有水平滚动条
- [ ] 中文标题没有单字孤行 (orphan)
- [ ] 使用语义化的 HTML5 地标元素
- [ ] 使用系统字体，没有引入外部字体或 icon 库
- [ ] 性能：没有大图片、重型库、无限动画循环

---

**设计原则版本**：v2.0 (App Surface)
**最后更新**：2026-11-17
