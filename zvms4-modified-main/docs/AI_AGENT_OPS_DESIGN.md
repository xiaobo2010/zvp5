# AI 运维 Agent 设计（文件 / SSH / 数据库）

> 状态：**契约冻结**。基线：`feature/agent-core` 工作区（活动域 agent 已落地并通过 101 项测试）。
> 本文只定义「扩到运维」的增量与不可协商的不变量；活动域既有行为不改变。

---

## 0. 不变量（任何实现都不得违反）

1. **模型不是安全边界**。能力由封闭工具注册表 + 权限谓词决定，提示词只影响语气。
2. **写操作只出提案**。模型对写工具的调用不落任何业务数据；执行只发生在用户确认之后。
3. **确认必须双人**。运维写工具（文件/DB/SSH）在确认时校验**另一位管理员**的 6 位动态码；
   operator 与 approver 不得是同一人（`util.dual_auth.require_dual_auth` 已保证）。
4. **批准的就是执行的**。`ARGS_PAYLOAD` 全文 + `ARGS_HASH` 覆盖全文；确认时重算失败 → 409，
   一次都不执行。`ARGS_SUMMARY` 只用于展示，永不参与校验。
5. **权限每次现查**。工具执行前（读）与提案确认时（写）都要走 `ops_auth.ensure_system_admin`：
   `per` 含 admin → 查库 → `ban_is_active` → 重新算权限。不信 token 快照。
6. **审计是旁路，但拒绝也要有行**。`AI_CALL_LOGS` 每一轮一行；门禁/preflight 拒绝同样一行
   （`status='rejected'`）。审计写失败只 warning，不改变业务结论。
7. **正文不落审计**。`AI_AGENT_EVENTS.RESULT_SUMMARY` ≤500 字符且必须经 `redact()`：
   文件正文、SQL 参数值、命令输出只在 `PREVIEW`（提案期）与响应体里出现，不复制进事件摘要。
8. **提示词前缀稳定**。服务端 system 段版本化并固定在最前；`tools` 按 name 排序；
   动态内容（工具结果、用户消息）只追加在尾部。前缀一变，缓存全灭。

---

## 1. 线上契约（冻结）

前缀 `/api/ai/agent`，鉴权 `Depends(get_current_user)` + `_has_ai_access`。
成功裸 JSON，失败 `{"error":{message,type,code}}` + `X-AI-Request-Id`。

### 1.1 `POST /run`

```json
{"messages":[{"role":"user","content":"看看 /var/log 最近改了哪些文件"}],"model":"glm-4.6v-flash"}
```

- `messages`：1–50 条，`role ∈ {user, assistant}`。**`system` 由服务端注入，客户端传 → 400
  `AI_AGENT_BAD_ARGUMENTS`**（缓存前缀与行为护栏都归服务端）。
- `model` 可省；显式指定必须在当前用户可见目录内，否则 404 `model_not_found`。

响应：

```json
{
  "request_id": "hex32",
  "content": "最终回答",
  "stop_reason": null,
  "rounds": 3,
  "usage": {"prompt_tokens": 1200, "completion_tokens": 300, "total_tokens": 1500,
            "cache_read_tokens": 1024, "cache_write_tokens": 0},
  "events": [{"round_no":1,"tool_name":"fs_list","tool_kind":"read",
              "args_summary":"{\"path\":\"/var/log\"}","result_summary":"…",
              "status":"executed"}],
  "proposals": [{
    "oid":"hex32","tool_name":"fs_write","summary":"写入 12 行到 /etc/x.conf",
    "args_summary":"{\"path\":\"/etc/x.conf\",\"content\":\"…\"}",
    "preview":{"kind":"diff","path":"/etc/x.conf","diff":"@@ -1,3 +1,4 @@ …","truncated":false},
    "expires_at":"2026-10-25 12:40:00",
    "requires_dual_auth": true
  }]
}
```

- `stop_reason ∈ {null, "max_iterations", "token_budget", "timeout", "cancelled"}`。
- `rounds` = 实际发生的上游调用轮数；`usage` = 全轮累计（`null` 表示上游未报）。
- 单次 run 的 wall-clock 上限 `AI_AGENT_RUN_TIMEOUT_SECONDS`（默认 300s）；超时**不丢已有结果**：
  返回当前 `content`（可能为 ""）+ `stop_reason="timeout"`，并落一行 `status='timeout'` 审计。

### 1.2 `POST /proposals/{oid}/confirm`

```json
{"totp_code":"123456"}
```

- `totp_code`：工具 `requires_dual_auth=true` 时必填（运维写工具全部为 true；活动域两条写工具保持 false，
  不改变既有行为）。缺失 → 422 `AI_AGENT_TOTP_REQUIRED`；格式错 → 422；码错/已被用/无第二管理员 → 403/409，
  与 `dual_auth` 的既有语义逐字一致（翻译 `DualAuthError`）。
- **30 分钟授权缓存**：首次校验通过后，同一 `operator` 的同一 `action`（`agent_write`）在
  `DUAL_AUTH_CACHE_TTL_SECONDS`（默认 1800，`0` = 关闭）内**不必再输码**；此时候略 `totp_code` 直接 200。
  缓存命中写一条 `system_dual_auth` 审计并带 `cached=true`。换人/换 action 不继承。
  响应与 `GET /proposals` 必须回 `dual_auth_cached`（布尔）让前端决定是否弹码框。
- 成功 → `{"status":"confirmed","result_summary":"…","approver_oid":"…","dual_auth_cached":false}`。
- 状态机：`proposed → executing → confirmed | failed`；驳回：`proposed → rejected`；
  过期：`proposed → expired`（**CAS**，输掉竞态不得覆盖终态）。
- 并发确认：CAS 失败 → 409 `AI_AGENT_PROPOSAL_CONFLICT`。

### 1.3 `POST /proposals/{oid}/reject`

无请求体；语义同上，`proposed → rejected`。

### 1.4 新增查询端点（本批必须）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/proposals?status=proposed&limit=20` | 本人待确认提案（按 `created_at` 倒序；过期行顺手落 `expired`） |
| GET | `/runs/{request_id}` | 一次 run 的事件时间线（`list_for_request`），用于刷新后回看 |

`GET /proposals` 的 `limit ≤ 50`，只查本人（`actor_oid`）。

---

## 2. `ToolDef` v2（`services/agent/tools.py`）

```python
@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    kind: str                                   # READ | WRITE
    params: dict                                # JSON Schema
    permission: Callable[[dict], bool]          # 纯函数，快速否决
    impl: Callable[..., Awaitable[Any]]         # 读：执行；写：执行（apply）
    prepare: Callable[..., Awaitable["ProposalDraft"]] | None = None  # 仅写：生成预览/摘要
    guard: Callable[[dict], Awaitable[None]] | None = None            # 现查库权限闸（运维工具必填）
    dual_auth_action: str | None = None         # 非空 → 确认时必须 TOTP
```

- 注册校验：`READ` 不得声明 `prepare`/`dual_auth_action`；`WRITE` 必须有 `impl`；
  运维写工具必须同时有 `prepare` 与 `guard`。
- `tool_specs()` 改为**按 name 排序**输出（缓存前缀稳定，不随注册顺序漂移）。
- 现有 10 个活动域工具签名不变；`prepare=None` 时提案预览回落
  `{"kind":"args","args":{...}}`，`requires_dual_auth=false`。

`ProposalDraft`（`services/agent/proposal.py`）：

```python
@dataclass(frozen=True)
class ProposalDraft:
    summary: str          # ≤200 字符，人读的一句话
    preview: dict         # 结构化预览，直接回给前端并落 PREVIEW（CLOB）
```

---

## 3. 提案 v2（`services/agent/proposal.py` + V034）

新增列（`ai_agent_events`）：

| 列 | 类型 | 语义 |
|---|---|---|
| `ARGS_PAYLOAD` | `CLOB` | 完整可复原参数（canonical JSON）；`ARGS_HASH` 覆盖它 |
| `PREVIEW` | `CLOB` | 确认界面用的结构化预览（diff / 命令 / SQL / 行变更） |
| `APPROVER_OID` | `VARCHAR2(32)` | 双人码审批人；活动域无 TOTP 时为 NULL |
| `CONFIRMED_AT` | `TIMESTAMP` | 实际执行时间 |

- `ARGS_SUMMARY` 保留 ≤500，**仅展示**：`ARGS_PAYLOAD` 超过 500 时 summary 做截断并加 `…`，
  不再拒绝建提案（原 `AI_AGENT_ARGS_TOO_LARGE` 删除）。
- 兼容旧行：`ARGS_PAYLOAD IS NULL` 时回落到 `ARGS_SUMMARY` 校验（活动域已落库的提案不失效）。
- `status` CHECK 增加 `executing`（V034 先 `DROP CONSTRAINT` 再重建，幂等吞错）。
- 状态迁移全部走 `WHERE status = <预期>` 的 CAS；`_mark_expired` 必须改 CAS。

---

## 4. 运维工具清单（新增，全部落在 `services/agent/ops_tools.py`）

权限：`permission=_admin_only`（`per` 含 admin）+ `guard=ops_auth.ensure_system_admin`。
读写分界与既有 REST 完全一致；参数上限取 `services/file_ops.py` / `db_content.py` 的常量，不另立。

### 4.1 读（自动执行，仍落事件）

| 工具 | 参数 | 实现 | 结果上限 |
|---|---|---|---|
| `fs_list` | `path`, `limit=100`(1–200), `offset=0` | `file_ops.list_dir` | 100 条、总 8KB |
| `fs_stat` | `path` | `file_ops.stat_entry` | 8KB |
| `fs_read` | `path`, `max_bytes=8000`(≤32768) | `file_ops.preview_file`，仅文本 | 8KB |
| `db_schema` | — | `db_content.list_schema` | 200 表 |
| `db_columns` | `table` | `db_content.get_columns` | 8KB |
| `db_rows` | `table`, `limit=50`(≤200), `offset=0`, `order_by?`, `desc=false` | `db_content.read_rows` | 50 行 |
| `db_query` | `sql`, `limit=100`(≤500) | `validate_readonly_sql` + `db_content.run_query` | 100 行、30s |

`db_*` 一律 `async with session_scope() as s:`。

### 4.2 写（提案 + TOTP）

| 工具 | 参数 | 预览 | 执行 |
|---|---|---|---|
| `fs_write` | `path`, `content`, `encoding="utf-8"` | 统一 diff（`difflib.unified_diff`，超 40KB 截断并标 `truncated`）；新文件 `kind="new_file"` | `file_ops.write_text` |
| `fs_mkdir` | `path` | `{kind:"mkdir",path}` | `file_ops.make_dir` |
| `fs_rename` | `source`, `target` | `{kind:"rename",from,to}` | `file_ops.rename_path` |
| `fs_delete` | `path`, `recursive=false` | `{kind:"delete",path,entries,entries_truncated}`（条目数 >10000 由服务层拒绝） | `file_ops.delete_path` |
| `fs_chmod` | `path`, `mode`(0–511) | `{kind:"chmod",path,from,to}`（八进制字符串） | `file_ops.chmod_path` |
| `db_insert` | `table`, `values`(object) | `{kind:"row_insert",table,columns,values}` | `db_content.insert_row` |
| `db_update` | `table`, `key`(object), `values`(object) | `{kind:"row_update",table,key,values}` | `db_content.update_row` |
| `db_delete` | `table`, `key`(object) | `{kind:"row_delete",table,key}` | `db_content.delete_row` |
| `db_ddl` | `sql`, `confirm?` | `{kind:"sql",sql,summary,verb,target,destructive}`（`validate_ddl_sql` 先行校验，非法直接 `AgentError`） | `db_content.run_ddl` |
| `ssh_exec` | `command`(≤1024), `timeout=30`(≤60) | `{kind:"command",target:"root@127.0.0.1:22",command}` | `agent.ssh_exec.run_once` |

**SSH 硬约束**：目标只来自 `ssh_bridge.load_target()`，客户端无法指定；单次 `conn.run`，
`check=False`；输出 8KB 截断（stdout/stderr 分别计）；每次执行写一条
`audit_system_op(action="agent_ssh_exec", log_type="system_ssh_command")`，detail 只含命令与退出码。

**敏感值**：`db_*.values` 与 `ssh_exec.command` 允许出现在 `PREVIEW` 与 `ARGS_PAYLOAD`（确认与重放需要），
但 `ARGS_SUMMARY` / `RESULT_SUMMARY` 必须脱敏：`ops_tools.redact()` 抹掉 `password|secret|token|key`
键值、`--password=` 形态、以及超过 200 字符的字符串（换成长度标注）。

---

## 5. 迁移 V034

`db/oracle/ddl/V034__agent_proposal_payload.sql`：

- `ALTER TABLE ai_agent_events ADD (args_payload CLOB, preview CLOB, approver_oid VARCHAR2(32), confirmed_at TIMESTAMP)`（幂等吞 -1430/-955）。
- `DROP CONSTRAINT ck_ai_agent_events_status` + 重建含 `executing`（吞 -2264/-2443/-955）。
- 同步：`db/models.py`、`V001__initial_schema.sql`、`tests/test_ai_ddl_mirror.py`、
  `db/repos/oracle/ai_agent_events.py` 的列投影与 insert/update、`db/repos/protocols.py`、`db/backends.py`。

`db/oracle/ddl/README.md` 增一行；**不得**再改 V032/V033（已定为增量基线）。

---

## 6. 提示词与缓存

`services/agent/prompt.py`：

```python
PROMPT_VERSION = "ops-v1"
SYSTEM_PROMPT = f"[{PROMPT_VERSION}] 你是 zvp 平台运维助手 …（能力边界、只读优先、写操作必须提案、
不得推断未读取的文件内容、路径必须绝对、禁止声称已执行未提案的操作）"
```

- `executor.run` 组装：`[system(PROMPT_VERSION)] + 客户端 messages`。
- `_call_upstream` 内对每个候选 entry 调 `prompt_cache.request_body(payload, entry)`，
  返回 `cache_key` 并透传进 `_audit_round(cache_key=...)`。
- 预算/用量：`total_tokens is None` 时按 `(prompt_tokens or 0) + (completion_tokens or 0)` 累计；
  两者都无 → 按 `prompt_chars/4 + completion_chars/4` 估算，保证预算闸永不为 0。

---

## 7. 车道与文件白名单（互不重叠）

| 车道 | 独占文件 |
|---|---|
| A 核心 | `services/agent/{tools,proposal,executor,prompt}.py`、`routers/ai_agent_router.py`、`db/models.py`、`db/oracle/ddl/V034*.sql`、`V001`、`db/oracle/ddl/README.md`、`db/repos/oracle/ai_agent_events.py`、`db/repos/protocols.py`、`db/backends.py`、`tests/test_agent_{tools,proposals,executor}.py`、`tests/test_ai_ddl_mirror.py`、`tests/test_ban_rule_crud.py` |
| B 运维工具 | `services/agent/{ops_tools,ops_auth,ssh_exec}.py`、`services/agent/__init__.py`、`services/ssh_bridge.py`（仅新增 `connect()`）、`tests/test_agent_ops_tools.py` |
| C 前端 | `src/views/ai/**`、`src/components/ai/**`、`src/api/ai/agent.ts`、`types/ai.d.ts`、`src/i18n/locales/ai.ts` |
| D 集成 | 主会话：跑验证、修跨车道缝隙 |

**禁止**：任何车道改 `services/ai/*`；B 不碰 A 的文件；C 不碰后端。

---

## 8. 验收

后端：
```bash
.venv/bin/python -m pytest -q tests/test_agent_tools.py tests/test_agent_proposals.py \
  tests/test_agent_executor.py tests/test_agent_ops_tools.py tests/test_ai_ddl_mirror.py
.venv/bin/python -m pytest -q                      # 全量，已知 2 条 umask 假失败（0o664 vs 0o644）
.venv/bin/python -m db.ddl_check                   # no drift
```
前端：
```bash
npx vue-tsc --noEmit && npx eslint src/views/ai src/components/ai src/api/ai
npx vitest run src/components/ai src/api/ai
```

功能（离线桩，不打真实上游）：读工具端到端、写工具提案 → TOTP 确认 → 执行、错码 403、
重放 409、过期 410、并发确认 409、`stop_reason` 三态、拒绝路径有审计行。
