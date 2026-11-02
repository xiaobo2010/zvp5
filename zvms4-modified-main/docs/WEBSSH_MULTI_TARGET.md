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
      { "id": "content", "label": "前端服务器", "display": "root@192.0.2.11" },
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
    "host": "192.0.2.11",
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
