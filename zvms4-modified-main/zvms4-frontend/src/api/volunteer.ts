import type { OcrResult, OcrStatus } from '@/../types'
import { enqueueOcr } from '@/api/ai/ocr'
import { getOcr } from '@/api/attachment/manage'
import { upload as uploadAttachment } from '@/api/attachment/upload'
import { parseOcrText, type OcrVolunteerRow } from '@/utils/ocrText'

/**
 * 义工登记表的 OCR：上传图片 → AI 网关入队 → 轮询附件结果 → 本地拆行。
 *
 * 不再假设后端有 `/volunteer/registration/ocr` 这种专用路由。图片走现有附件
 * 库（purpose=volunteer_order），识别走 `/ai/ocr` 网关，因此额度、频控、引擎
 * 选择都复用既有实现。
 */
export type { OcrVolunteerRow } from '@/utils/ocrText'

export type VolunteerOcrStage = 'uploading' | 'queued' | 'recognizing'

export interface VolunteerOcrProgress {
  stage: VolunteerOcrStage
  progress?: number
  status?: OcrStatus
  engine?: string
}

export type VolunteerOcrProgressHandler = (progress: VolunteerOcrProgress) => void

export interface OcrVolunteerResponse {
  rows: OcrVolunteerRow[]
  warnings?: string[]
  text?: string
  attachmentOid?: string
  engine?: string
  confidence?: number
}

const SUPPORTED_MIME = new Set(['image/jpeg', 'image/png', 'image/webp'])
const MAX_BYTES = 15 * 1024 * 1024
const POLL_INTERVAL_MS = 2500
const MAX_POLL_ATTEMPTS = 40
const MAX_CONSECUTIVE_ERRORS = 3

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms)
  })
}

function validateFile(file: File): void {
  if (!file) {
    throw new Error('请选择要识别的图片')
  }
  if (!SUPPORTED_MIME.has((file.type || '').toLowerCase())) {
    throw new Error('OCR 仅支持 JPG / PNG / WebP 图片')
  }
  if (file.size > MAX_BYTES) {
    throw new Error('图片不能超过 15MB')
  }
}

async function waitForResult(
  attachmentOid: string,
  onProgress?: VolunteerOcrProgressHandler,
  engine?: string
): Promise<OcrResult> {
  let consecutiveErrors = 0
  for (let attempt = 0; attempt < MAX_POLL_ATTEMPTS; attempt += 1) {
    try {
      const result = await getOcr(attachmentOid)
      if (!result) {
        consecutiveErrors += 1
      } else {
        consecutiveErrors = 0
        onProgress?.({ stage: 'recognizing', status: result.status, engine })
        if (result.status === 'done' || result.status === 'failed') {
          return result
        }
      }
    } catch {
      consecutiveErrors += 1
    }

    if (consecutiveErrors >= MAX_CONSECUTIVE_ERRORS) {
      throw new Error('OCR 状态查询连续失败，请稍后重试')
    }
    await sleep(POLL_INTERVAL_MS)
  }
  throw new Error('OCR 识别等待超时，请换一张更清晰的图片或稍后重试')
}

/**
 * 上传登记表图片并等待 OCR 文本，再在本地拆成登记行。
 *
 * 识别行只包含 OCR 能确定的字段；学号/姓名/时长仍由页面校验，不会静默落库。
 */
export async function parseVolunteerSheet(
  file: File,
  verificationToken: string,
  onProgress?: VolunteerOcrProgressHandler
): Promise<OcrVolunteerResponse> {
  validateFile(file)

  const uploaded = await uploadAttachment(
    file,
    'volunteer_order',
    undefined,
    undefined,
    (event) => {
      onProgress?.({
        stage: 'uploading',
        progress: event.total ? Math.round((event.loaded / event.total) * 100) : undefined
      })
    }
  )
  const attachment = uploaded[0]
  if (!attachment?._id) {
    throw new Error('图片上传失败：服务端没有返回附件标识')
  }

  onProgress?.({ stage: 'queued' })
  const queued = await enqueueOcr(attachment._id, verificationToken)
  onProgress?.({
    stage: 'recognizing',
    status: queued.status,
    engine: queued.engine
  })

  const result = await waitForResult(attachment._id, onProgress, queued.engine)
  if (result.status === 'failed') {
    throw new Error('OCR 识别失败，请换一张更清晰的图片或稍后重试')
  }

  const text = result.text?.trim() ?? ''
  const warnings: string[] = []
  if (!text) {
    warnings.push('OCR 没有返回文字，请检查图片清晰度或手动粘贴')
  }

  const rows = parseOcrText(text)
  if (text && !rows.length) {
    warnings.push('OCR 文本中未解析出学生行，请手动粘贴或调整识别图片')
  }

  return {
    rows,
    warnings: warnings.length ? warnings : undefined,
    text,
    attachmentOid: attachment._id,
    engine: result.engine ?? queued.engine,
    confidence: result.confidence
  }
}
