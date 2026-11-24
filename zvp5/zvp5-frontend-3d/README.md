# ZVMS Astra-Alpha

`zvp5-frontend-3d` 是 Astra-Alpha 三维主题：独立的 ZVMS 账号与接口，四个模块
（简要信息、义工时长、最近义工、其他）以伪 3D 卡片悬浮在暗色空间里。滚轮缩放场景，
在画面左右两侧横向拖动/滑动切换模块；左侧仪表盘可进入首页、义工列表和个人中心；个人中心拆成三个与首页同动画的模块。
义工列表只读当前账号自己的记录，每页最多 8 条；个人中心沿用同样的 3D 卡片旋转与拖拽。

## 运行

```bash
pnpm install --frozen-lockfile
pnpm dev        # http://localhost:5283/
pnpm build      # vue-tsc --noEmit + vite build，主站主题构建会追加 --base
pnpm preview    # http://localhost:5284/3d/
```

独立预览仍挂在 `/3d/`；接入主站多主题构建时由
`build-theme-apps.mjs --base=/themes/astra-alpha/` 覆盖，产物不依赖其他主题。开发服务器使用根路径，方便直接打开。

## 数据与登录

- 接口使用 ZVMS 公共契约：`/v1/users/{id}`、`/time`、`/activities`、
  `/v1/notifications`，以及 `/ai/runs`、`/logs` 等。
- 登录态存放在同源 `localStorage.token`；未登录时页面提示先登录，再回来刷新。
- `src/views/buildStageData.ts` 把接口数据整理成 `src/types/stage.ts` 里的
  `StageData`。展示层只消费这个快照，不再发请求。

## 结构

```text
src/
  api/canary.ts          精简的 ZVMS API 客户端（token / 请求 / 读取接口）
  api/home.ts            首页工作台接口（待审核、Agent、风险日志）
  types/stage.ts         展示层数据契约（只读）
  views/buildStageData.ts 接口数据 → StageData
  views/HomeView.vue     加载、登录/错误状态、顶部栏
  components/stage/      3D 舞台与模块视觉
```

## 门禁

- `.github/workflows/canary-3d.yml`：3D 目录变更时执行类型检查与构建。
- 主站多主题构建通过 `zvp5/themes/manifest.json` 中的 `astra-alpha` 条目接入；
  发布产物位于 `/themes/astra-alpha/`。
