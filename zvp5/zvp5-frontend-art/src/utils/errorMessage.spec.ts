import { beforeAll, describe, expect, it } from 'vitest'
import { AxiosError, type AxiosResponse } from 'axios'
import { AiRequestError } from '@/api/ai/errors'
import { loadNamespace } from '@/i18n/locales/namespaces'
import {
  DEFAULT_ERROR_MESSAGE,
  EN_ERROR_MESSAGES,
  errorDetailOf,
  resolveDetailMessage,
  resolveErrorMessage,
  resolveErrorMessageOr
} from './errorMessage'

// server.* 等大字典改为按路由懒加载，生产里由守卫装载；测试不走路由，先手动装一次，
// 才能验证「取不到 detail 时回落到 i18n key」这条链路。
beforeAll(async () => {
  await loadNamespace('server')
})

function axiosErrorOf(status: number, data: unknown): AxiosError {
  const config = { headers: {} } as AxiosError['config']
  const response: AxiosResponse = { data, status, statusText: 'Error', headers: {}, config: config! }
  return new AxiosError('Request failed with status code ' + status, 'ERR_BAD_REQUEST', config, undefined, response)
}

describe('resolveErrorMessage', () => {
  it('translates a high-frequency English detail', () => {
    expect(resolveErrorMessage(axiosErrorOf(403, { detail: 'Permission denied' }))).toBe(
      '没有权限执行该操作'
    )
    expect(resolveErrorMessage(axiosErrorOf(404, { detail: 'User not found' }))).toBe('用户不存在')
    expect(resolveErrorMessage(axiosErrorOf(400, { detail: 'Invalid Object ID' }))).toBe(
      '无效的对象 ID'
    )
  })

  it('keeps an unmapped detail verbatim instead of swallowing it', () => {
    expect(resolveErrorMessage(axiosErrorOf(400, { detail: 'series 不能为空' }))).toBe(
      'series 不能为空'
    )
    expect(resolveErrorMessage(axiosErrorOf(409, { detail: 'Something unheard of' }))).toBe(
      'Something unheard of'
    )
  })

  it('prefers the structured detail.message of an object detail', () => {
    const body = { detail: { code: 'GONE', message: 'Attachment unavailable' } }
    expect(resolveErrorMessage(axiosErrorOf(410, body))).toBe('附件暂不可用')
  })

  it('ignores the axios status-line message and falls back', () => {
    expect(resolveErrorMessage(axiosErrorOf(500, {}))).toBe(DEFAULT_ERROR_MESSAGE)
    expect(resolveErrorMessage(axiosErrorOf(500, {}), 'server.auth.failed')).toBe(
      '动态码无效或已过期'
    )
  })

  it('localizes a network failure instead of leaking the browser text', () => {
    const error = new AxiosError('Network Error', 'ERR_NETWORK')
    expect(resolveErrorMessage(error)).toBe('网络连接失败，请检查网络设置')
  })

  it('localizes a timeout', () => {
    const error = new AxiosError('timeout of 12000ms exceeded', 'ECONNABORTED')
    expect(resolveErrorMessage(error)).toBe('请求超时，请稍后重试')
  })

  it('keeps a plain Error message and uses the default fallback for empty ones', () => {
    expect(resolveErrorMessage(new Error('boom'))).toBe('boom')
    expect(resolveErrorMessage(undefined)).toBe(DEFAULT_ERROR_MESSAGE)
    expect(resolveErrorMessage('', 'server.auth.failed')).toBe('动态码无效或已过期')
  })

  it('keeps the AI error copy for mapped codes and the raw code for unmapped ones', () => {
    const mapped = new AiRequestError({ status: 429, code: 'AI_RATE_LIMITED' })
    expect(resolveErrorMessage(mapped)).toContain('：')
    const unmapped = new AiRequestError({ status: 400, code: 'AI_UNMAPPED_CODE' })
    expect(resolveErrorMessage(unmapped)).toBe('AI_UNMAPPED_CODE')
  })

  it('translates the AI error message when the gateway gives one', () => {
    const error = new AiRequestError({ status: 400, code: 'X', message: 'Permission denied' })
    expect(resolveErrorMessage(error)).toBe('没有权限执行该操作')
  })
})

describe('resolveDetailMessage', () => {
  it('translates strings and structured values', () => {
    expect(resolveDetailMessage('Group not found')).toBe('用户组不存在')
    expect(resolveDetailMessage(undefined)).toBe('')
    expect(resolveDetailMessage([{ message: 'Activity not found' }])).toBe('活动不存在')
  })
})

describe('resolveErrorMessageOr', () => {
  it('uses the given text only when nothing can be extracted', () => {
    expect(resolveErrorMessageOr(axiosErrorOf(400, { detail: 'Thumbnail not found' }), '兜底')).toBe(
      '缩略图不存在'
    )
    expect(resolveErrorMessageOr(axiosErrorOf(400, {}), '兜底')).toBe('兜底')
  })
})

describe('errorDetailOf', () => {
  it('returns the raw protocol string for machine-readable comparisons', () => {
    expect(errorDetailOf(axiosErrorOf(403, { detail: 'USER_BANNED' }))).toBe('USER_BANNED')
  })
})

describe('EN_ERROR_MESSAGES', () => {
  it('never maps a key to an empty or English value', () => {
    const bad = Object.entries(EN_ERROR_MESSAGES).filter(
      ([key, value]) => !value.trim() || value === key
    )
    expect(bad).toEqual([])
  })
})
