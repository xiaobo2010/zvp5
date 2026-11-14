# ZVMS 4

[![Vercel](https://vercelbadge.vercel.app/api/zvms/zvms4-frontend)](https://vercel.com/quasi-studio/zvms4-frontend) [![Netlify Status](https://api.netlify.com/api/v1/badges/64e280fa-ea7f-4f6b-a89d-7696b483b178/deploy-status)](https://app.netlify.com/sites/zvms/deploys)

> The frontend of ZHZX Volunteer Management System

The v4 of ZVMS is brought to you with many new features, which gives you a brand new experience.

ZVMS 4 搭载了许多先前版本所想要实现却没有实现的功能。它可以给予你全新的体验。

It checks if you are using XueHai pad with UA (Samsung pad, Android Webkit), in order to extend the platform but make you use the pad properly in the school. For example, the auto route to maps and so on will not displayed in pads.

我们可以通过 User Agent 自动判断您是使用学海平板访问抑或者普通访问，以便让你合规使用学海平板。例如，外链将不会在学海平板中生效。

The most feature of v4 is the data structure. We use `mongodb` instead of `sqlite`, which can make us manage data more convenient and effective.

以上 MongoDB 描述为上游历史背景；当前配套后端运行于 Oracle Autonomous Database，前端通过 HTTP API 访问。

## 本地运行与验证

仓库只保留 `pnpm-lock.yaml` 作为依赖锁文件，并由 `package.json#packageManager`
固定 pnpm 版本。本轮本地验收使用 Node.js 24.19.0、pnpm 10.34.4。

```sh
pnpm install --frozen-lockfile
pnpm dev
pnpm exec vitest run
pnpm build
```

`build` 包含类型检查和生产/PWA 打包。`pnpm preview` 使用 4173 端口，与端到端测试入口一致。
`pnpm lint` 使用 ESLint 9 flat config；`pnpm lint:fix` 才会写入修复。
`pnpm-workspace.yaml` 默认未安装 Cypress 浏览器二进制；需要运行
`pnpm exec cypress install` 后再执行 `pnpm test:e2e`。

## ZVAgent 相关界面（2026-10-29）

AI 侧统一以 **ZVAgent** 呈现，本轮新增/调整：

- **会话**：运行中的 run 逐轮显示回复（`GET /api/ai/runs/{run_id}?since_msg_seq=` 增量拉取）；模型只读展示（创建时锁定，`PATCH` 改模型会被 409 拒绝）；思考强度三档（`low` / `medium` / `high`）；归档会话独立视图并支持恢复。
- **三个轻量场景**：文件预览的图片「识别文字」（`POST /api/ai/ocr/file`）、编辑器的「AI 分析」（`scene=file_edit`）、终端页的「智能分析」（`scene=terminal`），共用 `ZSceneRunDialog` 弹窗与 1.5s 轮询。
- **文件管理**：根目录收敛到 `/www/zvms4`（`/home` 分支因生产 `ProtectHome=true` 未生效，2026-11-14 随后端删除），越界由前端预校验 + 后端 403 `FILES_ROOT_FORBIDDEN` 双重拦截。
- **管理端**：日志卡片展示 `client_ip` / `user_agent`，事件 label 与后端 `services/log_events.py` 对齐；平台管理支持添加班级（`POST /api/v1/groups`）。
