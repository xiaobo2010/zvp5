import { request, unwrap } from '@/shared/http/client'
import { encryptCredential } from '@/shared/lib/crypto'
import type { FeaturePolicy, LoginResult, UserProfile } from '@/types/domain'

/**
 * 认证 API。
 * 登录凭据走后端 RSA 公钥加密；token 由调用方写入本地存储。
 */

export async function fetchPublicCert(): Promise<string> {
  const payload = await request<unknown>({
    url: '/cert',
    method: 'GET',
    params: { type: 'public', method: 'RSA' }
  })
  return unwrap<string>(payload)
}

export async function loginWithPassword(id: string, password: string): Promise<LoginResult> {
  const pem = await fetchPublicCert()
  const credential = await encryptCredential(pem, password)
  return request<LoginResult>({
    url: '/v1/users/auth',
    method: 'POST',
    data: { id, credential, mode: 'long', purpose: 'login' }
  })
}

export async function loginWithTempPassword(
  challenge: string,
  tempPassword: string
): Promise<LoginResult> {
  return request<LoginResult>({
    url: '/v1/users/auth/device',
    method: 'POST',
    data: { challenge, temp_password: tempPassword }
  })
}

export async function logout(): Promise<void> {
  try {
    await request({ url: '/v1/users/auth/logout', method: 'POST' })
  } catch {
    // 登出失败不阻断本地清理：token 可能已过期。
  }
}

export async function fetchProfile(oid: string): Promise<UserProfile> {
  const payload = await request<unknown>({ url: `/v1/users/${encodeURIComponent(oid)}`, method: 'GET' })
  return unwrap<UserProfile>(payload)
}

/**
 * 密码再认证：换一张 5 分钟有效的短期令牌，用于发布通知、删号等敏感写操作。
 * 服务端对 `purpose !== 'login'` 一律只签短期令牌，不会签发会话。
 */
export async function verifyPassword(id: string, password: string): Promise<string> {
  const pem = await fetchPublicCert()
  const credential = await encryptCredential(pem, password)
  const result = await request<{ token: string }>({
    url: '/v1/users/auth',
    method: 'POST',
    data: { id, credential, mode: 'short', purpose: 'verify' }
  })
  return result.token
}

export async function fetchMyPolicy(): Promise<FeaturePolicy> {
  const payload = await request<unknown>({ url: '/system/role-policies/me', method: 'GET' })
  return unwrap<FeaturePolicy>(payload)
}
