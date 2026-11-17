# ZVMS Pro 设计原则

本文档定义了 ZVMS Pro 的视觉语言与技术规范，确保产品体验的一致性与品质。

## 核心理念

ZVMS Pro 的设计灵感源自 Apple.com 的产品发布页面：**克制、清晰、深度**。我们追求「少即是多」的高端极简主义，通过精准的排版、光影与材质营造专业而温暖的氛围。

### 设计支柱

- **产品优先**：内容与功能是主角，设计为其服务而非喧宾夺主
- **编辑式排版**：大标题、慷慨留白、清晰层级
- **真实的光**：每个场景有统一的光源方向，材质有可信的深度
- **克制的动效**：有意义的互动反馈，避免炫技式动画

## 视觉原则

### 光影系统 (Light System)

所有页面共享**一个光源方向**：从左上方照射。这创造了视觉连贯性与空间深度。

**规则**：
- 每个页面/区域只有一个主要光源
- 使用 `radial-gradient` 在页面顶部或左上创建柔和的环境光晕
- 玻璃材质的高光边缘 (edge highlight) 应与光源方向一致
- 阴影方向应反映光源位置

```css
/* 环境光晕 — 固定在页面顶部 */
body::before {
  background: radial-gradient(
    ellipse 1400px 900px at 50% 0%,
    rgb(0 113 227 / 0.12),
    transparent 70%
  );
}
```

### 玻璃材质 (Liquid Glass)

ZVMS Pro 的标志性材质。玻璃需要**克制使用**：导航栏、一个主要的 hero 卡片、少量浮动控件。

**配方**：
```css
.glass-surface {
  background: var(--glass-bg); /* color-mix(in srgb, #f5f5f7 72%, transparent) */
  backdrop-filter: var(--glass-blur); /* blur(20px) saturate(180%) */
  border: 1px solid var(--color-border-subtle); /* rgb(0 0 0 / 0.08) */
  border-radius: var(--radius-xl); /* 24px */
  box-shadow: var(--glass-shadow);
  position: relative;
}

/* 顶部高光边缘 — 模拟光线折射 */
.glass-surface::before {
  content: '';
  position: absolute;
  top: 0;
  left: 5%;
  right: 5%;
  height: 1px;
  background: linear-gradient(
    90deg,
    transparent,
    var(--glass-border), /* rgb(255 255 255 / 0.6) */
    transparent
  );
  pointer-events: none;
}
```

**禁止事项**：
- ❌ 不要嵌套半透明表面（玻璃套玻璃）
- ❌ 不要在玻璃内使用半透明背景的子元素
- ❌ 不要在深色内容上使用浅色玻璃

**推荐用法**：
- ✅ 固定导航栏（sticky glass nav）
- ✅ Hero 区域的一个主要展示卡片
- ✅ 浮动的操作按钮或控件
- ✅ 模态对话框（未实现，但适用）

### 形状与圆角 (Shape & Radius)

Apple 式的克制圆角——足够柔和但不过度。

```css
--radius-xs: 6px;   /* 小标签、徽章 */
--radius-sm: 10px;  /* 按钮、输入框 */
--radius-md: 14px;  /* 卡片、对话框 */
--radius-lg: 18px;  /* 大卡片、面板 */
--radius-xl: 24px;  /* 玻璃表面、主要展示区域 */
--radius-pill: 999px; /* 胶囊按钮、标签 */
```

**规则**：
- 相关元素使用相同的圆角大小
- 卡片内的小元素使用比卡片本身更小的圆角
- 主要操作按钮使用 `--radius-md` 或 `--radius-pill`
- 不要混用太多不同的圆角尺寸

## 色彩系统 (Color System)

ZVMS Pro 使用**高度克制的调色板**：品牌蓝作为稀缺的强调色，大量的白、灰、近黑构建层级。

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
--color-bg-canvas: #ffffff;    /* 页面底色 */
--color-bg-elevated: #f5f5f7;  /* 卡片、面板 */
--color-bg-sunken: #fbfbfd;    /* 内嵌区域 */

/* 边框 */
--color-border-subtle: rgb(0 0 0 / 0.08);   /* 轻微分隔 */
--color-border-moderate: rgb(0 0 0 / 0.12); /* 明显分隔 */
```

### 使用规则

- **品牌蓝的使用场景**：主要 CTA、重要状态标签、数据可视化、icon accent、focus outline
- **禁止**：不要大面积使用品牌色作为背景；不要在一屏内出现超过 3 个蓝色主要元素
- **语义色**：如需成功/错误/警告状态，使用 iOS 系统色调：
  - 成功：`rgb(52 199 89)` 或 `rgb(48 176 80)`
  - 警告：`rgb(255 159 10)`
  - 错误：`rgb(255 69 58)`

## 字体排印 (Typography)

使用系统字体栈以获得最佳性能与本地化体验。

### 字体栈

```css
--font-system: 'SF Pro Display', 'SF Pro Text', -apple-system, BlinkMacSystemFont,
  'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;
```

### 字阶系统 (Type Scale)

```css
--font-size-hero: clamp(56px, 7vw, 96px);      /* 首屏大标题 */
--font-size-title-1: clamp(40px, 5vw, 64px);   /* 区块主标题 */
--font-size-title-2: clamp(32px, 4vw, 48px);   /* 次级标题 */
--font-size-title-3: clamp(24px, 3vw, 36px);   /* 卡片标题 */
--font-size-headline: 21px;                     /* 强调正文 */
--font-size-body: 17px;                         /* 标准正文 */
--font-size-callout: 15px;                      /* 辅助信息 */
--font-size-caption: 13px;                      /* 标签、元数据 */
```

### 行高与字距

```css
--line-height-tight: 1.05;   /* 超大标题 */
--line-height-snug: 1.2;     /* 标题 */
--line-height-normal: 1.5;   /* 正文 */
--line-height-relaxed: 1.6;  /* 长文本 */

--letter-spacing-tight: -0.05em;   /* 大标题紧缩 */
--letter-spacing-normal: -0.01em;  /* 默认 */
--letter-spacing-wide: 0.08em;     /* 小写标签、eyebrow */
```

### 使用规则

- **Hero 标题**：使用 `--font-size-hero` + `--line-height-tight` + `--letter-spacing-tight`，字重 700
- **正文最大宽度**：限制在 `60-70ch`（中文）或 `40-50ch`（英文）以保证可读性
- **中英混排**：中文段落用 `--line-height-relaxed` (1.6)，纯英文用 `--line-height-normal` (1.5)
- **小写标签** (eyebrow text)：使用 `--font-size-caption` + `text-transform: uppercase` + `--letter-spacing-wide`

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

- **组件内部间距**：`--space-4` 至 `--space-6` (16-32px)
- **卡片 padding**：`--space-6` 至 `--space-8` (32-64px)
- **区块垂直间距**：`--space-10` 至 `--space-12` (96-160px)，响应式调整：
  ```css
  padding: clamp(80px, 12vw, 160px) var(--content-padding);
  ```
- **内容最大宽度**：`--content-max-width: 1200px`
- **水平内边距**：`--content-padding: clamp(20px, 5vw, 80px)`

### 响应式断点

```css
/* 移动优先 */
/* 默认: 320px - 767px */

@media (min-width: 768px) {
  /* 平板与小桌面 */
}

@media (min-width: 1024px) {
  /* 桌面 */
}

@media (min-width: 1440px) {
  /* 大屏 — 可选的视觉优化 */
}
```

## 阴影系统 (Shadows)

软阴影营造深度，避免生硬的边缘。

```css
--shadow-sm: 0 1px 2px rgb(0 0 0 / 0.04), 0 1px 4px rgb(0 0 0 / 0.04);
--shadow-md: 0 2px 8px rgb(0 0 0 / 0.04), 0 4px 16px rgb(0 0 0 / 0.06);
--shadow-lg: 0 8px 24px rgb(0 0 0 / 0.08), 0 2px 4px rgb(0 0 0 / 0.04);
--shadow-xl: 0 16px 48px rgb(0 0 0 / 0.12), 0 4px 8px rgb(0 0 0 / 0.06);
--glass-shadow: 0 4px 24px rgb(0 0 0 / 0.08), 0 1px 2px rgb(0 0 0 / 0.04);
```

**使用建议**：
- 静态卡片：`--shadow-sm` 或 `--shadow-md`
- Hover 抬升效果：从 `--shadow-md` 过渡到 `--shadow-lg`
- 玻璃表面：`--glass-shadow`
- 模态/浮动面板：`--shadow-xl`

## 动效系统 (Motion)

ZVMS Pro 的动效追求「Q弹」(springy) 的手感——快速响应但有自然的回弹，避免线性或过于机械的动画。

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
--duration-slow: 500ms;    /* 大区块、页面转场 */
```

### 使用规则

- **只动画 transform 和 opacity**：避免触发 layout 或 paint 的属性 (width, height, margin, padding, top, left 等)
- **按压反馈**：
  ```css
  .button:hover {
    transform: translateY(-2px);
    transition: all var(--duration-base) var(--ease-spring);
  }
  .button:active {
    transform: translateY(0) scale(0.98);
  }
  ```
- **悬停抬升**：卡片 hover 时使用 `translateY(-4px)` + 阴影增强
- **淡入序列**：元素分组错开 50-100ms 出现
  ```css
  .item {
    opacity: 0;
    transform: translateY(20px);
    transition: all var(--duration-slow) var(--ease-smooth);
    transition-delay: calc(var(--item-index) * 0.1s);
  }
  .item.visible {
    opacity: 1;
    transform: translateY(0);
  }
  ```

### 滚动触发动画

使用 `IntersectionObserver` 触发元素的 `.visible` 类，避免重复计算或使用重型库。

```typescript
const observer = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('visible')
      }
    })
  },
  { threshold: 0.1, rootMargin: '-80px' }
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

## 性能预算 (Performance Budget)

ZVMS Pro 不使用任何第三方动画库、icon 库或重型资产。

### 硬性约束

- ❌ **禁止**：GSAP, Framer Motion, Anime.js 等动画库
- ❌ **禁止**：Font Awesome, Material Icons 等 icon 字体或包
- ❌ **禁止**：网络字体 (使用系统字体栈)
- ❌ **禁止**：大尺寸光栅图片 (JPEG/PNG 产品截图)
- ✅ **允许**：手写的内联 SVG icon (< 2KB)
- ✅ **允许**：CSS 渐变、`backdrop-filter`、`clip-path` 等 CSS 特性
- ✅ **允许**：轻量级的 Vue transition 组件

### 目标

- **首次内容绘制 (FCP)**：< 1.5s (3G 网络)
- **最大内容绘制 (LCP)**：< 2.5s
- **累积布局偏移 (CLS)**：< 0.1
- **帧率**：保持 60fps，中端设备无卡顿

### 优化手段

- 使用 CSS `will-change` 提示浏览器优化（谨慎使用）
- 避免在滚动时触发 reflow/repaint
- 使用 `transform` 和 `opacity` 触发 GPU 加速
- 懒加载非首屏内容

## 无障碍 (Accessibility)

ZVMS Pro 遵循 WCAG 2.1 AA 标准。

### 必要规范

1. **语义化 HTML**：
   - 每页一个 `<h1>`
   - 逻辑的标题层级 (h1 → h2 → h3)
   - 使用 `<nav>`, `<main>`, `<section>`, `<article>`, `<footer>` 等地标
   - 表单元素关联 `<label>`

2. **键盘可访问**：
   - 所有交互元素可通过 Tab 键访问
   - 使用 `:focus-visible` 提供清晰的焦点样式
   - 模态对话框支持 Escape 关闭
   - 移动菜单支持 Escape 关闭

3. **对比度**：
   - 正文文本至少 4.5:1 (`#1d1d1f` on `#ffffff` = 16.1:1 ✓)
   - 辅助文本至少 4.5:1 (`#6e6e73` on `#ffffff` = 4.6:1 ✓)
   - 玻璃材质上的文本需验证对比度

4. **ARIA 标签**：
   ```html
   <nav aria-label="主导航">
   <button aria-expanded="false" aria-controls="menu-id">
   <div role="status" aria-live="polite">
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

## 组件规范

### 按钮 (Buttons)

**主要按钮** (Primary CTA)：
```css
.button-primary {
  padding: var(--space-4) var(--space-7);
  background: var(--color-brand);
  color: white;
  font-size: var(--font-size-body);
  font-weight: 600;
  border-radius: var(--radius-md);
  box-shadow: 0 4px 16px rgb(0 113 227 / 0.3);
  transition: all var(--duration-base) var(--ease-spring);
}

.button-primary:hover {
  background: var(--color-brand-hover);
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgb(0 113 227 / 0.4);
}

.button-primary:active {
  transform: translateY(0) scale(0.98);
}
```

**次要按钮** (Secondary)：
```css
.button-secondary {
  padding: var(--space-4) var(--space-7);
  background: var(--color-bg-elevated);
  color: var(--color-text-primary);
  border: 1px solid var(--color-border-moderate);
  border-radius: var(--radius-md);
  font-size: var(--font-size-body);
  font-weight: 600;
  transition: all var(--duration-base) var(--ease-spring);
}

.button-secondary:hover {
  background: white;
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}
```

### 卡片 (Cards)

**标准卡片**：
```css
.card {
  padding: var(--space-7);
  background: white;
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  transition: all var(--duration-base) var(--ease-spring);
}

.card:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-lg);
}
```

**玻璃卡片**：
```css
.card-glass {
  padding: var(--space-7);
  background: var(--glass-bg);
  backdrop-filter: var(--glass-blur);
  border: 1px solid var(--color-border-subtle);
  border-radius: var(--radius-xl);
  box-shadow: var(--glass-shadow);
  position: relative;
}

.card-glass::before {
  content: '';
  position: absolute;
  top: 0;
  left: 5%;
  right: 5%;
  height: 1px;
  background: linear-gradient(90deg, transparent, var(--glass-border), transparent);
}
```

## Do / Don't 示例

### ✅ Do

- **大胆使用留白**：区块间距至少 80px，桌面端 120-160px
- **一屏一个焦点**：每个视口只有一个主要视觉元素
- **真实的材质感**：玻璃有高光、阴影、折射，卡片有抬升
- **克制的品牌色**：蓝色只用于 CTA 与关键信息
- **简洁的文案**：标题 10-20 字，正文段落 60-80 字
- **对称与平衡**：左右布局交替出现，保持视觉平衡

### ❌ Don't

- **不要紧凑布局**：避免把多个模块挤在一屏
- **不要彩虹渐变**：紫/粉/青渐变不符合 ZVMS 气质
- **不要嵌套玻璃**：半透明材质不能叠加
- **不要滥用动画**：不要让所有元素都动，不要无限循环
- **不要混乱的圆角**：一个组件内使用统一的圆角尺寸
- **不要低对比度**：文本对比度必须通过 WCAG AA

## 实现清单

开发新页面时的自查：

- [ ] 使用 `tokens.css` 中的设计变量
- [ ] 页面有一个 `<h1>` 且标题层级正确
- [ ] 交互元素有 `:focus-visible` 样式
- [ ] 文本对比度满足 WCAG AA (4.5:1)
- [ ] 按钮尺寸 >= 44px (移动端)
- [ ] 玻璃材质有顶部高光边缘
- [ ] 动画使用 `transform`/`opacity`，避免 layout 属性
- [ ] 支持 `prefers-reduced-motion`
- [ ] 响应式测试：390px, 768px, 1024px, 1440px
- [ ] 没有水平滚动条
- [ ] 使用语义化的 HTML5 地标元素
- [ ] 使用系统字体，没有引入外部字体或 icon 库
- [ ] 性能：没有大图片、重型库、无限动画循环

---

**设计原则版本**：v1.0
**最后更新**：2026-11-17
