# 设备追踪 / 学海 ID 日志设计（APP_LOGS.XUEHAI_ID）

> **v1（2026-10-14）**：采集链已在主干落地（`f74b0bc` / `66a593f`「采集学海ID——为平板加固做准备」）；本设计稿固化其**契约**，并补上本轮新增的 **按学海 ID 查询 + 索引**。状态：**采集与查询已实施**；设备↔用户强绑定为**未实施**的后续项。
> **v2（2026-10-15）**：修复「一条都采不到」——初版 `getXuehaiId()` 只读学海浏览器桥 `window.xhBrowserJava`，而**资料中心 App 无此桥**，导致其请求头恒不发送。现改为**双源**：桥优先，UA（资料中心 `userId/<id>`）兜底（§2、§7.1）。
> 基线仓库：`zvms4-modified-main/`，后端 `zvms4-backend-python/`。
> 来源：zvp 旧代码（`zvp5-repo/zvp-latest`）学海 API 只读排查 + 本仓 LOGS/APP_LOGS schema 与日志链核实。

---

## 0. TL;DR

1. **要记什么**：每条应用日志（`APP_LOGS`）记录**发起该请求的设备的学海用户 ID**（`XUEHAI_ID` 列），取值来自前端注入的 `Xuehai-Id` 请求头。**两个客户端两种来源**：学海浏览器走原生桥 `window.xhBrowserJava.getUserId()`，资料中心 App 走 UA 里的 `userId/<id>`（§2）。
2. **不记什么**：不落会话指纹 cookie、不落 Clarity-ID、不做登录期设备绑定、不引入学海签名代理——zvp-latest 那套「设备会话 + 绑定 + 外呼」在本仓一律不采用（详见 §6）。
3. **读**：`GET /api/logs`（admin）现已支持 `xuehai_id` 等值过滤，配合 `APP_LOGS` 行返回的 `xuehai_id`，管理员可**按一台平板反查用过它的平台账号**。
4. **索引**：新增 `ix_app_logs_xuehai_id`（`V001` fresh + `V008` 存量，`models.py` 同步镜像），`python -m db.ddl_check` 无漂移。
5. **信任边界**：`Xuehai-Id` 是**客户端自报、可伪造**的头（与 zvp 同源局限）。它用于**追踪/审计与设备侧加固的信息基础**，不单独作为鉴权或封禁的唯一凭据。

---

## 1. 现状约束（已核实，附出处）

| # | 事实 | 出处 |
|---|---|---|
| D1 | 前端 `getXuehaiId()` **双源**：① 原生桥 `window.xhBrowserJava?.getUserId?.()`（学海浏览器 xhbrowser）；② 兜底解析 `navigator.userAgent` 的 `userId/(\w+)`（资料中心 App xhdatacenter，UA 形如 `deviceId/<x> userId/<y>`）。两者都归一等价性为正整数，否则 `0` | `zvms4-frontend/src/plugins/ua.ts:23-33`；来源 `义工平台代码笔记整理.md:1240-1249` |
| D2 | 请求拦截器按需注入 `Xuehai-Id` 头：仅当 `getXuehaiId() > 0` 且未显式设置时写入 | `zvms4-frontend/src/plugins/axios.ts:61-64` |
| D3 | `window.xhBrowserJava` 的 TS 声明存在（可选属性） | `zvms4-frontend/src/types/xuehai.d.ts:5` |
| D4 | 后端读取头 `Xuehai-Id` 并 `.strip()`；头名大小写不敏感由 Starlette 保证 | `zvms4-backend-python/typings/log.py:66-67` |
| D5 | `ZVMSLog` 含 `xuehai_id` 字段，`insert_log()` 经 `AppLogsRepo.insert(...)` 落库 | `typings/log.py:8-63` |
| D6 | `inject_log` 依赖项在所有写日志端点注入 `xuehai_id = xuehai_id_from(request)` | `typings/log.py:70-79` |
| D7 | shield 中间件被拦请求（418）也写一条 `security_alert`，同样带 `xuehai_id` | `main.py:103-123` |
| D8 | 非依赖式写日志点（登录、导出、附件）显式传 `xuehai_id=xuehai_id_from(request)` | `routers/users_router.py:70-76`、`exports_router.py:302-308`、`attachments_router.py:178-183` |
| D9 | `APP_LOGS` 列 `XUEHAI_ID VARCHAR2(32 CHAR)`；`xuehai_id` 为 `str`（缺省 `""`） | `db/models.py:442-456`、`db/oracle/ddl/V001__initial_schema.sql:424-441` |
| D10 | Oracle 仓储 `insert` 绑定 `xuehai_id`，`_log_row` 回填 `xuehai_id`（Mongo dict 形状） | `db/repos/oracle/app_logs.py:182-196,205-223` |
| D11 | 仓储协议 `AppLogsRepo` 行形状含 `xuehai_id`（`str`） | `db/repos/protocols.py:664-719` |
| D12 | 管理端读端点 `GET /api/logs` 返回行含 `xuehai_id`，本轮新增 `xuehai_id` 过滤 | `routers/logs_router.py:13-65` |
| D13 | `db/ddl_check.py` 只比对 `models.metadata` 与 `V001`；新对象须 **V001(fresh)+V00N(存量)** 双写 | `db/ddl_check.py:50,232-239`、`db/oracle/ddl/README.md:26-32` |
| D14 | 应用代码**零出站 HTTP**（既有红线）：本设计不新增任何外呼 | 全树 grep；`docs/FILES_API_DESIGN.md` C9 |

> D5–D8 覆盖三类写入点：`Depends(inject_log)` 依赖（15+ 站点）、显式 `ZVMSLog(...)`、中间件；未发现遗漏 `xuehai_id` 的写入点。

---

## 2. 采集链（前端 → 后端 → 库）

```
两个客户端，一个出口
────────────────────
学海浏览器 (xhbrowser)              资料中心 App (xhdatacenter)
  window.xhBrowserJava                 UA: … deviceId/<x> userId/<id> …
  .getUserId()  ─► 原始 id            （无 xhBrowserJava 桥）
        │                                    │
        └────────────► ua.ts:getXuehaiId() ◄─┘   （桥优先，UA 兜底）      (D1)
                              │
                              ▼  axios 拦截器注入头  Xuehai-Id: <id>          (D2–D3)
                              │
                              ▼  HTTP
后端 xuehai_id_from(request) = header("Xuehai-Id") or ""                    (D4)
        │
        ├─ inject_log 依赖  ──► ZVMSLog.xuehai_id                              (D5,D6)
        ├─ 显式 ZVMSLog(...) ──► （登录/导出/附件）                            (D8)
        └─ shield 中间件      ──► security_alert 日志                          (D7)
        │
        ▼  AppLogsRepo.insert(url,user,data,ts,type,level,xuehai_id)
APP_LOGS.XUEHAI_ID  ──►  GET /api/logs?xuehai_id=…（admin 反查）              (D9–D12)
```

**归一语义**：前端非学海设备（`getXuehaiId()` 返回 `0`）**不注入**该头，后端落库为空串 `""`——即「非平板请求」与「未采集」在库中同一表示。

**并发/多用户设备说明**：学海 ID 标识的是**设备侧登录的学海账号**，而非 ZVMS 平台账号。同一平板先后被不同平台账号登录时，日志里的 `(xuehai_id, user)` 组合会变化——这正是「反查设备归属」的信号来源。

---

## 3. 数据模型

`APP_LOGS`（`db/models.py:442-456`，`V001:424-441`）：

| 列 | 类型 | 说明 |
|---|---|---|
| `oid` | `VARCHAR2(24 CHAR)` PK + `ck_app_logs_oid` | 24-hex |
| `url` | `VARCHAR2(512 CHAR)` | 请求 URL（**不含头信息**） |
| `app_user` | `VARCHAR2(64 CHAR)` | 平台用户 oid（未登录为空） |
| `data` | `VARCHAR2(2000 CHAR)` | 中文摘要 |
| `ts_at` | `TIMESTAMP` | 派生时间 |
| `ts_raw` | `BINARY_DOUBLE` | 原始 epoch 秒（排序 / 范围过滤权威值） |
| `ltype` | `VARCHAR2(64 CHAR)` | 业务类型（`user_login` / `security_alert` …） |
| `log_level` | `VARCHAR2(32 CHAR)` | `info` / `warning` / … |
| **`xuehai_id`** | **`VARCHAR2(32 CHAR)`, NULL** | **设备学海 ID；空串表示未采集** |

> 另有一张遗留 `logs` 表（`db/models.py:400-418`，`xuehai_id NUMBER(18)`）是 zvp 的**设备会话 schema**，本仓**未启用**，与本设计的 `APP_LOGS.XUEHAI_ID` 无关。

---

## 4. 日志 API 设计

### 4.1 `GET /api/logs`（admin）

| 参数 | 类型 | 语义 |
|---|---|---|
| `performer` | `str` | `app_user` 等值 |
| `page` / `perpage` | `int` | 分页（`page=-1` 不分页；`perpage` 1–100） |
| `query` | `str` | `url`/`data` 包含（大小写敏感，`re.escape`，≤`MAX_QUERY_COUNT`） |
| `type` / `level` | `str` | 逗号分隔 OR 列表 |
| `time_start` / `time_end` | `int?` | `ts_raw` **开区间** |
| **`xuehai_id`** | **`str`（本轮新增）** | **`xuehai_id` 等值；空串=不过滤** |

响应 `data[]` 每行含 `xuehai_id` 与 `user_name`；`metadata.size` 为过滤后总数。

**鉴权**：`admin` 才可读（`logs_router.py:26-27`）；日志端点为 GET-only 防篡改（既有约定）。

### 4.2 仓储契约

`AppLogsRepo.list_page(..., xuehai_id: str = "", ...)`（`protocols.py:688-708`）；Oracle 实现于 `db/repos/oracle/app_logs.py` 的 `_filter` 追加 `app_logs.c.xuehai_id == xuehai_id`。空值不下推条件，保持既有查询计划不变。

### 4.3 典型用法

- **按设备反查账号**：`GET /api/logs?xuehai_id=123456`
- **某用户是否用他人平板**：`GET /api/logs?performer=<user_oid>&xuehai_id=123456`
- **异常请求的设备分布**：`GET /api/logs?type=security_alert&xuehai_id=123456`

---

## 5. 索引

| 索引 | 表 / 列 | 去处 |
|---|---|---|
| `ix_app_logs_xuehai_id` | `app_logs(xuehai_id)` | `db/models.py:457`、`V001:451-458`、存量迁移 `V008__add_app_logs_xuehai_index.sql` |

- 幂等：`CREATE INDEX` 吞 `ORA-00955`（名已用）/`ORA-01408`（索引已存在），其余错误上抛。
- 一致性：`python -m db.ddl_check`（离线）——当前 **19 表 / 18 索引，无漂移**。
- 取舍：单列等值过滤，非唯一、非组合；与 `ix_app_logs_ts_at` 并存。若后续出现「时间+设备」复合查询热点，再评估组合索引，本轮不预置（YAGNI）。

---

## 6. 与 zvp-latest 的取舍（红线内的最小引入）

从 `zvp5-repo/zvp-latest` 排查到的设备/学海面，本仓**只取「采集请求设备学海 ID」这一点**，其余全部不采用：

| zvp-latest 机制 | 出处 | 本仓决定 |
|---|---|---|
| 学海 ID 头采集 | `frontend/plugins/axios.ts:26`、`ua.ts:7-9`；`backend/util/log.py:36` | **采用**（输血为 `Xuehai-Id` → `APP_LOGS.XUEHAI_ID`） |
| 学海签名代理 `/xh/api`（任意 URL + 硬编码 KEY） | `backend/routers/xh_router.py:46,128-197` | 不采用（SSRF / 密钥泄露） |
| 学海 profile 外呼 `xh.js`（`yunzuoye.net/cloud`） | `frontend/src/api/xh.js` | 不采用（零外呼红线） |
| `zvp_session` 设备会话 cookie | `backend/main.py:79-118` | 不采用（会话指纹） |
| 登录期设备绑定 `verifiedName/verifiedId` | `backend/routers/v1/platform.py:143-154`、`frontend/stores/user.ts:66-86` | 不采用（登录层绑定） |
| Clarity-ID / `_clck` 采集 | `backend/util/logify.py:15-24` | 不采用 |
| `BLOCKED_USERS` 黑名单链 | `backend/util/blocking.py`（且 `from settings import BLOCKED_USERS` 在本仓无对应模块，zvp 侧亦为坏引用/死代码） | 不采用 |
| `_1.py` 删 `rule_violation` 日志脚本 | `backend/_1.py` | 不采用（清痕） |

> 结论对齐既有口径：zvp 侧「设备会话 / 绑定 / 外呼」三类禁止面**不移植**；学海 ID **日志采集**是为「平板加固」新增的**独立、可审计**能力（`f74b0bc`），不附带上述任何副作用。

---

## 7. 风险与后续

**风险**

1. **可伪造**：`Xuehai-Id` 由客户端自报，非学海侧签名校验；不能单独作为封禁/鉴权唯一依据（与 D1–D2 的信任边界一致）。
2. **PII 边界**：学海 ID 关联到自然人设备，属敏感标识；读端已限 `admin`，且**不写入 URL / 不落 query**，避免经代理 access_log 二次泄漏（见 `docs/FILES_API_DESIGN.md` C10/C14 的同类关注）。
3. **空值二义**：非平板请求与采集失败均记为 `""`；如需区分，后续可加显式标志（本轮不做）。
4. **UA 兜底的取值假设**：资料中心 App 分支假定 UA 的 `userId/<y>` 是**原始数字 id**。若实际为 MD5（32 位十六进制）等非数字串，`Number()` 归零 → 仍采集不到；届时需换取 `window.webView.getUserId()`（返回 MD5）并放宽列语义（`VARCHAR2(32)` 可容纳）。**首次上线需用真机 UA 复核。**

**已修复（v2）**

- **资料中心 App 采不到**：初版只读 `window.xhBrowserJava`（学海浏览器桥），资料中心无此桥 → 头恒不发。根因见 §2 双源图；修复 `zvms4-frontend/src/plugins/ua.ts:23-33`。

**后续（未实施，逐项评审后另开）**

- 设备↔平台用户**强绑定**（登录期校验），形态参考 `zvp5-gitlab` 的 `users.xhid` + `devices` 表 + `device_identifier` 校验（`reference/zvp5-gitlab/models/users.py:29`；设备绑定实现在原 `routers/v1/platform.py:72-108`，该文件未随参考树归档，现存副本见 `reference/xuehai/zvp-latest/backend/routers/v1/platform.py`）——需先定注册/解绑与误伤回退策略。
- 设备维度**风控规则**（同一学海 ID 短时多账号、异常 UA/路径）——规则库与告警投递另行设计。
- 读端 `xuehai_id` 的**聚合视图**（每设备关联账号列表）。

---

## 8. 验收证据

- 采集链：`typings/log.py:66-79`、`main.py:111-119`、`frontend/plugins/axios.ts:61-64`（静态核实）。
- 本轮改动：`routers/logs_router.py`（+`xuehai_id` 参数与下推）、`db/repos/protocols.py`、`db/repos/oracle/app_logs.py`、`db/models.py`、`V001`、`V008`。
- `python -m compileall` 通过；`python -m db.ddl_check` → `OK 19 tables, 18 indexes - no drift`；`import main` 成功且 `/api/logs` 路由在册。
- v2 双源：`npm run type-check`（vue-tsc）EXIT=0；逻辑用例——普通浏览器 → `0`、资料中心 UA → `1024321`、学海浏览器桥 → `998877`、异常 `userId/abc` → `0`。
