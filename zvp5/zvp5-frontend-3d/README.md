# ZVMS 3D 概念版

`zvp5-frontend-3d` 是 Canary Pro 首页的三维概念验证：同样的账号与接口，四个模块
（简要信息、义工时长、最近义工、其他）以伪 3D 卡片悬浮在暗色空间里，通过滚轮或
上下滑动切换模块；左侧仪表盘提供首页与后续入口的占位。

## 运行

```bash
pnpm install --frozen-lockfile
pnpm dev        # http://localhost:5283/
pnpm build      # vue-tsc --noEmit + vite build，产物基准路径 /3d/
pnpm preview    # http://localhost:5284/3d/
```

构建产物固定使用 `/3d/` 作为 `base`，因为线上与 Pro 同源、挂在
`https://canary-pro.xiaobopisces.top/3d/`。开发服务器使用根路径，方便直接打开。

## 数据与登录

- 只读接口复用 Canary Pro 的契约：`/v1/users/{id}`、`/time`、`/activities`、
  `/v1/notifications`，以及 `/ai/runs`、`/logs` 等。
- 登录态与 Pro 共用同一个 origin 下的 `localStorage.token`；未登录时页面会提示
  先去 Pro 的 `/login` 登录，再回来刷新。
- `src/views/buildStageData.ts` 把接口数据整理成 `src/types/stage.ts` 里的
  `StageData`。展示层只消费这个快照，不再发请求。

## 结构

```text
src/
  api/canary.ts          精简的 Canary 客户端（token / 请求 / 读取接口）
  api/home.ts            首页工作台接口（待审核、Agent、风险日志）
  types/stage.ts         展示层数据契约（只读）
  views/buildStageData.ts 接口数据 → StageData
  views/HomeView.vue     加载、登录/错误状态、顶部栏
  components/stage/      3D 舞台与模块视觉
```

## 门禁

- `.github/workflows/canary-3d.yml`：3D 目录变更时快速跑类型检查与构建。
- `.github/workflows/canary-pro.yml`：完整门禁通过后，把 3D 产物复制进 Pro 的
  `dist/3d/`，随 Pro 一起发布到同一个 Worker，并在冒烟阶段校验 `/3d/` 入口。
