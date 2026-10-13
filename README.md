简单说明:
1.当前仓库采取私有形式，预计前端v4.2.0(v5前瞻版本) 实现正式版后进行开源。
2.zvp-latest目录下为截至10.08时的zvms4(前端4.2.0-1005-beta)前后端。
3.zvp5-gitlab目录下为zzj在被gitlab封禁前在gitlab的zvp5后端代码。
4.zvms4-modified-main目录下为面向替换现役 zvp 的定制版 zvms4：数据库层已对齐现役 Atlas 结构；2026-10-09 起义工管理（创建义工/添加修改成员）按 zvp 移植并经安全审计，详见其内 STATUS.md。

## 学海（XueHai / 云作业）API 调用调查报告

> 调查对象：本仓库各代码线对**学海生态 API**的调用。结论一句话：**真正发起学海 API 调用的是 `zvp-latest`（参考 zvp 线）与 `zvp5-gitlab`（学海登录体系）；`zvms4-modified-main`（移植主线）已红线剥离，零调用**。

### 0. 学海生态域名

| 域名 | 角色 | 出现位置 |
|---|---|---|
| `admin.yunzuoye.net` | 学海管理后台 API（`/Other/TransferInterface2`） | `zvp-latest/frontend/src/api/xh.js:12` |
| `ztp.yunzuoye.cloud` | 学海平台 API（`/api/v1/pub/platform/users/{id}/profile`） | `zvp-latest/frontend/src/api/xh.js:31` |
| `oss.yunzuoye.net` | 学海静态资源（应用图标） | `zvp-latest/frontend/src/utils/constants.ts:2,4` |
| `cloud-resource.yunzuoye.net` | 学海前端打包资源（`js/app.js`，`@/utils/axios.ts`） | `zvp-latest/backend/routers/xh_router.py:69`（签名算法出处注释） |

### 1. 调用总览

| # | 调用方 | 形态 | 目标 | 签名 | 用途 |
|---|---|---|---|---|---|
| 1 | `zvp-latest` 后端 `POST /xh/api` | 通用签名代理（任意 URL） | 由前端传入 | `V1` / `encrytred` / `disabled` | 代前端向学海发起带签名的 HTTP |
| 2 | `zvp-latest` 前端 `transfer()` | `POST /../xh/api`（经 1） | `https://admin.yunzuoye.net/Other/TransferInterface2` | `disabled`（不签名） | 学海数据接口透传 |
| 3 | `zvp-latest` 前端 `getXHProfile()` | 经 1+2 | `http://ztp.yunzuoye.cloud/api/v1/pub/platform/users/{userId}/profile` | `disabled` | 取学海用户资料（`username`） |
| 4 | `zvp5-gitlab` 后端 | 登录请求类型 | 学海平板/数据中心设备 | `xhid` / `xhid_md5` | 以学海设备身份登录 ZVMS |

### 2. 后端签名代理 —— `zvp-latest/backend/routers/xh_router.py`

- **挂载**：`main.py:141` `app.include_router(xh_router.app, prefix="/xh")` → 对外端点 **`POST /xh/api`**（`xh_router.py:128`）。
- **请求体**（Pydantic 判别联合，`xh_router.py:102-126`）：`method`、`url`（任意）、`headers`、`body`、`token`、`options{sign_version,response_is_json,response_encoding}`。
- **签名算法 `sign_request`（:51-94）**，三档：
  - **`V1`**（默认，:63-67）：`sign = md5(method + url + t + KEY + body + "Authorization: " + token)`，其中 `t = 当前毫秒`；结果以 `?sign=<sign>&t=<t>`（已有 `?` 则 `&`）附加到 URL。
  - **`encrytred`**（:68-92）：`appCode='WEB002001'`、`appKey=md5('XH'+appCode)`、`timestamp=t`；GET 时把原 query 并入待签名对象；`raw = ''.join(f'{k}={obj[k]}&' for k in sorted(obj))`；`sign = md5(md5(raw + body))`；并写入请求头 `timestamp/sign/X-B3-SpanId/X-B3-TraceId/loginSign(空)/token`。
  - **`disabled`**（:61-62）：原样 URL，不签名。
- **硬编码密钥**（:46）：`KEY = 'eptim]q34imt5b]-q04i5q=fdkfjfsadlkjfasdfrt573df4pltoy]-pn965498d'`，注释标明 *dumped from `xhcore.so`*。
- **出站与 TLS**（:96-98, :152-159）：用 `urllib.request.urlopen(req, timeout=10, context=ssl_ctx)`；`ssl_ctx.check_hostname=False`、`verify_mode=CERT_NONE`，即**关闭证书校验**。
- **签名例外**（:147）：`body` 序列化后 `≥ 1 MiB` 时**不参与签名**（`None`）。
- **响应**（:168-173）：原样回传 `{actual_url, status, headers, body}`；`response_encoding='raw'` 时以字节返回。
- **错误映射**（:174-197）：JSON 解析失败 502、DNS 失败 502、超时 504、连接拒绝/重置 502、编码不存在 400、其它 500。
- **旧实现注释**（:199-201）：`/xhapi.html` 的 `GET /index.html` 已注释掉。

### 3. 前端 —— `zvp-latest/frontend/src/api/xh.js`

- `transfer(options)`（:5-26）：固定 `POST /../xh/api`，代理到 `https://admin.yunzuoye.net/Other/TransferInterface2`，`options.sign_version='disabled'`，把调用参数包进 `body` 的 `header` 字段。
- `getXHProfile(userId = getXuehaiId())`（:28-43）：经 `transfer` 请求 `http://ztp.yunzuoye.cloud/api/v1/pub/platform/users/{userId}/profile`；失败用 `ElMessage` 报错并抛异常。
- **唯一消费点**：`stores/user.ts:68` —— `username = verifiedName ?? window.webView?.getUserName?.() ?? (await import('@/api')).xh.getXHProfile(getXuehaiId()).username`；`stores/user.ts:8` 的静态导入已注释。

### 4. 设备桥接与 UA 判定

- `frontend/src/plugins/ua.ts`：
  - `getXuehaiId(): number` = `window.xhBrowserJava?.getUserId?.() || 0`（:7-9）；
  - `pad()` = `!!getXuehaiId()`（:11）；
  - `isXuehaiDCenter(requireFeature)` 依赖 UA 含 `userId/`、`deviceId/` 与 `window.webView.getUserName`（:13）。
- 后端 UA 门禁放行设备端：`zvp-latest/backend/main.py:82` 允许 UA 含 `com.xh`；:98 识别 `X-Requested-With ∈ {com.xh.browser, com.xh.datacenter}`。
- 学海设备请求头 `Xuehai-Id` 被采集进日志：`zvp-latest/backend/util/log.py:36`（`optional_int(header(req,'Xuehai-Id'))`）、`util/logify.py:26-30`。
- 前端常量含学海资源 URL：`utils/constants.ts:2,4`（`oss.yunzuoye.net`）。

### 5. `zvp5-gitlab` 的学海登录体系

- 请求类型（`schemas.py:20-32`）：
  - `XuehaiBrowserLoginRequest`：`login_type='xhbrowser'`、`device_identifier`、`xhid:int`；
  - `XuehaiDataCenterLoginRequest`：`login_type='xhdatacenter'`、`device_identifier`、`xhid`、`xhid_md5`、`name`、`class_name`。
- 校验（`routers/v1/platform.py:72-99`）：`device_identifier` 必须等于 `x_device_id` 且设备已注册（redis 或 `Device` 表）；按 `xhid` 查 `User`；数据中心登录额外校验 `md5(xhid).hexdigest()==xhid_md5` 与 `user.name==name`（否则 `DO_NOT_STEAL_ACCOUNT`）。

### 6. `zvms4-modified-main`（移植主线）的处置 —— 不调用

移植主线**有意不引入**学海调用面，历史记录见其 `STATUS.md` / `README.md`：

- 未移植：`xh_router.py` 整文件、`frontend/src/api/xh.js`、`routers/v1/platform.py` 学海登录、`util/logify.py`、`util/blocking.py`、`util/logs.py`。
- 剥离的设备面：`src/plugins/axios.ts` 的 Clarity/Xuehai/Device 三请求头与 `_clck`；`src/plugins/ua.ts` 的 `getXuehaiId()`/`xhBrowserJava` 桥接；`main.py` 的 `zvp_session` 设备会话 cookie；登录日志的三设备字段。
- 终局审计：后端全树对 `requests|httpx|aiohttp|urlopen|urllib` **零命中**（无任何出站 HTTP 能力点）；前端对 `xuehai|clarity|_clck|deviceId|xhBrowserJava|yunzuoye|window.open|urlopen` 仅剩历史日志展示/文案层残留。
- 文字/数据残留：`frontend/README.md`、`SECURITY.md`、`TECH_REPORT.md` 提及 XueHai 平板；ADB 线 `db/models.py` 的 `LOGS` 表保留 `XUEHAI_ID NUMBER(18)` 列（仅镜像 `zvms_new.logs` 历史设备 schema，代码零引用）。

### 7. 风险清单（学海调用面）

1. **硬编码签名密钥**：`KEY` 源自逆向 `xhcore.so`，随源码分发即泄露；任何持有者可为学海请求伪造签名。
2. **TLS 证书校验关闭**：`CERT_NONE` + `check_hostname=False`，中间人可篡改/窃听。
3. **开放 URL 签名代理（SSRF）**：`/xh/api` 接受任意 `url`，服务端代为发起请求且回传完整响应头，可被用于探测内网/绕过网络边界。
4. **设备身份与账号绑定**：`xhid`/`device_identifier` 直接映射到 `User`，设备指纹泄露面大。
5. **明文信道**：`getXHProfile` 使用 `http://ztp.yunzuoye.cloud`。
6. **≥1MiB body 免签名**：大请求体绕过签名校验。

### 8. 证据索引

- 后端代理与签名：`zvp-latest/backend/routers/xh_router.py:46,51-94,96-98,128-197`
- 挂载：`zvp-latest/backend/main.py:141`（UA 放行 :82,98）
- 前端调用：`zvp-latest/frontend/src/api/xh.js:5-43`；消费点 `stores/user.ts:68`
- 设备桥接：`zvp-latest/frontend/src/plugins/ua.ts:7-13`；常量 `utils/constants.ts:2,4`
- 日志采集：`zvp-latest/backend/util/log.py:36`、`util/logify.py:26-30`
- 学海登录：`zvp5-gitlab/schemas.py:20-32`、`routers/v1/platform.py:72-99`
- 主线剥离记录：`zvms4-modified-main/STATUS.md`（§红线剥离 / 终局 grep）、`zvms4-adb-migration:zvms4-modified-main/docs/ADB_SCHEMA_SPEC.md`（LOGS.XUEHAI_ID）
