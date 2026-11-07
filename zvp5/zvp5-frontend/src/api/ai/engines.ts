import type {
  AiOcrEngineCreateInput,
  AiOcrEngineRecord,
  AiOcrEngineUpdateInput
} from '@/../types/ai'
import { aiRequest, aiRequestVoid } from './http'

/**
 * OCR 引擎注册表（/ai/ocr-engines）。仅 admin 可用。
 *
 * 响应只透露「有没有凭证」（has_secret）与账号标识末四位（key_hint），
 * 凭证内容一律不回显。
 */

export async function listOcrEngines(): Promise<AiOcrEngineRecord[]> {
  return aiRequest<AiOcrEngineRecord[]>({ method: 'get', url: '/ai/ocr-engines' })
}

export async function createOcrEngine(input: AiOcrEngineCreateInput): Promise<AiOcrEngineRecord> {
  return aiRequest<AiOcrEngineRecord>({
    method: 'post',
    url: '/ai/ocr-engines',
    data: input
  })
}

export async function updateOcrEngine(
  oid: string,
  input: AiOcrEngineUpdateInput
): Promise<AiOcrEngineRecord> {
  return aiRequest<AiOcrEngineRecord>({
    method: 'patch',
    url: `/ai/ocr-engines/${oid}`,
    data: input
  })
}

/** 删除成功返回 204，没有响应体。 */
export async function deleteOcrEngine(oid: string): Promise<void> {
  await aiRequestVoid({ method: 'delete', url: `/ai/ocr-engines/${oid}` })
}
