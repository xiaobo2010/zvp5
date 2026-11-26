# ZVP5 Art 主题前端

这是 **ZVP5** 的独立 Art 主题前端，构建后挂载在 `/themes/art/`。根路径 `/`
继续由 Canary 主题提供，两套主题可以分别预览和切换。除艺术首页外，登录、活动、
个人资料、通知与管理能力沿用 ZVP5 Web 前端。

## 功能

### 学生端

- 登录（支持 TOTP 两步验证）、首页与个人主页
- 个人中心：资料维护、密码与 TOTP 管理
- 义工活动浏览、报名与时长登记，个人时长统计
- 通知收件箱、关于页

### 管理端

- 用户与角色管理、权限组（班级）管理
- 活动管理（发布 / 审核 / 登记）
- 日志审计（含 `client_ip` / `user_agent` 展示）、封禁违规处理
- 通知发送、变更日志（changelog）维护

### 服务端（服务器总览）

- 数据库监控与内容管理
- 系统文件管理（限定 `/www/zvms4` 根目录）
- Web SSH 终端
- 双人 TOTP、维护模式、角色策略

### ZVAgent（AI 能力对外统一入口，`/api/ai/*`）

- 会话管理与流式回复；思考强度三档（`low` / `medium` / `high`）
- 归档会话独立视图与恢复
- 轻量场景：OCR 识别文字（`POST /api/ai/ocr/file`）、文件编辑器「AI 分析」（`scene=file_edit`）、终端页「智能分析」（`scene=terminal`）
- 配额与 API Key 管理、定时任务、OpenAI 兼容接口面

### 体验特性

- 中英双语（vue-i18n，zh / en）、明暗主题
- PWA：离线可用、Service Worker 自动接管
- 低 GPU 档下「流畅 / 精细」渲染模式
- 液态玻璃风格导航

## 技术栈

| 类别 | 选型 |
| --- | --- |
| 框架 | Vue 3.5（Composition API）+ TypeScript 5.8 |
| 构建 | Vite（rolldown-vite）+ UnoCSS |
| 状态 / 路由 | Pinia 3 + vue-router 4 |
| UI | Element Plus 2.10 |
| 国际化 | vue-i18n 11（zh / en） |
| PWA | vite-plugin-pwa |
| 可视化 / 编辑器 / 终端 | Chart.js + Monaco Editor + xterm |
| 测试 | Cypress 15（E2E）+ Vitest 4（单元） |
| 包管理 | pnpm 10.34.5（`packageManager` 固定，仅 `pnpm-lock.yaml`） |

## 本地开发

前置：Node.js 24（`.node-version` = 24.19.0）、pnpm 10.34.5。

```sh
pnpm install --frozen-lockfile   # 安装依赖
pnpm dev                         # 开发服务器（5173，启动前自动 design:sync）
pnpm build                       # 类型检查 + 生产 / PWA 打包（prebuild 亦自动 design:sync）
pnpm preview                     # 预览生产构建（4173）
pnpm test:unit                   # Vitest watch 模式
pnpm exec vitest run             # 单元测试单次运行
pnpm test:e2e                    # Cypress E2E（需先 pnpm exec cypress install）
pnpm lint                        # ESLint 9 flat config 检查
pnpm lint:fix                    # ESLint 检查并写入修复
pnpm format                      # Prettier 格式化 src/
pnpm type-check                  # vue-tsc 类型检查
```

> `pnpm-workspace.yaml` 默认不安装 Cypress 浏览器二进制，首次跑 E2E 前先执行 `pnpm exec cypress install`。

## 环境变量

参考 `.env.production.example`（真实 `.env` 不入库）：

| 变量 | 说明 |
| --- | --- |
| `VITE_API_BASE` | API 基址；同源部署用 `/api`，拆分部署用绝对 HTTPS URL（以 `/api` 结尾） |
| `VITE_API_RACE_BASE` | 可选竞速线路（仅少量 JSON GET），未配置时全部走主基址 |
| `VITE_API_CONTENT_BASE` | 可选内容主机基址（`/system/files/*` 等在页面源执行的端点） |
| `VITE_STATIC_BASE` | 可选高带宽静态资产源，绝对 HTTPS URL；未配置即同源 |
| `VITE_PAGE_BASE` | 页面公开源，用于 PWA scope；同源部署用 `/` |

校验逻辑见 `src/utils/apiBaseConfig.ts`（生产构建对非 `/api` 的取值要求绝对 HTTPS 且以 `/api` 结尾）。

## 目录结构（简）

| 路径 | 内容 |
| --- | --- |
| `src/views/` | 页面组件（`user/` `activity/` `manage/` `server/` `ai/` 等） |
| `src/api/` | HTTP API 封装（按域拆分：`user` `activity` `ai` `system` …） |
| `src/router/` | 路由与守卫 |
| `src/assets/styles/yg/` | `design:sync` 从设计系统同步生成的样式（勿手改） |
| `src/i18n/` | vue-i18n 语言包（zh / en） |
| `cypress/` | E2E 测试 |
| `scripts/sync-design-css.mjs` | 设计系统样式同步脚本（`pnpm design:sync`） |

## 设计系统

- 样式**唯一事实来源**在仓库根 `前端约定/css`，详见 `前端约定/README.md` 与 `前端约定/前端设计准则.md`。
- `pnpm dev` / `pnpm build` 前置钩子会自动执行 `pnpm design:sync`，把设计系统同步到 `src/assets/styles/yg/`；手工改动该目录后需重跑 `pnpm design:sync`。
- 设计令牌一律使用 `--yg-*` 变量；组件类名遵循 `yg-block__element--modifier` 约定，新增类名必须先在《前端设计准则》第 4 章登记。

## 与后端的关系

- 前端只通过 HTTP API 访问后端，**不直连数据库**；后端数据层为 Oracle Autonomous Database（python-oracledb thin），对前端透明。
- API 前缀 `/api/`：业务端点已全量收口到 `/api/v1/*`（原 `/api/v2`、`/api/v3` 已于 2026-11-19 归并退役，见 `zvp5/docs/ADR_API_V1_REVERSIONING.md`）；AI 能力统一走 `/api/ai/*`，其中 `/api/ai/v1/*` 是对外的 OpenAI 兼容契约。
- 本地联调时，开发服务器把 `/api/` 代理到本机后端（含 Web SSH 的 WebSocket 升级），配置见 `vite.config.ts`。

## 致谢与许可

- 本项目基于上游 [zvms/zvms4-frontend](https://github.com/zvms/zvms4-frontend)（MIT, Copyright (c) 2023 zvms）二次开发。
- 本仓库以 MIT 许可发布。
