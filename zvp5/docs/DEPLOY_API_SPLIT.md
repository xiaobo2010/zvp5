# 内容站 / API 直连拆分部署

本文档描述目标拓扑：主站负责页面和静态资源；数据库密集型 `/api` 请求直接发送到
靠近数据库的 API 域名，少数依赖内容站本机资源的端点保留在内容站，避免错误的跨机
房往返或访问到另一台机器的本地资源。

文中 `<content-domain>`、`<api-domain>` 是通用部署说明的占位符。发布工作流不保存
具体生产域名，而是从 GitHub Environment 读取基址并校验格式、健康端点、构建注入和
preconnect。

> **2026-11-02 更新（Web SSH 多目标）**：SSH 终端已改为「单网关 + 服务端目标目录」，
> 网关部署在 API 站。`/api/system/ssh/{targets,session,ws}` 改由 API 站代理到本机
> uvicorn；内容站不再承接 SSH。文件管理 `/api/system/files/*` 仍留在内容站。
> 契约、配置与 Nginx 片段见 [`WEBSSH_MULTI_TARGET.md`](./WEBSSH_MULTI_TARGET.md)。

## 1. 拓扑

```text
浏览器
  ├─ GET /、/assets/* ───────────────────────→ 内容站：静态文件
  ├─ /api/system/files/* ────────────────────→ 内容站：本机文件管理后端
  ├─ /api/system/ssh/* ──────────────────────→ API 站：SSH 网关（连前端/API 目标）
  └─ 其余 /api/*（绝对 API 地址）───────────→ API 站：nginx → uvicorn
```

| 角色   | 页面 Origin                | 请求目标                     | 职责                                             |
| ------ | -------------------------- | ---------------------------- | ------------------------------------------------ |
| 内容站 | `https://<content-domain>` | 同源；本机文件管理 API       | HTML、JS、CSS、图标、PWA 文件；本机文件管理      |
| API 站 | 不作为页面 Origin          | `https://<api-domain>/api/*` | FastAPI、数据库业务、AI、SSE、上传；Web SSH 网关 |

浏览器发出的 API 请求是跨域请求。浏览器 Origin 必须是内容站 Origin，不能填 API
域名；CORS 白名单配置的是“谁在调用 API”，不是“API 自己在哪里”。

## 2. 必须配置的环境变量

### 内容站前端构建

```text
VITE_API_BASE=https://<api-domain>/api
```

- 必须是绝对 HTTPS URL。
- 必须以 `/api` 结尾。
- 不得带用户名、密码、query 或 fragment。
- `vite build` 会校验以上条件；缺失或不合法时直接失败，不再静默回退到同源
  `/api`。
- 本地 `vite dev` 不需要该变量，仍由 Vite 代理 `/api` 到 `127.0.0.1:8000`。

模板见 `zvp5-frontend/.env.production.example`。实际值应来自部署系统，不要提交到
仓库。

### API 后端 CORS

```text
ZVMS_CORS_ALLOWED_ORIGINS=https://<content-domain>
```

- 逗号分隔多个完整 Origin。
- 只写 scheme + host + port，不能带路径或末尾斜杠。
- 禁止 `*`；API 使用 `allow_credentials=True`，必须回显明确 Origin。
- 后端在进程启动时读取，修改后重启 API 服务。

Systemd drop-in 示例：

```ini
# /etc/systemd/system/zvms4.service.d/cors.conf
[Service]
Environment="ZVMS_CORS_ALLOWED_ORIGINS=https://<content-domain>"
```

```bash
systemctl daemon-reload
systemctl restart zvms4
```

### 内容站 CSP

```text
ZVMS_API_ORIGIN=https://<api-domain>
ZVMS_API_WS_ORIGIN=wss://<api-domain>
ZVMS_STATIC_ORIGIN=https://<static-domain>
```

`nginx-security-headers.conf` 是模板，保留三个占位符：

- `__ZVMS_API_ORIGIN__`
- `__ZVMS_API_WS_ORIGIN__`
- `__ZVMS_STATIC_ORIGIN__`

使用仓库脚本渲染：

```bash
cd zvp5-backend-python
ZVMS_API_ORIGIN='https://<api-domain>' \
ZVMS_API_WS_ORIGIN='wss://<api-domain>' \
ZVMS_STATIC_ORIGIN='https://<static-domain>' \
python config/render_nginx_security_headers.py \
  --output /etc/nginx/snippets/zvms-security-headers.conf
nginx -t
systemctl reload nginx
```

未渲染时占位符不会匹配任何浏览器源，CSP 会 fail closed，跨域 API 被阻断。这是
有意设计，避免把某个环境域名固化进模板。

## 3. 前端构建

POSIX shell：

```bash
cd zvp5-frontend
VITE_API_BASE='https://<api-domain>/api' pnpm build
```

PowerShell：

```powershell
$env:VITE_API_BASE = 'https://<api-domain>/api'
pnpm build
```

构建后应确认主 bundle 内包含部署期 API 地址：

```bash
rg -l 'https://<api-domain>/api' dist/assets
```

部署到内容站：

```text
/www/zvms4/frontend-dist
```

API 站可以保留同一份前端文件用于应急查看，但 API vhost 不应承担页面入口职责。

## 4. 内容站 Nginx

内容站服务静态文件和 SPA 路由，并只代理必须访问内容站本机资源的少数路径。其余
`/api/*` 显式拒绝，防止错误配置把数据库 API 请求留在内容站：

```nginx
http {
    # 模板含 map，必须放在 http 上下文。
    include /etc/nginx/snippets/zvms-security-headers.conf;

    server {
        server_name <content-domain>;

        root /www/zvms4/frontend-dist;
        index index.html;

        # 本机资源端点：前端用页面 Origin 访问，只保留文件管理。
        # Web SSH 已于 2026-11-02 迁到 API 站网关（见 WEBSSH_MULTI_TARGET.md），
        # 内容站不再承接 /api/system/ssh/*，由下面的 ^~ /api/ 返回 404。
        location ^~ /api/system/files/ { proxy_pass http://127.0.0.1:8018; }

        # 不允许内容站兜底代理其他 API。
        location ^~ /api/ { return 404; }

        location /assets/ {
            try_files $uri =404;
            expires 30d;
            add_header Cache-Control "public, immutable";
        }

        location / {
            try_files $uri $uri/ /index.html;
        }
    }
}
```

如果 `location /assets/` 自己声明了 `add_header`，Nginx 不会继承 server 级别的
安全响应头。生产配置应把安全头放在 `http` 级别的 include 中，或在该 location
重复所需响应头，然后用实际响应验证。

## 5. API 站 Nginx

API vhost 只暴露数据库业务 API、AI、SSE 和它自己的实时接口。内容站本机运维路径
不应转发到这里；否则读到的是 API 机的文件系统或 SSH 环境。

```nginx
map $http_upgrade $zvms4_connection_upgrade {
    default upgrade;
    "" close;
}

server {
    server_name <api-domain>;
    client_max_body_size 25m;

    # 文件管理仍是内容站本机资源，API 域名不承接。
    location ^~ /api/system/files/ { return 404; }

    # Web SSH：API 站是网关（2026-11-02 起）。targets 由下面的 ^~ /api/ 覆盖；
    # session 单独声明，ws 必须转发 Upgrade。
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
        proxy_set_header Connection $zvms4_connection_upgrade;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 3600s;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:8018;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 120s;

        # AI SSE 必须关闭代理缓冲。
        proxy_buffering off;
    }

    location /socket.io/ {
        proxy_pass http://127.0.0.1:8018;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $zvms4_connection_upgrade;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 3600s;
    }

    location / {
        return 404;
    }
}
```

CORS 响应头由 FastAPI `CORSMiddleware` 生成，不应在 API Nginx 中重复添加，否则容易
产生重复的 `Access-Control-Allow-Origin`。

## 6. 跨域请求行为

前端统一 API 基址由 `plugins/axios.ts` 的 `baseURL` 提供：

- Axios 请求：`withCredentials: true`，携带 `Authorization`。
- AI SSE/JSON：原生 `fetch`，同样使用绝对 `baseURL`。
- 活动上传：Element Plus Upload 使用绝对 `baseURL` 和 Bearer token。
- 登出：原生 `fetch` 使用绝对 `baseURL`。
- SSH 票据与 WebSocket：固定使用页面 Origin，确保票据和终端落到同一台内容站。

后端允许任意请求头和方法，但使用严格 Origin 白名单。以下响应头必须暴露给浏览器：

```text
X-Ban-Reason
X-Ban-Until
Retry-After
X-AI-Request-Id
X-AI-Quota-Used
X-AI-Quota-Limit
```

这些头分别用于封禁提示、重试等待、请求追踪与额度展示；不在
`Access-Control-Expose-Headers` 中时，浏览器会返回 `null`。

下载接口使用 Axios blob 和前端自带文件名，不依赖跨域读取
`Content-Disposition`。

## 7. 验证

### CORS 预检

```bash
curl -i -X OPTIONS 'https://<api-domain>/api/health' \
  -H 'Origin: https://<content-domain>' \
  -H 'Access-Control-Request-Method: GET' \
  -H 'Access-Control-Request-Headers: authorization,content-type,xuehai-id,x-device-unlock'
```

必须满足：

- 状态码为 `200`。
- `Access-Control-Allow-Origin` 精确等于内容站 Origin。
- `Access-Control-Allow-Credentials: true`。
- 请求头和方法均被允许。

### 暴露响应头

```bash
curl -i 'https://<api-domain>/api/health' \
  -H 'Origin: https://<content-domain>'
```

检查 `Access-Control-Expose-Headers` 包含第 6 节列出的全部名称。

### WebSocket

浏览器访问内容站后，SSH WebSocket 应连接：

```text
wss://<content-domain>/api/system/ssh/ws?ticket=<one-time-ticket>
```

SSH 的票据申请和 WebSocket 必须落在同一内容站实例；不能把申请请求发到 API 域名，
再把 WebSocket 接到内容站。

## 8. 发布顺序

1. 更新 API 后端代码与 `ZVMS_CORS_ALLOWED_ORIGINS`，重启 `zvms4`。
2. 渲染新的 CSP 文件，执行 `nginx -t` 后 reload 内容站 Nginx。
3. 使用绝对 `VITE_API_BASE` 重新构建前端并发布 dist。
4. 确认内容站只反向代理证书、文件管理和 SSH 的本机路径，其余 `/api/*` 返回 404。
5. 执行 CORS、SSE、上传、下载、AI 和 SSH WebSocket 冒烟测试。

前端与服务端必须作为一组发布：旧前端仍使用同源 `/api`，新前端使用绝对 API
地址。若只发布其中一侧，就会落入同源/跨域混用状态。

### 现网反代注意事项

1. **反代必须改写 `Host`**：`/api`、`/api/system/ssh/ws`、`/socket.io/` 三处都要
   `proxy_set_header Host zvpapi.xiaobopisces.top`。tencent-43 上 443 的**默认站点**
   是旧域名块，用 `$host`（`zvp.xiaobopisces.top`）透传时匹配不到任何 server，
   会落到默认站点——停用期间正好是 410，于是 `/api` 全线 410。这是本次最隐蔽的一个坑。
2. **旧域名停用不要用 410，要用 301**：410 属于「启发式可缓存」状态码，客户端会把
   停用状态缓存住；实测出现过服务端 0 条 410、用户浏览器仍持续显示 410 的情况。
   现在的做法是 `return 301 https://zvp.xiaobopisces.top$request_uri` 并带
   `add_header Cache-Control "no-store" always;`。若要彻底不响应，用 444。
   `zvp.albwxy.fun` 已按此停用，`albwxy.fun`（class-site）不受影响。
3. 内容站反代是双层 nginx，API 侧 nginx 日志里的客户端 IP 是 `23.169.184.163`；
   需要真实 IP 时依赖 `X-Forwarded-For`（后端已按此解析）。
4. Redis 只在本机监听；`/etc/redis/redis.conf` 末尾的覆盖段不要删。
