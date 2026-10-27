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

/* ---------------------------------------------------------------------------
   持久会话（/ai/conversations）
   契约见 docs/AI_CONVERSATION_DESIGN.md §3。对话与智能体共用一套会话存储：
   conversation 是容器，message 是会话内按 seq 单调递增的一条，run 是一次
   后台执行的智能体任务（run_id 同时是 ai_call_logs / ai_agent_events 的
   request_id）。
   --------------------------------------------------------------------------- */

/** 会话类型：建会话时定，之后不可改。 */
export type AiConversationMode = 'chat' | 'agent'

/** 会话状态：active 在列表里，archived 是收起来的（保留期到期才硬删）。 */
export type AiConversationStatus = 'active' | 'archived'

/** 会话级累计用量（ai_conversations.tokens_*）。 */
export interface AiConversationTokens {
  prompt: number
  completion: number
  total: number
}

/** 上下文水位：最近一次请求的 prompt token 与该模型的上下文上限。 */
export interface AiConversationContext {
  tokens: number
  limit: number
  /** 占用比例（0–1）；上游没报时给 0。 */
  ratio: number
}

/** 压缩点：≤ upto_seq 的消息不进模型上下文，只进摘要。 */
export interface AiConversationCompaction {
  upto_seq: number
  /** 是否压过（upto_seq > 0 即为 true，这里冗余一份方便直接渲染）。 */
  summarized: boolean
  /** 最近一次压缩降级（硬截断）的时间；null = 正常。 */
  degraded_at: string | null
}

/** 一个会话的视图（列表与详情共用；列表不含消息正文与活跃 run）。 */
export interface AiConversation {
  oid: string
  mode: AiConversationMode
  title: string
  /** 最近使用的模型；null = 服务端默认。 */
  model: string | null
  status: AiConversationStatus
  tokens: AiConversationTokens
  context: AiConversationContext
  compaction: AiConversationCompaction
  message_count: number
  created_at: string
  updated_at: string
}

/** 会话列表的一页：游标分页（cursor 是 base64 的 updated_at|oid）。 */
export interface AiConversationPage {
  conversations: AiConversation[]
  /** 下一页游标；null = 没有更多了。 */
  next_cursor: string | null
  /** 本页条数（metadata.size）。 */
  size: number
}

/** 会话里的消息角色；摘要不落成消息（只存在会话行的 summary_upto_seq）。 */
export type AiMessageRole = 'user' | 'assistant' | 'tool'

/** 单条消息那一轮的 token 用量；上游没报时各字段为 null。 */
export interface AiMessageUsage {
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  cache_read_tokens: number | null
  cache_write_tokens: number | null
}

/** 会话内的一条消息（按 seq 排序渲染）。 */
export interface AiMessage {
  oid: string
  /** 会话内单调递增；压缩点就是拿它与 compaction.upto_seq 比。 */
  seq: number
  role: AiMessageRole
  content: string
  /** assistant 的 tool_calls 原文；其它角色为 null。 */
  tool_calls?: unknown
  /** role='tool' 时对应那次调用。 */
  tool_call_id?: string | null
  /** role='tool' 时工具名（折叠标题用）。 */
  tool_name?: string | null
  /** 所属 run 的轮次（1 起）。 */
  round_no?: number | null
  /** 产生该消息的 run id（= ai_call_logs.request_id）。 */
  request_id?: string | null
  usage?: AiMessageUsage | null
  created_at: string
}

/** 消息的一页：after_seq 增量拉取。 */
export interface AiMessagePage {
  messages: AiMessage[]
  /** 还有更早的消息（用最后一条的 seq 再拉一次）。 */
  has_more: boolean
  /** 会话内消息总数。 */
  total: number
}

/** 一次后台执行的状态。 */
export type AiRunStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'interrupted'

/** run 的终止原因（succeeded 且 stop_reason='completed' 表示正常跑完）。 */
export type AiRunStopReason =
  | 'completed'
  | 'max_iterations'
  | 'token_budget'
  | 'timeout'
  | 'cancelled'
  | 'error'

/** 一次后台执行的视图。 */
export interface AiRun {
  /** run_id（同时是 ai_call_logs.request_id）。 */
  oid: string
  conversation_oid: string
  status: AiRunStatus
  /** 本次使用的模型；null = 服务端默认。 */
  model: string | null
  stop_reason: AiRunStopReason | null
  /** 已完成的轮数。 */
  rounds: number
  error_code: string | null
  /** 人话错误（已脱敏）。 */
  error_message: string | null
  /** 最终 assistant 消息的 oid。 */
  result_message_oid: string | null
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  /** 已请求中止（worker 在轮边界检查）。 */
  cancel_requested: boolean
  created_at: string
  started_at: string | null
  finished_at: string | null
}

/** 提交 run 的受理结果（202）。 */
export interface AiRunSubmitResult {
  run_id: string
  /** 提交时落库的那条用户消息。 */
  message_oid: string
  status: 'queued'
  /** 命中 client_msg_id 幂等键时为 true：没有重复写消息、没有重复计费。 */
  deduped?: boolean
}

/** 一次 run 的详情：状态 + 增量事件 + 提案。 */
export interface AiRunDetail {
  run: AiRun
  /** 只含 round_no > after_round 的增量。 */
  events: AiAgentEvent[]
  proposals: AiAgentProposal[]
}

/** 硬删会话的结果。 */
export interface AiConversationDeleteResult {
  deleted: boolean
  /** 一并删掉的消息数。 */
  messages: number
  /** 一并删掉的运行记录数。 */
  runs: number
}

/* ---------------------------------------------------------------------------
   定时任务（/ai/scheduled-tasks）
   契约见后端 routers/ai_schedule_router.py 与 services/ai/scheduled_runner.py。
   字段名与后端 _view / _run_view 逐字对齐，前端不自造字段。
   --------------------------------------------------------------------------- */

/** 排程方式：固定间隔或 5 段式 crontab。 */
export type AiScheduledScheduleKind = 'interval' | 'cron'

/**
 * 审核状态。
 *
 * draft 由「改了提示词」产生（上一轮的审核结论已失效）；
 * pending 是新建后的初始状态；只有 approved 会被调度器真正执行。
 */
export type AiScheduledReviewStatus = 'draft' | 'pending' | 'approved' | 'rejected'

/** 审核裁决。 */
export type AiScheduledReviewDecision = 'approved' | 'rejected'

/**
 * 最近一次执行结论。
 *
 * queued = 已入队；failed = 执行出错；
 * skipped_* = 前置检查拦下的那一轮（未审核 / 正在跑 / 没有可用凭据 /
 * 额度用尽 / 所有者不可用 / 内容被规则拦截），后端在 last_error 里写明原因。
 */
export type AiScheduledLastStatus =
  | 'queued'
  | 'skipped_review'
  | 'skipped_busy'
  | 'skipped_no_key'
  | 'skipped_quota'
  | 'skipped_owner'
  | 'skipped_flagged'
  | 'failed'

/** 一个定时任务的视图（列表、创建、更新、审核共用同一形状）。 */
export interface AiScheduledTask {
  oid: string
  owner_oid: string
  name: string
  prompt_text: string
  review_status: AiScheduledReviewStatus
  reviewed_by_oid: string | null
  reviewed_at: string | null
  review_note: string | null
  schedule_kind: AiScheduledScheduleKind
  interval_minutes: number
  cron_expr: string | null
  enabled: boolean
  /** null = 用服务端默认模型。 */
  model: string | null
  last_run_at: string | null
  next_run_at: string | null
  run_count: number
  last_status: AiScheduledLastStatus | null
  /** 最近一次失败或跳过的原因（后端截断到 512 字）。 */
  last_error: string | null
  created_at: string | null
  updated_at: string | null
}

/** 建任务：审核状态由服务端决定（创建即 pending），前端不传。 */
export interface AiScheduledTaskCreateInput {
  name: string
  prompt_text: string
  schedule_kind: AiScheduledScheduleKind
  interval_minutes: number
  cron_expr?: string | null
  model?: string | null
}

/** 部分更新：只传要改的字段；改 prompt_text 会把审核结论打回 draft。 */
export interface AiScheduledTaskUpdateInput {
  name?: string
  prompt_text?: string
  schedule_kind?: AiScheduledScheduleKind
  interval_minutes?: number
  cron_expr?: string | null
  model?: string | null
  enabled?: boolean
}

/** 一次执行记录。 */
export interface AiScheduledTaskRun {
  run_id: string
  status: string
  model: string | null
  stop_reason: string | null
  error_code: string | null
  error_message: string | null
  created_at: string | null
  started_at: string | null
  finished_at: string | null
}

/** 立即执行的受理结果。 */
export interface AiScheduledRunNowResult {
  run_id: string
  status: 'queued'
}
