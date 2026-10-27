import { aiRequest, aiRequestVoid } from '@/api/ai/http'

/**
 * 主学海设备 / 一次性临时密码的前端契约层。
 *
 * 设备身份全部取自请求头 `Xuehai-Id`（axios 拦截器注入），这里不提供任何
 * 「客户端自报设备号」的参数：后端只认请求头，请求体里的设备号不参与判定，
 * 自报等于允许任何令牌持有者替任意平板注册。
 */

/** GET /v3/users/me/device 的 data 形状。 */
export interface MyDeviceStatus {
  /** 本机设备号（后端按请求头规整）；空串 = 本机不是学海设备。 */
  xuehai_id: string
  /** 本人主设备号；null = 未绑定（存量用户）。 */
  primary_xuehai_id: string | null
  device_banned: boolean
  device_ban_reason: string | null
  device_ban_until: string | null
}

/** POST /v3/users/{oid}/temp-password 的 data 形状。 */
export interface TempPasswordIssueResult {
  /** 8 位一次性临时密码明文：只在本响应出现一次，库中只留 bcrypt 哈希。 */
  code: string
  expires_at: string | null
  target: { _id: string; id?: string; name?: string }
}

export async function readMyDevice(): Promise<MyDeviceStatus> {
  return aiRequest<MyDeviceStatus>({ method: 'get', url: '/v3/users/me/device' })
}

export async function setMyPrimaryDevice(xuehaiId: string, verificationToken?: string): Promise<void> {
  await aiRequestVoid({
    method: 'put',
    url: '/v3/users/me/primary-device',
    data: {
      xuehai_id: xuehaiId,
      ...(verificationToken ? { verification_token: verificationToken } : {})
    }
  })
}

export async function clearMyPrimaryDevice(verificationToken?: string): Promise<void> {
  await aiRequestVoid({
    method: 'delete',
    url: '/v3/users/me/primary-device',
    ...(verificationToken ? { data: { verification_token: verificationToken } } : {})
  })
}

export async function issueTempPassword(
  userOid: string,
  verificationToken?: string
): Promise<TempPasswordIssueResult> {
  return aiRequest<TempPasswordIssueResult>({
    method: 'post',
    url: `/v3/users/${userOid}/temp-password`,
    ...(verificationToken ? { data: { verification_token: verificationToken } } : {})
  })
}

export async function clearUserPrimaryDevice(
  userOid: string,
  verificationToken?: string
): Promise<void> {
  await aiRequestVoid({
    method: 'delete',
    url: `/v3/users/${userOid}/primary-device`,
    ...(verificationToken ? { data: { verification_token: verificationToken } } : {})
  })
}