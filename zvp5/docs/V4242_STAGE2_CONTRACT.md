# v4.2.4-2 Stage 2 契约（冻结）

> 唯一真相源。实现车道按此执行；任何偏离先在本文档改，再改代码。
> 上游：`zvp5/docs/V4242_FIXES_AND_OPS_DESIGN.md` §4（Stage 2 框架）。

## 0. 已裁定与新增事实

| 项 | 裁定 | 来源 |
|---|---|---|
| SSH 形态 | 全交互真终端：xterm.js + WebSocket + asyncssh | 用户（本批） |
| 文件管理范围 | 整个文件系统 `/`，仅保留 `/proc`、`/sys` 虚拟文件系统保护（2026-10-19 修订） | 用户（本批） |
| 数据库内容管理 | 含 DDL 的完整管理 | 用户（本批） |
| 双人 TOTP | 操作者现场输入**另一位管理员**的动态码 | 用户（本批） |
| 视觉 | 清除半透明，瑞士风格，最大化信息密度 | 用户（本次） |

生产实测（2026-10-18）：

- `sshd` active，监听 `0.0.0.0:22`，`PubkeyAuthentication yes`。
- **可登录账号只有 `root`**（uid 0，`/bin/bash`）；`www` 是 `/sbin/nologin` 且无 `~/.ssh`。
- `asyncssh 2.24.1` 可安装；`@xterm/xterm 6.0.0` 可安装。
- 规格：1 vCPU / 1876 MB RAM / 29G 盘（62% 已用）。

## 1. 共享鉴权：双人 TOTP

实现：`util/dual_auth.py`（车道 fix-13），三块功能统一调用。

```python
approver_oid = await require_dual_auth(operator, payload.totp_code, action="ssh.session")
```

规则（全部为实现约束，不得放宽）：

1. 动态码 6 位数字，否则 `422`。
2. 必须来自**另一位** `admin`；不得使用操作者自己的码；没有第二管理员时直接拒绝（`403`），不做"降级为单人"。
3. 时间窗：`OTP_STEP` 与既有登录一致（60s），接受 `counter` 与 `counter-1`（沿用 `verify_totp` 语义）。
4. **防重放**：`(approver_oid, otp_counter)` 在 `dual_auth_uses` 上唯一；冲突 → `409 该动态码已被使用`。依赖 INSERT 原子性，禁止先查后插。
5. **失败限流**：同一 operator 60s 内 5 次失败 → `429`。
6. **审计**：成功与失败都写 `app_logs`（`type=system_dual_auth`，`level=warning`），**永不记录动态码明文**。
7. 错误提示统一为「动态码无效或已过期」，不泄露命中的是谁、也不泄露候选人数。

## 2. Web SSH 终端

### 2.1 连接目标（**已裁定**：B1）

后端以 asyncssh 连接本机 sshd：`root@127.0.0.1:22`，密钥认证。
凭据来源（优先级从高到低）：环境变量 `ZVMS_SSH_HOST/PORT/USER/KEY_PATH` → `settings.py` 同名字段 → 上述默认值。

已就绪（2026-10-18 22:16，生产实测 `sudo -u www ssh … root@127.0.0.1 'id -un'` → `root`）：

| 项 | 值 |
|---|---|
| 私钥 | `/www/zvms4/backend/ssh_key`（`-rw------- www:www`） |
| 公钥 | `/www/zvms4/backend/ssh_key.pub`（`644`） |
| 指纹 | `SHA256:jQ+urHtjlr58YLaHw8IEKbIFS4VM6wMXOgDR7oGm3+A`（注释 `zvms4-webssh`） |
| known_hosts | `/www/zvms4/backend/ssh_known_hosts`（首次连接自动写入） |
| authorized_keys | 已追加该公钥（原有 1 条 + 新增 1 条 = 2 条） |

硬约束：
- 私钥/known_hosts **不入库、不进 git、不回显**；后续所有 `rsync` 必须 `--exclude ssh_key --exclude ssh_known_hosts`。
- 不在代码里硬编码密钥路径以外的凭据；不得把私钥内容写进日志或响应。
- 密钥仅允许 `root@127.0.0.1` 使用；如需吊销，只需从 `/root/.ssh/authorized_keys` 删除对应行。

### 2.2 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/system/ssh/session` | 校验双人 TOTP → 发放**一次性 ticket** |
| WS | `/api/system/ssh/ws?ticket=…` | 建立终端会话 |

`POST /session` 请求体：`{"totp_code": "123456"}`；响应：`{"ticket": "...", "expires_in": 120}`。
ticket：随机 32 字节 base64url，存进程内（单 worker 前提），**单次使用**、`120s` 过期、绑定 operator oid。

### 2.3 WS 协议

客户端 → 服务端：

```json
{"type": "input",  "data": "ls\n"}
{"type": "resize", "cols": 120, "rows": 32}
{"type": "ping"}
```

服务端 → 客户端：

```json
{"type": "output", "data": "..."}
{"type": "exit",   "code": 0}
{"type": "error",  "detail": "..."}
{"type": "pong"}
```

### 2.4 硬约束

- 连接参数固定（host/port/user/key 全部来自配置），**不接受**客户端传入目标。
- 单 operator 同时最多 **2** 个会话；空闲 15 分钟自动关闭；总并发 ≤ 4。
- 输出按 ≤ 32KB 分片下发；`input` 单条 ≤ 8KB。
- ticket 校验失败 / 过期 → 关闭 WS（`4401`）。
- 建立与关闭都写审计（`type=system_ssh`）。
- 前端 xterm.js 主题必须与瑞士风格一致（无半透明、等宽字体、无光标动画）。

## 3. 文件管理

### 3.1 端点（统一前缀 `/api/system/files`）

| 方法 | 路径 | 双人码 | 说明 |
|---|---|---|---|
| GET | `/list?path=/&offset=0&limit=500` | 否 | 目录列表（`limit ≤ 2000`） |
| GET | `/stat?path=` | 否 | 单条目 stat |
| GET | `/download?path=` | 否 | 流式下载（≤ 256MB，超过拒绝） |
| POST | `/mkdir` | 是 | `{path}` |
| POST | `/upload` | 是 | multipart，单文件 ≤ 100MB |
| POST | `/rename` | 是 | `{from, to}` |
| POST | `/delete` | 是 | `{path, recursive}` |
| POST | `/chmod` | 是 | `{path, mode}`（`0o000-0o777`） |

所有写操作请求体都带 `totp_code`。

### 3.2 路径安全

1. 必须是绝对路径；`os.path.realpath` 解析符号链接后仍必须是绝对路径。
2. 解析后若命中 `/proc`、`/sys`（**虚拟文件系统**）→ `403`，文案「虚拟文件系统不可访问」（**读也不允许**，避免 `/proc/self/environ` 泄露后端进程环境变量）。
3. 拒绝包含 NUL、换行的路径；`..` 由 `realpath` 展开后再做虚拟文件系统判定。
4. 删除 `recursive=true` 时，先统计条目数，`> 10000` 拒绝并提示。

### 3.3 路径范围（2026-10-19 修订：系统目录黑名单已放开）

保留：

```
/proc /sys        # 仅这两个虚拟文件系统，读写全禁
```

其余路径**全部放开**：`/etc`、`/root`、`/usr`、`/bin`、`/sbin`、`/lib*`、`/dev`、`/boot`、`/run`、`/var/lib/*`（含 docker/systemd/containerd/mysql）、`/snap` 等不再由应用层拦截。实际能否读写**由 OS 权限决定**：后端进程以 `www` 运行，写不动时自然返回 `403 权限不足`。

`/proc`、`/sys` 例外保留的理由：它们是内核虚拟视图，`/proc/self/environ`、`/proc/self/cmdline` 会泄露后端进程的环境变量（含数据库口令），不属于「用户文件」。

应用自身目录 `/www/zvms4/backend` 允许读写（运维需要改后端）；风险由双人码 + 审计承担，已在 §8 记录。

> **变更说明（2026-10-19）**：按用户裁定取消系统目录黑名单，只保留 `/proc`、`/sys` 虚拟文件系统保护；同步把所有同步文件 IO 从 async 路由移入线程池，`list_dir` 改为有界窗口（`heapq.nsmallest`），避免大目录阻塞单 worker 事件循环。API 端点/字段/状态码语义不变（仅 403 文案变为「虚拟文件系统不可访问」），双人码流程不变。

### 3.4 其他硬约束

- 上传：文件名必须 `basename` 化，拒绝路径穿越；同名默认拒绝，`overwrite=true` 才覆盖。
- `chmod` 不允许 setuid/setgid/sticky（只接受 `0o000-0o777`）。
- 所有写操作写审计（`type=system_file`，含 path/op/operator/approver，不含文件内容）。
- 读取大目录用 `os.scandir` 迭代，不 `listdir` 全量；排序改用 `heapq.nsmallest` 只维护 `offset + limit` 个候选（窗口上限 20000），避免大目录把整张名单物化进内存。
- 所有同步文件 IO（list/stat/download 前置/写操作/上传落盘）必须经 `asyncio.to_thread` 执行，单 worker 下不得阻塞事件循环。

## 4. 数据库内容管理（含 DDL）

### 4.1 端点（前缀 `/api/system/db/content`）

| 方法 | 路径 | 双人码 | 说明 |
|---|---|---|---|
| GET | `/schema` | 否 | 表/视图清单（名称、类型、行数估算） |
| GET | `/table/{name}/columns` | 否 | 列定义、可空、默认值、主键 |
| GET | `/table/{name}/rows?offset&limit&order_by&desc` | 否 | 分页读取（`limit ≤ 200`） |
| POST | `/table/{name}/rows` | 是 | 插入（只接受列名 → 参数绑定） |
| PUT | `/table/{name}/rows` | 是 | 按主键更新 |
| DELETE | `/table/{name}/rows` | 是 | 按主键删除；无主键表禁止 |
| POST | `/query` | 否 | **只读** SELECT（见 4.3） |
| POST | `/ddl` | 是 | 执行 DDL/DML（见 4.4） |

### 4.2 标识符与取值

- 表名/列名必须匹配 `^[A-Za-z][A-Za-z0-9_$#]{0,29}$`，且**必须存在于** `user_tables` / `user_tab_columns`（否则 `404`）。
- 列名只用于拼接 SQL 标识符（已校验 + 大写化）；**值永远走绑定参数**，禁止字符串插值。
- `order_by` 必须是该表真实列名，否则 `400`。

### 4.3 只读查询

1. 去注释后必须以 `SELECT` 或 `WITH` 开头；不允许 `;`（单语句）。
2. 拒绝关键字（大写匹配，词边界）：`INSERT UPDATE DELETE MERGE DROP ALTER CREATE TRUNCATE GRANT REVOKE BEGIN DECLARE CALL EXECUTE COMMIT ROLLBACK`。
3. 强制 `fetchmany(limit)`，`limit ≤ 500`；超时 30s（`CALL TIMEOUT`）。
4. 返回列名 + 行数组；`CLOB/BLOB` 只回长度与前 2KB 预览。

### 4.4 DDL / 写语句

1. 必须双人码。
2. **自保护表**（任何 DDL/DML 一律拒绝）：`dual_auth_uses`、`user_trash`、`changelog`、`users` 的 `DROP`/`TRUNCATE`。
3. 破坏性语句（`DROP`、`TRUNCATE`、无 `WHERE` 的 `DELETE`/`UPDATE`）额外要求请求体 `confirm` 字段等于目标表名，否则 `400`。
4. 单次只允许**一条**语句（`;` 结尾可有，内部不允许）。
5. 执行后记录 `rowcount`（DDL 记 `None`）；DDL 自动提交是 Oracle 语义，必须在响应里明确说明「已提交」。
6. 全部写审计（`type=system_db_ddl`、`system_db_dml`，含语句摘要 ≤ 500 字）。

## 4.5 冻结的线上格式（前后端均以此为准）

统一响应外壳沿用既有约定：`{"status":"ok","code":200,"data":{…}}`；错误 `{"detail":"中文"}`。

**`GET /schema`** → `data`：
```json
{"total": 21, "tables": [{"name":"USERS","type":"TABLE","num_rows":1234,"last_analyzed":"2026-10-01T00:00:00"}]}
```
`num_rows` / `last_analyzed` 允许 `null`（不做 `COUNT(*)` 全表扫）。`type` ∈ `TABLE` | `VIEW`。

**`GET /table/{name}/columns`** → `data`：
```json
{"table":"USERS",
 "columns":[{"name":"OID","type":"VARCHAR2","length":24,"nullable":false,"default":null,"primary_key":true}],
 "primary_key":["OID"]}
```
`primary_key` 为空数组 = 该表无主键（前端应禁用行编辑/删除）。

**`GET /table/{name}/rows`** → `data`：
```json
{"table":"USERS","columns":["OID","NAME"],
 "rows":[{"OID":"6ad4…","NAME":"张三"}],
 "primary_key":["OID"],"offset":0,"limit":100,"has_more":false,"total_estimate":1234}
```
- `rows` 是对象数组；JSON 标量直接给（`NUMBER`→number，`TIMESTAMP`→ISO8601 字符串，`NULL`→null）。
- **LOB 值**统一序列化成：`{"$lob":"CLOB","length":12345,"preview":"前 2048 字符"}`；`BLOB` 的 `preview` 省略（或给 `null`）。
- `has_more`：取 `limit+1` 行判定，**不得**用 `COUNT(*)`；`total_estimate` 允许 `null`。
- `limit ≤ 200`；`order_by` 必须是真实列名。

**`POST /table/{name}/rows`** 请求 `{"values":{…},"totp_code":"…"}` → `data`：`{"rowcount":1}`
**`PUT /table/{name}/rows`** 请求 `{"key":{…},"values":{…},"totp_code":"…"}` → `data`：`{"rowcount":1}`
**`DELETE /table/{name}/rows`** 请求 `{"key":{…},"totp_code":"…"}` → `data`：`{"rowcount":1}`
`key` 必须覆盖该表全部主键列，缺列 → `400`；无主键表 → `400`。

**`POST /query`** 请求 `{"sql":"SELECT …","limit":100}`（**不带**双人码）→ `data`：
```json
{"columns":["OID"],"rows":[{"OID":"6ad4…"}],"truncated":false,"elapsed_ms":12}
```

**`POST /ddl`** 请求 `{"sql":"…","confirm":"USERS","totp_code":"…"}` → `data`：
```json
{"rowcount":0,"committed":true,"summary":"DROP TABLE USERS"}
```
`rowcount` 对 DDL 为 `null`；`committed: true` 恒为真（Oracle DDL 自动提交），前端必须显式提示这一点。
`confirm` 仅破坏性语句（`DROP`/`TRUNCATE`/无 `WHERE` 的 `DELETE`/`UPDATE`）必填且须等于目标表名，否则 `400`。
自保护表（`dual_auth_uses`、`user_trash`、`changelog`，以及 `users` 的 `DROP`/`TRUNCATE`）→ `403`。

**负数路径状态码（前端据此提示）**：`400` 语句不合法/缺 confirm/标识符不存在；`403` 自保护表或越权；`404` 表/列不存在；`409` 动态码重放；`422` 动态码格式；`429` 失败限流。



| 页面 | 路由 | 说明 |
|---|---|---|
| SSH 终端 | `/server/ssh` | xterm.js，全屏、无半透明、瑞士风格 |
| 文件管理 | `/server/files` | 双栏（目录树 + 列表），面包屑，密度优先 |
| 数据库内容 | `/server/db/content` | 表清单 + 数据网格 + SQL 编辑器 |
| 双人授权 | 组件 | 统一的「输入另一位管理员动态码」弹层，三处共用 |

- 新的 system 页面必须**追加**到现有 `ServerHome` 的入口，不改既有导航语义。
- 双人码弹层复用同一组件，错误码映射：`403/409/429` 分别给出明确中文提示。
- 所有页面遵循瑞士风格契约（无半透明、hairline、tabular-nums、高密度）。

## 6. 车道文件白名单（防写冲突）

| 车道 | 独占文件 |
|---|---|
| 守卫（fix-13） | `util/dual_auth.py`、`util/cert.py`、`db/oracle/ddl/V012*`、`db/models.py`、`db/repos/oracle/users.py`、`tests/test_v4242_dual_auth.py` |
| SSH | `routers/system_ssh_router.py`、`services/ssh_bridge.py`、`tests/test_v4242_ssh.py` |
| 文件 | `routers/system_files_router.py`、`services/file_ops.py`、`tests/test_v4242_files.py` |
| 数据库内容 | `routers/system_db_content_router.py`、`services/db_content.py`、`tests/test_v4242_db_content.py` |
| 集成（主会话） | `main.py`（`include_router` 三行）、`requirements.txt`、部署与 DDL 应用 |
| 前端 | 由 design 车道统一收口，后端车道**不动**前端 |

## 7. 验收标准

1. 后端：`pytest` 全绿（基线 504 + 各车道新增）。
2. `python -m db.ddl_check` → `no drift`。
3. 端点存在性：未鉴权访问新端点返回 `401`（而非 `404`）。
4. 双人码负路径实测：错码 `403`、重放 `409`、超限 `429`。
5. 文件管理：`/proc`、`/sys` 虚拟文件系统 `403`（文案「虚拟文件系统不可访问」）；`/etc` 等原黑名单目录不再因应用层拦截被拒；`..` 穿越到 `/proc`、`/sys` 仍 `403`；正常路径可列/可传。
6. DDL：只读查询拒绝写关键字；破坏性语句缺 `confirm` 为 `400`。
7. 前端：`type-check` 通过；`grep -rn "backdrop-filter\|backdrop-blur" src/` 为空。
8. 生产冒烟：只做**只读**探测（列表/查询/stat），写操作不在生产验证（避免动真实数据），除非用户单独授权。

## 8. 裁定结果

| # | 问题 | 裁定 | 落地状态 |
|---|---|---|---|
| B1 | Web SSH 用哪个账号连本机 | **`root@127.0.0.1` + 专用 ed25519 密钥** | 已完成：密钥/known_hosts/authorized_keys 就绪，`www` 身份连接实测 `id -un` → `root`（详见 §2.1）。风险已记录：后端进程持有 root 密钥 = 等同 root 能力，由双人码 + 审计约束 |
| B2 | `/www/zvms4/backend` 是否允许文件管理器读写 | **允许**（双人码 + 审计兜底） | 实现中 |
| B3 | DDL 是否允许 `DROP TABLE` | **允许**，但需双人码 + `confirm=表名`；`dual_auth_uses`/`user_trash`/`changelog`/`users` 的 `DROP`/`TRUNCATE` 永久拒绝 | 实现中 |
| B4 | 新页面导航层级 | **`/server/*` 二级**，从 `ServerHome` 进入 | 待前端车道 |

## 9. 集成记录（主会话）

### 9.1 已挂载（`main.py:26-28` 导入，`main.py:390-404` 注册）

| 前缀 | 来源车道 | 路由 |
|---|---|---|
| `/api/system/ssh` | fix-14 | `POST /session`、`WS /ws` |
| `/api/system/files` | fix-15 | `GET /list /stat /download`、`POST /mkdir /upload /rename /delete /chmod` |
| `/api/system/db/content` | fix-17（fix-16 卡死中止、零落盘后重派） | `GET /schema`、`GET /table/{name}/columns`、`GET·POST·PUT·DELETE /table/{name}/rows`、`POST /query`、`POST /ddl` |

取证：`python -c "import main"` → `app.routes` 实查（非 OpenAPI 推断），上表路由全部存在；后端全量 `pytest` **662 passed**（580 + DB 内容 82）、`ddl_check` → `OK 21 tables, 19 indexes - no drift`。

### 9.2 语义裁定（实现车道提出，已采纳）

| 项 | 裁定 | 理由 |
|---|---|---|
| 符号链接的 delete/rename/chmod/stat | **操作链接本身**，不跟随目标；`realpath` 仅用于虚拟文件系统校验（链接目标命中 `/proc`、`/sys` 仍 403） | 与 `rm` 语义一致，避免"删链接却删掉目标"的破坏性意外（车道实测发现该缺陷） |
| WS 关闭码 | `4401` 票据无效/过期、`4429` 并发超限、`4408` 空闲超时、`4500` 连接失败 | 契约原未规定，前端按此映射提示 |
| 未授权 WS 先 `accept` 再 `close(4401)` | 采纳 | 浏览器在握手阶段拒连时读不到自定义关闭码 |
| `list` 的 `limit > 2000` | 返回 `422`（FastAPI 校验），服务层再兜底夹紧 | 显式拒绝优于静默截断 |
| `/query` 是否允许 `SELECT … FOR UPDATE` | **拒绝**（fix-17 提出，主会话补丁 + 回归用例） | `FOR UPDATE` 不在写关键字黑名单里，却会在只读端点上加行锁并持有到会话结束，一次「查询」即可堵住其它事务；只读语义要求排除 |

### 9.3 保留的功能性半透明（非装饰，已核对）

- `el-overlay` 模态遮罩、loading mask（模态可用性必需）。
- `UserHome.vue:131` `.z-blur`（待重置密码的隐私遮罩，删除会直接暴露敏感内容）。
- `ZAttachmentUpload.vue:419/432/455/469/533` 图片 hover 操作层与全屏灯箱底、`:546` 灯箱图片 `shadow-lg`。
- `chartTheme.ts:15` ECharts 网格线 `rgba(148,163,184,.22)`（绘图线色，非面板）。

其余全站已零 `backdrop-filter`、零渐变、零半透明面板（取证：`grep -rn "backdrop-filter\|backdrop-blur" src/` 无命中；视图层半透明 Tailwind 类仅剩上表所列功能性遮罩）。
