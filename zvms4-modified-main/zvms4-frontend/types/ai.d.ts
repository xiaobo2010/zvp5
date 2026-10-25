// AI 网关（/api/ai）的类型定义。
// 字段名与后端 routers/ai_router.py、services/ai/* 的响应视图逐字对齐，
// 前端不得自造字段名。

export type AiKeyScope = 'personal' | 'shared'

export type AiOcrEngineKind = 'tencent' | 'aliyun' | 'baidu' | 'rapidocr' | 'groq' | 'zhipu'

export type AiQuotaKind = 'ocr' | 'chat'

export type AiLogCapability = 'ocr' | 'chat'

export type AiLogStatus = 'success' | 'error' | 'timeout' | 'rejected' | 'busy'

export type AiLogKeyScope = 'personal' | 'shared' | 'server'

export interface AiKeyRecord {
  oid: string
  scope: AiKeyScope
  owner_oid: string
  name: string
  kind: string
  base_url: string
  models: string[]
  /** 头名 → 恒为 ****，服务端不回显头值。 */
  headers: Record<string, string>
  api_path: string | null
  /** 密钥末四位提示，不含完整密钥。 */
  key_hint: string | null
  enabled: boolean
  /** 到期时间（ISO 字符串）；null 表示永不过期。 */
  expires_at: string | null
  created_at: string | null
  updated_at: string | null
  /** personal 且属于当前管理员时为 true。 */
  owned: boolean
}

export interface AiKeyCreateInput {
  scope: AiKeyScope
  name: string
  base_url: string
  models?: string[]
  api_key: string
  enabled?: boolean
  headers?: Record<string, string>
  api_path?: string
  /** 到期时间（ISO 字符串）；不传表示永不过期。 */
  expires_at?: string | null
}

export interface AiKeyUpdateInput {
  name?: string
  base_url?: string
  models?: string[]
  /** 传入即轮换密钥，不传表示保持不变。 */
  api_key?: string
  enabled?: boolean
  headers?: Record<string, string>
  api_path?: string
  /** 显式传 null 表示清除到期，不传表示保持不变。 */
  expires_at?: string | null
}

/** 调用日志的一行（/ai/logs）。仅 admin 可读。 */
export interface AiLogRecord {
  oid: string
  user_oid: string
  user_name: string
  capability: AiLogCapability
  endpoint: string
  provider: string | null
  model: string | null
  status: AiLogStatus
  http_status: number
  error_code: string | null
  latency_ms: number | null
  units: number
  input_bytes: number | null
  request_id: string | null
  key_scope: AiLogKeyScope | null
  /** 上游 usage 报的三个 token 数；全 null = 这次没报（units 是估算值）。 */
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  /** 同一次请求里的第几次尝试（1 起）；null = 不是某次上游尝试（门禁直接拒）。 */
  attempt_no: number | null
  /** 调用场景（chat / markdown-assistant / ocr）；null = 来源未知（第三方 SDK）。 */
  purpose: string | null
  /** 流式首块到达耗时（毫秒）；非流式为 null。 */
  first_token_ms: number | null
  /** OCR 引擎类型（tencent/aliyun/rapidocr/baidu/groq/zhipu）；OCR 行才有。 */
  engine_kind: string | null
  /** OCR 针对的附件 oid。 */
  attachment_oid: string | null
  created_at: string
}

/** 按日聚合的一行（/ai/logs/stats 的 daily）。 */
export interface AiLogDailyStat {
  /** 本地自然日 YYYY-MM-DD。 */
  date: string
  calls: number
  errors: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  avg_latency_ms: number | null
}

/** 按某个维度（provider / model / purpose）的小计。 */
export interface AiLogGroupStat {
  calls: number
  errors: number
  units: number
  total_tokens: number
}

export interface AiLogProviderStat extends AiLogGroupStat {
  provider: string | null
}

export interface AiLogModelStat extends AiLogGroupStat {
  model: string | null
}

export interface AiLogPurposeStat extends AiLogGroupStat {
  purpose: string | null
}

/** 按用户的小计（/ai/logs/stats 的 by_user，top 50）：谁在花钱。 */
export interface AiLogUserStat extends AiLogGroupStat {
  user_oid: string
  /** 显示名来自 LEFT JOIN USERS；账号已删/查不到时后端回落到 oid 字符串。 */
  name: string
  avg_latency_ms: number | null
}

/** 按状态的小计（/ai/logs/stats 的 by_status）：把 errors 拆成逐状态计数。 */
export interface AiLogStatusStat {
  /** 未知状态/脏数据回落到 null。 */
  status: AiLogStatus | null
  calls: number
}

/** /ai/logs/stats 的响应 data。 */
export interface AiLogStats {
  days: number
  /** 窗口起点（本地时间 ISO 字符串）。 */
  since: string
  daily: AiLogDailyStat[]
  by_provider: AiLogProviderStat[]
  by_model: AiLogModelStat[]
  by_purpose: AiLogPurposeStat[]
  by_user: AiLogUserStat[]
  by_status: AiLogStatusStat[]
}

/** /ai/prompts 列表的一行：元数据，**不含正文**。 */
export interface AiPromptAuditRecord {
  oid: string
  /** 账号被删时后端把 user_oid 置空，名字回落成「未知用户」。 */
  user_oid: string | null
  user_name: string
  purpose: string | null
  /** 留痕时刻的角色快照（逗号分隔的角色标识）。 */
  roles_snapshot: string | null
  /** 正文长度（后端 LENGTH），用来判断这条值不值得点开。 */
  prompt_chars: number
  created_at: string
}

/** /ai/prompts/{oid} 的详情：元数据 + 请求编号 + 正文（每次读取都会写访问审计）。 */
export interface AiPromptAuditDetail {
  oid: string
  user_oid: string | null
  user_name: string
  request_id: string | null
  purpose: string | null
  roles_snapshot: string | null
  /** 已脱敏、截断后的正文；null 表示库里就是空的。 */
  prompt_text: string | null
  created_at: string
}

export interface AiOcrEngineRecord {
  oid: string
  name: string
  kind: AiOcrEngineKind
  config: Record<string, unknown>
  key_hint: string | null
  /** 是否已录入凭证（只透露有无，不回显内容）。 */
  has_secret: boolean
  enabled: boolean
  priority: number
  created_at: string | null
  updated_at: string | null
}

export interface AiOcrEngineCreateInput {
  name: string
  kind: AiOcrEngineKind
  config?: Record<string, unknown>
  secret?: Record<string, string> | null
  enabled?: boolean
  priority?: number
}

export interface AiOcrEngineUpdateInput {
  name?: string
  config?: Record<string, unknown>
  secret?: Record<string, string> | null
  enabled?: boolean
  priority?: number
}

export interface AiQuotaEntry {
  /** 计费周期，形如 202610。 */
  period: string
  used: number
  limit: number
}

export interface AiQuotaSnapshot {
  ocr: AiQuotaEntry
  chat: AiQuotaEntry
}

export interface AiQuotaMutation {
  period: string
  kind: AiQuotaKind
  used: number
  limit: number
}

export interface AiProviderView {
  name: string
  kind: string
  /** 只含主机，不含完整路径与凭据。 */
  base_url: string
  models: string[]
  configured: boolean
}

export interface AiSharedEntry {
  name: string
  base_url: string
  models: string[]
  enabled: boolean
}

export interface AiHealthData {
  enabled: boolean
  /** 本地引擎（rapidocr）预热是否就绪。 */
  local_engine_ready?: boolean
  max_concurrency: number
  providers: AiProviderView[]
  /** 供应商 → 熔断剩余秒数。 */
  cooldown_seconds: Record<string, number>
  models: string[]
  quota_defaults: { ocr: number; chat: number }
  credentials: {
    personal: number
    shared: number
    server: number
    shared_entries: AiSharedEntry[]
  }
}

export interface AiModelItem {
  id: string
  object: string
  owned_by: string
}

export interface AiModelList {
  object: string
  data: AiModelItem[]
}

export type AiChatRole = 'system' | 'user' | 'assistant'

export interface AiChatMessage {
  role: AiChatRole
  content: string
}

/** POST /ai/ocr 的受理结果：202 新入队带 engine，200 幂等不带 engine。 */
export interface AiOcrEnqueueResult {
  status: 'pending' | 'processing'
  attachment_oid: string
  engine?: string
}

/** 工具类别：读工具直接执行，写工具只出提案。 */
export type AiAgentToolKind = 'read' | 'write'

/** 一次工具调用的终态（服务端 executor 里显式给出的那个值）。 */
export type AiAgentEventStatus = 'executed' | 'proposed' | 'denied' | 'failed'

/** `POST /ai/agent/run` 的 events[]：一次工具调用一行留痕。 */
export interface AiAgentEvent {
  round_no: number
  /** 白名单外的工具名也会出现在这里（status=denied）。 */
  tool_name: string | null
  /** 未知工具没有 kind（null）。 */
  tool_kind: AiAgentToolKind | null
  /** 模型给的参数（紧凑 JSON 文本，已按 500 字截断）。 */
  args_summary: string | null
  /** 执行结果摘要（已截断）；提案事件里是「等待确认」之类的状态文本。 */
  result_summary: string
  status: AiAgentEventStatus
}

/* ---------------------------------------------------------------------------
   提案预览（proposal preview）
   服务端 prepare 阶段产出的结构化预览，直接回给前端渲染确认界面：
   diff / new_file / mkdir / rename / delete / chmod / row_insert / row_update /
   row_delete / sql / command；活动域写工具没有 prepare，回落 kind='args'。
   --------------------------------------------------------------------------- */

/** 预览的判别标签。 */
export type AiAgentPreviewKind =
  | 'diff'
  | 'new_file'
  | 'mkdir'
  | 'rename'
  | 'delete'
  | 'chmod'
  | 'row_insert'
  | 'row_update'
  | 'row_delete'
  | 'sql'
  | 'command'
  | 'args'

/** 统一 diff（fs_write 改已有文件）：path 是目标路径，diff 是 unified diff 文本。 */
export interface AiAgentPreviewDiff {
  kind: 'diff'
  path: string
  diff: string
  /** 差异超过 40KB 被截断。 */
  truncated?: boolean
}

/** 新建文件（fs_write 目标不存在）：content 是文件正文。 */
export interface AiAgentPreviewNewFile {
  kind: 'new_file'
  path: string
  content: string
  truncated?: boolean
}

/** 新建目录（fs_mkdir）。 */
export interface AiAgentPreviewMkdir {
  kind: 'mkdir'
  path: string
}

/** 重命名 / 移动（fs_rename）。 */
export interface AiAgentPreviewRename {
  kind: 'rename'
  from: string
  to: string
}

/** 删除（fs_delete）：entries 是将被删掉的条目名。 */
export interface AiAgentPreviewDelete {
  kind: 'delete'
  path: string
  entries: string[]
  /** 条目过多，列表被截断。 */
  entries_truncated?: boolean
}

/** 改权限（fs_chmod）：from / to 都是八进制字符串（如 "644"）。 */
export interface AiAgentPreviewChmod {
  kind: 'chmod'
  path: string
  from: string
  to: string
}

/** 插入行（db_insert）：columns 是列名，values 是对应值。 */
export interface AiAgentPreviewRowInsert {
  kind: 'row_insert'
  table: string
  columns: string[]
  values: Record<string, unknown>
}

/** 更新行（db_update）：key 是主键条件，values 是要改的列。 */
export interface AiAgentPreviewRowUpdate {
  kind: 'row_update'
  table: string
  key: Record<string, unknown>
  values: Record<string, unknown>
}

/** 删除行（db_delete）：key 是主键条件。 */
export interface AiAgentPreviewRowDelete {
  kind: 'row_delete'
  table: string
  key: Record<string, unknown>
}

/** DDL（db_ddl）：destructive 为真表示 DROP / TRUNCATE 这类破坏性语句。 */
export interface AiAgentPreviewSql {
  kind: 'sql'
  sql: string
  summary: string
  /** 语句动词（CREATE / ALTER / DROP ...）。 */
  verb: string
  /** 作用对象（表名等）。 */
  target: string
  destructive: boolean
}

/** 远程命令（ssh_exec）：target 由服务端 ssh_bridge 决定，客户端不能指定。 */
export interface AiAgentPreviewCommand {
  kind: 'command'
  target: string
  command: string
}

/** 活动域写工具的回落预览（prepare 为空时）。 */
export interface AiAgentPreviewArgs {
  kind: 'args'
  args: Record<string, unknown>
}

/** 提案预览的联合类型；按 kind 判别。 */
export type AiAgentPreview =
  | AiAgentPreviewDiff
  | AiAgentPreviewNewFile
  | AiAgentPreviewMkdir
  | AiAgentPreviewRename
  | AiAgentPreviewDelete
  | AiAgentPreviewChmod
  | AiAgentPreviewRowInsert
  | AiAgentPreviewRowUpdate
  | AiAgentPreviewRowDelete
  | AiAgentPreviewSql
  | AiAgentPreviewCommand
  | AiAgentPreviewArgs

/** 提案状态机：proposed → executing → confirmed | failed；另有 rejected / expired。 */
export type AiAgentProposalStatus =
  | 'proposed'
  | 'executing'
  | 'confirmed'
  | 'rejected'
  | 'expired'
  | 'failed'

/** `POST /ai/agent/run` 的 proposals[]：待用户确认的写操作。 */
export interface AiAgentProposal {
  /** 后续 confirm / reject 的句柄（32 位 hex）。 */
  oid: string
  tool_name: string
  args_summary: string
  /** 结构化预览（确认界面用）；没有预览的提案为 null。 */
  preview: AiAgentPreview | null
  /** 服务端本地时间，形如 `2026-10-25 12:10:00`（默认 10 分钟有效）。 */
  expires_at: string
  /** 确认时是否需要另一位管理员的 6 位动态码（运维写工具为 true）。 */
  requires_dual_auth: boolean
  /**
   * 30 分钟双人授权窗口是否已命中。
   *
   * true 表示该 operator 的写权限在窗口内已被另一位管理员批准过，
   * 确认时直接执行、不再要动态码。
   */
  dual_auth_cached: boolean
}

/** run 的终止原因；null = 正常跑完。 */
export type AiAgentStopReason = 'max_iterations' | 'token_budget' | 'timeout' | 'cancelled'

/** 全轮累计 token 用量；上游没报时各字段为 null。 */
export interface AiAgentUsage {
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  cache_read_tokens: number | null
  cache_write_tokens: number | null
}

/** `POST /ai/agent/run` 的响应：一次 run 的全部留痕（后端不推流）。 */
export interface AiAgentRunResponse {
  request_id: string
  content: string
  /** 触顶终止的原因；null = 正常结束。 */
  stop_reason: AiAgentStopReason | null
  /** 实际发生的上游调用轮数。 */
  rounds: number
  /** 全轮累计 token；null = 上游没报。 */
  usage: AiAgentUsage | null
  events: AiAgentEvent[]
  proposals: AiAgentProposal[]
}

/** `GET /ai/agent/proposals` 的响应：本人待确认提案。 */
export interface AiAgentProposalListResponse {
  proposals: AiAgentProposal[]
}

/** `GET /ai/agent/runs/{request_id}` 的响应：一次 run 的事件回放。 */
export interface AiAgentRunReplay {
  request_id: string
  events: AiAgentEvent[]
  /** 后端若一并回带提案则给全；没有该字段时按空处理。 */
  proposals?: AiAgentProposal[]
}

/** confirm / reject 的响应：终态 + 执行结果摘要（驳回没有结果）。 */
export interface AiAgentProposalResult {
  status: string
  result_summary?: string
  /** 双人码审批人（requires_dual_auth 的提案才有）。 */
  approver_oid?: string
  /** 本次确认是否命中了 30 分钟双人授权窗口（确认时才有）。 */
  dual_auth_cached: boolean
}
