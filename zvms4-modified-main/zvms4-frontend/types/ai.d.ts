// AI 网关（/api/ai）的类型定义。
// 字段名与后端 routers/ai_router.py、services/ai/* 的响应视图逐字对齐，
// 前端不得自造字段名。

export type AiKeyScope = 'personal' | 'shared'

export type AiOcrEngineKind = 'tencent' | 'aliyun' | 'baidu' | 'rapidocr' | 'groq'

export type AiQuotaKind = 'ocr' | 'chat'

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
  routes: Record<string, string[]>
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
