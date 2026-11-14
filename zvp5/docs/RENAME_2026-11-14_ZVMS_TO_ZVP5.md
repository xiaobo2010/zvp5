# zvms → zvp5 改名记录（2026-11-14）

项目对外叫 **ZVP5**，仓库是 `xiaobo2010/zvp5`，但部署路径、systemd 单元、
环境变量、nginx 配置里仍大量留着 zvms/zvms4。这份文档记录改名的**边界**和
**已完成的部分**，避免下一个人（或下一次的自己）在错误的假设上动手。

## 结论先讲

- **代码、部署脚本、systemd、nginx 已经改成 zvp5，并且全部保留旧名回落。**
  两台生产机已在 2026-11-14 20:19~20:25 完成切换并验证。
- **Oracle schema 仍叫 `zvms_new`**，本次不动。Oracle 没有 schema rename，
  改名等于「建新用户 + 全量对象搬迁 + 切连接串」，是一次独立的数据迁移窗口。
  代码里 `zvms_new.xxx` 只在文档字符串和 DDL 注释里出现，运行时 schema 由连接
  用户决定，所以不影响任何逻辑。
- 前端 localStorage / IndexedDB 的键名、`x-zvms-*` 响应头等**浏览器侧标识符**
  尚未改；它们不涉及服务端，改与不改都能跑，排在最后。

## 兼容策略：双名读取，新名优先

| 层 | 新名 | 旧名（仍生效） | 收口条件 |
|---|---|---|---|
| 后端环境变量 | `ZVP5_*` | `ZVMS_*` | 两台机器的 env 文件与 drop-in 全换名后，删 `util/env.py` 里的 `LEGACY_PREFIX` |
| 主密钥凭据 id | `zvp5_ai_key` | `zvms_ai_key` | 新代码部署完成后，删 unit 里 `zvms_ai_key` 那行 `LoadCredential` |
| 密钥路径变量 | `ZVP5_PLATFORM_KEY_FILE` | `ZVMS_PLATFORM_KEY_FILE` | 同上，环境变量批次一起收 |
| 部署脚本 | `/usr/local/sbin/zvp5-deploy` | `zvms4-deploy` 符号链接 + 独立 sudo 规则 | 老 workflow 全部退役后 |
| 脚本环境变量 | `ZVP5_*` | `ZVMS4_*` | 脚本开头那段映射删掉即可 |
| systemd | `zvp5.service` | `zvms4.service`（`Alias=`） | 运维手册全改完后去掉 `[Install]` 的 Alias |
| 路径 | `/www/zvp5`、`/etc/zvp5` | 都是指向 `/www/zvms4`、`/etc/zvms4` 的符号链接 | 真正搬迁目录时改成实目录 |

后端读环境变量只允许走 `util/env.py` 的 `getenv()` / `getenv_bool()`。直接
`os.environ.get("ZVP5_X")` 会丢掉旧名回落，双名期等于埋一个「换台机器就崩」的坑。

## 已完成的切换（含验证）

### 代码与模板

- `util/env.py`：新增，`getenv(name, default)` 读 `ZVP5_*` 优先、回落 `ZVMS_*`；
  空字符串算「显式关掉」，不回落。20 处读取点全部改走它。
- `util/platform_key.py`：凭据 id 改 `zvp5_ai_key`，旧 id 存在时回落。
- `deployment/github-actions/zvms4-deploy` → `zvp5-deploy`。
- `deployment/zvms4.service` → `zvp5.service`。
- `deployment/zvms4.nginx.conf` → `zvp5.nginx.conf`；`deployment/nginx/zvms-*.conf`
  → `zvp5-*.conf`；`deployment/redis-zvms.conf` → `redis-zvp5.conf`。

### zvp-backend（后端 + API vhost）

1. `/etc/zvp5 -> /etc/zvms4`。
2. 装 `/etc/systemd/system/zvp5.service` 与 `zvp5.service.d/{10-ssh-targets,cors}.conf`。
   同时注入 `zvp5_ai_key` 和 `zvms_ai_key` 两个凭据——线上旧代码只认后者。
3. `systemctl disable --now zvms4` → 删旧 unit 文件 → `systemctl enable --now zvp5`。
   `zvms4.service` 现在是 `Alias=` 建出来的符号链接，`systemctl status zvms4`
   仍能命中同一个服务。
4. nginx：`conf.d/zvms4-upstream.conf` → `zvp5-upstream.conf`（upstream 改名
   `zvp5_api`）、`zvms-api-gzip.conf` / `zvms-cors-map.conf` 改名前缀、
   `snippets/zvms-*.conf` → `zvp5-*.conf`；`$zvms_cors_origin` → `$zvp5_cors_origin`、
   `$zvms_csp` → `$zvp5_csp`、`root /www/zvms4` → `/www/zvp5`。
   `nginx -t` 通过后 reload。

验证：`systemctl is-active zvp5` = active、`is-enabled` = enabled、
`http://127.0.0.1:8018/api/health` = 200、`https://zvp.xiaobopisces.top/` = 200、
`https://zvpapi.xiaobopisces.top/api/health` = 200、CORS 预检回显 canary 域、
静态资源 CORS 回显内容域、journal 无异常。切换瞬间后端中断约 3 秒。

### zvp-frontend（前端静态）

1. `/www/zvp5 -> /www/zvms4`（早先已建）。
2. 该机是宝塔面板的 nginx（`/www/server/nginx/conf` +
   `/www/server/panel/vhost/nginx`），改名面：`00-zvms-cors-map.conf` → `00-zvp5-cors-map.conf`、
   `conf/zvms-*.conf` → `conf/zvp5-*.conf`、`$zvms_cors_origin` → `$zvp5_cors_origin`、
   `$zvms_csp` → `$zvp5_csp`、`root /www/zvms4` → `/www/zvp5`。
3. `nginx -t` 通过后 `nginx -s reload`。

验证：`zvp` 与 `zvpapi` 直连本机 Host 头均 200，静态目录可列。

### 部署账号

`bootstrap-deploy-user.sh` 现在装 `zvp5-deploy` 并留 `zvms4-deploy` 符号链接。
**sudo 不跟随符号链接匹配策略**，所以 sudoers 里两个路径各授权一次，否则老版本
workflow 发 release 会被拒。sudoers 文件名改成 `/etc/sudoers.d/zvp5-github-actions`，
旧文件在下次 bootstrap 时自动删除。

实测：`sudo -l -U github-deploy` 有 8 条 NOPASSWD 规则（两条路径 × 四个动作）。

## 首次真机部署（2026-11-14 20:50）

把 main `d49da4b8` 发到 zvp-backend，暴露并修掉部署脚本两个真 bug（都是第一次
真跑才可能发现）：

1. `umask 027` → tar 解出的 `requirements.txt` 是 `root:root 0640`，而 pip 以
   `www-data` 跑，直接 `Permission denied`。修复：解包后立刻 `set_owner "$staging"`。
2. `.venv` 还是普通目录时，`activate_backend_venv` 把新建的 venv 整个 `mv` 进
   `.venv`。venv 里 `bin/*` 的 shebang 写死创建时的绝对路径，搬完就失效，
   systemd 报 `203/EXEC: No such file or directory`，健康检查 12 次全失败 →
   自动回滚（回滚本身是好的，服务恢复 200）。

现在统一成符号链接模型：`.venv -> .github-actions/venvs/backend-<rev>-<id>`，
切版本只换链接，shebang 永远有效。

最终结果：`backend.current = d49da4b8...`，health 200（该端点会真连 Oracle 做
ping，等于验证了新 venv 的 oracledb + 钱包），公网 zvp/zvpapi 200，
`settings.py` / `aes_key.txt` / `system_state.db` / `data/` 原样保留，
adb-sync 的 path 单元仍 active。

**注意**：现在 `.venv` 指向 `/www/zvp5/.github-actions/venvs/...`，所以
`/www/zvms4` 这个旧路径不能立刻删——它还是 `.github-actions` 的实体目录。

## 回滚

每一步都可单独回退，备份都在机器上：

| 机器 | 备份 |
|---|---|
| zvp-backend | `/root/nginx-bak-<ts>.tar.gz`、`/root/systemd-bak/zvms4.service.<ts>`、`/root/systemd-bak/zvms4.service.d.<ts>`、`/root/sudoers-bak/` |
| zvp-frontend | `/root/nginx-bak-<ts>.tar.gz`、`/root/sudoers-bak/` |

后端回滚：

```bash
systemctl disable --now zvp5
cp /root/systemd-bak/zvms4.service.<ts> /etc/systemd/system/zvms4.service
systemctl daemon-reload && systemctl enable --now zvms4
```

nginx 回滚：`tar xzf /root/nginx-bak-<ts>.tar.gz -C /` 后 `nginx -t && systemctl reload nginx`
（zvp-frontend 用 `nginx -s reload`）。

## 环境变量收口（2026-11-14 20:56）

新代码上线后，zvp-backend 的变量名已经真换成 `ZVP5_*`：

| 文件 | 变化 |
|---|---|
| `/etc/zvms4/zvp-secrets.env` | `ZVP5_ORACLE_PASSWORD`、`ZVP5_ORACLE_WALLET_PASSWORD` |
| `/etc/zvms4/ssh-targets.env` | `ZVP5_SSH_TARGETS`、`ZVP5_SSH_DEFAULT_TARGET` |
| `zvp5.service.d/cors.conf` | `ZVP5_CORS_ALLOWED_ORIGINS` |
| `zvp5.service.d/10-ssh-targets.conf` | `EnvironmentFile=/etc/zvp5/ssh-targets.env` |
| `/www/zvp5/backend/settings.py` | 读 `ZVP5_ORACLE_*`（带旧名回落），钱包路径改 `/www/zvp5` |

`settings.py` 不在部署树里，是手工维护的，所以单独改；它自带一层旧名回落，
下次再动它之前不会踩空。重启后 `/proc/<pid>/environ` 里只剩 `ZVP5_*`，
`/api/health`（内部真 ping Oracle）200，公网 CORS 预检回显 canary 域。

代码侧的回落层同步删除：`util/env.py` 不再认 `ZVMS_*`，`platform_key` 不再认
`zvms_ai_key`，部署脚本不再把 `ZVMS4_*` 映射成 `ZVP5_*`，`configure_runtime`
与 `adb-backup/install.sh` 的旧名兜底也去掉了。测试里的 `ZVMS_*` 全部换名
（`test_env_compat` 留了一条「旧名不再生效」的断言防回归）。

**故意保留的三处**（都是回滚保险，不是漏网）：

1. `zvp5.service` 里 `LoadCredential=zvms_ai_key:...` —— 回滚会恢复到改名前的
   构建，那份代码只认旧 id。
2. `[Install] Alias=zvms4.service` —— `systemctl restart zvms4` 仍能命中。
3. `/usr/local/sbin/zvms4-deploy` 符号链接 + 对应 sudo 规则 —— 老 tag 发
   release 时 workflow 里写的还是这个路径（sudo 不跟随符号链接匹配策略，
   所以那条规则不能省）。

等下一次发布稳定、回滚窗口过去后再删这三处。

## 还没做的

1. **撤掉回滚保险**：unit 里 `zvms_ai_key` 那行、`Alias=zvms4.service`、
   `zvms4-deploy` 符号链接与对应 sudo 规则（见上一节的说明）。
2. **目录实体化**：`/www/zvms4` → `/www/zvp5`、`/etc/zvms4` → `/etc/zvp5`，
   把符号链接换成真目录（后端的 venv 绝对路径、nginx root 都指着
   `/www/zvp5`，改实体目录不影响它们）。
3. **浏览器侧标识符**：`zvms.platformGuard.*`（localStorage）、`zvms4-cache`
   （IndexedDB）、`meta[name="zvms-platform"]`、`x-zvms-local-cache` 响应头、
   `zvms:api-race` 事件、`ZVMSLog` 类名。改 localStorage/IndexedDB 键等于让
   老客户端丢掉缓存（对功能无影响），可以一次性换掉。
4. **Oracle schema `zvms_new`**：独立窗口，见下。

> 顺序很重要：**代码先上线，再改机器上的 env 名字，最后删回落逻辑。** 反过来会在
> 旧代码还在跑的窗口里把服务弄起不来。2026-11-14 已按这个顺序走完。

## Oracle schema 改名（未排期）

`zvms_new` 是物理 schema 名。Oracle 不支持 `ALTER SCHEMA ... RENAME`，实际做法是：

1. 建新用户 `zvp5`，`GRANT` 同权限；
2. 每个对象 `ALTER TABLE ... RENAME TO` 不行（跨 schema），要走 Data Pump
   `REMAP_SCHEMA=zvms_new:zvp5` 或 `CREATE TABLE AS SELECT`；
3. 切换连接串（`ZVMS_ORACLE_PASSWORD` 等 env + `settings.py`）；
4. 校验行数与关键统计口径，再删旧 schema。

这是停机窗口级别的工作，和代码改名解耦。在它发生之前，代码里的 `zvms_new`
就是真实 schema 名，不要「顺手」改成 `zvp5`——那会让文档和数据库对不上。
