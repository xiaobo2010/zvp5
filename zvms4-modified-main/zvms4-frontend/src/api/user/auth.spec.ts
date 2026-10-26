import { beforeEach, describe, expect, it, vi } from 'vitest'
import axios from '@/plugins/axios'
import { resetToInitial, useLongTermAuth, useVerificationAuth } from './auth'

vi.mock('@/plugins/axios', () => ({ default: vi.fn() }))
vi.mock('element-plus', () => ({ ElNotification: vi.fn() }))
vi.mock('./crypto', () => ({
  importPublicKey: vi.fn().mockResolvedValue({}),
  encryptData: vi.fn().mockResolvedValue(new Uint8Array([1, 2]).buffer)
}))

const request = vi.mocked(axios)
beforeEach(() => { request.mockReset(); localStorage.clear() })

describe('authentication response contract', () => {
  it('reads the raw login body and persists its token', async () => {
    const login = { token: 'session', _id: 'user', weak_password: false }
    request.mockResolvedValueOnce({ data: { status: 'ok', data: 'public-key' } })
    request.mockResolvedValueOnce({ data: login })
    expect(await useLongTermAuth('123', 'StrongPwd1!')).toEqual(login)
    expect(localStorage.getItem('token')).toBe('session')
  })

  it('verification never replaces the existing login session', async () => {
    localStorage.setItem('token', 'existing')
    request.mockResolvedValueOnce({ data: { status: 'ok', data: 'public-key' } })
    request.mockResolvedValueOnce({ data: { token: 'proof', _id: 'user' } })
    expect((await useLongTermAuth('123', 'StrongPwd1!', 'long', 'verify')).token).toBe('proof')
    expect(localStorage.getItem('token')).toBe('existing')
  })

  it('issues a short-lived proof token for password re-verification', async () => {
    request.mockResolvedValueOnce({ data: { status: 'ok', data: 'public-key' } })
    request.mockResolvedValueOnce({ data: { token: 'proof', _id: 'user' } })
    expect((await useVerificationAuth('123', 'StrongPwd1!')).token).toBe('proof')
    expect(request.mock.calls[1][1]).toMatchObject({
      method: 'POST',
      data: { id: '123', mode: 'short', purpose: 'verify' }
    })
  })

  it('reads initial-password resets from the response envelope', async () => {
    request.mockResolvedValueOnce({ data: { status: 'ok', data: { initial_password: 'random123' } } })
    expect(await resetToInitial('user', 'proof')).toBe('random123')
  })
})
