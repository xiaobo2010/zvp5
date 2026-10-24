import type { AiOcrEnqueueResult } from '@/../types/ai'
import { aiRequest } from './http'

/**
 * OCR 入队（/ai/ocr）。
 *
 * 走网关而不是 /attachments/{oid}/ocr：网关入口有额度与频控，
 * 附件接口那条是 manager 手动重识别用的，不扣额度。
 *
 * 202 = 新受理（响应带 engine），200 = 已在处理中（幂等，不带 engine）。
 */
export async function enqueueOcr(attachmentOid: string): Promise<AiOcrEnqueueResult> {
  return aiRequest<AiOcrEnqueueResult>({
    method: 'post',
    url: '/ai/ocr',
    data: { attachment_oid: attachmentOid }
  })
}
