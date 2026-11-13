# 站内通知 API（`/api/v1/notifications`）

本文是前端接入的唯一契约。后端负责「谁发的、什么时候发的、内容是什么、
全局/单条版本号是多少」，以及**每个账号读到哪一版**（V053 起跟着帐号走，不再
只存在 localStorage）。「不再显示 / suppress」仍然是**设备级**偏好，留在客户端，
服务端不保存。

## 1. 数据与版本语义

### 1.1 全局版本 `version`

`NOTIFICATION_STATE` 是单行计数器（`id = 1`）。以下操作在**同一事务**里把
全局 `version` 加 1：

| 操作 | 全局 version |
| ---- | ------------ |
| 管理员成功创建通知 | +1 |
| 管理员成功编辑通知（标题/内容/`active` 任一变化） | +1 |
| 管理员成功删除通知 | +1 |
| 通知不存在（404） | 不变 |
| 用户标记某条通知已读（`POST /{oid}/read`） | 不变 |
| 用户点击「不再显示」 | 不变（服务端无请求） |

因此客户端只需保存上次看到的全局版本：服务端 `version` 更大时拉一次完整列表；
相等时可继续用本地缓存。

### 1.2 单条通知版本 `version`

每条通知有独立的修订号：

- 创建时 `version = 1`；
- 每次成功编辑标题、内容或 `active`（包括停用/重新启用）`version += 1`；
- 单条通知的版本变化**不会**影响其他通知的版本。

客户端用它与服务端的「已读版本」比较：`notice.version > read[notice.id]`
表示这条通知在该用户上次阅读后又改过，可以再次提示为未读。

### 1.3 不可变发布人

`author_id`（24 位十六进制用户 oid）和 `author_name`（发布当时的显示名）在创建
时写入，之后**永不修改**。管理员改名不会改写历史通知；账号被删除也不影响历史
通知的发布人字段（表上没有外键）。`created_at` 同样是不可变发送时间。

### 1.4 内容哈希

每条通知响应都带 `content_hash`：`sha256(title + "\n" + content + "\n" + ("1" if
active else "0"))` 的 64 位小写 hex。`active` 参与哈希，因为停用/启用也是内容
变化。客户端比较哈希即可判断「内容是否等价」，不必逐字 diff；历史版本行里的
`content_hash` 是**那一版**的哈希，可用来确认回滚目标。

### 1.5 版本历史与回滚

每次成功写入（创建/编辑/删除/回滚）都在**同一事务**里向
`NOTIFICATION_VERSIONS` 追加一行完整内容快照：

| `op` | 何时写入 | `source_version` |
| ---- | -------- | ---------------- |
| `create` | 创建成功（`version = 1`） | `null` |
| `update` | 编辑/停用/启用成功 | `null` |
| `delete` | 硬删除之前（`active` 取删除前的值） | `null` |
| `rollback` | 回滚成功 | 回滚到的目标版本 |

快照表不建外键：通知被硬删后历史仍可用。删除与回滚不复用通知行已有的版本号，
总是取「该通知历史最大版本 + 1」，因此 `(notification_oid, version)` 始终唯一。

### 1.6 已读状态（服务端，跟着帐号走）

`NOTIFICATION_READS` 每行一个 `(user_oid, notification_oid)`，存的是**读到的
版本号**而不是 bool：

| 列 | 类型 | 说明 |
| -- | ---- | ---- |
| `user_oid` | `VARCHAR2(24)` | 用户 oid；与 `notification_oid` 组成主键 |
| `notification_oid` | `VARCHAR2(24)` | 通知 oid |
| `version` | `NUMBER(19)` | 标记时该通知的 `version` |
| `read_at` | `TIMESTAMP` | 最后一次标记时间（重复标记会刷新） |

判定口径（客户端执行，服务端只提供数据）：

```ts
const isRead = (read[notice.id] ?? 0) >= notice.version;
```

- 存版本号而不是「已读/未读」：管理员编辑通知后 `version += 1`，旧值自动小于
  新版本，**不需要**给每个用户写一遍就自动重新变未读；
- `POST /{oid}/read` 由服务端读通知的**当前** `version` 落库，客户端不传版本；
- 重复标记是幂等 upsert（同一 user + 通知只有一行），只更新 `version` /
  `read_at`；
- 通知被硬删除时，其已读行在**同一事务**里清理（表上没有外键，故没有级联）；
- 已读行不参与 `GET /notifications` 的缓存：那份缓存的是对所有用户都相同的
  「列表 + 全局版本」，per-user 数据掺进去既不能共享，也会把缓存放大成
  「用户数 × 通知数」。

## 2. 通用响应外壳

成功响应：

```json
{
  "status": "ok",
  "code": 200,
  "data": {}
}
```

错误沿用项目既有约定：

| 状态码 | 场景 | `detail` 示例 |
| ------ | ---- | ------------- |
| 400 | oid 不是 24 位十六进制 | `Invalid Object ID` |
| 401 | 未登录/令牌失效 | `Could not validate credentials` |
| 403 | 当前角色不含 `admin` | `权限不足` |
| 404 | 通知不存在 | `通知不存在` |
| 422 | 字段缺失/空白/超长 | 中文校验信息 |

管理端每个请求都会用 `get_current_user` 重新读取数据库中的**当前角色**；JWT
里的旧权限快照不参与判断。

## 3. 用户端：读取当前有效通知

```
GET /api/v1/notifications
Authorization: Bearer <access_token>
```

任何已登录用户可调用。只返回 `active = true` 的通知，按 `created_at` 倒序。

```json
{
  "status": "ok",
  "code": 200,
  "data": {
    "version": 42,
    "notifications": [
      {
        "id": "0d00000000000000000000aa",
        "title": "关于本周义工活动调整的通知",
        "content": "……",
        "version": 3,
        "content_hash": "9f2c…（64 位 hex）",
        "author_id": "000000000000000000000001",
        "author_name": "张老师",
        "created_at": "2026-10-21T09:30:00",
        "updated_at": "2026-10-21T10:15:00",
        "active": true
      }
    ]
  }
}
```

字段说明：

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `version` | int | **全局**通知版本；用于和本地 `lastGlobalVersion` 比较 |
| `notifications[].id` | string | 通知稳定 id（24 位小写十六进制），本地标记的键 |
| `notifications[].version` | int | **单条**修订号，单调递增 |
| `notifications[].content_hash` | string | 当前内容的 sha256（64 位小写 hex），见 §1.4 |
| `notifications[].author_id` | string | 发布人用户 oid，不可变 |
| `notifications[].author_name` | string | 发布当时显示名，不可变 |
| `notifications[].created_at` | string | 发送时间，`YYYY-MM-DDTHH:MM:SS`（naive） |
| `notifications[].updated_at` | string | 最后一次编辑/停用时间 |
| `notifications[].active` | bool | 用户端当前恒为 `true`；管理端可能为 `false` |

> **响应结构不变**：`GET /api/v1/notifications` 的 `data` 里**不会**出现任何
> per-user 字段。已读状态走下面的独立端点。

### 3.1 读取当前账号的已读状态

```
GET /api/v1/notifications/read
Authorization: Bearer <access_token>
```

任何已登录用户可调用，返回**该用户全部**已读行（不按通知是否还存在过滤；
多出来的键客户端自己忽略）。

```json
{
  "status": "ok",
  "code": 200,
  "data": {
    "read": {
      "0d00000000000000000000aa": 3,
      "0d00000000000000000000bb": 1
    }
  }
}
```

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| `read` | object | 键 = 通知 oid，值 = 该用户读到的版本号；没有已读记录时是 `{}` |

### 3.2 标记某条通知已读

```
POST /api/v1/notifications/{oid}/read
Authorization: Bearer <access_token>
```

任何已登录用户可调用（不需要 `admin`），无请求体。服务端读取该通知的**当前**
`version` 落库：

```json
{
  "status": "ok",
  "code": 200,
  "data": {
    "oid": "0d00000000000000000000aa",
    "version": 3
  }
}
```

- 幂等：重复调用不会报错、不会产生多行，只把 `version` / `read_at` 刷新为
  最新；用户连点两次与点一次等价；
- 版本以**服务端当前值**为准，客户端不能补写更大的版本号；
- 不推进全局 `version`，也不推进单条 `version`；
- 通知不存在 → `404 {"detail":"通知不存在"}`；oid 不是 24 位十六进制 →
  `400 {"detail":"Invalid Object ID"}`。

## 4. 管理端

### 4.1 列出全部通知（含停用）

```
GET /api/v1/notifications/admin
```

需要当前角色含 `admin`。返回形状与用户端相同，但包含 `active = false` 的行，
按 `created_at` 倒序。

### 4.2 创建/发送通知

```
POST /api/v1/notifications
Content-Type: application/json

{
  "title": "标题（1–200 字符）",
  "content": "正文（1–4000 字符，且 UTF-8 编码后不超过 4000 字节）",
  "verification_token": "<5 分钟密码再认证令牌>"
}
```

需要 `admin`。创建即为发送（`active = true`），全局版本 +1，单条版本为 1。
`author_id` / `author_name` 由服务端从当前登录用户解析，不接受客户端传入。

> **密码再认证**：`verification_token` 由 `POST /api/users/auth` 的 `purpose=verify` 用密码换出，5 分钟有效。缺失、过期、伪造或非本人 → `403 {"detail":"需要密码验证后才能继续"}`。下文的编辑、删除、回滚同理。

成功 `201`：

```json
{
  "status": "ok",
  "code": 201,
  "data": {
    "notification": { "...同用户端单条通知..." },
    "version": 43
  }
}
```

### 4.3 编辑/停用/重新启用

```
PUT /api/v1/notifications/{oid}
Content-Type: application/json

{
  "title": "可选，新标题",
  "content": "可选，新正文",
  "active": false,
  "verification_token": "<5 分钟密码再认证令牌>"
}
```

`title` / `content` / `active` 至少提供一个；未提供的字段保持不变。成功时：

- 单条 `version += 1`；
- 全局 `version += 1`；
- `updated_at` 刷新；
- `author_id` / `author_name` / `created_at` 不变。

`active = false` 是「停用」：用户端不再返回，管理端仍可见，可再用
`active = true` 恢复（会再次推进单条版本）。

成功 `200`：

```json
{
  "status": "ok",
  "code": 200,
  "data": {
    "notification": { "...更新后的通知..." },
    "version": 44
  }
}
```

### 4.4 删除

```
DELETE /api/v1/notifications/{oid}
Content-Type: application/json

{ "verification_token": "<5 分钟密码再认证令牌>" }
```

需要 `admin` + 密码再认证（`DELETE` 也走 body，与附件/用户端点一致；缺令牌 → 403）。硬删除，成功返回 `204 No Content`；全局版本 +1。审计日志仍保留
删除动作和当时标题。通知不存在时 `404`，版本不变。

> 管理界面如果希望保留历史记录，请使用 `PUT {"active": false}` 停用，而不是
> `DELETE`。硬删除前会先落一条 `op = delete` 快照，因此仍可通过
> §4.6 回滚恢复（沿用原 `id`）。

### 4.5 版本历史

```
GET /api/v1/notifications/{oid}/versions
```

需要 `admin`。按 `version` **倒序**返回该通知的完整修订历史；通知已被删除时
同样可查（快照表独立于通知行）。没有历史时返回空数组。

```json
{
  "status": "ok",
  "code": 200,
  "data": {
    "version": 46,
    "versions": [
      {
        "version": 3,
        "content_hash": "…64 位 hex…",
        "title": "标题",
        "content": "正文",
        "active": true,
        "op": "rollback",
        "source_version": 1,
        "author_id": "000000000000000000000001",
        "author_name": "张老师",
        "created_at": "2026-10-21T10:20:00"
      }
    ]
  }
}
```

| 字段 | 说明 |
| ---- | ---- |
| `version` | 全局通知版本，与本列表同一事务读出 |
| `versions[].version` | 该版修订号 |
| `versions[].content_hash` | **该版**内容哈希（不是当前内容） |
| `versions[].op` | `create` / `update` / `delete` / `rollback` |
| `versions[].source_version` | 仅 `rollback` 行有值：回滚到的目标版本 |
| `versions[].author_id` | 触发该次写入的账号 oid；回滚/编辑时是操作人 |
| `versions[].author_name` | 触发该次写入时的显示名 |

### 4.6 回滚到指定版本

```
POST /api/v1/notifications/{oid}/rollback
Content-Type: application/json

{ "version": 2, "verification_token": "<5 分钟密码再认证令牌>" }
```

需要 `admin` + 密码再认证。把 `title` / `content` / `active` 还原成目标快照的内容：

- 通知仍存在 → 与编辑同路径（行锁 + `version += 1`）；
- 通知已被删除 → 用**原 `oid`** 重新插入，`version` = 该通知历史最大版本 + 1。
  这样客户端按 `id` 保存的「已阅 / 不再显示」标记仍然有意义；
- 无论哪条路径，都追加一条 `op = rollback`、`source_version = 目标版本` 的快照，
  并把全局版本 +1；
- 回滚只还原内容：发布人与 `created_at` 仍是不可变事实，不会被目标快照覆盖。

成功 `200`：

```json
{
  "status": "ok",
  "code": 200,
  "data": {
    "notification": { "...回滚后的通知..." },
    "version": 47,
    "content_hash": "…新快照的哈希…"
  }
}
```

错误：

| 状态码 | 场景 | `detail` |
| ------ | ---- | -------- |
| 400 | `version` 缺失/非整数/小于 1 | `版本号非法` |
| 400 | oid 不是 24 位十六进制 | `Invalid Object ID` |
| 404 | 该通知没有这个版本 | `版本不存在` |

## 5. 客户端状态算法（推荐）

已读状态在服务端（§1.6），「不再显示」仍是本设备偏好：

```ts
type ClientState = {
  lastGlobalVersion: number;
  // 已读：服务端 /read 返回的映射（内存态，不落 localStorage 也够用）
  read: Record<string, number>;
  // 不再显示：按 id 永久压制（不因编辑后 version 变大而重新出现）
  suppressed: Record<string, true>;
};
```

打开应用时：

1. `GET /api/v1/notifications`，拿到 `server.version` 和当前有效通知列表。
2. `GET /api/v1/notifications/read`，拿到该账号的 `read` 映射（跨设备一致）。
3. 对每条通知：
   - 若 `suppressed[notice.id]` 存在 → **永不展示**，跳过；
   - 否则若 `notice.version > (read[notice.id] ?? 0)` → 视为未读，展示；
   - 否则视为已读，不提示。
4. 处理完后把 `lastGlobalVersion = server.version` 写回本地。

用户点击 **「已阅」**：

```ts
await fetch(`/api/v1/notifications/${notice.id}/read`, { method: "POST" });
read[notice.id] = notice.version; // 用响应里的 data.version 更稳
// 不改变 lastGlobalVersion
```

用户点击 **「不再显示」**：

```ts
suppressed[notice.id] = true; // 落 localStorage
// 不调用服务端；该 id 以后无论 version 如何变化都不再展示
```

> `不再显示` 不进服务端：同一账号在手机上压制、在平板上可能仍想看，它是设备级
> 偏好，不是账号事实。

> 重要：`不再显示` 必须按 `id` 判断，而不是只比较 `version`。管理员编辑通知会
> 让 `notice.version` 变大；若只用「version > 压制时的 version」判断，被压制的
> 通知会重新出现，违反「该设备永不再显示」的语义。

### 5.1 竞态说明

服务端读取列表时先读全局版本、再读通知行。若管理员恰好在两次读取之间创建通知，
响应可能返回「旧版本 + 包含新通知」；客户端会处理到新通知并写入旧版本，下次
打开时版本变大再刷新一次，**不会漏通知**。反过来（新版本 + 缺通知）不会发生。

标记已读与管理员编辑并发时，落库的可能是编辑前的版本号；客户端下次 `GET /read`
会读回该值，`notice.version > read[oid]` 成立即再次提示未读。不会静默吞掉通知。

## 6. 数据库与迁移

- 新表：
  - `NOTIFICATIONS`：`OID`、`TITLE`、`CONTENT`、`ACTIVE`、`AUTHOR_OID`、
    `AUTHOR_NAME`、`VERSION`、`CREATED_AT`、`UPDATED_AT`；
  - `NOTIFICATION_STATE`：单行 `ID = 1`、`VERSION`、`UPDATED_AT`；
  - `NOTIFICATION_VERSIONS`：`OID`、`NOTIFICATION_OID`、`VERSION`、`TITLE`、
    `CONTENT`、`ACTIVE`、`OP`、`SOURCE_VERSION`、`CONTENT_HASH`、`AUTHOR_OID`、
    `AUTHOR_NAME`、`CREATED_AT`（append-only 内容快照）；
  - `NOTIFICATION_READS`（V053）：`USER_OID`、`NOTIFICATION_OID`、`VERSION`、
    `READ_AT`；主键 `(USER_OID, NOTIFICATION_OID)`，无外键、无 `OID` 列。
- 索引：`ix_notifications_active_created (ACTIVE, CREATED_AT)`、
  `ix_notification_versions_doc (NOTIFICATION_OID, VERSION)`；唯一约束
  `uq_notification_versions (NOTIFICATION_OID, VERSION)`。`NOTIFICATION_READS`
  不额外建索引：主键索引的 `USER_OID` 前缀服务 `GET /read`，按通知清理可用
  skip scan。
- 存量实例：`db/oracle/ddl/V015__add_notifications.sql`、
  `db/oracle/ddl/V017__add_notification_versions.sql`、
  `db/oracle/ddl/V053__notification_reads.sql`（均幂等）。
- 全新实例：`V001__initial_schema.sql` 已包含同样对象和 `id=1` 种子。
- `db/models.py` 已镜像；修改 DDL/模型后运行：

```bash
.venv/bin/python -m db.ddl_check
```

### 6.1 发布顺序（重要）

新接口读写 `NOTIFICATION_READS`，表不存在时是 `ORA-00942` → `500`。所以
**先上表、再发后端**：

```bash
.venv/bin/python misc/apply_ddl.py --dry-run v053   # 只解析，不连库
.venv/bin/python misc/apply_ddl.py v053             # 发版窗口内手工执行
```

DDL 不会在应用启动时自动执行。已读状态是新增能力，表缺失期间旧接口行为不变
（前端降级为本地记录即可）。
