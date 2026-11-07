import type {
  AiKeyCreateInput,
  AiKeyRecord,
  AiKeyUpdateInput
} from '@/../types/ai'
import { aiRequest, aiRequestVoid } from './http'

/**
 * AI 凭据（/ai/keys）。仅 admin 可用。
 *
 * 响应里永远没有明文密钥：列表与创建结果都只带 key_hint。
 * 因此前端不设计「创建后展示一次明文」的交互。
 */

export async function listKeys(): Promise<AiKeyRecord[]> {
  return aiRequest<AiKeyRecord[]>({ method: 'get', url: '/ai/keys' })
}

export async function createKey(
  input: AiKeyCreateInput,
  verificationToken?: string
): Promise<AiKeyRecord> {
  return aiRequest<AiKeyRecord>({
    method: 'post',
    url: '/ai/keys',
    data: { ...input, ...(verificationToken ? { verification_token: verificationToken } : {}) }
  })
}

export async function updateKey(
  oid: string,
  input: AiKeyUpdateInput,
  verificationToken?: string
): Promise<AiKeyRecord> {
  return aiRequest<AiKeyRecord>({
    method: 'patch',
    url: `/ai/keys/${oid}`,
    data: { ...input, ...(verificationToken ? { verification_token: verificationToken } : {}) }
  })
}

/** 删除成功返回 204，没有响应体：这里不解析 body。 */
export async function deleteKey(oid: string, verificationToken?: string): Promise<void> {
  await aiRequestVoid({
    method: 'delete',
    url: `/ai/keys/${oid}`,
    ...(verificationToken ? { data: { verification_token: verificationToken } } : {})
  })
}
