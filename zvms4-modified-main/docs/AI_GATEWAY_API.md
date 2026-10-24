# AI 网关 API

适用代码：`zvms4-backend-python` @ `fd2f006`（2026-10-24）
挂载点：`main.py` → `app.include_router(ai_router.router, prefix="/api/ai")`

网关有三件事：**LLM 对话**（OpenAI 兼容面）、**OCR**（多引擎池）、**运维面**（凭据 / 引擎 / 额度 / 流水 / 健康）。
OCR 还有一条附件侧入口 `POST /api/attachments/{oid}/ocr`，与 `/api/ai/ocr` 共用同一套计价。

---

## 1. 通用约定

### 1.1 鉴权

与平台其它接口一致：`Authorization: Bearer <token>`（登录态 JWT / session）。
没有独立的「AI API Key」体系；OpenAI SDK 走的也是同一个 token。

| 门禁 | 判定 |
|---|---|
| `AI_FORBIDDEN` | 角色不在 `config.AI_ALLOWED_ROLES`（默认 `admin`、`department`） |
| `admin` | 凭据 / 引擎 / 额度管理 / 流水 / 健康 |
| `department`（学生会） | OCR、对话、查自己的额度 |
| 普通用户 | 无 AI 权限 |

个人凭据（`scope=personal`）的调用**不占**平台月度额度（自付上游成本）。

### 1.2 两套响应协议

**zvp 面**（`/ocr`、`/keys`、`/logs`、`/ocr-engines`、`/quota`、`/health`）：

```json
{ "status": "ok", "code": 200, "data": { } }
```

错误用 HTTP 状态码 + 稳定的错误码字符串，客户端按 `detail` 分支，不解析长句：

```json
{ "detail": "AI_OCR_SITE_QUOTA_EXHAUSTED" }
```

**OpenAI 面**（`/v1/models`、`/v1/chat/completions`）：

```json
{ "error": { "message": "...", "type": "rate_limit_error", "code": "insufficient_quota" } }
```

`type` 由状态码推导：429 → `rate_limit_error`，5xx → `server_error`，其余 → `invalid_request_error`。

### 1.3 公共响应头

| 头 | 出现时机 | 含义 |
|---|---|---|
| `X-AI-Request-Id` | 对话（含流式） | 请求编号，对账 / 排查用 |
| `Retry-After` | 429（频控 / 上游限流） | 建议退避秒数（≥1） |
| `X-AI-Quota-Used` | 429 额度耗尽 | 本月已用量 |
| `X-AI-Quota-Limit` | 429 额度耗尽 | 本月上限 |

### 1.4 状态码语义

| 码 | 含义 |
|---|---|
| 200 | 成功 / 已在处理中（幂等） |
| 201 | 资源已创建 |
| 202 | 已受理，后台异步执行（OCR 入队） |
| 204 | 删除成功，空体 |
| 400 | 参数 / 类型不合法（`AI_*` 错误码） |
| 403 | 角色不足或没有该附件的读权限 |
| 404 | 资源不存在 |
| 409 | 唯一性冲突（重名） |
| 429 | 频控或额度耗尽 |
| 502 | 上游故障（鉴权失败 / 拒绝 / 5xx / 不可读凭证） |
| 503 | 网关不可用（未启用 / 无可用上游 / 无可用 OCR 引擎 / 站点额度全满） |
| 504 | 上游超时 |

---

## 2. OCR

### 2.1 POST /api/ai/ocr —— 入队

```http
POST /api/ai/ocr
Content-Type: application/json

{ "attachment_oid": "0123456789abcdef01234567" }
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `attachment_oid` | string | 是 | 附件 oid，必须已通过附件 API 落库 |
| `provider` | string \| null | 否 | **收下但忽略**，保留给以后按请求指定引擎；不能用它绕过站点额度 |

> 网关不提供图片直传：OCR 只处理已落库的附件，否则就成了绕过附件配额与内容审核的旁路。

响应 `data`：

| 状态 | HTTP / body code | `data` |
|---|---|---|
| 新受理 | 202 / 202 | `{status:"pending", attachment_oid, engine}` |
| 已在处理 | 200 / 200 | `{status:"processing", attachment_oid}`（不重复计价） |

错误：

| 状态 | `detail` | 触发 |
|---|---|---|
| 403 | `AI_FORBIDDEN` | 角色无 AI 权限 |
| 404 | `Attachment not found` | oid 不存在 |
| 403 | `Permission denied` | 无权读该附件 |
| 400 | `AI_UNSUPPORTED_MEDIA` | mime 不在 `image/jpeg`、`image/png`、`image/webp` |
| 429 | `AI_RATE_LIMITED` | 超过 10 次/分钟（带 `Retry-After`） |
| 429 | `AI_QUOTA_EXCEEDED` | 个人 `ocr` 月额度耗尽（带 `X-AI-Quota-*`） |
| 503 | `AI_OCR_NO_ENGINE` | 没有任何启用的引擎 |
| 503 | `AI_OCR_SITE_QUOTA_EXHAUSTED` | 所有启用引擎的站点额度都满了 |

执行顺序（固定，不可调换）：鉴权 → mime → 幂等检查 → 频控 → 用户额度 `peek` → 选引擎并扣站点额度 → 扣用户额度 → 入队。
「先扣额度再拒绝」是不允许的：引擎选不出来时用户额度一分不扣。

结果通过轮询 `GET /api/attachments/{oid}/ocr` 获取（前端间隔 2.5s，连续失败 3 次停表）。

### 2.2 附件侧入口

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/api/attachments/{oid}/ocr` | `admin` / `department` | 与 `/api/ai/ocr` **同一套计价**（频控 + 用户额度 + 站点额度）；202 新受理 / 200 已在处理 |
| GET | `/api/attachments/{oid}/ocr` | owner / `admin` / `department` | 结果：`{status, engine, engine_version, text, confidence, updated_at}` |

`status` 状态机：`pending → processing → done | failed`。`failed` 后可再次 POST 重试（会重新计价）。

### 2.3 引擎选择语义

1. 取所有 `enabled` 引擎，按 `priority ASC, name ASC` 排序。
2. 逐个调用 `site_consume(kind, 1)`（原子条件自增）；**第一个还有额度的胜出**，被拒的那次不扣费。
3. 选定即锁定：后台任务只用入队时的 `kind`/`name`，识别失败**不换引擎、不退额度**，状态置 `failed`。

生产推荐优先级：

| 引擎 | priority | 月额度 | 说明 |
|---|---|---|---|
| `tencent` | 90 | 1000 | 免费额度最先用 |
| `baidu` | 95 | 500 | `accurate_basic` |
| `rapidocr` | 100 | 1000000 | 本地不限量，但占 CPU |
| `zhipu` | 500 | 100000 | 免费视觉模型兜底 |
| `groq` | 900 | 1500 | 对比用 |

### 2.4 引擎能力矩阵

| kind | 凭证 | bbox | 逐行置信度 | 自动重试 |
|---|---|---|---|---|
| `tencent` | `secret_id` / `secret_key` / `region` | 有 | 有 | 无 |
| `aliyun` | `access_key_id` / `access_key_secret` | 有 | 有 | 无 |
| `baidu` | `api_key` / `secret_key` | 有 | **无** | 无（110/111 自动换 token） |
| `rapidocr` | 无需（必须留空） | 有 | 有 | 无 |
| `groq` | `api_key` | 有（schema 约束） | **无** | 无（429 → 直接报限流） |
| `zhipu` | `api_key` | **无** | **无** | 429 + 1302/1305 重试 3 次（2s/5s），1308 不重试 |

统一返回 `{text, lines:[{text, confidence, box}], engine, raw}`；无 bbox 的引擎 `box` 恒为 `null`。

---

## 3. 凭据管理 `/api/ai/keys`

三层作用域：`personal`（个人自付）、`shared`（平台共享）、`server`（服务端配置）。`api_key` 落库前 AES-256-GCM 加密，任何响应都不回明文或密文，只回 `key_hint`。

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/ai/keys` | admin | 列出可见凭据（含 `owned` 标记，不含密钥） |
| POST | `/api/ai/keys` | admin | 新建，201 |
| PATCH | `/api/ai/keys/{oid}` | admin + owner | 部分更新；`expires_at` 三态：不传=不改，传值=设置，`null`=清除 |
| DELETE | `/api/ai/keys/{oid}` | admin + owner | 204 |

创建请求体：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `scope` | string | 是 | `personal` / `shared` / `server` |
| `name` | string | 是 | 同作用域内唯一 |
| `base_url` | string | 是 | 上游地址（SSRF 校验在服务层） |
| `models` | string[] | 否 | 该 key 可用的模型名 |
| `api_key` | string | 是 | 8–512 字符 |
| `enabled` | bool | 否 | 默认 `true` |
| `headers` | object | 否 | 自定义头（接 `X-Api-Key` 这类非 OpenAI 形状的 token plan） |
| `api_path` | string | 否 | 覆盖默认路径（如 `/v1/messages`），必须是相对路径 |
| `expires_at` | string | 否 | ISO 8601；不传 = 永不过期 |

错误：`400 AI_KEY_INVALID` / `AI_KEY_BAD_EXPIRY`，`403 AI_KEY_FORBIDDEN`，`404 AI_KEY_NOT_FOUND`，`409 AI_KEY_DUPLICATE`。

---

## 4. OCR 引擎管理 `/api/ai/ocr-engines`

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/ai/ocr-engines` | admin | 含 disabled；无 `secret_cipher`，只有 `has_secret` 与 `key_hint` |
| POST | `/api/ai/ocr-engines` | admin | 201；`secret` 为**明文 dict**，仅本次请求存在 |
| PATCH | `/api/ai/ocr-engines/{oid}` | admin | 部分更新；**`kind` 不可改**（换类型等于换凭证形状，删行重建） |
| DELETE | `/api/ai/ocr-engines/{oid}` | admin | 204 |

引擎视图：`{oid, name, kind, config, key_hint, has_secret, enabled, priority, created_at, updated_at}`。

创建请求体：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `name` | string | 是 | 全局唯一 |
| `kind` | string | 是 | `tencent` / `aliyun` / `baidu` / `rapidocr` / `groq` / `zhipu` |
| `config` | object | 否 | 非敏感参数，见下表 |
| `secret` | object | 按 kind | 明文凭证，按 kind 校验必填项 |
| `enabled` | bool | 否 | 默认 `true` |
| `priority` | int | 否 | 0–999，默认 100，**升序先选** |

错误：`400 AI_OCR_ENGINE_BAD_KIND` / `AI_OCR_ENGINE_INVALID`，`404 AI_OCR_ENGINE_NOT_FOUND`，`409 AI_OCR_ENGINE_DUPLICATE`。

### 4.1 各 kind 的 secret / config

| kind | secret 必填 | config 可选（默认值） |
|---|---|---|
| `tencent` | `secret_id`, `secret_key` | `region`（secret 里也可传）, `language_type`（`zh`）, `timeout`（30s） |
| `aliyun` | `access_key_id`, `access_key_secret` | `endpoint`（`ocr-api.cn-hangzhou.aliyuncs.com`）, `timeout`（30s） |
| `baidu` | `api_key`, `secret_key` | `endpoint`（`https://aip.baidubce.com`）, `ocr_api`（`accurate_basic`）, `timeout`（30s） |
| `rapidocr` | **必须留空** | 线程数由环境变量 `AI_OCR_ORT_THREADS` 控制 |
| `groq` | `api_key` | `base_url`（`https://api.groq.com/openai/v1`）, `model`（`qwen/qwen3.8-27b`）, `reasoning_effort`（`none`）, `max_tokens`, `timeout`（30s） |
| `zhipu` | `api_key` | `base_url`（`https://open.bigmodel.cn/api/paas/v4`）, `model`（`glm-4.6v-flash`）, `max_tokens`（4096）, `temperature`（0.1）, `retry_attempts`（3）, `json_mode`（true）, `thinking`（`disabled`）, `timeout`（30s） |

> `glm-4.7-flash` 是纯文本模型，不能 OCR；视觉要用 `glm-4.6v-flash` 及同系列。

---

## 5. 额度 `/api/ai/quota`

两套互相独立的口径：

| 口径 | kind | 归属 | 默认上限 |
|---|---|---|---|
| 用户额度 | `ocr` / `chat` | 每个 `user_oid` | `config.AI_DEFAULT_OCR_MONTHLY_UNITS=100`、`AI_DEFAULT_CHAT_MONTHLY_UNITS=200000`；角色覆盖 `AI_ROLE_QUOTA`（admin 500/1,000,000，department 100/200,000） |
| 站点额度 | `ocr_<engine_kind>` | `SHARED_OWNER_OID` | `settings.AI_SITE_QUOTA[kind]` → `settings.AI_DEFAULT_SITE_OCR_MONTHLY_UNITS` → **1000** |

站点额度**不会**回落到用户默认的 100——那个数字是单人免费额度，站点额度是平台垫付总量，差一个数量级。

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/api/ai/quota` | 登录 | `{ocr:{period,used,limit}, chat:{...}}` |
| GET | `/api/ai/quota/{user_oid}` | admin | 同上（不校验用户是否存在） |
| PUT | `/api/ai/quota/{user_oid}` | admin | `{kind, limit_count}`，只改上限不动已用量 |
| POST | `/api/ai/quota/{user_oid}/reset` | admin | `{kind}`，`used` 归零，上限保留 |

- `period` 为 `YYYYMM`，按月自动开新行。
- 扣减是单条条件 UPDATE（`used + n <= limit_count`），原子防超卖。
- 错误：`400 AI_QUOTA_BAD_KIND` / `AI_QUOTA_BAD_LIMIT`。

> 注意（已知缺陷）：`PUT /quota/{user_oid}` 接受 `ocr_<kind>`，但写的是目标用户行而非站点行，会生成一条永不生效的孤儿记录。调站点额度目前只能改 `settings.AI_SITE_QUOTA`。

---

## 6. 调用流水 `/api/ai/logs`

| 方法 | 路径 | 权限 |
|---|---|---|
| GET | `/api/ai/logs` | admin |

| 参数 | 类型 | 说明 |
|---|---|---|
| `page` | int ≥1 | 默认 1 |
| `perpage` | int 1–100 | 默认 20 |
| `user_oid` | string | 按用户过滤 |
| `capability` | string | `ocr` / `chat` |
| `status` | string | `success` / `error` / `timeout` / `rejected` / `busy` |
| `key_scope` | string | `personal` / `shared` / `server` |
| `time_start` / `time_end` | epoch 秒 | 闭区间 |

响应 `data` 为行数组（含 `_id`、`oid`、`user_name`），`metadata.size` 为总数。
错误：`400 AI_LOGS_BAD_FILTER`。

保留期由定时任务清理（默认 90 天），没有手工删除端点。

---

## 7. 健康检查 `/api/ai/health`

`GET /api/ai/health`（admin），同时会**后台预热**本地引擎（幂等）。

```json
{
  "enabled": true,
  "local_engine_ready": true,
  "max_concurrency": 4,
  "providers": { },
  "routes": { },
  "cooldown_seconds": { },
  "models": ["..."],
  "quota_defaults": { },
  "credentials": {
    "personal": 0, "shared": 1, "server": 3,
    "shared_entries": [{ "name": "...", "base_url": "...", "models": [], "enabled": true }]
  }
}
```

`local_engine_ready` 只是一个布尔，不暴露路径、模型名或密钥材料。

---

## 8. OpenAI 兼容面

### 8.1 GET /api/ai/v1/models

```json
{ "object": "list", "data": [{ "id": "glm-4.6v-flash", "object": "model", "owned_by": "..." }] }
```

只列当前用户可见凭据里声明的模型。

### 8.2 POST /api/ai/v1/chat/completions

请求体按 OpenAI 规范透传给上游，网关只读 `model`、`messages`、`stream` 与 `provider`，其余原样转发。

| 字段 | 说明 |
|---|---|
| `model` | 必填，须在可见凭据的 `models` 里，否则 404 `model_not_found` |
| `messages` | 必填 |
| `stream` | `true` 时返回 `text/event-stream` |
| `provider` | **仅 admin 可用**，强制指定某条凭据/上游 |

错误：

| 状态 | code | 触发 |
|---|---|---|
| 403 | `AI_FORBIDDEN` | 角色无 AI 权限 |
| 404 | `model_not_found` | 没有任何可见凭据声明该模型 |
| 429 | `AI_RATE_LIMITED` | 超过 20 次/分钟 |
| 429 | `insufficient_quota` | 个人 `chat` 月额度耗尽 |
| 429 | `AI_BUSY` | 全局并发闸门已满 |
| 502 | `AI_UPSTREAM_AUTH` | 上游 401/403（凭证问题） |
| 502 | `AI_UPSTREAM_REJECTED` | 上游 4xx 拒绝请求 |
| 502 | `AI_UPSTREAM_RATE_LIMIT` | 上游 429 |
| 502 | `AI_UPSTREAM_ERROR` | 上游 5xx |
| 502 | `AI_CREDENTIAL_UNREADABLE` | 凭证解密失败 |
| 503 | `AI_DISABLED` / `AI_NO_PROVIDER` | 网关关闭 / 没有可用候选 |
| 504 | `AI_TIMEOUT` | 上游超时 |

### 8.3 SDK 接入

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://zvp.xiaobopisces.top/api/ai/v1",
    api_key="<平台登录 token>",
)
print(client.chat.completions.create(
    model="glm-4.6v-flash",
    messages=[{"role": "user", "content": "你好"}],
).choices[0].message.content)
```

`api_key` 就是平台 Bearer token，不是单独的 AI key——**没有按 key 限额、按模型授权的机制**。

### 8.4 上游重试与熔断

- 每个请求最多尝试 2 个候选上游（`MAX_PROVIDER_ATTEMPTS`）。
- 仅 `AI_UPSTREAM_RATE_LIMIT`、`AI_UPSTREAM_ERROR`、`AI_TIMEOUT` 会换下一个上游。
- `AI_NO_PROVIDER`、`AI_UPSTREAM_AUTH`、`AI_UPSTREAM_REJECTED` 直接失败，不换。
- 失败上游进入冷却（秒数见 `/health` 的 `cooldown_seconds`），冷却期内不再当选。
- 频控、熔断、凭据缓存都是**进程内**状态。

---

## 9. 错误码总表

| code | HTTP | 含义 |
|---|---|---|
| `AI_FORBIDDEN` | 403 | 角色无 AI 权限 |
| `AI_DISABLED` | 503 | 网关未启用 |
| `AI_RATE_LIMITED` | 429 | 本地频控超限 |
| `AI_QUOTA_EXCEEDED` / `insufficient_quota` | 429 | 用户月额度耗尽 |
| `AI_BUSY` | 429 | 并发闸门满 |
| `AI_NO_PROVIDER` | 503 | 无可用上游 |
| `AI_OCR_NO_ENGINE` | 503 | 无启用 OCR 引擎 |
| `AI_OCR_SITE_QUOTA_EXHAUSTED` | 503 | 全部 OCR 引擎站点额度耗尽 |
| `AI_UNSUPPORTED_MEDIA` | 400 | OCR 入队 mime 不支持 |
| `AI_TIMEOUT` | 504 | 上游超时 |
| `AI_UPSTREAM_AUTH` | 502 | 上游鉴权失败 |
| `AI_UPSTREAM_REJECTED` | 502 | 上游 4xx 拒绝 |
| `AI_UPSTREAM_RATE_LIMIT` | 502 | 上游限流 |
| `AI_UPSTREAM_ERROR` | 502 | 上游 5xx |
| `AI_CREDENTIAL_UNREADABLE` | 502 | 凭证解密失败 |
| `model_not_found` | 404 | 模型不可见 |
| `AI_INVALID_REQUEST` | 422 | 请求体校验失败（含 pydantic 数组 detail） |
| `AI_KEY_*` | 400/403/404/409 | 凭据相关 |
| `AI_OCR_ENGINE_*` | 400/404/409 | 引擎相关 |
| `AI_QUOTA_BAD_KIND` / `AI_QUOTA_BAD_LIMIT` | 400 | 额度参数非法 |
| `AI_LOGS_BAD_FILTER` | 400 | 流水过滤参数非法 |

前端映射在 `zvms4-frontend/src/api/ai/errors.ts` + `src/i18n/locales/ai.ts`；新增错误码必须两处同步。

---

## 10. 已知限制与缺口

| # | 缺口 | 影响 |
|---|---|---|
| 1 | OCR 选定引擎后失败**不回退** | 站点额度已扣、用户额度不退，一次失败白花一次上游调用 |
| 2 | 用户额度与站点额度不在同一事务 | 并发竞态下站点额度可能漏损（代码注释已承认） |
| 3 | `PUT /quota/{oid}` 可写 `ocr_*` 但落到用户行 | 管理员以为调了站点额度，实际是孤儿记录 |
| 4 | 频控 / 熔断 / 凭据缓存均为进程内 | 多 worker 下实际限额变成 N 倍，冷却状态各自为政 |
| 5 | `settings.AI_ROUTES` 是死配置 | 配了不生效，`/health` 却当生效项回显 |
| 6 | `AI_OCR_MAX_IMAGE_BYTES` 未接线 | 大图直接进 base64，无体积闸门 |
| 7 | OpenAI 面无独立 API key | 第三方接入必须拿用户 token，无法按 key 限额 |
| 8 | 审计写入失败静默 | `units` 与 `ai_quota.used` 可能对不上账 |
| 9 | `OcrRequest.provider` 空字段 | 客户端以为能指定引擎，实际被忽略 |
| 10 | 附件 OCR 只支持 jpeg/png/webp | PDF 附件无法识别 |

---

## 11. 运维速查

| 配置项 | 位置 | 作用 |
|---|---|---|
| `AI_ENABLED` | `settings` | 网关总开关 |
| `AI_ALLOWED_ROLES` | `config.py` | 可用角色，默认 `admin`/`department` |
| `AI_RATE_LIMIT_PER_MINUTE` | `config.py` | `{"ocr":10,"chat":20}` |
| `AI_DEFAULT_OCR_MONTHLY_UNITS` | `config.py` | 用户 OCR 默认 100 |
| `AI_DEFAULT_CHAT_MONTHLY_UNITS` | `config.py` | 用户对话默认 200000 |
| `AI_ROLE_QUOTA` | `config.py` | 角色覆盖额度 |
| `AI_SITE_QUOTA` | `settings.py`（生产专属） | 按 `ocr_<kind>` 覆盖站点额度 |
| `AI_DEFAULT_SITE_OCR_MONTHLY_UNITS` | `settings` | 站点额度兜底 1000 |
| `AI_CREDENTIALS_CACHE_TTL` | `config.py` | 凭据缓存 30s |
| `AI_OCR_ORT_THREADS` | 环境变量 | rapidocr 线程数 |
| `AI_CALL_LOGS_RETENTION_DAYS` | `config.py` | 流水保留 90 天 |

相关表：`ai_credentials`、`ai_ocr_engines`、`ai_quota`、`ai_call_logs`、`file_ocr`（迁移 `V024`–`V029`）。
