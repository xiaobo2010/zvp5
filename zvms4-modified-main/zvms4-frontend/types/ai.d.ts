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

/** /ai/logs/stats 的响应 data。 */
export interface AiLogStats {
  days: number
  /** 窗口起点（本地时间 ISO 字符串）。 */
  since: string
  daily: AiLogDailyStat[]
  by_provider: AiLogProviderStat[]
  by_model: AiLogModelStat[]
  by_purpose: AiLogPurposeStat[]
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
