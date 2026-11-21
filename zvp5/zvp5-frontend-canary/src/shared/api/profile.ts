import { fetchPublicCert } from './auth'
import { request, unwrap } from '@/shared/http/client'
import { encryptCredential } from '@/shared/lib/crypto'
import type { MyDeviceState, TotpCode } from '@/types/domain'

/**
 * 个人中心安全域。
 *
 * 三条约束来自后端真实实现：
 * 1. 改密必须先 `verify` 换 5 分钟短期令牌，再带着令牌 PUT 新凭据；
 *    `verify` 令牌可以满足 `password_change` 依赖，但不会签发会话。
 * 2. 动态码只允许查看本人，且每次 GET 都会写 `totp_view` 审计日志，
 *    因此前端不做自动轮询，只在用户点击/刷新时读取。
 * 3. 主设备绑定与解绑都由 `Xuehai-Id` 请求头决定，前端不能伪造该头；
 *    普通浏览器没有它时，界面只展示真实状态并说明原因。
 */

export async function fetchMyDevice(): Promise<MyDeviceState> {
  const payload = await request<unknown>({ url: '/v1/users/me/device', method: 'GET' })
  return unwrap<MyDeviceState>(payload)
}

export async function fetchMyTotp(oid: string): Promise<TotpCode> {
  const payload = await request<unknown>({
    url: `/v1/users/${encodeURIComponent(oid)}/totp`,
    method: 'GET'
  })
  return unwrap<TotpCode>(payload)
}

export async function changeMyPassword(
  oid: string,
  newPassword: string,
  verificationToken: string
): Promise<void> {
  const pem = await fetchPublicCert()
  const credential = await encryptCredential(pem, newPassword)
  await request({
    url: `/v1/users/${encodeURIComponent(oid)}/password`,
    method: 'PUT',
    data: { credential },
    headers: { Authorization: `Bearer ${verificationToken}` }
  })
}

export async function setMyPrimaryDevice(
  xuehaiId: string,
  verificationToken?: string
): Promise<void> {
  await request({
    url: '/v1/users/me/primary-device',
    method: 'PUT',
    data: {
      xuehai_id: xuehaiId,
      ...(verificationToken ? { verification_token: verificationToken } : {})
    }
  })
}

export async function clearMyPrimaryDevice(verificationToken: string): Promise<void> {
  await request({
    url: '/v1/users/me/primary-device',
    method: 'DELETE',
    data: { verification_token: verificationToken }
  })
}
