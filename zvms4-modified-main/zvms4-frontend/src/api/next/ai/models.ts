import type {
  AiModelDiscoverResult,
  AiModelDiscoverRow,
  AiModelPatch,
  AiModelRow
} from '../ai-types'
import { aiRequest, aiRequestVoid } from '@/api/ai/http'

/**
 * 模型管理（/ai/models）。仅 admin 可用。
 *
 * 一张表把「渠道目录里的模型」与「手工登记的模型」放在一起：
 * in_catalog 为 true 的是目录行，编辑会写一条覆盖；删除覆盖后回落到目录默认值。
 */

type RawRow = Record<string, unknown>

function asRecord(value: unknown): RawRow {
  return value && typeof value === 'object' ? (value as RawRow) : {}
}

function textOf(value: unknown): string | null {
  if (typeof value === 'string') {
    return value
  }
  if (typeof value === 'number') {
    return String(value)
  }
  return null
}

function numberOf(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = Number(value)
    return Number.isFinite(parsed) ? parsed : null
  }
  return null
}

function normalizeRow(value: unknown): AiModelRow {
  const row = asRecord(value)
  const provider = textOf(row.provider)
  const providers = Array.isArray(row.providers)
    ? row.providers.map((item) => textOf(item)).filter((item): item is string => !!item)
    : []
  return {
    model: textOf(row.model) ?? '',
    display_name: textOf(row.display_name),
    provider,
    providers: providers.length ? providers : provider ? [provider] : [],
    context_length: numberOf(row.context_length),
    rate: numberOf(row.rate),
    enabled: row.enabled !== false,
    in_catalog: row.in_catalog === true,
    notes: textOf(row.notes),
    updated_at: textOf(row.updated_at)
  }
}

/** 全部模型行（目录行 + 手工登记行）。 */
export async function fetchAiModels(): Promise<AiModelRow[]> {
  const data = await aiRequest<unknown>({ method: 'get', url: '/ai/models' })
  const models = Array.isArray(data) ? data : asRecord(data).models
  return Array.isArray(models) ? models.map(normalizeRow) : []
}

/** 部分更新一行；只传要改的字段。 */
export async function updateAiModel(model: string, patch: AiModelPatch): Promise<AiModelRow> {
  const data = await aiRequest<unknown>({
    method: 'put',
    url: `/ai/models/${encodeURIComponent(model)}`,
    data: patch
  })
  return normalizeRow(data)
}

/** 删除覆盖行；成功返回 204，没有响应体。 */
export async function deleteAiModel(model: string): Promise<void> {
  await aiRequestVoid({ method: 'delete', url: `/ai/models/${encodeURIComponent(model)}` })
}

function normalizeDiscoverRow(value: unknown): AiModelDiscoverRow {
  const row = asRecord(value)
  return {
    model: textOf(row.model) ?? '',
    context_length: numberOf(row.context_length),
    in_credential: row.in_credential === true,
    applied: row.applied === true
  }
}

/**
 * 从渠道上游读模型元数据（`POST /ai/models/discover`）。
 *
 * apply=false 只做预览；apply=true 顺带把上游给的上下文长度写回库内。
 * 该端点与凭据写路径使用同一套密码再验证，因此可带一张 verify 短令牌。
 */
export async function discoverAiModels(
  provider: string,
  apply = false,
  verificationToken?: string
): Promise<AiModelDiscoverResult> {
  const data = await aiRequest<unknown>({
    method: 'post',
    url: '/ai/models/discover',
    data: {
      provider,
      apply,
      ...(verificationToken ? { verification_token: verificationToken } : {})
    }
  })
  const record = asRecord(data)
  const models = Array.isArray(record.models) ? record.models.map(normalizeDiscoverRow) : []
  return {
    provider: textOf(record.provider) ?? provider,
    models,
    applied: numberOf(record.applied) ?? 0
  }
}
