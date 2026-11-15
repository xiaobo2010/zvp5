# ZVAgent 流式与效率改造交接

日期：2026-11-16

## 交付范围

- `main`：Agent 后台执行正确性、Token/Tools 效率与真实上游流式，提交 `86e1c1d0`。
- `yg-redesign-v2` → `origin/feat/yg-redesign-v2`：保留 wzxundesigned 全部设计，合入 `main`/`v5-canary` 已提交功能与本后端优化，增加流式 Markdown、工具时间线、平板适配和真实平台 Logo。
- 未做生产部署、Release、DDL 执行或真实付费上游调用。

## 实现契约

- `GET /api/ai/runs/{run_id}/events` 提供 fetch SSE；旧轮询接口保持兼容。
- `snapshot` 是持久化事实，`live` 是独立 ephemeral 状态；助手帧携带累计文本，工具帧按调用 ID 更新。
- 终态顺序为最终 `snapshot` → `terminal` → `[DONE]`；首帧即可用于断线恢复。
- 慢客户端使用有界队列和 resync；检测到多 worker 时返回 503，前端自动回退轮询。
- 工具参数必须完整且 JSON 合法后才执行；取消/中断保留已执行工具链，未执行调用不落残链。
- 长工具结果使用 head+tail、长度与摘要标记；重复只读结果和历史工具输出做有界压缩。
- 添加义工成员只对明确 `400/409/422` 做逐行归因；网络/超时/5xx 不自动重发，避免重复累计。

## 验证证据

- `main` 后端：`4708 passed`；Ruff F821/F401、compileall、`git diff --check` 通过。
- 发行工作树后端：`4753 passed`；Ruff F821/F401、compileall 通过。
- 发行前端：106 个文件、`1224 passed`；type-check、lint、生产构建通过。
- Cypress 生产构建真实 `/ai/chat`：2000×1200、1200×2000、503 fallback 共 3 例通过；验证分片 SSE、工具 pending→success、终态不重复、回看入口唯一、工具展开、44px 触控、composer 在视口内、独立滚动和无横向溢出。
- 截图：`/tmp/opencode/zvp5-agent-e2e/screenshots/agent-redesign.cy.ts/`；observer 复验 PASS，回看入口仅助手区一处、实测对比度约 6.3:1，无重复状态行或裁切。
- actionlint 通过；基础 gitleaks 无泄漏。高置信度规则 30 项与 `main` 基线逐项相同，本次零新增。

## 运行边界

- SSE 需要单 worker；多 worker 环境依赖轮询降级，不声称具备跨进程实时推送。
- 反向代理须允许 SSE 并关闭该路径缓冲；应用响应已带 `X-Accel-Buffering: no`。
- 真实 Oracle ADB、真实上游流式、长时生产负载未在本机验证。
- 发行分支继承了 V053 通知已读 DDL；发布时仍须遵循既有迁移窗口，不得由应用启动自动执行。
- 构建保留既有大 chunk、CSS `:deep()` 与 Markdown lint 警告；它们不是本次新增回归。

## 后续

该分支是后续发行候选。合并入 `main` 前先 fetch 并检查并发提交；生产部署和 Release 需要另行明确授权。
