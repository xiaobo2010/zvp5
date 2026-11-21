/**
 * 与后端真实契约对应的领域类型。
 * 字段名以后端实际响应为准（例如通知用 `id`，活动用 `_id`）。
 */

export type UserRole = 'student' | 'secretary' | 'department' | 'admin' | 'system' | 'auditor'

export interface UserProfile {
  _id: string
  id: string
  name: string
  roles: UserRole[]
  class_id?: string
  group?: string[]
  sex?: 'male' | 'female' | 'unknown'
  banned?: boolean
}

/** `GET /v1/users/me/device`：当前请求携带的设备号 + 主设备绑定/封禁状态。 */
export interface MyDeviceState {
  xuehai_id: string
  primary_xuehai_id: string | null
  device_banned: boolean
  device_ban_reason: string | null
  device_ban_until: string | null
}

/** `GET /v1/users/{oid}/totp`：本人当前登录动态码与过期时间（epoch 秒）。 */
export interface TotpCode {
  otp: string
  exp: number
}

export interface LoginResult {
  token: string
  _id: string
  profile?: UserProfile
  weak_password?: boolean
  binding_mismatch?: boolean
  temp_session?: boolean
}

export type TimeMode = 'on-campus' | 'off-campus' | 'social-practice'

export type ActivityStatus = 'pending' | 'effective' | 'refused' | 'draft'

export type UserTime = Record<TimeMode, number>

export interface TimePercentile {
  value: number
  group: number
  grade: number
}

export type UserTimeStatistics = Record<TimeMode, TimePercentile>

export interface Activity {
  _id: string
  type: 'on-campus' | 'off-campus' | 'social-practice' | 'hybrid'
  name: string
  description: string
  date: string
  createdAt: string
  updatedAt: string
  appointee: string
  approver: string
  creator: string
  creator_name?: string
  status: 'pending' | 'effective' | 'refused'
  place: string
  origin:
    | 'labor'
    | 'organization'
    | 'tasks'
    | 'occasions'
    | 'import'
    | 'activities'
    | 'practice'
    | 'club'
    | 'prize'
    | 'other'
    | 'unknown'
}

export interface ActivityMemberRow {
  _id: string
  activity: string
  member: string
  member_name: string
  mode: TimeMode
  status: 'effective' | 'pending' | 'refused'
  duration: number
  user?: UserProfile | null
}

export interface NotificationItem {
  id: string
  title: string
  content: string
  version: number
  content_hash?: string
  author_id?: string
  author_name?: string
  created_at?: string
  updated_at?: string
}

/** 管理端的通知行：多了启用状态，用户侧列表不会返回停用条目。 */
export interface AdminNotification extends NotificationItem {
  active?: boolean
  source_ref?: string | null
}

/** 审计日志行（`GET /v1/logs`）。timestamp 是秒级浮点。 */
export interface AuditLogRow {
  _id: string
  url: string
  user: string | null
  user_name?: string | null
  data: string
  timestamp: number
  type: string
  level: string
  xuehai_id?: string | null
  client_ip?: string | null
  user_agent?: string | null
}

/** 管理端用户行（`GET /v1/users`）：比 UserProfile 多一个班级名与封禁标记。 */
export interface AdminUserRow {
  _id: string
  id: string
  name: string
  roles: UserRole[]
  class_id: string
  class_name: string
  banned: boolean
}

/** 班级或权限组（`GET /v1/groups`）。权限组没有 type 字段时按 name+permissions 判断。 */
export interface GroupRow {
  _id: string
  name: string
  type?: 'class' | 'permission'
  description?: string
  permissions?: string[]
}

/** 班级 / 权限组成员行（`GET /v1/groups/{id}/users`）。 */
export interface GroupMemberRow {
  _id: string
  id: string
  name: string
  roles: UserRole[]
  class_id: string
  class_name: string
  banned: boolean
}

export interface FeaturePolicy {
  features: Record<string, boolean>
  quotas: {
    af: number | null
    schedule: number | null
  }
  unlimited: boolean
}

export interface ActivityPage {
  activities: Activity[]
  total: number
}

export interface UserActivityItem extends Activity {
  mine?: {
    _id: string
    member: string
    status: 'effective' | 'pending' | 'refused'
    mode: TimeMode
    duration: number
  }
}

export interface UserActivityPage {
  activities: UserActivityItem[]
  total: number
}

export interface MemberPage {
  members: ActivityMemberRow[]
  total: number
}

/* ------------------------------------------------------------------ */
/* 运维（`/api/system/*`）：字段名与后端 db_monitor / system_state 一致。 */
/* ------------------------------------------------------------------ */

export interface MaintenanceState {
  enabled: boolean
  /** 秒级 epoch；仅开启时存在。 */
  since: number | null
  announcement: string | null
  /** 维护期间给登录页/维护页的准入提示：当前 token 是否具备 admin。 */
  admin?: boolean
}

export interface DatabaseTableItem {
  name: string
  estimated_rows: number | null
  last_analyzed: string | null
  table_bytes: number
  index_bytes: number
  lob_bytes: number
  lob_index_bytes: number
  total_bytes: number
}

export interface DatabaseTablespaceItem {
  name: string
  used_bytes: number | null
  max_bytes: number | null
  used_percent: number | null
  is_application: boolean
}

export interface DatabasePool {
  available?: boolean
  scope?: string
  size?: number
  checked_out?: number
  checkedout?: number
  overflow?: number
  capacity?: number
  utilization_percent?: number | null
  waited?: number
  reason?: string
}

export interface DatabaseLoadMetric {
  name: string
  value: number | null
  unit: string
  begin_time: string
  end_time: string
  interval_centiseconds: number
  group_id: number
}

export interface DatabaseStatus {
  observed_at?: string
  storage: {
    available: boolean
    tables: {
      available: boolean
      count: number | null
      total_bytes: number | null
      items: DatabaseTableItem[]
      message?: string
    }
    tablespaces: {
      available: boolean
      items: DatabaseTablespaceItem[]
      message?: string
    }
  }
  load: {
    available: boolean
    source: string
    metrics: DatabaseLoadMetric[]
    connection_pool?: DatabasePool
    assessment?: string
    message?: string
  }
  cache?: { cached: boolean; age_seconds: number; ttl_seconds: number }
  warnings?: string[]
}

export interface HostSnapshot {
  host: {
    cpu: {
      user_pct: number | null
      system_pct: number | null
      idle_pct: number | null
      iowait_pct: number | null
    }
    mem: { total_mb: number | null; used_mb: number | null; available_mb: number | null }
    load: { '1m': number | null; '5m': number | null; '15m': number | null }
    disk: { mount?: string; used_pct: number | null; free_gb: number | null }
    net: { rx_kbps: number | null; tx_kbps: number | null }
    diskio: { read_kbps: number | null; write_kbps: number | null }
    proc: { rss_mb: number | null; uptime_hours: number | null }
  }
  pool: DatabasePool
  api: {
    window_seconds: number
    items: Array<{
      method: string
      route: string
      count: number
      err_count: number
      avg_ms: number
      p95_ms: number
      max_ms: number
    }>
  }
}

export interface RetentionProgressCell {
  deleted: number
  remaining: number
}

export interface RetentionProgress {
  running: boolean
  job_id: string | null
  phase: string | null
  started_at: string | null
  finished_at: string | null
  progress?: Partial<Record<string, RetentionProgressCell | number>>
  result?: unknown
  error?: string | null
}

export interface BackupSnapshot {
  state: 'idle' | 'running' | 'ok' | 'failed'
  started_at: string | null
  finished_at: string | null
  tables_total: number | null
  tables_done: number
  rows_loaded: number
  current_table: string | null
  elapsed_seconds: number | null
  message: string
}

export interface RetentionPreviewRow {
  cutoff: string
  deletable: number | null
}

export interface RetentionPreview {
  app_logs: RetentionPreviewRow
  export_tasks: RetentionPreviewRow
  ai_call_logs: RetentionPreviewRow
  ai_prompt_audit: RetentionPreviewRow
  ai_agent_events: RetentionPreviewRow
}

/* ------------------------------------------------------------------ */
/* ZVAgent（`/api/ai/*`）：字段与 conversation_view / run_view 一致。 */
/* ------------------------------------------------------------------ */

export type AiThinking = 'low' | 'medium' | 'high'
export type AiConversationStatus = 'active' | 'archived'
export type AiRunStatus =
  | 'queued'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'interrupted'

export interface AiConversation {
  oid: string
  mode: 'agent'
  title: string
  model: string | null
  thinking: AiThinking | null
  scene: string | null
  status: AiConversationStatus
  tokens: { prompt: number; completion: number; total: number }
  context: { tokens: number; limit: number; ratio: number }
  compaction: { upto_seq: number; summarized: boolean; degraded_at: string | null }
  message_count: number
  created_at: string | null
  updated_at: string | null
}

export interface AiConversationPage {
  conversations: AiConversation[]
  next_cursor: string | null
}

export interface AiTokenUsage {
  prompt_tokens: number | null
  completion_tokens: number | null
  total_tokens: number | null
  cache_read_tokens: number | null
  cache_write_tokens: number | null
}

/** 会话消息：`content` 对 assistant 是 Markdown，对 tool 是工具结果的原文。 */
export interface AiMessage {
  oid: string
  seq: number
  role: 'user' | 'assistant' | 'tool' | 'system'
  content: string
  reasoning: string | null
  tool_calls: unknown
  tool_call_id: string | null
  tool_name: string | null
  round_no: number | null
  request_id: string | null
  usage: AiTokenUsage | null
  created_at: string | null
}

export interface AiMessagePage {
  messages: AiMessage[]
  has_more: boolean
  total: number
}

export interface AiRun {
  oid: string
  conversation_oid: string
  status: AiRunStatus
  model: string | null
  stop_reason: string | null
  rounds: number
  error_code: string | null
  error_message: string | null
  result_message_oid: string | null
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cancel_requested: boolean
  created_at: string | null
  started_at: string | null
  finished_at: string | null
}

export type AiToolKind = 'read' | 'write'

/** run 详情里的持久化工具事件（`services.agent.proposal.run_events`）。 */
export interface AiToolEvent {
  oid: string
  round_no: number | null
  tool_name: string | null
  tool_kind: AiToolKind | null
  args_summary: string | null
  result_summary: string | null
  status: string | null
  created_at: string | null
}

export interface AiProposal {
  oid: string
  tool_name: string | null
  summary: string | null
  args_summary: string | null
  preview: unknown
  status: string | null
  expires_at: string | null
  requires_dual_auth: boolean
  dual_auth_cached: boolean
  created_at: string | null
}

export interface AiRunDetail {
  run: AiRun
  events: AiToolEvent[]
  proposals: AiProposal[]
  messages?: AiMessage[]
}

/** 实时事件流里的累计回答（同一 `stream_key` 反复覆盖）。 */
export interface AiLiveAssistant {
  seq: number
  round_no: number
  stream_key: string
  content: string
}

/** 实时事件流里的工具开始 / 结束（同一 `tool_call_id` 反复覆盖）。 */
export interface AiLiveTool {
  seq: number
  round_no: number
  tool_call_id: string | null
  tool_name: string | null
  tool_kind: AiToolKind | null
  status: 'pending' | 'success' | 'error'
  args_summary?: string | null
  result_summary?: string | null
}

export interface AiLiveState {
  assistant: AiLiveAssistant[]
  tools: AiLiveTool[]
}

export interface AiSubmitRunResult {
  run_id: string
  message_oid: string
  status: string
  deduped?: boolean
}

export interface AiRiskAck {
  acked: boolean
  acked_at: string | null
}
