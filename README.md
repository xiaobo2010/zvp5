# ZVP5 · 镇海中学义工管理平台

> 面向中学志愿服务场景的开源管理平台：**活动组织 → 义工报名 → 时长核算 → 统计报表 → 审计留痕**，
> 附带一个可控的 AI 助手（ZVAgent）与一套管理员运维台。
>
> 前端 Vue 3 + PWA，后端 FastAPI + Oracle Autonomous Database，部署用 systemd + nginx，无容器依赖。

[![MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![docs](https://img.shields.io/badge/docs-mkdocs-blue.svg)](https://xiaobo2010.github.io/zvp-docs/)
[![code](https://img.shields.io/badge/docs-zvp5--docs-informational.svg)](zvp5/docs/README.md)

| 入口 | 说明 |
| --- | --- |
| 文档站 | <https://xiaobo2010.github.io/zvp-docs/> —— 快速开始、架构总览、端点 API 参考 |
| 源码 | 本仓库 `xiaobo2010/zvp5` |
| 贡献 | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| 安全 | [`SECURITY.md`](SECURITY.md) |

---

## 这是什么

中学的志愿服务管理长期靠 Excel + 群消息，问题是时长算不清、报名和审核对不上、
谁做了什么查不到。ZVP5 把这条链路做成一个系统：

- **义工**（学生）注册、报名活动、查看自己的时长与活动记录；
- **活动组织者**创建活动、审核报名、核定时长；
- **管理员**管理用户、班级与权限组，处理违规与封禁，查看全站审计日志；
- **系统运维**在管理台里看数据库健康度、文件、终端与配置。

平台由镇海中学在真实使用中迭代而成，本仓库是它的**完整实现**（MIT 开源）。
本平台的上一代为ZVMS4，由于开发人员毕业已停止迭代。

### 功能面

**学生端**

- 登录（密码 / TOTP 动态码）、弱密码强制改密、登出即刻失效
- 首页与个人主页、个人中心
- 义工活动浏览、报名与登记（「添加义工 / 修改义工」同一编辑器）
- 时长统计与达标进度
- 站内通知收件箱

**管理端**

- 用户、角色、班级与权限组（团队）管理
- 活动创建、成员增删改、时长调整、状态与审核流转
- 全站行为日志审计（按类型 / 级别 / 用户 / 设备 ID 过滤，记录来源 IP 与 UA）
- 封禁规则、违规上报与处理流水
- 通知下发、变更日志（管理员可编辑）

**服务端（管理员运维台）**

- 数据库监控：表存储占用、行数、表空间容量、近期负载；保留期清理与备份
- 数据库内容管理：按表浏览与编辑
- 文件管理（根目录 jail，越界 403）
- Web SSH 终端：多目标、双人 TOTP + 一次性 ticket、并发上限、审计留痕
- 在线设置中心与用户配额、维护模式开关、角色策略、双人 TOTP 管理

**ZVAgent（AI 助手）**

- 持久会话与流式回复，逐轮增量拉取；模型在建会话时锁定，思考强度三档
- 只读场景：文件预览 OCR 识别文字、编辑器「AI 分析」、终端「智能分析」
- 模型目录、API Key、配额与配额申请、AI 定时任务、prompt 审计
- OpenAI 兼容面（`/api/ai/v1/chat/completions`）
- 内容安全 **fail-closed**：判定不可用即拒绝回答，判定成立按规则封号

**体验特性**

中英双语、明暗主题、PWA 离线与自动接管、低 GPU 设备可选「流畅 / 精细」渲染档、
液态玻璃导航。

---

## 技术栈

| 层 | 技术 |
| --- | --- |
| 前端 | Vue 3.5 · Vite（rolldown-vite）· TypeScript 5.8 · Element Plus 2.10 · Pinia 3 · vue-router 4 · vue-i18n 11（中/英）· UnoCSS · vite-plugin-pwa · Chart.js · Monaco · xterm |
| 后端 | FastAPI 0.162 · Pydantic 2.14 · SQLAlchemy 2.0 async · python-oracledb（thin 模式）· python-socketio · APScheduler（14 个周期任务）· Redis（可选读穿缓存） |
| 数据库 | Oracle Autonomous Database（唯一运行时数据库；DDL 版本化在 `zvp5/zvp5-backend-python/db/oracle/ddl/`，53 个版本） |
| 部署 | systemd + nginx + uvicorn（:8018）· GitHub Actions 发布流水线 · Cloudflare Worker 预览站 |
| 测试 | pytest（后端 253 个测试文件）· Vitest（前端 105 个 spec）· Cypress |

> 早期版本继承ZVMS4代码基于 MongoDB，现已全量迁移到 Oracle ADB；仓库中的 Mongo 相关代码仅作历史
> 记录（见 `zvp5/docs/ADB_MIGRATION.md`）。

---

## 快速开始

### 前置条件

| 组件 | 版本 |
| --- | --- |
| Python | 3.11+（CI 用 3.13） |
| Node.js | 24（`.node-version` = 24.19.0） |
| pnpm | 10.34.5（由 `packageManager` 固定，请勿使用 npm / yarn） |
| 数据库 | Oracle Autonomous Database 实例 + wallet |

### 后端

```bash
cd zvp5/zvp5-backend-python

python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python tools/setup_local.py     # 生成 settings.py / RSA 密钥 / aes_key.txt（已存在则不覆盖）

# 编辑 settings.py：ORACLE_USER / ORACLE_PASSWORD、所选实例的 DSN、wallet 路径与 wallet 密码
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

`GET /api/health` 在数据库不可用时返回 503。盾牌中间件要求请求 `User-Agent`
包含 `Mozilla`，用 curl 验收时记得带上。

### 前端

```bash
cd zvp5/zvp5-frontend

pnpm install --frozen-lockfile
pnpm dev          # http://localhost:5173，/api 已代理到 127.0.0.1:8000
```

`predev` / `prebuild` 会自动把根目录 `前端约定/css` 同步到
`src/assets/styles/yg/`——**样式的事实来源在仓库根，不在 `src/` 里**。

### 测试

```bash
# 后端
.venv/bin/python -m pytest -q
.venv/bin/python -m compileall -q routers services util db typings

# 前端
pnpm exec vitest run
pnpm lint && pnpm type-check && pnpm build
pnpm exec cypress install && pnpm test:e2e
```

---

## 仓库结构

```
zvp5/
├── zvp5-backend-python/     FastAPI 后端
│   ├── routers/              HTTP 层（30 个路由文件 + routers/ai/）
│   ├── services/             业务编排（AI、agent、风控、维护、监控）
│   ├── db/                   仓储实现（oracle/）+ 模型 + 版本化 DDL
│   ├── util/ tasks/ tools/   无状态工具 / 定时任务 / 本地初始化脚本
│   ├── tests/                pytest
│   └── docs/                 后端侧接口文档
├── zvp5-frontend/            Vue 3 前端
│   ├── src/views/            页面（student / manage / server / ai）
│   ├── src/api/              前端 API 封装
│   ├── src/assets/styles/yg/ 由「前端约定/css」同步而来，不要直接改
│   ├── cypress/              E2E
│   └── scripts/              design:sync 等构建脚本
├── docs/                     设计、契约、部署与运维文档（索引见 docs/README.md）
├── deployment/               systemd / nginx / GitHub Actions / 预览 Worker
├── 前端约定/                  前端设计规范与设计令牌（样式的唯一事实来源）
└── reference/                只读历史参考归档（不参与构建、不被导入）
```

---

## 文档

| 我想知道 | 解决方案 |
| --- | --- |
| 怎么把前后端跑起来 | [文档站 · 快速开始](https://xiaobo2010.github.io/zvp-docs/getting-started/) |
| 后端怎么分层、路由怎么组织 | [`zvp5/docs/API_STRUCTURE.md`](zvp5/docs/API_STRUCTURE.md) |
| 某个接口的路径与参数 | [文档站 · API 参考](https://xiaobo2010.github.io/zvp-docs/api/) |
| AI 助手的契约 | [`zvp5/docs/AI_GATEWAY_API.md`](zvp5/docs/AI_GATEWAY_API.md)、[`AI_CONVERSATION_DESIGN.md`](zvp5/docs/AI_CONVERSATION_DESIGN.md) |
| 全部文档地图 | [`docs/README.md`](docs/README.md)、[`zvp5/docs/README.md`](zvp5/docs/README.md) |
| 怎么部署到生产 | [`zvp5/deployment/github-actions/README.md`](zvp5/deployment/github-actions/README.md) |
| 前端视觉规范 | [`前端约定/README.md`](前端约定/README.md)、[`前端设计准则.md`](前端约定/前端设计准则.md) |
| 这个项目的历史 | [`docs/history/DEVELOPMENT_LOG.md`](docs/history/DEVELOPMENT_LOG.md)（另有模块移植审计、旧平台笔记、上游技术报告快照等，见 [`docs/README.md`](docs/README.md)） |

---

## 开发与发布

- **CI**：PR 与 `main` 推送跑五类门禁——后端 pytest + Ruff、前端 Vitest + ESLint +
  类型检查 + 生产构建、Cypress E2E、工作流与密钥扫描（Gitleaks + ShellCheck +
  actionlint）、依赖漏洞（Trivy HIGH/CRITICAL + `pnpm audit --prod`）。
  纯文档改动由一条几十秒的 `Docs guard` 兜底，拦截内网地址等基础设施标识被写回。
- **发布**：只有**非预发布 GitHub Release** 且 release commit 属于 `main`、五类门禁
  全过时才触发生产部署；健康检查失败按相反顺序回滚。**数据库 DDL 永不自动执行。**
- **预览**：推送到 `v5-canary` 分支自动构建前端并发布到预览站，跨域直连正式 API。
- **依赖**：Dependabot 每周分组更新 pip / pnpm / GitHub Actions。
- **版本与变更记录**：产品内变更日志由数据库 `CHANGELOG` 表驱动（管理员可在平台内编辑），
  `GET /api/version` 的当前版本也从该表读取——这是版本的唯一事实来源。仓库侧没有第二份
  CHANGELOG 文件，避免两处漂移；公开发行记录看
  [GitHub Releases](https://github.com/xiaobo2010/zvp5/releases)。

---

## 贡献

欢迎。先读 [`CONTRIBUTING.md`](CONTRIBUTING.md)：本地环境、测试命令、提交规范、
PR 流程，以及**脱敏红线**（本仓库公开，任何 PR 都不得引入真实 IP、凭据或个人信息）。

行为准则见 [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)。

## 安全

发现重大安全漏洞请走 [`SECURITY.md`](SECURITY.md) 的私密渠道，**不要开公开 Issue**。部署者自查清单（凭证、频控、文件 jail、审计、依赖扫描）也在那一页。
一般问题和改进建议请走公开Issue。

## 许可

本仓库以 **MIT** 发布，见 [`LICENSE`](LICENSE)。派生自上游
[`zvms/zvms4-backend-python`](https://github.com/zvms/zvms4-backend-python) 与
[`zvms/zvms4-frontend`](https://github.com/zvms/zvms4-frontend)（前端 MIT,
Copyright (c) 2023 zvms），来源与署名见 [`NOTICE`](NOTICE)。

> `reference/` 下的历史归档随本仓库一并以 MIT 分发，但不参与构建、不被应用导入。
