# zvp5-gitlab 设计留档

> 来源：`zvp5-gitlab/`（已删除）。原树是从未完成的第二套后端，`app.py` 与
> `routers/misc.py` 为空，多个文件无法编译，**不可运行**。
>
> 本目录只保留后续可能参考的设计：独立审核流、FAQ/资源中心、定向 WebSocket
> 推送。多学校（`School` / 多校 `Class`）明确不采用，仅保留被 FAQ 关系引用的
> 模型作为上下文。

## 1. 保留内容

### 1.1 独立审核流

| 文件 | 作用 |
| --- | --- |
| `models/workflows.py` | `Workflow` / `WorkflowItem`：审核流程主表与流程项 |
| `dtypes/workflows.py` | `WorkflowStatus`（DRAFT/PENDING/EFFECTIVE/REJECTED/INVALID） |
| `schemas.py` | 审核通过/驳回、改标题、改活动信息、改成员的请求 schema |
| `routers/v1/activities.py` | 活动创建时写 `Workflow`/`WorkflowItem`、调用推送的参考实现 |
| `models/users.py` | `User.pending` 与审核请求的关联 |

主线当前用活动自身的 `status` / `approver` / `appointee` 表达审核结果，没有独立
流程表；若以后需要完整审计链，以这些文件为设计起点。

### 1.2 FAQ / 资源中心

| 文件 | 作用 |
| --- | --- |
| `models/rsrc.py` | `Help`（FAQ，含分类、附件资源）与 `Resource`（资源/附件） |
| `dtypes/faq.py` | `FaqCategory` 位标志分类 |
| `models/groups.py` | `SchoolFaqTable` 所依赖的 `School` 模型；**多学校本身不采用** |
| `models/activities.py` | `ActivityResTable`：活动与资源的关联 |

FAQ 与资源的表结构可独立采用；`School` 仅作为原设计的关系上下文保留，不意味着
主线要引入多学校。

### 1.3 定向 WebSocket 推送

| 文件 | 作用 |
| --- | --- |
| `tasking.py` | `PushService`：按用户保存 WebSocket 连接、发送定向消息、离线时写入 `Notification` 缓存；`TaskManager` 长任务骨架 |
| `models/push.py` | `Notification`（离线消息，按 `user_id`）与 `Notice`（公告，含已阅统计关系） |
| `models/users.py` | `User.unread_notices` 已阅关系 |
| `cache.py` | Redis 客户端骨架（跨实例广播的候选实现） |

主线目前使用 socket.io + 站内通知 API，客户端本地记录已阅；若需要服务端已阅
统计和严格按用户定向推送，可参考这里的模型和 `PushService` 思路。

### 1.4 运行上下文

`database.py`、`models/shared.py`、`models/__init__.py`、`dtypes/` 是上述模型所依赖
的基础定义。保留它们是为了让归档代码可读，不代表它们是完整可运行工程。

## 2. 已清理内容

以下内容未留档：

- 空的 `app.py`、`routers/misc.py`；
- 未采用的 `routers/v1/users.py`、`routers/v1/platform.py`；
- 通用 `crypto.py`、`errors.py`、`utils.py`、`serializer.py`、`config.example.py`；
- 多学校专用的 `School`/`Class` 路由与其余未使用模型；
- `models.py.bak`、`dtypes.py.bak`、根 `README.md` 等历史杂项。

## 3. 已知缺陷（阅读时注意）

- `models/__init__.py` 引用不存在的 `relations` 模块；
- `dtypes/__init__.py` 引用 `activities`，实际文件名为 `activitities.py`；
- `routers/v1/activities.py` 含语法错误与拼写错误；
- `tasking.py` 引用未定义的 `TaskStatus`、`NotifyTasks`、`db.session_factory`；
- `PushService` 使用类变量保存连接，不适合多 worker 直接照搬。

因此本目录是**设计参考**，不是可执行实现。
