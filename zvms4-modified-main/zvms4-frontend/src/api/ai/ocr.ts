import type { AiOcrEnqueueResult } from '@/../types/ai'
import { aiRequest } from './http'

/**
 * OCR 入队（/ai/ocr）。
 *
 * ``/attachments/{oid}/ocr`` 也走同一套引擎池（频控 + 用户额度 + 站点额度），
 * 区别只在权限：网关入口给普通用户，附件入口要 manager。前端统一从这里入队，
 * 少一条路径就少一处计价口径对不上的地方。
 *
 * 202 = 新受理（响应带 engine），200 = 已在处理中（幂等，不带 engine）。
 * 额度不足抛 ``AiRequestError``：``AI_QUOTA_EXCEEDED``（个人）或
 * ``AI_OCR_SITE_QUOTA_EXHAUSTED``（站点所有引擎都满），页面文案见 errors.ts。
 */
export async function enqueueOcr(attachmentOid: string): Promise<AiOcrEnqueueResult> {
  return aiRequest<AiOcrEnqueueResult>({
    method: 'post',
    url: '/ai/ocr',
    data: { attachment_oid: attachmentOid }
  })
}
