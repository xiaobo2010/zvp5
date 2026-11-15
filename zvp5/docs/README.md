# 义工平台 · 前端约定

本目录是义工平台前端的设计与实现规范。**所有前端智能体与开发者必须先行阅读
[《前端设计准则.md》](./前端设计准则.md)，再开始编写任何前端代码。**

## 目录结构

```
前端约定/
├── README.md            ← 本文件（快速开始）
├── 前端设计准则.md       ← 完整设计准则（色彩/圆角/阴影/弹层/组件规范/检查清单）
├── demo.html            ← 全组件演示页（浏览器直接打开，可交互预览）
└── css/
    ├── main.css         ← 样式总入口（业务页面只需引入这一个文件）
    ├── design-tokens.css← 设计令牌（颜色/字体/间距/圆角/阴影/动效，唯一事实来源）
    ├── base.css         ← 基础样式（Reset、排版、栅格、布局骨架、毛玻璃/G2 工具）
    └── components/
        ├── buttons.css       ← 按钮、图标按钮、链接、按钮组
        ├── forms.css         ← 表单与数据录入组件
        ├── navigation.css    ← 侧边栏、菜单、顶栏、标签页、分页、下拉、步骤条等
        ├── data-display.css  ← 卡片、表格、列表、标签、头像、日历等展示组件
        └── feedback.css      ← 弹窗、抽屉、消息、通知、加载、结果页等反馈组件
```

## 快速开始

```html
<!DOCTYPE html>
<html lang="zh-CN" data-theme="light"><!-- 去掉 data-theme 则跟随系统深浅色 -->
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>义工平台</title>
  <link rel="stylesheet" href="./css/main.css">
</head>
<body>
  <div class="yg-layout">
    <aside class="yg-layout__sidebar yg-sidebar yg-liquid-glass">…</aside>
    <div class="yg-layout__main">
      <header class="yg-layout__topbar yg-topbar">…</header>
      <main class="yg-layout__content">…</main>
    </div>
  </div>
</body>
</html>
```

- **主题**：`<html data-theme="dark">` 手动暗色；`data-theme="light"` 手动浅色；
  不设置则跟随系统。
- **预览**：双击打开 `demo.html` 查看全部标准组件的实际效果。

## 给智能体的三条底线

1. **先读准则**：所有视觉决策（颜色、圆角 G2、阴影、弹层不透明规则等）
   均以《前端设计准则.md》为准。
2. **只用令牌**：颜色/尺寸/圆角/阴影/动效一律引用 `--yg-*` CSS 变量，
   禁止硬编码色值与魔法数值，禁止 `!important`。
3. **类名规范**：前缀 `yg-`，BEM 风格 `yg-block__element--modifier`，
   状态用 `.is-*`；新增组件必须在准则第 4 章登记类名后方可编写样式。

## 后端设计文档索引

本目录同时收纳后端契约与迁移设计（前端准则见上）：

| 文档 | 内容 |
|---|---|
| `ADB_MIGRATION.md` / `ADB_SCHEMA_SPEC.md` | MongoDB → Oracle ADB 迁移记录与 schema 规格 |
| `AI_GATEWAY_API.md` | AI 网关：OCR（含 `/api/ai/ocr/file`）、凭据、额度、流水、OpenAI 兼容面、ZVAgent 工具、会话与只读场景 |
| `AI_CONVERSATION_DESIGN.md` | 会话 / 消息 / run 数据模型与契约（含模型锁定、思考强度、归档、`since_msg_seq`） |
| `AI_AGENT_OPS_DESIGN.md` | ZVAgent 运维能力（文件 / SSH / 数据库）、写操作提案、`ban_history`、内容安全语义 |
| `FILES_API_DESIGN.md` | 附件 API，以及系统文件管理的根目录 jail（§13） |
| `DEVICE_TRACKING_DESIGN.md` | `APP_LOGS` 学海 ID 采集 |
| `DB_PERF_MAINTENANCE_DESIGN.md` | 数据库性能与保留期维护 |
| `DEPLOY_ZVP_ORIGIN_2026-11-10.md` | **当前部署口径**：Origin=zvp、同源 /api、DNS 切换与刷新缓存 |
| `DEPLOY_API_SPLIT.md` | 历史：内容站 / API 直连拆分、CORS、CSP、SSE 与 WebSocket 部署 |
| `V4242_*` / `REFERENCE_TREES_ALIGNMENT.md` | 阶段契约与参考树对账记录 |
