import { describe, expect, it } from 'vitest'
import { ApiError, DEVICE_CODES, kindOfStatus, messageFromDetail } from './errors'

describe('http errors', () => {
  it('extracts message from string detail', () => {
    expect(messageFromDetail('权限不足')).toBe('权限不足')
    expect(messageFromDetail('USER_BANNED')).toBe('USER_BANNED')
  })

  it('extracts message/code from object detail', () => {
    expect(messageFromDetail({ code: 'DEVICE_NOT_PRIMARY' })).toBe('DEVICE_NOT_PRIMARY')
    expect(messageFromDetail({ code: 'X', message: '需要验证' })).toBe('需要验证')
  })

  it('ignores unknown detail shapes', () => {
    expect(messageFromDetail(undefined)).toBe('')
    expect(messageFromDetail(42)).toBe('')
  })

  it('maps status codes to semantic kinds', () => {
    expect(kindOfStatus(401)).toBe('unauthorized')
    expect(kindOfStatus(403)).toBe('forbidden')
    expect(kindOfStatus(409)).toBe('conflict')
    expect(kindOfStatus(503)).toBe('server')
    expect(kindOfStatus(418)).toBe('unknown')
  })

  it('keeps the banned and maintenance flags explicit', () => {
    const error = new ApiError({ message: '维护中', status: 503, maintenance: true })
    expect(error.maintenance).toBe(true)
    expect(DEVICE_CODES.has('DEVICE_BAN_APPLIED')).toBe(false)
    expect(DEVICE_CODES.has('DEVICE_BANNED')).toBe(true)
  })
})
