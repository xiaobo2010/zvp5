# AI 会话管理 + 公开渠道 — 契约（冻结稿 v2）

> 状态：**已冻结并实施**（2026-10-25）。车道 A/B/C/D/E 全部收口，后端 2581 passed、前端 426 passed。
> v2 修订：并入 oracle 评审的 P0×3 / P1×4 / P2×4（并发唯一性、watchdog 语义、删除门禁、
> 压缩在飞重建、压缩计费豁免、消息原子配对、幂等边界）。
> 关联：`docs/AI_GATEWAY_API.md`、`docs/AI_AGENT_OPS_DESIGN.md`。

---

## 0. 目标与边界

### 0.1 目标

1. **持久会话**：对话与智能体共用一套会话/消息存储，刷新、换设备、隔天回来都能接上。
2. **后台执行**：智能体 run 任务化，浏览器关掉也跑完；重连后取结果。
3. **Token 上下文可视化**：会话级累计用量、单条消息用量、当前上下文占用与压缩水位。
4. **上下文压缩**：超阈值时把旧轮次自动摘要，保留最近 N 轮原文；历史在 UI 侧不丢。
5. **SiliconFlow 公开渠道**：平台出资、现有角色门禁内所有用户可用、按用户月额度计费。

### 0.2 已裁定

| 议题 | 裁定 |
|---|---|
| 会话覆盖 | 对话 + 智能体统一 |
| 后台执行 | 任务化，断开也跑完 |
| 上下文压缩 | 自动摘要 + 保留最近 N 轮 |
| 公开渠道准入 | 现有 `AI_ALLOWED_ROLES` 门禁 + 平台 shared 凭据 + 用户月额度 |
| 公开渠道模型 | **只上免费模型**（7 个支持工具调用的通用模型；限免/翻译/OCR 不入内），白名单 `AI_SILICONFLOW_FREE_MODELS` |
| 旧同步端点 | **直接删除** `POST /api/ai/agent/run`，前端同批切到任务化端点 |
| Key 投递 | 环境变量 / dotenv（`SILICONFLOW_API_KEY`），脚本只打印 `key_hint` |

### 0.3 明确不做（本期）

- **不改** `/api/ai/v1/chat/completions` 语义：仍是**无状态** OpenAI 兼容面，服务外部 `zvk_` 客户端。会话化只走新端点。
- **不做** 对话模式的流式后台化：chat 仍是同步 SSE（理由：chat 的产物就是逐字输出，后台跑完再回看没有价值；后台任务只给 agent 的长时间工具循环）。
- **不做** 多 worker：队列按单 worker 设计（`config/supervisor.conf:2` 无 `--workers`；`db/system_state.py:19-20`）。多 worker 需外部租约，本期只标注。
- **不做** 会话分享/协作、跨用户读取。
- **不做** 消息正文加密存储（与 `ai_prompt_audit` 同口径：DB 明文，权限靠 owner 过滤）。
- **不做** `reasoning_content` 落库：`_message_of`（`services/agent/executor.py:232-253`）现在直接丢弃，回放协议也不要求。后人不要"顺手补"。

---

## 1. 术语

| 术语 | 含义 |
|---|---|
| conversation | 一段会话，`ai_conversations` 一行，`oid` 是对外句柄（32 位 uuid4 hex） |
| message | 会话内一条消息，`ai_messages` 一行，`seq` 会话内单调递增 |
| run | 一次智能体执行，`ai_runs` 一行；`run_id` 同时作为 `ai_call_logs.request_id` 与 `ai_agent_events.request_id` |
| turn | 一问一答（user + 若干 assistant/tool + 最终 assistant） |
| 压缩水位 | `summary_upto_seq`：≤ 该 seq 的消息不进模型上下文，只进摘要 |

现状差别：`request_id` 每个 POST 新生成（`routers/ai_agent_router.py:158`）；messages 全在请求体（`services/agent/executor.py:678-683`）；回答正文与工具全文从不落库（`ai_agent_events.result_summary` ≤500，`executor.py:282`）。

---

## 2. 数据模型（V035）

三张新表，DDL 同步 `db/models.py`、`V001` 基线、`ddl/README.md`，过 `python -m db.ddl_check`。

### 2.1 `ai_conversations`

| 列 | 类型 | 约束/语义 |
|---|---|---|
| `oid` | VARCHAR2(32) | PK，`uuid4().hex` |
| `owner_oid` | VARCHAR2(24) | NOT NULL；只有本人可见 |
| `mode` | VARCHAR2(8) | CHECK `IN ('chat','agent')`；建会话时定，不可改 |
| `title` | VARCHAR2(120) | 首条用户消息截断生成；可手动改 |
| `model` | VARCHAR2(120) | 最近使用的模型；NULL = 服务端默认 |
| `status` | VARCHAR2(12) | CHECK `IN ('active','archived')`，默认 `active` |
| `summary` | CLOB | 压缩摘要正文；NULL = 从未压缩 |
| `summary_upto_seq` | NUMBER(10) | 已压入摘要的最大 seq；0 = 未压缩 |
| `tokens_prompt` / `tokens_completion` / `tokens_total` | NUMBER(12) | 累计用量 |
| `context_tokens` | NUMBER(12) | **最近一次**请求的 prompt token（上下文水位） |
| `message_count` | NUMBER(10) | 冗余计数；列表页不 count |
| `compaction_degraded_at` | TIMESTAMP | 最近一次压缩降级（硬截断）时间；NULL = 正常 |
| `created_at` / `updated_at` | TIMESTAMP | 追加消息/改名/改状态时刷新 `updated_at` |

索引：`(owner_oid, status, updated_at DESC)`。

### 2.2 `ai_messages`

| 列 | 类型 | 约束/语义 |
|---|---|---|
| `oid` | VARCHAR2(32) | PK |
| `conversation_oid` | VARCHAR2(32) | NOT NULL；**逻辑外键**，不建 FK（与全库一致） |
| `seq` | NUMBER(10) | 会话内递增，`UNIQUE(conversation_oid, seq)` |
| `role` | VARCHAR2(12) | CHECK `IN ('user','assistant','tool')`。**不设 `summary` 角色**：摘要只存会话行，UI 用 `summary_upto_seq` 画分隔线 |
| `content` | CLOB | 正文；`tool` 是工具结果全文（≤8000 字符，与模型看到的同长） |
| `tool_calls` | CLOB | assistant 的 `tool_calls` JSON 原文；其它角色 NULL |
| `tool_call_id` | VARCHAR2(64) | `role='tool'` 时对应调用 id |
| `tool_name` | VARCHAR2(64) | `role='tool'` 时工具名（UI 折叠标题） |
| `round_no` | NUMBER(3) | 所属 run 的轮次（1 起）；与 `ai_call_logs(request_id, round_no)` 对齐，逐轮 provider/cache/units 可 join |
| `finish_reason` | VARCHAR2(24) | assistant 行：`stop` / `tool_calls` / `length` 等；诊断截断回答 |
| `request_id` | VARCHAR2(64) | 产生该消息的 run id |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | NUMBER(10) | 该轮 usage；`user`/`tool` 行 NULL |
| `cache_read_tokens` / `cache_write_tokens` | NUMBER(10) | 该轮缓存；NULL = 上游未报 |
| `created_at` | TIMESTAMP | |

索引：`UNIQUE(conversation_oid, seq)` + `(conversation_oid, created_at)`。

**硬不变量（评审 P1-3）**

1. 每轮的 `assistant(tool_calls)` + 该轮**全部** `tool` 消息在**同一事务**落库。中断时不允许出现"半条工具链"。
2. 回放组装侧防御：若 `assistant.tool_calls` 存在但缺失任一对应 `tool.tool_call_id`，丢弃该 assistant 与其残缺 tool（上游会 400）。
3. 用户消息在 run 创建时写；assistant/tool 在每轮收尾写。**run 进入 `running` 前，唯一持久化就是那条用户消息**（见 §4.5 幂等边界）。

### 2.3 `ai_runs`

| 列 | 类型 | 约束/语义 |
|---|---|---|
| `oid` | VARCHAR2(32) | PK = `run_id` |
| `conversation_oid` | VARCHAR2(32) | NOT NULL |
| `owner_oid` | VARCHAR2(24) | NOT NULL |
| `client_msg_id` | VARCHAR2(64) | 客户端幂等键；可空 |
| `status` | VARCHAR2(16) | CHECK `IN ('queued','running','succeeded','failed','cancelled','interrupted')` |
| `model` | VARCHAR2(120) | 本次模型 |
| `stop_reason` | VARCHAR2(24) | `completed/max_iterations/token_budget/timeout/cancelled/error` |
| `rounds` | NUMBER(3) | 实际轮数 |
| `error_code` | VARCHAR2(40) | 失败码（`AI_AGENT_*` / `AI_RUN_INTERRUPTED`） |
| `error_message` | VARCHAR2(500) | 人话错误，已脱敏 |
| `result_message_oid` | VARCHAR2(32) | 最终 assistant 消息 oid |
| `prompt_tokens` / `completion_tokens` / `total_tokens` | NUMBER(12) | 本次累计 |
| `cancel_requested` | NUMBER(1) | 默认 0；worker 每轮检查 |
| `heartbeat_at` | TIMESTAMP | 独立 ticker 每 30s 刷新；判僵尸 |
| `created_at` / `started_at` / `finished_at` | TIMESTAMP | |

**唯一性由数据库兜底（评审 P0-1）**

```sql
CREATE UNIQUE INDEX uq_ai_runs_active_conv ON ai_runs (
  CASE WHEN status IN ('queued','running') THEN conversation_oid END);
CREATE UNIQUE INDEX uq_ai_runs_active_owner ON ai_runs (
  CASE WHEN status IN ('queued','running') THEN owner_oid END);
CREATE UNIQUE INDEX uq_ai_runs_client_msg ON ai_runs (conversation_oid, client_msg_id);
```

Oracle 不索引全 NULL 键，所以终态行不互斥。这不是可选项：应用层 `SELECT ... FOR UPDATE` + 检查只是**第一道**，函数唯一索引是**第二道**（ORA-00001 → 409）。

其他索引：`(owner_oid, status)`、`(conversation_oid, created_at DESC)`、`(status, heartbeat_at)`。

**提交事务（必须整段在一个事务内，且先锁会话行）**

```
SELECT ... FROM ai_conversations WHERE oid=:c AND owner_oid=:u FOR UPDATE
  → 校验 mode='agent'
  → 校验无 queued|running run（第二道靠唯一索引）
  → 校验 owner 活跃 run 数 < AI_RUN_MAX_PER_USER
  → INSERT ai_messages(user, seq=N+1)
  → INSERT ai_runs(status='queued')
COMMIT
```

并发两个 POST：第二个在唯一索引上吃 ORA-00001 → 409 `AI_RUN_CONFLICT`，不产生孤儿用户消息（事务回滚）。

客户端重试：带 `client_msg_id` 时命中唯一索引 → 返回**已存在**的 `run_id` 与 `message_oid`，不重复写消息、不重复计费。

---

## 3. API 契约

全部挂 `/api/ai`，鉴权 `get_current_user` + `_has_ai_access`，**所有查询强制 `owner_oid = 当前用户`**。

### 3.1 会话

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| POST | `/conversations` | `{mode, model?, title?}` | `{conversation}` |
| GET | `/conversations?status=active&limit=50&cursor=` | — | `{conversations:[...], next_cursor}` |
| GET | `/conversations/{oid}` | — | `{conversation}` |
| PATCH | `/conversations/{oid}` | `{title?, status?, model?}` | `{conversation}` |
| DELETE | `/conversations/{oid}` | — | `{deleted:true, messages:N, runs:N}`（硬删） |
| GET | `/conversations/{oid}/messages?after_seq=0&limit=50` | — | `{messages:[...], has_more, total}` |

`conversation` 视图：

```json
{
  "oid": "...", "mode": "agent", "title": "...", "model": "...", "status": "active",
  "tokens": {"prompt": 0, "completion": 0, "total": 0},
  "context": {"tokens": 0, "limit": 32768, "ratio": 0.0},
  "compaction": {"upto_seq": 0, "summarized": false, "degraded_at": null},
  "message_count": 0,
  "created_at": "...", "updated_at": "..."
}
```

**列表视图不含 `active_run`、不含消息正文**（避免 N+1 与大字段；活跃 run 走 `GET /runs?active=1`）。

`message` 视图：`{oid, seq, role, content, tool_calls?, tool_call_id?, tool_name?, round_no?, request_id?, usage?:{...}, created_at}`。

`cursor`：`updated_at|oid` base64；默认 50，上限 100。

**DELETE 门禁（评审 P0-3）**

1. 存在 `queued|running` run → 409 `AI_RUN_CONFLICT`（前端可先 cancel 再删）。
2. 同一事务内作废该会话所有待确认提案：
   ```sql
   UPDATE ai_agent_events SET status='failed', result_summary='会话已删除'
    WHERE request_id IN (SELECT oid FROM ai_runs WHERE conversation_oid=:c)
      AND status='proposed';
   ```
   否则删掉会话后，`confirm` 仍能按 oid 执行一个**用户以为已删除**的写操作（`ai_agent_router.py:203+` 只校验 proposer，不依赖 run 存在）。
3. 批删顺序 `messages → runs → conversation`，按 ROWNUM/rowid 分批提交（沿用 `tasks/retention.py` 的批删范式），不用 `IN (...)` 大列表。

### 3.2 智能体（任务化）

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| POST | `/conversations/{oid}/runs` | `{content, model?, client_msg_id?}` | **202** `{run_id, message_oid, status:"queued", deduped?:true}` |
| GET | `/runs/{run_id}?after_round=0` | — | `{run, events, proposals}` |
| POST | `/runs/{run_id}/cancel` | — | `{status}` |
| GET | `/runs?active=1` | — | `{runs:[...]}` |

- 提交：一个事务（§2.3）→ `worker.kick()` → 立即 202，不等待执行。
- `GET /runs/{id}`：`events` 只回 `round_no > after_round` 的增量（同轮重发按 oid 去重），轮询载荷随轮次线性而非平方增长。
- 取消：置 `cancel_requested=1`；`queued` 直接 CAS 成 `cancelled`；worker 在轮边界和工具执行前检查。已执行工具不可回滚，消息照常落库。
- `GET /runs?active=1`：重连横幅用。

### 3.3 对话（同步 SSE，新增）

`POST /conversations/{oid}/chat`（`mode='chat'`，SSE）：

- 入参 `{content, model?}`；服务端从 DB 取历史 + 压缩摘要组装。
- 沿用 `src/api/ai/stream.ts` 的 `data:` 帧与 `[DONE]`；首帧附 `conversation_oid`/`message_oid`。
- 完成后 assistant 消息落库 + 会话用量与水位更新；流中断则 assistant 不落库，`ai_call_logs` 记 `error`。

`/api/ai/v1/chat/completions` **保持无状态**，不读写会话。

**旧端点删除**：`POST /api/ai/agent/run` 本期**直接删除**，前端同一批改造切到 `POST /conversations/{oid}/runs`。不做兼容层；发布窗口内未升级的旧前端会 404，这是已裁定代价。

### 3.4 错误码（新增）

| 码 | HTTP | 场景 |
|---|---|---|
| `AI_CONVERSATION_NOT_FOUND` | 404 | 不存在或不属于本人 |
| `AI_CONVERSATION_MODE_MISMATCH` | 409 | 往 chat 会话提交 agent run（或反之） |
| `AI_RUN_CONFLICT` | 409 | 同会话/同用户已有活跃 run；或删会话时有活跃 run |
| `AI_RUN_NOT_FOUND` | 404 | run 不存在或不属于本人 |
| `AI_RUN_NOT_CANCELLABLE` | 409 | 已是终态 |
| `AI_CONTEXT_UNAVAILABLE` | 503 | 压缩所需上游不可用（已降级为截断时**不**抛此码） |

全部进 `docs/AI_GATEWAY_API.md` §11 与前端 `src/api/ai/errors.ts`。

---

## 4. 执行与恢复（后台任务）

### 4.1 组件

- `services/ai/conversations.py`：会话/消息 CRUD、seq 分配、token 累加、视图组装、批删。
- `services/agent/run_queue.py`：入队、领取、心跳 ticker、收尾、启动收敛、取消、watchdog。
- `services/agent/context.py`：上下文组装与压缩判定/执行。

### 4.2 单 worker 领取

进程内单个 pump 协程（与 `tasks/retention.py:799` 同范式）：

1. 空闲时每 1s：
   ```sql
   SELECT oid FROM ai_runs
    WHERE status='queued' AND cancel_requested=0
    ORDER BY created_at FETCH FIRST 1 ROWS ONLY FOR UPDATE SKIP LOCKED
   ```
2. 同事务 CAS 到 `running`、写 `started_at`/`heartbeat_at`，提交；
3. 全局 `asyncio.Semaphore(AI_RUN_CONCURRENCY)`（默认 2）；领取循环串行；
4. 每 run 独立 task，异常一律收敛为 `failed`，pump 不死。

> `FOR UPDATE SKIP LOCKED` 在 ADB 21c+ 支持。若实测 thin 驱动不支持，退化为「`UPDATE ... WHERE status='queued' AND oid=(子查询)` 的 CAS」，单 worker 语义等价。

### 4.3 心跳、watchdog、恢复

**心跳**：每个 run 的 task 内起一个 30s ticker，与上游等待无关地刷新 `heartbeat_at`。**不能**只在轮边界刷——单轮最坏可占满剩余 run deadline（`executor.py:780-782`）。

**watchdog**（每 30s）：
```sql
UPDATE ai_runs SET status='interrupted', error_code='AI_RUN_INTERRUPTED',
       finished_at=SYSTIMESTAMP, error_message='执行进程失去心跳'
 WHERE status='running' AND heartbeat_at < :now_minus_stale
```
- `AI_RUN_STALE_SECONDS` 默认 **360**，硬约束 `> AI_AGENT_RUN_TIMEOUT_SECONDS`（默认 300）。配置校验时取 `max(360, run_timeout + 60)`。
- 命中后用**CAS 条件**确认（`status='running' AND heartbeat_at=:读到的值`），并 `task.cancel()` 对应协程，防止"终态行上继续写消息/继续扣费"。
- 另加一致性兜底：任何写消息/扣额度前检查 run 仍 `running`，否则丢弃本轮。

**启动收敛**（`main.py` startup，紧跟 `fail_pending`，`main.py:274-275`）：
- `running` → `interrupted`（单 worker 下启动即无合法在跑任务）；
- `queued` **保留**，pump 起来后领取。

### 4.4 executor 接入

`executor.run()` 保持纯执行器定位，新增参数（带默认值，现有调用与测试不受影响）：

```python
executor.run(
    user=..., messages=..., model=..., request_id=run_id,
    on_round=callable,      # 每轮收尾：事务内写 assistant+tool 消息、累加 token、更新会话
    should_stop=callable,   # 轮边界 + 每次工具执行前：cancel_requested / deadline / 已中断
)
```

executor 内不碰会话表；`on_round` 由 `run_queue` 提供。

### 4.5 幂等边界（承重墙）

> **run 进入 `running` 前，唯一的持久化是那条用户消息。**
> 计费、assistant/tool 消息、`ai_conversations` 累加都发生在**轮收尾**的同一个事务里。

由此：
- 崩溃的 `running` 不重跑 → 不可能重复扣费；
- `queued` 重跑 → 只有用户消息已存在，重跑零重复；
- 重试同一 `client_msg_id` → 返回已有 run，不新增消息。

### 4.6 计费与审计

- 每轮仍写 `ai_call_logs`（`request_id=run_id`、`round_no`），口径不变。
- run/会话累计冗余 token，用于列表与水位展示。
- 事件仍写 `ai_agent_events`（`request_id=run_id`）；提案 `confirm` 语义与 30 分钟免码缓存**完全不变**。

---

## 5. 上下文组装与压缩

### 5.1 稳定前缀（不可破坏）

1. `system`：`PROMPT_VERSION` 版本化提示（`services/agent/prompt.py`），逐字不变；
2. `system`：压缩摘要（仅 `summary_upto_seq > 0`；两次压缩之间逐字不变）；
3. 历史消息（`seq > summary_upto_seq`）；
4. 本轮新消息。

`tools` 仍按 name 排序。结论（评审 P1-1）：摘要**必须**放第二条 system——放尾部会让 `prompt_cache.prefix_fingerprint`（只看 leading system，`services/ai/prompt_cache.py:71-113`）退化成全站同 key，且摘要永远打不到前缀缓存。前缀只在压缩点变化，这是可接受代价。

### 5.2 水位

- 优先用上一轮实测 `prompt_tokens`；
- 加上新消息 `chars/4` 估算（`services/ai/usage.py:42`）。

### 5.3 触发、执行与在飞重建（评审 P1-1）

- 阈值：`context_tokens >= ceil(limit * AI_CONTEXT_COMPACT_RATIO)`，默认 `0.75`。
- `limit`：`AI_MODEL_CONTEXT_LIMITS`（model → int）+ `AI_DEFAULT_CONTEXT_LIMIT`（默认 32768，**保守值**）。带 5×8000 字符工具结果的 agent 轮会较早触发压缩——运维按模型调高，文档写清取舍。
- 保留最近 `AI_CONTEXT_KEEP_TURNS`（默认 6）轮。
- **切点硬约束**：只能在 `role='user'` 之前切；`assistant(tool_calls)` 与其 tool 结果不可被切断（上游协议硬约束）。
- **在飞重建**：压缩发生在**轮边界**；压缩成功后必须用「摘要 + `seq > summary_upto_seq` 的消息」**重建 executor 的 `conversation` 列表**，而不是只写 DB。否则在飞 run 仍带全量历史，水位不降、下一轮照样超限。
- 一次 run 内最多压缩 1 次（防抖动）。
- 摘要调用：普通 `chat_completion`（无工具），固定 system 提示，输出 ≤ `AI_CONTEXT_SUMMARY_MAX_TOKENS`（默认 800）。

### 5.4 压缩的计费（评审 P1-2）

- **计入用户月额度**（真实上游成本），且调用前过 `quota.peek`（与 `executor.py:738-742` 同闸）；
- **豁免 run token 预算**（`AI_AGENT_TOKEN_BUDGET`）：压缩的输入是被摘要的历史，计入预算会出现「压缩费付了、省下的上下文一次没用上就被 budget 掐死」；
- 审计 `ai_call_logs.purpose='compact'`，`request_id=run_id`，`round_no=NULL`；
- 摘要输入设上限：超限先硬截断旧段再摘要。

### 5.5 降级

1. 换候选 provider 重试 1 次；
2. 仍失败 → 不压缩，硬截断最旧消息至水位以下，写 `compaction_degraded_at`；
3. 压缩后仍超限 → 硬截断补齐；
4. 全链路失败不阻塞 run，只在会话视图标 `degraded_at`。

---

## 6. Token 展示口径

| 位置 | 数据 | 来源 |
|---|---|---|
| 会话列表项 | 累计 total token | `ai_conversations.tokens_total` |
| 会话头部 | `context_tokens / limit` 进度条 | `context_tokens` + §5.3 limit |
| 压缩标记 | 「已压缩至第 N 条」 | `summary_upto_seq` |
| 单条 assistant | 该轮 prompt/completion/cache | `ai_messages` token 列 |
| run 详情 | 轮数、累计、stop_reason | `ai_runs` |
| 运行中 | 已完成轮数 + 事件增量 | `GET /runs/{id}?after_round=` |

上游未报 usage 显示 `—`（不显示 0），与现有 `None = 未知` 一致。

---

## 7. SiliconFlow 公开渠道

### 7.1 结论：不改 provider 层

`ai_credentials` 的 `shared` + `kind='openai_compat'` 已满足「公开渠道」：所有登录用户可见、管理员维护、按用户月额度计费（计费口径 `_billable`：`services/ai/executor` 侧 `services/agent/executor.py:108` 与 `routers/ai_router.py:381`，scope≠personal 才计费）。SiliconFlow OpenAI 兼容基址 `https://api.siliconflow.cn/v1`，端点拼接沿用 `providers._endpoint`。

### 7.2 落地

1. `misc/seed_channel.py`：
   - `--name siliconflow --base-url https://api.siliconflow.cn/v1 --key-env SILICONFLOW_API_KEY --free-only`；
   - key 来源优先级：`--key-file <path>` 里的 dotenv → 环境变量 `SILICONFLOW_API_KEY`。**只打印 `key_hint`，绝不打印明文**；
   - `--free-only`：用该 key 请求 `GET {base_url}/models`，与 `AI_SILICONFLOW_FREE_MODELS`（settings 配置的免费模型白名单）取交集，取前 20（`_MODELS_MAX=20`，`services/ai/credentials.py:49`）；
   - **只上免费模型**：白名单为空时脚本拒绝执行（报错提示先配 `AI_SILICONFLOW_FREE_MODELS`），避免误把付费模型暴露给全员。免费清单是运维资产：SiliconFlow 的免费档会变，脚本不猜；
   - 幂等：同名 shared 已存在则 `update_entry` 轮换 key/models。
2. `AI_STREAM_USAGE_HOSTS` 默认值追加 `api.siliconflow.cn`（`services/ai/config.py:495-511`），让流式 token/额度可计量；**不加** `prompt_cache` host（SiliconFlow 不认 OpenAI `prompt_cache_key`）。
3. 文档补「渠道」小节 + 前端 Key 管理页给 `shared` 加「公开渠道」徽标与模型数。
4. 成本/风控：扣发起人月额度 + 每用户频控（默认 20/min）+ 全局并发闸门；本期不做站点级 chat 额度。

---

## 8. 前端

- 左栏会话列表：新建/切换/重命名/归档/删除，按 `updated_at` 倒序，每项显示标题、时间、累计 token。
- 对话区按 `seq` 渲染；agent 消息折叠工具事件与提案卡；运行中显示轮次进度与「后台执行中」。
- 上下文条 `context_tokens/limit`，≥90% 警示；压缩点画分隔线并提示「更早 N 条已压缩」。
- 进入页面先 `GET /runs?active=1` 恢复在飞任务；轮询 1.5s，`document.hidden` 降频 5s。
- store：`stores/aiConversations.ts`；消息增量 `after_seq`；游标分页。
- 文案进 `src/i18n/locales/ai.ts`（`ai.chat.sessions.*`），注意裸 `@` 会挂 `locales.spec.ts`。
- 类型追加仓库根 `types/ai.d.ts`（**不在** `src/types`）。

---

## 9. 迁移、保留期、运维

- **V035**：三表 + 三个函数唯一索引 + 常规索引 + CHECK；同步 models/V001/README/mirror 测试。
- **保留期**（`tasks/retention.py`）：
  - `ai_conversations`：`status='archived'` 且 `updated_at` 超 `AI_CONVERSATION_RETENTION_DAYS`（默认 180）→ 删；`active` 不动。
  - `ai_messages` / `ai_runs`：**跟随会话删除**（批删，ROWNUM 分段，不用 `IN` 大列表）。
  - `ai_runs` 不独立设更短窗口——否则 `result_message_oid` 会指向已删消息（评审 P2-2）。仅兜底清理「会话已不存在」的孤儿 run。
  - 三类都进 retention 批次统计与测试（现五表 → 八表）。
- **容量**：单会话 200 轮按 8KB/条 ≈1.6MB CLOB；列表查询**不得 select content**。
- **运维脚本**：`misc/apply_ddl.py` 已可用（V035 用同一路径应用）。

---

## 10. 车道与验收

| 车道 | 文件白名单 | 交付 | 验收 |
|---|---|---|---|
| A 数据层 | `db/models.py`、`db/oracle/ddl/V035*.sql`、`db/oracle/ddl/V001*.sql`、`ddl/README.md`、`db/repos/oracle/ai_conversations.py`、`db/repos/oracle/ai_runs.py`、`db/repos/protocols.py`、`db/backends.py`、`tests/test_ai_ddl_mirror.py`、新增 repo 测试 | 三表 + 三个函数唯一索引 + repo | `pytest tests/test_ai_repo_sql.py tests/test_ai_ddl_mirror.py tests/test_ai_conversations_repo.py` + `db.ddl_check` |
| B 会话与任务 | `services/ai/conversations.py`、`services/agent/run_queue.py`、`routers/ai_conversations_router.py`、`main.py`、`services/agent/executor.py`、`tests/test_ai_conversations.py`、`tests/test_ai_run_queue.py` | CRUD/SSE/任务/心跳/恢复/取消/删除门禁 | `pytest tests/test_ai_conversations.py tests/test_ai_run_queue.py tests/test_agent_executor.py` |
| C 上下文压缩 | `services/agent/context.py`、`tests/test_agent_context.py`（**不碰** `services/ai/config.py`：阈值用 `getattr(settings, ...)` 读取，默认值写在 context.py） | 组装/摘要/在飞重建/降级 | `pytest tests/test_agent_context.py tests/test_agent_executor.py` |
| D 前端 | `types/ai.d.ts`、`src/api/ai/conversations.ts`、`src/stores/aiConversations.ts`、`src/components/ai/**`（含 `ZAiKeysAdmin.vue` 的「公开渠道」徽标）、`src/views/ai/AIHome.vue`、`src/api/ai/errors.ts`、`src/i18n/locales/ai.ts`、新增 spec | 侧栏/消息/上下文/重连；旧端点调用全部切走 | `vue-tsc`、`eslint`、`vitest` |
| E 渠道 | `misc/seed_channel.py`、`services/ai/config.py`（仅 `AI_STREAM_USAGE_HOSTS` 默认值 + `AI_SILICONFLOW_FREE_MODELS` 读取）、`settings.example.py`、`docs/AI_GATEWAY_API.md` | SiliconFlow 渠道脚本与配置 | 脚本 `--dry-run` + 单测（不连网） |

依赖：A → B → C；D 按 §3 契约并行；E 独立。

## 11. 验收总表

1. `pytest -q` 全绿（基线 2396 + 新增）。
2. `python -m db.ddl_check` no drift。
3. 前端 `vue-tsc --noEmit` / `eslint` / `vitest run` 全绿。
4. 并发：同会话同时提交两个 run → 恰好一个 202、一个 409；重复 `client_msg_id` → 同一 `run_id`。
5. 恢复：run 执行中重启进程 → 该 run 变 `interrupted`，`queued` 的自动续跑；无重复计费。
6. 压缩：连续对话触发一次压缩，历史仍可上翻，`context_tokens` 回落，缓存只在压缩轮失效。
7. 删除：存在活跃 run 时会话删不掉；删除后其待确认提案变 `failed` 且 confirm 返回 404。
8. SiliconFlow 模型出现在选择器并能流式回答，`ai_call_logs.provider` 记到渠道名。
