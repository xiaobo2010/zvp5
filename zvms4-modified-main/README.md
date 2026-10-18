# ZVP Modified v4

> **当前运行状态（2026-10-17）**：Oracle Autonomous Database 是唯一运行时数据库，
> MongoDB 代码与依赖已移除。本文保留早期移植与双库阶段的工程记录；部署、配置和
> 数据库口径以 `zvms4-backend-python/README.md`、`db/oracle/` 与
> `docs/ADB_SCHEMA_SPEC.md` 为准。

> 基于上游 **zvms4**（`zvms/zvms4-backend-python` + `zvms/zvms4-frontend`）的定制版本。
> **改动方向一：数据库层向现役 zvp 平台的真实结构对齐。**
> **改动方向二（2026-10-09 起）：义工管理模块（创建义工 / 添加修改义工成员）按指令从现役 zvp 移植，经逐文件安全审计放行，见 `STATUS.md` Part 1。**
> **改动方向三（2026-10-10 起）：用户管理与登录（登录语义补齐 / 用户·班级 v2/v3 API / 设备-绑定-外链三类禁止面清除）按指令从现役 zvp 移植，经全树终局 grep + 写后自审放行，见 `STATUS.md` Part 2。**
> **改动方向四（2026-10-10 起）：日志 API 移植与汉化（type/level 业务语义 + 35 站点全面行为记录 + 管理端过滤），日志端点 GET-only 防篡改，并复查清除历史残面，见 `STATUS.md` Part 3。**
> **改动方向五（2026-10-10 起）：登录与身份验证 API 移植（TOTP 动态码按用户派生 / 重置为初始密码 v3 / 登出与改密语义修复），登录/鉴权路径零外呼，见 `STATUS.md` Part 4。**
> **改动方向六（2026-10-11 起）：后端可用状态落地——shield 请求加固中间件按 zvp 语义改写移植（剥离设备会话/学海面），运行缺口修复（依赖缺失 / example 配置字段缺失 / RSA 1024 / 关停竞态），并完成真实运行时端到端验证（41/41 断言），见 `STATUS.md` Part 8。**
> **改动方向七（2026-10-12 起）：双库断裂面系统性收敛——群组与用户读取全面双库合并（权限组列表与成员、用户搜索去重、roles 合并、班级选择器修复），PWA SW 即时接管修复旧客户端首页 404，见 `STATUS.md` Part 10。**
> **改动方向八（2026-10-14 起，10-15 修）：设备追踪——`APP_LOGS` 记录请求设备的学海 ID（前端 `Xuehai-Id` 头 → `xuehai_id` 列），`GET /api/logs` 新增 `xuehai_id` 过滤 + `ix_app_logs_xuehai_id` 索引；前端取 ID 支持**双客户端**（学海浏览器 `xhBrowserJava` 桥 + 资料中心 App UA `userId/`，修复此前只读桥导致资料中心一条都采不到）；设计见 `docs/DEVICE_TRACKING_DESIGN.md`。**
> 其余前端 UI 与后端业务逻辑仍以上游 zvms4 为基准，不采用 zvp 的代码（zvp 代码经检查存在漏洞与后门，仅提取其数据结构；义工管理与用户管理移植面经扫描确认无外链 / 外呼 / 越权代码，范围外危险代码一律未引入，上游自带的设备采集 / 外呼代码已在用户管理面上清除）。**学海 ID 设备采集为 2026-10-13 起另行设计并落地的独立能力**（仅将请求头写入 `APP_LOGS.XUEHAI_ID`，不恢复 zvp 的设备会话 / 登录绑定 / 外呼面），见 `docs/DEVICE_TRACKING_DESIGN.md`。
>
> **文件位置变更（2026-10-12）**：本目录原 `DB_ALIGNMENT.md`（数据库对齐 Ground Truth）已并入**仓库根 `README.md` §三**，本目录不再有该文件。下文出现的 `DB_ALIGNMENT.md` / `DB_ALIGNMENT §N` 一律对应**根 README §三 / §三.N**。

---

## 1. 目录结构

```
zvp-modified-v4/
├── README.md                 # 本文件
├── STATUS.md                 # 模块移植：Part 1 义工管理（10-09）/ Part 2-4 用户·日志·登录（10-10）/ Part 5 全量 API 盘点（10-10）/ Part 6 弱密码强制改密（10-10）/ Part 7 token 有效期调整（10-10）/ Part 8 可用状态落地：运行时验证 + shield 加固 + 运行缺口修复（10-11）/ Part 9 部署切换·logo·四 bug 修复·CHANGELOG 页面（10-11）/ Part 10 双库收敛与 PWA 接管（10-12）/ Part 11 前端体验批次（10-12）/ Part 12 CHANGELOG 数据库化·管理员可编辑（10-12）
├── DB_ALIGNMENT.md           # 已并入仓库根 README §三（数据库对齐 Ground Truth），本目录不再有该文件
├── docs/                     # 设计稿（ADB 迁移/规范、FILES_API_DESIGN.md、DEVICE_TRACKING_DESIGN.md 等）
├── zvms4-backend-python/     # FastAPI 后端（上游 zvms4，DB 层已对齐；义工/用户/日志/登录 v2-v3 已移植；shield 加固就位，已通过 41 项运行时冒烟）
│   ├── README.md
│   ├── requirements.txt
│   ├── config/               # supervisor.conf / nginx 样例
│   └── ...
└── zvms4-frontend/           # Vue3 + Vite 前端（上游为基准；仅义工管理 API 接线改动，见 STATUS.md §2）
```

> **入库路径**：本目录在 GitHub 仓库 `xiaobo2010/zvp5` 中对应 `zvms4-modified-main/`（本地目录名 `zvp-modified-v4` 与入库目录名不同，同步时注意映射）。

## 2. 这份代码从哪来、为什么

| 部分 | 来源 | 说明 |
|---|---|---|
| 前端 | 上游 zvms4 为基准 + zvp 接线 | 仅义工管理与用户管理 API 接线改动（见 `STATUS.md` §2 / §9），UI 与模板未动 |
| 后端逻辑 | 上游 zvms4 为基准 | 除下述义工管理与用户管理移植面外，业务逻辑、路由、权限均保持 zvms4 |
| 义工管理 v3 层 | **现役 zvp**（2026-10-09 移植） | `routers/activities_v3_router.py` 四端点 + 权限修复，逐文件安全审计后放行，见 `STATUS.md` Part 1 |
| 用户管理与登录 v2/v3 层 | **现役 zvp**（2026-10-10 移植） | users/groups v2 列表·详情 + v3 建/改角色/建班/成员进出 + 登录默认密码与 profile，见 `STATUS.md` Part 2；设备 / 绑定 / 外链三类禁止面未迁移，上游同源残留已清除（学海 ID 日志采集为后置独立能力，见改动方向八） |
| 日志系统 | **现役 zvp**（2026-10-10 移植） | logs API 业务面（过滤/分页/user_name 联查）+ type/level 语义 + 35 站点汉化与补埋点 + GET-only 防篡改，见 `STATUS.md` Part 3 |
| 登录与身份验证 | **现役 zvp**（2026-10-10 移植） | TOTP 动态码登录（按用户派生，RFC6238 本地自实现）+ 重置为初始密码 v3 端点 + 登出/改密语义修复；设备绑定 / 会话指纹 / 外呼投递 / recovery 问答拒绝移植，见 `STATUS.md` Part 4 |
| shield 请求加固 + 可用状态 | **现役 zvp**（2026-10-11 移植/改写） | 路径黑名单 + HTTP 方法白名单 + UA 门禁（418 + security_alert 日志）；设备会话 cookie、学海 UA 分支、调试路由、全量请求头落盘一律剥离；伴随运行缺口修复与 41 项端到端冒烟验证，见 `STATUS.md` Part 8 |
| 双库读取收敛 + PWA 接管 | **现役 zvp 语义 + 数据真相驱动**（2026-10-12） | 权限组列表与成员、用户搜索去重与 roles 合并、班级选择器全面双库兼容；workbox skipWaiting/clientsClaim 让旧 PWA 客户端一次访问自动升级，见 `STATUS.md` Part 10 |
| 后端数据库层 | 向**现役 zvp** 对齐 | 改动见 §4 |

**zvp（现役平台）贡献「数据在库里长什么样」**：包名、集合名、字段名 / 类型。除经安全审计放行的义工管理移植面外，它的代码（含后门）一律不引入。

## 3. 现役数据库结构（权威依据）

现役 zvp 连接的是 **MongoDB Atlas 远程集群**，使用两个库：

- **`zvms`**（遗留库）：`users`(`id,name,sex,group[],password,past`)、`groups`(`name,description,permissions[],type`)、`tasks`、`logs`、`imports`、`notifications`；`activities` 为空。
- **`zvms_new`**（现役主库）：`users`(`id,name,roles[],class_id,password,session_id`)、`groups`(`name`)、`activities`、`activity_members`、`time`、`time_academic_year`、`time_with_origin`、`time_with_origin_stat`、`indicators`、`group_indicators`、`daily_exports`、`logs`。

完整字段 / 类型 / 文档数、以及「哪些端点用哪个库」的规律，见 **仓库根 `README.md` §三 数据库对齐（Ground Truth）**（由 SSH 只读 dump 建立，是目前唯一权威依据）。

## 4. 本次对齐做了什么

**规则**：v2 / v3 与 exports / imports、登录鉴权路径统一改用 `zvms_new` 的新 schema；v1（用户列表、班级 v1）与 `util/*` 保持遗留 `zvms.*` —— 与现役 zvp 的集合归属完全一致。

- **查询层**：5 个文件 11 处集合目标切换，含字段映射（旧 `user["group"]/sex/past` → 新 `roles/class_id`）。逐条见根 README §三.5。
  - `util/cert.py`（登录鉴权读/写切到 `zvms_new.users`，权限取 `roles`）
  - `routers/users_router.py`（改密端点）
  - `routers/users_v2_router.py`
  - `routers/exports_router.py`（用户/班级来源切换，班级按 `class_id`）
  - `routers/imports_router.py`
- **模型层**：`typings/group.py` 的 `UserPosition` 枚举删去实库与 zvp 均不存在的 `auditor/system/inspector`。
  - `typings/activity_v2.py` **保持上游严格版本**（`datetime` + 非空）：实库 `activities` 的日期为 BSON `Date` 且零 null，若改用 zvp 的 `int`/可空版本会让 `Activity.model_validate` 直接失败。见根 README §三.6。
- **未改动**：`database.py`（与 zvp 逐字节相同，已连两库）、其余业务逻辑。前端本次改动仅为义工管理 API 接线（见 `STATUS.md` §2），模板 / 样式未动。

验证：`python3 -m compileall zvms4-backend-python` 通过；改动集中在上述 6 个文件。

## 5. 配置与运行

> 本节在 2026-10-11 已按真实部署流程走通（Python 3.14.4 + venv + 本地/容器 MongoDB，端到端冒烟 41/41，见 `STATUS.md` Part 8）。

### 5.1 配置

```bash
cp zvms4-backend-python/settings.example.py zvms4-backend-python/settings.py
```

`settings.py` 填写：

- `MONGODB_URI`：指向现役数据所在的 MongoDB（当前为 Atlas 集群）。
- `MONGODB_DB`：按上游约定留空即可；`database.py` 固定连接 `zvms` 与 `zvms_new` 两个库。
- `LANGUAGE`：`util/calculate.py` 时长描述文案语言（`zh-CN` / 其他）。
- `CHINESE_FONT`：`util/statement.py` 导出 PDF 的中文字体路径（代码内置 fallback 链，路径不存在时自动尝试系统常见 CJK 字体）。

此外后端启动依赖同目录下的密钥文件（上游生成脚本，已升级为 2048 位）：

- `generate_key.sh` → 生成 `rsa_public_key.pem` / `rsa_private_key.pem`（2048 位） / `aes_key.txt`。

> 以上文件均属敏感凭证，禁止提交进版本库（`.gitignore` 已覆盖）。

### 5.2 运行后端

```bash
cd zvms4-backend-python
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# 生成密钥后
uvicorn main:app --host 0.0.0.0 --port 8000
```

依赖说明：`requirements.txt` 已在 2026-10-11 清理（移除 Python 3.13+ 不可安装的开发期工具 pipreqs 系与零引用的 requests，补齐 `to_excel` 必需的 openpyxl），在 Python 3.14 实测全量安装通过。

**shield 请求门禁（2026-10-11 起）**：所有请求须携带含 `Mozilla` 的 User-Agent 且路径不命中安全黑名单，否则返回 418 并写 `security_alert` 日志。浏览器前端不受影响；**外部健康检查器 / 监控探针请携带浏览器式 UA 或只做 TCP 探活**。

`config/` 内含 `supervisor.conf` 与 `nginx` 样例，可用于生产托管（路径按实际部署调整）。

### 5.3 前端

前端保持上游 zvms4，按上游方式构建（`npm install` / `npm run build`），产物由 Nginx 或后端静态目录托管。

## 6. 已知事项 / 待办

- ~~**zvp 代码待审核**：本次只吸收了 zvp 的数据结构，未采用其任何业务代码。~~ → **义工管理面已审计并移植（2026-10-09）**：v3 四端点 + 权限修复，逐文件扫描确认无外链 / 外呼 / 越权代码，范围外危险代码（学海代理 `xh_router` / `api/xh.js`、平台登录 `platform.py`、ManageHome 外站跳转、外部图标 URL）均未引入，审计记录见 `STATUS.md`。其余 zvp 模块（安全中间件、其他 v3 路由等）若要参考仍需单独安全审计。
- **用户管理与登录已审计移植（2026-10-10）**：登录语义补齐（默认密码 / profile / 5min 临时 token）+ users/groups v2/v3 端点 + 前端接线；zvp 的 platform 登录（TOTP / sid / 设备核验）与 `xh_router` 全部拒绝移植；上游自带的设备采集（`binding_user_credentials` / 三请求头 / `deviceId` 生成）已从用户管理面清除。roles 编辑后端端点已就绪、前端入口待接（见 `STATUS.md` Part 2 §14-3）。
- **日志 API 已审计移植（2026-10-10）**：`GET /api/logs` 重写（query/type/level/performer/时间过滤 + user_name），schema 加 `type/level`，35 站点全中文 + 13 处补埋点（成员移除/记录修改/活动信息/v1 用户组/导出全套）；zvp 的设备筛选参数与 `_1.py` 删库脚本拒绝移植；复查清掉 6 处历史残面（含 `get_export_file` 无鉴权下载修复）。
- **设备追踪 / 学海 ID 日志落地（2026-10-14，改动方向八）**：`APP_LOGS` 记录请求设备的学海 ID（前端 `ua.ts:getXuehaiId` + `axios.ts` 注入 `Xuehai-Id` 头 → 后端 `typings/log.py:xuehai_id_from` → `xuehai_id` 列），管理端 `GET /api/logs` 新增 `xuehai_id` 等值过滤与 `ix_app_logs_xuehai_id` 索引（`V001` fresh + `V008` 存量，`ddl_check` 无漂移），管理员可按一台平板反查用过它的平台账号。仅取 zvp 的「学海 ID 头采集」一点；设备会话 / 登录绑定 / Clarity-ID / 黑名单 / 出站代理一律不采用。**10-15 修复**：初版只读学海浏览器桥 `window.xhBrowserJava`，资料中心 App 无此桥 → 一条都采不到；现前端**双源**取 ID（桥优先 + UA `userId/` 兜底）。完整契约、信任边界与后续项见 **`docs/DEVICE_TRACKING_DESIGN.md`**。
- **登录与身份验证已审计移植（2026-10-10）**：TOTP 动态码登录（密码优先/OTP 兜底、按用户派生 secret、RFC6238 官方向量 6/6 验证）+ `DELETE /v3/users/{id}/password` 重置为初始密码（临时 token 重新验证）+ admin 动态码查看端点（`totp_view` warning 日志）+ 登出清 token / 改密死期望两处语义修复；zvp 的设备绑定、`zvp_session` 指纹、`sid` claim、recovery 问答、`/v1/platform/*` 平行端点拒绝移植（见 `STATUS.md` Part 4）。
- **zvp 全量 API 面盘点落档（2026-10-10）**：13 router ~75 端点穷举对照——已移植/等价占绝大多数，未移植总账五项（**服务端弱密码强制改密为首要候选**），四个勘察盲区模块（通知/邮件/推送/socket.io/定时任务等）确认 zvp 零实现，反向确认本仓能力更全（socket.io / apscheduler / notifications / statistics / mcp 独有）；红线拒绝面第 5 次全量复核维持（见 `STATUS.md` Part 5）。
- **服务端弱密码强制改密（2026-10-10，总账 #1 已闭）**：`/api/users/auth` 弱密码 long 请求仅发 5 分钟临时 token + `weak_password` 标记，强度规则与前端逐字镜像（独立测试 10/10）；short 改密流程与 OTP 路径不受影响（见 `STATUS.md` Part 6）。
- **`categories` / `debug_descriptions`**：`conversion/groups.py`、`tasks/categorize.py` 会用到，但现役库中不存在、zvp 也无对应代码。属 zvms4 独有的一次性/调试集合，首次写入 MongoDB 会自动创建；如需正式支持需另行建表。
- **`groups_v2_router.py` 成员读取、`times_router.py`**：仍用遗留 `zvms.users`，与现役 zvp 行为一致，故保留。
- **索引**：现役两库除 `_id_` 外无任何自定义索引；本 fork 未新增索引，性能问题按需另行评估。
- **后端已通过真实运行时验证（2026-10-11）**：本地 MongoDB 容器 + uvicorn 启动 + 41 项端到端断言全过（登录/弱密码强制改密/用户班级增改/义工活动与成员/TOTP/重置初始密码/日志过滤与 11 类 type/导出任务与 xlsx 下载/越权 403/shield 418 门禁），详见 `STATUS.md` Part 8。修复的运行缺口：requirements 依赖清理与 openpyxl 补齐、settings.example 缺字段（LANGUAGE/CHINESE_FONT）、RSA 1024→2048、SIGTERM 双路关停竞态。
- **shield 门禁的部署注意**：非浏览器客户端（监控探针等）会被 418 拦截，探活请带浏览器式 UA 或走 TCP 层；被拦请求写 `security_alert` 日志（admin 可在日志页审计）。
- **退出行为**：MongoDB 不可达时服务退出会阻塞至 serverSelectionTimeout（默认 30s）后才放弃——正常部署（mongo 在线）为瞬时操作。
- **版本发布规范（2026-10-11 定；2026-10-15 v4.2.2-6 修订）**：每次发版，**前后端版本号同步更新**——版本号唯一事实源为生产库 changelog 最新条目：后端 `/api/version` 与 `/api/` root 响应均从库读（`main.py` 不持有版本串，库不可读时返回中性值）；前端 `/about` 页读 `/api/version`。发版手工项：前端 `package.json` 版本号 + 生产库新增 changelog 条目（管理员 `/changelog` 页面可增删改）。`/changelog` 页面优先读库，离线回退 `src/data/changelog.ts` 静态数组（新条目需同步 prepend）。当前版本 **v4.2.4-3**。
- **用户线上报告 4 个 bug（2026-10-11 存档，已修复部署）**：用户身份栏空白 / 用户详情"班级不存在" / 日志未登录请求显示"无此用户" / 删除义工后应跳转 `/activities`——根因分析与修复方向见 `STATUS.md` Part 9 §38，四 bug 已随 v4.2.0-3 于 2026-10-12 部署上线（§45）。
- **前端体验批次（2026-10-12，v4.2.1-1）**：changelog/about 免登录可见并新增返回按钮；PWA 版本自动接管强化（SW 轮询 10h→30min + controllerchange 自动 reload）；IndexedDB 本地缓存层（取证确认 zvp-latest 无手写 IDB 代码，系自建——零依赖零网络，网络失败/网关错误时回读缓存兜底）；身份徽标升级为与群组管理页同源的彩色 logo（管理员红色）；生产 404 三类残留定案消除。详见 `STATUS.md` Part 11 §46。
- **通知 API 暂废弃、待重构（2026-10-13，随 Mongo 退役）**：`/api/users/{oid}/notification`（`routers/users_router.py`）与 `zvms_new.notifications` 集合——前端零调用（仅 UI 库 `ElNotification` 弹窗，非该 API）、集合计数为 0。随 MongoDB 退役一并移除端点与集合；**后续以新设计重构该通知 API**，不复刻旧 Mongo 的形状。记录于 `docs/ADB_MIGRATION.md` §7 与项目记忆。
- **MongoDB 退役进行中（2026-10-13）**：目标为彻底移除 Mongo 依赖（MongoDB 将来 phase-out，ADB 为唯一版本）。已切换 users/groups/activities/activity_members/time/stats/changelog 至 ADB；剩余新增表 `APP_LOGS`/`EXPORT_TASKS`/`IMPORTS`、`GROUPS.TYPE/PERMISSIONS` 与全部 v1/v2/v3 原始 Mongo 读取接线在推进中（见 `docs/ADB_MIGRATION.md` §6.6/§7）。
