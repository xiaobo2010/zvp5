# AI 网关 API

适用代码：`zvms4-backend-python` @ `dce53af`（2026-10-29）
挂载点：`main.py` → `app.include_router(ai_router.router, prefix="/api/ai")`
+ `app.include_router(ai_api_keys_router.router, prefix="/api/ai/api-keys")`
+ `app.include_router(ai_agent_router.router, prefix="/api/ai/agent")`
+ `app.include_router(ai_conversations_router.router, prefix="/api/ai")`
+ `app.include_router(ai_scenes_router.router, prefix="/api/ai")`

网关有四件事：**LLM 对话**（OpenAI 兼容面）、**OCR**（多引擎池）、**ZVAgent 会话/工具**（工具白名单 + 写操作人工确认）、**运维面**（凭据 / 引擎 / 额度 / 流水 / 健康）。
OCR 还有两条旁路入口：附件侧 `POST /api/attachments/{oid}/ocr`，以及站点文件预览用的 `POST /api/ai/ocr/file`（见 2.5），两者与 `/api/ai/ocr` 共用同一套计价。
AI 产品对外统一称 **ZVAgent**；「会话模式 `chat`」已下线，`/v1/chat/completions` 仍保留为**无状态**兼容面（见 8.2）。

---

## 1. 通用约定

### 1.1 鉴权

两种凭据，前缀不同、作用面不同：

| 凭据 | 形态 | 作用面 |
|---|---|---|
| 平台登录 token（`zvp_` / JWT） | `Authorization: Bearer <jwt>` | **全部** `/api/ai/*` 与 `/api/ai/v1/*` |
| API key（`zvk_...`） | `Authorization: Bearer zvk_...` | **仅** `/v1/models`、`/v1/chat/completions` |

API key 详见 §8；它**不能**打开管理面（`/keys`、`/quota`、`/logs`、`/health`）——
泄漏一把 key 的爆炸半径就是「以本人身份对话、花本人的月额度」。

| 门禁 | 判定 |
|---|---|
| `AI_FORBIDDEN` | 角色不在 `config.AI_ALLOWED_ROLES`（默认 `admin`、`department`） |
| `admin` | 凭据 / 引擎 / 额度管理 / 流水 / 健康 / API key 管理 |
| `department`（学生会） | OCR、对话、查自己的额度、管自己的 API key |
| 普通用户 | 无 AI 对话权限（API key 只能由本人签发/吊销） |

个人凭据（`scope=personal`）的调用**不占**平台月度额度（自付上游成本）。

### 1.2 两套响应协议

**zvp 面**（`/ocr`、`/keys`、`/logs`、`/ocr-engines`、`/quota`、`/health`、`/api-keys`）：

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
| 202 | 已受理，后台异步执行（OCR 入队 / agent run / 只读场景 run） |
| 204 | 删除成功，空体 |
| 400 | 参数 / 类型不合法（`AI_*` 错误码） |
| 403 | 角色不足、没有该附件的读权限，或内容安全拒绝（`AI_SAFETY_REJECTED`） |
| 404 | 资源不存在 |
| 409 | 唯一性冲突（重名 / 会话模型锁定 / 活跃 run） |
| 422 | 请求语义不成立（如未解锁 agent、内容安全拒绝的 run 提交） |
| 429 | 频控或额度耗尽 |
| 502 | 上游故障（鉴权失败 / 拒绝 / 5xx / 不可读凭证） |
| 503 | 网关不可用（未启用 / 无可用上游 / 无可用 OCR 引擎 / 站点额度全满） |
| 504 | 上游超时 |

### 1.5 管理写操作的密码再认证

以下端点要求请求体带 `verification_token`（`POST /api/users/auth` 的 `purpose=verify` 用密码换出的 **5 分钟**短令牌，scope=`temporary_token`）。缺失、过期、伪造、非本人一律 403，`detail` 为 `需要密码验证后才能继续`：

| 端点 | 方法 |
|---|---|
| `/api/ai/keys`、`/api/ai/keys/{oid}` | POST / PATCH / DELETE |
| `/api/ai/api-keys`、`/api/ai/api-keys/{oid}` | POST / PATCH / DELETE |
| `/api/v3/ban/rules`、`/api/v3/ban/rules/{code}` | POST / PUT / DELETE（DELETE 走 body） |
| `/api/v3/ban/devices`、`/api/v3/ban/devices/{xuehai_id}`、`.../reset-gradient` | POST / DELETE / POST（均走 body） |
| `/api/v3/notifications`、`/{oid}`、`/{oid}/rollback` | POST / PUT / DELETE / POST |
| `/api/changelog`、`/api/changelog/{oid}` | POST / PUT / DELETE |
| `/api/system/settings` | PUT |

再认证令牌与登录令牌走同一条 `_authenticate` 判定（签名、过期、scope、封禁、`sid`），只把失败统一翻成 403——前端 axios 把 401 当「登录过期」会清会话并跳登录页，密码框里打错一次就把人踢出去属于错误语义。

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
| `provider` | string \| null | 否 | **引擎 kind**（`tencent` / `baidu` / `rapidocr` / ...，不是模型名）。有值时只在该 kind 的引擎里选；**仅 admin 可用**，且不减免站点额度。非 admin 传了 → 403 `AI_FORBIDDEN`，拼错的 kind → 400 `AI_OCR_ENGINE_BAD_KIND` |

> 网关不提供图片直传：OCR 只处理已落库的附件，否则就成了绕过附件配额与内容审核的旁路。
> 单图体积上限 20MB（`config.AI_OCR_MAX_IMAGE_BYTES`，`settings.AI_OCR_MAX_IMAGE_BYTES` 可覆盖）：入队按元数据判一次，后台识别按真实字节再判一次。

响应 `data`：

| 状态 | HTTP / body code | `data` |
|---|---|---|
| 新受理 | 202 / 202 | `{status:"pending", attachment_oid, engine}` |
| 已在处理 | 200 / 200 | `{status:"processing", attachment_oid}`（不重复计价） |

错误：

| 状态 | `detail` | 触发 |
|---|---|---|
| 403 | `AI_FORBIDDEN` | 角色无 AI 权限；或非 admin 传了 `provider` |
| 404 | `Attachment not found` | oid 不存在 |
| 403 | `Permission denied` | 无权读该附件 |
| 400 | `AI_UNSUPPORTED_MEDIA` | mime 不在 `image/jpeg`、`image/png`、`image/webp` |
| 400 | `AI_OCR_IMAGE_TOO_LARGE` | 图片超过单图体积上限（默认 20MB） |
| 400 | `AI_OCR_ENGINE_BAD_KIND` | `provider` 不是受支持的引擎 kind |
| 429 | `AI_RATE_LIMITED` | 超过 10 次/分钟（带 `Retry-After`） |
| 429 | `AI_QUOTA_EXCEEDED` | 个人 `ocr` 月额度耗尽（带 `X-AI-Quota-*`） |
| 503 | `AI_OCR_NO_ENGINE` | 没有任何启用的引擎（含 `provider` 指定的 kind 没配） |
| 503 | `AI_OCR_SITE_QUOTA_EXHAUSTED` | 所有启用引擎的站点额度都满了 |

执行顺序（固定，不可调换）：鉴权 → mime → 体积 → 幂等检查 → 频控 → 用户额度 `peek` → 选引擎并扣站点额度 → 扣用户额度 → 入队。
「先扣额度再拒绝」是不允许的：引擎选不出来时用户额度一分不扣；用户额度在竞态中扣失败时，刚占的站点额度会被**退回**（见 2.3）。

审计（`AI_CALL_LOGS`）：入队成功写一行（`units=1`、`purpose='ocr'`、`engine_kind`、`attachment_oid`）；
之后每一次引擎尝试各写一行（`units=0`、`attempt_no` 从 1 递增、`engine_kind` 为实际尝试的引擎类型）；
拒绝路径（403 无权限 / 429 频控 / 429 额度 / 503 无引擎或站点额度满）也各写一行 `status='rejected'`。
同一次识别的所有**尝试**共用一个 `request_id`，按它就能把一条链查全（见 §6）。

结果通过轮询 `GET /api/attachments/{oid}/ocr` 获取（前端间隔 2.5s，连续失败 3 次停表）。

### 2.2 附件侧入口

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/api/attachments/{oid}/ocr` | `admin` / `department` | 与 `/api/ai/ocr` **同一套计价**（频控 + 用户额度 + 站点额度）；202 新受理 / 200 已在处理 |
| GET | `/api/attachments/{oid}/ocr` | owner / `admin` / `department` | 结果：`{status, engine, engine_version, text, confidence, updated_at}` |

`status` 状态机：`pending → processing → done | failed`。`failed` 后可再次 POST 重试（会重新计价）。

### 2.3 引擎选择语义

1. 取所有 `enabled` 引擎，按 `priority ASC, name ASC` 排序（`provider` 有值时先按 kind 过滤）。
2. 逐个调用 `site_consume(kind, 1)`（原子条件自增）；**第一个还有额度的胜出**，被拒的那次不扣费。
3. 入队选中的引擎是后台任务的**第一次尝试**；识别失败时按同一套顺序回退到下一个候选（`name` 已试过即跳过），最多共 **3 次尝试**（选中那个 + 2 次回退）。每次回退都按该引擎重新 `site_consume` 一份，`file_ocr.engine` 记**实际成功**的那个。
4. **失败不退额度**：上游可能已经计费。唯一会退的是「站点额度已扣、用户额度竞态失败」这条路径（`quota.site_refund`，单条条件 UPDATE，`used` 不足时归零）。
5. 尝试全部失败才落 `failed`（`ocr_retry` +1，可再次 POST 重试并重新计价）。超限体积、附件内容丢失这类**与引擎无关**的失败直接落 `failed`，不再换引擎白花额度。

每次尝试都会写一条 `ai_call_logs`：`model`/`provider` 是引擎名，失败带 `error_code=AI_OCR_ENGINE_ERROR`，`units=0`——计费那一笔在入队时已经记过（`units=1`），逐次流水只记「用了谁、成没成」。逐次行还带 `attempt_no`（第几次尝试）、`engine_kind`（引擎类型）与 `attachment_oid`，同一次识别的所有尝试共用一个 `request_id`。

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

### 2.5 POST /api/ai/ocr/file —— 站点文件路径识别

文件管理器的图片预览需要一个「点一下识别」的入口，而预览图片没有附件 oid。本端点接受**站点内绝对路径**，同步返回识别文本；它不是免费通道，计费与附件 OCR 完全同源。

```http
POST /api/ai/ocr/file
Content-Type: application/json

{ "path": "/www/zvms4/uploads/2026/order.png", "provider": "tencent" }
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `path` | string | 是 | 1–4096 字符，绝对路径；经 `services/file_ops.resolve_path` 解析（根目录 jail + 凭据名拦截） |
| `provider` | string \| null | 否 | 引擎 kind；语义与 `/api/ai/ocr` 的 `provider` 相同（仅 admin，且不减免站点额度） |

- **权限**：严格 `admin` + 功能开关 `ai.ocr`。
- **类型**：按扩展名收敛到 `jpg` / `jpeg` / `png` / `webp`（大小写不敏感）；其它 → 400 `AI_UNSUPPORTED_MEDIA`。
- **体积**：与附件 OCR 同一个上限（默认 20MB，`AI_OCR_MAX_IMAGE_BYTES` 可覆盖），超限 400 `AI_OCR_IMAGE_TOO_LARGE`。
- **执行**：同步识别（不落 `file_ocr`、不产生附件配额），返回 `{"status":"ok","code":200,"data":{"text":"..."}}`。
- **路径 jail**：越界 → 403 `FILES_ROOT_FORBIDDEN`（根目录只允许 `/www/zvms4` 与 `/home`，见 `docs/FILES_API_DESIGN.md` §13）；未命中路径也走同一套错误翻译。
- **计价与审计**：频控（`ocr`）+ 用户 `ocr` 月额度 + 站点额度 + 每次尝试的 `ai_call_logs`，与 §2.1/§2.3 同一套规则；成功另写一条 `APP_LOGS`（`type=ai_ocr_enqueue`）。

| 状态 | `detail` | 触发 |
|---|---|---|
| 400 | `AI_UNSUPPORTED_MEDIA` | 扩展名不在白名单 |
| 400 | `AI_OCR_IMAGE_TOO_LARGE` | 文件超过单图上限 |
| 403 | `AI_FORBIDDEN` | 非 admin / 未开通 `ai.ocr` |
| 403 | `FILES_ROOT_FORBIDDEN` | 路径不在允许的根目录内 |
| 404 | `AI_OCR_FILE_UNREADABLE` | 文件不存在或不可读（`FileOpError` 无专用码时的兜底） |
| 429 | `AI_RATE_LIMITED` / `AI_QUOTA_EXCEEDED` | 频控 / 月额度 |
| 503 | `AI_OCR_NO_ENGINE` / `AI_OCR_SITE_QUOTA_EXHAUSTED` | 引擎或站点额度不可用 |

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

### 3.1 渠道（shared）

`shared` 凭据就是「渠道」：平台出资、所有登录用户可见、按发起人的月额度计费（`scope=personal` 才不计费）。渠道不新增 provider 类型——`kind` 仍是 `openai_compat`，`base_url` 指向上游兼容基址即可。

| 约束 | 值 | 原因 |
|---|---|---|
| 单条凭据模型上限 | 20（`credentials._MODELS_MAX`） | 与 `create_entry` 校验同一口径 |
| 模型来源 | 只用 `settings.AI_SILICONFLOW_FREE_MODELS` 里的免费模型 | 渠道对全员开放，付费模型的账单直接落到平台 |
| 白名单为空 | 拒绝上线（脚本退出码 2） | 不回落「上游全部模型」 |
| 流式 usage | `api.siliconflow.cn` 已在 `AI_STREAM_USAGE_HOSTS` 默认值内 | 否则流式调用 token 列恒为 NULL，只能估算 |
| `prompt_cache_key` | **不注入** | SiliconFlow 不认该字段，加了只是未知字段 |

运维命令（在后端根目录，`--dry-run` 不写库、不打印明文 key）：

```bash
# 免费清单先配进 settings.py：AI_SILICONFLOW_FREE_MODELS = ["...", "..."]
.venv/bin/python misc/seed_channel.py --dry-run --free-only
.venv/bin/python misc/seed_channel.py --free-only --key-file ~/.config/zvms/siliconflow.env
```

脚本行为：取 `--key-file` 里的 `SILICONFLOW_API_KEY`（或同名环境变量）→ `GET {base_url}/models` → 与免费白名单取**交集**（只收窄，不扩集）→ 同名 shared 存在则走 `update_entry` 轮换 key/models，否则 `create_entry` 新建。输出只有动作、`key_hint` 与模型清单，**不含明文 key**。换 key 就是重跑同一条命令；想立刻下线渠道就把凭据 `enabled=false` 或删除，白名单改动则重跑脚本刷新 `models`。

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
- 扣减是单条条件 UPDATE（`used + n <= limit_count`），原子防超卖；补偿退款也是单条 UPDATE（`used - n < 0` 时归零）。
- `kind` 为 `ocr_<engine>` 时写的**是站点行**（`SHARED_OWNER_OID` + 该站点 kind），返回的 `kind` 仍是站点 kind；`PUT` 与 `POST .../reset` 都是这个规则。所以 `PUT /quota/{user_oid}` 可以直接调站点额度，`GET /quota/{user_oid}` 依然只回这个人的 `ocr`/`chat`。
- 错误：`400 AI_QUOTA_BAD_KIND` / `AI_QUOTA_BAD_LIMIT`。

---

## 6. 调用流水 `/api/ai/logs`

| 方法 | 路径 | 权限 |
|---|---|---|
| GET | `/api/ai/logs` | admin |
| GET | `/api/ai/logs/stats` | admin |
| GET | `/api/ai/prompts` | admin（见 6.8） |
| GET | `/api/ai/prompts/{oid}` | admin（读正文会写访问审计） |

> **读预算**：`GET /api/ai/logs` 与 `GET /api/ai/prompts` 属于「重查询」，与 `GET /api/logs`、`GET /api/exports/{id}/file` 共用每用户 60 次/分钟的滑动窗口（`util/read_throttle.py`）。超限返回 429 且 `detail={"code":"READ_RATE_LIMITED","message":"查询过于频繁，请稍后再试"}`，并带 `Retry-After`；管理员与普通用户同一档位。

### 6.1 GET /api/ai/logs —— 明细

| 参数 | 类型 | 说明 |
|---|---|---|
| `page` | int ≥1 | 默认 1 |
| `perpage` | int 1–100 | 默认 20 |
| `user_oid` | string | 按用户过滤（校验 oid 形状，非法回 400） |
| `capability` | string | `ocr` / `chat` |
| `status` | string | `success` / `error` / `timeout` / `rejected` / `busy` |
| `key_scope` | string | `personal` / `shared` / `server` |
| `request_id` | string | 精确匹配：一次请求的所有尝试共用同一个编号（响应头 `X-AI-Request-Id`） |
| `provider` | string | 精确匹配服务商/引擎名 |
| `model` | string | 精确匹配模型名 |
| `purpose` | string | 精确匹配场景（`chat` / `markdown-assistant` / `ocr`） |
| `time_start` / `time_end` | epoch 秒 | 闭区间 |

响应 `data` 为行数组，`metadata.size` 为总数。错误：`400 AI_LOGS_BAD_FILTER`（枚举类
过滤值非法）、`400 "Invalid Object ID"`（`user_oid` 形状不对）。

`_id` 与 `oid` 同值（前者是仓储行形状，后者是前端 `row-key`），另加 `user_name` 富化
（查不到用户时回「未知用户」）。

### 6.2 行字段（`AI_CALL_LOGS` 的实际列，迁移 `V024` + `V025` + `V031`）

| 列 | 类型 | 含义 |
|---|---|---|
| `oid` | VARCHAR2(24) | 主键（小写 hex） |
| `user_oid` | VARCHAR2(24) | 调用者；**不建外键**（审计要活过账号删除） |
| `capability` | VARCHAR2(16) | `ocr` / `chat` |
| `endpoint` | VARCHAR2(40) | 网关入口 |
| `provider` | VARCHAR2(40) | 服务商名 / OCR 引擎名 |
| `model` | VARCHAR2(80) | 模型名 / OCR 引擎名 |
| `status` | VARCHAR2(16) | `success` / `error` / `timeout` / `rejected` / `busy` |
| `http_status` | NUMBER(3) | 返回给客户端的状态码（流式失败为 NULL） |
| `error_code` | VARCHAR2(40) | 稳定错误码 |
| `latency_ms` | NUMBER(10) | 总耗时 |
| `units` | NUMBER(10) | 这次调用消耗的额度单位（见 6.4） |
| `input_bytes` | NUMBER(10) | OCR：附件字节数 |
| `request_id` | VARCHAR2(64) | 请求编号；同一次请求的所有尝试共用 |
| `key_scope` | VARCHAR2(16) | `personal` / `shared` / `server`；NULL = 未知 |
| `prompt_tokens` | NUMBER(10) | 上游 usage 的输入 token；NULL = 上游没报 |
| `completion_tokens` | NUMBER(10) | 上游 usage 的输出 token；NULL = 上游没报 |
| `total_tokens` | NUMBER(10) | 上游 usage 的合计；NULL = 上游没报 |
| `attempt_no` | NUMBER(3) | 同一次请求里的第几次尝试（1 起）；NULL = 不是某次上游尝试（门禁直接拒） |
| `purpose` | VARCHAR2(24) | 场景来源（开放集合，见 6.5） |
| `first_token_ms` | NUMBER(10) | 流式首块到达耗时；非流式 NULL |
| `engine_kind` | VARCHAR2(24) | OCR 引擎类型（`tencent`/`aliyun`/`rapidocr`/`baidu`/`groq`/`zhipu`） |
| `attachment_oid` | VARCHAR2(24) | OCR 针对的附件；无外键（附件删了事实仍成立） |
| `round_no` | NUMBER(3) | agent 一次 run 的第几轮（1 起）；NULL = 非 agent 轮次 |
| `roles_snapshot` | VARCHAR2(64) | 调用时的角色快照（逗号连接）；NULL = 未采集 |
| `created_at` | TIMESTAMP | 写入时刻（naive 本地时间） |

**正文（prompt / completion）一律不落库**——这是合规决定，不是缺口。表里只有元数据，
字段命名对齐 OTel GenAI 语义约定的子集（当自有 schema 管理，不承诺外部兼容）。

新增列全部 nullable、无默认值：存量行留 NULL。**NULL 不是 0**——0 是一个真实的 token
数，会把「不知道」伪装成观测值。

### 6.3 不变式：token 为 NULL ⟺ `units` 是估算值

| 路径 | token 三列 | `units` |
|---|---|---|
| 非流式，上游回 usage | 实测值 | `max(1, ceil(total_tokens / 1000))` |
| 非流式，上游没回 usage | NULL | `1`（至少记一次） |
| 流式，拿到 usage 帧 | 实测值 | 同上 |
| 流式，没拿到 usage 帧 | NULL | `max(1, ceil(prompt_chars/4 + completion_chars/4))` |
| OCR（入队/逐次尝试/拒绝） | NULL | 入队 `1`，其余 `0` |

读这份数据时只有一条规则：**token 全 NULL 的行，它的 `units` 是估算值**。

> 已知差异（本轮刻意不改）：流式估算口径是「字符数/4」，量纲接近 **token 个数**；而实测
> 口径是「token/1000」。两者相差约 1000 倍，因此**估算行与实测行的 `units` 不能直接相加
> 或横比**。要统一需要一个 `units_source` 标志列 + 一次数据回填，属于单独的改造。

### 6.4 `units` 的语义

`units` 是**这次调用扣掉的额度单位**（OCR 页数 / chat 次数），与 `AI_QUOTA.USED` 的增量
同源；两边都留下才能对账。个人 key（`key_scope='personal'`）由管理员自付，不扣平台额度，
但**仍然写 `units`**——它记的是「这次调用值多少」，不是「平台付了多少」，后者由
`key_scope` 区分。扣费失败（`_charge` 的写回异常）只记 WARNING，流水照写：审计回答的是
「上游给了什么」，不是「额度表成功了吗」。

### 6.5 `purpose` 取值

| 来源 | 值 |
|---|---|
| 前端对话页 `ZAiChat` | `chat`（请求头 `X-AI-Scene: chat`） |
| 前端 markdown 助手 `ZMarkdownAiDialog` | `markdown-assistant`（`X-AI-Scene: markdown-assistant`） |
| OCR（入队 / 逐次尝试 / 拒绝） | `ocr` |
| agent（`/api/ai/agent/run` 的每一轮） | `agent` |
| 第三方 SDK / curl | NULL（未知来源） |

`X-AI-Scene` 只影响审计：缺失、超长或含非法字符都不会拒绝请求（后端清洗成
`[a-z0-9_-]` 并截到 24 字符）。场景是**开放集合**，加新场景不需要 DDL。

### 6.6 GET /api/ai/logs/stats —— 聚合

| 参数 | 类型 | 说明 |
|---|---|---|
| `days` | int 1–90 | 默认 7；窗口 =「今天零点往前 `days-1` 天」到今天，按本地自然日切 |

```json
{
  "days": 7,
  "since": "2026-10-18 00:00:00",
  "daily": [
    { "date": "2026-10-24", "calls": 12, "errors": 1,
      "prompt_tokens": 3000, "completion_tokens": 900, "total_tokens": 3900,
      "avg_latency_ms": 820 }
  ],
  "by_provider": [{ "provider": "team", "calls": 9, "errors": 0, "units": 12, "total_tokens": 3900 }],
  "by_model":    [{ "model": "gpt-x",  "calls": 9, "errors": 0, "units": 12, "total_tokens": 3900 }],
  "by_purpose":  [{ "purpose": "chat", "calls": 7, "errors": 1, "units": 10, "total_tokens": 3900 }],
  "by_user": [
    { "user_oid": "000000000000000000000003", "name": "张三", "calls": 7, "errors": 1,
      "total_tokens": 3900, "units": 10, "avg_latency_ms": 760 }
  ],
  "by_status": [
    { "status": "success",  "calls": 11 },
    { "status": "rejected", "calls": 1 }
  ],
  "cache": { "calls": 12, "cached_calls": 4, "prompt_tokens": 3000,
             "cache_read_tokens": 1200, "cache_write_tokens": 0, "hit_rate": 0.4 }
}
```

- `errors` = 所有 `status != 'success'` 的行（`rejected` / `busy` 也算：运营关心「有多少次
  没成」，上游挂了还是我们挡了由 `status` 再看）。
- token 合计为 `0` 表示窗口内**没有任何观测值**（与行级 NULL 的「未知」不同）。
- 分组小计最多 50 组，按调用数降序；`provider` / `model` / `purpose` / `status` 为 NULL
  的行归到 `null` 桶（未知来源）。
- `by_user` 是「谁在花钱」：top 50，`name` 由 `LEFT JOIN USERS` 取显示名，账号已删/查不到
  时**回落 oid 字符串**（审计行要活过账号删除，用「未知用户」盖掉就再也对不上了）。
  `avg_latency_ms` 为窗口内均值，没有观测时为 `null`。
- `by_status` 只回 `status` + `calls`：`errors` 回答「有多少次没成」，这一层回答「是谁挡的」。
- 所有部分来自同一个时间窗口：分开查会让「总数」与「明细合计」跨分钟对不上。
- 窗口只按时间切，不叠用户/模型过滤——细分查询用 `/api/ai/logs` 的过滤条件。

### 6.7 保留期

定时任务清理，默认 90 天（`AI_CALL_LOGS_RETENTION_DAYS`），没有手工删除端点；统计窗口
上限与之对齐。`request_id` 有索引，按编号追一条链不会全表扫。

同一轮日清还会处理两张审计表：`AI_AGENT_EVENTS`（与调用流水同窗，工具事件和流水是同一次
run 的两面）与 `AI_PROMPT_AUDIT`（`AI_PROMPT_RETENTION_DAYS`，默认 30 天，见 6.8）。

### 6.8 受控的 prompt 留存与行为监控

`AI_CALL_LOGS` 的「零正文」原则不变；`AI_PROMPT_AUDIT` 是**唯一**的正文例外，只为一件事
存在：出现安全事件 / 申诉 / 质量评估时，能还原学生会（`department`）的对话问了什么。

| 事项 | 口径 |
|---|---|
| 捕获条件 | 调用者 `per` 与 `AI_PROMPT_CAPTURE_ROLES`（默认 `("department",)`）相交；覆盖 `chat` / `markdown-assistant` / `agent` 三种场景（OCR 没有 prompt） |
| 捕获内容 | `messages` 中**最后一条** `role=user` 的正文（多轮对话的每一轮请求各留一条，不重复存历史轮次）；run 收尾时回填 `ANSWER_TEXT`（最终 assistant 文本）与 `TOOL_CALLS`（工具调用摘要 JSON 数组，只存 `name/args_summary/result_summary/status`，不存参数原文） |
| 结果标记 | `REFUSED` / `WARNED`（`NUMBER(1)`，默认 0）：内容安全拒绝与警告都会标在留存行上，不必回查 `AI_CALL_LOGS` 的错误码 |
| 脱敏 | 18 位身份证（含 `X` 校验位）→ 前 3 位 + `*`×11 + 后 4 位；11 位手机号（`1[3-9]` 开头）→ 前 3 位 + `*`×6 + 后 2 位。正则在服务端，先脱敏后截断 |
| 上限 / 保留 | 单条截断至 `AI_PROMPT_MAX_CHARS`（默认 8000 字符）；保留 `AI_PROMPT_RETENTION_DAYS`（默认 30 天） |
| 写入失败 | 只记一条 **不含正文** 的 WARNING，绝不影响主请求 |
| 读取 | 只有 `GET /api/ai/prompts`（元数据）与 `GET /api/ai/prompts/{oid}`（正文 + `answer_text` / `tool_calls` / `refused` / `warned`）；后者每次读取写一条 `APP_LOGS`（`type=ai_prompt_access`、`level=info`，随日志保留期清理） |

`AI_PROMPT_CAPTURE_ROLES = ()` 表示关闭正文留存（空集合是显式关闭，不是配置写坏）。

**审阅端点**：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/ai/prompts?user_oid=&days=&page=&perpage=` | admin。**只回元数据**：`oid`、`user_oid`、`user_name`、`purpose`、`roles_snapshot`、`created_at`、`prompt_chars`（库里 `LENGTH` 算，不回正文）。`days` 1–90 默认 7（滚动窗口），`page` ≥1，`perpage` 1–100 |
| GET | `/api/ai/prompts/{oid}` | admin。回完整正文（含 `request_id`）与回填的 `answer_text` / `tool_calls` / `refused` / `warned`；每次调用写一条访问审计（目标 oid + 目标 user_oid，**不记正文**）。oid 形状非法或不存在一律 `404 AI_PROMPT_NOT_FOUND` |

**`ai_chat_burst` 风控规则**（种子随 `V033` 落库，只告警不封禁）：

- 证据：`AI_CALL_LOGS` 里该用户窗口内的 `capability='chat'` 行数（含 `rejected` / `busy`；
  agent 的每一轮也是一行 chat）。默认阈值 80 次 / 1 小时 / `mode=alert`。
- 触发点：chat 请求完成处（成功或扣费后）与 agent run 完成处各评估一次，60 秒
  `(trigger, user)` 节流兜底；规则可在 `/api/v3/ban/rules` 里调阈值或切 `ban`。
- `department` 在自动封禁豁免名单里（`NO_AUTO_BAN_ROLES`）：即使切成 `ban` 也只记证据与告警，
  不改现有语义。


---

## 7. 健康检查 `/api/ai/health`

`GET /api/ai/health`（admin），同时会**后台预热**本地引擎（幂等）。

```json
{
  "enabled": true,
  "local_engine_ready": true,
  "max_concurrency": 4,
  "worker_count": 1,
  "rate_limit_per_minute": { "ocr": 10, "chat": 20 },
  "providers": { },
  "cooldown_seconds": { },
  "cooldown_scope": "process",
  "models": ["..."],
  "quota_defaults": { },
  "credentials": {
    "personal": 0, "shared": 1, "server": 3,
    "shared_entries": [{ "name": "...", "base_url": "...", "models": [], "enabled": true }]
  }
}
```

| 字段 | 含义 |
|---|---|
| `worker_count` | 部署的 worker 进程数（`settings.AI_WORKER_COUNT` → `config.AI_WORKER_COUNT` → 环境变量 `WEB_CONCURRENCY` / `UVICORN_WORKERS` → 1） |
| `rate_limit_per_minute` | **每进程**的实际阈值 = `ceil(全局阈值 / worker_count)`（至少 1）；全局真实上限 ≈ 阈值 × `worker_count` |
| `cooldown_scope` | 恒为 `"process"`：熔断状态是进程内的，多 worker 各熔各的 |

`local_engine_ready` 只是一个布尔，不暴露路径、模型名或密钥材料。
`settings.AI_ROUTES` 已废弃并从响应里移除（它从来没生效过，回显只会误导运维）。
频控 / 熔断 / 凭据缓存 / API key 校验缓存都是**进程内**状态：`worker_count > 1` 时进程启动会打一条 WARNING。

---

## 8. OpenAI 兼容面

### 8.1 GET /api/ai/v1/models

```json
{ "object": "list", "data": [{ "id": "team/glm-4.6v-flash", "object": "model", "owned_by": "team", "context": 128000 }] }
```

只列当前用户可见凭据里声明的模型。`id` 是合格 ID `provider/model`；`context` 是该模型的上下文窗口（token），来自凭据 `MODELS_JSON` 的模型级配置——**没配就是 `null`**，不拿猜测值顶替。

模型清单支持两种写法：字符串数组（旧）与对象数组 `[{"id":"gpt-x","context":128000}]`。保存凭据时服务端 best-effort 请求一次上游 `/models` 补全缺失的 `context`，管理员手填的值优先；管理端也可直接编辑。

### 8.2 POST /api/ai/v1/chat/completions

请求体按 OpenAI 规范透传给上游，网关只读 `model`、`messages`、`stream` 与 `provider`，其余原样转发。

| 字段 | 说明 |
|---|---|
| `model` | 必填，须在可见凭据的 `models` 里，否则 404 `model_not_found` |
| `messages` | 必填 |
| `stream` | `true` 时返回 `text/event-stream` |
| `provider` | **仅 admin 可用**，强制指定某条凭据/上游 |

可选请求头 `X-AI-Scene`（如 `chat` / `markdown-assistant`）只是**审计元数据**：后端把它
落进 `AI_CALL_LOGS.PURPOSE`，不参与鉴权、额度或路由。缺失即记为 NULL。

**流式 usage（`stream_options.include_usage`）按上游白名单开启**：只有 `base_url` 的 host
命中白名单（默认 `api.openai.com` / `api.deepseek.com`）时，网关才在发给上游的请求体里补
`stream_options: {"include_usage": true}`；其余上游一律不带。理由是这个参数不在最早的
OpenAI 兼容面里，严格校验请求体的网关会直接 400——为了多拿一列 token 把流式对话打挂不
划算。白名单可用 `settings.AI_STREAM_USAGE_HOSTS` 追加（`["*"]` = 全部允许）。拿不到 usage
时 token 三列为 NULL、`units` 走字符估算（见 §6.3）。

错误：

| 状态 | code | 触发 |
|---|---|---|
| 401 | `AI_API_KEY_INVALID` | 用了 API key 但无效（不存在/已禁用/已过期/用户已删） |
| 403 | `AI_FORBIDDEN` | 角色无 AI 权限 |
| 403 | `AI_SAFETY_REJECTED` | 内容安全判定成立或判定不可用（fail-closed），固定文案「该请求未通过内容安全审核。」 |
| 403 | `USER_BANNED` | 账号处于封禁中（带 `X-Ban-Reason` / `X-Ban-Until`） |
| 404 | `model_not_found` | 没有任何可见凭据声明该模型 |
| 429 | `AI_RATE_LIMITED` | 用户维度超过 20 次/分钟，或单把 API key 超过 60 次/分钟 |
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
需要「按客户端限额 / 可单独吊销」的第三方接入，用 §9 的独立 API key。

### 8.4 上游重试与熔断

- 每个请求最多尝试 2 个候选上游（`MAX_PROVIDER_ATTEMPTS`）。
- 仅 `AI_UPSTREAM_RATE_LIMIT`、`AI_UPSTREAM_ERROR`、`AI_TIMEOUT` 会换下一个上游。
- `AI_NO_PROVIDER`、`AI_UPSTREAM_AUTH`、`AI_UPSTREAM_REJECTED` 直接失败，不换。
- 失败上游进入冷却（秒数见 `/health` 的 `cooldown_seconds`，作用域 `process`），冷却期内不再当选。
- 频控、熔断、凭据缓存、API key 校验缓存都是**进程内**状态（见 `/health` 的 `worker_count` / `rate_limit_per_minute`）。

### 8.5 内容安全（fail-closed）

请求在「角色/开关门禁之后、候选选择与频控/额度之前」过内容安全闸，因此被拒请求不花上游调用、不占分钟窗口、不扣额度。

1. **规则层**（`services/ai/moderation.py`）：命令式越狱/提示注入/凭据窃取等规则，纯函数、无 IO；只对 `department` 生效。
2. **灰区 LLM 判定**（`services/ai/safety_guard.py`）：温度 0、限 200 token、只判最后一条 user 文本；**判定不可用（无凭据/超时/5xx/坏 JSON）也拒绝本次回答**，但**不记违规、不封号**——没有判定结论就没有证据。
3. **对外唯一错误码** `AI_SAFETY_REJECTED` + 固定文案「该请求未通过内容安全审核。」：run 提交 422，`/v1/chat/completions` 403。不区分「警告」与「封禁」，避免攻击者按差异调参。
4. **判定成立**：记违规证据（`ban_violations`，5 分钟去重）+ 警告账本 + 按 `BAN_RULES.penalties` **首档立即封号**；重试同一份证据不会重复处罚，已在封禁中也只记新证据。
5. **admin 天然旁路**：管理员本来就掌握运维能力，审核只针对 `department`；每次旁路提交写一条 `APP_LOGS`（`type=ai_moderation_unrestricted`）。「管理员无限制模式」开关已整体下线。
6. 被拒的原文仍按 `AI_PROMPT_CAPTURE_ROLES` 受控留存，并在留存行标 `REFUSED=1`（发了警告再标 `WARNED=1`）。

---

## 9. 独立 API key `/api/ai/api-keys`

给 OpenAI 兼容面的第三方接入用：与平台登录 token 分开、可单独吊销、可单独限额。
**只在 `/v1/models` 与 `/v1/chat/completions` 生效**，管理面（`/keys`、`/quota`、
`/logs`、`/health`）仍然只认 JWT。

### 9.1 关键事实

| 项 | 说明 |
|---|---|
| token 形态 | `zvk_` + `token_urlsafe(32)`（约 51 字符）；平台登录 token 是 `zvp_`/JWT |
| 落库形态 | `sha256(token)` 的 hex，**明文只在创建响应里出现一次**；没有「再查看一次」的接口 |
| `key_hint` | 前 8 + 后 4（如 `zvk_ab12…wxyz`），只用于人眼识别 |
| 数量上限 | 每用户 10 把（超出 409 `AI_API_KEY_LIMIT`） |
| 额度 | 仍按 `user_oid` 计：同一用户的多把 key **共享**月额度 |
| 频控 | 用户维度沿用 `chat` 的 20 次/分钟；**另加**每把 key 60 次/分钟（`AI_API_KEY_RATE_LIMIT_PER_MINUTE`），两者都要过 |
| 吊销 | 删除即失效（同进程立即，其它 worker 最晚一个 30s 缓存 TTL） |
| 鉴权失败 | 不存在 / 已禁用 / 已过期 / 用户已删 一律 `401 AI_API_KEY_INVALID`（不区分，避免探测）；封禁是 `403 USER_BANNED` |

### 9.2 端点

| 方法 | 路径 | 权限 | 行为 |
|---|---|---|---|
| GET | `/api/ai/api-keys` | 登录 | 只列自己的；admin 可加 `?user_oid=` 看别人 |
| POST | `/api/ai/api-keys` | 登录 | `{name, expires_at?}` → 201，`data.token` = 明文（仅此一次） |
| PATCH | `/api/ai/api-keys/{oid}` | owner 或 admin | `{name?, enabled?, expires_at?}`；`expires_at` 三态同 `/keys` |
| DELETE | `/api/ai/api-keys/{oid}` | owner 或 admin | 204 |

视图字段：`{oid, name, key_hint, enabled, expires_at, last_used_at, created_at}`，
**永不回 `token` 或 `token_hash`**。`last_used_at` 是采样写（最多每 60 秒一次），
不是精确审计。

> **密码再认证**：`POST` / `PATCH` / `DELETE` 均要求 body 带 `verification_token`（由 `POST /api/users/auth` 的 `purpose=verify` 用密码换出的 5 分钟短令牌）。缺失、过期、伪造或非本人 → 403 `需要密码验证后才能继续`。同一规则见 §3（`/keys`）。

错误：`400 AI_API_KEY_BAD_NAME` / `AI_API_KEY_BAD_EXPIRY`、`401 AI_API_KEY_INVALID`、
`403 AI_FORBIDDEN`（不是 owner 也不是 admin）、`404 AI_API_KEY_NOT_FOUND`、
`409 AI_API_KEY_DUPLICATE` / `AI_API_KEY_LIMIT`。

### 9.3 使用

```python
from openai import OpenAI

client = OpenAI(
    base_url="https://zvp.xiaobopisces.top/api/ai/v1",
    api_key="zvk_...",   # 创建时拿到的明文
)
```

鉴权判定顺序：token 形状（`zvk_` 前缀）→ `sha256` 查表（30s 进程内缓存，CRUD 主动失效）
→ 存在 → `enabled` → `expires_at` → 用户存在 → 封禁 → 构造与 JWT 同形的 user
（`id` / `perm` / `per` / `class_id`，`per` 取**当前**角色，不是签发时的快照）。

---

## 10. 智能体 `/api/ai/agent`

服务端 agent 循环：模型按 OpenAI function-calling 协议要求调用工具，网关执行**只读**
工具、把**写**工具变成待确认提案，用户确认后才以**他自己的权限**执行。

| 方法 | 路径 | 权限 |
|---|---|---|
| POST | `/api/ai/agent/run` | 角色在 `AI_ALLOWED_ROLES` 内（默认 `admin` / `department`） |
| POST | `/api/ai/agent/proposals/{oid}/confirm` | 提案归属人 |
| POST | `/api/ai/agent/proposals/{oid}/reject` | 提案归属人 |

错误体与 `/v1/chat/completions` 同形（`{"error": {"message", "type", "code"}}`），并带
`X-AI-Request-Id`。

### 10.1 POST /api/ai/agent/run —— 跑一次会话

```json
{ "messages": [{ "role": "user", "content": "查一下图书馆志愿，把张三的时长改成 6 小时" }],
  "model": "gpt-x" }
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `messages` | 是 | 1–50 条；`role` 只能是 `system` / `user` / `assistant`（`tool` 由服务端产生，客户端伪造会被 400） |
| `model` | 否 | 必须在**该用户可见目录**里；省略时取目录里第一个模型 |

响应：

```json
{
  "request_id": "9f2c…",
  "content": "已生成待确认提案，请在页面上确认。",
  "events": [
    { "round_no": 1, "tool_name": "search_activities", "tool_kind": "read",
      "args_summary": "{\"query\":\"图书馆\"}", "result_summary": "…", "status": "executed" }
  ],
  "proposals": [
    { "oid": "3ab4…", "tool_name": "update_member_duration",
      "args_summary": "{\"activity_id\":\"…\"}", "expires_at": "2026-10-25 12:10:00" }
  ]
}
```

`events[].status` ∈ `executed` / `proposed` / `denied` / `failed`。被拒的工具（不在白名单、
无权限、参数非法）**不会中断会话**：网关把错误作为 tool 消息回给模型，让它自己纠偏。

### 10.2 工具清单（封闭集合）

读工具（8 个，直接执行）：

| 工具 | 参数 | 权限镜像 |
|---|---|---|
| `search_activities` | `query`、`limit`（1–20） | 登录即可（同 `GET /api/v2/activities`） |
| `list_activities` | `page`、`perpage`（1–100）、`activity_type` | 同上 |
| `get_activity` | `activity_id` | 同上（同 `GET /api/v2/activities/{id}`） |
| `activity_statistics` | `activity_id` | 团支书及以上（同统计端点） |
| `list_activity_members` | `activity_id`、`page`、`perpage`、`search` | 登录即可（同成员列表端点） |
| `user_volunteer_time` | `user_id`（缺省=自己） | 登录即可（同 `GET /api/v2/users/{id}/time`） |
| `user_activities` | `user_id`（缺省=自己）、`page` | 同上 |
| `list_groups` | `page`、`size` | 登录即可；权限组清单只对有管理权限者返回 |

写工具（2 个，**一律只生成提案，不直接执行**）：

| 工具 | 参数 | 权限镜像 |
|---|---|---|
| `update_member_duration` | `activity_id`、`member_id`、`duration`（小时，非负） | `admin` / `department`（同成员时长端点） |
| `set_member_status` | `activity_id`、`member_id`、`status` ∈ `effective`/`pending`/`refused` | `admin` / `department`（同成员审核端点） |

白名单以外的工具名一律拒绝（落 `denied` 事件）。工具的**能力由注册表限制**，不靠 prompt。

### 10.3 写操作 human-in-the-loop

1. 模型调用写工具 → 网关**不执行**，生成提案：`AI_AGENT_EVENTS` 一行 `status='proposed'`，
   记录 `args_hash`（落库参数文本的 sha256）、`args_summary`、`actor_oid`、10 分钟有效期；
2. 用户在平台上确认 → `POST /proposals/{oid}/confirm`；
3. 服务端**二次校验**：提案属于当前 actor、状态仍是 `proposed`、未过期、`args_hash`
   与落库参数一致、**当前用户**仍有该工具权限 → 才以该用户权限执行；
4. 终态：`confirmed` / `rejected` / `expired` / `denied` / `failed`。

| 端点 | 成功 | 失败语义 |
|---|---|---|
| `POST /proposals/{oid}/confirm` | `{"status":"confirmed","result_summary":"…"}` | `404` 不存在（含 oid 形状不对）、`403` 非本人提案 / 执行时无权限、`409` 已处理 / 参数哈希不符 / 工具已下线、`410` 已过期 |
| `POST /proposals/{oid}/reject` | `{"status":"rejected"}` | 同上（`410` 只对已过期提案） |

时长/状态落地后与 REST 端点一样重算该用户的时长缓存（`allow_cache=False`）；重算失败只
记 WARNING，不回滚已完成的写。

### 10.4 计费与审计口径

- **一次 `run` 的每一轮各记一条流水、各扣一次额度**：`AI_CALL_LOGS` 里 `capability='chat'`、
  `purpose='agent'`、`round_no` 递增、`request_id` 全轮共用。所以配额面板上「10 轮的一次
  run」看起来是 10 次调用——按真实上游成本记账，而不是按用户点了几个按钮。
- 每一轮都走与 `/v1/chat/completions` 相同的频控、额度预检、并发闸门与熔断；
  `AI_CALL_LOGS.roles_snapshot` 冻结该轮调用时的角色。
- 每次工具调用写一条 `AI_AGENT_EVENTS`（含被拒/过期/驳回的）；prompt 正文按受控规则
  留 `AI_PROMPT_AUDIT`（一次 run 只留入口那一份，见 6.8）。
- 成本闸：`AI_AGENT_MAX_ITERATIONS`（默认 10 轮）与 `AI_AGENT_TOKEN_BUDGET`（默认
  100000 token，含 prompt + completion）。任一触顶即终止，`content` 会说明原因。

---

## 11. 错误码总表

| code | HTTP | 含义 |
|---|---|---|
| `AI_FORBIDDEN` | 403 | 角色无 AI 权限 |
| `AI_SAFETY_REJECTED` | 422 / 403 | 内容安全拒绝（run 提交 / `/v1` 网关）；固定文案「该请求未通过内容安全审核。」 |
| `AI_CONVERSATION_MODEL_LOCKED` | 409 | 试图修改已锁定（建会话时确定）的模型 |
| `AI_SCENE_UNKNOWN` | 400 | 场景名不是 `terminal` / `file_edit` |
| `AI_AGENT_UNLOCK_REQUIRED` | 422 | 普通 agent 会话未通过 30 分钟密码解锁（只读场景豁免） |
| `READ_RATE_LIMITED` | 429 | 重查询读预算超限（日志 / prompt / 导出下载） |
| `FILES_ROOT_FORBIDDEN` | 403 | 文件路径不在 `/www/zvms4`、`/home` 之内 |
| `AI_DISABLED` | 503 | 网关未启用 |
| `AI_RATE_LIMITED` | 429 | 本地频控超限 |
| `AI_QUOTA_EXCEEDED` / `insufficient_quota` | 429 | 用户月额度耗尽 |
| `AI_BUSY` | 429 | 并发闸门满 |
| `AI_NO_PROVIDER` | 503 | 无可用上游 |
| `AI_OCR_NO_ENGINE` | 503 | 无启用 OCR 引擎（含 `provider` 指定的 kind 没配） |
| `AI_OCR_SITE_QUOTA_EXHAUSTED` | 503 | 全部 OCR 引擎站点额度耗尽 |
| `AI_UNSUPPORTED_MEDIA` | 400 | OCR 入队 mime 不支持 |
| `AI_OCR_IMAGE_TOO_LARGE` | 400 | OCR 单图超过体积上限（默认 20MB） |
| `AI_OCR_ENGINE_BAD_KIND` | 400 | `provider` 不是受支持的引擎 kind |
| `AI_OCR_ENGINE_ERROR` | — | 仅出现在 `ai_call_logs.error_code`：某次引擎尝试失败 |
| `AI_TIMEOUT` | 504 | 上游超时 |
| `AI_UPSTREAM_AUTH` | 502 | 上游鉴权失败 |
| `AI_UPSTREAM_REJECTED` | 502 | 上游 4xx 拒绝 |
| `AI_UPSTREAM_RATE_LIMIT` | 502 | 上游限流 |
| `AI_UPSTREAM_ERROR` | 502 | 上游 5xx |
| `AI_CREDENTIAL_UNREADABLE` | 502 | 凭证解密失败 |
| `model_not_found` | 404 | 模型不可见 |
| `AI_INVALID_REQUEST` | 422 | 请求体校验失败（含 pydantic 数组 detail） |
| `AI_KEY_*` | 400/403/404/409 | 凭据相关 |
| `AI_API_KEY_INVALID` | 401 | API key 无效（不存在/禁用/过期/用户已删） |
| `AI_API_KEY_NOT_FOUND` / `AI_API_KEY_DUPLICATE` / `AI_API_KEY_LIMIT` | 404 / 409 / 409 | API key 管理 |
| `AI_API_KEY_BAD_EXPIRY` | 400 | API key 到期时间非法 |
| `AI_API_KEY_BAD_NAME` | 400 | API key 名字非法（空 / 超 64 字符 / 控制字符） |
| `USER_BANNED` | 403 | 账号封禁中（带 `X-Ban-Reason` / `X-Ban-Until`） |
| `AI_OCR_ENGINE_*` | 400/404/409 | 引擎相关 |
| `AI_QUOTA_BAD_KIND` / `AI_QUOTA_BAD_LIMIT` | 400 | 额度参数非法 |
| `AI_LOGS_BAD_FILTER` | 400 | 流水过滤参数非法 |
| `AI_AGENT_BAD_ARGUMENTS` | 400 | agent 入参/消息不合法 |
| `AI_AGENT_BAD_ARGUMENT` | 400 | 工具参数非法（类型/枚举/上下界） |
| `AI_AGENT_NOT_FOUND` | 404 | 工具查询目标不存在（活动/成员） |
| `AI_AGENT_PROPOSAL_NOT_FOUND` | 404 | 提案不存在（含 oid 形状不对） |
| `AI_AGENT_PROPOSAL_FORBIDDEN` | 403 | 不是本人的提案 |
| `AI_AGENT_PROPOSAL_DENIED` | 403 | 执行时权限校验未通过 |
| `AI_AGENT_PROPOSAL_CONFLICT` | 409 | 提案已处理 |
| `AI_AGENT_PROPOSAL_TAMPERED` | 409 | 落库参数与记录的哈希不符 |
| `AI_AGENT_PROPOSAL_UNKNOWN_TOOL` | 409 | 提案引用了已下线的工具 |
| `AI_AGENT_PROPOSAL_EXPIRED` | 410 | 提案已过期 |
| `AI_AGENT_NOT_WRITE_TOOL` / `AI_AGENT_ARGS_TOO_LARGE` | 400 | 提案参数问题 |
| `AI_AGENT_TOOL_FAILED` | 502 | 工具执行内部失败 |
| `AI_CONVERSATION_NOT_FOUND` | 404 | 会话不存在或不属于本人 |
| `AI_CONVERSATION_MODE_MISMATCH` | 409 | 会话模式与端点不符（`chat` 模式已下线：新建回 409，存量只可读/归档/删除） |
| `AI_RUN_CONFLICT` | 409 | 同会话/同用户已有活跃 run；删除有活跃 run 的会话 |
| `AI_RUN_NOT_FOUND` | 404 | run 不存在或不属于本人 |
| `AI_RUN_NOT_CANCELLABLE` | 409 | run 已是终态 |
| `AI_RUN_INTERRUPTED` | — | run 因进程重启/心跳丢失被收敛（出现在 `ai_runs.error_code`） |
| `AI_CONTEXT_UNAVAILABLE` | 503 | 压缩所需上游不可用（已降级为截断时不抛） |

前端映射在 `zvms4-frontend/src/api/ai/errors.ts` + `src/i18n/locales/ai.ts`；新增错误码必须两处同步。

---

## 12. 已知限制与缺口

| # | 缺口 | 影响 |
|---|---|---|
| 8 | 审计写入失败静默 | `units` 与 `ai_quota.used` 可能对不上账（只记 WARNING，不改变业务返回） |
| 10 | 附件 OCR 只支持 jpeg/png/webp | PDF 附件无法识别 |
| 12 | 流式 `units` 与实测 `units` 量纲不同 | 估算行约为实测行的 1000 倍，两种行不能直接相加（见 §6.3） |
| 13 | 流式 usage 仅白名单上游可得 | 白名单外（含自建网关）流式的 token 列恒为 NULL，只能靠估算 |

**已补上的缺口（本轮）**：token 明细（`prompt/completion/total_tokens`）、尝试链路
（`attempt_no` + `request_id`）、场景来源（`purpose`）、OCR 引擎追踪（`engine_kind` /
`attachment_oid`）、流式首块耗时（`first_token_ms`）、拒绝路径落流水（403/503/404/429
各一行 `rejected`）、查询过滤（`request_id`/`provider`/`model`/`purpose`）与聚合端点
`/logs/stats`。

**既定合规决策（不是缺口）**：`AI_CALL_LOGS` 只存元数据，prompt / completion 正文不进流水；
唯一的正文例外是 `AI_PROMPT_AUDIT`——按角色（默认学生会）受控留存、服务端脱敏后截断、
默认 30 天自动清理、只有 admin 单条审阅且每次读取写访问审计（见 6.8）。审计要能回答
「谁、何时、用了哪个上游/模型、花了多少、成没成」，不需要也不允许把对话内容散落在日志里。

> 频控 / 熔断 / 凭据缓存 / API key 缓存仍是**进程内**状态，但不再「静默」：进程内阈值
> 已按 worker 数折算，`/health` 回显 `worker_count`、`rate_limit_per_minute`、
> `cooldown_scope`，启动时也会打 WARNING。要做到跨进程精确限流仍需 Redis。

---

## 13. 运维速查

| 配置项 | 位置 | 作用 |
|---|---|---|
| `AI_ENABLED` | `settings` | 网关总开关 |
| `AI_ALLOWED_ROLES` | `config.py` | 可用角色，默认 `admin`/`department` |
| `AI_RATE_LIMIT_PER_MINUTE` | `config.py` | `{"ocr":10,"chat":20}` |
| `AI_WORKER_COUNT` | `config.py` / `settings` / 环境变量 | 部署的 worker 数，默认按 `WEB_CONCURRENCY` / `UVICORN_WORKERS` 探测，缺省 1 |
| `AI_DEFAULT_OCR_MONTHLY_UNITS` | `config.py` | 用户 OCR 默认 100 |
| `AI_DEFAULT_CHAT_MONTHLY_UNITS` | `config.py` | 用户对话默认 200000 |
| `AI_ROLE_QUOTA` | `config.py` | 角色覆盖额度 |
| `AI_SITE_QUOTA` | `settings.py`（生产专属） | 按 `ocr_<kind>` 覆盖站点额度 |
| `AI_DEFAULT_SITE_OCR_MONTHLY_UNITS` | `settings` | 站点额度兜底 1000 |
| `AI_OCR_MAX_IMAGE_BYTES` | `config.py` / `settings` | OCR 单图体积上限，默认 20MB |
| `AI_CREDENTIALS_CACHE_TTL` | `config.py` | 凭据缓存 30s |
| `AI_API_KEY_CACHE_TTL` | `config.py` | API key 校验缓存 30s |
| `AI_API_KEY_RATE_LIMIT_PER_MINUTE` | `config.py` | 单把 API key 的每分钟上限，默认 60 |
| `AI_OCR_ORT_THREADS` | 环境变量 | rapidocr 线程数 |
| `AI_CALL_LOGS_RETENTION_DAYS` | `config.py` | 流水保留 90 天 |
| `AI_STREAM_USAGE_HOSTS` | `settings`（可选） | 额外允许 `stream_options.include_usage` 的上游 host；`["*"]` = 全部 |
| `AI_SILICONFLOW_FREE_MODELS` | `settings` | SiliconFlow 免费模型白名单（运维维护）；`misc/seed_channel.py` 只从这里取模型，留空 = 渠道不上线 |
| `AI_AGENT_MAX_ITERATIONS` | `config.py` / `settings` | agent 单次 run 的轮数上限，默认 10 |
| `AI_AGENT_TOKEN_BUDGET` | `config.py` / `settings` | agent 单次 run 的累计 token 上限，默认 100000 |
| `AI_AGENT_PROPOSAL_TTL_SECONDS` | `config.py` / `settings` | 写操作提案有效期，默认 600 秒 |
| `AI_AGENT_RUN_TIMEOUT_SECONDS` | `settings`（可选） | agent 单次 run 的 wall-clock 上限，默认 300 秒 |
| `DUAL_AUTH_CACHE_TTL_SECONDS` | `settings`（可选） | 双人授权缓存窗口，默认 1800 秒；`0` = 关闭 |
| `AI_PROMPT_CAPTURE_ROLES` | `config.py` / `settings` | 正文留存角色，默认 `("department",)`；`()` = 关闭 |
| `AI_PROMPT_RETENTION_DAYS` | `config.py` / `settings` | 正文保留 30 天（日清任务） |
| `AI_PROMPT_MAX_CHARS` | `config.py` / `settings` | 单条正文上限 8000 字符（超出截断） |

相关表：`ai_credentials`、`ai_ocr_engines`、`ai_api_keys`、`ai_quota`、`ai_call_logs`、`ai_agent_events`、`ai_prompt_audit`、`ai_conversations` / `ai_messages` / `ai_runs`、`file_ocr`（迁移 `V024`–`V048`）。

> `V045`–`V048` 摘要：`app_logs` 加 `client_ip` / `user_agent`；`ai_conversations` 加 `thinking` / `scene`；`ai_prompt_audit` 加 `answer_text` / `tool_calls` / `refused` / `warned`；`V047` 为 `ban_violations(created_at)`、`ai_call_logs(status,created_at)` 建索引，删 4 条冗余索引与死表 `logs`。完整口径见 `zvms4-backend-python/docs/database-status.md`。

---

## 14. Agent 面 `/api/ai/agent`

智能体在一次请求里跑完「模型 → 工具 → 模型」循环，**不流式**：工具调用的完整参数
只在整条响应里，逐字回调等于猜半个 JSON。鉴权同上（JWT + `AI_ALLOWED_ROLES`）。

### 14.1 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/run` | 跑一次会话；`{messages, model?}` → `{request_id, content, stop_reason, rounds, usage, events, proposals}` |
| GET | `/proposals?status=proposed&limit=20` | 本人待确认提案 `{proposals:[...]}`（`limit ≤ 50`，过期行顺手落 `expired`） |
| POST | `/proposals/{oid}/confirm` | `{totp_code?}` → 执行；`404/403/409/410/422` 语义见下 |
| POST | `/proposals/{oid}/reject` | 无 body；`proposed → rejected` |
| GET | `/runs/{request_id}` | 本人这次 run 的事件时间线（`404` = 不存在或不是你的） |

`messages` 只接受 `user` / `assistant`；`system` 由服务端注入（版本化前缀），客户端传
→ `400 AI_AGENT_BAD_ARGUMENTS`。

### 14.2 写操作 = 提案 + 双人码

模型对写工具的调用**不执行任何写**，只落一条 `ai_agent_events(status='proposed')`。
确认时按顺序校验：归属（`actor_oid`）→ 时效（`AI_AGENT_PROPOSAL_TTL_SECONDS`）→
参数哈希（`ARGS_HASH` 覆盖 `ARGS_PAYLOAD`）→ 现查库权限（`guard`）→ 双人码 →
CAS `proposed → executing → confirmed|failed`。

- 运维写工具（`fs_*` / `db_*` / `ssh_exec`）的 `dual_auth_action="agent_write"`，必须由
  **另一位管理员**提供 6 位动态码；缺码 `422 AI_AGENT_TOTP_REQUIRED`，码错 `403`，
  已被使用 `409`，失败超限 `429`。
- **30 分钟授权缓存**：同一 operator + 同一 action 首次验码成功后，窗口内
  （`DUAL_AUTH_CACHE_TTL_SECONDS`，默认 1800，`0` = 关闭）确认**不必再输码**；
  响应与 `GET /proposals` 每个条目都带 `dual_auth_cached`。命中缓存仍写一条
  `system_dual_auth` 审计并标 `cached=true`。换人或换 action 不继承。
- 活动域两条写工具（`update_member_duration` / `set_member_status`）保持无 TOTP，
  行为不变。

### 14.3 工具集（封闭白名单，37 个）

| 域 | 读（自动执行） | 写（提案 + TOTP） |
|---|---|---|
| 活动 | `search_activities` `list_activities` `get_activity` `activity_statistics` `list_activity_members` `user_volunteer_time` `user_activities` `list_groups` | `update_member_duration` `set_member_status` |
| 文件 | `fs_list` `fs_stat` `fs_read` | `fs_write` `fs_mkdir` `fs_rename` `fs_delete` `fs_chmod` |
| 数据库 | `db_schema` `db_columns` `db_rows` `db_query` | `db_insert` `db_update` `db_delete` `db_ddl` |
| SSH | — | `ssh_exec`（固定目标 `root@127.0.0.1:22`，一次 `exec`，输出 8KB 截断） |
| 平台读 | `platform_logs` `db_diagnostics` `backup_status` `scheduler_status` `device_ban_list` `user_lookup` `ban_history` `settings_snapshot` `scheduler_jobs` `ai_call_logs` `agent_queue_status` `server_time` `ai_gateway_status` `ai_prompt_audit_list` `attachment_stats` `attachment_gc_preview` `volunteer_audit` | — |
| 平台写 | — | `user_ban` `user_unban` `device_unban`（`direct`，可逆且目标不得为管理员）；`notify_send` `maintenance_mode` `scheduler_control` `backup_run` `ai_api_key_revoke` `ai_quota_set` `attachment_gc_run` 走提案 |

`ban_history` 是本轮新增的只读工具：按 `user_oid` 或 `business_id` 返回封禁流水（最近 ≤20 条）、按 `rule_code` 的历史档位与最近时间、最近 30 天违规批次、启用规则的 `penalties` 梯度、绑定设备的封禁计数（学海 ID 打码）。它的 `note` 字段说明「`tiers.count + 1` 档即本次应执行档位，超出数组按最后一档」，让模型解释处罚时不需要自己猜梯度。

运维工具权限镜像 REST：`per` 含 `admin` + 每次执行现查库（`ban_is_active` 与权限重算），
不信任 token 快照。`db_query` 走只读 SQL 校验；`db_ddl` 先跑 DDL 校验（含
`confirm=表名`）；文件路径仍过 `realpath` + 根目录 jail（`/www/zvms4`、`/home`）+ `/proc`/`/sys` + 敏感文件黑名单。

### 14.4 成本闸

`AI_AGENT_MAX_ITERATIONS`（默认 10 轮）、`AI_AGENT_TOKEN_BUDGET`（默认 100000，
上游未报 usage 时按字符估算）、`AI_AGENT_RUN_TIMEOUT_SECONDS`（默认 300s）、单轮最多
5 个工具调用。每轮一条 `ai_call_logs`（`purpose='agent'`、`round_no` 递增、
`request_id` 全轮共用）并按该轮 usage 扣一次 chat 额度。

**Prompt Cache**：服务端 system 段与按 name 排序的 `tools` 构成稳定前缀，每轮都经
`services/ai/prompt_cache.request_body` 注入，`cache_key` 落审计。工具结果只追加在
对话尾部，前缀不随轮次变化。

### 14.5 错误码

`AI_AGENT_BAD_ARGUMENTS`(400) `AI_AGENT_ARGS_TOO_LARGE`(413) `AI_AGENT_TOTP_REQUIRED`(422)
`AI_AGENT_TOTP_INVALID`(403) `AI_AGENT_TOTP_RATE_LIMITED`(429)
`AI_AGENT_PROPOSAL_NOT_FOUND`(404) `AI_AGENT_PROPOSAL_FORBIDDEN`(403)
`AI_AGENT_PROPOSAL_CONFLICT`(409) `AI_AGENT_PROPOSAL_EXPIRED`(410)
`AI_AGENT_PROPOSAL_DENIED`(403) `AI_AGENT_PROPOSAL_TAMPERED`(409)
`AI_AGENT_PROPOSAL_UNKNOWN_TOOL`(409) `AI_AGENT_TOOL_FAILED`(502)
`AI_AGENT_INTERNAL_ERROR`(500)

---

## 15. Web SSH 命令起草 `/api/system/ssh/suggest`

管理员用自然语言描述想做什么，网关起草一条命令**文本**。它是 Web 终端
（`routers/system_ssh_router.py` 的 `/session` + `/ws`）的辅助入口，不是执行入口。

### 15.1 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/system/ssh/suggest` | `{intent, context?, model?}` → `{command, explanation, model, request_id}` |

- 鉴权：登录态 + 现查库的严格 admin（`_require_system_admin`）。**不要求**双人码：
  本端点不批准也不执行任何动作。
- 体量：`intent` 1–1000 字符，`context` ≤ 2000，`model` ≤ 64。
- 频控与额度：与 `/api/ai/v1/chat/completions` 同口径——`rate_limit`(chat) +
  `quota.peek` 预检 + 成功后 `quota.consume`(**仅**共享/服务端 key；个人 key 不计费)。

```json
// 请求
{"intent": "看看 caddy 容器还活着没", "context": "宿主 Ubuntu 22.04，容器名 caddy"}

// 响应
{"status": "ok", "code": 200,
 "data": {"command": "docker ps --filter name=caddy --format '{{.Status}}'",
          "explanation": "只读查询 caddy 容器状态，不改动任何东西。",
          "model": "gpt-x", "request_id": "3f1c…"}}
```

### 15.2 错误码

| code | HTTP | 触发条件 |
|---|---|---|
| `AI_DISABLED` | 503 | 网关关闭 |
| `AI_NO_PROVIDER` | 503 | 目录为空 / 模型对应 provider 全在冷却 |
| `model_not_found` | 404 | `model` 不在本人可见模型集合里 |
| `AI_RATE_LIMITED` | 429 | 本地分钟级频控 |
| `insufficient_quota` | 429 | 本月 chat 额度耗尽（仅计费 key） |
| `AI_BUSY` | 429 | 并发闸门满 |
| `AI_TIMEOUT` | 504 | 上游超时 |
| `AI_UPSTREAM_*` / `AI_CREDENTIAL_UNREADABLE` | 502 | 上游失败 / 凭据解不开 |
| `AI_AGENT_BAD_ARGUMENT` | 400 | 生成的命令超过 1024 字符 |

### 15.3 生成不等于执行（安全边界）

1. **没有执行路径**。服务层 `services/ai/textgen.py` 不 import `ssh_bridge` /
   `ssh_exec`，端到端点也不发 ticket、不开 WS；命令只会以字符串形式回到浏览器。
2. **执行仍然在终端里**。真正落地只有两条既有路径：人把命令粘进已过双人 TOTP 的
   `/ws` 终端并亲自回车；或 agent 的 `ssh_exec` 提案（另一位管理员的 6 位码）。
3. **system prompt 是服务端常量**。客户端只能给 `intent` / `context` / `model`，
   无法替换规则段。规则明确禁止交互式/长驻/TTY 程序（`vim`/`top`/`less`/`tail -f`、
   交互 shell、无 `-c`/`-e` 的 REPL、需确认的 `apt`/`passwd`）、禁止无差别破坏
   （`rm -rf /`、写块设备的 `dd`/`mkfs`、fork bomb、`shutdown`），并要求绝对路径、
   凭据用占位符。
4. **输出被收敛**。去 ``` 围栏；`json.loads` 失败则整段当命令（解释留空）；多行压成
   单行（换行粘进终端等于隐式回车）；空命令按上游故障 502；超过 1024 字符 400。
5. **审计双写、都不含命令全文之外的敏感物**：
   - `ai_call_logs` 一行（`capability=chat`、`purpose=system-ssh-suggest`、`units`、
     `key_scope`、失败含 `error_code`）——只有元数据，没有命令文本；
   - `app_logs` 一行 `type=system_ssh_suggest`（`intent_len` / `model` /
     `command_head`），`command_head` 是**脱敏后**的前 200 字符
     （`services.agent.ops_tools.redact`：`--password=` / `-p` / `ENV=value` / 超长串）。
     该 ltype 已加入 `PROTECTED_LTYPES`，与 `system_ssh_command` 同享保留期豁免。
6. **前端也必须「不自动回车」**：抽屉里生成的文本要经过人编辑、点插入，才写进终端。

> 已知边界：生成质量取决于上游模型；prompt 只能降低、不能消除「产出危险命令」的概率。
> 因此**人审阅是唯一的强制闸**，本端点在任何情况下都不提供「一键执行」。

---

## 16. 持久会话、run 与只读场景

会话与 run 的完整设计见 `docs/AI_CONVERSATION_DESIGN.md`。本节只列网关侧的协议事实。

### 16.1 端点

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/api/ai/conversations` | AI 可用角色 + `ai.agent` | `{mode?,model?,thinking?,title?}` → 201；`mode` 只剩 `agent`（`chat` 回 409） |
| GET | `/api/ai/conversations?status=active&limit=&cursor=` | 本人 | `updated_at` 倒序游标分页；不含消息正文 |
| PATCH | `/api/ai/conversations/{oid}` | 本人 | `{title?,status?,thinking?}`；**`model` 不可改** → 409 `AI_CONVERSATION_MODEL_LOCKED` |
| DELETE | `/api/ai/conversations/{oid}` | 本人 | 硬删；有活跃 run → 409 `AI_RUN_CONFLICT` |
| GET | `/api/ai/conversations/{oid}/messages?after_seq=&limit=` | 本人 | `seq > after_seq` 的增量消息 |
| POST | `/api/ai/conversations/{oid}/runs` | 本人 + 解锁 | `{content, client_msg_id?}` → **202** `{run_id,message_oid,status:"queued"}`；**不收 `model`** |
| GET | `/api/ai/runs/{run_id}?after_round=&since_msg_seq=` | 本人 | `{run,events,proposals}`；传 `since_msg_seq` 时额外带 `messages` |
| POST | `/api/ai/runs/{run_id}/cancel` | 本人 | 请求中止 |
| POST | `/api/ai/scenes/{scene}/runs` | admin + `ai.agent` | 见 16.3 |

### 16.2 `since_msg_seq` —— 轮级增量消息

```http
GET /api/ai/runs/9f2c...?after_round=2&since_msg_seq=17
```

- 给了 `since_msg_seq`：响应多一个 `messages` 数组，内容是 `seq > since_msg_seq`、升序、形状与 `GET /conversations/{oid}/messages` **完全一致**（同一份 `message_view`）。
- 不传：响应与改造前逐字相同，**没有 `messages` 键**——老客户端不需要改。
- 为什么复用 run 端点：一次轮询同时拿「run 进度」与「这轮新增的消息」，不会出现「run 已结束、消息还没拉」的中间态。
- 上限与消息端点相同（一次最多 `MESSAGE_LIMIT_MAX` 条）；缺失的消息由下一次轮询按最新 `seq` 续拉。

### 16.3 只读场景 `POST /api/ai/scenes/{scene}/runs`

终端页与文件编辑器需要「就当前内容问一句」，但不需要写能力。场景会话复用同一套会话 + run 管线，只在服务层收紧三处：

| 项 | 规则 |
|---|---|
| `scene` | 只能是 `terminal` / `file_edit`；拼错 400 `AI_SCENE_UNKNOWN` |
| 请求体 | `{content: 1–4000, context?: ≤8000}`；`context` 是选中的终端输出 / 文件片段 |
| 门禁 | 严格 `admin` + 功能开关 `ai.agent`；**故意不挂 30 分钟密码门**（只读 + admin，`run_queue.submit` 里有显式豁免条件） |
| 会话 | 按 `(user, scene)` 复用最近一条，标题固定「终端智能」/「文件编辑智能」；不出现在普通会话列表（`scene IS NULL` 过滤） |
| 工具 | 执行器按 `scene` 把工具声明裁到 `kind='read'`：写工具、提案流、`ssh_exec`、数据库 DML/DDL 都不在 `tools` 数组里（不是靠提示词约束） |
| `context` 处理 | 先脱敏再截断：键值形态凭据按键保留、值替换为 `***`；bcrypt/JWT/hex/`sk-` 与身份证/手机号走同一套脱敏。**只进 prompt 与 prompt 留存，不写消息表** |
| 响应 | **202** `{"status":"ok","code":202,"data":{"run_id":"...","conversation_oid":"..."}}`，用 16.1 的 `GET /runs/{run_id}` 轮询 |
| 错误体 | `{"error":{message,type,code}}` + `X-AI-Request-Id`（与 `/api/ai/conversations` 同一套解析） |
| 保留期 | 场景会话 14 天（`scene_conversations` 清理项），级联删除 messages / runs |

错误码：`AI_FORBIDDEN`(403)、`AI_SCENE_UNKNOWN`(400)、`AI_SAFETY_REJECTED`(422)、`AI_AGENT_INTERNAL_ERROR`(500)。
