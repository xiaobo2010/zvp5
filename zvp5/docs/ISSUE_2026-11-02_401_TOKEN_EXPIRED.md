# 登录 401 `Token expired` 定位报告（2026-11-02）

> 现象：在登录页输入正确账号密码后，浏览器不进入系统，`POST /api/users/auth` 直接返回
> `401 {"detail":"Token expired"}`。直觉上像是「没登录就被判登录态过期」，实际与 JWT、
> 登录态、后端时钟都无关，是**客户端本地时钟与服务器相差超过 60 秒**，被凭据新鲜度
> 校验拦下。本文只做定位，不含代码改动。

## 0. 结论

| 项 | 结论 |
|---|---|
| 直接原因 | `util/cert.py:292-316` 的 `_credential_timestamp()` 判定客户端凭据里的 `time` 落在 server now ±60s 之外，抛 `401 Token expired`（`:305`） |
| 触发条件 | **仅此一种**：客户端 `Date.now()` 与服务器时间差 > 60 秒（双向） |
| 不是 | JWT 过期（那条是 `Could not validate credentials`，`util/object_id.py:211`）、不是密码错误（403）、不是服务端时钟漂移 |
| 受影响方 | 单一客户端（Chrome/155）。同一出口 IP 的另一客户端（Chrome/145）同一时刻登录成功并正常使用后台 |
| 前端为何显示成「未登录」 | 401 在 `src/plugins/axios.ts:284-304` 被统一接管：无 token 弹「未登录」、有 token 弹「登录已过期」，**detail 不展示**，`Token expired` 只在 Network/控制台可见 |

## 1. 触发点：唯一的 `Token expired`

```
util/cert.py:292  def _credential_timestamp(value) -> float:
util/cert.py:301      if timestamp > 10_000_000_000: timestamp /= 1000   # ms → s
util/cert.py:303      now = datetime.datetime.now(datetime.timezone.utc).timestamp()
util/cert.py:304      if timestamp < now - 60 or timestamp > now + 60:
util/cert.py:305          raise HTTPException(status_code=401, detail="Token expired")
```

调用链只有登录/改密/身份复核这一条：

```
routers/users_router.py:498-500   validate_by_cert(id, credential, validation_mode)
util/cert.py:362                  auth_field = _read_credential(cert)
util/cert.py:316                  _credential_timestamp(credential.get("time"))
```

三个要点：

1. **前端没有单位问题**：`src/api/user/auth.ts:36`（改密）与 `:92`（登录）都是
   `time: Date.now()`（毫秒），后端 `:296` 会归一化成秒；线上 dist 里也确认是
   `time:Date.now()`。
2. **对称窗口是新行为**：`c41b2b9`（2026-10-17）引入 `_credential_timestamp` 时同时
   加了上界。旧实现只有 `time < now - 60`，即**只拒绝「过旧」的凭据**；客户端时钟
   **偏快**过去能通过，现在会被拒。
3. **非存在学号不会走到这里**：`users_router` 先解析用户（`:468-487`），查不到直接
   403「用户名或密码错误」，所以这条 401 只可能发生在**真实账号**上。

## 2. 生产证据（nginx / journal / chrony，IP 与 oid 已脱敏）

时间轴（+0800，2026-11-02，API 主机 zvp-backend）：

| 时间 | 客户端 | 请求 | 结果 |
|---|---|---|---|
| 16:41:37 | Chrome/145 | `POST /api/users/auth` | **200**（登录成功） |
| 16:41:37~16:43:37 | Chrome/145 | `/api/v3/users/*`、`/api/v2/users?query=…`、`/api/v3/notifications` 等 | 全部 200（后台正常使用），16:43:37 正常 logout |
| 16:41:44 / :55 / 42:16 / 43:42 / 43:54 / 46:52 | Chrome/155 | `POST /api/users/auth` | **6 次 401，响应体 26 字节** |
| 同期 | Chrome/155 | `/`、`UserLogin-*.js` 等静态资源 | 200 —— 该客户端全程停在登录页 |

- 26 字节 = `{"detail":"Token expired"}`（逐字节吻合）；同一端点其它两种失败是
  37 字节（`用户名或密码错误`）与 43 字节（`Could not validate credentials`），
  日志里的状态码 + 字节数足以区分三者。
- 6 次 401 前都各有一条 `GET /api/cert`（200），说明是**真人反复重试**，不是自动重试。
- 服务端时间无异常：`chronyc tracking` 系统时间比 NTP 慢 0.00003s，`/var/log/syslog`
  在事故窗口内没有校时/跳变记录。
- 另一客户端（Chrome/145，同出口 IP）同时段 200 正常 → 账号、密码、后端、网络都不是变量，
  唯一差异是**那台机器的本地时间**。

## 3. 复现实验（生产，只读结论 + 3 条失败日志）

用真实 oid + 假密码，仅改 `time` 值，观察返回（毫秒时间戳，经同一套 RSA/OAEP 加密）：

| 时间戳偏移 | 响应 | 说明 |
|---|---|---|
| -120s / -90s | `401` 26B `{"detail":"Token expired"}` | 与线上 6 次 401 完全一致 |
| -30s / 0 / +30s | `403` 37B `用户名或密码错误` | **时间戳校验通过**，只是密码不对 |
| +90s | `429` `AUTH_RATE_LIMITED` | 连续失败触发登录限速，先于时间戳检查 |

结论：±60s 校验逻辑本身工作正常，不存在误判；能产生这条 401 的输入只有「时间戳偏出窗口」。

## 4. 根因与自查

根因：**出问题那台客户端（Chrome/155）的系统时间与服务器偏差 > 60 秒**。Windows /
虚拟机休眠恢复、CMOS 电池、关闭「自动设置时间」都是常见来源；偏差方向未知（快了慢了
都是同一条 401），但从「旧版本只拒过旧凭据」推断，若历史上同一台机器能登录、现在不行，
多半是**偏快**。

自查（不改代码）：

```js
// 出问题的浏览器控制台
Date.now() / 1000
```

```bash
curl -sI https://zvpapi.xiaobopisces.top | grep -i '^date'
```

两者相差 > 60 秒即可确诊；把系统时间设为自动同步后重试即可登录。

## 5. 建议（本次未实施，待产品拍板）

1. **登录页时钟预检**：`GET /api/cert` 已带 `Date` 头，登录前比一次，偏差别大时直接提示
   「本机时间与服务器相差 N 秒」，比现在的「未登录」有用得多。
2. **窗口策略**：60s 双向对客户端时钟过于敏感；可放宽到 5 分钟，或改为「服务端下发
   server_time + 前端校正」，彻底摆脱客户端时钟依赖。
3. **文案归属**：登录接口的 401 目前被 axios 拦截器当成「会话失效」处理（清 token、跳
   登录页）。登录页自身的失败应走页面内提示，不该复用会话失效的语义。

## 6. 验证副作用（如实记录）

复现实验对生产账号 oid `68c3****`（已脱敏）产生了 3 条 `login_fail` 审计行，并使
`<BACKEND_IP>` 对该账号的登录限速生效数分钟（后续请求 429）。`login_failure_burst`
默认 `mode=alert`、阈值 10/h（`services/risk_monitor.py:66-77`），3 条不会触发封禁；
账号本身与其它出口 IP 的登录不受影响。
