# 参考树对齐清单（zvp-latest / zvp5-gitlab → zvp5）

> 核对日期：2026-10-24
> 开发主线：`zvp5/`
> 参考树：`zvp-latest/`（未经审计的 beta 快照）、`zvp5-gitlab/`（未完成的第二套后端）
>
> 本文记录能力对账与留档结果。**2026-10-24 已按确认执行删除**：
> 学海相关文件归档至 `reference/xuehai/`（2026-11-14 已移出仓库，见 `REDACTION_POLICY.md`），审核流/FAQ/资源/定向推送归档至
> `reference/zvp5-gitlab/`；`UserProfile` 与 `ManageRoles` 页面已接入主线。

## 1. 结论

- `zvp-latest`：后端能力已基本覆盖；剩余差异主要是明确拒绝的学海/平台面、已被新端点取代的旧端点，以及两个未逐页复刻的前端页面。原树已删除，学海相关文件整份留档。
- `zvp5-gitlab`：不是主线的旧版本，而是另一套未完成实现；仍有主线没有的域模型与设计。原树已删除，审核流、FAQ/资源、定向 WebSocket 相关文件留档。
- 两套参考树合计约 3.8 MB，删除对服务器资源没有实质帮助；主要磁盘占用是主线的 `node_modules` 与 `.venv`。

## 2. `zvp-latest` 对账

### 2.1 后端端点

主线共 162 个已注册路由，`zvp-latest` 共 72 个。绝大多数端点已精确对应或由主线 v1/v2 端点等价覆盖。

| `zvp-latest` 端点 | 主线处置 | 状态 |
| --- | --- | --- |
| `POST /api/v1/platform/login` | 不建平行登录；由 `/api/users/auth` 覆盖 | 拒绝并行端点 / 功能等价 |
| `GET /api/v1/platform/{totp,cert,logs}` | 由 `/api/v3/users/{id}/totp`、`/api/cert`、`/api/logs` 覆盖 | 拒绝并行端点 / 功能等价 |
| `POST /xh/api` | 学海签名代理与任意 URL 外呼 | 明确拒绝 |
| `PUT /api/v2/users/{id}/password` | 由 `/api/users/{oid}/password` 覆盖 | 功能等价 |
| `DELETE /api/v2/users/{id}/password` | 由 `/api/v3/users/{id}/password` 覆盖 | 功能等价 |
| `GET /api/v3/groups/{id}/activities` | 由 `/api/groups/{id}/activities` / groups v2 覆盖 | 功能等价 |
| `GET /api/v3/users/{id}/activities` | 由 `/api/users/{id}/activities` / users v2 覆盖 | 功能等价 |
| `GET /api/v3/users/{id}/time` | 由 `/api/users/{id}/time` / users v2 覆盖 | 功能等价 |
| 其余 v1/v2/v3、exports、imports、logs 端点 | 主线存在同名或超集端点 | 已覆盖 |

主线额外具备而 `zvp-latest` 没有的面：MCP、活动统计、维护模式、数据库监控/内容管理、服务器文件、SSH、封禁规则、附件、CHANGELOG、站内通知。

### 2.2 前端页面与组件

| `zvp-latest` 前端 | 主线对应 | 状态 |
| --- | --- | --- |
| `views/user/UserProfile.vue`（`/me`） | `/user/:id` + `UserPage` + `UserNav` 个人资料入口 | 功能近似，未逐页复刻 |
| `views/manage/ManageRole.vue`（`/manage/roles/:role`） | `ManageHome` 用户页 + `ZUserModification` 角色编辑 | 功能近似，未逐页复刻 |
| `components/utils/ZDisplayTOTP.vue` | `ServerTotp` + `/v3/users/{id}/totp` | 功能近似，未逐页复刻 |
| `api/xh.js`、`services/platform.ts`、设备绑定/设备会话 | 主线明确拒绝 | 不移植 |
| 其余活动、班级、用户、日志页面 | 主线均有对应页面/组件 | 已覆盖 |

### 2.3 处置结果

`zvp-latest` 已删除。删除前已确认：

1. `UserProfile` 与 `ManageRole` 已按主线样式重写为 `UserProfile.vue` / `ManageRoles.vue` 并接入路由与管理页；`ZDisplayTOTP` 由 `ServerTotp` 覆盖；
2. `/api/v1/platform/*`、`/xh/api` 及设备面永久拒绝移植，相关原始文件已整份归档到 `reference/xuehai/zvp-latest/`。

## 3. `zvp5-gitlab` 对账

### 3.1 可运行性

- `app.py`、`routers/misc.py` 均为空文件；
- 全树约 10 个 handler，`users` 的 3 个端点为 `pass`，真正可用者仅 1–2 个；
- `routers/v1/activities.py`、`routers/v1/platform.py`、`utils.py` 无法编译；
- 缺少 `models/relations.py`、`config.py`、顶层 `typings` 等被引用模块；
- 整体完成度约 15%–20%，不具备可运行性。

### 3.2 功能面对照

| 能力 | 主线 | `zvp5-gitlab` | 状态 |
| --- | --- | --- | --- |
| 用户 / 班级 / 组 | v2/v3 已落地 | 模型已定义，路由基本未写 | 主线覆盖 |
| 活动 / 成员 | v2/v3 + 加成员 | 模型 + 部分路由（编译不过） | 主线覆盖 |
| 日志 | type/level + 35 站点 | 模型 + 枚举，未接线 | 主线覆盖 |
| 导出 / 长任务 | exports + 任务 | `SavedTask` / `TaskManager` 骨架 | 主线覆盖 |
| 通知 / 推送 | 站内通知 + socket.io | `Notification`/`Notice` + 已阅统计 + WebSocket `PushService` | 部分覆盖，服务端已阅统计与定向推送未移植 |
| 独立审核流 | 以活动 `status/approver/appointee` 表达 | `Workflow` / `WorkflowItem` | **主线无独立流程表** |
| 多学校 | 单校 | `School` / 多校 `Class` | **主线无多学校模型** |
| FAQ / 资源 | 无 | `Help` / `Resource` | **主线无** |
| Redis 缓存 | 无 Redis 依赖 | `cache.py` 骨架 | 未采用 |
| 设备识别 | 设备绑定/日志已落地 | `Device` 空壳 | 主线覆盖 |

另：`dtypes.py.bak` 保留了拆分时丢失的 `ActivityMode`、`WorkflowItemType`、`MemberModificationType`、`DeviceInfo`、`SystemUser` 等定义，删除前需确认这些定义是否还需要。

### 3.3 处置结果

`zvp5-gitlab` 已删除，保留范围由确认结果决定：

- 保留：独立审核流、FAQ/资源中心、定向 WebSocket 推送及必要模型上下文；
- 不留：多学校 `School` / 多校 `Class` 的采用方案（`models/groups.py` 仅作为 FAQ 关系上下文留档）；
- `.bak` 与通用未完成工具未保留。

留档位置：`reference/zvp5-gitlab/`，说明见该目录 README。

## 4. 处置总表

| 参考树 | 原状态 | 当前处置 |
| --- | --- | --- |
| `zvp-latest` | 未全部逐页移植，另有明确拒绝面 | 已删除；学海文件归档 `reference/xuehai/`，两个页面已接入主线 |
| `zvp5-gitlab` | 多项独有设计未落地 | 已删除；审核流/FAQ/资源/定向推送归档 `reference/zvp5-gitlab/` |

## 5. 磁盘说明（删除前基线）

| 路径 | 占用 |
| --- | --- |
| `zvp-latest/` | 3.6 MB |
| `zvp5-gitlab/` | 204 KB |
| `zvp5-frontend/node_modules/` | 1.4 GB |
| `zvp5-backend-python/.venv/` | 290 MB |
| `.git/` | 6.6 MB |

参考树只占约 3.8 MB；真正占空间的是开发依赖。删除参考树主要是收敛仓库与安全面，不是解决卡顿的有效手段。
