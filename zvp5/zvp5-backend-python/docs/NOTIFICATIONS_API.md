# 站内通知 API（`/api/v3/notifications`）

本文是前端接入的唯一契约。后端实现只负责「谁发的、什么时候发的、内容是什么、
全局/单条版本号是多少」；**已阅 / 不再显示都是客户端本地状态**，服务端不保存
每个用户的阅读记录，也不因本地操作推进任何版本号。

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
| 用户点击「已阅」/「不再显示」 | 不变（服务端无请求） |

因此客户端只需保存上次看到的全局版本：服务端 `version` 更大时拉一次完整列表；
相等时可继续用本地缓存。

### 1.2 单条通知版本 `version`

每条通知有独立的修订号：

- 创建时 `version = 1`；
- 每次成功编辑标题、内容或 `active`（包括停用/重新启用）`version += 1`；
- 单条通知的版本变化**不会**影响其他通知的版本。

客户端用它与本地的「已阅标记」比较：`notice.version > localReadVersion[notice.id]`
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
GET /api/v3/notifications
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

## 4. 管理端

### 4.1 列出全部通知（含停用）

```
GET /api/v3/notifications/admin
```

需要当前角色含 `admin`。返回形状与用户端相同，但包含 `active = false` 的行，
按 `created_at` 倒序。

### 4.2 创建/发送通知

```
POST /api/v3/notifications
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
PUT /api/v3/notifications/{oid}
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
DELETE /api/v3/notifications/{oid}
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
GET /api/v3/notifications/{oid}/versions
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
POST /api/v3/notifications/{oid}/rollback
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

## 5. 客户端本地状态算法（推荐）

服务端不提供「标记已读」接口，以下状态只存本设备/本账号：

```ts
type LocalState = {
  lastGlobalVersion: number;
  // 已阅：记录阅读时的单条 version
  read: Record<string, number>;
  // 不再显示：按 id 永久压制（不因编辑后 version 变大而重新出现）
  suppressed: Record<string, true>;
};
```

打开应用时：

1. `GET /api/v3/notifications`，拿到 `server.version` 和当前有效通知列表。
2. 删除本地 `read` / `suppressed` 中已不在列表里的 id（可选；`suppressed`
   建议保留，防止通知停用后再启用时重新出现）。
3. 对每条通知：
   - 若 `suppressed[notice.id]` 存在 → **永不展示**，跳过；
   - 否则若 `notice.version > (read[notice.id] ?? 0)` → 视为未读，展示；
   - 否则视为已读，不提示。
4. 处理完后把 `lastGlobalVersion = server.version` 写回本地。

用户点击 **「已阅」**：

```ts
read[notice.id] = notice.version;
// 不调用服务端，不改 lastGlobalVersion
```

用户点击 **「不再显示」**：

```ts
suppressed[notice.id] = true; // 或记录当前 notice.version 作为压制标记
// 不调用服务端；该 id 以后无论 version 如何变化都不再展示
```

> 重要：`不再显示` 必须按 `id` 判断，而不是只比较 `version`。管理员编辑通知会
> 让 `notice.version` 变大；若只用「version > 压制时的 version」判断，被压制的
> 通知会重新出现，违反「该设备/账号永不再显示」的语义。

### 5.1 竞态说明

服务端读取列表时先读全局版本、再读通知行。若管理员恰好在两次读取之间创建通知，
响应可能返回「旧版本 + 包含新通知」；客户端会处理到新通知并写入旧版本，下次
打开时版本变大再刷新一次，**不会漏通知**。反过来（新版本 + 缺通知）不会发生。

## 6. 数据库与迁移

- 新表：
  - `NOTIFICATIONS`：`OID`、`TITLE`、`CONTENT`、`ACTIVE`、`AUTHOR_OID`、
    `AUTHOR_NAME`、`VERSION`、`CREATED_AT`、`UPDATED_AT`；
  - `NOTIFICATION_STATE`：单行 `ID = 1`、`VERSION`、`UPDATED_AT`；
  - `NOTIFICATION_VERSIONS`：`OID`、`NOTIFICATION_OID`、`VERSION`、`TITLE`、
    `CONTENT`、`ACTIVE`、`OP`、`SOURCE_VERSION`、`CONTENT_HASH`、`AUTHOR_OID`、
    `AUTHOR_NAME`、`CREATED_AT`（append-only 内容快照）。
- 索引：`ix_notifications_active_created (ACTIVE, CREATED_AT)`、
  `ix_notification_versions_doc (NOTIFICATION_OID, VERSION)`；唯一约束
  `uq_notification_versions (NOTIFICATION_OID, VERSION)`。
- 存量实例：`db/oracle/ddl/V015__add_notifications.sql`、
  `db/oracle/ddl/V017__add_notification_versions.sql`（均幂等）。
- 全新实例：`V001__initial_schema.sql` 已包含同样对象和 `id=1` 种子。
- `db/models.py` 已镜像；修改 DDL/模型后运行：

```bash
.venv/bin/python -m db.ddl_check
```
