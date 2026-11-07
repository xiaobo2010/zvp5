# ZVMS4 Backend — ZVP Modified

当前后端使用 FastAPI、Pydantic、SQLAlchemy 2.0 async 和 python-oracledb thin mode。
Oracle Autonomous Database（ADB）是唯一运行时数据库；旧文档中的 MongoDB 集合名仅作为迁移背景。
仓储入口为 `db/backends.py`，模型为 `db/models.py`，版本化 DDL 见 [db/oracle/ddl](db/oracle/ddl/README.md)。

## 本地配置与运行

从本目录执行（Windows PowerShell）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe tools/setup_local.py
```

初始化脚本只创建缺失的 `settings.py`、RSA 密钥对和随机 `aes_key.txt`，已有文件不会被覆盖。
新配置默认选中 `ORACLE_ACTIVE = "test"`，数据库凭据留空。密钥、配置和 `.venv` 已被 Git 忽略。
Linux/macOS 可将上述 Python 路径换为 `.venv/bin/python`。

在 `settings.py` 中配置 `ORACLE_USER`、`ORACLE_PASSWORD`，以及所选实例的 DSN、wallet 路径和 wallet 密码。
生产与测试的键名、会话使用方法见 [Oracle 配置说明](db/oracle/README.md)。先连接测试实例验证，应用启动不会自动执行 DDL 或迁移数据。

```powershell
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

离线测试不需要真实 Oracle 凭据：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q routers services util db typings
```

`GET /api/health` 在数据库不可用时返回 503。应用现有盾牌中间件要求 HTTP `User-Agent` 包含 `Mozilla`；使用 curl 或脚本验收时也需设置该请求头。

可选：`ruff check .`（配置见 `pyproject.toml`，只查未使用 import / 未定义名，ruff 不是运行依赖，需自行安装）。

## 数据库状态接口

`GET /api/system/db/status` 需要管理员的长期登录令牌，返回当前 schema 的表存储占用明细、表数量、估计行数、表空间容量及近期负载指标。
权限不足的监控项会单独降级，连接异常返回 503；采集使用 15 秒进程内缓存。
完整响应、统计口径、权限和 OCI Monitoring 扩展方案见 [数据库状态 API](docs/database-status.md)。

## Web SSH 多目标（单网关）

网关部署在 API 服务器上，按管理员选择连接「前端服务器」或「API 服务器」；浏览器只发送目标
`id`，host/port/user/密钥路径全部由服务端配置决定，任何响应与日志都不返回密钥路径或
known_hosts 内容。交互式终端仍保留双人 TOTP、一次性 ticket（120 秒、绑定目标）、
每人 2 条/全局 4 条并发上限与 `web_ssh` 审计（open/close 的 detail 带 `target=<id>`）。

在 `settings.py` 配置（或等价的环境变量 `ZVMS_SSH_TARGETS`，优先级更高）：

```python
SSH_TARGETS = [
    {"id": "frontend", "label": "前端服务器", "host": "10.0.0.5"},
    {"id": "api", "label": "API 服务器", "host": "127.0.0.1",
     "port": 22, "user": "root", "key_path": "/www/zvms4/backend/ssh_key"},
]
SSH_DEFAULT_TARGET = "frontend"  # 也可用 ZVMS_SSH_DEFAULT_TARGET
```

`id` 必须匹配 `^[a-z0-9][a-z0-9_-]{0,31}$` 且唯一；非法项会被丢弃并记 warning，
`port`/`user`/`key_path`/`known_hosts` 缺省沿用旧的 `ZVMS_SSH_*` 与默认值。完全没配置、
配置为空或全部非法时退化为原来的单目标 `id="self"`、`label="本机"`，老部署无需改动。
`GET /api/system/ssh/targets`（仅 admin）返回 `[{id,label,display}]` 与 `default`；
`POST /api/system/ssh/session` 的未知 `target` 一律 400 `SSH_UNKNOWN_TARGET`。

## 本轮修复与兼容性

- 登录凭据校验、临时令牌用途、即时权限和封禁检查已收紧；密码、角色、班级或封禁状态变更会轮换会话版本。
- JWT 新增必需的 `sid` 声明。部署后旧令牌失效，用户需要重新登录；复用既有 `USERS.SESSION_ID` 字段，无新增 DDL。
- 活动成员批量写入（`POST /api/v1/activities/{id}/members`，原 v3 实现）使用同一事务并锁定活动行；单条写入的旧 v2 版本已随 v2/v3 全量归并退役（仅摘路由装饰器，函数体保留）。Excel 导入的文件、活动、成员及审计一起提交或回滚；其余写入口尚未全部采用同一并发锁约定。
- 修复空活动集时长统计、同班秘书校验、班级列表内部字段泄露、导出日期边界、任务失败状态及内存内下载授权校验。
- 导出文件使用内存缓冲并在线程中生成，避免临时文件残留。任务不再在下载后立即删除，支持重试下载。
- 日志按 UTF-8 字节限制写入；前端修复认证响应解析、退出状态清理和身份相关 GET 的持久化缓存隔离，并补齐 PWA 构建依赖。

当前离线测试使用模拟仓储/会话；监控 SQL 的聚合逻辑另有字典视图形状的 SQLite fixture 验证。这些检查不能替代真实 Oracle 权限、SQL、驱动取消和并发事务联调。
本地未提供 ADB 凭据或 wallet，因此未执行真实数据库验收。

现存限制：报告打包下载的一次性授权仍保存在进程内，多 worker 或重启场景需要共享存储；
登录接口尚无跨实例速率限制，生产环境应在反向代理或共享存储层补充。前端已迁移到
ESLint 9 flat config，根级 CI 会执行后端测试、前端单测、lint 和类型检查；
Cypress 浏览器二进制未默认安装，因此浏览器端 e2e 仍需单独安装后运行。

## ZVAgent 与安全基线（2026-10-29）

AI 产品统一称 **ZVAgent**（`/api/ai/*`；「AI 网关」是模块名）。本轮的安全与引擎能力：

- **内容审核 fail-closed**：`services/ai/moderation.py` 的命令式规则 + `services/ai/safety_guard.py` 的 LLM 灰区判定。判定不可用时拒绝本次回答但不记违规；判定成立时对外只回 `AI_SAFETY_REJECTED` 与固定文案「该请求未通过内容安全审核。」（run 提交 422、`/v1` 403），对内记违规 + 警告 + 按 `BAN_RULES.penalties` **首档立即封号**（`services/violation_guard.py:record_agent_jailbreak`）。admin 天然旁路，审计事件 `ai_moderation_unrestricted`。
- **安全与频控**：登录端点账号 5 次/分 + IP 60 次/分（`util/login_throttle.py`，429 `AUTH_RATE_LIMITED`）；重查询端点（`GET /api/logs`、`/api/ai/logs`、`/api/ai/prompts`、`GET /api/exports/{id}/file`）60 次/分/用户（`util/read_throttle.py`，429 `READ_RATE_LIMITED`）；`POST /api/users/auth/logout` 轮换 `session_id`；`require_password_verification` 为管理写操作提供 5 分钟再认证令牌（缺/错 → 403 `需要密码验证后才能继续`）。
- **响应头与门禁**：`main.py:SECURITY_RESPONSE_HEADERS` 统一加 nosniff / DENY / Referrer-Policy / HSTS / CSP；shield 418 采集命中原因、referer、origin、XFF、UA；socket.io 只认 `Authorization` 头与 auth 载荷。
- **引擎**：会话模型建时锁定，思考强度 `low/medium/high` 按上游翻译（`openai_compat → reasoning_effort`、智谱 `thinking`）；模型上下文窗口支持 `MODELS_JSON` 的 `{"id","context"}` 形态并 best-effort 探测上游 `/models`。
- **只读场景**：`routers/ai_scenes_router.py` 提供 `POST /api/ai/scenes/{scene}/runs`（`terminal` / `file_edit`），工具裁到只读集、参考内容脱敏后不落消息表、场景会话 14 天保留；`ban_history` 为新增只读工具。
- **文件 jail**：`services/file_ops.py:ALLOWED_ROOTS` 只放行 `/www/zvms4` 与 `/home`（realpath 后按路径段比较），HTTP 文件管理端点与 agent `fs_*` 共用；越界 403 `FILES_ROOT_FORBIDDEN`。

迁移与保留期见 `docs/database-status.md`；API 契约见仓库根 `docs/AI_GATEWAY_API.md`。
