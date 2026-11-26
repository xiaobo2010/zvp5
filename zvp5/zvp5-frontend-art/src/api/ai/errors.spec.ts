import { describe, expect, it } from 'vitest'
import {
  AI_ERROR_CODES,
  AI_ERROR_FALLBACK_SECTION,
  aiErrorCopy,
  aiErrorSection,
  parseAiCode,
  parseAiMessage
} from './errors'

const t = (key: string) => key

describe('错误码到人话文案的映射', () => {
  it('覆盖网关两套协议面用到的全部错误码', () => {
    for (const code of [
      'AI_FORBIDDEN',
      'AI_RATE_LIMITED',
      'AI_BUSY',
      'AI_QUOTA_EXCEEDED',
      'insufficient_quota',
      'AI_DISABLED',
      'AI_NO_PROVIDER',
      'model_not_found',
      'AI_UPSTREAM_AUTH',
      'AI_UPSTREAM_RATE_LIMIT',
      'AI_UPSTREAM_ERROR',
      'AI_UPSTREAM_REJECTED',
      'AI_TIMEOUT',
      'AI_CREDENTIAL_UNREADABLE',
      'AI_UNSUPPORTED_MEDIA',
      'AI_OCR_IMAGE_TOO_LARGE',
      'AI_OCR_NO_ENGINE',
      'AI_OCR_SITE_QUOTA_EXHAUSTED',
      'AI_KEY_DUPLICATE',
      'AI_KEY_INVALID',
      'AI_KEY_NOT_FOUND',
      'AI_KEY_FORBIDDEN',
      'AI_KEY_BAD_EXPIRY',
      'AI_LOGS_BAD_FILTER',
      'AI_OCR_ENGINE_DUPLICATE',
      'AI_OCR_ENGINE_INVALID',
      'AI_OCR_ENGINE_BAD_KIND',
      'AI_OCR_ENGINE_NOT_FOUND',
      'AI_QUOTA_BAD_KIND',
      'AI_QUOTA_BAD_LIMIT',
      'Permission denied',
      'Attachment not found'
    ]) {
      expect(AI_ERROR_CODES).toContain(code)
      expect(aiErrorSection(code)).not.toBe(AI_ERROR_FALLBACK_SECTION)
    }
  })

  it('已知码命中自己的文案段落', () => {
    expect(aiErrorSection('AI_RATE_LIMITED')).toBe('AI_RATE_LIMITED')
    expect(aiErrorSection('Permission denied')).toBe('PERMISSION_DENIED')
    expect(aiErrorSection('Attachment not found')).toBe('ATTACHMENT_NOT_FOUND')
  })

  it('未知码回落到 UNKNOWN', () => {
    expect(aiErrorSection('SOMETHING_ELSE')).toBe('UNKNOWN')
    expect(aiErrorSection(null)).toBe('UNKNOWN')
    expect(aiErrorSection(undefined)).toBe('UNKNOWN')
  })

  it('文案由标题与建议动作两部分组成', () => {
    const copy = aiErrorCopy('AI_FORBIDDEN', t)
    expect(copy.title).toBe('ai.errors.AI_FORBIDDEN.title')
    expect(copy.hint).toBe('ai.errors.AI_FORBIDDEN.hint')
  })

  it('未知码也拿得到文案', () => {
    const copy = aiErrorCopy('WHAT_IS_THIS', t)
    expect(copy.title).toBe('ai.errors.UNKNOWN.title')
    expect(copy.hint).toBe('ai.errors.UNKNOWN.hint')
  })
})

describe('错误体解析', () => {
  it('zvp 面读 detail', () => {
    expect(parseAiCode({ detail: 'AI_KEY_DUPLICATE' })).toBe('AI_KEY_DUPLICATE')
  })

  it('OpenAI 面读 error.code', () => {
    expect(parseAiCode({ error: { code: 'AI_BUSY', message: 'busy' } })).toBe('AI_BUSY')
  })

  it('422 的数组 detail 归成通用请求错误', () => {
    expect(parseAiCode({ detail: [{ loc: ['body'], msg: 'bad' }] })).toBe('AI_INVALID_REQUEST')
  })

  it('没有可识别的错误体时回空串', () => {
    expect(parseAiCode(null)).toBe('')
    expect(parseAiCode({})).toBe('')
  })

  it('message 只在 OpenAI 面存在', () => {
    expect(parseAiMessage({ error: { message: 'too many requests' } })).toBe('too many requests')
    expect(parseAiMessage({ detail: 'AI_BUSY' })).toBe('')
  })

  it('结构化 detail（设备闸 403）读 detail.code', () => {
    expect(
      parseAiCode({
        detail: { code: 'DEVICE_NOT_PRIMARY', message: '当前设备不是主学海设备', challenge: 'tok' }
      })
    ).toBe('DEVICE_NOT_PRIMARY')
  })

  it('结构化 detail 里的 message 也能取到', () => {
    expect(
      parseAiMessage({ detail: { code: 'DEVICE_NOT_PRIMARY', message: '当前设备不是主学海设备' } })
    ).toBe('当前设备不是主学海设备')
  })

  it('结构化 detail 缺 code 时回空串', () => {
    expect(parseAiCode({ detail: { message: '只有消息' } })).toBe('')
  })
})
