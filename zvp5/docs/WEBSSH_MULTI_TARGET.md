# Web SSH 多目标（单网关）

> 2026-11-02。方向：SSH 终端不再绑定「前端/内容站本机」，改为**单网关 + 服务端目标目录**。
> 网关部署在 API 站，按用户选择连接前端服务器或 API 服务器；浏览器只拿 `id/label/display`，
> 永远拿不到 host/port/user/密钥路径。双人 TOTP、一次性 ticket、审计与并发上限全部保留。

## 1. 拓扑

```text
浏览器 /server/ssh
  ├─ GET  https://<api-domain>/api/system/ssh/targets        → [{id,label,display}], default
  ├─ POST https://<api-domain>/api/system/ssh/session        → ticket（绑定 target）
  └─ WS   wss://<api-domain>/api/system/ssh/ws?ticket=...    → 网关 → asyncssh → target.host:22
```

- `/system/ssh/*` 全部走 API host（`VITE_API_BASE`），不再走页面源。
- `/system/files/*` 仍走内容站：文件管理操作的仍是「打开页面的那台机器」。
- 网关可以是任意一台能同时 SSH 到两个目标的机器；当前选择 API 站。

## 2. API 契约

### `GET /api/system/ssh/targets`

仅 admin。只回展示字段，不回连接参数：

```json
{
  "status": "ok",
  "code": 200,
  "data": {
    "targets": [
      { "id": "content", "label": "前端服务器", "display": "root@<INTERNAL_HOST>" },
      { "id": "api", "label": "API 服务器", "display": "root@127.0.0.1" }
    ],
    "default": "content"
  }
}
```

### `POST /api/system/ssh/session`

```json
{ "totp_code": "123456", "target": "api" }
```

- `target` 可省略，后端取默认目标；未知 id → `400 SSH_UNKNOWN_TARGET`（在验码之前判，不消耗 TOTP）。
- 通过后仍走 `require_dual_auth(action="web_ssh")`，返回：

```json
{
  "status": "ok",
  "code": 200,
  "data": { "ticket": "...", "expires_in": 120, "target": { "id": "api", "label": "API 服务器" } }
}
```

### `WS /api/system/ssh/ws?ticket=...`

- ticket 里绑定 `target_id`；WS 只信 ticket，不再接受任何目标参数。
- 发 ticket 后配置被改掉、目标已不存在 → 收手并关 `4500`（`终端目标不可用`），不会静默连到别的机器。
- open/close 审计的 `web_ssh` detail 带 `target=<id>`；命令审计内容不变。

## 3. 服务端配置

网关后端读取（优先级从高到低）：

1. 环境变量 `ZVMS_SSH_TARGETS`（JSON 字符串）；
2. `settings.SSH_TARGETS`（list 或 JSON 字符串）；
3. 都没有 → 退化为老的单目标 `id=self, label=本机`，参数继续走 `ZVMS_SSH_HOST/PORT/USER/KEY_PATH/KNOWN_HOSTS`。

默认目标：`ZVMS_SSH_DEFAULT_TARGET` → `settings.SSH_DEFAULT_TARGET` → 列表第一项。

```json
[
  {
    "id": "content",
    "label": "前端服务器",
    "host": "<INTERNAL_HOST>",
    "port": 22,
    "user": "root",
    "key_path": "/www/zvms4/backend/ssh_key.content",
    "known_hosts": "/www/zvms4/backend/ssh_known_hosts.content"
  },
  {
    "id": "api",
    "label": "API 服务器",
    "host": "127.0.0.1",
    "port": 22,
    "user": "root",
    "key_path": "/www/zvms4/backend/ssh_key",
    "known_hosts": "/www/zvms4/backend/ssh_known_hosts"
  }
]
```

约束：

- `id` 只允许 `^[a-z0-9][a-z0-9_-]{0,31}$` 且唯一；它会进 ticket、审计和前端参数，因此不做转义。
- 坏项丢弃、好项保留；全坏/空串/`[]`/非法 JSON → 退化单目标，升级漏配不会直接把终端打挂。
- warning 日志只写序号 + 固定原因码，**密钥路径与 known_hosts 原文绝不入日志**。
- 每个目标可以共用同一份密钥，也可以各自独立；建议每台一把、最小权限。

## 4. 部署：API 站 Nginx

API 站必须把这三个路径代理到本机 uvicorn（**删除历史上针对 SSH 的 `return 404`**）：

```nginx
# /api/system/ssh/targets 由 ^~ /api/ 覆盖；下面两条单独声明
location = /api/system/ssh/session {
    proxy_pass http://127.0.0.1:8018;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Real-IP $remote_addr;
}

location = /api/system/ssh/ws {
    proxy_pass http://127.0.0.1:8018;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $zvms4_connection_upgrade;  # 与 map 同名即可
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_read_timeout 3600s;
}
```

内容站不再需要 `/api/system/ssh/*`；那些 location 可以保留（前端已不再调用）或改成 404。
`/api/system/files/` 仍在内容站。

## 5. 前端与本地开发

- 生产构建仍注入 `VITE_API_BASE=https://<api-domain>/api`；`buildSshWsUrl()` 由它推导 `wss://`。
- 本地 `vite.config.ts` 的 `/api` 代理已开 `ws: true`，否则 WebSocket 升级不会转发到 uvicorn。
- 切换目标 = 断开现有会话 + 清屏；下一次连接必须重新过双人码。授权框打开期间目标选择器锁定，
  避免「授权的是 A、连到 B」。

## 6. 验证清单

1. `GET https://<api-domain>/api/system/ssh/targets`（admin token）返回两个目标且**不含** `host/key_path/known_hosts`。
2. 选 A → 过双人码 → WS 握手 `101` → `hostname` 是 A；断开，选 B 再来一次，`hostname` 是 B。
3. 审计：`system_ssh` 的 `open`/`close` detail 中有 `target=<id>`；命令日志类型仍为 `system_ssh_command`。
4. 未知 target：`POST /session` 返回 400，且不消耗动态码。
5. 把 `ZVMS_SSH_TARGETS` 清空重启 → 仍能以 `self` 目标连接（回滚路径）。

## 7. 已知边界

- ticket 仍在进程内存，单 worker 前提不变；多 worker 需要先迁 Redis。
- 网关到目标机的网络与密钥由部署侧保证；连不上只回通用错误，不暴露 host/密钥路径。
- 本方案不做本地 PTY / 不绕过 sshd；服务器 SSH 本身不可用不在代码兜底范围内。

## 8. 部署实录（2026-11-03）

> 目标主机：API/网关 = zvp-backend（<BACKEND_IP>），前端源站 = zvp-frontend
> （<FRONTEND_IP>）。提交基线 `5eadd16`（含 19f085f 多目标实现）。

### 8.1 审核结论

| 项 | 结论 |
|---|---|
| 后端全量 | `pytest -q` → **4189 passed**（2:40） |
| 前端全量 | `vitest run` → 90 files / **1008 passed**；`vue-tsc`（tsconfig.app 与 vitest 两套）0 错误 |
| 安全面 | `GET /targets` 仅 admin 且只回 id/label/display；未知 id 在验码前 400，不消耗 TOTP；ticket 绑定 target，配置变更后拒连（4500）；连接参数只从服务端配置解析 |
| 兼容面 | 未传 `target` 的老前端仍按默认目标走；`load_target()` 语义保留，AI Agent 的 `ssh_exec`/`ops_tools` 自动跟随默认目标 |

### 8.2 目标与密钥（网关侧 = zvp-backend）

- 密钥：沿用前端既有的 `zvms4-webssh` ed25519（`/www/zvms4/backend/ssh_key`，
  600 www-data）；公钥已存在于前端 `root@` 的 authorized_keys，本次追加到
  zvp-backend `root@` 的 authorized_keys（备份 `/root/backup-20261103/authorized_keys`）。
- known_hosts **按目标分开**：两台机器的 `127.0.0.1` host key 不同，混在一个文件会冲突。
  - `ssh_known_hosts.content` ← `ssh-keyscan <FRONTEND_IP>:22`
  - `ssh_known_hosts` ← `ssh-keyscan 127.0.0.1:23456`
- 注意：后端的 sshd 端口是 **23456**（不是 22），target 必须显式写 `port: 23456`。
- 配置走 systemd drop-in：`/etc/systemd/system/zvms4.service.d/10-ssh-targets.conf`
  → `EnvironmentFile=/etc/zvms4/ssh-targets.env`（640 root:root）：

```text
ZVMS_SSH_TARGETS=[{"id":"content","label":"前端服务器","host":"<FRONTEND_IP>","port":22,...},
                  {"id":"api","label":"API 服务器","host":"127.0.0.1","port":23456,...}]
ZVMS_SSH_DEFAULT_TARGET=api
```

**默认目标选 `api`（而不是文档示例的 `content`）**：升级前 `load_target()` 的语义是
「后端所在机器」，AI Agent 的 SSH 工具与其他遗留调用方都吃这个默认值；保持 `api`
等于保持升级前落点，人类 UI 仍可在下拉里选前端服务器。

### 8.3 变更流水

| # | 主机 | 操作 | 备份 |
|---|---|---|---|
| 1 | 后端 | 后端整目录备份后 rsync（排除 `.venv`/密钥/`settings.py`/本地 db），`chown -R www-data` | `/www/zvms4/backend.bak-sshmulti-20261103-144241`（57M） |
| 2 | 后端 | 密钥/known_hosts 落位；authorized_keys 追加公钥 | 同上 authorized_keys 备份 |
| 3 | 后端 | 新增 `EnvironmentFile` drop-in，`daemon-reload` + `restart` | 原 unit 未改 |
| 4 | 前端 | 前端备份后 rsync `--delete` 新 dist，`chown -R www:www` | `frontend-dist.pre-sshmulti-20261103-1450`（22M） |

Nginx **无需改动**：后端 `zvpapi` vhost 已把 `/api/` 与 `/api/system/ssh/ws` 代理到
127.0.0.1:8018；前端的 `/api/` 也已整体反代回后端。仓库模板里内容站的 SSH location
保留作单机部署兼容。

### 8.4 验证记录（已复跑）

| 项 | 方法 | 结果 |
|---|---|---|
| 配置解析 | 以服务进程 env 导入 `ssh_bridge.load_targets()` | `[('content','前端服务器','root@<FRONTEND_IP>',22),('api','API 服务器','root@127.0.0.1:23456',23456)]`，default=`api`，无 drop warning |
| 网关连通性 | `runuser -u www-data -- ssh -i ... -o StrictHostKeyChecking=yes` | api → `VM-0-15-debian`；content → `XiaoBoCloud-CMCU` |
| 路由存在性 | `GET /api/system/ssh/targets`（无 token） | `401 Could not validate credentials`（两个域名都是 401，不是 404） |
| WS 升级 | `curl --http1.1` 带 Upgrade，无 ticket | `101 Switching Protocols` → 服务端主动关并回「ticket 无效或已过期」 |
| 前端产物 | 首页 / 主 bundle | h2 200；bundle gzip 168183B；含 `preconnect`/`dns-prefetch`；`ServerSsh-*.js` 含目标选择文案 |
| API | `https://zvp.../api/version` | 200，`v4.3.0-7` |
| 服务日志 | `journalctl -u zvms4` 重启后 | 启动干净，无 `ssh targets` 告警 |

### 8.5 未做与待人工确认

- **双人码端到端**（选目标 → TOTP → `hostname` 核对 → 审计 `target=`）需要真人动态码，
  未在本次部署中执行，留作上线后第一次使用的验收项。
- 新增连通性带来的权限面：网关密钥可用于 `root@` 两台机器，后端进程一旦被攻破即等同
  两机 root。密钥仅 600 www-data 可读，若需收窄应改为每目标一把 + sudo 白名单。
- 前端的 `/api/system/files/*` 现已随 `/api/` 整体反代到后端，文件管理操作的其实是后端
  机器；这是 11-02 前端源站迁移时就存在的语义偏差，与本次改动无关，另行决策。

### 8.6 回滚

1. 后端：`rsync -a /www/zvms4/backend.bak-sshmulti-20261103-144241/ /www/zvms4/backend/`
   （不带 `--delete`），`chown -R www-data` 后 `systemctl restart zvms4`。
2. 配置：删 `/etc/systemd/system/zvms4.service.d/10-ssh-targets.conf` +
   `daemon-reload` + `restart` → 自动退化为单目标 `self`（老路径，默认 `127.0.0.1:22`）。
3. 前端：`rsync -a --delete /www/zvms4/frontend-dist.pre-sshmulti-20261103-1450/ /www/zvms4/frontend-dist/`
   + `chown -R www:www`。
