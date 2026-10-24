import type { AiChatMessage, AiModelList } from '@/../types/ai'
import { aiFetchJson } from './http'

/**
 * OpenAI 兼容面（/ai/v1/*）。
 *
 * 响应没有 zvp 的 envelope：成功就是上游原始 JSON，错误是 {"error":{...}}。
 * 因此这里不走 axios 的 envelope 拆包，也不假设上游字段形状。
 */

/** 当前用户真正能调的模型；空数组代表没有任何可用模型。 */
export async function listModels(): Promise<AiModelList> {
  return aiFetchJson<AiModelList>('/ai/v1/models', { method: 'GET' })
}

/** 非流式对话：返回上游原始 JSON，字段由上游决定。 */
export async function createChatCompletion(
  model: string,
  messages: AiChatMessage[]
): Promise<Record<string, unknown>> {
  return aiFetchJson<Record<string, unknown>>('/ai/v1/chat/completions', {
    method: 'POST',
    body: JSON.stringify({ model, messages, stream: false })
  })
}

/** 从上游原始 JSON 里取回文本；形状不认识就回空串。 */
export function readCompletionText(payload: unknown): string {
  if (!payload || typeof payload !== 'object') {
    return ''
  }
  const choices = (payload as { choices?: unknown }).choices
  if (!Array.isArray(choices) || choices.length === 0) {
    return ''
  }
  const first = choices[0]
  if (!first || typeof first !== 'object') {
    return ''
  }
  const message = (first as Record<string, unknown>).message
  if (message && typeof message === 'object') {
    const content = (message as Record<string, unknown>).content
    if (typeof content === 'string') {
      return content
    }
  }
  const text = (first as Record<string, unknown>).text
  if (typeof text === 'string') {
    return text
  }
  return ''
}
