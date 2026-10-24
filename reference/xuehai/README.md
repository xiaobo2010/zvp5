# 学海 API 参考归档（来自 zvp-latest）

> **警告：这是从 `zvp-latest` 原样复制的代码，按仓库既有结论包含后门、任意 URL 代理、设备身份采集和外呼能力。**
> 仅用于阅读、协议对照和安全审计，**不得运行、不得导入主线、不得重新接线**。

## 1. 保留了哪些学海能力

### 1.1 后端代理与签名

- `backend/routers/xh_router.py`
  - `POST /xh/api`：通用签名代理，请求体可指定目标 URL、method、headers、body。
  - 内置签名参数与硬编码签名材料；属于明确的红线拒绝面。
- `backend/main.py`
  - `app.include_router(xh_router.app, prefix="/xh")` 的挂载方式。
  - UA 门禁对 `com.xh` 设备的放行分支。
  - `X-Requested-With` 的学海浏览器/资料中心识别分支。
  - `zvp_session` 设备会话 cookie 相关逻辑。

### 1.2 平台登录与身份验证

- `backend/routers/v1/platform.py`
  - `POST /api/v1/platform/login`
  - `GET /api/v1/platform/cert`
  - `GET /api/v1/platform/totp`
  - `GET /api/v1/platform/logs`
  - 密码/临时授权/学海浏览器/资料中心/恢复问答等登录分支。
  - 设备绑定、`sid` 会话与设备核验逻辑。
- `backend/routers/users_router.py`
  - v1 登录中的设备绑定与 `session_id` 写入。
- `backend/util/get_user.py`
  - 从 token 读取 `session_id` 并绑定用户。

### 1.3 设备识别与请求头采集

- `frontend/src/plugins/ua.ts`
  - `window.xhBrowserJava.getUserId()` 桥接。
  - 资料中心 UA 的 `userId/`、`deviceId/` 识别。
- `frontend/src/plugins/axios.ts`
  - 学海 ID、Clarity、设备相关请求头/cookie 的注入逻辑。
- `backend/typings/log.py`
  - `Xuehai-Id` 请求头解析与日志字段。
- `backend/util/log.py`、`backend/util/logify.py`
  - 请求头、cookie、IP、学海 ID 的落库/审计链。
- `backend/util/blocking.py`、`backend/util/logs.py`
  - 与设备/安全拦截相关的辅助代码。
- `backend/routers/logs_router.py`
  - 按 `clarity` / `xuehai` 字段搜索的日志查询分支。
- `frontend/src/components/log/ZLogCard.vue`
  - 管理端对设备/学海字段的展示。
- `frontend/src/types/log.d.ts`
  - 日志中的学海/设备字段类型。

### 1.4 前端学海调用链

- `frontend/src/api/xh.js`
  - `transfer()`：经 `/../xh/api` 转发到 `admin.yunzuoye.net/Other/TransferInterface2`。
  - `getXHProfile()`：读取 `ztp.yunzuoye.cloud` 用户资料。
- `frontend/src/services/platform.ts`
  - 平台登录、证书、日志、TOTP 的前端封装。
  - 使用 `utils/crypto.ts`、`utils/byteArray.ts` 做 RSA 加密。
- `frontend/src/stores/user.ts`
  - 登录时读取学海 ID、设备核验与平台登录分支。
- `frontend/src/utils/constants.ts`
  - 学海资料中心/浏览器图标 URL 与最低版本常量。
- `frontend/src/App.vue`
  - 应用层与学海/平台初始化相关的残留逻辑。
- `frontend/README.md`
  - 原前端说明中的学海相关背景。

## 2. 文件清单

```text
reference/xuehai/zvp-latest/
├── backend/
│   ├── main.py
│   ├── routers/
│   │   ├── xh_router.py
│   │   ├── users_router.py
│   │   ├── logs_router.py
│   │   └── v1/platform.py
│   ├── typings/log.py
│   └── util/{blocking,get_user,log,logify,logs}.py
└── frontend/
    ├── README.md
    └── src/
        ├── App.vue
        ├── api/xh.js
        ├── components/log/ZLogCard.vue
        ├── plugins/{axios.ts,ua.ts}
        ├── services/platform.ts
        ├── stores/user.ts
        ├── types/{log.d.ts,services.d.ts}
        └── utils/{byteArray.ts,constants.ts,crypto.ts}
```

## 3. 已知外部目标

归档中出现的学海/外站目标（仅供审计定位，禁止调用）：

- `admin.yunzuoye.net/Other/TransferInterface2`
- `ztp.yunzuoye.cloud/api/v1/pub/platform/users/{id}/profile`
- `oss.yunzuoye.net/...` 图标资源
- `cloud-resource.yunzuoye.net/...` 前端打包资源

主线 `zvms4-modified-main` 对这些能力维持拒绝：不做任意 URL 代理、不做设备会话绑定、不向学海发起请求、不在登录/鉴权路径外呼。
