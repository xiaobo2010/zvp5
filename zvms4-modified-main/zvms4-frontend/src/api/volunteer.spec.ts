import { beforeEach, describe, expect, it, vi } from 'vitest'
import { upload } from '@/api/attachment/upload'
import { enqueueOcr } from '@/api/ai/ocr'
import { getOcr } from '@/api/attachment/manage'
import { parseVolunteerSheet } from './volunteer'

vi.mock('@/api/attachment/upload', () => ({ upload: vi.fn() }))
vi.mock('@/api/ai/ocr', () => ({ enqueueOcr: vi.fn() }))
vi.mock('@/api/attachment/manage', () => ({ getOcr: vi.fn() }))

const OID = 'a'.repeat(24)

function sheetFile(type = 'image/png', name = 'sheet.png'): File {
  return new File(['image-bytes'], name, { type })
}

beforeEach(() => {
  vi.mocked(upload).mockReset()
  vi.mocked(enqueueOcr).mockReset()
  vi.mocked(getOcr).mockReset()
})

describe('义工登记 OCR 流程', () => {
  it('拒绝非图片文件，不触发出站请求', async () => {
    await expect(parseVolunteerSheet(sheetFile('application/pdf', 'sheet.pdf'), 'short-lived')) .rejects.toThrow(
      '仅支持 JPG / PNG / WebP'
    )
    expect(upload).not.toHaveBeenCalled()
  })

  it('上传图片、走 AI 网关并解析 OCR 文本', async () => {
    vi.mocked(upload).mockResolvedValue([
      { _id: OID, mime: 'image/png', size: 12, ocr_status: 'pending' }
    ])
    vi.mocked(enqueueOcr).mockResolvedValue({
      status: 'pending',
      attachment_oid: OID,
      engine: 'tencent'
    })
    vi.mocked(getOcr).mockResolvedValue({
      status: 'done',
      engine: 'tencent',
      text: '学号 姓名 时长\n2024001 张三 2',
      confidence: 0.92
    })

    const progress = vi.fn()
    const result = await parseVolunteerSheet(sheetFile(), 'short-lived', progress)

    expect(upload).toHaveBeenCalledWith(
      expect.any(File),
      'volunteer_order',
      undefined,
      undefined,
      expect.any(Function)
    )
    expect(enqueueOcr).toHaveBeenCalledWith(OID, 'short-lived')
    expect(getOcr).toHaveBeenCalledWith(OID)
    expect(result.rows).toEqual([
      {
        studentId: '2024001',
        name: '张三',
        duration: 2,
        mode: undefined,
        note: undefined
      }
    ])
    expect(result.attachmentOid).toBe(OID)
    expect(result.engine).toBe('tencent')
    expect(progress).toHaveBeenCalledWith(expect.objectContaining({ stage: 'queued' }))
    expect(progress).toHaveBeenCalledWith(expect.objectContaining({ stage: 'recognizing' }))
  })

  it('OCR 失败状态转成用户可读错误', async () => {
    vi.mocked(upload).mockResolvedValue([
      { _id: OID, mime: 'image/png', size: 12, ocr_status: 'pending' }
    ])
    vi.mocked(enqueueOcr).mockResolvedValue({
      status: 'pending',
      attachment_oid: OID,
      engine: 'tencent'
    })
    vi.mocked(getOcr).mockResolvedValue({ status: 'failed' })

    await expect(parseVolunteerSheet(sheetFile(), 'short-lived')).rejects.toThrow('OCR 识别失败')
  })
})
