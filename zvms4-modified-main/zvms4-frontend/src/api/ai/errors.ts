import type { AiChatMessage } from '@/../types/ai'

/**
 * AI 网关错误码 → 人话文案的映射。
 *
 * 后端两套协议面的错误体形状不同：zvp 面是 {"detail":"<code>"}（结构化
 * 错误为 {"detail":{"code","message"}}，设备闸 403 用后者），OpenAI 面是
 * {"error":{"message","type","code"}}。两者最终都归一成
 * 一个错误码，由本模块给出「发生了什么 + 该做什么」。
 *
 * 这里只放 i18n key，不放文案本身：文案集中在 src/i18n/locales/ai.ts。
 */

/** 错误码 → i18n 段落名。非标识符形式的 detail（Permission denied 等）在此归一。 */
const ERROR_SECTIONS: Record<string, string> = {
  AI_FORBIDDEN: 'AI_FORBIDDEN',
  AI_RATE_LIMITED: 'AI_RATE_LIMITED',
  AI_BUSY: 'AI_BUSY',
  AI_QUOTA_EXCEEDED: 'AI_QUOTA_EXCEEDED',
  insufficient_quota: 'INSUFFICIENT_QUOTA',
  AI_DISABLED: 'AI_DISABLED',
  AI_NO_PROVIDER: 'AI_NO_PROVIDER',
  model_not_found: 'MODEL_NOT_FOUND',
  AI_UPSTREAM_AUTH: 'AI_UPSTREAM_AUTH',
  AI_UPSTREAM_RATE_LIMIT: 'AI_UPSTREAM_RATE_LIMIT',
  AI_UPSTREAM_ERROR: 'AI_UPSTREAM_ERROR',
  AI_UPSTREAM_REJECTED: 'AI_UPSTREAM_REJECTED',
  AI_TIMEOUT: 'AI_TIMEOUT',
  AI_CREDENTIAL_UNREADABLE: 'AI_CREDENTIAL_UNREADABLE',
  AI_UNSUPPORTED_MEDIA: 'AI_UNSUPPORTED_MEDIA',
  AI_OCR_IMAGE_TOO_LARGE: 'AI_OCR_IMAGE_TOO_LARGE',
  AI_OCR_NO_ENGINE: 'AI_OCR_NO_ENGINE',
  AI_OCR_SITE_QUOTA_EXHAUSTED: 'AI_OCR_SITE_QUOTA_EXHAUSTED',
  AI_KEY_DUPLICATE: 'AI_KEY_DUPLICATE',
  AI_KEY_INVALID: 'AI_KEY_INVALID',
  AI_KEY_NOT_FOUND: 'AI_KEY_NOT_FOUND',
  AI_KEY_FORBIDDEN: 'AI_KEY_FORBIDDEN',
  AI_KEY_BAD_EXPIRY: 'AI_KEY_BAD_EXPIRY',
  AI_LOGS_BAD_FILTER: 'AI_LOGS_BAD_FILTER',
  AI_OCR_ENGINE_DUPLICATE: 'AI_OCR_ENGINE_DUPLICATE',
  AI_OCR_ENGINE_INVALID: 'AI_OCR_ENGINE_INVALID',
  AI_OCR_ENGINE_BAD_KIND: 'AI_OCR_ENGINE_BAD_KIND',
  AI_OCR_ENGINE_NOT_FOUND: 'AI_OCR_ENGINE_NOT_FOUND',
  AI_QUOTA_BAD_KIND: 'AI_QUOTA_BAD_KIND',
  AI_QUOTA_BAD_LIMIT: 'AI_QUOTA_BAD_LIMIT',
  AI_INVALID_REQUEST: 'AI_INVALID_REQUEST',
  AI_AGENT_BAD_ARGUMENTS: 'AI_AGENT_BAD_ARGUMENTS',
  AI_AGENT_PROPOSAL_NOT_FOUND: 'AI_AGENT_PROPOSAL_NOT_FOUND',
  AI_AGENT_PROPOSAL_FORBIDDEN: 'AI_AGENT_PROPOSAL_FORBIDDEN',
  AI_AGENT_PROPOSAL_CONFLICT: 'AI_AGENT_PROPOSAL_CONFLICT',
  AI_AGENT_PROPOSAL_EXPIRED: 'AI_AGENT_PROPOSAL_EXPIRED',
  AI_AGENT_PROPOSAL_DENIED: 'AI_AGENT_PROPOSAL_DENIED',
  AI_AGENT_PROPOSAL_TAMPERED: 'AI_AGENT_PROPOSAL_TAMPERED',
  AI_AGENT_PROPOSAL_UNKNOWN_TOOL: 'AI_AGENT_PROPOSAL_UNKNOWN_TOOL',
  AI_AGENT_TOOL_FAILED: 'AI_AGENT_TOOL_FAILED',
  AI_AGENT_TOTP_REQUIRED: 'AI_AGENT_TOTP_REQUIRED',
  AI_AGENT_UNLOCK_REQUIRED: 'AI_AGENT_UNLOCK_REQUIRED',
  AI_AGENT_UNLOCK_FAILED: 'AI_AGENT_UNLOCK_FAILED',
  AI_AGENT_UNLOCK_LOCKED: 'AI_AGENT_UNLOCK_LOCKED',
  AI_AGENT_MODERATION_REJECTED: 'AI_AGENT_MODERATION_REJECTED',
  AI_CONVERSATION_NOT_FOUND: 'AI_CONVERSATION_NOT_FOUND',
  AI_CONVERSATION_MODE_MISMATCH: 'AI_CONVERSATION_MODE_MISMATCH',
  AI_RUN_CONFLICT: 'AI_RUN_CONFLICT',
  AI_RUN_NOT_FOUND: 'AI_RUN_NOT_FOUND',
  AI_RUN_NOT_CANCELLABLE: 'AI_RUN_NOT_CANCELLABLE',
  AI_RUN_INTERRUPTED: 'AI_RUN_INTERRUPTED',
  AI_CONTEXT_UNAVAILABLE: 'AI_CONTEXT_UNAVAILABLE',
  'Permission denied': 'PERMISSION_DENIED',
  'Attachment not found': 'ATTACHMENT_NOT_FOUND'
}

export const AI_ERROR_FALLBACK_SECTION = 'UNKNOWN'

/** 全部已知错误码（供测试遍历）。 */
export const AI_ERROR_CODES = Object.keys(ERROR_SECTIONS)

/** 错误码 → i18n 段落名；未知码回落到 UNKNOWN。 */
export function aiErrorSection(code: string | null | undefined): string {
  if (!code) {
    return AI_ERROR_FALLBACK_SECTION
  }
  return ERROR_SECTIONS[code] ?? AI_ERROR_FALLBACK_SECTION
}

export interface AiErrorCopy {
  title: string
  hint: string
}

export type AiTranslator = (key: string) => string

/** 错误码 → 已翻译的「标题 + 建议动作」。 */
export function aiErrorCopy(code: string | null | undefined, t: AiTranslator): AiErrorCopy {
  const section = aiErrorSection(code)
  return {
    title: t(`ai.errors.${section}.title`),
    hint: t(`ai.errors.${section}.hint`)
  }
}

/**
 * AI 网关的统一错误。zvp 面与 OpenAI 面都归一到它：
 * 页面按 code 分支，status 用于区分「配额 / 限流 / 上游故障」。
 */
export class AiRequestError extends Error {
  readonly status: number
  readonly code: string
  readonly errorType: string | null
  readonly requestId: string | null
  readonly retryAfter: number | null
  readonly quotaUsed: number | null
  readonly quotaLimit: number | null

  constructor(options: {
    status: number
    code: string
    message?: string
    errorType?: string | null
    requestId?: string | null
    retryAfter?: number | null
    quotaUsed?: number | null
    quotaLimit?: number | null
  }) {
    super(options.message || options.code)
    this.name = 'AiRequestError'
    this.status = options.status
    this.code = options.code
    this.errorType = options.errorType ?? null
    this.requestId = options.requestId ?? null
    this.retryAfter = options.retryAfter ?? null
    this.quotaUsed = options.quotaUsed ?? null
    this.quotaLimit = options.quotaLimit ?? null
  }
}

/** 从任意异常里取出 AI 错误码；非 AI 错误返回 null。 */
export function aiErrorCodeOf(error: unknown): string | null {
  if (error instanceof AiRequestError) {
    return error.code
  }
  return null
}
/** 从任意异常里取出 AI 错误对象；非 AI 错误返回 null。 */
export function aiErrorOf(error: unknown): AiRequestError | null {
  return error instanceof AiRequestError ? error : null
}

/** 对话请求体：网关只读 model / messages / stream / provider，其余原样透传上游。 */
export interface AiChatRequest {
  model: string
  messages: AiChatMessage[]
  stream: boolean
}

/**
 * 从错误体里取出错误码。
 * zvp 面是 {"detail":"<code>"} 或结构化 {"detail":{"code","message"}}（设备
 * 闸 403 用后者），OpenAI 面是 {"error":{"code","message"}}；pydantic 的 422
 * 校验错误是数组，代码层面无法定位字段，统一归成
 * AI_INVALID_REQUEST（页面给通用提示，不猜字段）。
 */
export function parseAiCode(body: unknown): string {
  if (typeof body === 'string') {
    return body.trim()
  }
  if (!body || typeof body !== 'object') {
    return ''
  }
  const record = body as Record<string, unknown>
  const detail = record.detail
  if (typeof detail === 'string') {
    return detail.trim()
  }
  if (Array.isArray(detail)) {
    return 'AI_INVALID_REQUEST'
  }
  if (detail && typeof detail === 'object') {
    const code = (detail as Record<string, unknown>).code
    if (typeof code === 'string' && code.trim()) {
      return code.trim()
    }
  }
  const error = record.error
  if (typeof error === 'string') {
    return error.trim()
  }
  if (error && typeof error === 'object') {
    const code = (error as Record<string, unknown>).code
    if (typeof code === 'string' && code.trim()) {
      return code.trim()
    }
  }
  return ''
}

/** 从错误体里取出后端给的人话 message（结构化 detail.message 或 OpenAI 面）。 */
export function parseAiMessage(body: unknown): string {
  if (!body || typeof body !== 'object') {
    return ''
  }
  const detail = (body as Record<string, unknown>).detail
  if (detail && typeof detail === 'object') {
    const message = (detail as Record<string, unknown>).message
    if (typeof message === 'string') {
      return message
    }
  }
  const error = (body as Record<string, unknown>).error
  if (error && typeof error === 'object') {
    const message = (error as Record<string, unknown>).message
    if (typeof message === 'string') {
      return message
    }
  }
  return ''
}
