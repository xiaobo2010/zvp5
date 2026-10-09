# STATUS — 义工管理模块移植（zvp → zvms4-modified）

> 日期：2026-10-09
> 任务：把现役 zvp（`~/zvp-build-test`，zvp-latest）的**义工管理**（创建义工 / 添加、修改义工成员）移植进本仓库，并对移植面做「外链 / 翻出平台」危险代码审计。
> 审计方式：静态扫描（grep / AST）+ 全量人工读码 + 独立安全复核；未运行服务、未连接数据库。

---

## 1. 后端端点对齐总表（`zvms4-backend-python`）

| 端点 | zvp 侧能力 | 本仓处理 | 状态 |
|---|---|---|---|
| `GET /api/v3/activities` | 全校列表：分页 / 关键词 / 类型 / 状态过滤 + creator 联表返回 `creator_name` | 新增（`routers/activities_v3_router.py:22`） | 已移植 |
| `POST /api/v3/activities` | 简化创建：body `{name, description, type}` → 201 `{id}` | 新增（:156）；适配见 §3 | 已移植（适配） |
| `GET /api/v3/activities/{aid}/members` | 快速成员列表：`$lookup` `zvms_new.users` 返回 `member_name` | 新增（:197） | 已移植 |
| `POST /api/v3/activities/{aid}/members` | 批量加成员：预校验类型匹配、去重（同 member+mode 时长累加）、插入置 `status=effective`、活动回置 `pending`、204 空体 | 整体替换上游旧实现（:312）；body 换 `NewActivityMember {member, mode, duration>0}` | 已移植（替换） |
| v2 全套（创建 / 详情 / 删除 / 成员增删查 / 改时长 / 改状态 / 改信息） | 与上游等价 | 上游已有，`routers/activities_v2_router.py` 未动 | 已存在 |
| `POST /api/imports/activities`（Excel 导入义工） | — | 与 zvp 侧**逐字节相同**（diff 验证） | 已一致，未动 |

## 2. 前端接线（`zvms4-frontend`，最小改动，UI 设计未动）

| 文件 | 变更 |
|---|---|
| `types/activity.v2.d.ts:34` | 新增 `NewActivityMember {member, mode, duration}`（经 `types/v2.ts` `export *` 自动再导出） |
| `src/api/activity/member.ts:18` | `insertMany` 改造：body 换 `NewActivityMember[]`，适配 204 空体（删掉响应解析） |
| `src/api/activity/member.ts:60` | 新增 `reads_v3`（GET `/v3/activities/{aid}/members`，返回含 `member_name`） |
| `src/api/activity/read.ts:66,99` | 新增 `campus_v3`（GET `/v3/activities`，含 `creator_name` / `total`） |
| `src/components/activity/ZActivityMemberList.vue:174` | 成员列表 `reads` → `reads_v3`（v2 路径在实库上会**漏掉不在 legacy `zvms.users` 里的成员**，v3 直连 `zvms_new.users`） |
| `src/components/activity/ZActivityMemberList.vue:266` | 批量添加构造 `{member, mode, duration}`（去掉 `_id/activity/status`） |
| `src/components/activity/getActivity.ts:26` | campus 分支按角色条件切换：admin/department → `campus_v3`；学生保持 v2 兜底（v3 有 403 门） |

单条加成员（`member.insert` → v2）、`searchActivity`、模板与样式：未动。

## 3. 与 zvp 的有意适配（差异清单）

| # | zvp 原样 | 本仓适配 | 原因 |
|---|---|---|---|
| 1 | `date/createdAt/updatedAt` 写 **int 毫秒时间戳** | 写 `datetime.now()`（BSON Date） | 实库 `activities` 日期为 BSON `Date` 且零 null（DB_ALIGNMENT §2/§6），严格 typing `Activity.model_validate` 要求 |
| 2 | v3 创建只插 7 个字段 | 补齐 `appointee=creator`、`approver=""`、`place=""`、`origin="activities"` | 实库全字段非空，缺字段会让 v2 读取路径 `model_validate` 崩 |
| 3 | `user.require('admin','department')`（CurrentUser 对象） | `"admin"/"department" in user.get("per", [])`（本仓 dict 风格） | 两侧鉴权层不同，语义等价 |
| 4 | `db.zvms_new.activities` 属性访问 | `db.zvms_new.get_collection("activities")` | 本仓集合访问惯例 |
| 5 | 创建日志带 level/type 参数 | 仅 `with_text` | 本仓 `inject_log` 不支持 level/type |
| 6 | 校验失败 detail 文案「权限不足」（volunteer_member 侧） | 「Permission denied」 | 与同文件上游 volunteer.py 文案一致；行为（抛 403）一致 |

## 4. 权限对齐（本次根因修复，含一个上游旁路封堵）

**根因**：JWT `per` 由 `util/cert.py:106` 从 `zvms_new.users.roles` 签发，词汇为 `student/secretary/department/admin`；而上游权限助手查 `user.get("perm")` / `"volunteer"`——对现役库**永不匹配**，department 用户在 v2 全部义工写操作被 403。
修复：`util/permission/volunteer.py`、`util/permission/volunteer_member.py` 统一改为 `user.get("per", [])` 含 `admin|department`（zvp 语义）。

**连带发现并封堵的上游权限旁路**：`volunteer_member` 两个校验器原为「返回 `False` 不抛异常」，而 v2 三个调用点（加成员 `activities_v2_router.py:323`、删成员 `:371`、改成员记录 `:529`）`await` 后**忽略返回值**直接落库 → 任何登录用户可写成员数据。现改为与 zvp 一致的**抛 403** 语义（全仓已确认无调用方依赖旧返回值行为），一处改动同时封三个调用点。

## 5. 安全审计结论

**移植面（§1/§2 全部文件）**：无 `requests/httpx/urllib/aiohttp`、无绝对 URL / 外链生成、无 `window.open` / `location` 外跳、无 `eval/exec/subprocess`、无 base64 payload、无硬编码主机 / IP / 凭证。NoSQL 注入面：`query` 一律 `re.escape` 后进 `$regex`；`type/status` 为 `Literal` 白名单；`activity_id`/`member` 走 `validate_object_id`。

**范围外危险代码（zvp 侧存在，均未移植、未触碰）**：

| 位置（zvp 侧） | 内容 | 处置 |
|---|---|---|
| `frontend/src/api/xh.js:12,31` | 学海外呼（`admin.yunzuoye.net` / `ztp.yunzuoye.cloud`）签名代理 | 不移植；义工管理页面无引用 |
| `frontend/src/views/manage/ManageHome.vue:200` | `window.location` 硬编码跳转外部站点 | 不移植 |
| `frontend/src/utils/constants.ts:2,4` | 外部图标 URL | 不移植 |
| `backend/routers/xh_router.py`、`routers/v1/platform.py` | 学海代理 / 平台登录体系 | 不在义工管理范围，未触碰 |

**部署注意（上游既有配置，本次未动）**：`zvms4-frontend/src/plugins/axios.ts:21` `baseURL = 'https://zvp.xiaobopisces.top/api/'` 指向现役 zvp 域名——替换上线时必须改为相对路径 `/api/` 或新域名。

## 6. 验证记录

- 后端：`python3 -m py_compile`（v3 路由 + 两个权限文件）exit 0；危险模式 grep 零命中。
- 前端：`npm ci --legacy-peer-deps`（上游 `unplugin-oxlint`×`oxlint` peer 冲突为纯 dev 工具问题）后 `vue-tsc --noEmit`：**改动文件 0 错误**；存量 6 处上游遗留错误（`App.vue` PWA 虚拟模块/watermark、`ZSelectActivityMode.vue` hybrid 图标、`icons/positions/index.ts`、`plugins/ua.ts`）与本次改动无关。
- 独立安全复核（oracle）：两次派发均因 oracle 模型变体不可用（基础设施故障，非任务问题）失败；改由编排者按同一检查单逐项替代复核（v3 路由 404 行全文通读 + 证据）：
  - 外链 / 外呼 / `eval` / `subprocess` / `print` / 整型时间戳：终局 grep 8 个改动文件（后端 3 + 前端 5）**零命中**（`SWEEP_EXIT=1`）。
  - 注入面：`query` 一律 `re.escape` 后进 `$regex`；`type/status` 为 `Literal` 白名单；`activity_id` 走 `validate_object_id`；成员 body 走 pydantic `NewActivityMember`（`duration gt=0`）。
  - 权限：三处写门均为 `admin|department`（`user.get("per")`）；全仓 grep 确认无调用方依赖 `volunteer_member` 旧「返回 False」语义（v2 三处调用点均为 `await` 后忽略返回值，抛异常语义即正确封堵）。
  - 数据完整性：v3 创建全字段齐备（含 `appointee/approver/place/origin`）且日期为 `datetime`（BSON Date）；`origin="activities"` 属合法 `Literal`；批量加成员「先全量预校验、后写入」、活动回置用 `datetime.now()`；`$convert onError/onNull` 防脏 ObjectId；成员计数切片 `pipeline[:5]` 与「条件 `$match` 恒为阶段」结构一致。
  - **结论：通过。** 注意：本审计为静态验证（本机无后端依赖环境），上线替换前建议在有 Atlas 连接的环境跑一次运行时冒烟（建义工 → 批量加成员 → 列表 / 成员读取回环）。

## 7. 已知遗留 / 偏差

1. `get_user_name` 仍读 legacy `zvms.users`（zvp 同款行为，异常安全回退 "Unknown"）：仅在 `zvms_new.users` 的用户在日志里显示 "Unknown"。两侧一致，未改。
2. v2 单条加成员仍按 zvp 相同方式读 legacy `zvms.users` 校验目标用户——批量主路径已切 v3，单条路径维持两侧一致。
3. `imports_router.py:32` `if not validate_activity_name(name)` truthiness bug（校验永不生效）：zvp 与本仓逐字节相同，维持一致未修。
4. v3 简化创建 `approver=""`、`appointee=creator`、`origin="activities"`：满足本仓严格 typing 的有意适配（见 §3）。
5. v3 `GET members` 无角色门（登录即可读成员姓名）：与 zvp 及本仓 v2 成员端点姿态一致，校内场景可接受。