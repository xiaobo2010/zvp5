import type { AiQuotaKind, AiQuotaMutation, AiQuotaSnapshot } from '@/../types/ai'
import { aiRequest } from './http'

/**
 * 额度（/ai/quota）。
 *
 * 没有「列出所有用户额度」的端点：管理端形态是「先选人，再查看与调整」。
 */

/** 当前用户本月额度自查。 */
export async function myQuota(): Promise<AiQuotaSnapshot> {
  return aiRequest<AiQuotaSnapshot>({ method: 'get', url: '/ai/quota' })
}

/** admin 查看某个用户的额度。 */
export async function userQuota(userOid: string): Promise<AiQuotaSnapshot> {
  return aiRequest<AiQuotaSnapshot>({ method: 'get', url: `/ai/quota/${userOid}` })
}

/** 设置某能力的月上限；请求字段是 limit_count，响应字段是 limit。 */
export async function setUserLimit(
  userOid: string,
  kind: AiQuotaKind,
  limitCount: number
): Promise<AiQuotaMutation> {
  return aiRequest<AiQuotaMutation>({
    method: 'put',
    url: `/ai/quota/${userOid}`,
    data: { kind, limit_count: limitCount }
  })
}

/** 重置某能力本月已用量（上限保留）。 */
export async function resetUserQuota(
  userOid: string,
  kind: AiQuotaKind
): Promise<AiQuotaMutation> {
  return aiRequest<AiQuotaMutation>({
    method: 'post',
    url: `/ai/quota/${userOid}/reset`,
    data: { kind }
  })
}
