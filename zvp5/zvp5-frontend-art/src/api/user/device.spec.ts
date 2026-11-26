import { beforeEach, describe, expect, it, vi } from 'vitest'
import { aiRequest, aiRequestVoid } from '@/api/ai/http'
import {
  clearMyPrimaryDevice,
  clearUserPrimaryDevice,
  issueTempPassword,
  readMyDevice,
  setMyPrimaryDevice
} from './device'

vi.mock('@/api/ai/http', () => ({
  aiRequest: vi.fn(),
  aiRequestVoid: vi.fn()
}))

const request = vi.mocked(aiRequest)
const requestVoid = vi.mocked(aiRequestVoid)
beforeEach(() => {
  request.mockReset()
  requestVoid.mockReset()
})

describe('device api contract', () => {
  it('读取本人设备状态走 v3 envelope', async () => {
    const data = {
      xuehai_id: '101',
      primary_xuehai_id: null,
      device_banned: false,
      device_ban_reason: null,
      device_ban_until: null
    }
    request.mockResolvedValueOnce(data)
    await expect(readMyDevice()).resolves.toEqual(data)
    expect(request).toHaveBeenCalledWith({ method: 'get', url: '/v1/users/me/device' })
  })

  it('绑定主设备提交与请求头语义一致的目标设备号', async () => {
    requestVoid.mockResolvedValueOnce(undefined)
    await setMyPrimaryDevice('101')
    expect(requestVoid).toHaveBeenCalledWith({
      method: 'put',
      url: '/v1/users/me/primary-device',
      data: { xuehai_id: '101' }
    })
  })

  it('解绑本人主设备', async () => {
    requestVoid.mockResolvedValueOnce(undefined)
    await clearMyPrimaryDevice()
    expect(requestVoid).toHaveBeenCalledWith({ method: 'delete', url: '/v1/users/me/primary-device' })
  })

  it('签发一次性临时密码按 oid 定位目标', async () => {
    const issued = {
      code: '12345678',
      expires_at: '2026-10-26T12:30:00',
      target: { _id: 'oid1', id: '2301', name: '张三' }
    }
    request.mockResolvedValueOnce(issued)
    await expect(issueTempPassword('oid1')).resolves.toEqual(issued)
    expect(request).toHaveBeenCalledWith({ method: 'post', url: '/v1/users/oid1/temp-password' })
  })

  it('管理员清空他人主设备按 oid 定位目标', async () => {
    requestVoid.mockResolvedValueOnce(undefined)
    await clearUserPrimaryDevice('oid1')
    expect(requestVoid).toHaveBeenCalledWith({ method: 'delete', url: '/v1/users/oid1/primary-device' })
  })

  it('高危写操作带上 verify 短令牌时进请求体', async () => {
    requestVoid.mockResolvedValue(undefined)
    request.mockResolvedValueOnce({
      code: '12345678',
      expires_at: null,
      target: { _id: 'oid1' }
    })

    await setMyPrimaryDevice('102', 'verify-token')
    await clearMyPrimaryDevice('verify-token')
    await clearUserPrimaryDevice('oid1', 'verify-token')
    await issueTempPassword('oid1', 'verify-token')

    expect(requestVoid).toHaveBeenNthCalledWith(1, {
      method: 'put',
      url: '/v1/users/me/primary-device',
      data: { xuehai_id: '102', verification_token: 'verify-token' }
    })
    expect(requestVoid).toHaveBeenNthCalledWith(2, {
      method: 'delete',
      url: '/v1/users/me/primary-device',
      data: { verification_token: 'verify-token' }
    })
    expect(requestVoid).toHaveBeenNthCalledWith(3, {
      method: 'delete',
      url: '/v1/users/oid1/primary-device',
      data: { verification_token: 'verify-token' }
    })
    expect(request).toHaveBeenCalledWith({
      method: 'post',
      url: '/v1/users/oid1/temp-password',
      data: { verification_token: 'verify-token' }
    })
  })
})