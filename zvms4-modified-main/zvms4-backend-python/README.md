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

## 数据库状态接口

`GET /api/system/db/status` 需要管理员的长期登录令牌，返回当前 schema 的表存储占用明细、表数量、估计行数、表空间容量及近期负载指标。
权限不足的监控项会单独降级，连接异常返回 503；采集使用 15 秒进程内缓存。
完整响应、统计口径、权限和 OCI Monitoring 扩展方案见 [数据库状态 API](docs/database-status.md)。

## 本轮修复与兼容性

- 登录凭据校验、临时令牌用途、即时权限和封禁检查已收紧；密码、角色、班级或封禁状态变更会轮换会话版本。
- JWT 新增必需的 `sid` 声明。部署后旧令牌失效，用户需要重新登录；复用既有 `USERS.SESSION_ID` 字段，无新增 DDL。
- v3 批量成员写入使用同一事务并锁定活动行；Excel 导入的文件、活动、成员及审计一起提交或回滚。旧 v2 写入入口尚未全部采用同一并发锁约定。
- 修复空活动集时长统计、同班秘书校验、班级列表内部字段泄露、导出日期边界、任务失败状态及内存内下载授权校验。
- 导出文件使用内存缓冲并在线程中生成，避免临时文件残留。任务不再在下载后立即删除，支持重试下载。
- 日志按 UTF-8 字节限制写入；前端修复认证响应解析、退出状态清理和身份相关 GET 的持久化缓存隔离，并补齐 PWA 构建依赖。

当前离线测试使用模拟仓储/会话；监控 SQL 的聚合逻辑另有字典视图形状的 SQLite fixture 验证。这些检查不能替代真实 Oracle 权限、SQL、驱动取消和并发事务联调。
本地未提供 ADB 凭据或 wallet，因此未执行真实数据库验收。

现存限制：报告打包下载的一次性授权仍保存在进程内，多 worker 或重启场景需要共享存储；
登录接口尚无跨实例速率限制，生产环境应在反向代理或共享存储层补充。前端已迁移到
ESLint 9 flat config，根级 CI 会执行后端测试、前端单测、lint 和类型检查；
Cypress 浏览器二进制未默认安装，因此浏览器端 e2e 仍需单独安装后运行。
