import type { AiOcrFileResult, AiSceneName, AiSceneRunSubmitResult } from '@/../types/ai'
import { aiRequest } from './http'

/**
 * 场景智能面（/ai/scenes、/ai/ocr/file）。
 *
 * 场景 run 与普通会话同一套后台执行：提交 202 拿 run_id，之后用
 * `/ai/runs/{run_id}` 轮询；场景会话不进普通会话列表，因此调用方不需要
 * 关心 conversation_oid 的归属，只用于取回终态消息。
 */

/** 指令上限（后端 content ≤ 4000）。 */
export const SCENE_CONTENT_LIMIT = 4000

/** 参考内容上限（后端 context ≤ 8000）。 */
export const SCENE_CONTEXT_LIMIT = 8000

export async function submitSceneRun(
  scene: AiSceneName,
  content: string,
  context?: string
): Promise<AiSceneRunSubmitResult> {
  return aiRequest<AiSceneRunSubmitResult>({
    method: 'post',
    url: `/ai/scenes/${scene}/runs`,
    data: {
      content,
      ...(context ? { context } : {})
    }
  })
}

/** 服务端文件 OCR：同步返回识别文本，路径受文件根白名单约束。 */
export async function ocrFile(path: string, provider?: string): Promise<AiOcrFileResult> {
  return aiRequest<AiOcrFileResult>({
    method: 'post',
    url: '/ai/ocr/file',
    data: {
      path,
      ...(provider ? { provider } : {})
    }
  })
}
